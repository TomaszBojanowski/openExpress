"""openexpress: command-line interface."""

from __future__ import annotations

import argparse
import collections
import json
import logging
import sys
from pathlib import Path

from openexpress import __version__, i18n, paths
from openexpress.auth import store
from openexpress.connect import (
    ApiError, ClockSkew, GarminClient, OwnershipError, Unauthorized,
)
from openexpress.device.storage import DeviceError, LocalStorage, Storage
from openexpress.device.xmldevice import GarminDevice, parse
from openexpress.i18n import N_, _, ngettext
from openexpress.settings import (
    ENVIRONMENTS, Consumer, SetupError, Settings, extract_consumer_from_dll,
    extract_url_salt_from_dll, load_consumer, save_consumer,
)


def _human(n: int | None) -> str:
    if n is None:
        return "?"
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return str(n)


def open_storage(args) -> Storage:
    if args.path:
        return LocalStorage(Path(args.path))
    from openexpress.device.gio_storage import open_device_storage

    return open_device_storage(args.device_uri)


def open_device(args) -> tuple[Storage, GarminDevice]:
    st = open_storage(args)
    return st, parse(st.read("GARMIN/GarminDevice.xml"))


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


def _settings_json_path() -> Path:
    return paths.config_dir() / "settings.json"


def _read_ui_language() -> str | None:
    path = _settings_json_path()
    try:
        return json.loads(path.read_text()).get("ui_language") if path.exists() else None
    except (OSError, ValueError):
        return None


def _write_ui_language(value: str) -> None:
    """Stores "ui_language" in settings.json, keeping the other keys (it is not a Settings field)."""
    path = _settings_json_path()
    data = json.loads(path.read_text()) if path.exists() else {}
    data["ui_language"] = value
    paths.write_private(path, json.dumps(data, indent=2))


def cmd_setup(args) -> int:
    settings = Settings.load()
    previous_ui_language = _read_ui_language()
    if args.from_installer:
        from openexpress.installer import consumer_from_installer

        consumer = consumer_from_installer(Path(args.from_installer))
        save_consumer(consumer)
        print(_("Read the application key and download signing data from {file}").format(
            file=Path(args.from_installer).name))
    elif args.from_dll:
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
        print(_("Saved OAuth1 consumer key ({key}…) in {path}").format(
            key=consumer.key[:8], path=paths.config_dir()))
    if args.environment:
        settings.environment = args.environment
    if args.locale:
        settings.locale = args.locale
    settings.save()
    # Settings.save() rewrites settings.json from its own fields, so ui_language goes after it.
    if args.ui_language:
        ui_language = "" if args.ui_language == "system" else args.ui_language
    else:
        ui_language = previous_ui_language
    if ui_language is not None:
        _write_ui_language(ui_language)
    print(_("Environment: {environment}, locale: {locale}").format(
        environment=settings.environment, locale=settings.locale))
    if args.ui_language:
        print(_("Interface language: {language}").format(
            language=i18n.LANGUAGE_NAMES.get(ui_language, ui_language) if ui_language
            else _("system language")))
    return 0


def cmd_info(args) -> int:
    st, dev = open_device(args)
    if args.json:
        print(json.dumps({
            "unit_id": dev.unit_id, "part_number": dev.part_number,
            "software_version": dev.software_version_str, "description": dev.description,
            "storage": st.label, "free_bytes": st.free_space(),
            "datatypes": dev.datatypes(),
        }, indent=2, ensure_ascii=False))
        return 0
    rows = [
        (_("Device:"), dev.description),
        (_("Part number:"), dev.part_number),
        (_("Software:"), dev.software_version_str),
        (_("Unit ID:"), dev.unit_id),
        (_("Storage:"), st.label),
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
        logged_in = store.load() is not None
    except Exception:  # noqa: BLE001 - only reported as yes/no
        logged_in = False
    print(report.build(dev, st, logged_in), end="")
    print("\n" + _("This report contains no serial number, unit ID or e-mail. "
                   "Paste it into a device report: {url}").format(url=report.ISSUE_URL),
          file=sys.stderr)
    return 0


def cmd_files(args) -> int:
    st, dev = open_device(args)
    types = [args.type] if args.type else sorted(
        {f.datatype for f in dev.files if f.readable},
        key=lambda s: (not s.startswith("FIT_TYPE_"), int(s[9:]) if s[9:].isdigit() else 0, s),
    )
    total = 0
    for dt in types:
        files: dict[str, int] = {}
        for src in dev.sources(dt):
            for path, e in st.walk_files(src.path):
                if src.matches(path):
                    files[path] = e.size
        if not files and not args.type:
            continue
        total += len(files)
        paths_ = ", ".join(sorted({s.path for s in dev.sources(dt)}))
        count = ngettext("{count} file", "{count} files", len(files)).format(
            count=f"{len(files):5}")
        print(f"{dt:22} {count:12} {_human(sum(files.values())):>9}  ({paths_})")
        if args.type or args.verbose:
            for p in sorted(files)[-args.limit:] if args.limit else sorted(files):
                print(f"    {p}  {_human(files[p])}")
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
                  f"consent={loc.get('requiresuserconsent')}{' [' + consents + ']' if consents else ''}")
    return 0


