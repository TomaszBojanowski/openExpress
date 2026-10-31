"""The device view of the window: the page of the open device ("Device": its software, maps,
apps and music, storage, sync, backup, account, the error logs of the device), its banners
and the pages of its side panel as they are loaded (load_section, _load), and the sync with
Garmin Connect started from it. DevicePage is a mixin of MainWindow (gui/app.py), without
__init__ of its own; Section is one page of the side panel.

Has: the rows of the device page and the state of its updates, maps and last sync.
"""
from __future__ import annotations

import logging
import sys
import threading
import traceback
import types
from datetime import datetime

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402 - imports must follow gi.require_version

from openexpress import (  # noqa: E402
    devicelist,
    diagnostics,
    i18n,
    known_devices,
    last_sync,
    paths,
    service,
    sync,
    tile_facts,
)
from openexpress.connect import ApiError, NotAccountDevice, OwnershipError  # noqa: E402
from openexpress.device.gio_storage import DeviceSummary, FoundDevice, mass_storage_text  # noqa: E402
from openexpress.gui import pictures, start  # noqa: E402
from openexpress.gui.apps_page import note_apps_count  # noqa: E402
from openexpress.gui.music_page import note_music_count  # noqa: E402
from openexpress.gui.navigation import Cause  # noqa: E402
from openexpress.gui.pictures import (  # noqa: E402
    cached_download,
    cached_texture,
    device_picture_path,
    download_picture,
    load_texture,
    picture_from_unit_info,
)
from openexpress.gui.preferences import resolve_account  # noqa: E402
from openexpress.gui.start import kind_icon, show_placeholder  # noqa: E402
from openexpress.gui.tasks import (  # noqa: E402
    forget_on_refresh,
    lets_the_window_close,
    stop_when_closed,
    writes_to_device,
)
from openexpress.gui.updates_page import update_check_failed  # noqa: E402
from openexpress.gui.window_tasks import (  # noqa: E402
    Answer,
    confirm,
    inform,
    log_unexpected,
    refresh_space,
    render_space,
    row_subtitle,
    still_in_window,
)
from openexpress.gui.words import (  # noqa: E402
    checked_text,
    never_synced,
    never_synced_tooltip,
    no_sync_record,
    primary_wearable_text,
    software_text,
    to_upload_text,
    update_state_text,
)
from openexpress.i18n import N_, _, caption, human_size, ngettext, pgettext  # noqa: E402
from openexpress.paths import APP_ID  # noqa: E402

log = logging.getLogger(__name__)


def last_sync_words(data: dict, now: datetime | None = None) -> str:
    """The row under "Last sync": when, told against now ("yesterday, 23:41"), and the
    numbers of that sync (last_sync.describe). Now is the window's clock."""
    return last_sync.describe(data).row(now or datetime.now())


def last_sync_subtitle(unit_id, data: dict | None, to_sync=None,
                       now: datetime | None = None) -> str:
    """The subtitle of "Last sync": "Never synced" when this installation has no state.json
    for the device (the one source of it, as on the tile); else the last sync (`data`,
    last_sync.json) or that none is recorded, then the new activities to upload (`to_sync`,
    the count of the chip "To sync"), unless that sync could not upload (no chip then)."""
    if not unit_id or not sync.synced_here(unit_id):
        return never_synced()
    text = last_sync_words(data, now) if data else no_sync_record()
    count = getattr(to_sync, "count", None)
    if count and (not data or service.not_uploaded(data) is None):
        text = caption(text, to_upload_text(count))
    return text


def open_to_sync(s):
    """The count of activities to upload of the open device for its page: what it counted, or
    until then what the last count found (known_devices.recorded_sync_count), as its tile
    from the first frame; None: not known."""
    counted = getattr(s, "to_sync", None)
    if counted is not None:
        return counted
    unit_id = getattr(s.device, "unit_id", None)
    if not unit_id or not sync.synced_here(unit_id):
        return None
    count = known_devices.recorded_sync_count(known_devices.load().get(unit_id),
                                              service.sync_stamp(unit_id))
    return types.SimpleNamespace(count=count) if count is not None else None


# What a count of the activities to upload found, kept with the device for its tile.
remember_sync_count = tile_facts.note_count


def refresh_last_sync_subtitle(win) -> None:
    """The subtitle of "Last sync" of the device page drawn again where its words changed:
    once a minute (its time told against now), and when the count of activities to upload
    comes or changes."""
    row, s = win.last_sync_row, win.session
    if row is None or s is None or getattr(s, "ownership_error", None) is not None:
        return
    text = GLib.markup_escape_text(last_sync_subtitle(
        s.device.unit_id, win._last_sync_data, open_to_sync(s),
        datetime.now()))
    if row.get_subtitle() != text:
        row.set_subtitle(text)


def count_open_activities(win) -> None:
    """The device page of the open device: its activities not uploaded yet counted once, in
    the background, also when its tile was never drawn (opened at once); under the
    conditions of its tile: logged in, on the account, not a drive that is not synced. The
    count is the window's (_activities_counted)."""
    s = win.session
    if (s is None or getattr(s, "to_sync_asked", True)
            or getattr(s, "drive_refusal", None) is not None or not s.logged_in
            or s.ownership_error is not None):
        return
    s.to_sync_asked = True
    win.in_background(lambda: sync.activities_to_sync(s.device, s.storage),
                      lambda result: win._activities_counted(s, result))


def music_kinds(device) -> list:
    """The kinds of media the device declares (its GarminDevice.xml; nothing is read)."""
    from openexpress import music

    try:
        return music.supported_kinds(device)
    except Exception:  # noqa: BLE001 - a device of a test or without its XML: none known
        return []


def backups_text(unit_id) -> str:
    """The subtitle of "Backup": when the last backup of the device was made and how many
    there are in its folder (only the backups openExpress made, not the copies of the app
    list); none: says so."""
    from openexpress.backup import STAMP, is_backup_dir

    found = []
    folder = paths.default_backup_dir() / str(unit_id) if unit_id else None
    try:
        if folder is not None and folder.is_dir():
            found = sorted(datetime.strptime(p.name, STAMP) for p in folder.iterdir()
                           if not p.is_symlink() and is_backup_dir(p))
    except OSError as e:
        log.info("backup: the folder could not be listed (%s)", type(e).__name__)
    if not found:
        # TRANSLATORS: subtitle of the row "Backup" of a device without one
        return _("No backup yet")
    # TRANSLATORS: subtitle of the row "Backup": when the last backup was made
    return caption(_("Last on {when}").format(when=i18n.full_time(found[-1])),
                   ngettext("{count} backup", "{count} backups", len(found)).format(
                       count=len(found)))


def tell_left_out(win, result) -> None:
    """After a backup: the FIT files the device was still writing, left out of it."""
    from openexpress.backup import incomplete_text

    if result.incomplete:
        inform(win, "backup-incomplete", _("Not everything was backed up"),
               incomplete_text(result.incomplete))


