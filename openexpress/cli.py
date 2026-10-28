"""openexpress: command-line interface."""

from __future__ import annotations

import argparse
import collections
import contextlib
import importlib.util
import json
import logging
import sys
import time
from pathlib import Path

from openexpress import __version__, applog, i18n, known_devices, paths
from openexpress.auth import store
from openexpress.connect import (
    ApiError,
    ClockSkew,
    GarminClient,
    OwnershipError,
    Unauthorized,
)
from openexpress.console import error as _error
from openexpress.device.storage import DeviceError, LocalStorage, Storage
from openexpress.device.xmldevice import GarminDevice, parse
from openexpress.i18n import N_, _, human_size, ngettext, pgettext
from openexpress.settings import (
    ENVIRONMENTS,
    Consumer,
    Settings,
    SetupError,
    extract_consumer_from_dll,
    extract_url_salt_from_dll,
    language_tag,
    load_consumer,
    save_consumer,
)
from openexpress.updates import FINISH_STEPS

log = logging.getLogger(__name__)


_human = human_size  # the name the commands use

# The exit code of `sync --apply` when the first sync with this program waits for a choice
# (baseline or --all): not 2, which argparse gives a wrong command line (README, "Exit
# codes").
FIRST_SYNC_CHOICE = 3


# Commands that read or write many files on the device when they are not a dry run.
WORKS_ON_DEVICE = frozenset({"sync", "backup", "updates", "maps", "apps", "music"})


# Commands that do not apply to a Garmin device in mass storage mode (as in the window).
NOT_ON_A_DRIVE = ("sync", "baseline", "apps", "music", "maps", "backup")


def open_storage(args) -> Storage:
    """The connected device: MTP first, else a Garmin drive, as the window opens them;
    --device-uri names an MTP device; --path a folder without a device (e.g. a copy)."""
    if args.path:
        return LocalStorage(Path(args.path))
    from openexpress import service
    from openexpress.device.gio_storage import open_device_storage

    # A device that was just plugged in needs a while before its files can be seen.
    wait = not getattr(args, "no_wait", False)

    def notify(line):
        print(line, file=sys.stderr, flush=True)

    st = (open_device_storage(args.device_uri, wait=wait, notify=notify) if args.device_uri
          else service.open_device_or_drive(wait=wait, notify=notify))
    if getattr(args, "apply", False) and getattr(args, "cmd", None) in WORKS_ON_DEVICE:
        # On stderr, so that it does not mix with the result.
        print(_("Do not unplug the device until the operation is finished."),
              file=sys.stderr, flush=True)
    return st


def open_device(args) -> tuple[Storage, GarminDevice]:
    from openexpress import applog, service
    from openexpress.device.gio_storage import mass_storage_text

    st = open_storage(args)
    dev = parse(st.read("GARMIN/GarminDevice.xml"))
    applog.hide_device(dev, getattr(st, "usb", None))
    command = getattr(args, "cmd", None)
    if st.mass_storage and command in NOT_ON_A_DRIVE:
        raise service.MassStorageMode(mass_storage_text())
    if command in ("sync", "baseline") and not dev.datatypes():
        raise service.MassStorageMode(_(
            "Nothing to sync: the device declares no data types (for example a device "
            "connected as a USB drive, or a copy of one)."))
    if not args.path:  # a device found now; a folder given with --path may be a copy
        known_devices.seen(dev.unit_id, dev.description, dev.part_number,
                           dev.software_version_str, "ums" if st.mass_storage else "mtp",
                           **service.drive_keys(getattr(st, "usb", None) if st.mass_storage
                                                else None))
    return st, dev


def make_client(settings: Settings, need_auth: bool) -> GarminClient:
    consumer = load_consumer()
    auth = store.load() if need_auth else None
    if auth and auth.environment != settings.environment:
        raise SetupError(
            _("The token is for environment {token_environment}, but the configured one is "
              "{environment}. Please log in again.").format(
                token_environment=auth.environment, environment=settings.environment)
        )
    return GarminClient(settings, consumer, auth)


# --- commands --------------------------------------------------------------------------


def cmd_setup(args) -> int:
    settings = Settings.load()
    if args.from_installer:
        from openexpress.installer import consumer_from_installer

        consumer = consumer_from_installer(Path(args.from_installer))
        save_consumer(consumer)
        print(_("Read the application key and download signing data from {file}").format(
            file=Path(args.from_installer).name))
    elif args.from_dll:
        from openexpress.installer import refuse_macos_installer

        refuse_macos_installer(Path(args.from_dll))
        consumer = extract_consumer_from_dll(Path(args.from_dll))
    elif args.key and args.secret:
        consumer = Consumer(args.key, args.secret)
    else:
        try:
            consumer = load_consumer()
        except SetupError:
            consumer = None
    util = Path(args.utilities_dll) if args.utilities_dll else (
        Path(args.from_dll).with_name("Utilities.dll") if args.from_dll else None)
    if consumer and util:
        if util.exists():
            consumer.url_salt = extract_url_salt_from_dll(util)
            print(_("Saved download URL signing data from {file}").format(file=util.name))
        else:
            print(_("Warning: {path} not found; map downloads will not work").format(path=util),
                  file=sys.stderr)
    if consumer and (args.from_dll or args.key or args.utilities_dll):
        save_consumer(consumer)
        # No part of the key is printed: it comes from the user's own installer.
        print(_("Saved OAuth1 consumer key in {path}").format(path=paths.config_dir()))
    if args.environment:
        settings.environment = args.environment
    if args.locale:
        settings.chosen_locale = args.locale
    if getattr(args, "diagnostics_copy", None):
        settings.diagnostics_copy = args.diagnostics_copy == "on"
    settings.save()  # keeps the keys of the others, ui_language among them
    if args.ui_language:
        ui_language = "" if args.ui_language == "system" else args.ui_language
        i18n.save_ui_language(ui_language)
    print(_("Environment: {environment}, locale: {locale}").format(
        environment=settings.environment, locale=settings.locale))
    if args.ui_language:
        print(_("Interface language: {language}").format(
            language=i18n.LANGUAGE_NAMES.get(ui_language, ui_language) if ui_language
            else _("system language")))
    if getattr(args, "diagnostics_copy", None):
        print(_diagnostics_copy_text(settings.diagnostics_copy))
    return 0


def _diagnostics_copy_text(on: bool) -> str:
    return (_("Copy of the device's diagnostic files at each connection: on") if on
            else _("Copy of the device's diagnostic files at each connection: off"))


def cmd_diagnostics(args) -> int:
    """`diagnostics`: what the device logged as errors, its memory dumps and the copies kept
    on this computer; with --apply the files it shows copied now, with --crash-logs also its
    hidden crash logs (a session of our own), with --dumps also its memory dumps. Nothing is
    ever sent."""
    from openexpress import diagnostics

    st, dev = open_device(args)
    print(_diagnostics_copy_text(diagnostics.copy_on()))
    entries = diagnostics.read_errors(st)
    print(_("Error logs on the device:"))
    for e in entries:
        print(f"  {e.words()} ({e.source})")
    if not entries:
        print("  " + _("no entries"))
    dumps = diagnostics.dumps_on_device(st)
    if dumps:
        print(_("Memory dumps on the device: {count}, {size} (encrypted; copied only with "
                "--dumps)").format(count=len(dumps),
                                   size=_human(sum(size for _p, size in dumps))))
    crashes = diagnostics.boot_log_crashes(dev.unit_id)
    if crashes:
        print(_("Crashes in the copied boot log:"))
        for line in crashes:
            print("  " + line)
    print(_("Copies on this computer: {copies}").format(
        copies=diagnostics.copies_text(dev.unit_id)))
    # Not the folder itself: it is named after the unit ID, and this is pasted into reports.
    print("  " + _("in the folder of the device under {folder}").format(
        folder=paths.data_dir() / "devices"))
    if not args.apply:
        print(_("Dry run. Add --apply to copy the files the device shows now."))
        if args.crash_logs:
            print(_("With --apply, --crash-logs also reads its hidden crash logs in a session "
                    "of its own: the device leaves the file manager for a moment."))
        return 0
    done = diagnostics.copy_files(dev, st)
    print(ngettext("Copied: {count} new file ({size}), {unchanged} unchanged, {listed} only "
                   "listed.",
                   "Copied: {count} new files ({size}), {unchanged} unchanged, {listed} only "
                   "listed.", done.new).format(count=done.new, size=_human(done.new_bytes),
                                               unchanged=done.unchanged, listed=done.listed))
    if args.dumps:
        dumped = diagnostics.copy_dumps(dev, st)
        print(_("Memory dumps copied: {count} new ({size}).").format(
            count=dumped.new, size=_human(dumped.new_bytes)))
        if dumped.not_copied:
            print(_("Not copied, over the limit of {size}: {names}").format(
                size=_human(diagnostics.LIMIT), names=", ".join(dumped.not_copied)))
    if args.crash_logs:
        hidden = diagnostics.copy_crash_logs(dev, st)
        print(ngettext("Crash logs: {count} file read ({new} new).",
                       "Crash logs: {count} files read ({new} new).",
                       hidden.new + hidden.unchanged).format(
            count=hidden.new + hidden.unchanged, new=hidden.new) if hidden.saved
              else _("The device has no hidden crash logs."))
    return 0


def storage_text(st) -> str:
    """Where the device is, for `info`: over MTP its storage; for a drive "USB drive" and
    the label of its volume, never the mount path (it has the user name)."""
    if not getattr(st, "mass_storage", False):
        return st.label
    name = getattr(st, "volume_name", None)
    # TRANSLATORS: a device connected as a USB drive; {name} is the label of its volume
    return _("USB drive ({name})").format(name=name) if name else _("USB drive")


def storage_id(st) -> str:
    """The same for `info --json`, not translated: "usb-drive:GARMIN"."""
    if not getattr(st, "mass_storage", False):
        return st.label
    name = getattr(st, "volume_name", None)
    return f"usb-drive:{name}" if name else "usb-drive"


def cmd_info(args) -> int:
    st, dev = open_device(args)
    if args.json:
        print(json.dumps({
            "unit_id": dev.unit_id, "part_number": dev.part_number,
            "software_version": dev.software_version_str, "description": dev.description,
            "storage": storage_id(st), "free_bytes": st.free_space(),
            "datatypes": dev.datatypes(),
        }, indent=2, ensure_ascii=False))
        return 0
    rows = [
        (_("Device:"), dev.description),
        (_("Part number:"), dev.part_number),
        (_("Software:"), dev.software_version_str),
        (_("Unit ID:"), dev.unit_id),
        (_("Storage:"), storage_text(st)),
        (_("Free space:"), _human(st.free_space())),
        (_("Data types:"), _("{datatypes}, components: {components}").format(
            datatypes=len(dev.datatypes()), components=len(dev.update_files))),
    ]
    if dev.iq_max_apps:
        rows.append(("Connect IQ:", ngettext(
            "max. {count} app, {size}", "max. {count} apps, {size}", dev.iq_max_apps).format(
                count=dev.iq_max_apps, size=_human(dev.iq_app_space))))
    width = max(len(label) for label, _value in rows) + 1
    for label, value in rows:
        print(f"{label:{width}} {value}")
    return 0


def cmd_report(args) -> int:
    from openexpress import report

    st, dev = open_device(args)
    try:
        store.load()  # it returns a login or raises
        logged_in = True
    except Exception:  # noqa: BLE001 - only yes/no: a broken login never stops the report
        logged_in = False
    report_text = report.build(dev, st, logged_in)
    # An address that opens the form with the report filled in; never with the log.
    url = report.issue_url(report_text, dev.description)
    if not args.log or url is None:  # too long for an address: the report printed as well
        print(report_text, end="" if not args.log else "\n")
    if args.log:
        # The log alone, ready for the "Log" field of the form: what the window puts in the
        # clipboard for it.
        print(report.form_log(applog.tail(report.LOG_LINES), applog.crash_tail()), end="")
    if url:
        print("\n" + _("This report contains no serial number, unit ID or e-mail. Open this "
                       "address to start a device report with it filled in: {url}").format(
            url=url), file=sys.stderr)
    else:  # too long for an address: the empty form
        print("\n" + _("This report contains no serial number, unit ID or e-mail. "
                       "Paste it into a device report: {url}").format(url=report.ISSUE_URL),
              file=sys.stderr)
    if args.log:
        print(_("The end of the log is printed above. It is filtered, but read it before you "
                "send it."), file=sys.stderr)
        if url:
            print(_("The log is not in the address: paste it into the “Log” field of the "
                    "form or into a comment."), file=sys.stderr)
    else:
        print(_("Log file: {path} (add --log to print its last lines for the “Log” field of "
                "the form or a comment)").format(path=applog.shown_path()), file=sys.stderr)
    return 0