def _sync_context(args):
    from openexpress.sync import SyncState

    st, dev = open_device(args)
    settings = Settings.load()
    state = SyncState(dev.unit_id)
    return st, dev, settings, state


def cmd_sync(args) -> int:
    from openexpress import sync

    st, dev, settings, state = _sync_context(args)
    print(_("{device} (software {version})").format(
        device=dev.description, version=dev.software_version_str))
    logged_in = True
    try:
        client = make_client(settings, need_auth=True)
    except store.NotLoggedIn:
        if args.apply:
            raise
        # The upload plan can be shown without logging in; the download queue needs a token.
        logged_in = False
        client = make_client(settings, need_auth=False)
        print(_("(not logged in: showing the upload plan only)"))
    if logged_in:
        from openexpress import service

        service.require_account_device(client, dev.unit_id)

    up_plan = None
    if not args.download_only:
        cfg = client.upload_settings(dev.unit_id)
        up_plan = sync.plan_uploads(
            dev, st, cfg, state, include_consent_locations=args.include_consent,
            only_types=set(args.type) if args.type else None,
            include_diagnostics=args.include_diagnostics,
        )
        counts = collections.Counter(c.datatype for c in up_plan.candidates)
        n_up = len(up_plan.candidates)
        print("\n" + ngettext("To upload: {count} file (already uploaded earlier: {done})",
                              "To upload: {count} files (already uploaded earlier: {done})",
                              n_up).format(count=n_up, done=up_plan.already_done))
        for dt, n in sorted(counts.items()):
            size = sum(c.size for c in up_plan.candidates if c.datatype == dt)
            print(f"  {dt:22} {n:5}  {_human(size)}")
        for dt, why in sorted(up_plan.skipped_types.items()):
            print(f"  {dt:22} " + _("skipped: {reason}").format(reason=why))

    dl_items = []
    messages: list[dict] = []
    if not args.upload_only and logged_in:
        messages = client.device_messages(dev.unit_id)
        dl_items = sync.plan_downloads(dev, messages)
        n_dl = sum(1 for i in dl_items if not i.unsupported)
        print("\n" + _("To download from Connect to the device: {count}").format(count=n_dl))
        for it in dl_items:
            note = ("  [" + _("skipped: {reason}").format(reason=it.unsupported) + "]"
                    if it.unsupported
                    else f"  [{it.handled_by}]" if it.handled_by else "")
            target = it.target.path if it.target else "-"
            print(f"  {it.datatype:22} {it.message_name or it.message_id} -> {target}{note}")

    if not args.apply:
        print("\n" + _("Dry run: nothing was uploaded or written to the device. "
                       "Add --apply to run the sync."))
        return 0

    first_run = not state.file.exists()
    if first_run and not args.all and not args.download_only:
        print(
            "\n" + _("This is the first sync with this program. If the device also syncs "
                     "via your phone, most files are already in Connect.\n"
                     "  openexpress baseline --apply   marks current files as uploaded "
                     "(recommended)\n"
                     "  openexpress sync --apply --all uploads everything "
                     "(Connect rejects duplicates)"),
            file=sys.stderr,
        )
        return 2

    from openexpress import service

    def progress(msg: str) -> None:
        print("  " + msg)

    summary = service.SyncSummary()
    with service.sync_lock():
        if up_plan is not None:
            # Another process may have synced since the plan was shown: plan again under the lock.
            state = sync.SyncState(dev.unit_id)
            up_plan = sync.plan_uploads(
                dev, st, cfg, state, include_consent_locations=args.include_consent,
                only_types=set(args.type) if args.type else None,
                include_diagnostics=args.include_diagnostics,
            )
        if up_plan and up_plan.candidates:
            print("\n" + _("Uploading…"))
            rep = sync.run_uploads(up_plan, dev, st, client, state, progress)
            line = _("Uploaded: {uploaded}, duplicates: {duplicates}, already in Connect: "
                     "{already}, rejected: {rejected}, errors: {errors}").format(
                uploaded=len(rep.uploaded), duplicates=len(rep.duplicates),
                already=len(rep.already_in_connect), rejected=len(rep.rejected),
                errors=len(rep.failed))
            print(line)
            summary.lines.append(line)
            summary.uploaded = len(rep.uploaded)
            summary.skipped_known = len(rep.duplicates) + len(rep.already_in_connect)
            summary.errors += [f"{label}: {err}" for label, err in rep.failed]
            for label, err in rep.failed:
                print("  " + _("ERROR {item}: {error}").format(item=label, error=err))
            for label in rep.not_confirmed:
                print("  " + _("Warning: Connect did not confirm processing of {item}").format(
                    item=label))
        if up_plan is not None:
            # Not for --download-only: the first-sync choice must stay pending until uploads run.
            state.save()
        if any(not i.unsupported and not i.handled_by for i in dl_items):
            print("\n" + _("Downloading to the device…"))
            drep = sync.run_downloads(dl_items, st, client, progress, state=state)
            line = _("Written: {written}, empty: {empty}, errors: {errors}").format(
                written=len(drep.written), empty=len(drep.empty), errors=len(drep.failed))
            print(line)
            summary.lines.append(line)
            summary.downloaded = len(drep.written)
            summary.errors += [f"{label}: {err}" for label, err in drep.failed]
            for label, err in drep.failed:
                print("  " + _("ERROR {item}: {error}").format(item=label, error=err))
        special = {i.handled_by for i in dl_items if i.handled_by}
        if special:
            session = service.DeviceSession(st, dev, settings, client)
            errors: list[str] = []
            lines = []
            if "Connect IQ" in special:
                lines += service._install_queued_apps(session, messages, progress, errors)
            if "TrueUp" in special:
                lines += service._trueup(session, messages, progress, errors)
            for line in lines:
                print(line)
            for err in errors:
                print("  " + _("ERROR {error}").format(error=err))
            summary.lines += lines
            summary.errors += errors
    service.save_last_sync(dev.unit_id, summary)
    if summary.errors:
        print("\n" + ngettext("Done with {count} error.", "Done with {count} errors.",
                              len(summary.errors)).format(count=len(summary.errors)))
        return 1
    print("\n" + _("Done."))
    return 0