def show_backups(win) -> None:
    """The row "Backup" as the folder of the backups is now; the folder in its tooltip
    (that of all devices: the device's own is named after its unit ID)."""
    row, s = win.backup_row, win.session
    if row is None or s is None:
        return
    row.set_subtitle(GLib.markup_escape_text(backups_text(getattr(s.device, "unit_id", None))))
    row.set_tooltip_text(str(paths.default_backup_dir()))


def device_error_text(entry) -> str:
    """The subtitle of "Device error log": the kind of the error, as the device names it, and
    when it logged it."""
    return caption(entry.kind, i18n.full_time(entry.at)) if entry.at else entry.kind


def device_error_details(entry) -> str:
    """What else the device logged with the error (its number, where, on which screen)."""
    parts = []
    if entry.error:
        # TRANSLATORS: the number of an error the device logged, e.g. "error 0x003"
        parts.append(_("error {number}").format(number=entry.error))
    if entry.program_counter:
        # TRANSLATORS: where in its software the device stopped (a program counter)
        parts.append(_("at {address}").format(address=entry.program_counter))
    if entry.screen:
        # TRANSLATORS: the number of the screen the device showed when the error came
        parts.append(_("screen {number}").format(number=entry.screen))
    return caption(*parts)


def device_error_row(win, group=None) -> None:
    """The row "Device error log" under "Backup": only when the device logged an "RTL Trap"
    in the last 30 days (read when it was opened, the copy on or off).
    Inside, where they apply: Copy Crash Logs (a device that hides its event logs) and Copy
    Memory Dumps. Drawn again in its place when that changes."""
    old = win.device_error_row
    group = group or win.device_error_group
    win.device_error_row, win.device_error_group = None, group
    if old is not None and group is not None:
        group.remove(old)
    s = win.session
    found = getattr(s, "diagnostics", None)
    crash = getattr(found, "crash", None)
    if crash is None or group is None:
        return
    rows = []
    details = device_error_details(crash)
    if details:
        rows.append(Adw.ActionRow(title=_("What the device logged"),
                                  subtitle=GLib.markup_escape_text(details)))
    if found.hidden_logs:
        r = Adw.ActionRow(title=_("Crash logs"), subtitle=_(
            "Hidden from the file manager. While they are copied, the device leaves it for a "
            "moment"))
        r.add_suffix(win._button(_("Copy Crash Logs"),
                                 lambda button: copy_crash_logs(win, button), with_button=True))
        rows.append(r)
    if found.dumps:
        size = sum(n for _p, n in found.dumps)
        r = Adw.ActionRow(title=_("Memory dumps"), subtitle=caption(
            ngettext("{count} dump", "{count} dumps", len(found.dumps)).format(
                count=len(found.dumps)), human_size(size),
            # TRANSLATORS: of the memory dumps of the device: only Garmin can read them
            _("encrypted")))
        r.add_suffix(win._button(_("Copy Memory Dumps"),
                                 lambda button: copy_dumps(win, button), with_button=True))
        rows.append(r)
    subtitle = GLib.markup_escape_text(device_error_text(crash))
    if rows:
        row = Adw.ExpanderRow(title=_("Device error log"), subtitle=subtitle)
        for r in rows:
            row.add_row(r)
    else:
        row = Adw.ActionRow(title=_("Device error log"), subtitle=subtitle)
    group.add(row)
    win.device_error_row = row


def device_name(s) -> str:
    """The name of the open device on its page: the name Garmin gives its model when its
    GarminDevice.xml names it in capitals only ("EPIX"), from the session's unit-info or as
    remembered (known_devices.shown_name); else that name."""
    d = s.device
    answer = getattr(s, "unit_info_answer", None)
    model = known_devices.garmin_name(
        answer.get("devicemodel") if isinstance(answer, dict) else None)
    if model is None and d.unit_id:
        known = known_devices.load().get(d.unit_id)
        model = known.model_name if known is not None else None
    return known_devices.shown_name(d.description, model)


def show_device_name(win, s) -> None:
    """The name on the page of `s` as it is known now, while it is still the open one."""
    title = win.device_title
    if win.session is s and title is not None:
        title.set_label(device_name(s))


def primary_wearable_row(win, wearable):
    """The row "Primary wearable" of the group of the account (devicelist.PrimaryWearable,
    from the account's list the ownership check read), with Set as Primary when this device
    is not the one (the list gives one only for a device that can be it)."""
    # TRANSLATORS: Garmin Connect's name of the setting (in Polish "Podstawowe urządzenie typu
    # wearable")
    row = Adw.ActionRow(title=_("Primary wearable"),
                        subtitle=GLib.markup_escape_text(primary_wearable_text(wearable)))
    if not wearable.is_this_device:
        row.add_suffix(win._button(
            _("Set as Primary"), lambda button: confirm_primary_wearable(win, wearable, button),
            with_button=True))
    return row


def confirm_primary_wearable(win, wearable, button=None) -> None:
    """Set as Primary on the row "Primary wearable": asked first; once Set is chosen, the
    change runs from `button`, which spins meanwhile."""
    s = win.session
    if s is None:
        return
    confirm(win, "primary-wearable", _("Change the primary wearable?"),
            _("Steps and sleep in Garmin Connect come from the primary wearable.\n\n"
              "Current: {old}\nNew: {new}").format(old=primary_wearable_text(wearable),
                                                  new=device_name(s)),
            # TRANSLATORS: button that makes this device the primary training device
            Answer("set", _("Set"), lambda: set_primary_wearable(win, button), look="suggested"),
            default="cancel")


def set_primary_wearable(win, button=None) -> None:
    """The change of the primary wearable on the account (service.set_primary_wearable,
    read back); its list then goes to the tiles too (their chip "Primary")."""
    s = win.session
    if not s or not s.logged_in or s.ownership_error is not None:
        return

    def done(change) -> None:
        win.toast(_("Primary wearable changed"))
        if win.session is s:
            s.account_devices = change.devices
            win._set_account(change.devices)
            win.show_overview()

    win.run_task(_("Changing the primary wearable…"),
                 lambda: service.set_primary_wearable(s.client, s.device, apply=True), done,
                 device=False, button=button)


# Why the picture work found no picture: unit-info gave no address of one.
def ask_to_eject_after_sync(win, s) -> None:
    """After a sync wrote files of Garmin Connect's queue to a drive: it is to be ejected
    first, so that the watch takes them; that the cable can go out is said only once it is
    (eject_device)."""
    confirm(win, "eject-after-sync", _("Files saved on the device"),
            _("Eject the device before you unplug the cable: until then it may not see the "
              "files from Garmin Connect."),
            # TRANSLATORS: button that ejects the device (its USB drive)
            Answer("eject", _("Eject"), lambda: win.eject_device(s, files=True),
                   look="suggested"),
            # TRANSLATORS: button; the device is ejected later
            cancel=_("Later"), default="eject")


NO_ADDRESS = "unit-info gave no address"


def offers_account_picture(s) -> bool:
    """Whether the page may offer Get Picture From Account: a device not on the account
    (the ownership check said so), the user logged in, not a drive."""
    return (s is not None and s.logged_in and isinstance(s.ownership_error, NotAccountDevice)
            and not getattr(s, "mass_storage", False))


