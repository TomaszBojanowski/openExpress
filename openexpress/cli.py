"""openexpress: command-line interface."""

from __future__ import annotations

import argparse
import collections
import contextlib
import dataclasses
import importlib.util
import json
import logging
import posixpath
import sys
import time
import unicodedata
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from openexpress import __version__, applog, i18n, known_devices, paths
from openexpress.auth import store
from openexpress.connect import (
    ApiError,
    ClockSkew,
    GarminClient,
    Unauthorized,
)
from openexpress.console import error as _error
from openexpress.device.storage import DeviceError, DeviceNotResponding, LocalStorage, Storage
from openexpress.device.xmldevice import GarminDevice, parse
from openexpress.errors import UserFacingError
from openexpress.i18n import (
    N_,
    _,
    caption,
    decimal,
    full_time,
    human_size,
    ngettext,
    pgettext,
    plain_spaces,
)
from openexpress.progress import Reporter
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


# The exit code of `sync --apply` when the first sync with this program waits for a choice
# (baseline or --all): not 2, which argparse gives a wrong command line (docs/cli.md, "Exit
# codes").
FIRST_SYNC_CHOICE = 3


# Commands that read or write many files on the device when they are not a dry run.
WORKS_ON_DEVICE = frozenset({"sync", "backup", "updates", "maps", "apps", "music", "courses"})


# Commands that open a device: they take --path, --device-uri and --no-wait after them too
# (_device_options).
DEVICE_COMMANDS = frozenset({
    "report", "info", "files", "activities", "diagnostics", "upload-config", "sync", "baseline",
    "backup", "updates", "maps", "primary", "register", "apps", "music", "courses"})


# Commands that do not apply to a Garmin device in mass storage mode (as in the window);
# `sync` of one goes as service.drive_sync_refusal lets it (service.refuse_drive_sync).
NOT_ON_A_DRIVE = ("apps", "music", "maps")


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
    from openexpress import service
    from openexpress.device.gio_storage import mass_storage_text

    command = getattr(args, "cmd", None)

    def refuse(st, dev):
        # A drive's data types before it is remembered, without a request; Garmin's word on
        # it once there is a session (service.refuse_drive_sync).
        if (st.mass_storage and command in ("sync", "baseline", "register")
                and not service.declares_uploads(dev)):
            service.drive_not_synced(service.NO_UPLOAD_TYPES)
        if st.mass_storage and command in NOT_ON_A_DRIVE:
            raise service.MassStorageMode(mass_storage_text())
        if command in ("sync", "baseline") and not dev.datatypes():
            raise service.MassStorageMode(_("Nothing to sync: the device declares no data "
                                            "types."))

    st = open_storage(args)
    # A device found now is remembered, and its connection noted in its log; a folder given
    # with --path may be a copy.
    dev = service.read_device(st, found_now=not args.path, refuse=refuse)
    if not args.path:
        from openexpress import history

        history.note_connection(dev, st)
    return st, dev