def cmd_files(args) -> int:
    st, dev = open_device(args)
    types = [args.type] if args.type else sorted(
        {f.datatype for f in dev.files if f.readable},
        key=lambda s: (not s.startswith("FIT_TYPE_"), int(s[9:]) if s[9:].isdigit() else 0, s),
    )
    # Several data types can point at the same folder: the total counts every file once.
    seen: set[str] = set()
    for dt in types:
        files: dict[str, int] = {}
        for src in dev.sources(dt):
            for path, e in st.walk_files(src.path):
                if src.matches(path):
                    files[path] = e.size
        if not files and not args.type:
            continue
        seen.update(p.casefold() for p in files)
        paths_ = ", ".join(sorted({s.path for s in dev.sources(dt)}))
        count = ngettext("{count} file", "{count} files", len(files)).format(
            count=f"{len(files):5}")
        print(f"{dt:22} {count:12} {_human(sum(files.values())):>9}  ({paths_})")
        if args.type or args.verbose:
            for p in sorted(files)[-args.limit:] if args.limit else sorted(files):
                print(f"    {p}  {_human(files[p])}")
    total = len(seen)
    print(ngettext("Total: {count} file", "Total: {count} files", total).format(count=total))
    return 0


def cmd_login(args) -> int:
    from openexpress.auth import sso

    settings = Settings.load()
    client = make_client(settings, need_auth=False)
    if args.manual:
        ticket = sso.login_manual(settings)
    else:
        print(_("Opening the Garmin sign-in window…"))
        ticket = sso.login_with_webview(settings)
    token, secret = client.exchange_ticket(ticket.ticket, ticket.service)
    auth = store.ConnectAuth(token, secret, settings.environment)
    client.auth = auth
    try:
        prof = client.user_profile()
        auth.account = {k: prof.get(k) for k in
                        ("userprofilepk", "username", "emailaddress", "garminguid")}
    except Exception as e:  # noqa: BLE001 - the token is valid even without the profile
        print(_("Warning: could not fetch the profile ({error})").format(
            error=str(e) or e.__class__.__name__), file=sys.stderr)
    store.save(auth)
    print(_("Logged in as {account}.").format(account=auth.display))
    return 0


def cmd_logout(args) -> int:
    print(_("Logged out (local token removed).") if store.delete()
          else _("No saved token found."))
    return 0


def cmd_whoami(args) -> int:
    auth = store.load()
    settings = Settings.load()
    print(_("Account: {account} (environment {environment})").format(
        account=auth.display, environment=auth.environment))
    if args.check:
        prof = make_client(settings, need_auth=True).user_profile()
        print(_("Token works: {username} / {email}").format(
            username=prof.get("username"), email=prof.get("emailaddress")))
    return 0


def cmd_status(args) -> int:
    """What this installation knows now, in one look: the login, the Garmin devices connected
    (name, USB mode, whether they are on the account), the last sync of each and what the
    last check of its updates found. Only the devices' GarminDevice.xml and the files this
    installation keeps are read; with --online the account's device list and Garmin's update
    server are asked as well (as `openexpress updates` does), and nothing of them is kept."""
    from datetime import datetime

    from openexpress.device import gio_storage

    try:
        auth = store.load()
    except store.NotLoggedIn:
        auth = None
    print(_("Garmin Connect: logged in as {account}").format(account=auth.display)
          if auth is not None else _("Garmin Connect: not logged in"))
    summaries = gio_storage.distinct_devices(
        [gio_storage.probe(f) for f in gio_storage.find_all_devices()])
    if not summaries:
        print(_("No Garmin device is connected."))
        return 0
    print(ngettext("Connected: {count} device", "Connected: {count} devices",
                   len(summaries)).format(count=len(summaries)))
    client, listed = None, None
    if args.online:
        settings = Settings.load()
        client = make_client(settings, need_auth=True) if auth is not None \
            else GarminClient(settings)
        if auth is not None:
            from openexpress.service import unit_id_of

            listed = {unit_id_of(d.get("unitid")) for d in client.account_devices()}
    known = known_devices.load()
    now = datetime.fromtimestamp(time.time())
    for s in summaries:
        _print_status(s, known.get(s.unit_id) if s.unit_id else None, listed, client, now)
    return 0


def _print_status(s, entry, listed, client, now) -> None:
    """One device of `openexpress status`: its line, its last sync (not of a drive) and its
    updates (`client`: asked of the update server now; None: what the last check found)."""
    from openexpress import last_sync, service, sync, tile_facts
    from openexpress.gui import words

    drive = s.found.kind == "ums"
    how = _("USB drive") if drive else "MTP"  # the window's pair of names
    if not s.ok:
        print(" · ".join((_("Garmin device"), how, _("its details were not read"))))
        return
    parts = [known_devices.shown_name(s.description, entry.model_name if entry else None), how]
    if not drive:
        on = (s.unit_id in listed if listed is not None else
              tile_facts.read(entry, connected=True, kind=s.found.kind,
                              software_version=s.software_version).on_account)
        parts.append(_("on your account") if on else _("not on your account")
                     if on is False else _("account not known"))
    print(" · ".join(parts))
    if not drive:
        data = service.load_last_sync(s.unit_id)
        text = (words.never_synced() if not sync.synced_here(s.unit_id) else
                last_sync.describe(data).row(now) if data is not None else
                _("No record of the last sync; the next sync uploads new data only"))
        print("  " + _("Last sync: {text}").format(text=text))
    text = (_status_updates_online(client, s) if client is not None
            else _status_updates(entry, s.software_version, now))
    print("  " + _("Device software: {text}").format(text=text))


def _status_updates(entry, software: str, now) -> str:
    """What the last check of updates kept with the device found, while it holds (recent, of
    the software it has now)."""
    from openexpress.gui import words

    at = now.timestamp()
    if entry is not None:
        found = known_devices.recorded_updates(entry, software, at)
        if found is not None:
            only = found if found.count == 1 and found.title is not None else None
            return words.checked_text(words.updates_available(found.count, only),
                                      entry.update_found, now)
        check = known_devices.recorded_check(entry, software, at)
        if check is not None and check.ready:
            return words.checked_text(_("an update is ready: unplug the device to install it"),
                                      check.at, now)
        if check is not None and entry.update_found is None:
            return words.checked_text(_("Software is up to date"), check.at, now)
        last = entry.update_check.at if entry.update_check is not None else entry.update_found
        if last is not None:
            return words.not_checked_since(last)
    return _("not checked yet (openexpress status --online checks it)")


def _status_updates_online(client, s) -> str:
    """The updates of the device as Garmin's update server offers them now."""
    from openexpress import updates as upd
    from openexpress.device import gio_storage
    from openexpress.gui import words

    try:
        device, storage = s.device, s.storage
        if device is None or storage is None:  # a drive: probe keeps nothing of it
            storage = gio_storage.open_ums_storage(s.found)
            device = parse(storage.read("GARMIN/GarminDevice.xml"))
        items = upd.mark_present(storage, upd.parse_updates(client.software_updates(device.raw)),
                                 upd.component_count(device))
    except (ApiError, DeviceError) as e:
        return _("could not be checked: {error}").format(error=e)
    new, staged = upd.pending(items), upd.staged(items)
    if staged:
        return _("an update is ready: unplug the device to install it")
    if new:
        return words.updates_available(len(new), new[0] if len(new) == 1 else None)
    return _("Software is up to date")


def cmd_cache(args) -> int:
    """How big the cache is, part by part (all, or those of --only). With --clear those parts
    are emptied (with --apply; without it, only said). One line of the log for each part."""
    from openexpress import cache

    only = None
    if args.only is not None:
        names = [name.strip() for value in args.only for name in value.split(",")
                 if name.strip()]
        unknown = list(dict.fromkeys(name for name in names if name not in cache.CATEGORIES))
        parts = ", ".join(cache.CATEGORIES)
        if unknown or not names:
            why = (_("Not a part of the cache: {names}. The parts are: {parts}.").format(
                names=", ".join(unknown), parts=parts) if unknown else
                _("No part of the cache named. The parts are: {parts}.").format(parts=parts))
            _error(_("Error: {error}").format(error=why))
            return 1
        only = names
    if args.clear and args.apply:
        try:
            use = cache.clear(only)
        except cache.CacheError as e:
            _error(_("Error: {error}").format(error=e))
            return 1
    else:
        use = cache.usage(only)
    print(_("Cache: {path}").format(path=use.root))
    for name, size in use.sizes.items():
        print(f"  {_(cache.CATEGORY_NAMES[name])}: {human_size(size)}")
    print("  " + _("Total: {size}").format(size=human_size(use.total)))
    for name, size in use.sizes.items():
        files = use.files[name]
        counted = f"{files} file" if files == 1 else f"{files} files"
        if args.clear and args.apply:
            log.info("cache: %s: cleared, %s freed (%s)", name, applog.size(size), counted)
        elif args.clear:
            log.info("cache: %s: %s in %s, dry run: nothing deleted", name, applog.size(size),
                     counted)
        else:
            log.info("cache: %s: %s in %s", name, applog.size(size), counted)
    if args.clear and args.apply:
        print(_("Freed {size}.").format(size=human_size(use.total)))
    elif args.clear:
        print(_("Dry run: nothing was deleted. Add --apply to delete it."))
    if args.clear and only is None:
        print(_("The pictures of the remembered devices stay; to delete them too: openexpress "
                "cache --clear --only pictures --apply"))
    return 0


def cmd_devices(args) -> int:
    """The devices this installation has seen (devices.json), numbered; --forget N forgets one
    (with --apply) and what this installation keeps of it (forget.forget_device). The unit ID
    is never shown."""
    devices = known_devices.in_order(known_devices.load())
    if args.forget is None:
        if not devices:
            print(_("No devices are remembered yet."))
            return 0
        print(_("Devices this installation has seen:"))
        for number, d in enumerate(devices, 1):
            how = _("USB drive") if d.kind == "ums" else "MTP"  # the window's pair of names
            print(f"  {number}. " + " · ".join((
                d.description, d.part_number,
                _("Software {version}").format(version=d.software_version), how,
                _("Last seen {when}").format(when=_seen_at(d.last_seen)))))
        return 0
    if not 1 <= args.forget <= len(devices):
        _error(_("Error: {error}").format(error=_(
            "There is no device number {number} on the list (it has {count}).").format(
            number=args.forget, count=len(devices))))
        return 1
    from openexpress import forget

    d = devices[args.forget - 1]
    name = f"{d.description} ({d.part_number})"
    if d.unit_id in _connected_unit_ids():
        _error(_("Error: {error}").format(error=_(
            "{device} is connected: unplug it to forget it.").format(device=name)))
        return 1
    kept = forget.kept(d.unit_id)
    anyway = _("Add --discard-pending to forget it anyway.")
    if not args.apply:
        print(_("Device to forget: {device}").format(device=name))
        print(_("What this installation keeps of it goes too: what was uploaded from it, its "
                "last sync, the maps of your own copied to it and the copies of its diagnostic "
                "files. Its next sync with this "
                "program will be a first sync again."))
        if kept.pending:
            lost = str(forget.PendingFiles(kept.pending))
            print(lost if args.discard_pending else f"{lost} {anyway}")
        if kept.backups and args.backups:
            print(ngettext("{count} backup of it in {path} is deleted too.",
                           "{count} backups of it in {path} are deleted too.",
                           kept.backups).format(count=kept.backups,
                                                path=forget.backup_folder()))
        elif kept.backups:
            print(ngettext(
                "{count} backup of it stays in {path} (add --backups to delete it too).",
                "{count} backups of it stay in {path} (add --backups to delete them too).",
                kept.backups).format(count=kept.backups, path=forget.backup_folder()))
        print(_("Dry run: nothing was forgotten. Add --apply to forget it."))
        return 0
    try:
        done = forget.forget_device(d.unit_id, discard_pending=args.discard_pending,
                                    backups=args.backups)
    except forget.PendingFiles as e:
        _error(_("Error: {error}").format(error=f"{e} {anyway}"))
        return 1
    except OSError as e:
        # Never the path: the device's folders are named after its unit ID.
        log.info("devices: not forgotten (%s)", type(e).__name__)
        _error(_("Error: {error}").format(error=_("{device} was not forgotten: {reason}").format(
            device=name, reason=e.strerror or type(e).__name__)))
        return 1
    if not done:
        _error(_("Error: {error}").format(error=_("{device} was not forgotten.").format(
            device=name)))
        return 1
    print(_("Forgot {device}.").format(device=name))
    if args.backups and kept.backups:
        print(ngettext("Deleted {count} backup.", "Deleted {count} backups.",
                       kept.backups).format(count=kept.backups))
    return 0