def account_picture_row(win, s):
    """The row "Device picture" of the group of the account, with Get Picture From Account:
    shown once the picture work found none (show_account_picture_offer)."""
    row = Adw.ActionRow(
        title=_("Device picture"),
        subtitle=_("Garmin's update server has none. Getting it sends the Unit ID of this "
                   "device to your Garmin Connect account."),
        visible=win.account_picture_offer is s)
    row.add_suffix(win._button(_("Get Picture From Account"),
                               lambda button: get_picture_from_account(win, button),
                               with_button=True))
    win.account_picture_row = row
    return row


def show_account_picture_offer(win, s) -> None:
    """Offer Get Picture From Account for `s`, the open device (its row, when it is built)."""
    win.account_picture_offer = s
    row = win.account_picture_row
    if row is not None:
        row.set_visible(True)


def _no_account_picture_offer(win) -> None:
    win.account_picture_offer = None
    row = win.account_picture_row
    if row is not None:
        row.set_visible(False)


def get_picture_from_account(win, button=None) -> None:
    """Get Picture From Account: the one request that names this device, which is not on the
    account, to the user's Garmin Connect account (device-info), on this click only. The
    picture goes into the cache as any other, its address is kept with the device, and the
    button is not offered again once the account has answered."""
    s = win.session
    if s is None:
        return
    part = s.device.part_number

    def work():
        url = s.client.device_image_url_off_account(s.device.unit_id)
        if not url:
            return None, "the account gave no address"
        remember_picture_address(s.device, url)
        path, why = download_picture(s.client.http, url, device_picture_path(part).name)
        return (path, "") if path else (None, f"download refused: {why}")

    def done(result):
        path, why = result
        texture = load_texture(path) if path else None
        _no_account_picture_offer(win)
        if texture is None:
            log.info("device picture: none from the account, asked on request (%s)",
                     why or "the file is not a picture")
            win.toast(_("Your Garmin Connect account has no picture of this device"))
            return
        log.info("device picture: from the account, asked on request")
        pictures.keep(win, part, texture)
        start_page = win.start
        if start_page is not None:
            start_page.set_picture(part, texture)  # its tile on the start page as well
        if win.session is s:
            win._set_device_texture(texture)

    def failed(e) -> bool:
        if not isinstance(e, ApiError):
            return False  # anything else: said as by every task
        # The server's own words are no help here; the button stays for a later try.
        log.info("device picture: the account refused, asked on request (HTTP %s)",
                 e.status if e.status is not None else "none")
        win.error(_("Device picture"), account_picture_refused_text(e.status))
        return True

    win.run_task(_("Getting the picture…"), work, done, failed, device=False, button=button)


def account_picture_refused_text(status: int | None) -> str:
    """Why Get Picture From Account got no answer, in short: HTTP 404, the account does not
    know the device (an epix not on it); anything else, try later."""
    if status == 404:
        return _("Garmin Connect does not know this device, so it has no picture of it.")
    return _("Garmin Connect could not be asked for the picture. Try again later.")


def read_diagnostics(win) -> None:
    """Reads in the background what the open device logged as errors, and copies its
    diagnostic files when the copy is on (diagnostics.at_open: once per connection, never a
    session of our own); leaves them in `session.diagnostics` and draws "Device error log".
    `session.diagnostics_reading` is set once done: tasks that write to the device wait for
    it (window_tasks.wait_for_the_diagnostics)."""
    s = win.session
    if (s is None or getattr(s, "diagnostics", None) is not None
            or getattr(s, "diagnostics_reading", None) is not None):
        return
    reading = s.diagnostics_reading = threading.Event()

    def work():
        try:
            return diagnostics.at_open(s.device, s.storage)
        finally:
            reading.set()  # also when it failed: the tasks go on

    def done(found) -> None:
        s.diagnostics = found
        if win.session is s:
            device_error_row(win)

    # Once the page is shown, not in the opening: on a watch it took 3.7-4.4 s of the
    # 4.5-5 s of an opening.
    win.in_background(work, done)


def copy_crash_logs(win, button=None) -> None:
    """Copy Crash Logs: one read-only session of our own (the device leaves the file manager
    for a moment); none found: the button is not offered again."""
    s = win.session
    if s is None:
        return

    def work():
        return diagnostics.copy_crash_logs(s.device, s.storage, progress=win.progress)

    def done(copied):
        if win.session is not s:
            return
        if copied.saved:
            win.toast(_("Crash logs copied to this computer"))
            return
        win.toast(_("The device has no hidden crash logs"))
        if s.diagnostics is not None:
            s.diagnostics.hidden_logs = False
        device_error_row(win)

    win.run_task(_("Copying the crash logs…"), work, done, button=button)


def copy_dumps(win, button=None) -> None:
    """Copy Memory Dumps: through the ordinary connection, the newest first while they fit
    within the limit of the copies."""
    s = win.session
    if s is None:
        return

    def work():
        return diagnostics.copy_dumps(s.device, s.storage, progress=win.progress)

    def done(copied):
        if win.session is not s:
            return
        copied_count = copied.new + copied.unchanged
        text = ngettext("{count} memory dump copied to this computer",
                        "{count} memory dumps copied to this computer",
                        copied_count).format(count=copied_count)
        if copied.not_copied:
            text = caption(text, ngettext(
                "{count} not: over the limit of {size}", "{count} not: over the limit of {size}",
                len(copied.not_copied)).format(count=len(copied.not_copied),
                                               size=human_size(diagnostics.LIMIT)))
        win.toast(text)

    win.run_task(_("Copying the memory dumps…"), work, done, button=button)


def primary_training_unknown(s) -> str:
    """The subtitle of "Primary training device" before (or instead of) asking the account:
    "Log in to see" while not logged in, "Not available" for a device off the account (why,
    the banner says), "Could not check" when its check against the account failed;
    "Checking…" while it is asked."""
    if not s.logged_in:
        # TRANSLATORS: subtitle of "Primary training device" while not logged in
        return _("Log in to see")
    if isinstance(s.ownership_error, NotAccountDevice):
        # TRANSLATORS: subtitle of "Primary training device" of a device off the account
        return pgettext("primary training device", "Not available")
    if s.ownership_error is not None or s.account_devices is None:
        return _("Could not check")
    return _("Checking…")


DEVICE_PICTURE_SIZE = 180


# The pages that do not apply to a Garmin device in mass storage mode.
NOT_FOR_MASS_STORAGE = ("maps", "apps", "music")


def picture_address_known(device) -> bool:
    """Whether the address of the picture of this device is kept with it already; True also
    for a device that is not remembered (there is nothing to keep it with)."""
    unit_id = getattr(device, "unit_id", None)
    entry = known_devices.load().get(unit_id) if unit_id else None
    return entry is None or entry.image_url is not None


def kind_untold(device) -> bool:
    """Whether Garmin has told no kind of this device yet: none kept with it, and unit-info
    has not answered about its model in this run."""
    unit_id = getattr(device, "unit_id", None)
    entry = known_devices.load().get(unit_id) if unit_id else None
    if entry is not None and entry.category is not None:
        return False
    return not devicelist.unit_info_heard(getattr(device, "part_number", "") or "")