def make_client(settings: Settings, need_auth: bool) -> GarminClient:
    """The client of a command (service.make_client): the key is needed; with `need_auth`
    the login too, without it none is read."""
    from openexpress import service

    return service.make_client(settings, need_auth=need_auth)


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
        print(_("Saved the application key in {path}").format(path=paths.config_dir()))
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
        # TRANSLATORS: `openexpress setup --ui-language`: {language} is the language's own name, or
        # "system language"
        print(_("Interface language: {language}").format(
            language=i18n.language_name(ui_language) if ui_language
            # TRANSLATORS: fills {language} in "Interface language: {language}": the program follows
            # the system's language
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
    if args.path:
        # A copy of a device's files: what this command keeps of it (the copies, those of the
        # dumps it removes) goes apart from the device's records, as a test drive's, and never
        # pushes out the device's own.
        st.test_drive = True
    if args.free_space:
        return _free_space_command(st, dev, args.apply)
    print(_diagnostics_copy_text(diagnostics.copy_on()))
    missed: list[str] = []
    entries = diagnostics.read_errors(st, missed=missed)
    print(_("Error logs on the device:"))
    # The "Assert" entries an Edge writes at each start: their number, each of them with -v.
    asserts = 0 if args.verbose else sum(e.is_assert for e in entries)
    for e in entries:
        if args.verbose or not e.is_assert:
            print(f"  {e.words()} ({e.source})")
    if asserts:
        # TRANSLATORS: under "Error logs on the device:"; "Assert" is the kind of the entries,
        # as the device writes it; -v is the option of the command line that shows them
        print("  " + ngettext("{count} Assert entry, not shown (all with -v)",
                              "{count} Assert entries, not shown (all with -v)",
                              asserts).format(count=asserts))
    if missed:
        # TRANSLATORS: under "Error logs on the device:" when one of them could not be read:
        # as on the page Device Status, the crashes are then those kept on this computer
        print("  " + _("not every error log could be read: the crashes below are those kept "
                       "on this computer"))
    elif not entries:
        # TRANSLATORS: under "Error logs on the device:" when the device has none
        print("  " + _("no entries"))
    try:
        dumps = diagnostics.dumps_on_device(st)
    except DeviceNotResponding:
        raise
    except DeviceError:
        # TRANSLATORS: `openexpress diagnostics` when the device's folder of memory dumps
        # could not be listed
        print(_("Memory dumps on the device: not known (their folder could not be listed)"))
        dumps = []
    if dumps:
        print(_("Memory dumps on the device: {count}, {size} (encrypted; copied only with "
                "--dumps)").format(count=len(dumps),
                                   size=human_size(sum(size for _p, size in dumps))))
    from openexpress import crashes

    found = crashes.history(dev.unit_id, None if missed else entries,
                            test_drive=bool(getattr(st, "test_drive", False)))
    print(_("Crashes that restarted the device (error logs, boot log, copies and device log "
            "on this computer):"))
    for crash in found:
        print(f"  {full_time(crash.at)}: {crash.words()} ({crash_sources(crash)})")
    if not found:
        # TRANSLATORS: under "Crashes that restarted the device (…):" when there is none
        print("  " + _("none found"))
    print(_("Copies on this computer: {copies}").format(
        copies=diagnostics.copies_text(dev.unit_id, bool(getattr(st, "test_drive", False)))))
    # Not the folder itself: it is named after the unit ID, and this is pasted into reports.
    print("  " + _("in the folder of the device under {folder}").format(
        folder=paths.data_dir() / "devices"))
    for line in history_lines(dev.unit_id, bool(getattr(st, "test_drive", False))):
        print(line)
    if not args.apply:
        print(_("Dry run: nothing was copied. Add --apply to copy the files the device shows now."))
        if args.crash_logs:
            print(_("With --apply, --crash-logs also reads its hidden crash logs in a session "
                    "of its own: the device leaves the file manager for a moment."))
        return 0
    with _progress() as progress:
        done = diagnostics.copy_files(dev, st, progress=progress)
    print(ngettext("Copied: {count} new file ({size}), {unchanged} unchanged, {listed} only "
                   "listed.",
                   "Copied: {count} new files ({size}), {unchanged} unchanged, {listed} only "
                   "listed.", done.new).format(count=done.new, size=human_size(done.new_bytes),
                                               unchanged=done.unchanged, listed=done.listed))
    if args.dumps:
        with _progress() as progress:
            dumped = diagnostics.copy_dumps(dev, st, progress=progress)
        print(_("Memory dumps copied: {count} new ({size}).").format(
            count=dumped.new, size=human_size(dumped.new_bytes)))
        if dumped.not_copied:
            print(_("Not copied, as they did not fit in {size}: {names}").format(
                size=human_size(diagnostics.LIMIT), names=", ".join(dumped.not_copied)))
    if args.crash_logs:
        with _progress() as progress:
            hidden = diagnostics.copy_crash_logs(dev, st, progress=progress)
        print(ngettext("Crash logs: {count} file read ({new} new).",
                       "Crash logs: {count} files read ({new} new).",
                       hidden.new + hidden.unchanged).format(
            count=hidden.new + hidden.unchanged, new=hidden.new) if hidden.saved
              else _("The device has no hidden crash logs."))
    return 0


def _free_space_command(st, dev, apply: bool) -> int:
    """`diagnostics --free-space`: only what can be freed, as the page Device Status lists it
    (freeing.plan); a dry run, or with --apply the copy before the removal and the removal,
    nothing else (not the copy of the diagnostic files)."""
    from openexpress import freeing

    client = GarminClient(Settings.load())
    plan = freeing.plan(st, dev, client, fresh=apply)
    if not apply:
        _print_free_space(plan)
        # TRANSLATORS: `openexpress diagnostics --free-space` without --apply
        print(_("Dry run: nothing was copied or removed. Add --apply to copy and remove the "
                "files listed; that cannot be undone."))
        return 0
    if (not plan.older or plan.dumps_plan is None) and not plan.updates:
        _print_free_space(plan)  # nothing it may remove, and why
        return 0
    if plan.why is not None:
        print(_("update files: not checked ({why})").format(why=plan.why))
    return _free_space(st, dev, plan, client)


def _count_size(items) -> str:
    return f"{len(items)} ({human_size(sum(item[1] for item in items))})"


def _print_free_space(plan) -> None:
    """What --free-space would remove: the older memory dumps, which of them it would copy
    first and which not (the copies would not fit), the update files; an update that waits to
    be installed only shown."""
    from openexpress import freeing

    # TRANSLATORS: `openexpress diagnostics --free-space`: heading above what it removes
    print(_("Space to free on the device:"))
    if plan.dumps is None:
        # TRANSLATORS: under "Space to free on the device:" when the device's folder of memory
        # dumps could not be listed
        print("  " + _("older memory dumps: not known (their folder could not be listed)"))
    elif plan.older is None:
        # TRANSLATORS: under "Space to free on the device:" when a dump's time is not known
        print("  " + _("older memory dumps: not known (the newest cannot be told)"))
    elif plan.older and plan.dumps_plan is None:
        # TRANSLATORS: under "Space to free on the device:" for a device whose GarminDevice.xml
        # gives no unit ID; {dumps} is a count and a size, e.g. "3 (2.5 MB)"
        print("  " + _("older memory dumps: {dumps}; not removed: the device gives no unit ID, "
                       "so their copies would have no folder of the device").format(
            dumps=_count_size(plan.older)))
    elif plan.older:
        # TRANSLATORS: under "Space to free on the device:"; {dumps} is a count and a size,
        # e.g. "3 (2.5 MB)"
        print("  " + _("older memory dumps: {dumps}; the newest stays").format(
            dumps=_count_size(plan.older)))
        if plan.copied:
            # TRANSLATORS: under "older memory dumps: …" in a dry run: what --apply would copy
            # to this computer before it removes them
            print("    " + _("to copy before the removal: {dumps}").format(
                dumps=_count_size(plan.copied)))
        if plan.without_copy:
            # TRANSLATORS: under "older memory dumps: …" in a dry run: what --apply would
            # remove without a copy; {limit} is the most the copies may take, "100 MB"
            print("    " + _("to remove without a copy, as the copies would not fit in "
                             "{limit}: {dumps}").format(limit=freeing.limit_words(),
                                                        dumps=_count_size(plan.without_copy)))
    for u in plan.updates:
        print("  " + _("update file not installed by the device: {name} {version} ({size})")
              .format(name=u.name, version=u.version, size=human_size(u.size)))
    for u in plan.waiting:
        print("  " + _("waiting to be installed, not removed: {name} {version} ({file})").format(
            name=u.name, version=u.version, file=u.filename))
    if plan.why is not None:
        print("  " + _("update files: not checked ({why})").format(why=plan.why))
    if not plan.size and not plan.updates:
        print("  " + _("nothing to remove"))
    else:
        print("  " + _("in all: {size}").format(size=human_size(plan.size)))


def _free_space(st, dev, plan, client) -> int:
    """--free-space --apply: the older dumps copied (where they fit) and removed as listed, then
    the update files, each looked at again first; 1 when the device refused or kept a file, a
    copy failed, or the files changed since they were listed (then nothing of them is removed).
    What was removed is told and put in the device log also when it stops half-way (Ctrl+C, the
    device stopping)."""
    from openexpress import freeing

    problems: list[tuple[str, str]] = []
    dumps = freeing.Freed()
    files: list[tuple[object, freeing.Freed]] = []
    changed = False
    try:
        if plan.older and plan.dumps_plan is not None:
            with _progress() as progress:
                try:
                    freeing.remove_dumps(st, dev.unit_id, plan.dumps_plan, progress=progress,
                                         freed=dumps)
                except freeing.ListChanged as e:
                    print(str(e))
                    changed = True
        for u in plan.updates:
            freed = freeing.Freed()
            files.append((u, freed))
            try:
                freeing.remove_update(st, u, client, dev, freed=freed)
            except freeing.ListChanged:
                # TRANSLATORS: why an update file stays on the device after --free-space
                problems.append((u.device_path, _("it changed since it was listed")))
            except UserFacingError as e:
                problems.append((u.device_path, str(e)))
    finally:
        _tell_freed(dev, st, dumps, files, problems)
    return 1 if problems or changed else 0


def _tell_freed(dev, st, dumps, files, problems: list[tuple[str, str]]) -> None:
    """What a --free-space --apply removed and the files that stayed, with why (`problems`,
    those of the device's refusals added), told also after it stopped half-way; then the device
    counted again and its device log put right (history.after_removal: by what was removed when
    the count fails), for the page of a device not connected."""
    from openexpress import history

    if dumps.removed or dumps.kept or dumps.copy_failed:
        text = ngettext("Removed: {count} memory dump, {size} freed",
                        "Removed: {count} memory dumps, {size} freed", len(dumps.removed)).format(
            count=len(dumps.removed), size=human_size(dumps.freed))
        if dumps.copied:
            text += "; " + _("{count} copied to this computer first").format(
                count=len(dumps.copied))
        print(text + ".")
        problems += [(path, why) for path, why in dumps.kept]
        # TRANSLATORS: why a memory dump stays on the device after --free-space
        problems += [(path, _("its copy on this computer failed")) for path in dumps.copy_failed]
    for u, freed in files:
        if freed.removed:
            print(_("Removed: the file of {name} {version}, {size} freed.").format(
                name=u.name, version=u.version, size=human_size(freed.freed)))
        problems += freed.kept
    for path, why in problems:
        # TRANSLATORS: `diagnostics --free-space --apply`: a file that stayed on the device
        print("  " + _("not removed: {name}: {why}").format(name=posixpath.basename(path),
                                                            why=why))
    if (dumps.gone or any(freed.gone for _u, freed in files)) and dumps.nothing \
            and all(freed.nothing for _u, freed in files):
        # TRANSLATORS: `diagnostics --free-space --apply` when the files were removed by
        # something else meanwhile
        print(_("Nothing removed: the files were no longer on the device."))
    removed = {kind: found for kind, found in (
        ("logs", dumps.removed), ("updates", [f for _u, freed in files for f in freed.removed]))
        if found}
    if removed:
        try:  # the device log tells the state after it (the page of a device not connected)
            history.after_removal(dev, st, removed)
        except DeviceNotResponding:
            raise
        except (DeviceError, OSError) as e:
            # TRANSLATORS: after --free-space --apply removed files, when the device could not
            # be counted again
            log.info("free space: not counted again after the removal (%s)", type(e).__name__)
            print(_("The device could not be counted again after the removal; its records keep "
                    "what was removed."))


def crash_sources(crash) -> str:
    """Where a crash was found, for `diagnostics`: the error logs on the device by their file
    names, then the copies on this computer, then the boot log."""
    from openexpress import crashes

    words = {crashes.COPY: _("copy on this computer"),
             # TRANSLATORS: the log a fēnix 8 writes at each start (recovery_mode_log.txt)
             crashes.BOOT_LOG: _("boot log"),
             # TRANSLATORS: where a crash was found: the log of the device's connections that
             # openExpress keeps (the line "Device log on this computer: …")
             crashes.DEVICE_LOG: _("device log on this computer")}
    names = sorted(s for s in crash.sources if s not in words)
    return ", ".join([*names, *(words[s] for s in (crashes.COPY, crashes.BOOT_LOG,
                                                   crashes.DEVICE_LOG)
                                if s in crash.sources)])


# The newest connections `diagnostics` shows of the device log.
CONNECTIONS_SHOWN = 5


def _storage_kinds() -> dict[str, str]:
    """The kinds of file of the device log (history.CATEGORIES), as the command line names
    them."""
    return {
        # TRANSLATORS: a kind of file in "Storage at the last connection: …" of `diagnostics`,
        # lowercase, followed by its size
        "maps": pgettext("storage", "maps"),
        "activities": pgettext("storage", "activities"),
        "music": pgettext("storage", "music and audio"),
        "apps": pgettext("storage", "apps"),
        "courses": pgettext("storage", "courses and locations"),
        "updates": pgettext("storage", "updates and satellite data"),
        "logs": pgettext("storage", "logs and memory dumps"),
        # TRANSLATORS: the rest of the used space: the device's own files and what its file
        # system keeps
        "system": pgettext("storage", "system"),
    }


def history_lines(unit_id: int, test_drive: bool = False) -> list[str]:
    """The device log for `diagnostics`: how many connections it holds, the newest of them and
    the space of each kind of file at the last one; damaged lines counted, never shown."""
    from openexpress import history

    kept = history.read(unit_id, test_drive)
    lines = kept.lines
    if not lines:
        out = [_("Device log on this computer: no connection yet")]
    else:
        first = history.local_time(lines[0]).strftime("%Y-%m-%d")
        out = [ngettext("Device log on this computer: {count} connection, the first on {day}",
                        "Device log on this computer: {count} connections, the first on {day}",
                        len(lines)).format(count=len(lines), day=first)]
        out += ["  " + _history_line(line) for line in reversed(lines[-CONNECTIONS_SHOWN:])]
        earlier = len(lines) - CONNECTIONS_SHOWN
        if earlier > 0:
            out.append("  " + ngettext("and {count} earlier connection",
                                       "and {count} earlier connections", earlier).format(
                count=earlier))
        names, sizes = _storage_kinds(), lines[-1]["categories"]
        shown = [f"{names[kind]} {human_size(sizes[kind])}" for kind in history.CATEGORIES
                 if kind in sizes]
        if shown:
            out.append(_("Storage at the last connection: {sizes}").format(
                sizes=", ".join(shown)))
    if kept.skipped:
        out.append("  " + ngettext("{count} damaged line skipped", "{count} damaged lines skipped",
                                   kept.skipped).format(count=kept.skipped))
    return out


def _history_line(line: dict) -> str:
    from openexpress import history

    # TRANSLATORS: in a line of the device log (`diagnostics`): the device's software version
    parts = [_("software {version}").format(version=line["software"])]
    # What was not known at that connection is said so, apart from none (no dumps, no crash).
    if "free" in line and "capacity" in line:
        # TRANSLATORS: in a line of the device log (`diagnostics`): its free space then
        parts.append(_("{free} of {total} free").format(free=human_size(line["free"]),
                                                       total=human_size(line["capacity"])))
    else:
        # TRANSLATORS: in a line of the device log (`diagnostics`): it could not be read
        parts.append(_("free space not known"))
    dumps = line.get("dumps")
    if dumps is None:
        # TRANSLATORS: in a line of the device log (`diagnostics`): they could not be listed
        parts.append(_("memory dumps not known"))
    elif dumps["count"]:
        parts.append(ngettext("{count} memory dump ({size})", "{count} memory dumps ({size})",
                              dumps["count"]).format(count=dumps["count"],
                                                     size=human_size(dumps["bytes"])))
    else:
        # TRANSLATORS: in a line of the device log (`diagnostics`)
        parts.append(_("no memory dumps"))
    if "crash" not in line:
        # TRANSLATORS: in a line of the device log (`diagnostics`): its error logs could not
        # be read
        parts.append(_("crashes not known"))
    elif line["crash"] is None:
        # TRANSLATORS: in a line of the device log (`diagnostics`)
        parts.append(_("no crash in the error logs"))
    else:
        # TRANSLATORS: in a line of the device log (`diagnostics`); {crash} is in English, as
        # the device logs it ("RTL Trap at 2026-10-20 22:24:06, error 0x003, …")
        parts.append(_("last crash: {crash}").format(crash=history.crash_entry(line).words()))
    via = _("USB drive") if line["via"] == "drive" else "MTP"
    when = history.local_time(line).strftime("%Y-%m-%d %H:%M")
    return f"{when} {via}: " + ", ".join(parts)


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
        # TRANSLATORS: label in `openexpress info`, followed by Garmin's part number of the model
        # ("006-B1234-00")
        (_("Part number:"), dev.part_number),
        # TRANSLATORS: label in `openexpress info`, followed by the version of the device's software
        (_("Software:"), dev.software_version_str),
        # TRANSLATORS: label in `openexpress info`, followed by Garmin's number of this one device
        # (its unit ID)
        (_("Unit ID:"), dev.unit_id),
        # TRANSLATORS: label in `openexpress info`, followed by where the files are: the MTP
        # storage's name or "USB drive (…)"
        (_("Storage:"), storage_text(st)),
        (_("Free space:"), human_size(st.free_space())),
        # TRANSLATORS: label in `openexpress info`, followed by "{datatypes}, components:
        # {components}"
        (_("Data types:"),
         # TRANSLATORS: `openexpress info`, after "Data types:": {datatypes} how many data
         # types, {components} how many software components
         _("{datatypes}, components: {components}").format(
             datatypes=len(dev.datatypes()), components=len(dev.update_files))),
    ]
    if dev.iq_max_apps:
        rows.append(("Connect IQ:", ngettext(
            "max. {count} app, {size}", "max. {count} apps, {size}", dev.iq_max_apps).format(
                count=dev.iq_max_apps, size=human_size(dev.iq_app_space))))
    width = max(len(label) for label, _value in rows) + 1
    for label, value in rows:
        print(f"{label:{width}} {value}")
    return 0


def cmd_report(args) -> int:
    from openexpress import report

    try:
        st, dev = open_device(args)
    except DeviceNotResponding:
        raise  # a stall stops what meets it
    except DeviceError as e:
        if args.path:
            raise  # a folder named to report on that is no device
        # Not ready, not supported, none…: a report of what is on USB, and the log.
        log.info("report: no device opened (%s), a report of what is on USB", type(e).__name__)
        st, dev, why = None, None, e
    try:
        store.load()  # it returns a login or raises
        logged_in = True
    except Exception:  # noqa: BLE001 - only yes/no: a broken login never stops the report
        logged_in = False
    if dev is None:
        report_text = report.build_without_device(why, logged_in)
        model = report.model_on_usb()
        # TRANSLATORS: before the hints of `openexpress report` when no device could be opened
        print(_("No device was opened: the report says what is connected over USB."),
              file=sys.stderr)
    else:
        report_text = report.build(dev, st, logged_in)
        model = dev.description
    # An address that opens the form with the report filled in; never with the log.
    url = report.issue_url(report_text, model)
    if not args.log or url is None:  # too long for an address: the report printed as well
        print(report_text, end="" if not args.log else "\n")
    if args.log:
        # The log alone, ready for the "Log" field of the form: what the window puts in the
        # clipboard for it.
        print(report.form_log(applog.tail(report.LOG_LINES), applog.crash_tail()), end="")
    if url:
        print("\n" + _("This report contains no serial number, Unit ID or e-mail. Open this "
                       "address to start a device report with it filled in: {url}").format(
            url=url), file=sys.stderr)
    else:  # too long for an address: the empty form
        print("\n" + _("This report contains no serial number, Unit ID or e-mail. "
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
        print(f"{dt:22} {count:12} {human_size(sum(files.values())):>9}  ({paths_})")
        if args.type or args.verbose:
            for p in sorted(files)[-args.limit:] if args.limit else sorted(files):
                print(f"    {p}  {human_size(files[p])}")
    total = len(seen)
    print(ngettext("Total: {count} file", "Total: {count} files", total).format(count=total))
    return 0


def _at_least_one(text: str) -> int:
    """The value of an option that counts something: a whole number from 1 (else the command
    line is wrong, exit code 2)."""
    try:
        number = int(text)
    except ValueError:
        number = 0
    if number < 1:
        raise argparse.ArgumentTypeError(
            _("a whole number from 1, not {value}").format(value=text))
    return number


def cmd_activities(args) -> int:
    """The activities of the connected device and of its backups, the newest first: one row
    each, the device's before those only in a backup; nothing is written."""
    from openexpress import activities

    st, dev = open_device(args)
    # TRANSLATORS: first line of `openexpress activities` and `sync`: {device} the device's name,
    # {version} its software version
    print(_("{device}, software {version}").format(
        device=dev.description, version=dev.software_version_str))
    exporting = args.activities_cmd == "export"
    if not activities.keeps_activities(dev):
        print(_("This device keeps no activities."))
        return 1 if exporting else 0
    backups = getattr(args, "export_backups", None) or args.backups
    found = activities.DeviceActivities(
        dev, st, Path(backups) if backups else paths.default_backup_dir())
    found.list_files()
    if exporting:
        return _export_activities(args, found)
    if not found.listing:
        print(_("No activities on the device or in its backups."))
        return 0
    with _progress() as progress:
        read = found.read_next(len(found.listing) if args.all else args.limit, progress)
    for line in _activity_table(read):
        print(line)
    only_in_backups = sum(1 for a in found.listing if a.source is activities.Source.BACKUP)
    words = [_("The newest {shown} of {count}; --all shows every one.").format(
                 shown=len(read), count=len(found.listing)) if found.left
             # TRANSLATORS: under the table of `openexpress activities`: how many recorded
             # activities the device and its backups have
             else ngettext("{count} activity.", "{count} activities.",
                           len(found.listing)).format(count=len(found.listing))]
    if only_in_backups:
        words.append(_("Only in backups: {count}.").format(count=only_in_backups))
    print("\n" + " ".join(words))
    return 0


def _export_activities(args, found) -> int:
    """`activities export NAME…`: each activity named (as the list names its file) as a GPX
    file "YYYY-MM-DD HH.MM Sport.gpx" in --dest or the GPX folder. Nothing is written unless
    every one of them can be, and only with --apply; a file that is there is never
    overwritten. The log gets counts and sizes, never a name or a path."""
    from openexpress import activities, fit, gpx

    dest = Path(args.dest) if args.dest else paths.default_gpx_dir()
    by_name: dict = {}
    for a in found.listing:
        by_name.setdefault(a.name.casefold(), a)  # the device's first, then the newest backup
    planned: list[tuple[Path, bytes, int]] = []
    refused: list[tuple[str, str]] = []
    for name in args.names:
        a = by_name.get(name.casefold())
        if a is None:
            refused.append(("not found", _("No activity named {name} is on the device or in "
                                           "its backups.").format(name=name)))
            continue
        data = found.read_file(a)
        try:
            summary = None if data is None else fit.summary(data)
        except fit.FitError:
            summary = None
        if summary is None:
            refused.append(("unreadable", _("{name} cannot be read as a FIT file.").format(
                name=a.name)))
            continue
        if summary.state is fit.FileState.STILL_BEING_WRITTEN:
            refused.append(("still being written", _(
                "{name} was still being written by the device when it was read. Unplug the "
                "device and plug it in again, then export it again.").format(name=a.name)))
            continue
        stem = activities.export_name(summary, a)
        try:
            track = gpx.track(data, name=stem)
            count = sum(1 for _point in fit.points(data))
        except (gpx.NoPositions, fit.FitError) as e:
            kind = "no positions" if isinstance(e, gpx.NoPositions) else "damaged"
            # TRANSLATORS: an activity not exported to GPX: {name} its file name, {error} why
            refused.append((kind, _("{name}: {error}").format(name=a.name, error=e)))
            continue
        target = dest / f"{stem}.gpx"
        if target.exists() or any(target == p for p, _track, _count in planned):
            refused.append(("there", _("{path} exists already; nothing is overwritten.").format(
                path=target)))
            continue
        planned.append((target, track, count))
    log.info("activities export: %d to write, %d refused (%s)", len(planned), len(refused),
             applog.counts(kind for kind, _words in refused))
    if refused:
        for _kind, words in refused:
            _error(_("Error: {error}").format(error=words))
        _error(_("Nothing was written."))
        return 1
    if not args.apply:
        for target, _track, count in planned:
            print(ngettext("Would write {path} ({count} point)",
                           "Would write {path} ({count} points)", count).format(
                path=target, count=count))
        print(ngettext("Dry run: nothing was written. Add --apply to write it.",
                       "Dry run: nothing was written. Add --apply to write them.",
                       len(planned)))
        return 0
    for target, track, count in planned:
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with open(target, "xb") as f:  # never over a file that appeared meanwhile
                f.write(track)
        except OSError as e:
            log.info("activities export: a GPX file could not be written (%s)",
                     type(e).__name__)
            words = (_("{path} exists already; nothing is overwritten.")
                     if isinstance(e, FileExistsError) else _("Cannot write {path}"))
            _error(_("Error: {error}").format(error=words.format(path=target)))
            return 1
        print(ngettext("Wrote {path} ({count} point).", "Wrote {path} ({count} points).",
                       count).format(path=target, count=count))
    log.info("activities export: %d GPX files written (%s)", len(planned),
             applog.size(sum(len(track) for _target, track, _count in planned)))
    return 0


def _activity_row(read) -> tuple[list[str], str | None]:
    """The columns of one activity in `activities` and the note under it (its state)."""
    from openexpress import activities, fit

    a, summary = read.activity, read.summary
    if a.source is activities.Source.DEVICE:
        # TRANSLATORS: activities: where an activity was read
        source = pgettext("activity source", "device")
    else:
        source = pgettext("activity source", "backup {date}").format(
            date=a.backup.strftime(_("%Y-%m-%d")))
    dash = "–"
    if summary is None:
        # TRANSLATORS: a note under a row of `openexpress activities`: the activity's file could not
        # be read
        return [dash] * 5 + [source, a.name], _("unreadable file")
    when = summary.start or summary.created
    cells = [_seen_at(when) if when else dash,
             activities.sport_name(summary.sport) if summary.sport is not None else dash]
    if summary.state is fit.FileState.STILL_BEING_WRITTEN:
        return cells + [dash] * 3 + [source, a.name], _(
            "still being written: unplug the device and plug it in again")
    seconds = None if summary.timer_time is None else round(summary.timer_time)
    cells += [
        dash if seconds is None else f"{seconds // 3600}:{seconds // 60 % 60:02}:{seconds % 60:02}",
        dash if not summary.distance else _km(summary.distance),
        dash if summary.ascent is None else f"{summary.ascent} m",
        source, a.name]
    # TRANSLATORS: a note under a row of `openexpress activities`: the activity's file is damaged
    return cells, _("damaged file") if summary.state is fit.FileState.DAMAGED else None


def _activity_table(read) -> list[str]:
    """The lines of `activities`: the heading, then a row for each activity, its note (a
    file still being written, damaged or unreadable) under its sport."""
    heading = [
        # TRANSLATORS: column heading in `openexpress activities`: when the activity started
        # (date and hour)
        _("Date"),
        # TRANSLATORS: column heading in `openexpress activities`: the activity's sport
        _("Sport"),
        pgettext("activity list column", "Time"),
        # TRANSLATORS: column heading in `openexpress activities`: the distance covered
        _("Distance"),
        # TRANSLATORS: column heading in `openexpress activities`: the total ascent of the
        # activity, in metres
        _("Ascent"),
        # TRANSLATORS: column heading in `openexpress activities`: where the activity was
        # found, on the device or in a backup (not source code)
        _("Source"),
        # TRANSLATORS: column heading in `openexpress activities`: the name of the file
        _("File"),
    ]
    rows = [_activity_row(r) for r in read]
    widths = [max(len(cells[n]) for cells in [heading] + [c for c, _note in rows])
              for n in range(len(heading))]

    def line(cells: list[str]) -> str:
        return "  ".join(cell.ljust(width) for cell, width in zip(cells, widths, strict=True))

    lines = [line(heading).rstrip()]
    for cells, note in rows:
        lines.append(line(cells).rstrip())
        if note:
            lines.append(" " * (widths[0] + 2) + note)
    return lines


def cmd_fit(args) -> int:
    """`fit inspect FILE`: what a FIT file on this computer holds, and 0 only when it is
    whole. The log gets its state and size, never its name or what it holds."""
    from openexpress import fit

    path = Path(args.file)
    try:
        data = path.read_bytes()
    except OSError as e:
        log.info("fit inspect: the file could not be read (%s)", type(e).__name__)
        words = (_("File not found: {path}") if isinstance(e, FileNotFoundError)
                 else _("Cannot read {path}")).format(path=path)
        _error(_("Error: {error}").format(error=words))
        return 1
    found = fit.inspect(data)
    log.info("fit inspect: %s, %d kinds of messages%s",
             found.not_whole.words() if found.not_whole else f"whole, {applog.size(len(data))}",
             len(found.messages), ", reading stopped" if found.broke_off else "")
    for line in _inspection(path.name, found):
        print(line)
    return 0 if found.whole else 1


def _fit_check(found) -> str:
    """The line "Check: …" of `fit inspect`."""
    from openexpress.fit import Problem

    not_whole = found.not_whole
    if not_whole is None:
        # TRANSLATORS: fit inspect: the file is whole (B: bytes)
        return _("Check: whole (header {header} B, data {data} B, CRC matches)").format(
            header=found.header_size, data=found.data_size)
    if not_whole.problem is Problem.CUT_SHORT:
        # TRANSLATORS: fit inspect: the header of the file declares more bytes than were read
        return _("Check: cut short: {read} B read, {declared} B declared (still being "
                 "written when it was read)").format(read=not_whole.read,
                                                     declared=not_whole.declared)
    if not_whole.problem is Problem.CRC:
        return _("Check: damaged: the CRC does not match ({read} B read)").format(
            read=not_whole.read)
    if not_whole.problem is Problem.AFTER_THE_END:
        return _("Check: damaged: {count} B after the whole FIT data are no FIT file").format(
            count=not_whole.after)
    return _("Check: not a FIT file ({read} B read)").format(read=not_whole.read)


def _inspection(name: str, found) -> list[str]:
    """The lines of `fit inspect` for the file `name`: the file, its check, then (a FIT file)
    its type, manufacturer, product, creation time and messages, the most frequent first."""
    from openexpress import fit, service

    # TRANSLATORS: fit inspect: the file that was read, {name} its name, {size} its size
    lines = [_("File: {name}, {size}").format(name=name, size=human_size(found.size)),
             _fit_check(found)]
    if found.header_size is None:
        return lines
    file_type = "-" if found.file_type is None else f"FIT_TYPE_{found.file_type}"
    if file_type in service.TYPE_NAMES:
        file_type = f"{_(service.TYPE_NAMES[file_type])} ({file_type})"
    maker = found.manufacturer
    if maker is None:
        maker = "-"
    elif maker in fit.MANUFACTURER_NAMES:
        maker = f"{fit.MANUFACTURER_NAMES[maker]} ({maker})"
    lines += [
        # TRANSLATORS: fit inspect: the kind of FIT file ("activities (FIT_TYPE_4)")
        _("Type: {type}").format(type=file_type),
        _("Manufacturer: {manufacturer}").format(manufacturer=maker),
        # TRANSLATORS: fit inspect: the product number of the device that made the file
        _("Product: {product}").format(product="-" if found.product is None else found.product),
        # TRANSLATORS: fit inspect: when the file was created (date and hour)
        _("Created: {when}").format(
            when="-" if found.created is None else _seen_at(found.created)),
    ]
    kinds = len(found.messages)
    # TRANSLATORS: fit inspect: "Messages: 6 kinds, 1213 in all"
    lines.append(_("Messages: {kinds}, {count} in all").format(
        # TRANSLATORS: fit inspect: fills {kinds} in "Messages: {kinds}, {count} in all": how many
        # kinds of messages
        kinds=ngettext("{count} kind", "{count} kinds", kinds).format(count=kinds),
        count=sum(found.messages.values())))
    named = [(f"{fit.MESSAGE_NAMES[num]} ({num})" if num in fit.MESSAGE_NAMES
              # TRANSLATORS: fit inspect: a message the FIT profile does not name here
              else _("message {number}").format(number=num), count)
             for num, count in sorted(found.messages.items(), key=lambda t: (-t[1], t[0]))]
    width = max((len(label) for label, _count in named), default=0)
    digits = max((len(str(count)) for _label, count in named), default=0)
    lines += [f"  {label:<{width}}  {count:>{digits}}" for label, count in named]
    developer = any(num in found.messages for num in fit.DEVELOPER_MESSAGES)
    lines.append(
        # TRANSLATORS: fit inspect: the file has developer data (fields that Connect IQ apps add)
        _("Developer data: present") if developer
        # TRANSLATORS: fit inspect: the file has no developer data (fields that Connect IQ apps
        # add)
        else _("Developer data: none"))
    if found.broke_off:
        # TRANSLATORS: fit inspect: the reading of the file broke off; {error} says why
        lines.append(_("Reading stopped: {error}").format(error=found.broke_off))
    return lines


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
    print(_("Signed in as {account}.").format(account=auth.masked_display))
    return 0


def cmd_logout(args) -> int:
    print(_("Signed out (local token removed).") if store.delete()
          else _("No saved token found."))
    return 0


def cmd_whoami(args) -> int:
    auth = store.load()
    settings = Settings.load()
    print(_("Account: {account} (environment {environment})").format(
        account=auth.masked_display, environment=auth.environment))
    if args.check:
        prof = make_client(settings, need_auth=True).user_profile()
        print(_("Token works: {username} / {email}").format(
            username=_masked_field(prof.get("username")),
            email=_masked_field(prof.get("emailaddress"))))
    return 0


def _masked_field(value) -> str:
    """A field of the account's profile as the command line shows it: masked; "?" when the
    profile gives none."""
    return store.masked(value) if isinstance(value, str) and value else "?"


def cmd_status(args) -> int:
    """Print what this installation knows now: the login, the Garmin devices connected (name,
    USB mode, whether on the account), the last sync of each and what the last check of its
    updates found. Reads only the devices' GarminDevice.xml and the files this installation
    keeps; with --online it asks the account's device list and the update server too,
    keeping nothing."""
    from openexpress.device import gio_storage

    try:
        auth = store.load()
    except store.NotLoggedIn:
        auth = None
    print(_("Garmin Connect: signed in as {account}").format(account=auth.masked_display)
          if auth is not None else _("Garmin Connect: not signed in"))
    summaries = gio_storage.distinct_devices(
        [gio_storage.probe(f) for f in gio_storage.find_all_devices()])
    if not summaries:
        print(_("No Garmin device is connected."))
        return 0
    print(ngettext("Connected: {count} device", "Connected: {count} devices",
                   len(summaries)).format(count=len(summaries)))
    client, listed = None, None
    if args.online:  # asked as `openexpress updates` asks them
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
    from openexpress import service, sync, tile_facts
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
        # TRANSLATORS: `openexpress status`, after a device's name: the device is on the signed-in
        # Garmin account
        parts.append(_("on your account") if on else _("not on your account")
                     # TRANSLATORS: `openexpress status`, after a device's name: it is not known
                     # whether the device is on the Garmin account
                     if on is False else _("account not known"))
    print(" · ".join(parts))
    if not drive:
        # As the device page: the activities it has to upload now, listed only.
        to_sync = (sync.activities_to_sync(s.device, s.storage)
                   if on is not False and s.device is not None and s.storage is not None
                   and sync.synced_here(s.unit_id) else None)
        text = words.last_sync_subtitle(s.unit_id, service.load_last_sync(s.unit_id), to_sync,
                                        now)
        # TRANSLATORS: `openexpress status`: {text} gives the window's words for the device's last
        # sync
        print("  " + _("Last sync: {text}").format(text=text))
    text = (_status_updates_online(client, s) if client is not None
            else _status_updates(entry, s.software_version, now))
    # TRANSLATORS: `openexpress status`: {text} says whether an update of the device's software is
    # available
    print("  " + _("Device software: {text}").format(text=text))


def _update_words(state: str, count: int = 0, only=None) -> str:
    """The words of `openexpress status` for the state of a device's updates (updates.state,
    known_devices.recorded_state); `count`: the updates to install, `only`: the one of them."""
    from openexpress.gui import words

    if state == "ready":
        return caption(_("an update is ready: unplug the device to install it"),
                       words.more_available(count))
    if state == "available":
        return words.updates_available(count, only)
    if state == "not-installed":
        # TRANSLATORS: after "Device software:" in openexpress status: an update openExpress
        # copied is on the device, which was unplugged since and has not installed it
        return _("update copied, not installed by the device")
    # TRANSLATORS: after "Device software:" in openexpress status
    return _("up to date")


def _status_updates(entry, software: str, now) -> str:
    """What the last check of updates kept with the device found, while it holds (recent, of
    the software it has now)."""
    from openexpress.gui import words

    if entry is not None:
        recorded = known_devices.recorded_state(entry, software, now.timestamp())
        if recorded is not None:
            return words.checked_text(
                _update_words(recorded.state, recorded.count, recorded.only), recorded.at, now)
        last = entry.update_check.at if entry.update_check is not None else entry.update_found
        if last is not None:
            return words.not_checked_since(last)
    return _("not checked yet (openexpress status --online checks it)")


def _status_updates_online(client, s) -> str:
    """The updates of the device as Garmin's update server offers them now."""
    from openexpress import updates as upd
    from openexpress.device import gio_storage

    try:
        device, storage = s.device, s.storage
        if device is None or storage is None:  # a drive: probe keeps nothing of it
            storage = gio_storage.open_ums_storage(s.found)
            device = parse(storage.read("GARMIN/GarminDevice.xml"))
        items = upd.offered(storage, client.software_updates(device.raw), device,
                            forget_installed=False)  # keeps nothing
    except DeviceNotResponding:
        raise
    except (ApiError, DeviceError) as e:
        return _("could not be checked: {error}").format(error=e)
    new = upd.pending(items)
    return _update_words(upd.state(new, upd.staged(items), upd.not_installed(items)), len(new),
                         new[0] if len(new) == 1 else None)


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
        # TRANSLATORS: after clearing the cache: how much disk space was freed
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
                "last sync, the log of its connections, the maps of your own copied to it and "
                "the copies of its diagnostic files. Its next sync with this program will be a "
                "first sync again."))
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
    # TRANSLATORS: a device was forgotten, so this computer no longer remembers it; {device} is its
    # name
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
    """A moment as the program writes dates, in local time: "DD.MM.YYYY HH:MM" in Polish."""
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
    # TRANSLATORS: `openexpress upload-config`: {name} is the kind of device as Garmin's server
    # names it
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
    from openexpress import service

    st, dev = open_device(args)
    settings = Settings.load()
    print(_("{device}, software {version}").format(
        device=dev.description, version=dev.software_version_str))
    try:
        client = make_client(settings, need_auth=True)
    except store.NotLoggedIn:
        if args.apply:
            raise
        # The upload plan can be shown without logging in; the download queue needs a token.
        client = make_client(settings, need_auth=False)
        print(_("(not signed in: showing the upload plan only)"))
    session = service.DeviceSession(st, dev, settings, client)
    if st.mass_storage and not args.apply:
        service.refuse_drive_sync(session)  # with --apply in run_sync, before its uploads
    # The sync of the window and the background service (service.run_sync), with what was
    # asked here.
    options = service.SyncOptions(
        first_run="all" if args.all else None, include_diagnostics=args.include_diagnostics,
        only_types=frozenset(args.type) if args.type else None,
        uploads=not args.download_only, downloads=not args.upload_only,
        drive=st.mass_storage)
    if not args.apply:
        _print_sync_plan(service.plan_sync(session, dataclasses.replace(options, preview=True)),
                         client, drive=st.mass_storage, makers=service.queue_makers(session))
        print("\n" + _("Dry run: nothing was uploaded or written to the device. "
                       "Add --apply to run the sync."))
        return 0
    try:
        with _progress() as progress:
            summary = service.run_sync(session, progress=progress, options=options)
    except service.FirstSyncChoiceNeeded:
        print(
            "\n" + _("This is the first sync with this program. If the device also syncs "
                     "via your phone, most files are already in Garmin Connect.\n"
                     "  openexpress baseline --apply   marks current files as uploaded "
                     "(recommended)\n"
                     "  openexpress sync --apply --all uploads everything "
                     "(Garmin Connect rejects duplicates)"),
            file=sys.stderr,
        )
        return FIRST_SYNC_CHOICE
    except (Exception, KeyboardInterrupt) as e:
        done = service.done_before(e)
        if done is not None:  # stopped after its uploads: what they did, then the error
            _print_sync_steps(done, dev.unit_id)
        raise
    code = _print_sync_result(summary, dev.unit_id)
    # A drive the sync wrote to is ejected, so that the watch takes the files.
    if st.mass_storage and summary.downloaded and not _eject_drive(st, files=True):
        return 1  # the cable must stay in
    return code


def _print_sync_plan(plan, client, drive: bool = False, makers=None) -> None:
    """What `sync` would do (its dry run), as service.plan_sync worked it out. On a `drive`
    a Connect IQ app stays in Garmin Connect's queue (service.stays_in_queue). Under each file
    of the queue the sync would write to the device: what it is (sync.describe_queue, which
    downloads it into memory and acknowledges nothing), and who made it (`makers`,
    sync.Makers)."""
    from openexpress import service, sync

    if plan.test_drive:
        print("\n" + service.test_drive_text())
    if plan.upload_error is not None:
        print("\n" + plan.upload_error)
    uploads = plan.uploads
    if uploads is not None:
        counts = collections.Counter(c.datatype for c in uploads.candidates)
        n_up = len(uploads.candidates)
        print("\n" + ngettext("To upload: {count} file (already uploaded earlier: {done})",
                              "To upload: {count} files (already uploaded earlier: {done})",
                              n_up).format(count=n_up, done=uploads.already_done))
        for dt, n in sorted(counts.items()):
            size = sum(c.size for c in uploads.candidates if c.datatype == dt)
            print(f"  {dt:22} {n:5}  {human_size(size)}")
        for dt, why in sorted(uploads.skipped_types.items()):
            # TRANSLATORS: dry run of sync: a data type or a file of the queue that is left out;
            # {reason} says why
            print(f"  {dt:22} " + _("skipped: {reason}").format(reason=why))
        if plan.gate is not None:
            print(plan.gate.message(done=False) if not plan.gate.allowed else _(
                "Uploads are allowed: the Garmin account has the upload consent and device "
                "sync is on."))
    if plan.other_newer is not None:
        print("\n" + service.other_install_text(plan.other_newer))
    if plan.queue is not None:
        items = plan.queue.items

        def left(it):
            return service.stays_in_queue(it, drive)

        n_dl = sum(1 for i in items if not i.unsupported and not left(i))
        print("\n" + _("To download from Garmin Connect to the device: {count}").format(count=n_dl))
        written = [it for it in items if not left(it)]
        described = {id(f.item): f for f in sync.describe_queue(written, client)}
        for it in items:
            note = ("  [" + _("skipped: {reason}").format(reason=it.unsupported) + "]"
                    if it.unsupported
                    else "  [" + _("stays in Garmin Connect's queue") + "]" if left(it)
                    else f"  [{it.handled_by}]" if it.handled_by else "")
            target = it.target.path if it.target else "-"
            print(f"  {it.datatype:22} {it.message_name or it.message_id} -> {target}{note}")
            if id(it) in described:
                print("      " + _queue_file_words(described[id(it)], makers))


def _queue_file_words(found, makers=None) -> str:
    """The line under a file of Garmin Connect's queue in the dry run of `sync`: what it is,
    its sport and name or days, who made it (when it says so), when it was made, and whether
    it is whole."""
    from openexpress import activities, service
    from openexpress.fit import Problem

    if found.error is not None:
        return _("could not be downloaded: {error}").format(error=found.error)
    if found.empty:
        # TRANSLATORS: dry run of sync: the queue names a file Garmin Connect does not have
        return _("Garmin Connect has no file for it")
    if found.described is None:
        return _("not a FIT file ({read} B read)").format(read=found.size)
    d = found.described
    parts = [service.type_name(f"FIT_TYPE_{d.file_type}" if d.file_type is not None
                               else found.item.datatype)]
    if d.sport is not None:
        parts.append(activities.sport_name(d.sport))
    if d.name:
        parts.append(d.name)
    if d.steps is not None:
        # TRANSLATORS: dry run of sync: the number of steps of a workout, not steps walked
        parts.append(ngettext("{count} step", "{count} steps", d.steps).format(count=d.steps))
    if d.days:
        def day(moment: int) -> str:
            return datetime.fromtimestamp(moment, UTC).strftime(_("%Y-%m-%d"))

        # TRANSLATORS: dry run of sync: the days of a schedule from Garmin Connect
        parts.append(ngettext("{count} day from {first} to {last}",
                              "{count} days from {first} to {last}", len(d.days)).format(
            count=len(d.days), first=day(d.days[0]), last=day(d.days[-1])))
    who = makers.words(d) if makers is not None else None
    if who:
        # TRANSLATORS: dry run of sync: the device (or Garmin Connect) that made a queue file
        parts.append(_("made by {maker}").format(maker=who))
    if d.created is not None:
        # TRANSLATORS: dry run of sync: when a file of Garmin Connect's queue was created
        parts.append(_("created {date}").format(
            date=time.strftime(_("%Y-%m-%d"), time.localtime(d.created))))
    not_whole = found.check
    if not_whole is None:
        # TRANSLATORS: dry run of sync: the check of a file from Garmin Connect
        parts.append(pgettext("FIT file", "whole"))
    elif not_whole.problem is Problem.CUT_SHORT:
        parts.append(_("cut short: {read} B read, {declared} B declared").format(
            read=not_whole.read, declared=not_whole.declared))
    elif not_whole.problem is Problem.CRC:
        parts.append(_("damaged: the CRC does not match"))
    else:
        parts.append(_("damaged: {count} B after the end are no FIT file").format(
            count=not_whole.after))
    return " · ".join(parts)


def _print_sync_steps(summary, unit_id: int):
    """The steps of a sync in the words of the details under "Last Sync", its errors and
    the uploads Connect did not confirm. Its words (last_sync.Told)."""
    from openexpress import last_sync

    told = last_sync.describe(summary.record(unit_id))
    said = [text for text, _warning in told.steps]
    # TRANSLATORS: after a sync, under its steps: an error of the sync; {error} says what went wrong
    said += ["  " + _("Error: {error}").format(error=error) for error in summary.errors]
    said += ["  " + _("Warning: Garmin Connect did not confirm processing of {item}").format(
        item=label) for label in summary.not_confirmed]
    if said:
        print("\n" + "\n".join(said))
    return told


def _print_sync_result(summary, unit_id: int) -> int:
    """What `sync --apply` did: its steps in the words of the details under "Last Sync",
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
    settings = Settings.load()
    client = make_client(settings, need_auth=True)
    session = service.DeviceSession(st, dev, settings, client)
    if st.mass_storage:
        # As its sync: a drive that Garmin's word does not let sync is not marked either.
        service.refuse_drive_sync(session)
    service.require_account_device(client, dev.unit_id, part_number=dev.part_number)
    cfg = client.upload_settings(dev.unit_id)
    # The record its sync goes by (a test drive's own; with the other install's taken in).
    if not args.apply:
        n = len(sync.plan_baseline(dev, st, cfg, session.state))
        print(ngettext("Dry run: {count} file would be marked as already uploaded. Add --apply.",
                       "Dry run: {count} files would be marked as already uploaded. Add --apply.",
                       n).format(count=n))
        return 0
    with service.sync_lock():
        n = sync.mark_baseline(dev, st, cfg, session.state, client)
    if not session.test_drive:
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
    with _progress() as progress:
        res = run_backup(dev, st, dest, keep=args.keep, progress=progress)
    print(ngettext("Copied {count} file ({size}) to {path}",
                   "Copied {count} files ({size}) to {path}", res.files).format(
        count=res.files, size=human_size(res.bytes), path=res.path))
    for p in res.removed:
        print(_("Removed old backup {path}").format(path=p))
    if res.incomplete:
        from openexpress.backup import incomplete_text

        print(incomplete_text(res.incomplete))
    return 0


class _Status:
    """Progress on the command line; progress.Reporter decides how often. A `Step`: on a
    terminal one line on stderr, written over; elsewhere a line when a step of another kind
    (head) begins, never a line a file. A wait of known length (TrueUp): one line on stderr
    that fills in place on a terminal, only its first line elsewhere. Any other message: a
    line of its own. `close` ends a line left open."""

    def __init__(self):
        self.open = False
        self.head = None

    def __call__(self, message: str) -> None:
        from openexpress.progress import Step, TimedWait

        if isinstance(message, TimedWait):
            self.head = None
            if not sys.stderr.isatty():
                if message.done == 0:
                    print("  " + plain_spaces(message), file=sys.stdout)
                return
            print(f"\r  {plain_spaces(message)} {int(message.done * 100):3d}%", end="",
                  file=sys.stderr, flush=True)
            self.open = message.done < 1
            if not self.open:
                print(file=sys.stderr)
            return
        if isinstance(message, Step):
            if sys.stderr.isatty():
                print(f"\r\x1b[K  {plain_spaces(message)}", end="", file=sys.stderr, flush=True)
                self.open = True
            elif message.head != self.head:
                print("  " + plain_spaces(message.head), file=sys.stdout)
            self.head = message.head
            return
        self.close()
        self.head = None
        print("  " + plain_spaces(message), file=sys.stdout)

    def close(self) -> None:
        if self.open:
            print(file=sys.stderr)
            self.open = False


@contextlib.contextmanager
def _progress():
    """The progress of a long command: progress.Reporter telling _Status, whose line is
    ended when the command is."""
    status = _Status()
    try:
        yield Reporter(status)
    finally:
        status.close()


def _beta_mark(u) -> str:
    """" [BETA]" after a beta version, in the list of updates and in what will be installed."""
    # TRANSLATORS: marks a beta version in the list of updates
    return " [" + _("BETA") + "]" if u.is_beta else ""


def _update_line(u) -> str:
    """One update in a list. The file name is added when the server gives no real name."""
    name = u.title if u.title == u.name else f"{u.title} ({u.filename})"
    return (f"{u.part_number:18} {u.version:>7}  {u.severity_text:<12} {human_size(u.size):>9}  "
            f"{name}")


def _flags(u) -> str:
    """" [PRIMARY]" after a system update and " [BETA]" after a beta version."""
    # TRANSLATORS: marks the main watch firmware in the list of updates
    return (" [" + _("PRIMARY") + "]" if u.is_system else "") + _beta_mark(u)


def _copied_note(u) -> str | None:
    """Of an update openExpress copied to the device before that the server offers again (the
    device has not installed it): when, and what that means; None for any other."""
    if u.copied_at is None or u.on_device:
        return None
    when = full_time(datetime.fromtimestamp(u.copied_at))
    if u.changed_on_device:
        return _("The device changed or refused the file copied {when}.").format(when=when)
    return _("Copied {when}: check on the device that it is installed.").format(when=when)


def _to_install_staged(st) -> str:
    """What makes the device install an update copied already: a device in mass storage
    mode takes it once its drive is ejected."""
    if getattr(st, "mass_storage", False):
        return _("To install it, eject the drive in the file manager, then unplug the device.")
    return _("Unplug the device to install it.")


def _print_staged(staged, st) -> None:
    """Updates copied to the device already, waiting for it to install them (staged)."""
    if not staged:
        return
    print(_("Update copied, waiting for the device to install it:"))
    for u in staged:
        print("  " + _update_line(u) + _flags(u))
    print(_to_install_staged(st))


def _eject_drive(st, files: bool = False) -> bool:
    """After a write to a Garmin drive: eject it, so that the device takes the update (or,
    `files`, the files of Garmin Connect's queue); only then may the cable go out. False,
    with what to do, when it is not ejected."""
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
        _error(gio_storage.not_ejected_text(window=False, files=files))
        return False
    print(gio_storage.ejected_text(update_waits=not files))
    return True


def _print_not_installed(untaken) -> None:
    """Updates copied, and not installed by the device although it was unplugged since
    (updates.not_installed): what became of each; nothing to eject or to unplug for them."""
    if not untaken:
        return
    # TRANSLATORS: `openexpress updates`: heading above the updates openExpress copied that the
    # device has not installed although it was unplugged since
    print(_("Copied, not installed by the device:"))
    for u in untaken:
        print("  " + _update_line(u))
        when = full_time(datetime.fromtimestamp(u.copied_at))
        if u.changed_on_device:
            print("      " + _("Copied {when}; the device was unplugged since, changed or refused "
                               "the file and has not installed it.").format(when=when))
        else:
            print("      " + _("Copied {when}; the device was unplugged since and has not "
                               "installed it (the file is still on it).").format(when=when))


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
    items = upd.offered(st, resp, dev)
    if not args.reinstalls:
        items = [u for u in items if not u.is_reinstall]
    # A system update that is on the device already waits for the device to install it.
    staged = [] if args.reinstalls else upd.staged(items)
    # Copied, and not installed by the device although it was unplugged since: nothing waits.
    untaken = [] if args.reinstalls else upd.not_installed(items)
    # Offered by the server although the device has the very same file: not an update.
    present = [u for u in items if u.on_device and not any(u is s for s in staged)
               and not any(u is n for n in untaken)]
    # TRANSLATORS: first line of `openexpress updates`: {device} the device's name, {version} its
    # software version
    print(_("{device}, software {version}").format(
        device=dev.description, version=dev.software_version_str))
    if upd.beta_offered(resp):
        print(_(upd.BETA_OFFERED))
    if not args.install:
        listed = items if args.reinstalls else [u for u in items if not u.on_device
                                                and not any(u is n for n in untaken)]
        if not listed:
            if staged:
                _print_staged(staged, st)  # not up to date: the update waits for the device
            elif not untaken:
                print(_("Software is up to date.") if not args.reinstalls
                      else _("The server returned no items."))
            _print_not_installed(untaken)
            _print_present(present, args.verbose)
            return 0
        visible = [u for u in listed if not u.is_metadata or args.verbose]
        # TRANSLATORS: `openexpress updates --reinstalls`: how many items the server offers to
        # install again, though the device has them already
        print((_("Reinstalls: {count}") if args.reinstalls
               else _("Available updates: {count}")).format(count=len(visible)))
        for u in sorted(visible, key=lambda x: (not x.is_system, x.severity.lower() != "critical",
                                                x.name)):
            print("  " + _update_line(u) + _flags(u))
            note = _copied_note(u)
            if note:
                print("      " + note)
            if args.verbose:
                print(f"      -> {u.device_path}")
            for c in u.changes[: (20 if args.verbose else 5)]:
                print(f"      • {c}")
        if not args.reinstalls:
            _print_present(present, args.verbose)
            _print_staged(staged, st)
            _print_not_installed(untaken)
        # TRANSLATORS: `openexpress updates`, under the list: {command} is the command that installs
        # an update (not translated)
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
    # One update for each file, and none for the file of one copied already that waits.
    chosen, put_off = upd.one_per_file(chosen, staged)
    for u, other in put_off:
        print(_("Not now: {name} {version} goes to the same file on the device as {other} "
                "{other_version}. Install it once the device has installed {other}: "
                "unplug the device, then plug it in again.").format(
            name=u.title, version=u.version, other=other.title, other_version=other.version))
    if not chosen:
        return 0
    need, free = upd.check_space(st, chosen)
    # TRANSLATORS: `openexpress updates --install`: heading above the updates about to be installed
    print(_("To install:"))
    for u in upd.sort_for_install(chosen):
        print(f"  {u.part_number} {u.version}  {u.title}{_beta_mark(u)}  ({human_size(u.size)}) "
              f"-> {u.device_path}")
    print(_("Space: {needed} needed, {free} free").format(needed=human_size(need),
                                                         free=human_size(free)))
    eulas = sorted({u.eula_url for u in chosen if u.eula_url})
    for e in eulas:
        print(_("License (EULA): {url}").format(url=e))
    for warning in upd.copy_warnings(chosen):
        print(_(warning))
    if _loses_data_on_update(client, dev):
        print(_(upd.DATA_LOSS_WARNING_DRIVE if getattr(st, "mass_storage", False)
                and not _drive_synced(client, dev) else upd.DATA_LOSS_WARNING_CLI))
    if not args.apply:
        print("\n" + _("Dry run: nothing was downloaded or written. Add --apply "
                       "(this means you accept the licenses above)."))
        return 0
    with _progress() as progress:
        rep = upd.install(chosen, st, session=client.http, progress=progress,
                          unit_id=dev.unit_id, offered=items)
    n = len(rep.written)
    print("\n" + ngettext("Copied {count} file.", "Copied {count} files.", n).format(count=n))
    if n and getattr(st, "mass_storage", False) and not _eject_drive(st):
        return 1  # the cable must stay in: no steps that begin with unplugging it
    if n:
        print(_(FINISH_STEPS))
    return 0


def _drive_synced(client, dev) -> bool:
    """Whether openExpress syncs this drive (service.drive_sync_refusal), from the answer of
    unit-info the data-loss check kept: no request of its own."""
    from openexpress import service

    try:
        info = client.unit_info(dev.unit_id, dev.part_number)
    except ApiError:
        info = None
    return service.drive_sync_refusal(dev, info) is None


def _loses_data_on_update(client, dev) -> bool:
    """Whether Garmin files the device (unit-info, asked here once) as a kind that loses the
    data it holds when its software is updated. A failed request only means no warning."""
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
    from openexpress import service

    def ask():
        try:
            client = make_client(Settings.load(), need_auth=True)
        except store.NotLoggedIn:
            return None  # no login saved: the account is not asked
        service.require_account_device(client, dev.unit_id,
                                       part_number=dev.part_number)  # on each run
        return client.profile_country()

    return service.read_account_country(ask)


def _url_salt() -> str | None:
    salt = load_consumer().url_salt
    if not salt:
        raise SetupError(_("Download URL signing data is missing. Run: {command}").format(
            command="openexpress setup --from-installer GarminExpress.exe"))
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
        file=plan.local.name, size=human_size(plan.size), path=plan.path))
    if plan.free is not None:
        print(_("Free space on the device: {free} (the map takes {size})").format(
            free=human_size(plan.free), size=human_size(plan.size)))
    if not args.apply:
        print(_("Dry run: nothing was changed. Add --apply."))
        return 0
    with _own_map_hints(), _progress() as progress:
        path = ownmaps.add(plan, dev, st, progress=progress)
    print(_("Wrote {path} ({size}).").format(path=path, size=human_size(plan.size)))
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
    print(f"  - {plan.path}  ({human_size(plan.size)})")
    if plan.free is not None:
        print(_("Free space on the device: {free} (removing it frees {size})").format(
            free=human_size(plan.free), size=human_size(plan.size)))
    if not args.apply:
        print(_("Dry run: nothing was changed. Add --apply."))
        return 0
    ownmaps.remove(plan, dev, st)
    print(_("Removed {path} ({size}).").format(path=plan.path, size=human_size(plan.size)))
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
            print(f"  {m.name:48} {m.version:>9}  {human_size(m.size):>9}  {m.status}")
            if (note := mp.restriction_note(m)) is not None:
                print("    " + note)
        if not args.all:
            n = len(all_maps) - len(on_device)
            print("\n" + ngettext("{count} more map is available (openexpress maps --all).",
                                  "{count} more maps are available (openexpress maps --all).",
                                  n).format(count=n))
        print(_("Free space on the device: {size}").format(size=human_size(st.free_space())))
        own = ownmaps.list_own(dev, st, all_maps)
        if own:
            print("\n" + _("Own maps (not from Garmin):"))
            for o in own:
                # TRANSLATORS: in the list of own maps: shown in place of a map's name that could
                # not be read from its file
                title = o.title or _("(name not read)")
                print(f"  {o.filename:48} {human_size(o.size):>9}  {title}")
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

    # The old files Garmin's cleanup rules would take: listed with the plan, in a dry run and
    # with --apply, and never removed.
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
                print(f"  - {p}  ({human_size(st.size(p))})")
            if not args.apply:
                print(_("Dry run: nothing was changed. Add --apply."))
                continue
            with _progress() as progress:
                removed = mp.uninstall(m, all_maps, st, progress=progress)
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
                         size=human_size(m.size), needed=human_size(need),
                         free=human_size(st.free_space())))
        for f in mp.write_order(m):
            print(f"  + {f.device_path}  {human_size(f.size)}")
        for p in removing:
            print(f"  - {p}")
        for e in m.eula_urls:
            print(_("License (EULA): {url}").format(url=e))
        if not args.apply:
            print(_("Dry run: nothing was downloaded or written. Add --apply."))
            continue
        with _progress() as progress:
            rep = mp.install(
                m, all_maps, st,
                activate=lambda ident: client.activate_maps(dev.raw, [ident]),
                session=client.http,
                url_salt=_url_salt(),
                progress=progress,
            )
        print(ngettext("Wrote {count} map file and {unlocks} unlock files.",
                       "Wrote {count} map files and {unlocks} unlock files.",
                       len(rep.written)).format(count=len(rep.written),
                                                unlocks=len(rep.unlocks)))
        print(_("The map will be loaded after the device is unplugged."))
    return 0


APP_TYPE_OPTIONS = {"app": 1, "widget": 2, "watch-face": 3, "data-field": 4, "music-app": 7,
                    "activity": 8}


def _find_app(items, query: str, app_type: int | None = None, also: dict | None = None):
    """One app by name, part of the name or ID; `app_type` picks between entries that share
    a name and ID (built-in items listed both as an app and as a widget). `also`: the other
    name of an app, by its ID, from the other list (the store's name of an app on the device,
    the device's of one on the account), looked for as its own name is."""
    from openexpress import ciq

    if app_type is not None:
        items = [a for a in items if a.app_type == app_type]
    q = query.casefold()

    def names(a) -> list[str]:
        other = (also or {}).get(a.app_id)
        return [a.name.casefold()] + ([other.casefold()] if other else [])

    exact = [a for a in items if q in names(a) or str(a.app_id) == q]
    partial = exact or [a for a in items if any(q in n for n in names(a))]
    if len(partial) == 1:
        return partial[0]
    if not partial:
        raise SetupError(_("No app matches “{query}”").format(query=query))

    def label(a):
        return f"{a.name} ({ciq.app_type_name(a.app_type) or a.app_type})"

    names = "; ".join(label(a) for a in partial)
    if len({a.name.casefold() for a in partial}) == 1:
        raise SetupError(_("Several entries match: {names}. Choose one with --type.").format(
            names=names))
    raise SetupError(_("Ambiguous name, matches: {names}").format(names=names))


def _app_status(disabled: bool) -> str:
    # TRANSLATORS: state of a Connect IQ app or built-in activity on the watch
    return (_("disabled") if disabled
            # TRANSLATORS: state of a Connect IQ app or built-in activity on the watch
            else _("enabled"))


def _apps_queue_only(args, dev, st, client, api, account) -> int:
    """`apps install` on a device without an app list (OUT.BIN): the app is queued in
    Garmin Connect for the phone or Garmin Express; nothing is written to the device."""
    from openexpress import ciq

    target = _find_app(account, args.name)
    print(_("Install {name} {version} ({size})").format(
        name=target.name, version=target.version_name, size=human_size(target.size)))
    targets, _skipped, allowed = _apps_consent(args, [target])
    if not args.apply:
        if targets:
            print("  " + ciq.no_app_list_text([target.name]))
        print(_("Dry run: nothing was changed. Add --apply."))
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
        choices = ciq.order_choices(dev, state)
        if not choices:
            # The folder of this device is named after its unit ID: only the one above it.
            print(_("No copies of the app list in {path}.").format(path=backups))
            return 0
        print(_("Copies of the app list (OUT.BIN); entries on the device now: {count}").format(
            count=len(state.listed)))
        # The oldest first; the same measure as the window: the apps to move.
        for c, plan in reversed(choices):
            print("  " + _("{name}: {entries} entries, {difference}").format(
                name=c.name, entries=len(c.state.apps), difference=ciq.order_difference(plan)))
        print(_("Show what a copy would change: openexpress apps order --from COPY"))
        return 0

    copy = ciq.load_app_list_copy(dev, args.source)
    if ciq.changes_waiting(dev, st):
        raise ciq.CiqError(ciq.waiting_list_text())
    plan = ciq.plan_order(state, copy.state)
    log.info("apps order: plan with %d entries, %d would move; apply: %s", len(plan.entries),
             len(plan.moved), args.apply)
    print(_("Order of the apps from the copy {name}").format(name=copy.name))
    if not plan.moved:
        print(_("The apps on the device are already in this order."))
        return 0
    for line, indented in ciq.describe_order(plan, None if args.all else 10):
        print(("  " if indented else "") + line)
    _print_app_list_size(len(plan.entries), sum(1 for a in state.apps if not a.pending))
    print("  " + _("the same apps with the same flags; only the order changes"))
    if not args.apply:
        print(ciq.app_list_copy_note(dev, st))
        print(_("Dry run: nothing was changed. Add --apply."))
        return 0
    _plan, saved = ciq.restore_order(dev, st, copy.state)
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
    _print_app_list_size(preview.entries, preview.on_device)
    if preview.pending or not preview.kept:
        raise _app_list_mismatch()
    print("  " + _("every other app on the device is kept, in the same order and with the "
                   "same flags"))
    kind = ciq.app_type_name(app.app_type) or app.app_type
    print("  " + _("to be removed: {name} ({type})").format(name=app.name, type=kind))
    if not args.apply:
        print(ciq.app_list_copy_note(dev, st))
        print(_("Dry run: nothing was changed. Add --apply."))
        return 0
    removed = ciq.remove(dev, st, app)
    if app_file not in removed:
        print(_("The app file was already gone; only the app list was updated."))
    _forget_and_say_removed(args, dev, app, api, removed)
    # TRANSLATORS: `openexpress apps remove`: the app that was removed, {name} its name, {type} its
    # kind ("watch face")
    print("  " + _("removed: {name} ({type})").format(name=app.name, type=kind))
    print(_("The device finishes the removal after it is unplugged."))
    return 0


def _forget_and_say_removed(args, dev, app, api, removed: list) -> None:
    """After `apps remove` deleted the app's files (`removed`): with --forget the app goes
    from the account's app list too; then how many files were deleted."""
    if args.forget:
        api.forget(dev, [app.app_id])
        msg = ngettext("Removed {count} file. Also removed from the app list of your Garmin "
                       "account.",
                       "Removed {count} files. Also removed from the app list of your Garmin "
                       "account.",
                       len(removed))
    else:
        msg = ngettext("Removed {count} file.", "Removed {count} files.", len(removed))
    print(msg.format(count=len(removed)))


def _print_app_list_size(entries: int, on_device: int) -> None:
    """The first line of an app list (IN.BIN) to be written: its entries and those the device
    lists now."""
    print(_("App list to be written (IN.BIN): {entries} entries; the device lists {count} "
            "(OUT.BIN)").format(entries=entries, count=on_device))


def _app_list_mismatch():
    """The error of an app list to be written that does not match the device's: nothing is
    written."""
    from openexpress import ciq

    return ciq.CiqError(_("The app list that would be written does not match the device. "
                          "Nothing was changed."))


def _print_in_bin_preview(preview) -> None:
    """The app list (IN.BIN) that an install would write to the device."""
    from openexpress import ciq

    _print_app_list_size(preview.entries, preview.on_device)
    if preview.pending:
        print("  " + ngettext("{count} more app is registered and waits for the device",
                              "{count} more apps are registered and wait for the device",
                              preview.pending).format(count=preview.pending))
    print("  " + (_("every app on the device is kept, in the same order and with the same flags")
                  if preview.kept else
                  _("Warning: the new list does not match the apps on the device")))
    for name, app_type, disabled in preview.new:
        print("  " + (_("new: {name} ({type}), added as inactive") if disabled
                      else _("new: {name} ({type}), added as active")).format(
            name=name, type=ciq.app_type_name(app_type) or app_type))
    if not preview.kept:
        raise _app_list_mismatch()


def cmd_apps(args) -> int:
    from openexpress import ciq

    st, dev = open_device(args)
    settings = Settings.load()
    client = make_client(settings, need_auth=True)
    from openexpress import service

    # Before a change (--apply), the account's list asked anew.
    service.require_account_device(client, dev.unit_id, fresh=bool(getattr(args, "apply", False)),
                                   part_number=dev.part_number)
    api = ciq.StoreApi(client)
    locale = language_tag(settings.locale)
    state = ciq.read_installed(dev, st)
    account = api.account_apps(dev, locale)
    ciq.name_pending(state, account)
    installed_ids = {a.app_id for a in state.apps}
    cmd = args.apps_cmd or "list"

    if cmd == "list":
        updates = {u.app_id: u for u in api.updates(dev, state.store_apps, locale)}
        if state.no_app_list:  # no app was ever installed: none, not an error
            print(ciq.no_apps_text())
        else:
            print(ngettext("Connect IQ on {device}: {count} store app, {slots} free slots, "
                           "{size} free",
                           "Connect IQ on {device}: {count} store apps, {slots} free slots, "
                           "{size} free", len(state.store_apps)).format(
                device=dev.description, count=len(state.store_apps),
                slots=state.available_slots, size=human_size(state.available_space)))
        if state.unreadable:
            print(ngettext("Warning: {count} entry of the app list on the device cannot be read; "
                           "apps cannot be installed, updated or removed.",
                           "Warning: {count} entries of the app list on the device cannot be "
                           "read; apps cannot be installed, updated or removed.",
                           state.unreadable).format(count=state.unreadable))
        for a in state.store_apps:
            upd = updates.get(a.app_id)
            # TRANSLATORS: after an app in the list of `openexpress apps`: {version} is the app's
            # newer version
            note = ("  " + _("update available: {version}").format(version=upd.version_name)
                    # TRANSLATORS: after an app update in `openexpress apps`: the new version asks
                    # for other permissions
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
                print(f"  {a.name:36} {a.store_type:11} {a.version_name:10} {human_size(a.size)}")
        if args.builtin:
            print("\n" + _("Built-in activities and apps:"))
            for a in state.apps:
                if a.preloaded:
                    print(f"  {a.name:36} {a.type_name:11} {_app_status(a.disabled)}")
        print("\n" + _("New apps are chosen in the Connect IQ Store (website or phone); "
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
            targets = [_find_app(account, args.name,
                                 also={a.app_id: a.name for a in state.apps})]
        else:
            targets = api.updates(dev, state.store_apps, locale)
            if not targets:
                print(_("All Connect IQ apps are up to date."))
                return 0
        for a in targets:
            flag = "  " + _("(permissions changed)") if a.permissions_changed else ""
            msg = (_("Update {name} {version} ({size})") if a.app_id in installed_ids
                   else _("Install {name} {version} ({size})"))
            print(msg.format(name=a.name, version=a.version_name, size=human_size(a.size)) + flag)
        targets, skipped, allowed = _apps_consent(args, targets)
        if not targets:
            if not args.apply:
                print(_("Dry run: nothing was changed. Add --apply."))
            return 1
        messages = client.device_messages(dev.unit_id)
        waiting = ciq.queued_app_ids(messages)
        for a in targets:
            if a.app_id in waiting:
                print("  " + _("{name}: already waits in the queue in Garmin Connect; no new "
                               "request will be sent.").format(name=a.name))
        # What is asked of the device from here on is asked once, for the install too.
        op = ciq.Operation(dev, st, state)
        # On some devices openExpress cannot install over USB yet: say so before queueing.
        dirs = {a.app_id: op.folder(ciq.store_datatype(a)) for a in targets}
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
        refused = ciq.install_refusals(dev, st, state, over_usb, op)
        over_usb = [a for a in over_usb if a.app_id not in refused]
        # The app list is written for a new app, and over PTP for an update too; an update
        # through the mounted storage leaves it as it is (it goes next to the app's file).
        if any(state.find(a.app_id) is None or dirs[a.app_id] == ciq.DIRECT for a in over_usb):
            _print_in_bin_preview(ciq.preview_in_bin(state, over_usb))
        if not args.apply:
            for a in targets:
                if a.app_id in refused:
                    # TRANSLATORS: a Connect IQ app that could not be installed: {item} its name,
                    # {error} why
                    print("  " + _("Error: {item}: {error}").format(
                        item=a.name, error=refused[a.app_id]))
            if len(refused) < len(targets):
                if over_usb:
                    print(ciq.app_list_copy_note(dev, st))
                print(_("Dry run: nothing was changed. Add --apply."))
            return 1 if refused or skipped else 0
        with _progress() as progress:
            rep = ciq.install_apps(dev, st, client, api, targets, progress=progress,
                                   allowed=allowed, messages=messages, op=op)
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
            # TRANSLATORS: after installing Connect IQ apps: {names} lists the installed apps,
            # separated by commas, or "none"
            print(_("Installed: {names}").format(names=", ".join(installed) or none))
        for name, err in rep.failed:
            print("  " + _("Error: {item}: {error}").format(item=name, error=err))
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

    app = _find_app(state.apps, args.name, APP_TYPE_OPTIONS.get(getattr(args, "type", None)),
                    also={a.app_id: a.name for a in account})
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
                    print(_("To remove it only from the app list of your Garmin account, use "
                            "--forget."))
            else:
                print(_("{name} cannot be removed over USB: this device shows no app files. "
                        "Remove it on the device itself or in Garmin Connect on the "
                        "phone.").format(name=app.name))
            if not args.forget:
                return 1
            if not args.apply:
                print(_("Dry run: nothing was removed. Add --apply to remove it from the app "
                        "list of your Garmin account."))
                return 0
            api.forget(dev, [app.app_id])
            print(_("Removed {name} from the app list of your Garmin account.").format(
                name=app.name))
            return 0
        print(_("Remove {name} from the device: {files}").format(
            name=app.name, files=", ".join(paths_) or "-"))
        if not args.apply:
            print(ciq.app_list_copy_note(dev, st))
            print(_("Dry run: nothing was changed. Add --apply."))
            return 0
        removed = ciq.remove(dev, st, app)
        _forget_and_say_removed(args, dev, app, api, removed)
        return 0

    enable = cmd == "enable"
    # Refused also in a dry run, as with --apply: a list that waits cannot be read whole.
    ciq.require_readable_waiting_list(state)
    # TRANSLATORS: CLI: the change "apps enable/disable" is about to make, before the dry-run note
    # ({name}: an app or built-in activity)
    print((_("Enable {name}") if enable
           # TRANSLATORS: CLI: the change "apps enable/disable" is about to make, before the
           # dry-run note ({name}: an app or built-in activity)
           else _("Disable {name}")).format(name=app.name))
    if not args.apply:
        print(ciq.app_list_copy_note(dev, st))
        print(_("Dry run: nothing was changed. Add --apply."))
        return 0
    ciq.set_enabled(dev, st, app.app_id, enable, app.app_type)
    print(_("Saved. The device applies the change after you unplug it."))
    return 0


@dataclasses.dataclass(frozen=True)
class _PrimaryRole:
    """One primary role of the account on the command line: how it is read and changed
    (service) and whether the account has one set; `words`, what is said of it."""

    read: Callable  # (client, device) -> its status
    change: Callable  # (client, device, apply) -> its status before
    is_set: Callable  # status -> whether the account has one
    words: dict[str, str]


def _primary_role(wearable: bool) -> _PrimaryRole:
    """The primary training device, or with `wearable` the primary wearable; its words in
    the language of now."""
    from openexpress import service

    if not wearable:
        return _PrimaryRole(
            service.primary_training_status,
            lambda client, dev, apply: service.set_primary_training(client, dev, apply=apply),
            lambda status: status.unit_id is not None, {
                "none": _("No primary training device is set for this Garmin account."),
                "this": _("Primary training device: {name} (this device)"),
                "other": _("Primary training device: {name}"),
                "unlisted": _("a device that is not on the account's list"),
                "not_it": _("The connected device ({device}) is not the primary training "
                            "device."),
                "cannot": _("The connected device cannot be a primary training device."),
                "already": _("{device} is already the primary training device."),
                "would": _("Would change it to: {device}"),
                "set": _("Primary training device set to {device}."),
                "changed": _("Primary training device changed from {old} to {new}."),
                "back": _("To change it back, connect {old} and run `openexpress primary set "
                          "--apply`, or change it in Garmin Connect."),
                # Said first by `primary set`, as the window's question says it.
                "what": _("The primary training device is the main source of performance and "
                          "training data in Garmin Connect."),
            })
    return _PrimaryRole(
        service.primary_wearable_status,
        lambda client, dev, apply: service.set_primary_wearable(client, dev,
                                                                apply=apply).before,
        lambda status: status.set, {
            "none": _("No primary wearable is set for this Garmin account."),
            "this": _("Primary wearable: {name} (this device)"),
            # TRANSLATORS: CLI: the account's primary wearable is another device ({name}: that
            # device's name)
            "other": _("Primary wearable: {name}"),
            "unlisted": _("a device that is not on the account's list"),
            "not_it": _("The connected device ({device}) is not the primary wearable."),
            "cannot": _("The connected device cannot be a primary wearable."),
            "already": _("{device} is already the primary wearable."),
            "would": _("Would change it to: {device}"),
            "set": _("Primary wearable set to {device}."),
            "changed": _("Primary wearable changed from {old} to {new}."),
            "back": _("To change it back, connect {old} and run "
                      "`openexpress primary set --wearable --apply`, or change it in Garmin "
                      "Connect."),
        })


def cmd_primary(args) -> int:
    _st, dev = open_device(args)
    client = make_client(Settings.load(), need_auth=True)
    role = _primary_role(args.wearable or getattr(args, "set_wearable", False))
    if args.primary_cmd == "set":
        return _primary_set(client, dev, args.apply, role)
    words, status = role.words, role.read(client, dev)
    if not role.is_set(status):
        print(words["none"])
    elif status.is_this_device:
        print(words["this"].format(name=status.name or dev.description))
    else:
        print(words["other"].format(name=status.name or words["unlisted"]))
        print(words["not_it"].format(device=dev.description))
    if status.capable is False:
        print(words["cannot"])
    return 0


def _primary_set(client, dev, apply: bool, role: _PrimaryRole) -> int:
    if "what" in role.words:
        print(role.words["what"])  # before anything is asked or changed
    words, before = role.words, role.change(client, dev, apply)
    old = before.name or words["unlisted"]
    if before.is_this_device:
        print(words["already"].format(device=dev.description))
        return 0
    if not apply:
        print(words["other"].format(name=old) if role.is_set(before) else words["none"])
        print(words["would"].format(device=dev.description))
        print(_("Dry run: nothing was changed. Add --apply."))
        return 0
    if not role.is_set(before):
        print(words["set"].format(device=dev.description))
        return 0
    print(words["changed"].format(old=old, new=dev.description))
    print(words["back"].format(old=old))
    return 0


# --- courses -------------------------------------------------------------------------------


def _km(metres: float) -> str:
    return f"{decimal(f'{metres / 1000:.2f}')} km"


def _points(line) -> str:
    """How many points a track or a route of a GPX file has (its own words for each kind, told
    from the file's waypoints)."""
    if line.kind == "track":
        # TRANSLATORS: `openexpress courses`: the points of a track of a GPX file (not its
        # waypoints)
        return ngettext("{count} track point", "{count} track points", line.points).format(
            count=line.points)
    # TRANSLATORS: `openexpress courses`: the points of a route of a GPX file, which a device
    # joins with straight lines (not its waypoints)
    return ngettext("{count} route point", "{count} route points", line.points).format(
        count=line.points)


def _gpx_contents(gpx) -> str:
    """What a GPX file holds, each kind of it: "track, 12.35 km, ↑ 321 m, 120 points"."""
    tracks = [line for line in gpx.lines if line.kind == "track"]
    routes = [line for line in gpx.lines if line.kind == "route"]
    parts = []
    if len(tracks) == 1:
        (track,) = tracks
        words = [pgettext("course contents", "track"), _km(track.distance_m)]
        if track.ascent_m is not None:
            words.append(f"↑ {round(track.ascent_m)} m")
        parts.append(", ".join([*words, _points(track)]))
    elif tracks:
        parts.append(ngettext("{count} track, {distance}", "{count} tracks, {distance}",
                              len(tracks)).format(count=len(tracks), distance=_km(
                                  sum(track.distance_m for track in tracks))))
    if len(routes) == 1:
        parts.append(", ".join([pgettext("course contents", "route"), _points(routes[0])]))
    elif routes:
        parts.append(ngettext("{count} route", "{count} routes", len(routes)).format(
            count=len(routes)))
    if gpx.waypoints:
        parts.append(ngettext("{count} waypoint", "{count} waypoints", gpx.waypoints).format(
            count=gpx.waypoints))
    return "; ".join(parts)


def _course_line(course) -> str:
    """A course of `courses`: its name, where it came from, its distance and ascent, and the
    day its file was made: by the device, or by Garmin Connect when it sent the course, not
    the day the course was created there (each left out when not known)."""
    parts = [course.title]
    if course.origin is not None:
        parts.append(course.origin)
    if course.distance_m:
        parts.append(_km(course.distance_m))
    if course.ascent_m is not None:
        parts.append(f"↑ {course.ascent_m} m")
    if course.made is not None:
        parts.append(time.strftime(_("%Y-%m-%d"), time.localtime(course.made)))
    return "  " + " · ".join(parts)


def _waiting_line(waiting) -> str:
    parts = [waiting.title]
    if waiting.gpx is not None:
        parts.append(_gpx_contents(waiting.gpx))
    if waiting.title != waiting.filename:
        parts.append(waiting.filename)
    if waiting.gpx is None:
        parts.append(human_size(waiting.size))
    return "  " + " · ".join(parts)


def _courses_lines(seen) -> list[str]:
    """The lines of `courses`: the device's courses, the GPX files waiting for it and how many
    saved locations it keeps."""
    lines = []
    if "courses" in seen.unreadable:
        lines.append(_("The courses on the device could not be read."))
    elif seen.courses:
        # TRANSLATORS: CLI heading above the list of the device's courses
        lines.append(ngettext("{count} course on the device:", "{count} courses on the device:",
                              len(seen.courses)).format(count=len(seen.courses)))
        lines += [_course_line(c) for c in seen.courses]
    else:
        lines.append(_("No courses on the device."))
    if "waiting" in seen.unreadable:
        lines.append(_("The files waiting for the device could not be read."))
    elif seen.waiting:
        # TRANSLATORS: CLI heading above the GPX files copied to the device that it has not
        # made courses of yet (it does when it is unplugged)
        lines.append(ngettext("{count} GPX file waiting; unplug the device so it can import it:",
                              "{count} GPX files waiting; unplug the device so it can import "
                              "them:", len(seen.waiting)).format(count=len(seen.waiting)))
        lines += [_waiting_line(w) for w in seen.waiting]
    if "locations" in seen.unreadable:
        lines.append(_("The saved locations could not be counted."))
    elif seen.locations is not None:
        lines.append(_("Saved locations on the device: {count}").format(count=seen.locations))
    return lines


def _course_plan_lines(plan) -> list[str]:
    """What `courses add` would copy: the course's name, the file's name on the device, what
    the file holds and what the device does with it, and the space."""
    from openexpress import courses

    lines = []
    if plan.courses:
        lines.append(ngettext("Course: {names}", "Courses: {names}", len(plan.courses)).format(
            names=", ".join(plan.courses)))
    lines.append("  " + _("{file} ({size}) will be written to {path}").format(
        file=plan.gpx.path.name, size=human_size(plan.size), path=plan.path))
    lines.append("  " + _("Contents: {contents}").format(contents=_gpx_contents(plan.gpx)))
    lines += ["  " + said for said, _warning in courses.what_the_device_does(plan.gpx)]
    if plan.same_waiting is not None:
        lines.append("  " + _("The same file is already waiting on the device as {name}.")
                     .format(name=plan.same_waiting))
    if plan.free is not None:
        lines.append(_("Free space on the device: {free} (the file takes {size})").format(
            free=human_size(plan.free), size=human_size(plan.size)))
    return lines


def _courses_add(args, dev, st) -> int:
    from openexpress import courses

    plan = courses.plan_add(dev, st, Path(args.file), args.name)
    print("\n".join(_course_plan_lines(plan)))
    if not args.apply:
        print(_("Dry run: nothing was changed. Add --apply."))
        return 0
    with _progress() as progress:
        path = courses.add(plan, dev, st, progress=progress)
    print(_("Wrote {path} ({size}).").format(path=path, size=human_size(plan.size)))
    print(_("Unplug the device so it can import the file."))
    return 0


def _name_key(name: str) -> str:
    """A name as `courses remove` matches it: letter case and diacritics aside (ł as l too)."""
    text = unicodedata.normalize("NFKD", name.casefold().replace("ł", "l"))
    return "".join(c for c in text if not unicodedata.combining(c))


def _courses_remove(args, dev, st, seen) -> int:
    """`courses remove NAME`: one course or waiting file, by its name or the file's (letter
    case and diacritics aside), else by a part of it that only one has."""
    from openexpress import courses

    shown = [*seen.courses, *seen.waiting]
    wanted = _name_key(args.name)

    def names(item) -> set[str]:
        return {_name_key(item.title), _name_key(item.filename)}

    found = ([item for item in shown if wanted in names(item)]
             or [item for item in shown if any(wanted in n for n in names(item))])
    if len(found) != 1:
        listed = ", ".join(item.title if item.title == item.filename
                           else f"{item.title} ({item.filename})" for item in found)
        _error(_("Courses and files that match “{name}”: {names}").format(
            name=args.name, names=listed) if found
            else _("No course or waiting file is named “{name}”.").format(name=args.name))
        return 1
    plan = courses.plan_remove(dev, st, found[0])
    print((_("Removing a file waiting for the device: {name}") if plan.waiting
           else _("Removing course: {name}")).format(name=plan.title))
    print(f"  - {plan.path}  ({human_size(plan.size)})")
    print("  " + courses.removal_note(plan))
    if not args.apply:
        print(_("Dry run: nothing was changed. Add --apply."))
        return 0
    courses.remove(plan, dev, st)
    print(_("Removed {path} ({size}).").format(path=plan.path, size=human_size(plan.size)))
    return 0


def cmd_courses(args) -> int:
    """The courses of the connected device, the GPX files waiting for it and its saved
    locations; `add` copies a GPX file for it to make courses of, `remove` removes a course or
    a waiting file (both a dry run without --apply)."""
    from openexpress import courses

    if args.courses_cmd == "add":
        # Before the device is opened: is it a GPX file to copy, and may it take the name?
        courses.check_local(Path(args.file), args.name)
    st, dev = open_device(args)
    if not courses.offered(dev):
        _error(courses.not_offered_text())
        return 1
    if args.courses_cmd == "add":
        return _courses_add(args, dev, st)
    seen = courses.overview(dev, st)
    if args.courses_cmd == "remove":
        return _courses_remove(args, dev, st, seen)
    print("\n".join(_courses_lines(seen)))
    return 0


def cmd_register(args) -> int:
    """Add the connected device to the Garmin account (service.add_device), experimental; a
    dry run without --apply. The account is named by its user name, an e-mail address
    masked: the output is pasted into reports."""
    from urllib.parse import urlsplit

    from openexpress import service
    from openexpress.connect import REGISTER_PATH

    _st, dev = open_device(args)
    client = make_client(Settings.load(), need_auth=True)
    account = client.auth.masked_display
    result = service.add_device(client, dev, apply=args.apply)
    if result.already:
        print(_("{model} is on the Garmin account {account} already.").format(
            model=dev.description, account=account))
        return 0
    if not args.apply:
        address = urlsplit(client.connect_url(REGISTER_PATH))
        print(_("{model} ({part}, software {version}) is not on the Garmin account "
                "{account}.").format(model=dev.description, part=dev.part_number,
                                     version=dev.software_version_str, account=account))
        print(_("Would add it as an active device: POST {address}").format(
            address=address.netloc + address.path))
        print(_("with its GarminDevice.xml ({size}; it holds the device's Unit ID).").format(
            size=human_size(len(dev.raw))))
        print(_("After that, a sync uploads its activities and health data to this account."))
        print(_("A device can be removed from an account only in Garmin Connect."))
        print(_("This is an experimental feature."))
        print(_("Dry run: nothing was changed. Add --apply."))
        return 0
    if result.listed is False:
        _error(_("Garmin accepted the device, but the account's list does not show it yet. "
                 "Try again in a few minutes; if it is still missing, check the devices of "
                 "your account in Garmin Connect."))
        return 1
    if result.listed is None:
        _error(_("Could not read the account's device list to check it. Check the devices "
                 "of your account in Garmin Connect."))
        return 1
    print(_("Added {model} to the Garmin account {account}.").format(model=dev.description,
                                                                    account=account))
    if result.made_active:
        print(_("Garmin kept it as retired; it is active now."))
    return 0


MUSIC_KIND_LINES = {
    # TRANSLATORS: CLI music list: music tracks on the device, not GPS tracks ({count}: how many,
    # {size}: their total size)
    "track": N_("Tracks: {count} ({size})"),
    # TRANSLATORS: CLI music list: podcast episodes on the device ({count}: how many, {size}: their
    # total size)
    "podcast": N_("Podcasts: {count} ({size})"),
    # TRANSLATORS: CLI music list: audiobooks on the device ({count}: how many, {size}: their total
    # size)
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
                count=len(files), size=human_size(sum(f.size for f in files))))
            if args.verbose:
                for f in files:
                    print(f"    {f.path}")
        pls = music.list_playlists(dev, st)
        if pls:
            # TRANSLATORS: CLI music list: how many playlists the device has ({names}: their names,
            # comma-separated)
            print(_("Playlists: {count}: {names}").format(count=len(pls), names=", ".join(pls)))
        elif music.supports_playlists(dev):
            print(_("Playlists: {count}").format(count=len(pls)))
        print(_("Free space: {size}").format(size=human_size(st.free_space())))
        return 0

    if cmd == "add":
        tracks = music.scan([Path(p) for p in args.paths], dev, forced_kind=args.kind)
        plan = music.plan_copy(tracks, dev, st)
        print(ngettext("Found {count} playable file; {to_copy} to copy ({size}), "
                       "{already} already on the device.",
                       "Found {count} playable files; {to_copy} to copy ({size}), "
                       "{already} already on the device.", len(tracks)).format(
            count=len(tracks), to_copy=len(plan.to_copy), size=human_size(plan.size),
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
            print(_("Dry run: nothing was changed. Add --apply."))
            return 0
        with _progress() as progress:
            music.copy(plan, st, progress=progress)
        if args.playlist:
            dests = [d for _t, d in plan.to_copy + plan.already]
            # TRANSLATORS: CLI: the playlist file written on the device after the music was copied
            # ({path}: its path)
            print(_("Playlist: {path}").format(
                path=music.write_playlist(dev, st, args.playlist, dests)))
        print(_("Done."))
        return 0

    if cmd == "playlist":
        tracks = music.scan(music.read_m3u(Path(args.m3u)), dev)
        plan = music.plan_copy(tracks, dev, st)
        name = args.name or Path(args.m3u).stem
        print(ngettext("Playlist “{name}”: {count} track, {to_copy} to copy ({size})",
                       "Playlist “{name}”: {count} tracks, {to_copy} to copy ({size})",
                       len(tracks)).format(name=name, count=len(tracks),
                                           to_copy=len(plan.to_copy), size=human_size(plan.size)))
        if plan.replaced:
            print(music.replaced_text(plan))
        if not args.apply:
            print(_("Dry run: nothing was changed. Add --apply."))
            return 0
        with _progress() as progress:
            music.copy(plan, st, progress=progress)
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
        print(_("Dry run: nothing was changed. Add --apply."))
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
                "background sync in the window: main menu, Preferences, Background Sync."))
        return 0

    if args.remove:
        removed = desktop.uninstall(dry_run=not args.apply)
        for p in removed:
            # TRANSLATORS: CLI install-desktop --remove: a file of the launcher, icon or service
            # that was removed ({path})
            print((_("Removed {path}") if args.apply
                   # TRANSLATORS: CLI install-desktop --remove, dry run: a file of the launcher,
                   # icon or service that would be removed ({path})
                   else _("Would remove {path}")).format(path=p))
        if not removed:
            # TRANSLATORS: CLI install-desktop --remove: no launcher, icon or service file was found
            print(_("Nothing to remove."))
        elif not args.apply:
            print(_("Dry run: nothing was changed. Add --apply."))
        elif any(p.name == desktop.SERVICE for p in removed):
            print(_("The background sync service was stopped and disabled."))
        return 0
    if desktop.flatpak_installed() and not args.force:
        print(_("The Flatpak of openExpress is installed and already provides the menu entry. "
                "A second entry with the same name would hide it. Use --force to install "
                "anyway, or turn on background sync in the Flatpak window."), file=sys.stderr)
        return 1
    if args.apply:
        _build_translations()
    written = desktop.install(dry_run=not args.apply)
    for p in written:
        # TRANSLATORS: CLI install-desktop: a file of the launcher, icon or service that was
        # written ({path})
        print((_("Wrote {path}") if args.apply
               # TRANSLATORS: CLI install-desktop, dry run: a file of the launcher, icon or
               # service that would be written ({path})
               else _("Would write {path}")).format(path=p))
    if not args.apply:
        print(_("Dry run: nothing was changed. Add --apply."))
        return 0
    print("\n" + _("To enable background sync, run:"))
    print("  systemctl --user daemon-reload && systemctl --user enable --now " + desktop.SERVICE)
    return 0


def _build_translations() -> None:
    """From source: the compiled catalogs and the metainfo built from po/ (they are not in the
    repository), before the menu entry, which takes its translations from them. Without
    gettext the program and its entry stay in English: said, and the install goes on."""
    from openexpress import catalogs

    if not catalogs.PO.is_dir():
        return  # an installed package: its catalogs came with it
    try:
        catalogs.build()
        catalogs.compose_metainfo()
    except catalogs.CatalogError as e:
        print(e, file=sys.stderr)


# --- parser ----------------------------------------------------------------------------


# Messages generated by argparse itself; argparse looks them up through
# argparse._, which is pointed at our catalog in build_parser().
ARGPARSE_MESSAGES = [
    # TRANSLATORS: argparse header before the command syntax, keep the trailing space
    N_("usage: "),
    # TRANSLATORS: argparse help heading above the arguments given without a dash (here mostly the
    # commands)
    N_("positional arguments"),
    # TRANSLATORS: argparse help heading above the list of options (--name)
    N_("options"),
    N_("show this help message and exit"),
    N_("show program's version number and exit"),
    N_("the following arguments are required: %s"),
    # TRANSLATORS: argparse error (%s: the arguments it did not recognize)
    N_("unrecognized arguments: %s"),
    N_("invalid choice: %(value)r (choose from %(choices)s)"),
    # TRANSLATORS: argparse error after "argument --NAME: ": the option was given without its value
    N_("expected one argument"),
    N_("%(prog)s: error: %(message)s\n"),
]


def build_parser() -> argparse.ArgumentParser:
    argparse._ = _
    argparse.ngettext = ngettext
    p = argparse.ArgumentParser(
        prog="openexpress",
        description=_("Unofficial Linux app for Garmin watches and bike computers."),
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    # TRANSLATORS: CLI help of -v/--verbose: print more details
    p.add_argument("-v", "--verbose", action="store_true", help=_("more details"))
    p.add_argument("--lang", metavar="LANG",
                   help=_("interface language, e.g. {examples}").format(
                       examples=", ".join(i18n.available_languages())))
    _device_options(p)
    sub = p.add_subparsers(dest="cmd")
    # Without a command the application window opens (as from the application menu), and
    # it is logged as the window. After add_subparsers: it would set "cmd" to None.
    p.set_defaults(func=cmd_gui, cmd="gui")

    s = sub.add_parser("setup", help=_("configuration: OAuth1 key, environment, locale"))
    s.add_argument("--from-installer", metavar="GarminExpress.exe",
                   help=_("read the needed data from the Garmin Express installer for Windows "
                          "(GarminExpress.exe from garmin.com); it is only read, nothing is "
                          "installed"))
    s.add_argument("--from-dll", metavar="Networking.dll",
                   help=_("path to Networking.dll from Garmin Express"))
    s.add_argument("--utilities-dll", metavar="Utilities.dll",
                   help=_("path to Utilities.dll (default: next to Networking.dll)"))
    s.add_argument("--key",
                   help=_("the application key of Garmin Express (its OAuth1 consumer key); "
                          "only together with --secret"))
    s.add_argument("--secret",
                   help=_("OAuth1 consumer secret of Garmin Express; only together with --key"))
    s.add_argument("--environment", choices=sorted(ENVIRONMENTS),
                   help=_("Garmin servers to use: production (default) or china"))
    # TRANSLATORS: CLI help of "setup --locale": "for example" ({example}: a locale code such as
    # pl_PL)
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
    s.add_argument("--json", action="store_true", help=_("print the device information as JSON"))
    s.set_defaults(func=cmd_info)

    s = sub.add_parser("files", help=_("files on the device by data type"))
    s.add_argument("--type", help=_("e.g. {example} (activities)").format(example="FIT_TYPE_4"))
    s.add_argument("--limit", type=int, default=0, metavar="N",
                   help=_("show only the last N files"))
    s.set_defaults(func=cmd_files)

    from openexpress.activities import PAGE

    s = sub.add_parser("activities", help=_("activities on the device and in its backups"))
    g = s.add_mutually_exclusive_group()
    g.add_argument("--limit", type=_at_least_one, default=PAGE, metavar="N",
                   help=_("show the newest N (default: {count})").format(count=PAGE))
    g.add_argument("--all", action="store_true", help=_("show every activity"))
    # The folders as the program finds them (in the Documents folder of the user's XDG
    # folders), with ~ for the home folder.
    backups_help = _("folder of the backups (default: {path})").format(
        path=paths.shown_path(paths.default_backup_dir()))
    s.add_argument("--backups", metavar="DIR", help=backups_help)
    asub = s.add_subparsers(dest="activities_cmd")
    m = asub.add_parser("export", help=_("write activities as GPX files"))
    m.add_argument("names", nargs="+", metavar="NAME",
                   help=_("the file name of an activity, as the list of activities shows it"))
    m.add_argument("--dest", metavar="DIR",
                   help=_("folder for the GPX files (default: {path})").format(
                       path=paths.shown_path(paths.default_gpx_dir())))
    m.add_argument("--backups", dest="export_backups", metavar="DIR", help=backups_help)
    m.add_argument("--apply", action="store_true", help=_("write the GPX files"))
    s.set_defaults(func=cmd_activities)

    s = sub.add_parser("fit", help=_("FIT files on this computer"))
    fsub = s.add_subparsers(dest="fit_cmd", metavar="inspect", required=True)
    m = fsub.add_parser("inspect", help=_("what a FIT file holds: its type, its messages and "
                                          "whether it is whole"))
    m.add_argument("file", metavar="FILE", help=_("a FIT file on this computer"))
    s.set_defaults(func=cmd_fit)

    s = sub.add_parser("diagnostics",
                       help=_("the device's error logs and their copy on this computer"))
    s.add_argument("--apply", action="store_true",
                   help=_("copy the diagnostic files the device shows now"))
    s.add_argument("--crash-logs", action="store_true",
                   help=_("also read the hidden crash logs (the device leaves the file "
                          "manager for a moment)"))
    s.add_argument("--dumps", action="store_true", help=_("also copy the memory dumps"))
    s.add_argument("--free-space", action="store_true",
                   help=_("also free up space on the device: remove the older memory dumps "
                          "(copied to this computer first where they fit) and the files of "
                          "updates it did not install"))
    s.set_defaults(func=cmd_diagnostics, _parser=s)

    s = sub.add_parser("login", help=_("sign in to Garmin Connect"))
    s.add_argument("--manual", action="store_true", help=_("sign in using a regular browser"))
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

    s = sub.add_parser("status", help=_("the sign-in, the devices connected, their last sync and "
                                        "updates, from what this installation knows"))
    s.add_argument("--online", action="store_true",
                   help=_("also ask Garmin Connect and Garmin's update server now"))
    s.set_defaults(func=cmd_status)

    # TRANSLATORS: CLI help of the whoami command, which shows the signed-in Garmin Connect account
    s = sub.add_parser("whoami", help=_("signed-in account"))
    s.add_argument("--check", action="store_true", help=_("verify the token with the server"))
    s.set_defaults(func=cmd_whoami)

    s = sub.add_parser("upload-config",
                       help=_("upload configuration from the Garmin server (OMT)"))
    s.add_argument("--json", action="store_true",
                   help=_("print the server's whole answer as JSON"))
    s.set_defaults(func=cmd_upload_config)

    s = sub.add_parser("sync", help=_("sync with Garmin Connect (dry run by default)"))
    s.add_argument("--apply", action="store_true", help=_("actually upload and write"))
    s.add_argument("--all", action="store_true", help=_("upload everything on the first sync"))
    s.add_argument("--upload-only", action="store_true",
                   help=_("only upload; leave what waits in Garmin Connect for the device"))
    s.add_argument("--download-only", action="store_true",
                   help=_("only write what waits in Garmin Connect; upload nothing"))
    s.add_argument("--type", action="append", help=_("only the given data type (repeatable)"))
    s.add_argument("--include-diagnostics", action="store_true",
                   help=_("also upload device diagnostics and telemetry to Garmin"))
    s.set_defaults(func=cmd_sync)

    s = sub.add_parser("baseline", help=_("mark current files as already uploaded"))
    s.add_argument("--apply", action="store_true", help=_("mark the files (nothing is uploaded)"))
    s.set_defaults(func=cmd_baseline)

    s = sub.add_parser("backup", help=_("local backup of device data"))
    s.add_argument("--dest", metavar="DIR",
                   help=_("destination directory (default: {path})").format(
                       path=paths.shown_path(paths.default_backup_dir())))
    s.add_argument("--keep", type=int, default=3, metavar="N",
                   help=_("how many most recent backups to keep"))
    s.add_argument("--apply", action="store_true",
                   help=_("copy the files (and remove backups beyond --keep)"))
    s.set_defaults(func=cmd_backup)

    s = sub.add_parser("updates", help=_("device software updates"))
    s.add_argument("--reinstalls", action="store_true",
                   help=_("also show items available for reinstall"))
    s.add_argument("--install", nargs="+", metavar="PART",
                   help=_("part numbers to install, or “all”"))
    s.add_argument("--apply", action="store_true",
                   help=_("download the updates chosen with --install and copy them to the "
                          "device, accepting their licenses"))
    s.set_defaults(func=cmd_updates)

    s = sub.add_parser("maps", help=_("maps on the device (TopoActive, base maps, CourseView…)"))
    s.add_argument("--all", action="store_true", help=_("also show maps available for download"))
    msub = s.add_subparsers(dest="maps_cmd")
    m = msub.add_parser("install", help=_("install a map"))
    m.add_argument("name", metavar="NAME",
                   help=_("the map's name, or a part of it that only one map has, as maps --all "
                          "lists it"))
    m.add_argument("--apply", action="store_true",
                   help=_("download the map and write it to the device"))
    m = msub.add_parser("update", help=_("update installed maps"))
    m.add_argument("--apply", action="store_true",
                   help=_("download the updates and write them to the device"))
    m = msub.add_parser("remove", help=_("remove a map from the device"))
    m.add_argument("name", metavar="NAME",
                   help=_("the map's name, or a part of it that only one map has; with --own the "
                          "exact file name of your own map"))
    m.add_argument("--own", action="store_true",
                   help=_("remove a map of your own that was copied with “maps add”"))
    m.add_argument("--apply", action="store_true", help=_("remove the map from the device"))
    m = msub.add_parser("add", help=_("copy a map of your own (.img) to the device"))
    m.add_argument("file", metavar="FILE", help=_("a Garmin map file (.img) on this computer"))
    m.add_argument("--name", help=_("the file name on the device (default: that of FILE)"))
    m.add_argument("--apply", action="store_true", help=_("copy the map to the device"))
    s.set_defaults(func=cmd_maps)

    s = sub.add_parser("primary",
                       help=_("show the primary training device of the Garmin account"))
    wearable_help = _("the primary wearable (steps and sleep in Garmin Connect) instead of the "
                      "primary training device")
    s.add_argument("--wearable", action="store_true", help=wearable_help)
    psub = s.add_subparsers(dest="primary_cmd")
    m = psub.add_parser("set", help=_("make the connected device the primary training device"))
    m.add_argument("--wearable", dest="set_wearable", action="store_true", help=wearable_help)
    m.add_argument("--apply", action="store_true", help=_("change it in your Garmin account"))
    s.set_defaults(func=cmd_primary)

    s = sub.add_parser("courses", help=_("the courses on the device, the GPX files waiting for "
                                         "it and its saved locations (experimental)"))
    csub = s.add_subparsers(dest="courses_cmd")
    m = csub.add_parser("add", help=_("copy a GPX file to the device, which makes courses of "
                                      "its tracks and routes (experimental)"))
    m.add_argument("file", metavar="FILE", help=_("a GPX file on this computer"))
    m.add_argument("--name", metavar="COURSE",
                   help=_("the course's name (default: that of its track or route, else that "
                          "of FILE)"))
    m.add_argument("--apply", action="store_true", help=_("copy the file to the device"))
    m = csub.add_parser("remove", help=_("remove a course or a GPX file waiting for the device "
                                         "(experimental)"))
    m.add_argument("name", metavar="NAME",
                   help=_("the name of the course or of its file, or a part of it that only "
                          "one has"))
    m.add_argument("--apply", action="store_true", help=_("remove it from the device"))
    s.set_defaults(func=cmd_courses)

    register_help = _("add the connected device to your Garmin account (experimental; a "
                      "dry run without --apply)")
    s = sub.add_parser("register", help=register_help, description=register_help)
    s.add_argument("--apply", action="store_true",
                   help=_("add it: changes your Garmin account"))
    s.set_defaults(func=cmd_register)

    s = sub.add_parser("apps", help=_("Connect IQ apps, watch faces and data fields"))
    s.add_argument("--builtin", action="store_true", help=_("also list built-in activities"))
    asub = s.add_subparsers(dest="apps_cmd")
    change_help = _("write the change to the device (it takes effect once unplugged)")
    apply_help = {
        "install": _("install the app, or queue it in Garmin Connect where USB cannot"),
        "remove": _("remove it from the device (with --forget: also from the app list of "
                    "your Garmin account)"),
        "enable": change_help,
        "disable": change_help,
    }
    for name, helptext in (("install", _("install an app from your Garmin account")),
                           ("remove", _("remove an app from the device")),
                           ("enable", _("enable an app or built-in activity")),
                           ("disable", _("disable an app or built-in activity"))):
        a = asub.add_parser(name, help=helptext)
        a.add_argument("name", metavar="NAME", help=_("app name (or part of it) or app id"))
        a.add_argument("--apply", action="store_true", help=apply_help[name])
        if name == "install":
            a.add_argument("--allow-permissions", action="store_true",
                           help=_("also allow the new permissions an app asks for"))
        if name == "remove":
            a.add_argument("--forget", action="store_true",
                           help=_("also remove it from the app list in your Garmin account"))
        if name in ("enable", "disable"):
            # TYPE as docs/cli.md names it; its values, which are not translated, after the help.
            types = sorted(APP_TYPE_OPTIONS)
            a.add_argument("--type", choices=types, metavar="TYPE",
                           help=_("which entry, when an app and a widget share a name")
                           + f" ({', '.join(types)})")
    a = asub.add_parser("update", help=_("update installed apps"))
    a.add_argument("--apply", action="store_true",
                   help=_("install the updates, or queue them in Garmin Connect where USB cannot"))
    a.add_argument("--allow-permissions", action="store_true",
                   help=_("also allow the new permissions an app asks for"))
    a = asub.add_parser("order", help=_("put the apps back in the order of a saved app list"))
    a.add_argument("--from", dest="source", metavar="COPY",
                   help=_("a copy of the app list: part of its folder name, or a path"))
    a.add_argument("--all", action="store_true",
                   help=_("list every position that would change"))
    a.add_argument("--apply", action="store_true",
                   help=_("write the order of the copy to the device (it takes effect once "
                          "unplugged)"))
    s.set_defaults(func=cmd_apps)

    s = sub.add_parser("music", help=_("music, podcasts and audiobooks on the device"))
    msub = s.add_subparsers(dest="music_cmd")
    m = msub.add_parser("add", help=_("copy audio files or folders to the device"))
    m.add_argument("paths", nargs="+", metavar="PATH",
                   help=_("audio files or folders on this computer"))
    m.add_argument("--as", dest="kind", choices=["track", "podcast", "audiobook"],
                   help=_("treat all files as this kind (default: detect from genre tag)"))
    m.add_argument("--playlist", metavar="NAME", help=_("also create a playlist with these files"))
    m.add_argument("--apply", action="store_true",
                   help=_("copy the files to the device (and write the playlist with --playlist)"))
    m = msub.add_parser("playlist", help=_("copy a local .m3u/.m3u8 playlist and its tracks"))
    m.add_argument("m3u", metavar="FILE", help=_("a .m3u or .m3u8 playlist on this computer"))
    m.add_argument("--name",
                   help=_("the playlist's name on the device (default: its name without "
                          "extension)"))
    m.add_argument("--apply", action="store_true",
                   help=_("copy the tracks and write the playlist to the device"))
    m = msub.add_parser("remove", help=_("remove files or playlists whose name, artist or "
                                         "album folder contains TEXT"))
    m.add_argument("paths", nargs="+", metavar="TEXT",
                   help=_("text in a file name, an artist or album folder, or a whole path on "
                          "the device (at least {count} characters)").format(
                       count=MIN_MUSIC_PATTERN))
    m.add_argument("--apply", action="store_true",
                   help=_("remove the files and playlists from the device"))
    s.set_defaults(func=cmd_music)

    # TRANSLATORS: CLI help of the gui command, which opens the application window
    s = sub.add_parser("gui", help=_("application window"))
    s.set_defaults(func=cmd_gui)

    s = sub.add_parser("watch", help=_("background service: sync when the device is connected"))
    s.set_defaults(func=cmd_watch)

    s = sub.add_parser("install-desktop",
                       help=_("add an application menu entry and a background sync service"))
    s.add_argument("--apply", action="store_true",
                   help=_("write the files (with --remove: delete them)"))
    s.add_argument("--remove", action="store_true",
                   help=_("remove the menu entry, icon and service instead"))
    s.add_argument("--force", action="store_true",
                   help=_("install even if the Flatpak of openExpress is installed"))
    s.set_defaults(func=cmd_install_desktop)
    for name in DEVICE_COMMANDS:
        for parser in _with_its_commands(sub.choices[name]):
            _device_options(parser, after_command=True)
    return p


def _device_options(parser: argparse.ArgumentParser, after_command: bool = False) -> None:
    """Which device a command opens: before the command, and after one that opens a device
    ("openexpress sync --device-uri …", as the message of several devices says it). After it,
    an option left out keeps what was given before the command (no default of its own)."""
    keep = {"default": argparse.SUPPRESS} if after_command else {}
    # A device in mass storage mode is found by itself, like an MTP device.
    parser.add_argument("--path", metavar="DIR",
                        help=_("a folder with a copy of a device's files, instead of a "
                               "connected device"), **keep)
    parser.add_argument("--device-uri", metavar="URI",
                        help=_("MTP device URI, e.g. {example}").format(
                            example="mtp://091e_51b4_…/"), **keep)
    parser.add_argument("--no-wait", action="store_true",
                        help=_("do not wait for a device that was just plugged in and is still "
                               "starting"), **keep)


def _with_its_commands(parser: argparse.ArgumentParser):
    """A command's parser and those of its own commands (maps install …), each once."""
    yield parser
    seen = set()
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for child in action.choices.values():
                if id(child) not in seen:
                    seen.add(id(child))
                    yield from _with_its_commands(child)


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
        return self._stream.write(plain_spaces(text))

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
    if args.cmd == "diagnostics" and args.free_space and (args.dumps or args.crash_logs):
        # TRANSLATORS: --free-space is an action of its own; the options are not translated
        args._parser.error(_("--free-space cannot be given with --dumps or --crash-logs"))
    if args.lang:
        i18n.setup(args.lang)
    if importlib.util.find_spec("gi") is None:
        # Without PyGObject no command can open a device or a window: one line and exit
        # code 1, not the traceback of the first import of GLib (--help and --version above).
        _error(no_pygobject_text())
        return 1
    # Before any process is started (the start line may ask flatpak-spawn) and before GTK:
    # a locale the C library cannot have (a Flatpak runtime without it) becomes C.UTF-8.
    fallback = i18n.ensure_utf8_locale()
    role = args.cmd if args.cmd in ("gui", "watch") else "cli"
    # What a command tells the user itself is not printed again from the log (-v: all of it).
    applog.setup(role, args.verbose, quiet_told=role == "cli" and not args.verbose)
    from openexpress import answers

    answers.use_disk()  # what Garmin's servers said a moment ago, for this command too
    started = time.monotonic()
    log.info("start: %s (%s)", _command_line(args, argv), applog.environment())
    if fallback:
        log.info("%s", fallback)
    applog.enable_crash_log()  # a crash of GTK or WebKit leaves Python's trace in crash.log
    from openexpress.auth import sso

    sso.note_unfinished_sign_in()  # the last run ended with the sign-in window open
    code: int | str = "error"
    try:
        code = _run(args)
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


def _run(args) -> int:
    import requests

    from openexpress.auth.sso import LoginCancelled
    from openexpress.device.gio_storage import MassStorageNotice, UnsupportedDevice
    from openexpress.errors import OrdinaryStop, UserFacingError

    try:
        return args.func(args)
    except (MassStorageNotice, UnsupportedDevice) as e:
        # What the window says about the drive, or about a device openExpress cannot use: a
        # notice, not an error; exit code 1.
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
        # Not an error (another sync running, a choice to make…): the user sees the same
        # line as for an error; the log says what it waits for.
        log.info("stopped: %s", e.log_reason)
        _error(_("Error: {error}").format(error=e))
        return 1
    except (Unauthorized, ClockSkew) as e:
        # Answers of a server that say what to do (log in again, set the clock): before
        # ApiError, which they are.
        log.info("error: %s: %s", type(e).__name__, e)  # shown to the user below
        _error(_("Error: {error}").format(error=e))
        return 1
    except ApiError as e:
        log.info("server error: %s: %s", type(e).__name__, e)
        # TRANSLATORS: CLI error from a Garmin server ({error}: the request's name and what went
        # wrong)
        _error(_("Server error: {error}").format(error=e))
        if args.verbose and e.body:
            print(applog.redact(e.body), file=sys.stderr)  # the server's own words: filtered
        return 1
    except UserFacingError as e:
        # A message for the user (the device not ready, a refusal…): one line, no traceback,
        # whatever its class. An error of our own reading (ProtoError, FitError) is not one:
        # it is unexpected below, with its traceback, as in the window.
        log.info("error: %s: %s", type(e).__name__, e)  # shown to the user below
        _error(_("Error: {error}").format(error=e))
        return 1
    except requests.RequestException as e:
        _error(_("Network error: could not reach the Garmin server ({error})").format(
            error=applog.network_error_text(e)))
        log.info("network error: %s", type(e).__name__)
        log.debug("network error", exc_info=True)
        return 1
    except KeyboardInterrupt:
        # TRANSLATORS: CLI: the command was stopped with Ctrl+C
        _error(_("Interrupted."))
        return 130
    except Exception as e:  # not foreseen: told briefly, the rest in the log
        # Logged with its traceback at INFO: always in the log file, on the terminal only with
        # -v; both filtered. Its text may carry a device address or a path, so the message for
        # the user is filtered as well.
        log.info("unexpected error: %s", type(e).__name__, exc_info=True)
        _error(applog.redact(_("Error: {error}").format(error=str(e) or type(e).__name__)))
        return 1