def _connected_unit_ids() -> set[int]:
    """The unit IDs of the devices connected now, from the GarminDevice.xml of each, as the
    window lists them: nothing is opened or mounted, nothing is asked of the account. A
    device whose file cannot be read names none."""
    from openexpress.device.gio_storage import find_all_devices, probe

    return {s.unit_id for s in (probe(f) for f in find_all_devices()) if s.unit_id}


def _seen_at(ts: float) -> str:
    """A moment as the program writes dates, in local time: "17.10.2026 13:20" in Polish."""
    t = time.localtime(ts)
    return pgettext("date and hour", "{date} {time}").format(
        date=time.strftime(_("%Y-%m-%d"), t), time=time.strftime("%H:%M", t))


def cmd_upload_config(args) -> int:
    _st, dev = open_device(args)
    settings = Settings.load()
    cfg = GarminClient(settings).upload_settings(dev.unit_id)
    if args.json:
        print(json.dumps(cfg, indent=2, ensure_ascii=False))
        return 0
    print(_("Device type: {name}").format(name=cfg.get("unittypename")))
    for dt in cfg.get("datatypes") or []:
        print(f"- {dt.get('datatypename')}  shouldDelete={dt.get('shoulddelete')}")
        for loc in dt.get("uploadlocations") or []:
            consents = ", ".join(c.get("name", "") for c in loc.get("requiredconsenttypes") or [])
            print(f"    {loc.get('url')}  auth={loc.get('requiresauthentication')} "
                  f"consent={loc.get('requiresuserconsent')}"
                  f"{' [' + consents + ']' if consents else ''}")
    return 0


def cmd_sync(args) -> int:
    import dataclasses

    from openexpress import service

    st, dev = open_device(args)
    settings = Settings.load()
    print(_("{device} (software {version})").format(
        device=dev.description, version=dev.software_version_str))
    try:
        client = make_client(settings, need_auth=True)
    except store.NotLoggedIn:
        if args.apply:
            raise
        # The upload plan can be shown without logging in; the download queue needs a token.
        client = make_client(settings, need_auth=False)
        print(_("(not logged in: showing the upload plan only)"))
    session = service.DeviceSession(st, dev, settings, client)
    # The sync of the window and the background service (service.run_sync), with what was
    # asked here.
    options = service.SyncOptions(
        first_run="all" if args.all else None, include_diagnostics=args.include_diagnostics,
        only_types=frozenset(args.type) if args.type else None,
        uploads=not args.download_only, downloads=not args.upload_only)
    if not args.apply:
        _print_sync_plan(service.plan_sync(session, dataclasses.replace(options, preview=True)))
        print("\n" + _("Dry run: nothing was uploaded or written to the device. "
                       "Add --apply to run the sync."))
        return 0
    try:
        summary = service.run_sync(session, progress=_SyncProgress(), options=options)
    except service.FirstSyncChoiceNeeded:
        print(
            "\n" + _("This is the first sync with this program. If the device also syncs "
                     "via your phone, most files are already in Connect.\n"
                     "  openexpress baseline --apply   marks current files as uploaded "
                     "(recommended)\n"
                     "  openexpress sync --apply --all uploads everything "
                     "(Connect rejects duplicates)"),
            file=sys.stderr,
        )
        return FIRST_SYNC_CHOICE
    except Exception as e:
        done = service.done_before(e)
        if done is not None:  # stopped after its uploads: what they did, then the error
            _print_sync_steps(done, dev.unit_id)
        raise
    return _print_sync_result(summary, dev.unit_id)


def _print_sync_plan(plan) -> None:
    """What `sync` would do (its dry run), as service.plan_sync worked it out."""
    uploads = plan.uploads
    if uploads is not None:
        counts = collections.Counter(c.datatype for c in uploads.candidates)
        n_up = len(uploads.candidates)
        print("\n" + ngettext("To upload: {count} file (already uploaded earlier: {done})",
                              "To upload: {count} files (already uploaded earlier: {done})",
                              n_up).format(count=n_up, done=uploads.already_done))
        for dt, n in sorted(counts.items()):
            size = sum(c.size for c in uploads.candidates if c.datatype == dt)
            print(f"  {dt:22} {n:5}  {_human(size)}")
        for dt, why in sorted(uploads.skipped_types.items()):
            print(f"  {dt:22} " + _("skipped: {reason}").format(reason=why))
        if plan.gate is not None:
            print(plan.gate.message(done=False) if not plan.gate.allowed else _(
                "Uploads are allowed: the Garmin account has the upload consent and device "
                "sync is on."))
    if plan.queue is not None:
        items = plan.queue.items
        n_dl = sum(1 for i in items if not i.unsupported)
        print("\n" + _("To download from Connect to the device: {count}").format(count=n_dl))
        for it in items:
            note = ("  [" + _("skipped: {reason}").format(reason=it.unsupported) + "]"
                    if it.unsupported
                    else f"  [{it.handled_by}]" if it.handled_by else "")
            target = it.target.path if it.target else "-"
            print(f"  {it.datatype:22} {it.message_name or it.message_id} -> {target}{note}")


def _print_sync_steps(summary, unit_id: int):
    """The steps of a sync in the words of the details under "Last sync", its errors and
    the uploads Connect did not confirm. Its words (last_sync.Told)."""
    from openexpress import last_sync

    told = last_sync.describe(summary.record(unit_id))
    said = [text for text, _warning in told.steps]
    said += ["  " + _("ERROR {error}").format(error=error) for error in summary.errors]
    said += ["  " + _("Warning: Connect did not confirm processing of {item}").format(
        item=label) for label in summary.not_confirmed]
    if said:
        print("\n" + "\n".join(said))
    return told


def _print_sync_result(summary, unit_id: int) -> int:
    """What `sync --apply` did: its steps in the words of the details under "Last sync",
    its errors, then the numbers of the row as a sentence. 1 with errors, as the window
    says "Sync finished with errors"."""
    told = _print_sync_steps(summary, unit_id)
    print("\n" + told.sentence)
    if summary.errors:
        print("\n" + ngettext("Done with {count} error.", "Done with {count} errors.",
                              len(summary.errors)).format(count=len(summary.errors)))
        return 1
    print("\n" + _("Done."))
    return 0


def cmd_baseline(args) -> int:
    from openexpress import service, sync, tile_facts

    st, dev = open_device(args)
    client = make_client(Settings.load(), need_auth=True)
    service.require_account_device(client, dev.unit_id)
    cfg = client.upload_settings(dev.unit_id)
    if not args.apply:
        n = len(sync.plan_baseline(dev, st, cfg, sync.SyncState(dev.unit_id)))
        print(ngettext("Dry run: {count} file would be marked as already uploaded. Add --apply.",
                       "Dry run: {count} files would be marked as already uploaded. Add --apply.",
                       n).format(count=n))
        return 0
    with service.sync_lock():
        n = sync.mark_baseline(dev, st, cfg, sync.SyncState(dev.unit_id), client)
    tile_facts.note_synced(dev.unit_id)  # synced here now, for its tile
    print(ngettext("Marked {count} file as already uploaded. "
                   "The next `sync` will upload only new data.",
                   "Marked {count} files as already uploaded. "
                   "The next `sync` will upload only new data.", n).format(count=n))
    return 0


def cmd_backup(args) -> int:
    from openexpress.backup import backup_files, run_backup

    st, dev = open_device(args)
    dest = Path(args.dest) if args.dest else paths.default_backup_dir()
    print(_("Backing up {device} to {path}").format(device=dev.description, path=dest))
    if not args.apply:
        n = 1 + sum(1 for f in backup_files(dev) for p, _e in st.walk_files(f.path)
                    if f.matches(p))
        print(ngettext("Dry run: about {count} file would be copied. Add --apply.",
                       "Dry run: about {count} files would be copied. Add --apply.",
                       n).format(count=n))
        return 0
    res = run_backup(dev, st, dest, keep=args.keep,
                     progress=(lambda p: print("  " + p)) if args.verbose else (lambda p: None))
    print(ngettext("Copied {count} file ({size}) to {path}",
                   "Copied {count} files ({size}) to {path}", res.files).format(
        count=res.files, size=_human(res.bytes), path=res.path))
    for p in res.removed:
        print(_("Removed old backup {path}").format(path=p))
    return 0


class _SyncProgress:
    """The progress of `sync --apply`: each step on a line of its own. A wait of known length
    (TrueUp) is one line on stderr that fills in place on a terminal, and only its first line
    elsewhere; a step after an unfinished one (a wait stopped) starts on a new line."""

    def __init__(self):
        self.open = False

    def __call__(self, message: str) -> None:
        from openexpress.progress import TimedWait

        if isinstance(message, TimedWait):
            if not sys.stderr.isatty():
                if message.done == 0:
                    print("  " + message)
                return
            print(f"\r  {message} {int(message.done * 100):3d}%", end="", file=sys.stderr,
                  flush=True)
            self.open = message.done < 1
            if not self.open:
                print(file=sys.stderr)
            return
        if self.open:
            print(file=sys.stderr)
            self.open = False
        print("  " + message)


class _Bar:
    """Simple progress indicator for large transfers."""

    def __init__(self, label: str = ""):
        self.label = label
        self.last = -1

    def __call__(self, done: int, total: int | None) -> None:
        if not total:
            return
        pct = int(done * 100 / total)
        if pct != self.last:
            self.last = pct
            print(f"\r    {self.label}{pct:3d}%  {_human(done)} / {_human(total)}   ",
                  end="", file=sys.stderr, flush=True)
            if pct >= 100:
                print(file=sys.stderr)


def _beta_mark(u) -> str:
    """" [BETA]" after a beta version, in the list of updates and in what will be installed."""
    # TRANSLATORS: marks a beta version in the list of updates
    return " [" + _("BETA") + "]" if u.is_beta else ""


def _update_line(u) -> str:
    """One update in a list. The file name is added when the server gives no real name."""
    name = u.title if u.title == u.name else f"{u.title} ({u.filename})"
    return (f"{u.part_number:18} {u.version:>7}  {u.severity_text:<12} {_human(u.size):>9}  "
            f"{name}")


def _flags(u) -> str:
    """" [PRIMARY]" after a system update and " [BETA]" after a beta version."""
    # TRANSLATORS: marks the main watch firmware in the list of updates
    return (" [" + _("PRIMARY") + "]" if u.is_system else "") + _beta_mark(u)


def _to_install_staged(st) -> str:
    """What makes the device install an update copied already: a device in mass storage
    mode takes it once its drive is ejected (#11)."""
    if getattr(st, "mass_storage", False):
        return _("To install it, eject the drive in the file manager, then unplug the device.")
    return _("Disconnect the device to install it.")


def _print_staged(staged, st) -> None:
    """System updates on the device already, waiting for it to install them."""
    if not staged:
        return
    print(_("Update copied, waiting for the device to install it:"))
    for u in staged:
        print("  " + _update_line(u) + _flags(u))
    print(_to_install_staged(st))


def _eject_drive(st) -> bool:
    """After a write to a Garmin drive: eject it, so that the device takes the update (#11);
    only then may the cable go out. False, with what to do, when it is not ejected."""
    from openexpress.device import gio_storage

    print(_("Ejecting the device…"))
    try:
        gio_storage.eject(st)
    except gio_storage.DriveNotMounted as e:
        _error(str(e))
        return False
    except DeviceError as e:
        log.info("eject: %s", e)
        _error(_("The device was not ejected: {error}").format(error=e))
        _error(gio_storage.not_ejected_text(window=False))
        return False
    print(gio_storage.ejected_text())
    return True


def _print_present(present, verbose: bool) -> None:
    if not present:
        return
    print(_("Already on the device: {count}").format(count=len(present)))
    if verbose:
        for u in present:
            print("  " + _update_line(u))
            print(f"      -> {u.device_path}")