def cmd_baseline(args) -> int:
    from openexpress import service, sync

    st, dev, settings, state = _sync_context(args)
    client = make_client(settings, need_auth=True)
    service.require_account_device(client, dev.unit_id)
    cfg = client.upload_settings(dev.unit_id)
    plan = sync.plan_uploads(dev, st, cfg, state, include_consent_locations=True)
    n = sum(1 for c in plan.candidates if c.device_path)
    if not args.apply:
        print(ngettext("Dry run: {count} file would be marked as already uploaded. Add --apply.",
                       "Dry run: {count} files would be marked as already uploaded. Add --apply.",
                       n).format(count=n))
        return 0
    with service.sync_lock():
        n = sync.mark_baseline(dev, st, cfg, sync.SyncState(dev.unit_id), client)
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


# Untranslated constant (also used by the GUI); show it with _(RESTART_STEPS).
RESTART_STEPS = N_("""
To finish the installation:
  1. Disconnect the device from the computer.
  2. The device installs the update by itself (it may ask for confirmation
     and restart). Wait until the device has restarted.
  3. Reconnect it and run `openexpress updates`: the installed update
     should no longer be listed.
""")


def cmd_updates(args) -> int:
    from openexpress import updates as upd

    st, dev = open_device(args)
    client = GarminClient(Settings.load())
    resp = client.software_updates(dev.raw, include_reinstalls=args.reinstalls)
    items = upd.parse_updates(resp)
    if not args.reinstalls:
        items = [u for u in items if not u.is_reinstall]
    print(_("{device}, software {version}").format(
        device=dev.description, version=dev.software_version_str))
    if not args.install:
        if not items:
            print(_("Software is up to date.") if not args.reinstalls
                  else _("The server returned no items."))
            return 0
        visible = [u for u in items if not u.is_metadata or args.verbose]
        print((_("Reinstalls: {count}") if args.reinstalls
               else _("Available updates: {count}")).format(count=len(visible)))
        for u in sorted(visible, key=lambda x: (not x.is_primary, x.severity.lower() != "critical",
                                                x.name)):
            # TRANSLATORS: marks the main watch firmware in the list of updates
            flag = " [" + _("PRIMARY") + "]" if u.is_primary else ""
            print(f"  {u.part_number:18} {u.version:>7}  {u.severity:<11} {_human(u.size):>9}  "
                  f"{u.name}{flag}")
            if args.verbose:
                print(f"      -> {u.device_path}")
            for c in u.changes[: (20 if args.verbose else 5)]:
                print(f"      • {c}")
        print("\n" + _("Install: {command}").format(
            command="openexpress updates --install PART_NUMBER [--reinstalls] --apply"))
        return 0

    chosen = upd.select(items, args.install)
    need, free = upd.check_space(st, chosen)
    print(_("To install:"))
    for u in upd.sort_for_install(chosen):
        print(f"  {u.part_number} {u.version}  {u.name}  ({_human(u.size)}) -> {u.device_path}")
    print(_("Space: {needed} needed, {free} free").format(needed=_human(need), free=_human(free)))
    eulas = sorted({u.eula_url for u in chosen if u.eula_url})
    for e in eulas:
        print(_("License (EULA): {url}").format(url=e))
    if any(u.is_primary or u.restart_required for u in chosen):
        print(_("Warning: this is a device system update. Charge the device and do not disconnect "
                "it while copying."))
    if not args.apply:
        print("\n" + _("Dry run: nothing was downloaded or written. Add --apply "
                       "(this means you accept the licenses above)."))
        return 0
    rep = upd.install(chosen, st, session=client.http, progress=lambda s: print("  " + s))
    n = len(rep.installed)
    print("\n" + ngettext("Copied {count} file.", "Copied {count} files.", n).format(count=n))
    if rep.restart_required:
        print(_(RESTART_STEPS))
    return 0


