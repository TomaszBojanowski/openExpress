"""The detection of the devices of the window and where the user goes after it: the events
of gvfs (mounts and volumes) and of USB (_poll_usb, every USB_POLL_S seconds), the one timer
of a detection (schedule_detection, detection_due), the detection itself (detect_devices) and
the opening of a device chosen, the list of the devices connected read again, Refresh, and the
helpers of navigation the start page shares (seen_open, _take_asked, visible_view, decided).
DeviceDetection is a mixin of MainWindow (gui/app.py), without __init__ of its own;
DeviceChoices is a list of devices to choose from.

Has: session (the device opened, or none), the state of USB (_usb_state, _usb_since,
_usb_title, _usb_gone, _normal_since, _replug_told, _open_unplugged), the one timer of a
detection and what it waits for (_detect_timer, _detect_causes, _detect_reasons,
_detect_after_task, _asked, _pending_cause, _pending_reopen, _refresh_pending), the list read
again (_reread_cause, _reread_pending), the drives looked for (_drive_look,
_drives_while_open), _updated_apps (cleared when the device goes), _opening_tile (the tile
whose device is being opened).
Uses: busy, run_task, in_background, toast, error, connected, navigation, start, remembered,
sections, _remembered_unit, _remembered_from_account, show_device, show_no_device,
show_choices, _on_start_page, _on_remembered_page, _render_start, _refresh_remembered,
_fetch_account, _want_account, _update_actions."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from gi.repository import GLib

from openexpress import known_devices, service
from openexpress.device.gio_storage import (
    ABSENT,
    DRIVE_OFF,
    MASS_STORAGE,
    PRESENT,
    STARTING,
    UNMOUNTED_DRIVE,
    USB_GAP_S,
    WAIT_FOR_DEVICE_S,
    NotThereYet,
    SeveralDevices,
    distinct_devices,
    drive_notice_text,
    drive_off_text,
    drive_volume,
    drives_on_usb,
    drives_without_a_volume,
    ejected_by_us,
    ejected_drive_waits,
    ejected_text,
    find_all_devices,
    garmin_products_on_usb,
    garmin_ums,
    garmin_usb,
    held_elsewhere_text,
    mtp_missing,
    no_mtp_text,
    no_mtp_title,
    not_ready_text,
    note_drive_place,
    own_remount,
    probe,
    usb_state,
    usb_still_there,
)
from openexpress.gui.connected import open_address, open_unit, session_summary, unit_ids, uris
from openexpress.gui.navigation import (
    READ_THE_LIST,
    Cause,
    CheckOpen,
    Detect,
    Nothing,
    Page,
    ReadList,
    Seen,
    ShowStart,
    Stay,
    View,
    describe,
    navigate_after,
)
from openexpress.gui.navigation import plan as detection_plan
from openexpress.gui.window_tasks import read_space
from openexpress.i18n import N_, _

# The window's logger: the lines of the detection and of navigation stay where they were
# before the split.
log = logging.getLogger("openexpress.gui.app")

# A drive may come a moment after its USB device (#19, analysis 18): a device in mass storage
# mode that the last list found without one is looked for again every DRIVE_RETRY_S seconds,
# at most DRIVE_RETRIES times.
DRIVE_RETRY_S = 4
DRIVE_RETRIES = 5
# A device this long on USB in its normal mode without being offered over MTP: the page adds
# that it may be plugged in again (gvfs did not mount an Edge 1050 restarted with its cable
# in; plugging it in again helped, 21.10.2026). Only the words change, not the detection.
REPLUG_HINT_S = 15
# The same words for a device this long on USB in its transitional mode (091e:0003): past the
# about 19 s of a fēnix 8 starting, so that a device starting as usual gets no such advice.
STARTING_REPLUG_HINT_S = 25
# TRANSLATORS: added to the page while a device on USB is not offered over MTP
REPLUG_HINT = N_("If the device isn't locked and isn't asking for anything, unplug the cable and "
                 "plug it back in.")
# TRANSLATORS: the same, after the sentence about a PIN, which names the device already
REPLUG_HINT_AFTER_PIN = N_("If it isn't locked and isn't asking for anything, unplug the cable "
                           "and plug it back in.")


@dataclass
class DeviceChoices:
    """Several Garmin devices are connected: their summaries, one per device, to choose
    from (what a detection gives instead of a session)."""
    summaries: list


def detect_devices(current_uri: str | None = None):
    """The window's detection: a session on the one device connected, as before, or
    DeviceChoices when several different ones are (MTP devices and Garmin drives alike;
    one device seen in both ways is one). The device open now (`current_uri`) is opened
    again when it is still there. The list asks nothing of the account."""
    found = find_all_devices()
    if len(found) > 1 or any(not f.mounted and f.kind == "ums" for f in found):
        # Several, or a drive the system has not mounted: a tile to choose (and mount).
        again = next((f for f in found if current_uri is not None
                      and f.uri.rstrip("/") == current_uri.rstrip("/")), None)
        if again is not None:
            return service.check_session(service.open_session(found=again))
        return _choose(found)
    try:
        return service.check_session(service.open_session(allow_ums=True))
    except SeveralDevices as e:
        if not e.devices:
            raise
        return _choose(e.devices)


def with_space(found):
    """A device opened for the window, with its free space and capacity read in the same
    thread (the device page and the report then ask it nothing); a list to choose from is
    passed on as it is. Its diagnostics come after its page is shown
    (device_page.read_diagnostics)."""
    if not isinstance(found, DeviceChoices):
        found.space = read_space(found.storage)
    return found


def _note_seen(summaries: list) -> None:
    """Each device whose GarminDevice.xml was read, noted as seen now (devices.json): in a
    detection, before anything is chosen, and when the list is read again."""
    for s in summaries:
        if s.ok:
            known_devices.seen(s.unit_id, s.description, s.part_number, s.software_version,
                               s.found.kind, **service.drive_keys(
                                   s.found.usb if s.found.kind == "ums" else None))


def _choose(found: list):
    """The list of these devices, one entry each, without those gone since they were found;
    with one left, that one opened (D7); with none, no device."""
    summaries = distinct_devices([probe(f) for f in found])
    _note_seen(summaries)
    left = _still_there(summaries)
    if len(left) > 1 or (left and left[0].found.kind == "ums" and not left[0].found.mounted):
        # A drive the system has not mounted is not mounted behind the user's back: its tile.
        return DeviceChoices(left)
    if left:  # also one device found in two ways: no choice to make
        return service.check_session(service.open_session(found=left[0].found))
    raise NotThereYet(not_ready_text(ABSENT))


def _still_there(summaries: list) -> list:
    """Without the entries that have no details and are no longer on USB: gone."""
    return [s for s in summaries
            if s.ok or s.found.usb is None or s.found.usb.bus is None
            or usb_still_there(s.found.usb)]


def connected_devices() -> list:
    """The devices connected now, one entry each, as the start page shows them while a
    device is open: GarminDevice.xml of each is read (and each noted as seen), nothing is
    opened or mounted and nothing is asked of the account."""
    summaries = distinct_devices([probe(f) for f in find_all_devices()])
    _note_seen(summaries)
    return _still_there(summaries)


def _mount_address(mount) -> str:
    """The address of a mount for the log. An MTP address carries the serial number,
    which the log filter hides; any other address may carry a local path with the user
    name, which the filter does not hide, so only its scheme is written."""
    root = mount.get_root()
    uri = (root.get_uri() if root is not None else None) or ""
    if uri.startswith("mtp://"):
        return uri
    return uri.split(":", 1)[0] + ":" if ":" in uri else "unknown"


def _same_device(old, new) -> str:
    """For the log: how the detected device relates to the open one. Compared by the
    unit ID, which is never written."""
    if old is None:
        return "a device found"
    before = getattr(getattr(old, "device", None), "unit_id", None)
    now = getattr(getattr(new, "device", None), "unit_id", None)
    if before is not None and before == now:
        return "the same device as the open one"
    return "another device than the open one"


def _on_mount(storage, mount_uri: str) -> bool:
    """Whether the open storage is on this mount (full addresses, compared in memory)."""
    return str(getattr(storage, "label", "")).startswith(mount_uri.rstrip("/") + "/")


def _cause_of(reason: str | None) -> Cause:
    """Why a trigger of the timer came, when its caller did not say."""
    if reason == READ_THE_LIST:
        return Cause.UNPLUGGED_OTHER
    return Cause.USB_CHANGED if reason is None else Cause.PLUGGED


def _timed_cause(causes: list) -> Cause:
    """The cause of a detection by the timer, from those of its triggers: a mount first;
    never "another device unplugged" (that one only reads the list)."""
    for cause in (Cause.PLUGGED, Cause.UNPLUGGED_CHOICE, Cause.USB_CHANGED):
        if cause in causes:
            return cause
    return Cause.USB_CHANGED if causes else Cause.PLUGGED


def seen_open(s, connected) -> Seen:
    """What the window knows after a detection or a read of the list: the devices connected
    (`connected`, entries) with the open one among them."""
    address, unit = open_address(s), open_unit(s)
    return Seen(uris(connected) | ({address.rstrip("/")} if address else set()), address,
                unit_ids(connected) | ({unit} if unit else set()), unit)


def schedule_detection(win, delay_ms: int, mount_uri: str | None = None,
                       cause: Cause | None = None) -> None:
    """Look for the device after `delay_ms`: after a mount of a Garmin device (`mount_uri`),
    after it showed up on USB (None), or only read the list of devices connected again
    (READ_THE_LIST); `cause`: why (navigation.Cause). One timer for all of them: another
    trigger moves it, and what is decided when it fires covers every trigger so far."""
    if getattr(win, "_detect_timer", None) is not None:
        GLib.source_remove(win._detect_timer)
        log.info("device detection: moved by another trigger")
    win._detect_reasons = [*getattr(win, "_detect_reasons", []), mount_uri]
    win._detect_causes = [*getattr(win, "_detect_causes", []), cause or _cause_of(mount_uri)]

    def due():
        win._detect_timer = None
        detection_due(win)
        return False

    win._detect_timer = GLib.timeout_add(delay_ms, due)


def watch_drives_while_open(win) -> None:
    """While a device is open the window does not look at USB otherwise (_poll_usb), and a
    Garmin drive that the system does not mount sends no mount event (KDE, #19): one that
    comes has the list read again (Q1: the open device stays open), and so has one not
    mounted that goes. Only /sys is read."""
    places = drives_on_usb()
    before = getattr(win, "_drives_while_open", None)
    win._drives_while_open = places
    if before is None or places == before:
        return  # the first look: what is there is the open device's company
    if places - before:
        log.info("USB: a Garmin device in mass storage mode came while a device is open, the "
                 "list of devices is read again in 2.5 s")
        schedule_detection(win, 2500, cause=Cause.PLUGGED)
        return
    connected = getattr(win, "connected", None)
    if connected is not None and any(u.startswith(UNMOUNTED_DRIVE) for u in connected.unread()):
        log.info("USB: a Garmin device in mass storage mode went while a device is open, the "
                 "list of devices is read again in 1.5 s")
        schedule_detection(win, 1500, READ_THE_LIST, Cause.UNPLUGGED_OTHER)


def look_again_for_drives(win, clock=time.monotonic) -> None:
    """A Garmin device in mass storage mode that the last list found without a drive: gvfs
    had no volume of it yet, or none with a medium (#19, analysis 18). Looked for again every
    DRIVE_RETRY_S, at most DRIVE_RETRIES times for the same devices; with a device open,
    only the list is read again (Q1)."""
    waiting = drives_without_a_volume()
    if not waiting:
        win._drive_look = None
        return
    now = clock()
    look = getattr(win, "_drive_look", None)
    if look is None or look[0] != waiting:
        win._drive_look = (waiting, 0, now)  # counted from now
        return
    places, tries, since = look
    if tries >= DRIVE_RETRIES or now - since < DRIVE_RETRY_S:
        return
    win._drive_look = (places, tries + 1, now)
    log.info("USB: a Garmin device in mass storage mode has no drive yet, looked for again "
             "(%d of %d)", tries + 1, DRIVE_RETRIES)
    schedule_detection(win, 500, cause=Cause.PLUGGED)


def detection_due(win) -> None:
    """The scheduled detection: decided now (navigation.plan), or after the running task
    (e.g. the detection at start, during which gvfs reports the mount that is being
    opened)."""
    if win.busy:
        win._detect_after_task = True
        return
    win._detect_after_task = False
    reasons, win._detect_reasons = getattr(win, "_detect_reasons", []), []
    causes, win._detect_causes = getattr(win, "_detect_causes", []), []
    if getattr(win, "_detect_timer", None) is not None:
        # Decided after a task: this covers the triggers that came meanwhile, so the timer
        # armed for them would only fire again with none left.
        GLib.source_remove(win._detect_timer)
        win._detect_timer = None
    cause = _timed_cause(causes)
    s = win.session
    connected = getattr(win, "connected", None)
    usb = getattr(s.storage, "usb", None) if s is not None else None
    what = detection_plan(cause, reasons,
                          open_label=str(getattr(s.storage, "label", "")) if s is not None
                          else None,
                          listed=uris(connected.devices) if connected is not None
                          else frozenset(),
                          unread=connected.unread() if connected is not None
                          else frozenset(),
                          open_moved=lambda: (usb is not None and usb.bus is not None
                                              and not usb_still_there(usb)))
    # Mounts of devices already on the start page's list (e.g. reported by gvfs as the
    # window started, before one was chosen or not) bring nothing new: no detection, and
    # they never close the device chosen.
    if isinstance(what, Nothing):
        if what.ignored:
            log.info("device detection: mount of a device already listed, nothing to do")
        return  # nothing new: the open device stays as it is
    if what.ignored:
        log.info("device detection: %d mount(s) of devices already on the list, ignored",
                 what.ignored)
    if isinstance(what, ReadList):
        # Another device came or went, or one listed was mounted: the open one stays (Q1).
        only_gone = bool(causes) and all(c is Cause.UNPLUGGED_OTHER for c in causes)
        win._reread_cause = Cause.UNPLUGGED_OTHER if only_gone else cause
        win._reread_connected()
        return
    if isinstance(what, Detect):
        if what.moved:
            # The same address (it carries the serial number), but not the same place on
            # USB: unplugged and plugged in again, so the device may have changed (it
            # applies what was waiting when it is unplugged) and the session points to the
            # old place.
            log.info("device detection: the same address, but not at the same place on USB")
        if what.read_list:
            win.refresh_device(cause=cause, read_list=True)  # another device came as well
        else:
            win.refresh_device(cause=cause)
        return

    def done(_xml):
        if win.session is s:
            log.info("device detection: the open device is still there, nothing to do")

    def failed(e):
        log.info("device detection: the open device does not answer (%s)", type(e).__name__)
        if win.session is s:
            win.refresh_device(cause=Cause.PLUGGED)

    # CheckOpen: its mount was reported, not a new one: if it still answers, the pages stay
    # as they are.
    assert isinstance(what, CheckOpen)
    win.in_background(lambda: s.storage.read("GARMIN/GarminDevice.xml"), done, failed)


def _take_asked(win, own: Cause) -> tuple[Cause, str | None]:
    """Why the decision made now is made and the device open when it was asked: those of
    the detection that ends here (if one), else `own`. A Refresh on the start page asked
    while a task ran keeps the start page for what that task brings as well, once."""
    asked = getattr(win, "_asked", None)
    win._asked = None
    cause, open_before = asked if asked is not None else (own, None)
    if getattr(win, "_pending_cause", None) is Cause.REFRESH_ON_START:
        cause, win._pending_cause = Cause.REFRESH_ON_START, Cause.REFRESH_IN_VIEW
    return cause, open_before


def decided(win, cause: Cause, before: Seen, after: Seen):
    """Where the user goes (navigation.navigate_after), with one line of the log."""
    view = visible_view(win)
    decision = navigate_after(cause, view, before, after)
    log.info("%s", describe(cause, view, before, after, decision))
    return decision


def visible_view(win) -> View:
    """The page shown, for navigation (the remembered device by its unit ID, in memory)."""
    page = win.navigation.get_visible_page()
    if page is win.start.page:
        return View(Page.START)
    if page is win.remembered.page:
        return View(Page.REMEMBERED, getattr(win, "_remembered_unit", None),
                    getattr(win, "_remembered_from_account", False))
    return View(Page.DEVICE)


class DeviceDetection:
    # --- the refresh that waited for a task ---------------------------------------

    def _run_pending_refresh(self) -> None:
        if self._refresh_pending and not self.busy:
            self._refresh_pending = False
            self.refresh_device()
        elif getattr(self, "_detect_after_task", False) and not self.busy:
            detection_due(self)
        elif getattr(self, "_reread_pending", False) and not self.busy:
            self._reread_pending = False
            self._reread_connected()

    # --- device ----------------------------------------------------------------------

    def _on_mount_added(self, _mon, mount):
        where = _mount_address(mount)
        if own_remount(mount):
            # Mounted again by our own install; the open device stays as it is.
            log.info("mount added (%s): our own remount, ignored", where)
            return
        ums = garmin_ums(mount)
        if ums is not None and ejected_by_us(ums):
            # Mounted again after we ejected it: it stays closed until it is unplugged.
            log.info("mount added (%s): a Garmin drive ejected by openExpress, ignored until it "
                     "is unplugged", where)
            return
        # Over MTP, or a drive of a Garmin device in mass storage mode.
        if garmin_usb(mount) or ums:
            log.info("mount added (%s): a Garmin device, detecting in 1.5 s "
                     "(a device is open: %s)", where, self.session is not None)
            schedule_detection(self, 1500, mount.get_root().get_uri(), Cause.PLUGGED)
        else:
            log.info("mount added (%s): not a Garmin device, ignored", where)

    def _on_volume_added(self, _mon, volume):
        self._on_drive_volume(volume, "added")

    def _on_volume_changed(self, _mon, volume):
        self._on_drive_volume(volume, "changed")

    def _on_volume_removed(self, _mon, volume):
        unix = volume.get_identifier("unix-device")
        if unix:
            self._drive_gone(UNMOUNTED_DRIVE + unix, "removed")

    def _on_drive_volume(self, volume, what: str) -> None:
        """A volume added or changed. A Garmin drive that the system does not mount (KDE)
        sends no mount event, and gvfs may offer its volume, or a medium in it, a moment
        after the device is on USB (#19, analysis 18): one not mounted with a medium, not on
        the list yet, brings a detection (with a device open: the list read again, Q1); one
        on the list without a medium any more leaves it. A mounted one comes with its mount
        (_on_mount_added); a drive ejected by openExpress stays closed (D3)."""
        drive = drive_volume(volume)
        if drive is None:
            return
        key, usb, medium = drive
        if getattr(volume, "get_mount", lambda: None)() is not None or ejected_by_us(usb):
            return
        if not medium:
            self._drive_gone(key, f"{what}, without a medium")
            return
        connected = self.connected
        if connected.listed(key) if self.session is not None else connected.is_choice(key):
            return  # its tile is there
        log.info("volume %s: a Garmin drive not mounted (USB %s), detecting in 2.5 s (a device "
                 "is open: %s)", what, usb.usb_id, self.session is not None)
        schedule_detection(self, 2500, key, Cause.PLUGGED)

    def _drive_gone(self, key: str, what: str) -> None:
        """The volume of a drive not mounted went, or its medium did: its tile goes too."""
        connected = self.connected
        if self.session is not None and connected.listed(key):
            log.info("volume %s: a Garmin drive not mounted on the list, the list of devices "
                     "is read again in 1.5 s", what)
            schedule_detection(self, 1500, READ_THE_LIST, Cause.UNPLUGGED_OTHER)
        elif self.session is None and connected.is_choice(key):
            log.info("volume %s: a Garmin drive not mounted, one of the devices to choose "
                     "from, detecting in 1.5 s", what)
            schedule_detection(self, 1500, cause=Cause.UNPLUGGED_CHOICE)

    def _on_mount_removed(self, _mon, mount):
        where = _mount_address(mount)
        if own_remount(mount):
            log.info("mount removed (%s): our own remount, ignored", where)
            return
        # The USB device is already gone, so compare with the device that is open.
        uri = mount.get_root().get_uri().rstrip("/") + "/"
        s = self.session
        # A Garmin drive is known by its label as well: an unplugged one is gone from /sys.
        ours = s is not None and str(getattr(s.storage, "label", "")).startswith(uri)
        connected = getattr(self, "connected", None)
        if s is None and connected is not None and connected.is_choice(uri):
            log.info("mount removed (%s): one of the devices to choose from, detecting in "
                     "1.5 s", where)
            schedule_detection(self, 1500, cause=Cause.UNPLUGGED_CHOICE)
            return
        if not ours and s is None and (garmin_usb(mount) or garmin_ums(mount)):
            # gvfs reports two mounts of one device: the pages are empty after the first.
            log.info("mount removed (%s): a Garmin device, no device is open, nothing to do",
                     where)
            note_drive_place(garmin_ums(mount))  # a watch may switch its drive off now
            return
        if (not ours and s is not None
                and usb_still_there(getattr(s.storage, "usb", None))):
            if connected is not None and not connected.listed(uri):
                # A pendrive, a phone, a share: none of the devices on the start page (one
                # mounted but not listed yet is read with the list its mount asked for).
                log.info("mount removed (%s): not a device on the list, ignored", where)
                return
            # Another Garmin device went away (several connected): the open one is still at
            # its place on USB, so it stays open; the start page lists those left, read once
            # by the timer of the other triggers (gvfs reports two mounts of one device).
            log.info("mount removed (%s): not the open device, which is still there; the "
                     "list of devices is read again in 1.5 s", where)
            schedule_detection(self, 1500, READ_THE_LIST, Cause.UNPLUGGED_OTHER)
            return
        if ours or garmin_usb(mount):
            log.info("mount removed (%s): %s, showing no device (a task is running: %s)",
                     where, "the open device" if ours else "a Garmin device",
                     bool(getattr(self, "busy", False)))
            if ours and getattr(s, "mass_storage", False):
                # Its place on USB, kept: a watch switches its drive off once it is ejected
                # and comes back as 091e:0003 (#19).
                note_drive_place(getattr(s.storage, "usb", None))
            self._asked = (Cause.UNPLUGGED_OPEN, open_address(s))
            self.session = None
            self._updated_apps = set()  # unplugged: the device applies what was waiting
            self._open_unplugged = True  # the USB poll looks for a device left (_poll_usb)
            self.show_no_device()
        else:
            log.info("mount removed (%s): not the open device, ignored", where)

    @staticmethod
    def _usb_notice(state: str, waited_s: float,
                    normal_s: float = 0.0) -> tuple[str, str, bool]:
        """(title, description, busy) of the placeholder while no device is open. `normal_s`:
        how long a device has been on USB in its normal mode without being offered over MTP;
        from REPLUG_HINT_S on, the description adds that it may be plugged in again (an Edge
        1050 restarted with its cable in was never mounted by gvfs, 21.10.2026)."""
        if state == PRESENT and mtp_missing():
            # gvfs here has no MTP backend (#13): said at once, nothing to wait for.
            return no_mtp_title(), no_mtp_text(), False
        if state == PRESENT and waited_s >= WAIT_FOR_DEVICE_S:
            # The PIN (not_ready_text, which names the device), then plugging it in again
            # (after REPLUG_HINT_S in its normal mode), then other programs.
            replug = f" {_(REPLUG_HINT_AFTER_PIN)}" if normal_s >= REPLUG_HINT_S else ""
            return (_("The device is not ready"),
                    f"{not_ready_text(state)}{replug} {held_elsewhere_text()}", False)
        if state == PRESENT and normal_s >= REPLUG_HINT_S:
            # One sentence becomes two: each with its full stop.
            text = _("This takes a moment after the cable is plugged in.")
            return _("The device is starting…"), f"{text} {_(REPLUG_HINT)}", True
        if state == DRIVE_OFF:
            # TRANSLATORS: a device connected as a USB drive, not over MTP
            return _("USB drive"), drive_off_text(), False
        if state == STARTING and STARTING_REPLUG_HINT_S <= waited_s < WAIT_FOR_DEVICE_S:
            # Still in its transitional mode: one sentence becomes two, as above.
            text = _("This takes a moment after the cable is plugged in.")
            return _("The device is starting…"), f"{text} {_(REPLUG_HINT)}", True
        if state == ABSENT:
            return (_("Connect your device"),
                    _("Connect your Garmin device with a USB cable. "
                      "It will be detected automatically."), False)
        if state == MASS_STORAGE:
            if ejected_drive_waits():
                # TRANSLATORS: title while a drive ejected by openExpress is still plugged in
                return _("Device ejected"), ejected_text(), False
            # The window opens such a drive once the system mounts it, so with none open it
            # is not mounted - or, in the Flatpak, mounted where the sandbox cannot see (F1).
            text = drive_notice_text()
            # TRANSLATORS: a device connected as a USB drive, not over MTP
            return _("USB drive"), text, False
        if waited_s < WAIT_FOR_DEVICE_S:
            return (_("The device is starting…"),
                    _("This takes a moment after the cable is plugged in"), True)
        return _("The device is not ready"), not_ready_text(state), False

    def _poll_usb(self, clock=time.monotonic) -> bool:
        """Every few seconds: while no device is open, is a Garmin device on USB that gvfs
        does not offer yet? While one is open, only the Garmin drives that come or go
        (watch_drives_while_open, look_again_for_drives). Only /sys is read; nothing is sent
        to the device."""
        if self.session is not None or self.busy:
            self._usb_state = ABSENT
            if self.session is not None:
                self._open_unplugged = False  # a device is open again
                if not self.busy:
                    watch_drives_while_open(self)
                    look_again_for_drives(self, clock)
            return True
        self._drives_while_open = None  # looked at anew once a device is open
        # The open device was just unplugged: a device left on USB is looked for (below).
        unplugged, self._open_unplugged = getattr(self, "_open_unplugged", False), False
        connected = getattr(self, "connected", None)
        if connected is not None and connected.choosing and connected.devices:
            # The list stays as it is; a Garmin device plugged in or out (also one that was
            # never mounted, which sends no mount event) brings one new detection.
            if connected.usb_changed(garmin_products_on_usb()):
                log.info("USB: the Garmin devices changed while the list is shown, detecting "
                         "in 1.5 s")
                schedule_detection(self, 1500, cause=Cause.USB_CHANGED)
            look_again_for_drives(self, clock)
            return True
        state = usb_state()
        if state == ABSENT and self._usb_state != ABSENT:
            # Off the bus for a moment between its two modes, or unplugged? Wait and see.
            gone = getattr(self, "_usb_gone", None)
            if gone is None:
                self._usb_gone = clock()
                return True
            if clock() - gone < USB_GAP_S:
                return True
        self._usb_gone = None
        if state != ABSENT and self._usb_state == ABSENT:
            self._usb_since = clock()
        if state == PRESENT and self._usb_state != PRESENT:
            # It left its transitional mode: look for it once gvfs had a moment.
            log.info("USB: a Garmin device in its normal mode, detecting in 2.5 s")
            self._normal_since, self._replug_told = clock(), False
            schedule_detection(self, 2500, cause=Cause.PLUGGED)
        elif state == MASS_STORAGE and self._usb_state != MASS_STORAGE and not unplugged:
            # A drive the system does not mount (KDE) sends no mount event: looked for once,
            # for its tile (analysis 15, stage 0).
            log.info("USB: a Garmin device in mass storage mode, detecting in 2.5 s")
            schedule_detection(self, 2500, cause=Cause.PLUGGED)
        elif unplugged and isinstance(detection_plan(Cause.UNPLUGGED_OPEN, left_on_usb=state,
                                                     ejected_waits=ejected_drive_waits()),
                                      Detect):
            # A Garmin drive left after the open device went (a Varia next to an Edge,
            # 22.10.2026): mounted already, it sends no event, so it is looked for as one in
            # its normal mode is, above.
            log.info("USB: the open device unplugged, a Garmin drive is still connected, "
                     "detecting in 2.5 s")
            schedule_detection(self, 2500, cause=Cause.PLUGGED)
        self._usb_state = state
        normal_s = clock() - getattr(self, "_normal_since", 0.0) if state == PRESENT else 0.0
        if normal_s >= REPLUG_HINT_S and not getattr(self, "_replug_told", False):
            self._replug_told = True
            log.info("USB: present for %d s without MTP, suggesting to replug", REPLUG_HINT_S)
        notice = self._usb_notice(state, clock() - self._usb_since if state != ABSENT else 0,
                                  normal_s)
        for sec in self.sections.values():
            sec.set_empty(*notice)
        self.start.set_empty(*notice)  # the start page says it on top
        if notice[0] != self._usb_title:
            self._usb_title = notice[0]
            self._update_actions()  # the Sync button says the same as the pages
        look_again_for_drives(self, clock)
        return True

    def refresh_device(self, cause: Cause | None = None, read_list: bool = False,
                       reopen: str | None = None) -> None:
        """Detect the device anew; with several devices connected, the one open now is
        opened again when it is still there. `cause`: why (navigation.Cause); none said: a
        detection asked again (Refresh and the like), or the one that waited for the
        running task. `read_list`: once a device is open, the list of devices connected is
        read again (another device came as well; asked by the timer, which itself waits for
        a running task). `reopen`: the address of the device open before its session was
        dropped (a change of account), opened again as the open one would be."""
        if self.busy:
            # E.g. the watch was replugged during a task: detect it once the task ends.
            log.info("device detection: after the running task")
            self._refresh_pending = True
            if cause is not None:
                self._pending_cause = cause  # the last one asked, as it was asked
            if reopen:
                self._pending_reopen = reopen
            return
        self._refresh_pending = False
        reopen = reopen or getattr(self, "_pending_reopen", None)
        self._pending_reopen = None
        pending, self._pending_cause = getattr(self, "_pending_cause", None), None
        if pending is Cause.REFRESH_ON_START:
            cause = pending  # Refresh on the start page waited for the task: it stays there
        elif cause is None:
            cause = pending or Cause.REFRESH_IN_VIEW
        # Why, and the device open now: where the user goes once it is done (navigation).
        self._asked = (cause, open_address(self.session) or reopen)
        log.info("device detection: %s", "again, a device is open" if self.session is not None
                 else "no device is open")
        # The device to open again when it is still there, among several (None: none).
        current = getattr(self.session, "storage", None)
        current_uri = getattr(current, "device_uri", None) or reopen

        def done(result):
            if isinstance(result, DeviceChoices):
                log.info("device detection: %d devices, a list to choose from",
                         len(result.summaries))
                self.session = None
                self.connected.listing(result.summaries, garmin_products_on_usb())
                self.show_choices()
                return
            log.info("device detection: %s, all pages are loaded anew",
                     _same_device(self.session, result))
            if current_uri is None:
                # With no device to open again, a session means the one device connected:
                # the devices listed before are gone (e.g. one of two unplugged).
                self.connected.found_alone()
            else:
                self.connected.stop_choosing()
            self.session = result
            self.show_device()
            if read_list and self.session is result:
                self._reread_cause = cause
                self._reread_connected()

        def failed(_e):
            self.session = None
            self.connected.clear()
            self.show_no_device()
            return True

        if self.session is None:
            self.sections["watch"].show("loading")
        # A Garmin device in mass storage mode too, when no MTP device is there or on its way;
        # several different devices give a list to choose from.
        self.run_task(_("Detecting the device…"),
                      lambda: with_space(detect_devices(current_uri)), done, failed)

    def open_choice(self, found, tile: int | None = None) -> None:
        """The device chosen from the list, opened as if it were the only one; `tile`: the
        place of the tile clicked, which shows the opening until it ends (StartPage
        .set_opening; a second click on it meanwhile says nothing, _open_tile)."""
        if self.busy:
            self.toast(_("Another operation is in progress"))
            return

        mounted_now = not getattr(found, "mounted", True)  # a drive mounted once chosen
        if tile is not None:
            self._opening_tile = tile
            self.start.set_opening(tile)

        def opened() -> None:
            if tile is not None:
                self._opening_tile = None
                self.start.set_opening(None)

        def done(sess):
            opened()
            log.info("device detection: the device chosen, all pages are loaded anew")
            self.connected.stop_choosing()
            self.session = sess
            if mounted_now:
                # Its entry had no details: the open one takes its place, so that the others
                # stay connected (the list was dropped, 0.7.1 on GNOME).
                self.connected.opened_in_place(found, session_summary(sess))
            self.show_device()
            if mounted_now:
                # Then all of them read again, as Refresh on the start page does.
                log.info("device detection: a drive mounted from its tile, the list read again")
                self._reread_cause = Cause.REFRESH_ON_START
                self._reread_connected()

        def failed(e):
            opened()
            # Unplugged meanwhile, or not readable: said, and the devices detected again.
            self.error(_("Could not open the device"), str(e) or type(e).__name__)
            self.connected.stop_choosing()
            self.refresh_device(cause=Cause.CHOICE_FAILED)
            return True

        self.run_task(_("Opening the device…"),
                      lambda: with_space(service.check_session(service.open_session(
                          found=found))), done, failed)

    # --- the refresh and the list of devices read again ----------------------------

    def _on_refresh(self) -> None:
        """Refresh (the button, F5). On the start page with a device open: the list is read
        again, nothing else (decision A); without one, a detection that stays there also when
        one device is found (decision 7). On the view of a device not connected: only that
        view (_refresh_remembered). In the device view: the device detected anew, the open
        one opened again (refresh_device)."""
        if self._on_remembered_page():
            self._refresh_remembered()
            return
        on_start = self._on_start_page()
        cause = Cause.REFRESH_ON_START if on_start else Cause.REFRESH_IN_VIEW
        s = self.session
        what = detection_plan(cause, open_label=str(getattr(s.storage, "label", ""))
                              if s is not None else None)
        if isinstance(what, ReadList):
            self._fetch_account()  # the start page lists the account's devices too
            self._reread_cause = cause
            self._reread_connected()
            return
        if on_start:
            self._want_account()  # from the device the detection opens, or asked after it
        self.refresh_device(cause=cause)

    def _reread_connected(self) -> None:
        """Read the devices connected again (their GarminDevice.xml only: nothing opened,
        nothing asked of the account), while one is open; the list is replaced whole and
        drawn once, the old tiles stay until then. Never opens a device view (D7). After the
        running task, if one runs."""
        if self.busy:
            self._reread_pending = True
            return
        s = self.session

        def done(found):
            if self.session is not s:
                return  # closed or replaced meanwhile: drawn by what did that
            log.info("start page: %d devices connected", len(found))
            cause, self._reread_cause = (getattr(self, "_reread_cause", None)
                                         or Cause.UNPLUGGED_OTHER), None
            address = open_address(s)
            before = Seen(self.connected.drawn, address)
            self.connected.reread(found)
            if s is None:  # nothing open, nothing to decide
                if self._on_start_page():
                    self._render_start()
                return
            # The open device stays open; the user goes to the start page only from the view
            # of a remembered device that came now (decision 3).
            decision = decided(self, cause, before, seen_open(s, found))
            if isinstance(decision, ShowStart):
                self.navigation.pop_to_page(self.start.page)  # drawn by _on_popped
            elif isinstance(decision, Stay) and decision.redraw_start:
                self._render_start()

        self.in_background(connected_devices, done)