def cmd_updates(args) -> int:
    from openexpress import updates as upd

    st, dev = open_device(args)
    client = GarminClient(Settings.load())
    resp = client.software_updates(dev.raw, include_reinstalls=args.reinstalls)
    # Only reads the device.
    items = upd.mark_present(st, upd.parse_updates(resp), upd.component_count(dev))
    if not args.reinstalls:
        items = [u for u in items if not u.is_reinstall]
    # A system update that is on the device already waits for the device to install it.
    staged = [] if args.reinstalls else upd.staged(items)
    # Offered by the server although the device has the very same file: not an update.
    present = [u for u in items if u.on_device and not any(u is s for s in staged)]
    print(_("{device}, software {version}").format(
        device=dev.description, version=dev.software_version_str))
    if upd.beta_offered(resp):
        print(_(upd.BETA_OFFERED))
    if not args.install:
        listed = items if args.reinstalls else [u for u in items if not u.on_device]
        if not listed:
            if staged:
                _print_staged(staged, st)  # not up to date: the update waits for the device
            else:
                print(_("Software is up to date.") if not args.reinstalls
                      else _("The server returned no items."))
            _print_present(present, args.verbose)
            return 0
        visible = [u for u in listed if not u.is_metadata or args.verbose]
        print((_("Reinstalls: {count}") if args.reinstalls
               else _("Available updates: {count}")).format(count=len(visible)))
        for u in sorted(visible, key=lambda x: (not x.is_system, x.severity.lower() != "critical",
                                                x.name)):
            print("  " + _update_line(u) + _flags(u))
            if args.verbose:
                print(f"      -> {u.device_path}")
            for c in u.changes[: (20 if args.verbose else 5)]:
                print(f"      • {c}")
        if not args.reinstalls:
            _print_present(present, args.verbose)
            _print_staged(staged, st)
        print("\n" + _("Install: {command}").format(
            command="openexpress updates --install PART_NUMBER [--reinstalls] --apply"))
        return 0

    # "all" means the updates: a file the device already has is not written again, unless
    # the reinstalls were asked for. A part number given by name is always taken.
    chosen = upd.select(items, args.install, keep_present=args.reinstalls)
    skipped = [u for u in upd.present_with_metadata(items) if not u.is_metadata
               and not any(u is c for c in chosen)] if args.install == ["all"] else []
    if skipped:
        print(ngettext("Skipped {count} file that is already on the device.",
                       "Skipped {count} files that are already on the device.",
                       len(skipped)).format(count=len(skipped)))
        if args.verbose:
            for u in skipped:
                print("  " + _update_line(u))
        if not chosen:
            print(_("Nothing to install: every offered file is already on the device."))
            if any(any(u is s for s in staged) for u in skipped):
                print(_to_install_staged(st))  # what is left to do
            return 0
    if not chosen:
        # "all" with nothing offered (a part number that is not offered is an error in select).
        print(_("Software is up to date.") if not args.reinstalls
              else _("The server returned no items."))
        return 0
    need, free = upd.check_space(st, chosen)
    print(_("To install:"))
    for u in upd.sort_for_install(chosen):
        print(f"  {u.part_number} {u.version}  {u.title}{_beta_mark(u)}  ({_human(u.size)}) "
              f"-> {u.device_path}")
    print(_("Space: {needed} needed, {free} free").format(needed=_human(need), free=_human(free)))
    eulas = sorted({u.eula_url for u in chosen if u.eula_url})
    for e in eulas:
        print(_("License (EULA): {url}").format(url=e))
    if any(u.is_system for u in chosen):
        print(_(upd.SYSTEM_UPDATE_WARNING))
    if _loses_data_on_update(client, dev):
        print(_(upd.DATA_LOSS_WARNING_DRIVE if getattr(st, "mass_storage", False)
                else upd.DATA_LOSS_WARNING_CLI))
    if not args.apply:
        print("\n" + _("Dry run: nothing was downloaded or written. Add --apply "
                       "(this means you accept the licenses above)."))
        return 0
    rep = upd.install(chosen, st, session=client.http, progress=lambda s: print("  " + s),
                      transfer_progress=_Bar())
    n = len(rep.installed)
    print("\n" + ngettext("Copied {count} file.", "Copied {count} files.", n).format(count=n))
    if n and getattr(st, "mass_storage", False) and not _eject_drive(st):
        return 1  # the cable must stay in: no steps that begin with unplugging it
    if n:
        print(_(FINISH_STEPS))
    return 0


def _loses_data_on_update(client, dev) -> bool:
    """Whether Garmin files the device (unit-info, asked here once) as a kind that loses the
    data it holds when its software is updated (point 6 of package 3). A failed request only
    means no warning."""
    from openexpress import updates as upd

    if not dev.unit_id:
        return False
    try:
        info = client.unit_info(dev.unit_id, dev.part_number)
    except ApiError as e:  # only for a warning; the text may quote the answer
        log.info("updates: unit-info failed (%s), no data-loss check", type(e).__name__)
        return False
    return upd.loses_data_on_update(info)


def _load_maps(args, client, own: bool = False):
    """The device and Garmin's maps for it. `own`: a command on maps of your own, which works
    whatever map service Garmin serves the device through; any other command is refused when
    map updates for the device are not supported."""
    from openexpress import maps as mp

    st, dev = open_device(args)
    if args.path:  # a folder (maybe a copy): the universal map service
        all_maps, listed = mp.read_maps(client.universal_maps(dev.raw))
        mp.apply_restrictions(all_maps)  # no account for a folder: a restricted map is marked
    else:  # MapsNotSupported when Garmin serves the device's maps another way
        try:
            all_maps, listed = mp.check(client, dev, country=lambda: _account_country(dev))
        except mp.MapsNotSupported:
            if not own:
                raise
            # A command on maps of your own does not depend on it: Garmin's maps are none,
            # so their names alone are kept apart (ownmaps.is_garmin_name).
            all_maps, listed = [], 0
    mp.mark_waiting(st, all_maps)
    return st, dev, all_maps, listed


def _account_country(dev) -> str | None:
    """The country of the Garmin account, for a map Garmin does not offer in a country: only
    with a saved login and for a device of the account (its ownership checked first);
    otherwise, or when it cannot be read, None and the map is only marked."""
    import requests

    from openexpress import service
    from openexpress.errors import UserFacingError

    try:
        client = make_client(Settings.load(), need_auth=False)
        if client.auth is None:
            return None
        service.require_account_device(client, dev.unit_id)
        return client.profile_country()
    except (UserFacingError, requests.RequestException, ValueError) as e:
        log.info("maps: the account's country not read (%s)", type(e).__name__)
        return None


def _url_salt() -> str | None:
    salt = load_consumer().url_salt
    if not salt:
        raise SetupError(_("Download URL signing data is missing. Run: {command}").format(
            command="openexpress setup --utilities-dll /path/to/Utilities.dll"))
    return salt


@contextlib.contextmanager
def _own_map_hints():
    """A refusal of ownmaps.py, with what to do about it on the command line."""
    from openexpress import maps as mp
    from openexpress import ownmaps

    try:
        yield
    except ownmaps.OwnMapError as e:
        raise mp.MapError(e.text(window=False)) from e


def _maps_add(args, dev, st, all_maps) -> int:
    from openexpress import ownmaps

    with _own_map_hints():
        plan = ownmaps.plan_add(dev, st, all_maps, Path(args.file), args.name)
    print(_("Map: {title}").format(title=plan.title or plan.name))
    print("  " + _("{file} ({size}) will be written to {path}").format(
        file=plan.local.name, size=_human(plan.size), path=plan.path))
    if plan.free is not None:
        print(_("Free space on the device: {free} (the map takes {size})").format(
            free=_human(plan.free), size=_human(plan.size)))
    if not args.apply:
        print(_("Dry run. Add --apply."))
        return 0
    with _own_map_hints():
        path = ownmaps.add(plan, dev, st, transfer_progress=_Bar())
    print(_("Wrote {path} ({size}).").format(path=path, size=_human(plan.size)))
    return 0


def _maps_remove_own(args, dev, st, all_maps) -> int:
    from openexpress import maps as mp
    from openexpress import ownmaps

    plan = ownmaps.plan_remove(dev, st, all_maps, args.name)
    if not plan.on_device:
        if not args.apply:
            raise mp.MapError(_("{name} is no longer on the device. Run the command with "
                                "--apply to take it off the list of own maps.").format(
                name=plan.name))
        ownmaps.remove(plan, dev, st)
        print(_("Took {name} off the list of own maps; the file was not on the device.").format(
            name=plan.name))
        return 0
    print(_("Removing own map: {title}").format(title=plan.title or plan.name))
    print(f"  - {plan.path}  ({_human(plan.size)})")
    if plan.free is not None:
        print(_("Free space on the device: {free} (removing it frees {size})").format(
            free=_human(plan.free), size=_human(plan.size)))
    if not args.apply:
        print(_("Dry run. Add --apply."))
        return 0
    ownmaps.remove(plan, dev, st)
    print(_("Removed {path} ({size}).").format(path=plan.path, size=_human(plan.size)))
    return 0


def _incomplete_maps_text(count: int) -> str:
    """`maps update` leaves out the maps partly on the device: what to do with them."""
    return ngettext("{count} map is incomplete on the device; install it again with "
                    "openexpress maps install NAME.",
                    "{count} maps are incomplete on the device; install them again with "
                    "openexpress maps install NAME.", count).format(count=count)


def cmd_maps(args) -> int:
    from openexpress import maps as mp
    from openexpress import ownmaps

    if args.maps_cmd == "add":
        # Before the device is opened: is it a map at all, and may it have that name?
        with _own_map_hints():
            ownmaps.check_local(Path(args.file), args.name)
    client = GarminClient(Settings.load())
    own = args.maps_cmd == "add" or (args.maps_cmd == "remove" and args.own)
    st, dev, all_maps, listed = _load_maps(args, client, own=own)
    if args.maps_cmd == "add":
        return _maps_add(args, dev, st, all_maps)
    if args.maps_cmd == "remove" and args.own:
        return _maps_remove_own(args, dev, st, all_maps)
    if not listed:
        # One message instead of an empty list, as for a device without media.
        if args.maps_cmd in ("install", "remove"):
            raise mp.MapError(mp.no_maps_message())
        print(mp.no_maps_message())
        return 0
    if args.maps_cmd in (None, "list"):
        on_device = [m for m in all_maps if m.listed or m.waiting]
        shown = all_maps if args.all else on_device
        print((_("Maps for {device} (all):") if args.all
               else _("Maps for {device} (installed):")).format(device=dev.description))
        for m in sorted(shown, key=lambda m: (m not in on_device, m.map_type, m.name)):
            print(f"  {m.name:48} {m.version:>9}  {_human(m.size):>9}  {m.status}")
            if (note := mp.restriction_note(m)) is not None:
                print("    " + note)
        if not args.all:
            n = len(all_maps) - len(on_device)
            print("\n" + ngettext("{count} more map is available (openexpress maps --all).",
                                  "{count} more maps are available (openexpress maps --all).",
                                  n).format(count=n))
        print(_("Free space on the device: {size}").format(size=_human(st.free_space())))
        own = ownmaps.list_own(dev, st, all_maps)
        if own:
            print("\n" + _("Own maps (not from Garmin):"))
            for o in own:
                title = o.title or _("(name not read)")
                print(f"  {o.filename:48} {_human(o.size):>9}  {title}")
        return 0

    if args.maps_cmd == "update":
        targets = [m for m in all_maps if m.update_available]
        incomplete = sum(1 for m in all_maps if m.incomplete)
        for m in all_maps:
            if m.would_update and m.restricted is True:
                print(_("{name}: Garmin does not offer this map in your account's country; "
                        "not updated.").format(name=m.name))
        if not targets:
            print(_("All installed maps are up to date."))
            if incomplete:
                print(_incomplete_maps_text(incomplete))
            return 0
        if incomplete:
            print(_incomplete_maps_text(incomplete))
    else:
        targets = [mp.find(all_maps, args.name)]

    # The old files Garmin's cleanup rules would take (point 8, stage 1): listed with the
    # plan, in a dry run and with --apply, and never removed.
    cleanup = mp.cleanup_lines(mp.cleanup_plan(st, dev, all_maps))
    if cleanup:
        print(cleanup[0])
        for path in cleanup[1:-1]:
            print(f"  - {path}")
        print(cleanup[-1])

    for m in targets:
        if args.maps_cmd == "remove":
            if not m.can_uninstall:
                # Refused before anything is listed: the files are not going to be removed.
                raise mp.MapError(mp.cannot_remove_message(m))
            files = mp.removal_list(all_maps, m, rewriting=False)
            print(ngettext("Removing map {name}: {count} file",
                           "Removing map {name}: {count} files", len(files)).format(
                name=m.name, count=len(files)))
            for p in files:
                print(f"  - {p}  ({_human(st.size(p))})")
            if not args.apply:
                print(_("Dry run. Add --apply."))
                continue
            removed = mp.uninstall(m, all_maps, st, progress=lambda s: print("  " + s))
            print(ngettext("Removed {count} file.", "Removed {count} files.",
                           len(removed)).format(count=len(removed)))
            continue

        if m.waiting == mp.WAIT_INSTALL:
            # Refused before anything is listed: it is written already.
            raise mp.MapError(mp.waiting_message(m))
        if not mp.can_install(m):
            # Refused before anything is promised, the same reason as with --apply.
            raise mp.MapError(mp.cannot_install_message(m))
        removing = mp.removal_list(all_maps, m, rewriting=True)
        need = mp.space_needed(st, m, removing)
        if m.installed:
            msg = ngettext("Updating map {name} {version}: {count} file, download {size}, "
                           "space on the device {needed} ({free} free)",
                           "Updating map {name} {version}: {count} files, download {size}, "
                           "space on the device {needed} ({free} free)", len(m.files))
        else:
            msg = ngettext("Installing map {name} {version}: {count} file, download {size}, "
                           "space on the device {needed} ({free} free)",
                           "Installing map {name} {version}: {count} files, download {size}, "
                           "space on the device {needed} ({free} free)", len(m.files))
        print(msg.format(name=m.name, version=m.version, count=len(m.files),
                         size=_human(m.size), needed=_human(need),
                         free=_human(st.free_space())))
        for f in mp.write_order(m):
            print(f"  + {f.device_path}  {_human(f.size)}")
        for p in removing:
            print(f"  - {p}")
        for e in m.eula_urls:
            print(_("License (EULA): {url}").format(url=e))
        if not args.apply:
            print(_("Dry run: nothing was downloaded or written. Add --apply."))
            continue
        rep = mp.install(
            m, all_maps, st,
            activate=lambda ident: client.activate_maps(dev.raw, [ident]),
            session=client.http,
            url_salt=_url_salt(),
            progress=lambda s: print("  " + s),
            transfer_progress=_Bar(),
        )
        print(ngettext("Wrote {count} map file and {unlocks} unlock files.",
                       "Wrote {count} map files and {unlocks} unlock files.",
                       len(rep.written)).format(count=len(rep.written),
                                                unlocks=len(rep.unlocks)))
        print(_("The map will be loaded after the device is unplugged."))
    return 0