def remember_picture_address(device, url: str | None) -> None:
    """Keep the address of the picture of the open device with it (known_devices), for when
    its picture is no longer in the cache and it is not connected; only an address that may
    be downloaded. Optional: a failure is only written to the log."""
    unit_id = getattr(device, "unit_id", None)
    if not unit_id or not url:
        return
    try:
        known_devices.set_image_url(unit_id, url)
    except Exception as e:  # noqa: BLE001 - optional; the error may name the folder
        log.warning("devices: the address of a picture not kept (%s)", type(e).__name__)


# What the window's check of updates found, kept with the open device for its tile and
# its page when it is not connected.
remember_update_found = tile_facts.note_update_check


def show_update_record(win) -> bool:
    """Sets the state of "Device software" and its chip from the last check of updates
    recorded for the open device (devices.json), if recent and for its software now: "ready"
    (an update copied; `update_count` still to install) or "available"; returns whether there
    was one. Only shown: the Updates page, its buttons and the installs use only the server's
    answer (updates_cache)."""
    device = getattr(win.session, "device", None)
    unit_id = getattr(device, "unit_id", None)
    if not unit_id:
        return False
    entry = known_devices.load().get(unit_id)
    software = getattr(device, "software_version_str", None)
    found = known_devices.recorded_updates(entry, software)
    check = known_devices.recorded_check(entry, software)
    # Until the check now running ends: it confirms or changes this; a failed one leaves it
    # and says when it was (update_checked_at).
    if check is not None and check.ready:
        win.update_state, win.update_count, win.update_only = "ready", 0, None
        win._update_record_at = check.at
        if found is not None:
            win.update_count = found.count
        return True
    if found is None:
        return False
    win.update_state, win.update_count = "available", found.count
    win.update_only = found if found.count == 1 and found.title is not None else None
    win._update_record_at = entry.update_found
    return True


def open_device_kind(s, account) -> str | None:
    """The kind of the open device as its tile knows it: the same devicelist.tiles from
    devices.json and the account's list (also for the one device connected, opened at once
    with no tile shown before). Nothing is asked."""
    d = s.device
    unit_id = getattr(d, "unit_id", None) or None
    if unit_id is None:
        return None
    kind = "ums" if getattr(s, "mass_storage", False) else "mtp"
    summary = DeviceSummary(FoundDevice("", "", True, kind=kind), getattr(d, "description", ""),
                            getattr(d, "part_number", ""), "", unit_id)
    tiles = devicelist.tiles([summary], known_devices.load(), account,
                             logged_in=account is not None)
    return next((devicelist.icon_kind(t) for t in tiles if t.connected), None)


def device_view_icon(win, s) -> str:
    """The icon of the device view before the picture: that of the device's kind, from the
    window's account list (or the one its ownership check read) and devices.json."""
    account = win._account
    if account is None:
        account = getattr(s, "account_devices", None)
    return kind_icon(open_device_kind(s, account))


def show_not_for_mass_storage(sec) -> None:
    """A page that does not apply to a Garmin device in mass storage mode: it loads nothing
    and says why, as information (the page's icon, no Try Again), not as an error."""
    sec.clear()
    sec.info.set_icon_name(sec.icon)
    sec.info.set_title(sec.title)
    # The description is markup: words with "<" or "&" would leave it empty.
    sec.info.set_description(GLib.markup_escape_text(mass_storage_text()))
    sec.show("info")


class Section:
    """One page of the window: shows a placeholder, a spinner, an error, a piece of
    information (what the device has not got) or the content."""

    def __init__(self, key: str, title: str, icon: str, loading_text: str):
        self.key, self.title, self.icon = key, title, icon
        self.loaded = False
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.empty = Adw.StatusPage(
            icon_name=APP_ID, title=_("Connect your device"),
            description=_("Connect your Garmin device with a USB cable. "
                          "It will be detected automatically."))
        self._empty_notice = None
        self.loading = Adw.StatusPage(title=loading_text)
        if hasattr(Adw, "SpinnerPaintable"):
            self.loading.set_paintable(Adw.SpinnerPaintable.new(self.loading))
        self.error = Adw.StatusPage(icon_name="dialog-warning-symbolic")
        self.retry = Gtk.Button(label=_("Try again"), halign=Gtk.Align.CENTER)
        self.retry.add_css_class("pill")
        self.error.set_child(self.retry)
        self.info = Adw.StatusPage()
        self.page = Adw.PreferencesPage()
        for name, widget in (("empty", self.empty), ("loading", self.loading),
                             ("error", self.error), ("info", self.info), ("content", self.page)):
            self.stack.add_named(widget, name)
        # Until the first detection finishes we don't know yet whether a watch is connected.
        self.stack.set_visible_child_name("loading")
        self.groups: list[Adw.PreferencesGroup] = []

    def set_empty(self, title: str, description: str, busy: bool) -> None:
        """What the placeholder says while no device is open."""
        if self._empty_notice == (title, description, busy):
            return
        self._empty_notice = (title, description, busy)
        self.empty.set_title(title)
        self.empty.set_description(description)
        if busy and hasattr(Adw, "SpinnerPaintable"):
            self.empty.set_paintable(Adw.SpinnerPaintable.new(self.empty))
        else:
            self.empty.set_paintable(None)
            self.empty.set_icon_name(APP_ID)

    def show(self, name: str) -> None:
        self.stack.set_visible_child_name(name)

    def show_error(self, text: str) -> None:
        self.error.set_title(_("Something went wrong"))
        self.error.set_description(GLib.markup_escape_text(text))
        self.show("error")

    def clear(self) -> None:
        for g in self.groups:
            self.page.remove(g)
        self.groups = []

    def group(self, title: str = "", description: str = "") -> Adw.PreferencesGroup:
        g = Adw.PreferencesGroup(title=title, description=description)
        self.page.add(g)
        self.groups.append(g)
        return g


# A page whose drawing failed (an error of the program): under "Something went wrong", with
# Try again.
PAGE_NOT_SHOWN = N_("This page could not be shown. Try again; if it happens again, report the "
                    "problem with the log attached.")