def _load_maps(args, client):
    from openexpress import maps as mp

    st, dev = open_device(args)
    return st, dev, mp.parse_maps(client.universal_maps(dev.raw))


def _url_salt() -> str | None:
    salt = load_consumer().url_salt
    if not salt:
        raise SetupError(_("Download URL signing data is missing. Run: {command}").format(
            command="openexpress setup --utilities-dll /path/to/Utilities.dll"))
    return salt


def cmd_maps(args) -> int:
    from openexpress import maps as mp

    client = GarminClient(Settings.load())
    st, dev, all_maps = _load_maps(args, client)
    if args.maps_cmd in (None, "list"):
        shown = all_maps if args.all else [m for m in all_maps if m.installed]
        print((_("Maps for {device} (all):") if args.all
               else _("Maps for {device} (installed):")).format(device=dev.description))
        for m in sorted(shown, key=lambda m: (not m.installed, m.map_type, m.name)):
            print(f"  {m.name:48} {m.version:>9}  {_human(m.size):>9}  {m.status}")
        if not args.all:
            n = sum(1 for m in all_maps if not m.installed)
            print("\n" + ngettext("{count} more map is available (openexpress maps --all).",
                                  "{count} more maps are available (openexpress maps --all).",
                                  n).format(count=n))
        print(_("Free space on the device: {size}").format(size=_human(st.free_space())))
        return 0

    if args.maps_cmd == "update":
        targets = [m for m in all_maps if m.update_available]
        if not targets:
            print(_("All installed maps are up to date."))
            return 0
    else:
        targets = [mp.find(all_maps, args.name)]

    for m in targets:
        if args.maps_cmd == "remove":
            files = mp.removal_list(all_maps, m, rewriting=False)
            print(ngettext("Removing map {name}: {count} file",
                           "Removing map {name}: {count} files", len(files)).format(
                name=m.name, count=len(files)))
            for p in files:
                print(f"  - {p}  ({_human(st.size(p))})")
            if not m.can_uninstall:
                print(_("This map cannot be removed."))
                return 1
            if not args.apply:
                print(_("Dry run. Add --apply."))
                continue
            removed = mp.uninstall(m, all_maps, st, progress=lambda s: print("  " + s))
            print(ngettext("Removed {count} file.", "Removed {count} files.",
                           len(removed)).format(count=len(removed)))
            continue

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
        for f in m.files:
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
    return 0