APP_TYPE_OPTIONS = {"app": 1, "widget": 2, "watch-face": 3, "data-field": 4, "music-app": 7,
                    "activity": 8}


def _find_app(items, query: str, app_type: int | None = None):
    """One app by name, part of the name or ID; `app_type` picks between entries that share
    a name and ID (built-in items listed both as an app and as a widget)."""
    from openexpress import ciq

    if app_type is not None:
        items = [a for a in items if a.app_type == app_type]
    q = query.casefold()
    exact = [a for a in items if a.name.casefold() == q or str(a.app_id) == q]
    partial = exact or [a for a in items if q in a.name.casefold()]
    if len(partial) == 1:
        return partial[0]
    if not partial:
        raise SetupError(_("No app matches '{query}'").format(query=query))

    def label(a):
        kind = ciq.APP_TYPES.get(a.app_type)
        return f"{a.name} ({_(kind) if kind else a.app_type})"

    names = "; ".join(label(a) for a in partial)
    if len({a.name.casefold() for a in partial}) == 1:
        raise SetupError(_("Several entries match: {names}. Choose one with --type.").format(
            names=names))
    raise SetupError(_("Ambiguous name, matches: {names}").format(names=names))


def _app_status(disabled: bool) -> str:
    # TRANSLATORS: state of a Connect IQ app or built-in activity on the watch
    return _("disabled") if disabled else _("enabled")


def _apps_queue_only(args, dev, st, client, api, account) -> int:
    """`apps install` on a device without an app list (OUT.BIN, #14): the app is queued in
    Garmin Connect for the phone or Garmin Express; nothing is written to the device."""
    from openexpress import ciq

    target = _find_app(account, args.name)
    print(_("Install {name} {version} ({size})").format(
        name=target.name, version=target.version_name, size=_human(target.size)))
    targets, _skipped, allowed = _apps_consent(args, [target])
    if not args.apply:
        if targets:
            print("  " + ciq.no_app_list_text([target.name]))
        print(_("Dry run. Add --apply."))
        return 0 if targets else 1
    if not targets:
        return 1
    rep = ciq.install_apps(dev, st, client, api, targets, allowed=allowed)
    print(ciq.no_app_list_text(rep.for_phone))
    return 0


def _apps_consent(args, targets):
    """The apps with new permissions among `targets`: what they ask for, and whether
    --allow-permissions allows them. Returns the apps to go on with, those set aside and the
    IDs allowed."""
    from openexpress import ciq

    asking = ciq.needs_consent(targets)
    allow = getattr(args, "allow_permissions", False)
    for a in asking:
        for line in ciq.permissions_text(a).splitlines():
            print("  " + line)
        if allow:
            print("  " + _("{name}: needs new permissions; allowed with "
                           "--allow-permissions.").format(name=a.name))
        else:
            print("  " + _("{name}: needs new permissions; nothing is done for it. Add "
                           "--allow-permissions to allow them.").format(name=a.name))
    skipped = [] if allow else asking
    return ([t for t in targets if t not in skipped], skipped,
            {a.app_id for a in asking} if allow else set())


def _apps_order(args, dev, st, state) -> int:
    """`apps order`: the saved copies of the app list, or the order of one of them put back."""
    from openexpress import ciq

    backups = paths.default_backup_dir()
    if not args.source:
        copies = ciq.app_list_copies(dev, backups)
        if not copies:
            # The folder of this device is named after its unit ID: only the one above it.
            print(_("No copies of the app list in {path}.").format(path=backups))
            return 0
        print(_("Copies of the app list (OUT.BIN); entries on the device now: {count}").format(
            count=len(state.listed)))
        for c in copies:
            # The same measure as the window: the apps to move.
            plan = ciq.plan_order(state, c.state)
            print("  " + _("{name}: {entries} entries, {difference}").format(
                name=c.name, entries=len(c.state.apps), difference=ciq.order_difference(plan)))
        print(_("Show what a copy would change: openexpress apps order --from NAME"))
        return 0

    copy = ciq.load_app_list_copy(dev, backups, args.source)
    if ciq.changes_waiting(dev, st):
        raise ciq.CiqError(_("the device has not applied earlier changes yet; unplug it, wait "
                             "until it is ready and try again"))
    plan = ciq.plan_order(state, copy.state)
    log.info("apps order: plan with %d entries, %d would move; apply: %s", len(plan.entries),
             len(plan.moved), args.apply)
    print(_("Order of the apps from the copy {name}").format(name=copy.name))
    if not plan.moved:
        print(_("The apps on the device are already in this order."))
        return 0
    for line, indented in ciq.describe_order(plan, None if args.all else 10):
        print(("  " if indented else "") + line)
    print(_("App list to be written (IN.BIN): {entries} entries; the device lists {count} "
            "(OUT.BIN)").format(entries=len(plan.entries),
                                count=sum(1 for a in state.apps if not a.pending)))
    print("  " + _("the same apps with the same flags; only the order changes"))
    if not args.apply:
        print(ciq.app_list_copy_note(dev, st))
        print(_("Dry run. Add --apply."))
        return 0
    _plan, saved = ciq.restore_order(dev, st, copy.state, backups)
    print(_("Copy of the current app list: {name}").format(name=saved))
    print(_("Wrote the new order to the device; it takes effect after the device is "
            "unplugged."))
    return 0


def _remove_app_directly(args, dev, st, state, app, api) -> int:
    """`apps remove` on a device that hides its app folder: over a PTP session."""
    from openexpress import ciq

    app_file = ciq.app_file_path(dev, app)
    print(_("Remove {name} from the device: {files}").format(name=app.name, files=app_file))
    settings = ciq.settings_file_path(dev, app)
    if settings:
        print("  " + _("and its settings file, if the device has one: {path}").format(
            path=settings))
    print("  " + _("The device does not show this file to ordinary file access. openExpress "
                   "will delete it over a direct USB connection; the device disappears from "
                   "the file manager for a moment."))
    preview = ciq.preview_removal(state, app)
    print(_("App list to be written (IN.BIN): {entries} entries; the device lists {count} "
            "(OUT.BIN)").format(entries=preview.entries, count=preview.on_device))
    if preview.pending or not preview.kept:
        raise ciq.CiqError(_("The app list that would be written does not match the device. "
                             "Nothing was changed."))
    print("  " + _("every other app on the device is kept, in the same order and with the "
                   "same flags"))
    kind = ciq.APP_TYPES.get(app.app_type)
    kind = _(kind) if kind else app.app_type
    print("  " + _("to be removed: {name} ({type})").format(name=app.name, type=kind))
    if not args.apply:
        print(ciq.app_list_copy_note(dev, st))
        print(_("Dry run. Add --apply."))
        return 0
    removed = ciq.remove(dev, st, app)
    if app_file not in removed:
        print(_("The app file was already gone; only the app list was updated."))
    if args.forget:
        api.forget(dev, [app.app_id])
        msg = ngettext("Removed {count} file. Also removed from your account list.",
                       "Removed {count} files. Also removed from your account list.",
                       len(removed))
    else:
        msg = ngettext("Removed {count} file.", "Removed {count} files.", len(removed))
    print(msg.format(count=len(removed)))
    print("  " + _("removed: {name} ({type})").format(name=app.name, type=kind))
    print(_("The device finishes the removal after it is unplugged."))
    return 0


def _print_in_bin_preview(preview) -> None:
    """The app list (IN.BIN) that an install would write to the device."""
    from openexpress import ciq

    print(_("App list to be written (IN.BIN): {entries} entries; the device lists {count} "
            "(OUT.BIN)").format(entries=preview.entries, count=preview.on_device))
    if preview.pending:
        print("  " + ngettext("{count} more app is registered and waits for the device",
                              "{count} more apps are registered and wait for the device",
                              preview.pending).format(count=preview.pending))
    print("  " + (_("every app on the device is kept, in the same order and with the same flags")
                  if preview.kept else
                  _("WARNING: the new list does NOT match the apps on the device")))
    for name, app_type, disabled in preview.new:
        kind = ciq.APP_TYPES.get(app_type)
        print("  " + (_("new: {name} ({type}), added as inactive") if disabled
                      else _("new: {name} ({type}), added as active")).format(
            name=name, type=_(kind) if kind else app_type))
    if not preview.kept:
        raise ciq.CiqError(_("The app list that would be written does not match the device. "
                             "Nothing was changed."))