class DevicePage:
    # --- the device view ---------------------------------------------------------

    def _clear_device_pages(self) -> None:
        """Every page of the device view loaded anew: nothing kept from the device before."""
        self.maps_cache = self.updates_cache = None
        self.map_cleanup = []
        self.update_state = None
        self.update_checked_at = self._update_record_at = None
        self.apps_count = self.music_count = None  # read again by their pages
        self.map_state = None
        for sec in self.sections.values():
            sec.loaded = False
            sec.clear()
            sec.show("empty" if self.session is None else "loading")
        self._update_banner()
        self._update_actions()

    def show_no_device(self) -> None:
        """No device open (unplugged, or none found): the pages are empty and tell the state
        on USB, the start page is shown (_show_start decides whether to go there)."""
        self._clear_device_pages()
        # No check waits for the device that is gone (nor keeps it in memory).
        self._map_check_session = self._update_check_session = None
        # The empty pages tell the state on USB now, not the one from before the device
        # was opened (e.g. "starting" when the window opened with a starting device).
        self._poll_usb()
        self.connected.clear()
        resolve_account(self)
        self._show_start()

    def show_device(self) -> None:
        """The device open now (self.session) in every page, and its view
        (_show_device_view); without one, show_no_device."""
        if self.session is None:
            self.show_no_device()
            return
        self._clear_device_pages()
        if self.navigation.get_visible_page() is not self.device_view:
            self._front_device_page()  # opened, or opened again: on its device page
        page = self.pages.get_visible_child_name()
        # The Maps or Updates page asks right away, and its answer fills the row of the device
        # page as well (show_maps, show_updates): the row does not ask a second time.
        if page == "maps":
            self._map_check_session = self.session
        elif page == "updates":
            self._update_check_session = self.session
        self.show_overview()
        self.load_section(page)
        # Else the one device: its tile comes from the session.
        self.connected.keep_if_open(lambda c: self._is_open(c))
        resolve_account(self)
        self._show_device_view()
        read_diagnostics(self)

    def _update_banner(self) -> None:
        s = self.session
        if s is not None and getattr(s, "drive_refusal", None) is not None:
            # A drive openExpress does not sync needs neither the setup nor the account.
            self.banner.set_revealed(False)
        elif s is not None and s.client.consumer is None:
            self.banner.set_title(_("One-time setup: choose the Garmin Express installer for "
                                    "Windows (GarminExpress.exe from garmin.com). openExpress "
                                    "only reads it."))
            self.banner.set_button_label(_("Choose Installer…"))
            self._banner_kind = "setup"
            self.banner.set_revealed(True)
        elif s is not None and not s.logged_in:
            self.banner.set_title(_("Not logged in to Garmin Connect"))
            self.banner.set_button_label(_("Log In"))
            self._banner_kind = "login"
            self.banner.set_revealed(True)
        elif s is not None and s.ownership_error is not None:
            unknown = not isinstance(s.ownership_error, NotAccountDevice)
            self.banner.set_title(self._ownership_title(s.ownership_error))
            self.banner.set_button_label(_("Try Again") if unknown else None)
            self._banner_kind = "recheck"
            self.banner.set_revealed(True)
        else:
            self.banner.set_revealed(False)

    def _banner_action(self) -> None:
        if self.busy:
            self.toast(_("Another operation is in progress"))
            return
        kind = self._banner_kind
        if kind == "setup":
            self.choose_dll()
        elif kind == "recheck":
            forget_on_refresh(self.session)
            self.refresh_device(cause=Cause.REFRESH_IN_VIEW)
        else:
            self.login()

    def load_section(self, key: str, force: bool = False) -> None:
        sec = self.sections[key]
        if self.session is None or (sec.loaded and not force) or key == "watch":
            if key == "watch" and self.session is not None and force:
                self.refresh_device(cause=Cause.REFRESH_IN_VIEW)
            return
        if getattr(self.session, "mass_storage", False) and key in NOT_FOR_MASS_STORAGE:
            show_not_for_mass_storage(sec)  # no request, nothing read
            return
        loader = {"updates": self.load_updates, "maps": self.load_maps,
                  "apps": self.load_apps, "music": self.load_music}[key]
        loader()

    def _load(self, key: str, title: str, work, show, device: bool = True,
              on_error=None) -> None:
        """Common pattern: spinner in the section, work in a thread, then `show(result)`.
        `on_error(e)` when the work failed for the device that is still open."""
        sec = self.sections[key]
        sess = self.session
        source = self._retry_sources.pop(key, None)
        if source is not None:
            GLib.source_remove(source)
        if sess is None:
            return
        sec.show("loading")

        def done(result):
            if self.session is not sess:
                return  # the watch was disconnected or replaced meanwhile
            sec.loaded = True
            sec.clear()
            # Without the buttons of the page that was just cleared.
            self._action_buttons = still_in_window(self._action_buttons)
            try:
                show(result)
            except Exception:  # noqa: BLE001 - said on the page, written to the log
                # Not "loading" for good: the page says so, and is read again when it is
                # chosen again (or with Try again).
                log_unexpected(f"showing the page {key}", sys.exc_info())
                traceback.print_exc()  # to stderr too
                sec.loaded = False
                sec.clear()
                self._action_buttons = still_in_window(self._action_buttons)
                sec.show_error(_(PAGE_NOT_SHOWN))
                return
            sec.loaded = True
            sec.show("content")

        def failed(e):
            if self.session is sess:
                sec.show_error(str(e) or e.__class__.__name__)
                if on_error is not None:
                    on_error(e)
            return True

        if self.busy:
            # Another task runs; try again shortly so the page does not stay empty.
            def retry():
                self._retry_sources.pop(key, None)
                if self.session is sess:
                    self._load(key, title, work, show, device, on_error)
                return False

            self._retry_sources[key] = GLib.timeout_add(700, retry)
            return
        self.run_task(title, work, done, failed, device=device, in_page=True)

    # --- overview ---------------------------------------------------------------------

    def show_overview(self) -> None:
        s = self.session
        sec = self.sections["watch"]
        sec.clear()
        d = s.device

        hero = sec.group()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, halign=Gtk.Align.CENTER,
                      margin_bottom=12)
        # Until its picture comes: the icon its tile shows, not the program's.
        self.device_image = Gtk.Image()
        show_placeholder(self.device_image, device_view_icon(self, s), DEVICE_PICTURE_SIZE)
        self.device_picture = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN, can_shrink=True,
                                          visible=False)
        self.device_picture.set_size_request(DEVICE_PICTURE_SIZE, DEVICE_PICTURE_SIZE)
        box.append(self.device_image)
        box.append(self.device_picture)
        name = device_name(s)
        title = Gtk.Label(label=name, wrap=True, justify=Gtk.Justification.CENTER)
        title.add_css_class("title-1")
        box.append(title)
        self.device_title = title
        if d.description.isupper() and name == d.description:
            # Named in capitals only, Garmin's name of it not known yet: unit-info has it
            # (asked once for the session, as for the picture and the maps).
            self.in_background(lambda: service.unit_info(s),
                               lambda _answer: show_device_name(self, s))
        # It wraps, as the name above it: the window gets as narrow as MIN_WINDOW.
        self.software_label = Gtk.Label(
            label=_("Software {version}").format(version=d.software_version_str), wrap=True,
            justify=Gtk.Justification.CENTER)
        self.software_label.add_css_class("dim-label")
        box.append(self.software_label)
        # An update to install: the chip of the device's tile, under its version
        # (_highlight_update).
        self.update_chip = Gtk.Label(label=start.update_available(), halign=Gtk.Align.CENTER,
                                     css_classes=["oe-chip", "warning"], visible=False)
        box.append(self.update_chip)
        hero.add(box)
        self._show_device_picture()

        # The space as the session holds it (read with the device, or after a task that
        # wrote to it): the page asks the device nothing.
        # TRANSLATORS: group on the device page: the device's own storage
        g = sec.group(_("Storage"))
        self.storage_row = Adw.ActionRow(title=_("Device storage"))
        self.storage_bar = Gtk.LevelBar(min_value=0, max_value=1, valign=Gtk.Align.CENTER,
                                        width_request=120, visible=False)
        self.storage_bar.add_offset_value("low", 0.75)
        self.storage_bar.add_offset_value("high", 0.9)
        self.storage_bar.add_offset_value("full", 1.0)
        self.storage_row.add_suffix(self.storage_bar)
        g.add(self.storage_row)
        render_space(self)
        if getattr(s, "space", None) is None:
            refresh_space(self, s)  # not read with the device: now, in the background

        # A Garmin device in mass storage mode has no maps; one that is not synced says why
        # in place of the sync.
        mass_storage = getattr(s, "mass_storage", False)
        refused = getattr(s, "drive_refusal", None) if mass_storage else None
        if refused is not None:
            sec.group(_("USB drive"), service.drive_sync_refusal_text(*refused))
        else:
            # TRANSLATORS: under "Sync with Garmin Connect" on the page of a device connected
            # as a USB drive, which has no row of the background sync
            g = sec.group(_("Sync with Garmin Connect"), _(
                "A device connected as a USB drive is synced with the Sync button only, never "
                "in the background.") if mass_storage else "")
            if s.ownership_error is not None:
                # Why, the banner says; not here again.
                # TRANSLATORS: subtitle of "Last sync" of a device that cannot be synced
                g.add(Adw.ActionRow(title=_("Last sync"), subtitle=pgettext("sync",
                                                                            "Not available")))
            else:
                self.last_sync_row = Adw.ExpanderRow(title=_("Last sync"))
                self._last_sync_children = []
                g.add(self.last_sync_row)
                self._show_last_sync()
                count_open_activities(self)  # how many to upload, also without the tiles
            if s.ownership_error is None and not mass_storage:
                g.add(self._auto_sync_row())  # the background service never syncs a drive

        g = sec.group(pgettext("tab", "Updates"))
        self.update_row = Adw.ActionRow(title=_("Device software"), activatable=True)
        self.update_row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
        self.update_row.connect("activated", lambda *_a: self.select_section("updates"))
        g.add(self.update_row)
        # What the last check found, from the first frame (nothing moves when the check
        # confirms it); the check runs all the same.
        recorded = self.update_state is None and show_update_record(self)
        self._render_update_state()
        if self.update_state is None or recorded:
            self._check_updates_quietly()
        if not mass_storage:
            self.map_row = Adw.ActionRow(title=pgettext("tab", "Maps"), activatable=True)
            self.map_row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
            self.map_row.connect("activated", lambda *_a: self.select_section("maps"))
            g.add(self.map_row)
            self._render_map_state()
            if self.map_state is None:
                self._check_maps_quietly()

        if not mass_storage:
            # As "Maps": to their pages; how many only once those pages have read them
            # (note_apps_count, note_music_count), never a read of their own.
            # TRANSLATORS: group on the device page with the rows "Apps" and "Music"
            g = sec.group(_("Apps and music"))
            self.apps_row = Adw.ActionRow(title=pgettext("tab", "Apps"), activatable=True)
            self.apps_row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
            self.apps_row.connect("activated", lambda *_a: self.select_section("apps"))
            g.add(self.apps_row)
            note_apps_count(self, self.apps_count)
            self.music_row = None
            if music_kinds(d):
                self.music_row = Adw.ActionRow(title=pgettext("tab", "Music"),
                                               activatable=True)
                self.music_row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
                self.music_row.connect("activated", lambda *_a: self.select_section("music"))
                g.add(self.music_row)
                count = self.music_count
                note_music_count(self, *(count or (None, None)))

        if s.logged_in or not mass_storage:
            g = sec.group(_("Garmin Connect account"))
        if s.logged_in:
            row = Adw.ActionRow(title=GLib.markup_escape_text(s.client.auth.display))
            row.add_suffix(self._button(_("Log out"), self.logout))
            g.add(row)
        if not mass_storage:
            # The row stays when it cannot be read: why, in short.
            self.primary_row = Adw.ActionRow(title=_("Primary training device"),
                                             subtitle=primary_training_unknown(s))
            g.add(self.primary_row)
            if s.logged_in and s.ownership_error is None and s.account_devices is not None:
                self._show_primary_training(self.primary_row)
                # Read only, from the list the ownership check read: only for a device that
                # can be one.
                wearable = devicelist.primary_wearable(s.account_devices, d.unit_id)
                if wearable is not None:
                    g.add(primary_wearable_row(self, wearable))
        if offers_account_picture(s):
            g.add(account_picture_row(self, s))

        g = sec.group(_("Tools"))
        # A backup only reads the files of the device: of a drive too.
        self.backup_row = Adw.ActionRow(title=_("Backup"))
        show_backups(self)  # when the last one was made, how many; the folder in the tooltip
        # TRANSLATORS: button that creates a backup
        self.backup_row.add_suffix(self._button(_("Create"), self.backup))
        g.add(self.backup_row)
        device_error_row(self, g)  # only when the device logged an error lately
        row = Adw.ActionRow(title=_("Part number"), subtitle=d.part_number,
                            subtitle_selectable=True)
        g.add(row)
        sec.loaded = True
        sec.show("content")

    @staticmethod
    def _primary_training_text(status) -> str:
        if status.unit_id is None:
            return _("Not set")
        if status.is_this_device:
            return _("This device")
        return status.name or _("Another device")

    def _show_primary_training(self, row) -> None:
        """Which device the account uses as its primary training device; offers this one."""
        s = self.session

        def done(status):
            if self.session is not s:
                return
            row.set_subtitle(GLib.markup_escape_text(self._primary_training_text(status)))
            if not status.is_this_device and status.capable is True:
                row.add_suffix(self._button(
                    _("Set as Primary"),
                    lambda button: self.confirm_primary_training(status, button),
                    with_button=True))

        def failed(_e):
            if self.session is s:
                row.set_subtitle(_("Could not check"))

        self.in_background(
            lambda: service.primary_training_status(s.client, s.device, s.account_devices),
            done, failed)

    def confirm_primary_training(self, status, button: Gtk.Button | None = None) -> None:
        """Set as Primary: asked first; once Set is chosen, the change runs from `button`,
        which spins meanwhile."""
        s = self.session
        if s is None:
            return
        current = self._primary_training_text(status)
        confirm(self, "primary-training", _("Change the primary training device?"),
                _("The primary training device is the main source of performance and "
                  "training data in Garmin Connect.\n\nCurrent: {old}\nNew: {new}").format(
                    old=current, new=s.device.description),
                # TRANSLATORS: button that makes this device the primary training device
                Answer("set", _("Set"), lambda: self.set_primary_training(button),
                       look="suggested"),
                default="cancel")

    def set_primary_training(self, button: Gtk.Button | None = None) -> None:
        s = self.session
        if not s or not s.logged_in or s.ownership_error is not None:
            return

        def done(_before):
            self.toast(_("Primary training device changed"))
            if self.session is s:
                self.show_overview()

        self.run_task(_("Changing the primary training device…"),
                      lambda: service.set_primary_training(s.client, s.device, apply=True), done,
                      device=False, button=button)

    def _set_device_texture(self, texture) -> None:
        self.device_picture.set_paintable(texture)
        self.device_picture.set_visible(True)
        self.device_image.set_visible(False)

    def _show_device_picture(self) -> None:
        """Shows the device's picture at once from memory or the cache (the window's one
        store and reader, as the tiles have them); else asks for it in the background, the
        account first, then unit-info, and keeps it for every view."""
        s = self.session
        part = s.device.part_number
        # The account is asked only about a device that is on it; never about a Garmin device
        # in mass storage mode, which it was not checked for.
        on_account = (s.logged_in and s.ownership_error is None
                      and not getattr(s, "mass_storage", False))
        texture = pictures.known(self, part)
        if texture is None:
            texture = cached_texture(part)
            if texture is not None:
                pictures.keep(self, part, texture)
                log.info("device picture: from the cache")
        if texture is not None:
            self._set_device_texture(texture)
            if (not on_account and not picture_address_known(s.device)) or (
                    getattr(s, "mass_storage", False) and kind_untold(s.device)):
                # Off the account its address comes only from unit-info, while it is
                # connected: asked now for the address alone, so that the picture can come
                # back once the cache is cleared (a Venu 2). The file stays.
                # A drive whose kind Garmin has not told: asked once a run as well, so that
                # the log says what unit-info tells of it (an inReach).
                def address():
                    return picture_from_unit_info(s.client, s.device,
                                                  ask=lambda: service.unit_info(s))

                self.in_background(lambda: remember_picture_address(s.device, address()),
                                   lambda _r: None)
            return
        self.device_image.set_visible(True)
        self.device_picture.set_visible(False)
        name = device_picture_path(part).name

        def work():
            """(file, where it came from) or (None, why there is no picture)."""
            if on_account:
                try:
                    url = s.client.device_image_url(s.device.unit_id)
                except Exception as e:  # noqa: BLE001 - optional; the other source is tried
                    log.info("device picture: the account failed (%s)", type(e).__name__)
                    url = None
                remember_picture_address(s.device, url)
                path = cached_download(s.client.http, url, name) if url else None
                if path:
                    return path, "the account"
            url = picture_from_unit_info(s.client, s.device,
                                         ask=lambda: service.unit_info(s))
            if not url:
                return None, NO_ADDRESS
            remember_picture_address(s.device, url)
            path, why = download_picture(s.client.http, url, name)
            return (path, "unit-info") if path else (None, f"download refused: {why}")

        def done(result):
            path, source = result
            texture = load_texture(path) if path else None
            if texture is None:
                log.info("device picture: none (%s)", source if path is None
                         else "the file is not a picture")
                if self.session is s:
                    # Still the icon (a picture in the cache returns before this work);
                    # unit-info may have told the kind meanwhile (a bike accessory).
                    show_placeholder(self.device_image, device_view_icon(self, s),
                                     DEVICE_PICTURE_SIZE)
                    if path is None and source == NO_ADDRESS and offers_account_picture(s):
                        show_account_picture_offer(self, s)
                return
            log.info("device picture: from %s", source)
            pictures.keep(self, part, texture)
            start = self.start
            if start is not None:
                start.set_picture(part, texture)  # its tile on the start page as well
            if self.session is s:
                self._set_device_texture(texture)

        self.in_background(work, done)

    def _render_update_state(self) -> None:
        """Shows self.update_state in the overview: the "Device software" row, the chip under
        the version (_highlight_update) and the software label."""
        if self.session is None or self.update_row is None:
            return
        state = self.update_state
        self._highlight_update(start.update_available() if state == "available"
                               else start.update_ready() if state == "ready" else None)
        # The words of the top of the Updates page (update_state_text); how many (and which)
        # for updates to install, and those still to install besides one copied already.
        text = update_state_text(
            state, self.update_count if state in ("available", "ready") else 0,
            self.update_only if state == "available" else None,
            mass_storage=getattr(self.session, "mass_storage", False))
        checked_at = self.update_checked_at
        if checked_at is not None:
            text = checked_text(text, checked_at, datetime.now())  # not a check of now
        self.update_row.set_subtitle(GLib.markup_escape_text(text))
        # Its chip says it; the row what to do.
        self.software_label.set_label(software_text(self.session.device.software_version_str))

    def _highlight_update(self, chip: str | None) -> None:
        """An update to install, or one copied already, stands out on the device page: a
        chip under the version with these words ("Update available" as on its tile, "Update
        ready") and the subtitle of "Device software" in the warning colour. Otherwise (none,
        checking, failed: None) as the other rows. The side panel stays as it is."""
        if chip is not None:
            self.update_chip.set_label(chip)
        self.update_chip.set_visible(chip is not None)
        subtitle = row_subtitle(self.update_row)
        if subtitle is None:
            return
        if chip is not None:
            subtitle.add_css_class("warning")
        else:
            subtitle.remove_css_class("warning")

    def _set_update_state(self, new_updates: list, staged: list = ()) -> None:
        """`staged`: system updates copied already, waiting for the device to install them
        when it is disconnected: "ready", which wins over the updates still to install
        (those are told after it, with how many)."""
        self._update_check_session = None  # the row has its answer (show_device may wait)
        self.update_checked_at = self._update_record_at = None  # an answer of now
        self.update_count = len(new_updates)
        self.update_only = new_updates[0] if len(new_updates) == 1 else None
        self.update_state = ("ready" if staged else "available" if new_updates else "current")
        # For the tile of the device on the start page, also when it is not open: an update
        # to install, or none (up to date, or what was found is copied now), and whether one
        # waits for the device to install it.
        remember_update_found(getattr(self.session, "device", None),
                              new_updates, staged)
        self._render_update_state()

    def _check_updates_quietly(self) -> None:
        from openexpress import updates as upd

        s = self.session
        if s is None or self._update_check_session is s:
            return  # no watch, or a check for this watch is already running
        self._update_check_session = s

        def work():
            # Asked as the page Updates asks (reinstalls too): one answer for both (answers);
            # the row counts only what pending and staged leave.
            answer = s.client.software_updates(s.device.raw, include_reinstalls=True)
            items = upd.mark_present(s.storage, upd.parse_updates(answer),
                                     upd.component_count(s.device))
            return upd.pending(items), upd.staged(items)

        def done(result):
            if self._update_check_session is s:
                self._update_check_session = None
            if self.session is not s:
                return
            self._set_update_state(*result)

        def failed(_e):
            if self._update_check_session is s:
                self._update_check_session = None
            if self.session is s:
                update_check_failed(self)

        self.in_background(work, done, failed)

    def _render_map_state(self) -> None:
        """Shows self.map_state in the overview (the "Maps" row)."""
        if self.session is None or self.map_row is None:
            return
        from openexpress import maps as mp

        if self.map_state == "available":
            text = ngettext("{count} update available", "{count} updates available",
                            self.map_update_count).format(count=self.map_update_count)
        elif self.map_state == "current":
            # TRANSLATORS: subtitle of the row "Maps" of the device page, without a full stop
            text = _("All installed maps are up to date")
        elif self.map_state == "empty":
            text = _("No maps are installed")
        elif self.map_state == "none":
            text = mp.no_maps_message()
        elif self.map_state == "unsupported":
            text = mp.not_supported_text()
        elif self.map_state == "failed":
            text = _("Could not check for updates")
        else:
            text = _("Checking…")
        self.map_row.set_subtitle(text)

    def _set_map_state(self, result) -> None:
        """From an answer of the map check: the maps read, and how many the server listed;
        None when map updates for the device are not supported (maps.check). Also called by
        the Maps page, whose answer is the newer one."""
        self._map_check_session = None  # a quiet check that is still running is out of date
        if result is None:
            self.map_update_count = 0
            self.map_state = "unsupported"
            self._render_map_state()
            return
        all_maps, listed = result
        self.map_update_count = sum(1 for m in all_maps if m.update_available)
        if not listed:
            self.map_state = "none"
        elif self.map_update_count:
            self.map_state = "available"
        elif any(m.installed or m.incomplete for m in all_maps):
            self.map_state = "current"
        else:
            self.map_state = "empty"
        self._render_map_state()

    def _check_maps_quietly(self) -> None:
        """Ask Garmin's server once for the maps of the device that was just opened, for the
        "Maps" row only: the Maps page asks for itself. Nothing is written anywhere."""
        from openexpress import maps as mp

        s = self.session
        if s is None or self._map_check_session is s:
            return  # no device, or a check for this device is already running
        self._map_check_session = s

        def done(result):
            if self._map_check_session is not s:
                return  # another device, or the Maps page has a newer answer
            if self.session is s:
                self._set_map_state(result)
            self._map_check_session = None

        def failed(_e):
            if self._map_check_session is not s:
                return
            self._map_check_session = None
            if self.session is s and self.map_state is None:
                self.map_state = "failed"
                self._render_map_state()

        def work():
            try:
                result = mp.check(s.client, s.device, ask=lambda: service.unit_info(s),
                                  country=lambda: service.account_country(s))
            except mp.MapsNotSupported:
                return None  # the row says so; nothing to read on the device
            mp.mark_waiting(s.storage, result[0])
            return result

        self.in_background(work, done, failed)

    def _show_last_sync(self) -> None:
        if (self.last_sync_row is None or self.session is None
                or self.session.ownership_error is not None):
            return
        row = self.last_sync_row
        for child in self._last_sync_children:
            # With the row the list put around it: the box alone is no child of the expander
            # row, so it would stay, and each drawing would add the details once more.
            row.remove(child.get_parent() or child)
        self._last_sync_children = []
        s = self.session
        unit_id = s.device.unit_id
        data = service.load_last_sync(unit_id)
        self._last_sync_data = data  # its time told against now, again every minute
        row.set_subtitle(GLib.markup_escape_text(last_sync_subtitle(
            unit_id, data, open_to_sync(s), datetime.now())))
        synced = bool(unit_id) and sync.synced_here(unit_id)
        if not synced or not data:
            # Never synced here (the tooltip says by which installation), or no record.
            row.set_tooltip_text(None if synced else never_synced_tooltip())
            row.set_enable_expansion(False)
            return
        # Shown as last_sync.describe tells it: the same words as the command line and the
        # notification of the background sync.
        told = last_sync.describe(data)
        row.set_tooltip_text(told.tooltip)  # the full date
        said = told.details
        row.set_enable_expansion(bool(said))
        if said:
            details = start.last_sync_details(said)  # as on the page of a remembered device
            row.add_row(details)
            self._last_sync_children.append(details)

    def _watch_last_sync(self) -> None:
        """Updates the overview when the background service finishes a sync."""
        path = service.last_sync_path()
        paths.make_dir(path.parent)
        self._last_sync_monitor = Gio.File.new_for_path(str(path)).monitor_file(
            Gio.FileMonitorFlags.WATCH_MOVES, None)
        self._last_sync_monitor.connect("changed", lambda *_a: self._show_last_sync())
        self._last_sync_monitor.connect("changed", lambda *_a: self._count_activities_again())

    def _count_activities_again(self) -> None:
        """After a sync (this window's, or one of the background service or the command line,
        seen in last_sync.json): the activities of the devices connected counted again, from
        what was listed against state.json as it is now (the devices are not asked); the
        start page drawn again when it is shown."""
        s = self.session
        if s is not None and getattr(s, "to_sync", None) is not None:
            s.to_sync = s.to_sync.again()
            remember_sync_count(s.to_sync)
            refresh_last_sync_subtitle(self)
        for summary in self.connected.devices:
            if summary.to_sync is not None:
                summary.to_sync = summary.to_sync.again()
                remember_sync_count(summary.to_sync)
        if self._on_start_page():
            self._update_tile_chips()

    # --- backup -----------------------------------------------------------------

    def backup(self) -> None:
        from openexpress.backup import run_backup

        s = self.session
        if not s:
            return
        # Closing the window stops it, and its unfinished copy is removed.
        self.run_task(
            _("Creating backup…"),
            lets_the_window_close(
                lambda: run_backup(s.device, s.storage, paths.default_backup_dir(),
                                   progress=stop_when_closed(self, self.progress))),
            lambda r: (self.toast(ngettext("Copied {count} file to {path}",
                                           "Copied {count} files to {path}",
                                           r.files).format(count=r.files, path=r.path)),
                       show_backups(self), tell_left_out(self, r)),
        )

    # --- sync -------------------------------------------------------------------------

    def start_sync(self, first_run: str | None = None) -> None:
        s = self.session
        if (not s or getattr(s, "drive_refusal", None) is not None or not s.logged_in
                or s.ownership_error is not None):
            return

        def done(summary: service.SyncSummary):
            self.toast(_("Sync finished") if not summary.errors
                       else _("Sync finished with errors"))
            self._show_last_sync()
            self._count_activities_again()
            if getattr(s, "mass_storage", False) and summary.downloaded:
                ask_to_eject_after_sync(self, s)

        def failed(e):
            # What it uploaded before it stopped is done: kept in its record (run_sync).
            self._show_last_sync()
            self._count_activities_again()
            if isinstance(e, service.FirstSyncChoiceNeeded):
                self.ask_first_sync()
                return True
            if isinstance(e, OwnershipError) and self.session is s:
                # The account or its device list changed since the watch was detected.
                s.ownership_error = e
                self.show_device()
            return False

        def work():
            if getattr(s, "mass_storage", False):
                # Asked for a drive (only here and on the command line, never in the
                # background); run_sync judges it again before anything is uploaded.
                return service.run_sync(s, progress=self.progress, options=service.SyncOptions(
                    first_run=first_run, drive=True))
            return service.run_sync(s, first_run, self.progress)

        # Stop Waiting pressed during the previous sync of this session: not for this one.
        s.stop_waiting.clear()
        self.run_task(_("Syncing…"), writes_to_device(work), done, failed)

    def ask_first_sync(self) -> None:
        confirm(self, "first-sync", _("First sync"),
                _("If the device also syncs via your phone, most data is already "
                  "in Garmin Connect. You can mark the current files as uploaded "
                  "and upload only new ones, or upload everything (Connect skips duplicates)."),
                Answer("all", _("Upload everything"), lambda: self.start_sync("all")),
                Answer("baseline", _("New data only"), lambda: self.start_sync("baseline"),
                       look="suggested"),
                default="baseline")