def _find_app(items, query: str):
    q = query.casefold()
    exact = [a for a in items if a.name.casefold() == q or str(a.app_id) == q]
    if exact:
        return exact[0]
    partial = [a for a in items if q in a.name.casefold()]
    if len(partial) == 1:
        return partial[0]
    if not partial:
        raise SetupError(_("No app matches '{query}'").format(query=query))
    raise SetupError(_("Ambiguous name, matches: {names}").format(
        names="; ".join(a.name for a in partial)))


def _app_status(disabled: bool) -> str:
    # TRANSLATORS: state of a Connect IQ app or built-in activity on the watch
    return _("disabled") if disabled else _("enabled")


def cmd_apps(args) -> int:
    from openexpress import ciq

    st, dev = open_device(args)
    settings = Settings.load()
    client = make_client(settings, need_auth=True)
    from openexpress import service

    service.require_account_device(client, dev.unit_id)
    api = ciq.StoreApi(client)
    locale = settings.locale.replace("_", "-")
    state = ciq.read_installed(dev, st)
    account = api.account_apps(dev, locale)
    installed_ids = {a.app_id for a in state.apps}
    cmd = args.apps_cmd or "list"

    if cmd == "list":
        updates = {u.app_id: u for u in api.updates(dev, state.store_apps, locale)}
        print(ngettext("Connect IQ on {device}: {count} store app, {slots} free slots, "
                       "{size} free",
                       "Connect IQ on {device}: {count} store apps, {slots} free slots, "
                       "{size} free", len(state.store_apps)).format(
            device=dev.description, count=len(state.store_apps),
            slots=state.available_slots, size=_human(state.available_space)))
        for a in state.store_apps:
            upd = updates.get(a.app_id)
            note = ("  " + _("update available: {version}").format(version=upd.version_name)
                    if upd else "")
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
        if not args.apply:
            print(_("Dry run. Add --apply."))
            return 0
        api.queue_install(dev, targets)
        rep = ciq.install_queued(dev, st, client, client.device_messages(dev.unit_id),
                                 {a.app_id: a for a in targets},
                                 only={a.app_id for a in targets},
                                 progress=lambda s: print("  " + s))
        # TRANSLATORS: shown after "Installed:" when no app was installed
        none = _("none")
        print(_("Installed: {names}").format(names=", ".join(rep.installed) or none))
        for name, err in rep.failed:
            print("  " + _("ERROR {item}: {error}").format(item=name, error=err))
        if not rep.installed and not rep.failed:
            print(_("The server has not queued the app yet; run `openexpress sync` in a moment."))
        return 0 if not rep.failed else 1

    app = _find_app(state.apps, args.name)
    if cmd == "remove":
        paths_ = [p for p in (ciq.app_file_path(dev, app), ciq.settings_file_path(dev, app)) if p]
        print(_("Remove {name} from the device: {files}").format(
            name=app.name, files=", ".join(paths_) or "-"))
        if not args.apply:
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
    print((_("Enable {name}") if enable else _("Disable {name}")).format(name=app.name))
    if not args.apply:
        print(_("Dry run. Add --apply."))
        return 0
    ciq.set_enabled(dev, st, app.app_id, enable)
    print(_("Saved. The device applies the change after you disconnect it."))
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
    if cmd == "list":
        items = music.list_device(dev, st)
        by_kind: dict[str, list] = {}
        for i in items:
            by_kind.setdefault(i.kind, []).append(i)
        for kind in ("track", "podcast", "audiobook"):
            files = by_kind.get(kind, [])
            print(_(MUSIC_KIND_LINES[kind]).format(
                count=len(files), size=_human(sum(f.size for f in files))))
            if args.verbose:
                for f in files:
                    print(f"    {f.path}")
        pls = music.list_playlists(dev, st)
        if pls:
            print(_("Playlists: {count}: {names}").format(count=len(pls), names=", ".join(pls)))
        else:
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
            print(f"  + {dest}")
        if len(plan.to_copy) > 15 and not args.verbose:
            more = len(plan.to_copy) - 15
            print("  " + ngettext("… and {count} more", "… and {count} more", more).format(
                count=more))
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
        if not args.apply:
            print(_("Dry run. Add --apply."))
            return 0
        music.copy(plan, st, progress=lambda s: print("  " + s), transfer_progress=_Bar())
        dests = [d for _t, d in plan.to_copy + plan.already]
        print(_("Playlist: {path}").format(path=music.write_playlist(dev, st, name, dests)))
        return 0

    # remove
    bad = [p for p in args.paths if len(p.strip()) < MIN_MUSIC_PATTERN]
    if bad:
        print(_("Error: each TEXT must have at least {count} characters: {texts}").format(
            count=MIN_MUSIC_PATTERN, texts=", ".join(repr(p) for p in bad)), file=sys.stderr)
        return 1
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
    p.add_argument("--path",
                   help=_("directory with device contents instead of MTP (e.g. copy, UMS)"))
    p.add_argument("--device-uri", help=_("MTP device URI, e.g. {example}").format(
        example="mtp://091e_51b4_…/"))
    # Without a command the application window opens (as from the application menu).
    p.set_defaults(func=cmd_gui)
    sub = p.add_subparsers(dest="cmd")

    s = sub.add_parser("setup", help=_("configuration: OAuth1 key, environment, locale"))
    s.add_argument("--from-installer", metavar="GarminExpress.exe",
                   help=_("read the needed data from the official Garmin Express installer"))
    s.add_argument("--from-dll", help=_("path to Networking.dll from Garmin Express"))
    s.add_argument("--utilities-dll",
                   help=_("path to Utilities.dll (default: next to Networking.dll)"))
    s.add_argument("--key")
    s.add_argument("--secret")
    s.add_argument("--environment", choices=sorted(ENVIRONMENTS))
    s.add_argument("--locale", help=_("e.g. {example}").format(example="pl_PL"))
    s.add_argument("--ui-language", choices=i18n.available_languages() + ["system"],
                   help=_("language of this program (\"system\": follow the system locale)"))
    s.set_defaults(func=cmd_setup, _parser=s)

    s = sub.add_parser("report", help=_("device report for GitHub, without serial numbers"))
    s.set_defaults(func=cmd_report)

    s = sub.add_parser("info", help=_("information about the connected device"))
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_info)

    s = sub.add_parser("files", help=_("files on the device by data type"))
    s.add_argument("--type", help=_("e.g. {example} (activities)").format(example="FIT_TYPE_4"))
    s.add_argument("--limit", type=int, default=0, help=_("show only the last N files"))
    s.set_defaults(func=cmd_files)

    s = sub.add_parser("login", help=_("log in to Garmin Connect"))
    s.add_argument("--manual", action="store_true", help=_("log in using a regular browser"))
    s.set_defaults(func=cmd_login)

    s = sub.add_parser("logout", help=_("remove the local token"))
    s.set_defaults(func=cmd_logout)

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
    s.add_argument("--include-consent", action="store_true",
                   help=_("also upload to locations that require specific GDPR consents"))
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
    m.add_argument("--apply", action="store_true")
    s.set_defaults(func=cmd_maps)

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
        if name == "remove":
            a.add_argument("--forget", action="store_true",
                           help=_("also remove it from the app list in your Garmin account"))
    a = asub.add_parser("update", help=_("update installed apps"))
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


def main(argv: list[str] | None = None) -> int:
    lang = _lang_from_argv(sys.argv[1:] if argv is None else argv)
    if lang:
        i18n.setup(lang)
    args = build_parser().parse_args(argv)
    if args.cmd == "setup" and bool(args.key) != bool(args.secret):
        args._parser.error(_("--key and --secret must be given together"))
    if args.lang:
        i18n.setup(args.lang)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    import requests

    from openexpress import service

    user_errors = (DeviceError, SetupError, store.NotLoggedIn, ClockSkew, OwnershipError,
                   Unauthorized, service.SyncBusy) + _domain_errors()
    try:
        return args.func(args)
    except user_errors as e:
        print(_("Error: {error}").format(error=e), file=sys.stderr)
        return 1
    except ApiError as e:
        print(_("Server error: {error}").format(error=e), file=sys.stderr)
        if args.verbose and e.body:
            print(e.body, file=sys.stderr)
        return 1
    except requests.RequestException as e:
        print(_("Network error: could not reach the Garmin server ({error})").format(
            error=e), file=sys.stderr)
        logging.getLogger(__name__).debug("network error", exc_info=True)
        return 1
    except KeyboardInterrupt:
        print(_("Interrupted."), file=sys.stderr)
        return 130