def cmd_apps(args) -> int:
    from openexpress import ciq

    st, dev = open_device(args)
    settings = Settings.load()
    client = make_client(settings, need_auth=True)
    from openexpress import service

    service.require_account_device(client, dev.unit_id)
    api = ciq.StoreApi(client)
    locale = language_tag(settings.locale)
    state = ciq.read_installed(dev, st)
    account = api.account_apps(dev, locale)
    ciq.name_pending(state, account)
    installed_ids = {a.app_id for a in state.apps}
    cmd = args.apps_cmd or "list"

    if cmd == "list":
        updates = {u.app_id: u for u in api.updates(dev, state.store_apps, locale)}
        if state.no_app_list:  # no app was ever installed (#14): none, not an error
            print(ciq.no_apps_text())
        else:
            print(ngettext("Connect IQ on {device}: {count} store app, {slots} free slots, "
                           "{size} free",
                           "Connect IQ on {device}: {count} store apps, {slots} free slots, "
                           "{size} free", len(state.store_apps)).format(
                device=dev.description, count=len(state.store_apps),
                slots=state.available_slots, size=_human(state.available_space)))
        if state.unreadable:
            print(ngettext("WARNING: {count} entry of the app list on the device cannot be read; "
                           "apps cannot be installed, updated or removed.",
                           "WARNING: {count} entries of the app list on the device cannot be "
                           "read; apps cannot be installed, updated or removed.",
                           state.unreadable).format(count=state.unreadable))
        for a in state.store_apps:
            upd = updates.get(a.app_id)
            note = ("  " + _("update available: {version}").format(version=upd.version_name)
                    + (" " + _("(permissions changed)") if upd.permissions_changed else "")
                    if upd else "")
            if a.pending:
                note = "  " + _("will be installed after the device is unplugged")
            elif a.pending_removal:
                note = "  " + _("will be removed after the device is unplugged")
            print(f"  {a.name:36} {a.type_name:11} v{a.version or '?':<5} "
                  f"{_app_status(a.disabled):9}{note}")
        missing = [a for a in account if a.app_id not in installed_ids]
        if missing:
            print("\n" + _("In your Garmin account but not on the device:"))
            for a in missing:
                print(f"  {a.name:36} {a.store_type:11} {a.version_name:10} {_human(a.size)}")
        if args.builtin:
            print("\n" + _("Built-in activities and apps:"))
            for a in state.apps:
                if a.preloaded:
                    print(f"  {a.name:36} {a.type_name:11} {_app_status(a.disabled)}")
        print("\n" + _("New apps are chosen in the Connect IQ store (website or phone); "
                       "they then appear in the list above."))
        return 0

    if cmd == "install" and state.no_app_list:
        return _apps_queue_only(args, dev, st, client, api, account)
    if cmd != "list":
        # Before anything is queued or written: the device's app list must be complete.
        ciq.require_complete(state)

    if cmd == "order":
        return _apps_order(args, dev, st, state)

    if cmd in ("install", "update"):
        if cmd == "install":
            targets = [_find_app(account, args.name)]
        else:
            targets = api.updates(dev, state.store_apps, locale)
            if not targets:
                print(_("All Connect IQ apps are up to date."))
                return 0
        for a in targets:
            flag = "  " + _("(permissions changed)") if a.permissions_changed else ""
            msg = (_("Update {name} {version} ({size})") if a.app_id in installed_ids
                   else _("Install {name} {version} ({size})"))
            print(msg.format(name=a.name, version=a.version_name, size=_human(a.size)) + flag)
        targets, skipped, allowed = _apps_consent(args, targets)
        if not targets:
            if not args.apply:
                print(_("Dry run. Add --apply."))
            return 1
        waiting = ciq.queued_app_ids(client.device_messages(dev.unit_id))
        for a in targets:
            if a.app_id in waiting:
                print("  " + _("{name}: already waits in the queue in Garmin Connect; no new "
                               "request will be sent.").format(name=a.name))
        # On some devices openExpress cannot install over USB yet: say so before queueing.
        dirs = {a.app_id: ciq.usb_dir_state(dev, st, ciq.store_datatype(a)) for a in targets}
        for a in targets:
            folder = ciq.app_dir(dev, ciq.store_datatype(a))
            if dirs[a.app_id] == ciq.SANDBOXED:
                print("  " + f"{a.name}: " + ciq.sandbox_text(a.app_id in installed_ids))
            elif dirs[a.app_id] == ciq.REFUSED:
                print("  " + _("{name}: openExpress cannot install apps on this device over USB "
                               "yet. The app will be queued in Garmin Connect and installed by "
                               "the phone or Garmin Express.").format(name=a.name))
            elif dirs[a.app_id] == ciq.DIRECT:
                print("  " + _("{name}: the device does not show the app folder {path} to "
                               "ordinary file access. openExpress will write the app over a "
                               "direct USB connection; the device disappears from the file "
                               "manager for a moment.").format(name=a.name, path=folder))
            elif dirs[a.app_id] == ciq.MISSING:
                print("  " + _("{name}: the app folder {path} does not exist on the device. "
                               "openExpress will try to create it; if the device refuses, the "
                               "app is only queued in Garmin Connect and installed by the phone "
                               "or Garmin Express.").format(name=a.name, path=folder))
        over_usb = [a for a in targets if dirs[a.app_id] not in (ciq.REFUSED, ciq.SANDBOXED)]
        log.info("apps %s: %s; app folder: %s; already queued: %d; apply: %s", cmd,
                 ", ".join(a.name for a in targets), applog.counts(dirs.values()),
                 sum(1 for a in targets if a.app_id in waiting), args.apply)
        # What --apply would refuse before it asks Garmin for anything, by app ID.
        refused = ciq.install_refusals(dev, st, state, over_usb)
        over_usb = [a for a in over_usb if a.app_id not in refused]
        if over_usb:
            _print_in_bin_preview(ciq.preview_in_bin(state, over_usb))
        if not args.apply:
            for a in targets:
                if a.app_id in refused:
                    print("  " + _("ERROR {item}: {error}").format(
                        item=a.name, error=refused[a.app_id]))
            if len(refused) < len(targets):
                if over_usb:
                    print(ciq.app_list_copy_note(dev, st))
                print(_("Dry run. Add --apply."))
            return 1 if refused or skipped else 0
        rep = ciq.install_apps(dev, st, client, api, targets,
                               progress=lambda s: print("  " + s), allowed=allowed)
        written = [n for n in rep.installed if n in rep.direct and n not in rep.updated]
        updated = [n for n in rep.installed if n in rep.updated]
        installed = [n for n in rep.installed if n not in rep.direct]
        if written:
            print(ciq.written_text(written))
        if updated:
            print(ciq.update_written_text(updated))
            written += updated
        if installed or (not written and (rep.failed or not rep.for_phone)):
            # TRANSLATORS: shown after "Installed:" when no app was installed
            none = _("none")
            print(_("Installed: {names}").format(names=", ".join(installed) or none))
        for name, err in rep.failed:
            print("  " + _("ERROR {item}: {error}").format(item=name, error=err))
        sandboxed = [n for n in rep.for_phone if n in rep.sandboxed]
        if sandboxed:
            print(", ".join(sandboxed) + ": " + ciq.sandbox_text(
                all(n in rep.sandboxed_updates for n in sandboxed), queued=True))
        for_phone = [n for n in rep.for_phone if n not in sandboxed]
        if for_phone:
            print(_("Queued in Garmin Connect: {names}. openExpress cannot install apps on "
                    "this device over USB yet; they will be installed by the phone or Garmin "
                    "Express.").format(
                names=", ".join(for_phone)))
        if not rep.installed and not rep.failed and not rep.for_phone:
            print(_("The server has not queued the app yet; run `openexpress sync` in a moment."))
        return 0 if not rep.failed and not skipped else 1

    app = _find_app(state.apps, args.name, APP_TYPE_OPTIONS.get(getattr(args, "type", None)))
    if cmd == "remove":
        if not app.removable:
            raise ciq.CiqError(_("{name} is part of the device software and cannot be removed")
                               .format(name=app.name))
        if app.pending or app.pending_removal:
            raise ciq.CiqError(ciq.waiting_text(app))
        # The app file alone decides the way (ciq.removal_way), as in the window.
        way = ciq.removal_way(dev, st, app)
        if way == ciq.DIRECT:
            return _remove_app_directly(args, dev, st, state, app, api)
        paths_ = ciq.files_on_device(dev, st, app) if way == ciq.MOUNTED else []
        if not paths_:
            # E.g. the fēnix 8: no app files are visible over USB, so there is nothing to delete.
            if way == ciq.SANDBOXED:
                print(f"{app.name}: " + ciq.sandbox_removal_text())
                if not args.forget:
                    print(_("To remove it only from your account list, use --forget."))
            else:
                print(_("{name} cannot be removed over USB: this device shows no app files. "
                        "Remove it on the device itself or in Garmin Connect on the "
                        "phone.").format(name=app.name))
            if not args.forget:
                return 1
            if not args.apply:
                print(_("Would remove it from your account list. Add --apply."))
                return 0
            api.forget(dev, [app.app_id])
            print(_("Removed {name} from your account list.").format(name=app.name))
            return 0
        print(_("Remove {name} from the device: {files}").format(
            name=app.name, files=", ".join(paths_) or "-"))
        if not args.apply:
            print(ciq.app_list_copy_note(dev, st))
            print(_("Dry run. Add --apply."))
            return 0
        removed = ciq.remove(dev, st, app)
        if args.forget:
            api.forget(dev, [app.app_id])
            msg = ngettext("Removed {count} file. Also removed from your account list.",
                           "Removed {count} files. Also removed from your account list.",
                           len(removed))
        else:
            msg = ngettext("Removed {count} file.", "Removed {count} files.", len(removed))
        print(msg.format(count=len(removed)))
        return 0

    enable = cmd == "enable"
    # Refused also in a dry run, as with --apply: a list that waits cannot be read whole.
    ciq.require_readable_waiting_list(state)
    print((_("Enable {name}") if enable else _("Disable {name}")).format(name=app.name))
    if not args.apply:
        print(ciq.app_list_copy_note(dev, st))
        print(_("Dry run. Add --apply."))
        return 0
    ciq.set_enabled(dev, st, app.app_id, enable, app.app_type)
    print(_("Saved. The device applies the change after you disconnect it."))
    return 0


def cmd_primary(args) -> int:
    from openexpress import service

    _st, dev = open_device(args)
    client = make_client(Settings.load(), need_auth=True)
    wearable = args.wearable or getattr(args, "set_wearable", False)
    if args.primary_cmd == "set":
        return (_primary_set_wearable if wearable else _primary_set)(client, dev, args.apply)
    if wearable:
        return _primary_wearable(client, dev)
    status = service.primary_training_status(client, dev)
    if status.unit_id is None:
        print(_("No primary training device is set for this Garmin account."))
    elif status.is_this_device:
        print(_("Primary training device: {name} (this device)").format(
            name=status.name or dev.description))
    else:
        print(_("Primary training device: {name}").format(
            name=status.name or _("a device that is not on the account's list")))
        print(_("The connected device ({device}) is not the primary training device.").format(
            device=dev.description))
    if status.capable is False:
        print(_("The connected device cannot be a primary training device."))
    return 0


def _primary_set(client, dev, apply: bool) -> int:
    from openexpress import service

    before = service.set_primary_training(client, dev, apply=apply)
    old = before.name or _("a device that is not on the account's list")
    if before.is_this_device:
        print(_("{device} is already the primary training device.").format(
            device=dev.description))
        return 0
    if not apply:
        if before.unit_id is None:
            print(_("No primary training device is set for this Garmin account."))
        else:
            print(_("Primary training device: {name}").format(name=old))
        print(_("Would change it to: {device}").format(device=dev.description))
        print(_("Dry run. Add --apply."))
        return 0
    if before.unit_id is None:
        print(_("Primary training device set to {device}.").format(device=dev.description))
        return 0
    print(_("Primary training device changed from {old} to {new}.").format(
        old=old, new=dev.description))
    print(_("To change it back, connect {old} and run `openexpress primary set --apply`, "
            "or change it in Garmin Connect.").format(old=old))
    return 0


def _primary_wearable(client, dev) -> int:
    from openexpress import service

    status = service.primary_wearable_status(client, dev)
    if not status.set:
        print(_("No primary wearable is set for this Garmin account."))
    elif status.is_this_device:
        print(_("Primary wearable: {name} (this device)").format(
            name=status.name or dev.description))
    else:
        print(_("Primary wearable: {name}").format(
            name=status.name or _("a device that is not on the account's list")))
        print(_("The connected device ({device}) is not the primary wearable.").format(
            device=dev.description))
    if status.capable is False:
        print(_("The connected device cannot be a primary wearable."))
    return 0


def _primary_set_wearable(client, dev, apply: bool) -> int:
    from openexpress import service

    before = service.set_primary_wearable(client, dev, apply=apply).before
    old = before.name or _("a device that is not on the account's list")
    if before.is_this_device:
        print(_("{device} is already the primary wearable.").format(device=dev.description))
        return 0
    if not apply:
        if not before.set:
            print(_("No primary wearable is set for this Garmin account."))
        else:
            print(_("Primary wearable: {name}").format(name=old))
        print(_("Would change it to: {device}").format(device=dev.description))
        print(_("Dry run. Add --apply."))
        return 0
    if not before.set:
        print(_("Primary wearable set to {device}.").format(device=dev.description))
        return 0
    print(_("Primary wearable changed from {old} to {new}.").format(
        old=old, new=dev.description))
    print(_("To change it back, connect {old} and run "
            "`openexpress primary set --wearable --apply`, or change it in Garmin Connect."
            ).format(old=old))
    return 0


MUSIC_KIND_LINES = {
    "track": N_("Tracks: {count} ({size})"),
    "podcast": N_("Podcasts: {count} ({size})"),
    "audiobook": N_("Audiobooks: {count} ({size})"),
}


MIN_MUSIC_PATTERN = 3


def match_music_paths(device_paths: list[str], roots: list[str], patterns: list[str]) -> list[str]:
    """Device paths selected by `music remove` patterns (case-insensitive).

    A pattern matches the full device path, a part of the file name, or a part of an
    Artist/Album folder name; the top media folders (`roots`, e.g. "Music") never match.
    """
    wanted = [p.strip().casefold() for p in patterns if p.strip()]
    tops = sorted({r.strip("/").casefold() for r in roots if r.strip("/")}, key=len, reverse=True)
    out: dict[str, None] = {}
    for path in device_paths:
        norm = path.strip("/")
        low = norm.casefold()
        rel = low
        for top in tops:
            if low.startswith(top + "/"):
                rel = low[len(top) + 1:]
                break
        else:
            rel = low.split("/", 1)[1] if "/" in low else low
        parts = rel.split("/")
        name, folders = parts[-1], parts[:-1]
        for w in wanted:
            if w.strip("/") == low or w in name or any(w in folder for folder in folders):
                out[path] = None
                break
    return list(out)


def cmd_music(args) -> int:
    from openexpress import music

    st, dev = open_device(args)
    cmd = args.music_cmd or "list"
    if cmd == "remove":
        bad = [p for p in args.paths if len(p.strip()) < MIN_MUSIC_PATTERN]
        if bad:
            print(_("Error: each TEXT must have at least {count} characters: {texts}").format(
                count=MIN_MUSIC_PATTERN, texts=", ".join(repr(p) for p in bad)), file=sys.stderr)
            return 1
    kinds = music.supported_kinds(dev)
    if not kinds:
        if cmd != "list":
            raise music.MusicError(music.no_media_message())
        print(music.no_media_message())
        return 0
    if cmd == "list":
        items = music.list_device(dev, st)
        by_kind: dict[str, list] = {}
        for i in items:
            by_kind.setdefault(i.kind, []).append(i)
        for kind in kinds:
            files = by_kind.get(kind, [])
            print(_(MUSIC_KIND_LINES[kind]).format(
                count=len(files), size=_human(sum(f.size for f in files))))
            if args.verbose:
                for f in files:
                    print(f"    {f.path}")
        pls = music.list_playlists(dev, st)
        if pls:
            print(_("Playlists: {count}: {names}").format(count=len(pls), names=", ".join(pls)))
        elif music.supports_playlists(dev):
            print(_("Playlists: {count}").format(count=len(pls)))
        print(_("Free space: {size}").format(size=_human(st.free_space())))
        return 0

    if cmd == "add":
        tracks = music.scan([Path(p) for p in args.paths], dev, forced_kind=args.kind)
        plan = music.plan_copy(tracks, dev, st)
        print(ngettext("Found {count} playable file; {to_copy} to copy ({size}), "
                       "{already} already on the device.",
                       "Found {count} playable files; {to_copy} to copy ({size}), "
                       "{already} already on the device.", len(tracks)).format(
            count=len(tracks), to_copy=len(plan.to_copy), size=_human(plan.size),
            already=len(plan.already)))
        for t, dest in plan.to_copy[: (None if args.verbose else 15)]:
            note = music.replaced_note(plan, t, dest)
            print(f"  ~ {dest}  {note}" if note else f"  + {dest}")
        if len(plan.to_copy) > 15 and not args.verbose:
            more = len(plan.to_copy) - 15
            print("  " + ngettext("… and {count} more", "… and {count} more", more).format(
                count=more))
        if plan.replaced:
            # Also when the list above is cut short.
            print(music.replaced_text(plan))
        if not args.apply:
            print(_("Dry run. Add --apply."))
            return 0
        music.copy(plan, st, progress=lambda s: print("  " + s), transfer_progress=_Bar())
        if args.playlist:
            dests = [d for _t, d in plan.to_copy + plan.already]
            print(_("Playlist: {path}").format(
                path=music.write_playlist(dev, st, args.playlist, dests)))
        print(_("Done."))
        return 0

    if cmd == "playlist":
        tracks = music.scan(music.read_m3u(Path(args.m3u)), dev)
        plan = music.plan_copy(tracks, dev, st)
        name = args.name or Path(args.m3u).stem
        print(ngettext("Playlist '{name}': {count} track, {to_copy} to copy ({size})",
                       "Playlist '{name}': {count} tracks, {to_copy} to copy ({size})",
                       len(tracks)).format(name=name, count=len(tracks),
                                           to_copy=len(plan.to_copy), size=_human(plan.size)))
        if plan.replaced:
            print(music.replaced_text(plan))
        if not args.apply:
            print(_("Dry run. Add --apply."))
            return 0
        music.copy(plan, st, progress=lambda s: print("  " + s), transfer_progress=_Bar())
        dests = [d for _t, d in plan.to_copy + plan.already]
        print(_("Playlist: {path}").format(path=music.write_playlist(dev, st, name, dests)))
        return 0

    # remove
    items = music.list_device(dev, st)
    pls = music.list_playlists(dev, st)
    roots = [f.path for kind in (*music.KINDS.values(), music.PLAYLIST)
             for f in dev.targets(kind)[:1]]
    matches = match_music_paths([i.path for i in items] + pls, roots, args.paths)
    print(_("Files to remove: {count}").format(count=len(matches)))
    for m in matches:
        print(f"  - {m}")
    if not args.apply:
        print(_("Dry run. Add --apply."))
        return 0
    n = len(music.remove(st, matches))
    print(ngettext("Removed {count} file.", "Removed {count} files.", n).format(count=n))
    return 0


def cmd_gui(args) -> int:
    from openexpress.gui.app import main as gui_main

    return gui_main()


def cmd_watch(args) -> int:
    from openexpress.watch import main as watch_main

    return watch_main()


def cmd_install_desktop(args) -> int:
    from openexpress import background, desktop

    if background.in_flatpak():
        print(_("In the Flatpak version, the menu entry is installed by Flatpak. Turn on "
                "background sync in the window: main menu, Preferences, Sync in the background."))
        return 0

    if args.remove:
        removed = desktop.uninstall(dry_run=not args.apply)
        for p in removed:
            print((_("Removed {path}") if args.apply else _("Would remove {path}")).format(path=p))
        if not removed:
            print(_("Nothing to remove."))
        elif not args.apply:
            print(_("Dry run. Add --apply."))
        elif any(p.name == desktop.SERVICE for p in removed):
            print(_("The background sync service was stopped and disabled."))
        return 0
    if desktop.flatpak_installed() and not args.force:
        print(_("The Flatpak of openExpress is installed and already provides the menu entry. "
                "A second entry with the same name would hide it. Use --force to install "
                "anyway, or turn on background sync in the Flatpak window."), file=sys.stderr)
        return 1
    written, removed = desktop.install(dry_run=not args.apply)
    for p in written:
        print((_("Wrote {path}") if args.apply else _("Would write {path}")).format(path=p))
    for p in removed:
        print((_("Removed old file {path}") if args.apply
               else _("Would remove old file {path}")).format(path=p))
    if not args.apply:
        print(_("Dry run. Add --apply."))
        return 0
    print("\n" + _("To enable background sync, run:"))
    print("  systemctl --user daemon-reload && systemctl --user enable --now " + desktop.SERVICE)
    return 0


# --- parser ----------------------------------------------------------------------------


# Messages generated by argparse itself; argparse looks them up through
# argparse._, which is pointed at our catalog in build_parser().
ARGPARSE_MESSAGES = [
    # TRANSLATORS: argparse header before the command syntax, keep the trailing space
    N_("usage: "),
    N_("positional arguments"),
    N_("options"),
    N_("show this help message and exit"),
    N_("show program's version number and exit"),
    N_("the following arguments are required: %s"),
    N_("unrecognized arguments: %s"),
    N_("invalid choice: %(value)r (choose from %(choices)s)"),
    N_("expected one argument"),
    N_("%(prog)s: error: %(message)s\n"),
]


def build_parser() -> argparse.ArgumentParser:
    argparse._ = _
    argparse.ngettext = ngettext
    p = argparse.ArgumentParser(
        prog="openexpress",
        description=_("Independent Garmin Express client for Linux."),
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("-v", "--verbose", action="store_true", help=_("more details"))
    p.add_argument("--lang", metavar="LANG",
                   help=_("interface language, e.g. {examples}").format(
                       examples=", ".join(i18n.available_languages())))
    # A device in mass storage mode is found by itself, like an MTP device.
    p.add_argument("--path",
                   help=_("a folder with a copy of a device's files, instead of a connected "
                          "device"))
    p.add_argument("--device-uri", help=_("MTP device URI, e.g. {example}").format(
        example="mtp://091e_51b4_…/"))
    p.add_argument("--no-wait", action="store_true",
                   help=_("do not wait for a device that was just plugged in and is still "
                          "starting"))
    sub = p.add_subparsers(dest="cmd")
    # Without a command the application window opens (as from the application menu), and
    # it is logged as the window. After add_subparsers: it would set "cmd" to None.
    p.set_defaults(func=cmd_gui, cmd="gui")

    s = sub.add_parser("setup", help=_("configuration: OAuth1 key, environment, locale"))
    s.add_argument("--from-installer", metavar="GarminExpress.exe",
                   help=_("read the needed data from the Garmin Express installer for Windows "
                          "(GarminExpress.exe from garmin.com); it is only read, nothing is "
                          "installed"))
    s.add_argument("--from-dll", help=_("path to Networking.dll from Garmin Express"))
    s.add_argument("--utilities-dll",
                   help=_("path to Utilities.dll (default: next to Networking.dll)"))
    s.add_argument("--key")
    s.add_argument("--secret")
    s.add_argument("--environment", choices=sorted(ENVIRONMENTS))
    s.add_argument("--locale", help=_("e.g. {example}").format(example="pl_PL"))
    s.add_argument("--ui-language", choices=i18n.available_languages() + ["system"],
                   help=_("language of this program (\"system\": follow the system locale)"))
    s.add_argument("--diagnostics-copy", choices=("on", "off"),
                   help=_("copy the device's diagnostic files to this computer at each "
                          "connection in the window (never sent)"))
    s.set_defaults(func=cmd_setup, _parser=s)

    s = sub.add_parser("report", help=_("device report for GitHub, without serial numbers"))
    s.add_argument("--log", action="store_true",
                   help=_("print the last lines of the log file instead, for the form "
                          "(filtered)"))
    s.set_defaults(func=cmd_report)

    s = sub.add_parser("info", help=_("information about the connected device"))
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_info)

    s = sub.add_parser("files", help=_("files on the device by data type"))
    s.add_argument("--type", help=_("e.g. {example} (activities)").format(example="FIT_TYPE_4"))
    s.add_argument("--limit", type=int, default=0, help=_("show only the last N files"))
    s.set_defaults(func=cmd_files)

    s = sub.add_parser("diagnostics",
                       help=_("the device's error logs and their copy on this computer"))
    s.add_argument("--apply", action="store_true",
                   help=_("copy the diagnostic files the device shows now"))
    s.add_argument("--crash-logs", action="store_true",
                   help=_("also read the hidden crash logs (the device leaves the file "
                          "manager for a moment)"))
    s.add_argument("--dumps", action="store_true", help=_("also copy the memory dumps"))
    s.set_defaults(func=cmd_diagnostics)

    s = sub.add_parser("login", help=_("log in to Garmin Connect"))
    s.add_argument("--manual", action="store_true", help=_("log in using a regular browser"))
    s.set_defaults(func=cmd_login)

    s = sub.add_parser("logout", help=_("remove the local token"))
    s.set_defaults(func=cmd_logout)

    from openexpress.cache import CATEGORIES

    s = sub.add_parser("cache", help=_("size of the cache by part ({parts}), and clearing it")
                       .format(parts=", ".join(CATEGORIES)))
    s.add_argument("--clear", action="store_true",
                   help=_("delete what is in the cache (downloaded again when needed)"))
    s.add_argument("--only", action="append", metavar="PARTS",
                   help=_("only these parts of the cache, separated by commas: {parts}")
                   .format(parts=", ".join(CATEGORIES)))
    s.add_argument("--apply", action="store_true", help=_("really delete (with --clear)"))
    s.set_defaults(func=cmd_cache)

    s = sub.add_parser("devices", help=_("the devices this installation has seen"))
    s.add_argument("--forget", type=int, metavar="N",
                   help=_("forget device number N of the list and what this installation "
                          "keeps of it (its sync state, its last sync)"))
    s.add_argument("--discard-pending", action="store_true",
                   help=_("with --forget: also when files of it have not been uploaded yet "
                          "(they are lost)"))
    s.add_argument("--backups", action="store_true",
                   help=_("with --forget: delete its backups too"))
    s.add_argument("--apply", action="store_true", help=_("really forget (with --forget)"))
    s.set_defaults(func=cmd_devices)

    s = sub.add_parser("status", help=_("the login, the devices connected, their last sync and "
                                        "updates, from what this installation knows"))
    s.add_argument("--online", action="store_true",
                   help=_("also ask Garmin Connect and Garmin's update server now"))
    s.set_defaults(func=cmd_status)

    s = sub.add_parser("whoami", help=_("logged-in account"))
    s.add_argument("--check", action="store_true", help=_("verify the token with the server"))
    s.set_defaults(func=cmd_whoami)

    s = sub.add_parser("upload-config",
                       help=_("upload configuration from the Garmin server (OMT)"))
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_upload_config)

    s = sub.add_parser("sync", help=_("sync with Garmin Connect (dry run by default)"))
    s.add_argument("--apply", action="store_true", help=_("actually upload and write"))
    s.add_argument("--all", action="store_true", help=_("upload everything on the first sync"))
    s.add_argument("--upload-only", action="store_true")
    s.add_argument("--download-only", action="store_true")
    s.add_argument("--type", action="append", help=_("only the given data type (repeatable)"))
    s.add_argument("--include-diagnostics", action="store_true",
                   help=_("also upload device diagnostics and telemetry to Garmin"))
    s.set_defaults(func=cmd_sync)

    s = sub.add_parser("baseline", help=_("mark current files as already uploaded"))
    s.add_argument("--apply", action="store_true")
    s.set_defaults(func=cmd_baseline)

    s = sub.add_parser("backup", help=_("local backup of device data"))
    s.add_argument("--dest", help=_("destination directory (default: {path})").format(
        path="~/Documents/Garmin/Backups"))
    s.add_argument("--keep", type=int, default=3, help=_("how many most recent backups to keep"))
    s.add_argument("--apply", action="store_true")
    s.set_defaults(func=cmd_backup)

    s = sub.add_parser("updates", help=_("device software updates"))
    s.add_argument("--reinstalls", action="store_true",
                   help=_("also show items available for reinstall"))
    s.add_argument("--install", nargs="+", metavar="PART",
                   help=_("part numbers to install, or 'all'"))
    s.add_argument("--apply", action="store_true")
    s.set_defaults(func=cmd_updates)

    s = sub.add_parser("maps", help=_("maps on the device (TopoActive, base maps, CourseView…)"))
    s.add_argument("--all", action="store_true", help=_("also show maps available for download"))
    msub = s.add_subparsers(dest="maps_cmd")
    m = msub.add_parser("install", help=_("install a map"))
    m.add_argument("name")
    m.add_argument("--apply", action="store_true")
    m = msub.add_parser("update", help=_("update installed maps"))
    m.add_argument("--apply", action="store_true")
    m = msub.add_parser("remove", help=_("remove a map from the device"))
    m.add_argument("name")
    m.add_argument("--own", action="store_true",
                   help=_("remove a map of your own that was copied with 'maps add'"))
    m.add_argument("--apply", action="store_true")
    m = msub.add_parser("add", help=_("copy a map of your own (.img) to the device"))
    m.add_argument("file")
    m.add_argument("--name")
    m.add_argument("--apply", action="store_true")
    s.set_defaults(func=cmd_maps)

    s = sub.add_parser("primary",
                       help=_("show the primary training device of the Garmin account"))
    wearable_help = _("the primary wearable (steps and sleep in Garmin Connect) instead of the "
                      "primary training device")
    s.add_argument("--wearable", action="store_true", help=wearable_help)
    psub = s.add_subparsers(dest="primary_cmd")
    m = psub.add_parser("set", help=_("make the connected device the primary training device"))
    m.add_argument("--wearable", dest="set_wearable", action="store_true", help=wearable_help)
    m.add_argument("--apply", action="store_true")
    s.set_defaults(func=cmd_primary)

    s = sub.add_parser("apps", help=_("Connect IQ apps, watch faces and data fields"))
    s.add_argument("--builtin", action="store_true", help=_("also list built-in activities"))
    asub = s.add_subparsers(dest="apps_cmd")
    for name, helptext in (("install", _("install an app from your Garmin account")),
                           ("remove", _("remove an app from the device")),
                           ("enable", _("enable an app or built-in activity")),
                           ("disable", _("disable an app or built-in activity"))):
        a = asub.add_parser(name, help=helptext)
        a.add_argument("name", help=_("app name (or part of it) or app id"))
        a.add_argument("--apply", action="store_true")
        if name == "install":
            a.add_argument("--allow-permissions", action="store_true",
                           help=_("also allow the new permissions an app asks for"))
        if name == "remove":
            a.add_argument("--forget", action="store_true",
                           help=_("also remove it from the app list in your Garmin account"))
        if name in ("enable", "disable"):
            a.add_argument("--type", choices=sorted(APP_TYPE_OPTIONS),
                           help=_("which entry, when an app and a widget share a name"))
    a = asub.add_parser("update", help=_("update installed apps"))
    a.add_argument("--apply", action="store_true")
    a.add_argument("--allow-permissions", action="store_true",
                   help=_("also allow the new permissions an app asks for"))
    a = asub.add_parser("order", help=_("put the apps back in the order of a saved app list"))
    a.add_argument("--from", dest="source", metavar="COPY",
                   help=_("a copy of the app list: part of its folder name, or a path"))
    a.add_argument("--all", action="store_true",
                   help=_("list every position that would change"))
    a.add_argument("--apply", action="store_true")
    s.set_defaults(func=cmd_apps)

    s = sub.add_parser("music", help=_("music, podcasts and audiobooks on the device"))
    msub = s.add_subparsers(dest="music_cmd")
    m = msub.add_parser("add", help=_("copy audio files or folders to the device"))
    m.add_argument("paths", nargs="+")
    m.add_argument("--as", dest="kind", choices=["track", "podcast", "audiobook"],
                   help=_("treat all files as this kind (default: detect from genre tag)"))
    m.add_argument("--playlist", help=_("also create a playlist with these files"))
    m.add_argument("--apply", action="store_true")
    m = msub.add_parser("playlist", help=_("copy a local .m3u/.m3u8 playlist and its tracks"))
    m.add_argument("m3u")
    m.add_argument("--name")
    m.add_argument("--apply", action="store_true")
    m = msub.add_parser("remove", help=_("remove files or playlists whose name, artist or "
                                         "album folder contains TEXT"))
    m.add_argument("paths", nargs="+", metavar="TEXT")
    m.add_argument("--apply", action="store_true")
    s.set_defaults(func=cmd_music)

    s = sub.add_parser("gui", help=_("application window"))
    s.set_defaults(func=cmd_gui)

    s = sub.add_parser("watch", help=_("background service: sync when the device is connected"))
    s.set_defaults(func=cmd_watch)

    s = sub.add_parser("install-desktop",
                       help=_("add an application menu entry and a background sync service"))
    s.add_argument("--apply", action="store_true")
    s.add_argument("--remove", action="store_true",
                   help=_("remove the menu entry, icon and service instead"))
    s.add_argument("--force", action="store_true",
                   help=_("install even if the Flatpak of openExpress is installed"))
    s.set_defaults(func=cmd_install_desktop)
    return p


def _domain_errors() -> tuple:
    from openexpress.ciq import CiqError
    from openexpress.download import DownloadError
    from openexpress.maps import MapError
    from openexpress.music import MusicError
    from openexpress.proto import ProtoError
    from openexpress.updates import UpdateError

    return (CiqError, DownloadError, MapError, MusicError, ProtoError, UpdateError)


def _lang_from_argv(argv: list[str]) -> str | None:
    """Finds --lang before parsing, so that the help texts are already translated."""
    for i, arg in enumerate(argv):
        if arg == "--":
            break
        if arg == "--lang" and i + 1 < len(argv):
            return argv[i + 1]
        if arg.startswith("--lang="):
            return arg.split("=", 1)[1]
    return None


class _PlainSpaces:
    """A text stream that writes the hard spaces of the program's texts (U+00A0, so that a
    size or a Polish one-letter word stays on its line in the window) as plain ones: what
    the command line writes is copied, searched and parsed."""

    def __init__(self, stream):
        self._stream = stream

    def write(self, text: str) -> int:
        return self._stream.write(text.replace("\u00a0", " "))

    def __getattr__(self, name):
        return getattr(self._stream, name)


def main(argv: list[str] | None = None) -> int:
    """The command line: every command, its help and its errors write plain spaces."""
    streams = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = (None if s is None else _PlainSpaces(s) for s in streams)
    try:
        return _main(argv)
    finally:
        sys.stdout, sys.stderr = streams


def _main(argv: list[str] | None) -> int:
    lang = _lang_from_argv(sys.argv[1:] if argv is None else argv)
    if lang:
        i18n.setup(lang)
    args = build_parser().parse_args(argv)
    if args.cmd == "setup" and bool(args.key) != bool(args.secret):
        args._parser.error(_("--key and --secret must be given together"))
    if args.lang:
        i18n.setup(args.lang)
    if importlib.util.find_spec("gi") is None:
        # Without PyGObject no command can open a device or a window: one line and exit
        # code 1, not the traceback of the first import of GLib (--help and --version above).
        _error(no_pygobject_text())
        return 1
    import requests

    from openexpress import applog, service

    # Before any process is started (the start line may ask flatpak-spawn) and before GTK:
    # a locale the C library cannot have (a Flatpak runtime without it) becomes C.UTF-8.
    fallback = i18n.ensure_utf8_locale()
    role = args.cmd if args.cmd in ("gui", "watch") else "cli"
    # What a command tells the user itself is not printed again from the log (-v: all of it).
    applog.setup(role, args.verbose, quiet_told=role == "cli" and not args.verbose)
    started = time.monotonic()
    log.info("start: %s (%s)", _command_line(args, argv), applog.environment())
    if fallback:
        log.info("%s", fallback)
    applog.enable_crash_log()  # a crash of GTK or WebKit leaves Python's trace in crash.log
    from openexpress.auth import sso

    sso.note_unfinished_sign_in()  # the last run ended with the sign-in window open
    code: int | str = "error"
    try:
        code = _run(args, service, requests)
        return code
    finally:
        log.info("end: %s, exit code %s, %.1f s", args.cmd or "-", code,
                 time.monotonic() - started)


def no_pygobject_text() -> str:
    """PyGObject is not installed (a virtual environment without the system's packages):
    what to install, with the package names of Fedora, Arch and Debian."""
    return _("openExpress needs PyGObject with GTK 4 and libadwaita. Install them (Fedora: "
             "sudo dnf install python3-gobject gtk4 libadwaita; Arch: sudo pacman -S "
             "python-gobject gtk4 libadwaita; Debian and Ubuntu: sudo apt install python3-gi "
             "gir1.2-gtk-4.0 gir1.2-adw-1), then run the command again.")


def _command_line(args, argv) -> str:
    """The command as it goes to the log: its name, its subcommand and the names of the
    options given, never their values or other arguments (names, paths, addresses)."""
    words = [args.cmd or "-"]
    words += [str(getattr(args, name)) for name in sorted(vars(args))
              if name.endswith("_cmd") and getattr(args, name)]
    given = sys.argv[1:] if argv is None else argv
    words += [a.split("=", 1)[0] for a in given if a.startswith("-")]
    return " ".join(words)


def _run(args, service, requests) -> int:

    from openexpress.auth.sso import LoginCancelled
    from openexpress.device.gio_storage import MassStorageNotice
    from openexpress.errors import OrdinaryStop

    user_errors = (DeviceError, SetupError, store.NotLoggedIn, ClockSkew, OwnershipError,
                   service.MassStorageMode,
                   Unauthorized, service.SyncBusy,
                   service.PrimaryTrainingError) + _domain_errors()
    try:
        return args.func(args)
    except MassStorageNotice as e:
        # What the window says about the drive: a notice, not an error; exit code 1.
        log.info("not opened: %s", type(e).__name__)
        _error(str(e))
        return 1
    except LoginCancelled as e:
        # The sign-in window closed, or an address without a ticket: the user's choice, not
        # a fault of the program (one line, no traceback).
        log.info("login: not signed in (%s)", type(e).__name__)
        _error(_("Error: {error}").format(error=e))
        return 1
    except OrdinaryStop as e:
        # Not an error (another sync running, a choice to make…): the user is told as
        # before, the log says what it waits for.
        log.info("stopped: %s", e.log_reason)
        _error(_("Error: {error}").format(error=e))
        return 1
    except user_errors as e:
        log.info("error: %s: %s", type(e).__name__, e)  # shown to the user below
        _error(_("Error: {error}").format(error=e))
        return 1
    except ApiError as e:
        log.info("server error: %s: %s", type(e).__name__, e)
        _error(_("Server error: {error}").format(error=e))
        if args.verbose and e.body:
            print(applog.redact(e.body), file=sys.stderr)  # the server's own words: filtered
        return 1
    except requests.RequestException as e:
        _error(_("Network error: could not reach the Garmin server ({error})").format(
            error=applog.network_error_text(e)))
        log.info("network error: %s", type(e).__name__)
        log.debug("network error", exc_info=True)
        return 1
    except KeyboardInterrupt:
        _error(_("Interrupted."))
        return 130
    except Exception as e:  # not foreseen: told briefly, the rest in the log
        # Logged with its traceback at INFO: always in the log file, on the terminal only with
        # -v; both filtered. Its text may carry a device address or a path, so the message for
        # the user is filtered as well.
        log.info("unexpected error: %s", type(e).__name__, exc_info=True)
        _error(applog.redact(_("Error: {error}").format(error=str(e) or type(e).__name__)))
        return 1
