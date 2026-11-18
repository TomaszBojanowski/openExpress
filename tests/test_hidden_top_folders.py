"""A device that hides a folder of its top level from MTP (a car navigator's .System): what
openExpress cannot see it does not take for missing; it reads that folder through its own USB
session and writes there software updates and the time zone map as Garmin Express does, and a
map only through the trial of the command line.

The made-up device (device_root) has no .System at all, as ordinary MTP shows a navigator, and
visible Voice and Vehicle folders. Garmin's update server offers its software and a time-zone
map in .System, a voice and a vehicle it does not have (add-ons: not installed, optional), and a
new part in a new folder of its GARMIN folder (recommended, not installed yet); the universal map
service lists a map whose files are in .System/Map and one in the visible Garmin/Maps. All parts,
names and values are made up."""

import contextlib
import hashlib
import os
import pathlib
import posixpath
import types

import pytest
from fakes import (
    DRIVE_MAP,
    DRIVE_NEW_MAP,
    DRIVE_SPACE,
    Section,
    in_language,
    maps_window,
    window_methods,
)
from fakes import window as stand_in
from ptp_fake import DRIVE_53_OPERATIONS, FakeDevice

from openexpress import download, hidden_folders, maps, updates
from openexpress.cli import main
from openexpress.connect import GarminClient, lower_keys
from openexpress.device import ptp
from openexpress.device.storage import LocalStorage

SYSTEM = b"made-up navigator software"
NEW_PART = b"made-up new part"


def _entry(part, name, path, filename, data, severity="Recommended", installed=True,
           primary=False, locale=None, eula=None):
    """One offered item, the keys as the server sends them."""
    return {
        "partNumber": part, "productName": name, "dataType": "Firmware",
        "displayVersion": "9.00", "severity": severity, "isPrimaryFirmware": primary,
        "locale": locale, "releaseState": "live", "releaseNotes": [], "eulaUrl": eula,
        "deliverable": {"downloadUrl": "https://example.org/" + filename,
                        "md5": hashlib.md5(data).hexdigest().upper(),
                        "sizeInBytes": len(data)},
        "installation": {"path": path, "fileName": filename, "isReinstall": False,
                         "isProductInstalled": installed, "isRestartRequired": False,
                         "order": 0},
    }


def server_answer():
    return {"updates": [
        _entry("006-B0000-00", "Made-up Drive", ".System", "B000000.update", SYSTEM,
               primary=True),
        _entry("006-D9001-00", "Time zone map", ".System", "gmaptz.img", b"tz"),
        _entry("006-D9002-00", "English Made-up Voice", "Voice", "English_Made_Up.vpm", b"v",
               severity="Optional", installed=False, locale="en_US"),
        _entry("006-D9003-00", "Made-up Vehicle", "Vehicle", "Made_Up.srf", b"car",
               severity="Optional", installed=False),
        _entry("006-D9004-00", "New Part", "Garmin\\NewPart", "new.bin", NEW_PART,
               installed=False),
    ]}


@pytest.fixture
def navigator(device_root):
    """The made-up device with the visible folders of a navigator."""
    (device_root / "Voice").mkdir()
    (device_root / "Vehicle").mkdir()
    return device_root


def _offered(root):
    storage = LocalStorage(root)
    return storage, _offered_from(storage)


def _by_part(items):
    return {u.part_number: u for u in items}


# --- what is seen and what is not -------------------------------------------------------------


def test_an_update_in_a_top_folder_not_seen_is_not_taken_for_missing(navigator):
    _storage, items = _offered(navigator)
    by = _by_part(items)
    assert by["006-B0000-00"].hidden_folder == ".System"
    assert by["006-D9001-00"].hidden_folder == ".System"
    # A new folder inside a folder that is seen is made for the write.
    assert by["006-D9004-00"].hidden_folder is None
    assert [u.part_number for u in updates.installable(updates.pending(items))] == [
        "006-D9004-00"]


def test_optional_items_the_device_does_not_have_are_add_ons_not_updates(navigator):
    _storage, items = _offered(navigator)
    by = _by_part(items)
    assert by["006-D9002-00"].is_add_on and by["006-D9003-00"].is_add_on
    # Recommended and not installed yet: a new part to install, as Garmin Express installs it.
    assert not by["006-D9004-00"].is_add_on
    # The device's own software is never an add-on.
    assert not by["006-B0000-00"].is_add_on
    assert [u.part_number for u in updates.pending(items)] == [
        "006-B0000-00", "006-D9001-00", "006-D9004-00"]
    assert [u.part_number for u in updates.add_ons(items)] == ["006-D9002-00", "006-D9003-00"]


def test_without_the_servers_word_an_item_is_no_add_on():
    item = updates.parse_updates(lower_keys({"updates": [
        {**_entry("006-D9002-00", "Voice", "Voice", "x.vpm", b"v", severity="Optional"),
         "installation": {"path": "Voice", "fileName": "x.vpm", "isReinstall": False}}]}))[0]
    assert not item.is_add_on


# --- the install: nothing in a top folder not seen ----------------------------------------------


def _fetching(monkeypatch, content, again=True):
    """download.fetch replaced: each file written as `content(name, md5)` gives it; the names
    of the files fetched, in their order. `again=False`: a file in the cache at its size is not
    fetched again, as download.fetch does."""
    fetched = []

    def fetch(urls, dest, size, md5, session=None, progress=None):
        if again or not (dest.exists() and dest.stat().st_size == size):
            fetched.append(dest.name)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(content(dest.name, md5))
        return dest

    monkeypatch.setattr(download, "fetch", fetch)
    return fetched


@pytest.fixture
def downloads(monkeypatch, tmp_path):
    monkeypatch.setattr(updates, "cache_path", lambda u: tmp_path / "cache" / u.filename)
    return _fetching(monkeypatch, lambda name, md5: NEW_PART if name == "new.bin" else SYSTEM)


def test_the_install_into_a_top_folder_not_seen_is_refused_before_anything(navigator, downloads):
    storage, items = _offered(navigator)
    system = _by_part(items)["006-B0000-00"]
    with pytest.raises(updates.UpdateError) as refused:
        updates.install([system], storage)
    assert str(refused.value) == ("Not installed: Made-up Drive 9.00 goes to the folder .System, "
                                  "which openExpress cannot see on the device.")
    assert downloads == []
    assert not any(p.name.casefold() == ".system" for p in navigator.iterdir())


def test_a_new_folder_inside_a_folder_seen_is_made_for_the_write(navigator, downloads):
    storage, items = _offered(navigator)
    report = updates.install([_by_part(items)["006-D9004-00"]], storage)
    assert [u.part_number for u in report.written] == ["006-D9004-00"]
    assert (navigator / "GARMIN" / "NewPart" / "new.bin").read_bytes() == NEW_PART


# --- the command line ---------------------------------------------------------------------------


@pytest.fixture
def offered_to(navigator, monkeypatch):
    answer = lower_keys(server_answer())
    monkeypatch.setattr(GarminClient, "software_updates",
                        lambda self, raw, include_reinstalls=False: answer)
    monkeypatch.setattr(GarminClient, "unit_info", lambda self, unit_id, part_number: {})
    return navigator


NOT_INSTALLED_SYSTEM = ("      Not installed: Made-up Drive 9.00 goes to the folder .System, which "
                        "openExpress cannot see on the device.")
NOT_INSTALLED_TZ = ("      Not installed: Time zone map 9.00 goes to the folder .System, which "
                    "openExpress cannot see on the device.")
NOTICE = ("openExpress cannot see the folder .System, where this device keeps its software, so it "
          "installs nothing there. Some devices, such as car navigators, hide it from the "
          "ordinary USB connection.")
ADD_ONS = ("2 add-ons that are not on the device are not listed: openExpress does not install "
           "add-ons yet.")


def test_the_list_says_what_cannot_be_installed_and_leaves_out_the_add_ons(offered_to, capsys):
    assert main(["--path", str(offered_to), "updates"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert "Available updates: 3" in out
    assert NOT_INSTALLED_SYSTEM in out and NOT_INSTALLED_TZ in out
    assert not any("006-D9002-00" in line or "006-D9003-00" in line for line in out)
    assert ADD_ONS in out and NOTICE in out


def test_install_all_writes_what_it_can_and_refuses_the_rest(offered_to, downloads, capsys):
    assert main(["--path", str(offered_to), "updates", "--install", "all", "--apply"]) == 1
    out = capsys.readouterr().out.splitlines()
    assert NOT_INSTALLED_SYSTEM.strip() in [line.strip() for line in out]
    assert NOTICE in out
    assert downloads == ["new.bin"]  # nothing for .System, no add-on
    assert (offered_to / "GARMIN" / "NewPart" / "new.bin").read_bytes() == NEW_PART
    assert not any(p.name.casefold() == ".system" for p in offered_to.iterdir())


def test_the_software_alone_is_refused_with_why(offered_to, downloads, capsys):
    assert main(["--path", str(offered_to), "updates", "--install", "006-B0000-00",
                 "--apply"]) == 1
    out = capsys.readouterr().out.splitlines()
    assert NOTICE in out and downloads == []


# --- the window -----------------------------------------------------------------------------------


def _updates_page(items):
    """The groups of the Updates page of the made-up Drive as it shows `items`."""
    win, _session = _window_of(None, items)
    return win.sections["updates"].groups


def _texts(widget):
    from gi.repository import Gtk

    from openexpress.gui.window_tasks import descendants

    return [w.get_label() for w in [widget, *descendants(widget)]
            if isinstance(w, (Gtk.Label, Gtk.Button)) and w.get_label()]


@pytest.mark.needs_display
def test_the_updates_page_installs_nothing_into_a_top_folder_not_seen(navigator):
    from gi.repository import Adw

    _storage, items = _offered(navigator)
    status, available, advanced = _updates_page(items)
    top = _texts(status.rows[0])
    # The device's software: no Install, and why.
    assert NOTICE in top and "Install" not in top and "Install All" not in top
    rows = {r.get_title(): r for r in available.rows}
    assert rows["Time zone map"].get_subtitle().endswith(
        "Cannot be installed: openExpress cannot see its folder .System on this device")
    assert "Install" not in _texts(rows["Time zone map"])
    assert "Install" in _texts(rows["New Part"])
    (add_ons,) = [r for r in advanced.rows if isinstance(r, Adw.ActionRow)
                  and r.get_title() == "Add-Ons"]
    assert add_ons.get_subtitle() == ("2 add-ons that are not on the device; openExpress does "
                                      "not install add-ons yet")


# --- maps -----------------------------------------------------------------------------------------


def _map(name, path):
    return {
        "displayname": name, "maptype": "CityNavigator", "installationstate": 1,
        "isreinstall": True, "canuninstall": True,
        "release": {"majorversion": 2026, "minorversion": 20},
        "downloadhosts": {"foregroundprimaryhost": "https://example.org/maps/",
                          "failoverhosts": []},
        "filestoremove": [{"filename": path}],
        "installoptions": [{"ispreferred": True, "identifier": {"regionpartnumber": name},
                            "files": [{"filename": path, "datatype": "X",
                                       "deliverableoptions": [{
                                           "type": 1, "url": "x.img", "md5": "AAAA",
                                           "sizeinbytes": 4}]}]}],
    }


def test_a_map_whose_folder_is_not_seen_cannot_be_read_not_removed(navigator):
    (navigator / "GARMIN" / "Maps").mkdir()
    found, _count = maps.read_maps({"maps": [
        _map("Made-up City Navigator", ".System/Map/D0000001A.img"),
        _map("Made-up Topo", "Garmin/Maps/T.img")]})
    maps.mark_waiting(LocalStorage(navigator), found)
    hidden, gone = found
    assert hidden.unseen and hidden.waiting is None and not hidden.update_available
    assert hidden.status == "cannot be read: openExpress cannot see the folder of its files"
    # Its folder is seen, its file is not there: removed.
    assert not gone.unseen and gone.waiting == maps.WAIT_REMOVE


def test_a_map_that_cannot_be_read_is_not_removed(navigator):
    found, _count = maps.read_maps({"maps": [
        _map("Made-up City Navigator", ".System/Map/D0000001A.img")]})
    storage = LocalStorage(navigator)
    maps.mark_waiting(storage, found)
    with pytest.raises(maps.MapError, match="cannot see the folder of its files"):
        maps.uninstall(found[0], found, storage)


@pytest.mark.needs_display
def test_the_maps_page_shows_a_map_that_cannot_be_read_without_buttons(navigator):
    from gi.repository import Gtk

    from openexpress.gui.app import MainWindow
    from openexpress.gui.window_tasks import descendants

    storage = LocalStorage(navigator)
    win, _reloads = maps_window(storage)
    found, count = maps.read_maps({"maps": [
        _map("Made-up City Navigator", ".System/Map/D0000001A.img")]})
    maps.mark_waiting(storage, found)  # as the window's check does before the page
    MainWindow.show_maps(win, (found, count))
    (on_device,) = win.sections["maps"].groups
    (row,) = [r for r in on_device.rows if r.get_title() == "Made-up City Navigator"]
    assert row.get_subtitle().endswith(
        "\nCannot be read: openExpress cannot see the folder of its files on this device")
    assert not [w for w in descendants(row) if isinstance(w, Gtk.Button)]


# --- the folders not seen, read through a session of its own ------------------------------------
#
# The made-up navigator over MTP (OverUsb): ordinary MTP shows its folder without .System; its own
# USB session (navigator_session) shows .System through Garmin's listing, with the software of
# another size than offered, the time zone map at the size offered, the map's file and a folder of
# logs no update or map needs.

OLDER_SYSTEM = b"older made-up navigator software, of another size"
MAP_FILE = b"MAPS"  # at the size the map service gives (_map)


class _Session(FakeDevice):
    """The navigator's own USB session; `listed`: (operation, folder) of each listing asked;
    `fail_at`: a folder whose listing fails as a device unplugged meanwhile does; `steps`:
    what changed or read a file, in order; `corrupt_reads`: a file read back with other
    bytes."""

    def __init__(self):
        super().__init__()
        self.supported = list(DRIVE_53_OPERATIONS)
        self.listed, self.fail_at, self.steps, self.corrupt_reads = [], None, [], False
        self.parts = []  # (offset, size) of each part of a file read back

    def _handle(self, operation, params, data):
        if operation in (ptp.GET_OBJECT_HANDLES, ptp.GARMIN_GET_OBJECT_IDS):
            parent = params[2] if operation == ptp.GET_OBJECT_HANDLES else params[1]
            self.listed.append((operation, parent))
            if self.fail_at is not None and parent == self.handle(self.fail_at):
                raise ptp.PtpError("No such device (it may have been disconnected)")
        elif operation == ptp.DELETE_OBJECT and params[0] in self.objects:
            self.steps.append(("delete", self.path_of(params[0])))
        elif operation == ptp.SEND_OBJECT_INFO:
            name, _end = ptp.read_string(data, 52)
            self.steps.append(("info", f"{self.path_of(params[1])}/{name}"))
        elif operation == ptp.SEND_OBJECT:
            self.steps.append(("send",))
        elif operation == ptp.GET_OBJECT and params[0] in self.objects:
            self.steps.append(("read", self.path_of(params[0])))
            answer = super()._handle(operation, params, data)
            if self.corrupt_reads and answer[0] == ptp.OK:
                return ptp.OK, bytes(b ^ 1 for b in answer[1])
            return answer
        elif operation == ptp.GET_PARTIAL_OBJECT and params[0] in self.objects:
            self.parts.append((params[1], params[2]))
            answer = super()._handle(operation, params, data)
            if self.corrupt_reads and answer[0] == ptp.OK:
                return ptp.OK, bytes(b ^ 1 for b in answer[1]), answer[2]
            return answer
        return super()._handle(operation, params, data)

    def data_at(self, path: str):
        handle = self.handle(path)
        return self.objects[handle].data if handle is not None else None

    def path_of(self, handle) -> str:
        parts = []
        while handle != ptp.ROOT:
            parts.append(self.objects[handle].name)
            handle = self.objects[handle].parent
        return "/".join(reversed(parts)) or "/"

    def folders_listed(self) -> set[str]:
        return {self.path_of(parent) for _operation, parent in self.listed}

    def both_listings(self, path: str) -> bool:
        handle = ptp.ROOT if path == "/" else self.handle(path)
        return {(ptp.GET_OBJECT_HANDLES, handle), (ptp.GARMIN_GET_OBJECT_IDS, handle)} <= set(
            self.listed)


def navigator_session(software=OLDER_SYSTEM):
    fake = _Session()
    fake.add("GARMIN/GarminDevice.xml", b"<Device/>")
    fake.add("Voice")
    fake.add("Vehicle")
    fake.add("Demo/Tour.mp4", b"a demo the device plays")  # never touched
    for folder in (".System", ".System/Map", ".System/Diag"):
        fake.add(folder, hidden=True)
    for path, data in ((".System/B000000.update", software), (".System/gmaptz.img", b"tz"),
                       (".System/Map/D0000001A.img", MAP_FILE),
                       (".System/Diag/events.log", b"never listed")):
        fake.add(path, data, hidden=True)
    return fake


class OverUsb(LocalStorage):
    """The made-up device over MTP: ordinary MTP shows its folder; `fake` answers a session of
    its own (None: none can be opened here); `sessions` counts them."""

    def __init__(self, root, fake=None):
        super().__init__(root)
        self.device_uri = "mtp://made-up-navigator/"
        self.usb = types.SimpleNamespace(bus=1, address=7, usb_id="091e:0000")
        self.fake, self.sessions, self.blocked = fake, 0, False

    def usb_blocked(self):
        return self.blocked

    def exclusive_ptp(self):
        if self.fake is None:
            return None

        @contextlib.contextmanager
        def opened():
            self.sessions += 1
            self.fake.reconnect()
            with ptp.Session(self.fake) as session:
                yield session

        return opened()


CITY_NAVIGATOR = ".System/Map/D0000001A.img"


def _offered_from(storage, answer=None):
    from openexpress import service

    device = service.read_device(storage, found_now=False)
    return updates.offered(storage, lower_keys(answer or server_answer()), device,
                           forget_installed=False)


def _maps_from(storage, *entries):
    found, _count = maps.read_maps({"maps": list(entries) or [
        _map("Made-up City Navigator", CITY_NAVIGATOR)]})
    maps.mark_waiting(storage, found)
    return found


def _needed(items, found):
    return sorted({*updates.hidden_needed(items), *maps.hidden_needed(found)}, key=str.casefold)


def test_the_folders_not_seen_are_read_through_a_session_of_its_own(navigator):
    fake = navigator_session()
    storage = OverUsb(navigator, fake)
    items, found = _offered_from(storage), _maps_from(storage)
    assert _needed(items, found) == [".System", ".System/Map"]
    listing = hidden_folders.read(storage, _needed(items, found), "updates and maps go there")
    assert listing.state == hidden_folders.READ and storage.sessions == 1
    assert hidden_folders.kept(storage) is listing
    # Only the folders needed, each by the ordinary listing and Garmin's together.
    assert fake.folders_listed() == {"/", ".System", ".System/Map"}
    assert all(fake.both_listings(p) for p in ("/", ".System", ".System/Map"))
    # Names and sizes only: nothing read, written or removed.
    assert [fake.sent(op) for op in (ptp.GET_OBJECT, ptp.SEND_OBJECT_INFO, ptp.SEND_OBJECT,
                                     ptp.DELETE_OBJECT)] == [0, 0, 0, 0]


def test_what_was_read_tells_the_updates_by_name_and_size(navigator):
    storage = OverUsb(navigator, navigator_session())
    hidden_folders.read(storage, [".System"], "test")
    by = _by_part(_offered_from(storage))
    tz, system = by["006-D9001-00"], by["006-B0000-00"]
    # The time zone map at the size offered: on the device, its contents not compared.
    assert (tz.hidden_read, tz.hidden_size_same, tz.on_device) == (True, True, True)
    # The device's software of another size there: still an update.
    assert (system.hidden_read, system.hidden_size_same, system.on_device) == (True, False, False)
    # Read, never written.
    assert system.hidden_folder == tz.hidden_folder == ".System"
    pending = updates.pending(list(by.values()))
    assert [u.part_number for u in pending] == ["006-B0000-00", "006-D9004-00"]
    assert [u.part_number for u in updates.installable(pending)] == ["006-B0000-00",
                                                                     "006-D9004-00"]


def test_the_device_s_software_at_the_size_offered_stays_an_update(navigator):
    storage = OverUsb(navigator, navigator_session(software=SYSTEM))
    hidden_folders.read(storage, [".System"], "test")
    system = _by_part(_offered_from(storage))["006-B0000-00"]
    # Its contents not compared: taken neither for installed nor for copied and waiting.
    assert system.hidden_size_same is True and not system.on_device
    assert updates.pending([system]) == [system] and updates.staged([system]) == []


def test_a_session_refused_keeps_the_safeguards(navigator, downloads):
    storage = OverUsb(navigator)  # no session of its own here
    listing = hidden_folders.read(storage, [".System"], "test")
    assert listing.state == hidden_folders.NO_SESSION
    by = _by_part(_offered_from(storage))
    system = by["006-B0000-00"]
    assert system.hidden_folder == ".System" and not system.hidden_read
    assert system.hidden_session == hidden_folders.NO_SESSION
    assert not by["006-D9001-00"].on_device  # nothing said of what cannot be read
    with pytest.raises(updates.UpdateError, match="which openExpress cannot see"):
        updates.install([system], storage)
    assert downloads == []


@pytest.mark.parametrize("fail_at", ["/", ".System", ".System/Map"])
def test_a_device_unplugged_during_the_session_keeps_the_safeguards(navigator, fail_at):
    fake = navigator_session()
    fake.fail_at = fail_at
    storage = OverUsb(navigator, fake)
    listing = hidden_folders.read(storage, [".System", ".System/Map"], "test")
    assert listing.state == hidden_folders.FAILED and listing.entries(".System") is None
    by = _by_part(_offered_from(storage))
    assert not by["006-D9001-00"].on_device and not by["006-B0000-00"].hidden_read
    (city,) = _maps_from(storage)
    assert city.unseen and city.waiting is None


def test_a_device_not_mounted_again_after_the_session_is_said(navigator):
    from openexpress.device.storage import RemountFailed

    storage = OverUsb(navigator, navigator_session())
    real = storage.exclusive_ptp

    @contextlib.contextmanager
    def not_mounted_again():
        with real() as session:
            yield session
        raise RemountFailed("Unplug the device and plug it in again.")

    storage.exclusive_ptp = not_mounted_again
    with pytest.raises(RemountFailed):
        hidden_folders.read(storage, [".System"], "test")
    assert hidden_folders.kept(storage) is None  # read again at the next look


WATCHES = {"fēnix 8": ("Otd/", "bk_obj.csv"), "Edge 1050": ("JOURNAL/", "autorun.inf")}


def watch_answer():
    """Updates of a made-up watch: its software and a part, both in its visible GARMIN folder."""
    return {"updates": [
        _entry("006-B0000-00", "Made-up Watch", "GARMIN", "GUPDATE.GCD", SYSTEM, primary=True),
        _entry("006-D9005-00", "Made-up Part", "GARMIN\\RemoteSW", "part.gcd", b"part")]}


def _window(monkeypatch, storage, answer, map_entries, run=None):
    """A stand-in window with the device just opened: the first checks of the device page
    (updates and maps) done at once, the server's answers given, run_task done at once."""
    from fakes import RunNow

    from openexpress import service

    device = service.read_device(storage, found_now=False)
    client = types.SimpleNamespace(
        software_updates=lambda raw, include_reinstalls=False: lower_keys(answer))
    monkeypatch.setattr(maps, "check", lambda *a, **kw: maps.read_maps(
        {"maps": list(map_entries)}))
    s = service.DeviceSession(storage=storage, device=device, settings=None, client=client)
    shown = {}
    win = stand_in(
        session=s, run_task=run or RunNow(), progress=None, map_row=object(),
        sections={"updates": types.SimpleNamespace(loaded=False),
                  "maps": types.SimpleNamespace(loaded=False)},
        load_section=lambda key, force=False: shown.setdefault("loaded", []).append(key),
        _set_update_state=lambda new, staged=(), not_installed=(): (
            shown.__setitem__("updates", [u.part_number for u in new]),
            shown.__setitem__("update checks", shown.get("update checks", 0) + 1)),
        _set_map_state=lambda result, no_maps=False: shown.__setitem__(
            "maps", [(m.name, m.unseen) for m in result[0]]),
        in_background=lambda work, done, failed=None: done(work()))
    window_methods(win, ["_check_updates_quietly", "_check_maps_quietly"])
    return win, s, shown


def _first_checks(win) -> None:
    """The device page's first checks, both started at once (show_device): the update check
    ends first."""
    win._map_check_session = win.session
    win._check_updates_quietly()
    win._map_check_session = None
    win._check_maps_quietly()


@pytest.mark.parametrize("hidden", WATCHES.values(), ids=WATCHES)
def test_a_watch_hiding_objects_at_its_top_opens_no_session(device_root, hidden, monkeypatch):
    fake = FakeDevice()
    for name in hidden:
        fake.add(name.rstrip("/"), None if name.endswith("/") else b"x", hidden=True)
    storage = OverUsb(device_root, fake)
    storage.exclusive_ptp = lambda: pytest.fail("a USB session of its own was opened")
    items = _offered_from(storage, watch_answer())
    found = _maps_from(storage, _map("Made-up Topo", "GARMIN/Maps/T.img"))
    assert updates.hidden_needed(items) == [] and maps.hidden_needed(found) == []
    assert all(u.hidden_folder is None for u in items) and found[0].hidden_folder is None
    # The window's first checks after the opening: nothing to read either.
    win, _s, shown = _window(monkeypatch, storage, watch_answer(),
                             [_map("Made-up Topo", "GARMIN/Maps/T.img")])
    _first_checks(win)
    assert win.run_task.calls == [] and shown["updates"] == ["006-B0000-00", "006-D9005-00"]


def test_the_window_reads_once_both_first_checks_are_done(navigator, monkeypatch,
                                                           in_the_window):
    fake = navigator_session()
    storage = OverUsb(navigator, fake)
    counted = []
    in_the_window("count_again", lambda win, s: counted.append(s))
    win, s, shown = _window(monkeypatch, storage, server_answer(),
                            [_map("Made-up City Navigator", CITY_NAVIGATOR)])
    # The update check ends while the map check still runs: nothing read yet.
    win._map_check_session = s
    win._check_updates_quietly()
    assert win.run_task.calls == [] and storage.sessions == 0
    win._map_check_session = None
    win._check_maps_quietly()
    assert win.run_task.titles == ["Reading the folder .System…"]
    assert storage.sessions == 1 and fake.folders_listed() == {"/", ".System", ".System/Map"}
    # Then the checks again with what was read (the time zone map is there), the device counted
    # again for Device Status.
    assert shown["updates"] == ["006-B0000-00", "006-D9004-00"] and shown["update checks"] == 2
    assert shown["maps"] == [("Made-up City Navigator", False)]
    assert counted == [s]
    # Checks of the same connection later, and a new opening of it: no session again.
    win._check_updates_quietly()
    win._check_maps_quietly()
    from openexpress import service

    win.session = service.DeviceSession(storage=OverUsb(navigator, fake), device=s.device,
                                        settings=None, client=s.client)
    win._check_updates_quietly()
    assert storage.sessions == 1 and win.session.storage.sessions == 0
    assert len(win.run_task.calls) == 1


def test_check_again_reads_the_folders_anew(navigator, monkeypatch, in_the_window):
    from openexpress.gui.maps_page import check_maps_again
    from openexpress.gui.updates_page import check_updates_again

    fake = navigator_session()
    storage = OverUsb(navigator, fake)
    in_the_window("count_again", lambda win, s: None)
    win, _s, shown = _window(monkeypatch, storage, server_answer(),
                             [_map("Made-up City Navigator", CITY_NAVIGATOR)])
    _first_checks(win)
    assert storage.sessions == 1
    win.sections["updates"].loaded = True  # the page is shown
    check_updates_again(win)
    # Read anew, then the page that was shown read again.
    assert storage.sessions == 2 and shown["loaded"] == ["updates"]
    win.sections["maps"].loaded = True
    check_maps_again(win)
    assert storage.sessions == 3 and shown["loaded"][-2:] == ["updates", "maps"]


def test_the_window_shows_nothing_of_a_reading_ended_by_an_unplug(navigator, monkeypatch,
                                                                    in_the_window):
    from fakes import RunNow

    counted = []
    in_the_window("count_again", lambda win, s: counted.append(s))
    fake = navigator_session()
    fake.fail_at = ".System/Map"
    storage = OverUsb(navigator, fake)
    holder = {}
    run = RunNow(before_done=lambda: setattr(holder["win"], "session", None))  # unplugged
    win, _s, shown = _window(monkeypatch, storage, server_answer(),
                             [_map("Made-up City Navigator", CITY_NAVIGATOR)], run=run)
    holder["win"] = win
    _first_checks(win)
    assert storage.sessions == 1 and run.errors == []
    # Closed meanwhile: nothing checked or counted again for it.
    assert counted == [] and shown["update checks"] == 1


def test_the_flatpak_without_usb_says_why_it_cannot_read(navigator):
    storage = OverUsb(navigator)
    storage.blocked = True
    listing = hidden_folders.read(storage, [".System"], "test")
    assert listing.state == hidden_folders.NO_USB
    system = _by_part(_offered_from(storage))["006-B0000-00"]
    assert updates.not_read_text(system) == (
        "The Flatpak version has no access to USB devices, so it cannot read the folder "
        ".System. Give it the permission (see the README) or use the version from source.")


# --- maps in a folder read ------------------------------------------------------------------------


def test_a_map_in_a_folder_read_has_its_state_and_is_not_written_without_the_trial(navigator):
    fake = navigator_session()
    storage = OverUsb(navigator, fake)
    found = _maps_from(storage)
    assert found[0].unseen and maps.hidden_needed(found) == [".System/Map"]
    hidden_folders.read(storage, maps.hidden_needed(found), "test")
    (city,) = _maps_from(storage)
    # Its file is there at its size: listed, not removed, no longer "cannot be read".
    assert not city.unseen and city.waiting is None and city.hidden_folder == ".System"
    assert "cannot be read" not in city.status
    assert not maps.can_install(city)
    assert maps.cannot_install_message(city) == (
        "Map “Made-up City Navigator” cannot be installed: openExpress does not install maps "
        "into the folder .System yet.")
    with pytest.raises(maps.MapError) as refused:
        maps.uninstall(city, [city], storage)
    assert str(refused.value) == ("Map “Made-up City Navigator” cannot be removed: openExpress "
                                  "does not remove maps from the folder .System yet.")
    assert fake.sent(ptp.DELETE_OBJECT) == 0


def test_a_new_map_into_a_folder_not_seen_is_refused_before_anything(navigator, monkeypatch):
    from openexpress import download

    monkeypatch.setattr(download, "fetch", lambda *a, **kw: pytest.fail("downloaded"))
    storage = OverUsb(navigator)
    entry = {**_map("Made-up New Region", ".System/Map/D0000002A.img"), "installationstate": 0,
             "isreinstall": False}
    (new,) = _maps_from(storage, entry)
    assert new.hidden_folder == ".System" and not maps.can_install(new)
    with pytest.raises(maps.MapError, match="does not install maps into the folder .System yet"):
        maps.install(new, [new], storage, activate=lambda identifier: pytest.fail("activated"))
    assert not any(p.name.casefold() == ".system" for p in navigator.iterdir())


# --- Device Status ----------------------------------------------------------------------------

NAVIGATOR_TYPES = """
  <DataType><Name>NavigationMaps</Name>
   <File><Location><Path>.System/Map</Path><FileExtension>img</FileExtension></Location>
    <TransferDirection>InputToUnit</TransferDirection></File></DataType>
  <UpdateFile><PartNumber>006-B0000-00</PartNumber><Version><Major>8</Major><Minor>50</Minor></Version>
   <Path>.System</Path><FileName>B000000.update</FileName></UpdateFile>
"""


def _xml_with(root, block):
    """`root`, its GarminDevice.xml with `block` added at the end of what it declares."""
    xml = root / "GARMIN" / "GarminDevice.xml"
    xml.write_text(xml.read_text().replace("</MassStorageMode>", block + "</MassStorageMode>"))
    return root


def test_device_status_counts_what_was_read_in_the_folders_not_seen(navigator):
    from openexpress import history, service

    storage = OverUsb(_with_types(navigator), navigator_session())
    device = service.read_device(storage, found_now=False)
    before = history.count(device, storage)
    assert (before["maps"], before["updates"]) == (0, 0)  # not seen: nothing counted
    hidden_folders.read(storage, [".System", ".System/Map"], "test")
    after = history.count(device, storage)
    assert (after["maps"], after["updates"]) == (len(MAP_FILE), len(OLDER_SYSTEM))
    assert storage.sessions == 1  # counted from what the one session read


@pytest.mark.parametrize("language", ["en", "pl"])
def test_the_words_of_a_folder_not_seen_in_both_languages(language):
    with in_language(language):
        u = updates.parse_updates(lower_keys(server_answer()))[0]
        u.hidden_folder = ".System"
        texts = [updates.not_installed_here_text(u), updates.hidden_software_text(".System"),
                 updates.add_ons_text(2)]
    expected = {
        "en": [("Not installed: Made-up Drive 9.00 goes to the folder .System, which openExpress "
                "cannot see on the device."), NOTICE, ADD_ONS],
        "pl": [("Nie zainstalowano: Made-up Drive 9.00 trafia do folderu .System, którego "
                "openExpress nie widzi na urządzeniu."),
               ("openExpress nie widzi folderu .System, w\u00a0którym to urządzenie trzyma swoje "
                "oprogramowanie, więc niczego tam nie instaluje. Niektóre urządzenia, np. "
                "nawigacje samochodowe, ukrywają go przed zwykłym połączeniem USB."),
               ("Pominięto 2 dodatki, których nie ma na urządzeniu: openExpress jeszcze nie "
                "instaluje dodatków.")]}
    assert texts == expected[language]


# --- what the pages and the command line say of a folder read -------------------------------------

NO_SESSION_LINE = ("openExpress cannot open its own USB session with this device, so it cannot "
                   "read the folder .System.")


@pytest.mark.needs_display
def test_the_updates_page_after_the_reading_offers_what_goes_there(navigator):
    from gi.repository import Adw, Gtk

    from openexpress.gui.window_tasks import descendants

    storage = OverUsb(navigator, navigator_session())
    hidden_folders.read(storage, [".System"], "test")
    status, available, advanced = _updates_page(_offered_from(storage))
    top = _texts(status.rows[0])
    assert "Install" in top and NOTICE not in top
    assert [r.get_title() for r in available.rows] == ["New Part"]
    # The time zone map at the size offered is among the components on the device, as on a
    # watch: nothing said of its folder.
    (components,) = [r for r in advanced.rows if isinstance(r, Adw.ExpanderRow)]
    (tz,) = [r for r in descendants(components)
             if isinstance(r, Adw.ActionRow) and r.get_title() == "Time zone map"]
    assert "folder" not in tz.get_subtitle()
    assert [w.get_label() for w in descendants(tz) if isinstance(w, Gtk.Button)] == [
        "Reinstall"]


@pytest.mark.needs_display
def test_the_updates_page_says_why_the_folder_was_not_read(navigator):
    storage = OverUsb(navigator)  # no session of its own here
    hidden_folders.read(storage, [".System"], "test")
    status, *_groups = _updates_page(_offered_from(storage))
    top = _texts(status.rows[0])
    assert NOTICE in top and NO_SESSION_LINE in top and "Install" not in top


@pytest.mark.needs_display
def test_the_maps_page_shows_a_map_read_with_its_state_and_no_buttons(navigator):
    from gi.repository import Gtk

    from openexpress.gui.app import MainWindow
    from openexpress.gui.window_tasks import descendants

    storage = OverUsb(navigator, navigator_session())
    hidden_folders.read(storage, [".System/Map"], "test")
    win, _reloads = maps_window(storage)
    found, count = maps.read_maps({"maps": [_map("Made-up City Navigator", CITY_NAVIGATOR)]})
    maps.mark_waiting(storage, found)
    MainWindow.show_maps(win, (found, count))
    (on_device,) = win.sections["maps"].groups
    (row,) = [r for r in on_device.rows if r.get_title() == "Made-up City Navigator"]
    assert row.get_subtitle().endswith(
        "\nopenExpress does not install maps into the folder .System yet")
    assert "Cannot be read" not in row.get_subtitle()
    assert not [w for w in descendants(row) if isinstance(w, Gtk.Button)]


def test_the_command_line_reads_the_folder_once_and_says_what_it_read(offered_to, monkeypatch,
                                                                       capsys):
    from openexpress import cli

    fake = navigator_session()
    storage = OverUsb(offered_to, fake)
    monkeypatch.setattr(cli, "open_storage", lambda args: storage)
    assert main(["updates"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert storage.sessions == 1
    assert "Available updates: 2" in out and "Already on the device: 1" in out
    # Listed as any update: nothing said of the folder it goes to.
    assert not [line for line in out if "trial" in line]
    assert fake.sent(ptp.SEND_OBJECT_INFO) == fake.sent(ptp.DELETE_OBJECT) == 0


def test_the_command_line_says_why_it_could_not_read_the_folder(offered_to, capsys):
    assert main(["--path", str(offered_to), "updates"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert NOTICE in out and NO_SESSION_LINE in out


def test_the_command_line_lists_a_map_read_and_never_removes_it(navigator, monkeypatch, capsys):
    from openexpress import cli

    storage = OverUsb(navigator, navigator_session())
    monkeypatch.setattr(cli, "open_storage", lambda args: storage)
    monkeypatch.setattr(maps, "check", lambda *a, **kw: maps.read_maps(
        {"maps": [_map("Made-up City Navigator", CITY_NAVIGATOR)]}))
    assert main(["maps"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert "    openExpress does not install maps into the folder .System yet." in out
    assert storage.sessions == 1
    assert main(["maps", "remove", "Made-up City Navigator", "--apply"]) == 1
    said = capsys.readouterr()
    assert ("Map “Made-up City Navigator” cannot be removed: openExpress does not remove maps "
            "from the folder .System yet.") in said.out + said.err
    assert storage.fake.sent(ptp.DELETE_OBJECT) == 0 and not said.out.startswith("Removing")


@pytest.mark.parametrize("language", ["en", "pl"])
def test_the_words_of_a_folder_read_in_both_languages(language):
    with in_language(language):
        u = updates.parse_updates(lower_keys(server_answer()))[0]
        u.hidden_folder, u.hidden_session = ".System", hidden_folders.READ
        m = maps.read_maps({"maps": [_map("Made-up City Navigator", CITY_NAVIGATOR)]})[0][0]
        m.hidden_folder = ".System"
        texts = [hidden_folders.reading_text([".System"]),
                 hidden_folders.no_session_text(".System", hidden_folders.NO_SESSION),
                 maps.cannot_install_reason(m), maps.removal_refused(m)]
    expected = {
        "en": ["Reading the folder .System…", NO_SESSION_LINE,
               "openExpress does not install maps into the folder .System yet",
               ("Map “Made-up City Navigator” cannot be removed: openExpress does not remove maps "
                "from the folder .System yet.")],
        "pl": ["Odczytywanie folderu .System…",
               ("openExpress nie może otworzyć własnej sesji USB z\u00a0tym urządzeniem, więc nie "
                "odczyta folderu .System."),
               "openExpress jeszcze nie instaluje map w\u00a0folderze .System",
               ("Nie można usunąć mapy „Made-up City Navigator”: openExpress jeszcze nie usuwa map "
                "z\u00a0folderu .System.")]}
    assert texts == expected[language]


# --- the window and the command line read and count the same ----------------------------------


def _with_types(root):
    """The made-up navigator's GarminDevice.xml naming its maps in .System/Map and its software
    in .System, as Device Status counts them."""
    return _xml_with(root, NAVIGATOR_TYPES)


def test_a_device_found_to_hide_a_top_folder_is_remembered_so(navigator):
    from openexpress import known_devices, service

    storage = OverUsb(navigator, navigator_session())
    device = service.read_device(storage, found_now=True)  # remembered, as a command does
    hidden_folders.read(storage, [".System"], "test", unit_id=device.unit_id)
    assert known_devices.hidden_tops(device.unit_id) == (".System",)


def test_the_folders_read_are_those_needed_and_what_the_xml_names_under_them(navigator):
    from openexpress import service

    device = service.read_device(LocalStorage(_with_types(navigator)), found_now=False)
    assert hidden_folders.folders_to_read(device, [".System"]) == [".System", ".System/Map"]
    assert hidden_folders.folders_to_read(device, []) == []  # nothing needed, nothing known


def test_the_command_line_counts_what_the_window_counts_after_the_reading(offered_to,
                                                                         monkeypatch):
    from openexpress import cli, history, service

    _with_types(offered_to)
    storage = OverUsb(offered_to, navigator_session())
    monkeypatch.setattr(cli, "open_storage", lambda args: storage)
    assert main(["updates"]) == 0  # reads .System: the device is known to hide it from now on
    device = service.read_device(storage, found_now=False)
    window = history.count(device, storage)  # as the window counts once it has read
    hidden_folders.forget(storage)  # another command: nothing read in it yet
    assert main(["diagnostics"]) == 0
    line = history.read(device.unit_id).lines[-1]
    assert line["categories"]["maps"] == window["maps"] == len(MAP_FILE)
    assert line["categories"]["updates"] == window["updates"]
    assert storage.sessions == 2


@pytest.mark.parametrize("hidden", WATCHES.values(), ids=WATCHES)
def test_a_watch_opens_no_session_for_a_command(device_root, hidden, monkeypatch):
    from openexpress import cli

    fake = FakeDevice()
    for name in hidden:
        fake.add(name.rstrip("/"), None if name.endswith("/") else b"x", hidden=True)
    storage = OverUsb(device_root, fake)
    storage.exclusive_ptp = lambda: pytest.fail("a USB session of its own was opened")
    monkeypatch.setattr(cli, "open_storage", lambda args: storage)
    assert main(["diagnostics"]) == 0


def test_status_online_says_what_the_window_says_after_the_reading(navigator):
    from openexpress import cli, service

    storage = OverUsb(navigator, navigator_session())
    device = service.read_device(storage, found_now=False)
    client = types.SimpleNamespace(
        software_updates=lambda raw, include_reinstalls=False: lower_keys(server_answer()))
    said = cli._status_updates_online(client, types.SimpleNamespace(device=device,
                                                                    storage=storage))
    # As the window once it has read .System: the time zone map is there at its size; the
    # software and the new part are updates.
    assert said == cli._update_words("available", 2, None) and storage.sessions == 1


def test_the_window_reads_what_the_xml_names_under_the_folder_needed(navigator, monkeypatch,
                                                                       in_the_window):
    from openexpress import history

    fake = navigator_session()
    storage = OverUsb(_with_types(navigator), fake)
    in_the_window("count_again", lambda win, s: None)
    win, s, _shown = _window(monkeypatch, storage, server_answer(), [])  # no map listed
    _first_checks(win)
    assert fake.folders_listed() == {"/", ".System", ".System/Map"}
    assert history.count(s.device, storage)["maps"] == len(MAP_FILE)


def test_the_window_reads_the_folders_a_device_is_known_to_hide(navigator, monkeypatch,
                                                                  in_the_window):
    from openexpress import known_devices

    fake = navigator_session()
    storage = OverUsb(_with_types(navigator), fake)
    in_the_window("count_again", lambda win, s: None)
    visible_only = {"updates": [u for u in server_answer()["updates"]
                                if u["installation"]["path"] == "Garmin\\NewPart"]}
    win, s, _shown = _window(monkeypatch, storage, visible_only, [])
    known_devices.remember(s.device.unit_id, "Made-up Drive", "006-B0000-00", "8.50", "mtp")
    known_devices.note_hidden_tops(s.device.unit_id, [".System"])
    _first_checks(win)  # nothing offered there, yet Device Status counts it as the commands do
    assert win.run_task.titles == ["Reading the folder .System…"]
    assert fake.folders_listed() == {"/", ".System", ".System/Map"}


@pytest.mark.needs_display
@pytest.mark.parametrize("language, title", [("en", "Update Cannot Be Installed"),
                                             ("pl", "Nie można zainstalować aktualizacji")])
def test_the_updates_page_title_says_when_nothing_can_be_installed(navigator, language, title):
    storage = OverUsb(navigator, None)  # no session of its own: .System is not read
    offered = _offered_from(storage)
    # The device's software alone (with the add-ons), its folder not read: nothing offered can
    # be installed.
    only_hidden = [u for u in offered if u.part_number not in ("006-D9004-00", "006-D9001-00")]
    with in_language(language):
        status, *_groups = _updates_page(only_hidden)
        none_installable = _texts(status.rows[0])
        status, *_groups = _updates_page(offered)
        one_installable = _texts(status.rows[0])
    assert title in none_installable and title not in one_installable


# --- own maps: never into a folder the device hides --------------------------------------------

OWN_MAPS_HIDDEN = """
  <DataType><Name>NMaps</Name>
   <File><Location><Path>.System/Map</Path><FileExtension>img</FileExtension></Location>
    <TransferDirection>InputToUnit</TransferDirection></File></DataType>
"""


def _own_maps_in_system(root):
    return _xml_with(root, OWN_MAPS_HIDDEN)


def _map_file(tmp_path):
    """A made-up map file of the user's: the header the program reads, a name in it."""
    from openexpress import ownmaps

    data = bytearray(ownmaps.HEADER_SIZE * 2)
    data[:ownmaps.HEADER_SIZE] = bytes(ownmaps.HEADER_SIZE)
    local = tmp_path / "Made_up_map.img"
    local.write_bytes(bytes(data))
    return local


def test_an_own_map_is_never_written_into_a_folder_the_device_hides(navigator, tmp_path):
    from openexpress import ownmaps, service

    storage = OverUsb(_own_maps_in_system(navigator), navigator_session())
    device = service.read_device(storage, found_now=False)
    overview = ownmaps.overview(device, storage, [])
    assert overview.hidden_folder == ".System"
    with pytest.raises(maps.MapError, match="does not install maps into the folder .System yet"):
        ownmaps.plan_add(device, storage, [], _map_file(tmp_path))
    assert not any(p.name.casefold() == ".system" for p in navigator.iterdir())


def test_an_own_map_of_a_watch_goes_where_it_did(device_root):
    from openexpress import ownmaps, service

    storage = LocalStorage(device_root)
    device = service.read_device(storage, found_now=False)
    assert ownmaps.overview(device, storage, []).hidden_folder is None


@pytest.mark.needs_display
def test_the_maps_page_offers_no_own_map_for_a_folder_the_device_hides(navigator):
    from gi.repository import Gtk

    from openexpress import ownmaps, service
    from openexpress.gui.app import MainWindow
    from openexpress.gui.window_tasks import descendants

    storage = OverUsb(_own_maps_in_system(navigator), navigator_session())
    win, _reloads = maps_window(storage)
    win.session.device = service.read_device(storage, found_now=False)
    win.choose_map = lambda *args: pytest.fail("a map was chosen")
    MainWindow.show_own_maps(win, ownmaps.overview(win.session.device, storage, []))
    (own,) = win.sections["maps"].groups
    buttons = [w for w in [own.header, *descendants(own.header)] if isinstance(w, Gtk.Button)
               and w.get_label() == "Add Map…"]
    assert buttons and not any(b.get_sensitive() for b in buttons)
    assert any(r.get_subtitle() == "openExpress does not install maps into the folder .System yet"
               for r in own.rows if hasattr(r, "get_subtitle"))


# Its own maps' folder at the top level, which ordinary MTP does not show and which is no
# .System: a folder named like a word, "Map".
OWN_MAPS_AT_THE_TOP = """
  <DataType><Name>NMaps</Name>
   <File><Location><Path>Map</Path><FileExtension>img</FileExtension></Location>
    <TransferDirection>InputToUnit</TransferDirection></File></DataType>
"""


@pytest.mark.needs_display
@pytest.mark.parametrize(("language", "text"), [
    ("en", "openExpress does not install maps into the folder Map yet"),
    ("pl", "openExpress jeszcze nie instaluje map w\u00a0folderze Map")])
def test_a_folder_of_the_device_is_called_a_folder(navigator, language, text):
    from openexpress import ownmaps, service
    from openexpress.gui.app import MainWindow

    storage = OverUsb(_xml_with(navigator, OWN_MAPS_AT_THE_TOP), navigator_session())
    win, _reloads = maps_window(storage)
    win.session.device = service.read_device(storage, found_now=False)
    win.choose_map = lambda *args: pytest.fail("a map was chosen")
    with in_language(language):
        MainWindow.show_own_maps(win, ownmaps.overview(win.session.device, storage, []))
        (own,) = win.sections["maps"].groups
        subtitles = [r.get_subtitle() for r in own.rows if hasattr(r, "get_subtitle")]
        m = types.SimpleNamespace(name="Made-up City Navigator", hidden_folder="Map")
        u = types.SimpleNamespace(title="Time zone map", version="9.00", hidden_folder="Map")
        words = [maps.read_only_text("Map"), maps.cannot_install_message(m),
                 maps.removal_refused(types.SimpleNamespace(unseen=False, **vars(m))),
                 updates.not_installed_here_text(u)]
    assert text in subtitles
    assert all(any(f"{word} Map" in w for word in ("folder", "folderze", "folderu"))
               for w in words), words

# --- an install writes into the hidden folder, in Garmin Express's order ---------------------

TZ_NEW = b"made-up time zones, newer and of another size"
TZ_OLD = b"tz"  # as navigator_session has it


def drive_answer():
    """The software and the time zone map, both in .System, as a Drive 53 EX is offered them."""
    return {"updates": [
        _entry("006-B0000-00", "Made-up Drive", ".System", "B000000.update", SYSTEM,
               primary=True),
        # The server gives the time zone map a license, as the real Drive's question shows.
        _entry("006-D9001-00", "Time zone map", ".System", "gmaptz.img", TZ_NEW,
               eula="https://example.org/made-up-eula")]}


@pytest.fixture
def drive_downloads(monkeypatch, tmp_path):
    content = {"B000000.update": SYSTEM, "gmaptz.img": TZ_NEW}
    monkeypatch.setattr(updates, "cache_path", lambda u: tmp_path / "cache" / u.filename)
    return _fetching(monkeypatch, lambda name, md5: content[name])


def _drive_offered(storage):
    hidden_folders.read(storage, [".System"], "test")
    return _by_part(_offered_from(storage, drive_answer()))


def _both(by):
    return [by["006-B0000-00"], by["006-D9001-00"]]


TZ_STEPS = [("delete", ".System/gmaptz.img"), ("info", ".System/gmaptz.img"), ("send",),
            ("read", ".System/gmaptz.img")]
SYSTEM_STEPS = [("delete", ".System/B000000.update"), ("info", ".System/B000000.update"),
                ("send",), ("read", ".System/B000000.update")]
POWER = ("Keep the device powered until it has installed its software: connect it to its car "
         "cable or a USB charger, or make sure its battery is well charged.")
NOT_WRITTEN_TZ = ("Time zone map was not written: its old file was removed and the new one is not "
                  "on the device. Nothing else was changed; install it again.")


def test_the_install_writes_the_time_zone_map_then_the_software_in_express_order(
        navigator, drive_downloads):
    fake = navigator_session()
    storage = OverUsb(navigator, fake)
    folders_before = sorted(fake.path_of(h) for h, o in fake.objects.items() if o.data is None)
    report = updates.install(_both(_drive_offered(storage)), storage)
    assert [u.part_number for u in report.written] == ["006-D9001-00", "006-B0000-00"]
    # Each: the old file deleted, the new one announced in .System and sent, then read back.
    assert fake.steps == TZ_STEPS + SYSTEM_STEPS
    assert fake.data_at(".System/gmaptz.img") == TZ_NEW
    assert fake.data_at(".System/B000000.update") == SYSTEM
    # Nothing else: no folder made, Demo as it was.
    assert sorted(fake.path_of(h) for h, o in fake.objects.items()
                  if o.data is None) == folders_before
    assert fake.data_at("Demo/Tour.mp4") == b"a demo the device plays"


def test_nothing_goes_into_a_hidden_folder_that_was_not_read(navigator, drive_downloads):
    fake = navigator_session()
    storage = OverUsb(navigator, fake)
    by = _by_part(_offered_from(storage, drive_answer()))
    with pytest.raises(updates.UpdateError, match="which openExpress cannot see on the device"):
        updates.install(_both(by), storage)
    assert fake.steps == [] and drive_downloads == []


@pytest.mark.parametrize(("fault", "steps"), [
    # Refused while sent, or of another size on the device: deleted before any reading back.
    ("refused", TZ_STEPS[:3] + [("delete", ".System/gmaptz.img")]),
    ("written short", TZ_STEPS[:3] + [("delete", ".System/gmaptz.img")]),
    ("read back with other bytes", TZ_STEPS + [("delete", ".System/gmaptz.img")]),
])
def test_a_file_not_written_whole_is_removed_and_the_install_stops(navigator, drive_downloads,
                                                                   fault, steps):
    fake = navigator_session()
    if fault == "refused":
        fake.refuse[ptp.SEND_OBJECT] = ptp.GENERAL_ERROR
    elif fault == "written short":
        fake.truncate_written = 1
    else:
        fake.corrupt_reads = True
    storage = OverUsb(navigator, fake)
    with pytest.raises(updates.UpdateError) as failed:
        updates.install(_both(_drive_offered(storage)), storage)
    assert str(failed.value) == NOT_WRITTEN_TZ
    assert fake.steps == steps
    # The new file gone, and the software never touched.
    assert fake.data_at(".System/gmaptz.img") is None
    assert fake.data_at(".System/B000000.update") == OLDER_SYSTEM


def test_a_read_back_refused_ends_the_install_after_the_size(navigator, drive_downloads):
    fake = navigator_session()
    fake.refuse[ptp.GET_OBJECT] = ptp.ACCESS_DENIED
    storage = OverUsb(navigator, fake)
    with pytest.raises(updates.UpdateError) as stopped:
        updates.install(_both(_drive_offered(storage)), storage)
    assert str(stopped.value) == (
        "Time zone map was written and has the right size, but the device did not let "
        "openExpress read it back, so its contents were not compared. openExpress stops here and "
        "writes nothing else.")
    assert fake.data_at(".System/gmaptz.img") == TZ_NEW  # kept, as Garmin Express leaves it
    assert fake.data_at(".System/B000000.update") == OLDER_SYSTEM


def test_not_enough_space_changes_nothing(navigator, drive_downloads):
    fake = navigator_session()
    storage = OverUsb(navigator, fake)
    by = _drive_offered(storage)
    fake.free = 1
    with pytest.raises(updates.UpdateError, match="openExpress removes nothing else to make room"):
        updates.install(_both(by), storage)
    assert fake.steps == [] and fake.data_at(".System/gmaptz.img") == TZ_OLD


def test_the_write_lists_the_folder_afresh(navigator, drive_downloads):
    fake = navigator_session()
    storage = OverUsb(navigator, fake)
    by = _drive_offered(storage)
    # The device replaced its file after the reading: the write finds the one there now.
    del fake.objects[fake.handle(".System/gmaptz.img")]
    fake.add(".System/gmaptz.img", b"another", hidden=True)
    updates.install([by["006-D9001-00"]], storage)
    assert fake.steps == TZ_STEPS and fake.data_at(".System/gmaptz.img") == TZ_NEW


def test_a_folder_not_on_the_device_is_never_made(navigator, tmp_path):
    fake = navigator_session()
    storage = OverUsb(navigator, fake)
    with pytest.raises(hidden_folders.WriteRefused, match="openExpress does not create folders there"):
        hidden_folders.write(storage, ".System/Made-up/x.bin", _on_the_computer(tmp_path, b"x"),
                             hashlib.md5(b"x").hexdigest(), "X", margin=0)
    assert fake.steps == []


def _hidden_folders_log(caplog) -> list[str]:
    """The lines of hidden_folders in the log, as the log file has them (applog.redact)."""
    from openexpress import applog

    return [applog.redact(r.getMessage()) for r in caplog.records
            if r.name == "openexpress.hidden_folders"]


def test_the_log_keeps_the_sizes_of_a_write_whole(navigator, caplog, tmp_path):
    """A size is no identifier: seven digits and more stay in the log, as `updates` writes them."""
    import logging

    fake = navigator_session(software=b"o" * 1_000_001)
    storage = OverUsb(navigator, fake)
    new = b"n" * 1_234_567
    with caplog.at_level(logging.INFO, logger="openexpress.hidden_folders"):
        _write(storage, tmp_path, new)
    lines = _hidden_folders_log(caplog)
    assert [line for line in lines if "<number>" in line] == []
    assert any("writing B000000.update (1_234_567 B)" in line for line in lines)
    assert any("the old B000000.update (1_000_001 B) deleted" in line for line in lines)
    assert any("(1_234_567 B), read back whole" in line for line in lines)


def test_the_log_keeps_the_sizes_of_a_write_refused_for_space(navigator, caplog, tmp_path):
    import logging

    fake = navigator_session()
    storage = OverUsb(navigator, fake)
    fake.free = 2_000_000
    new = b"n" * 3_000_000
    with caplog.at_level(logging.INFO, logger="openexpress.hidden_folders"), \
            pytest.raises(hidden_folders.WriteRefused):
        _write(storage, tmp_path, new)
    (refused,) = [line for line in _hidden_folders_log(caplog) if "not written" in line]
    assert "<number>" not in refused and "3_000_000 B needed" in refused


def test_the_log_names_a_folder_not_on_the_device_as_it_was_asked(navigator, caplog):
    import logging

    storage = OverUsb(navigator, navigator_session())
    with caplog.at_level(logging.INFO, logger="openexpress.hidden_folders"):
        listing = hidden_folders.read(storage, [".System", ".System/MD2", ".System/GPX/Archive"],
                                      "test")
    (read,) = [line for line in _hidden_folders_log(caplog) if "entries" in line]
    assert ".System/GPX/Archive: not on the device" in read
    assert ".System/MD2: not on the device" in read
    # Looked up in any letter case.
    assert listing.knows(".system/md2") and listing.state_of(".SYSTEM/MD2") == hidden_folders.READ


def test_the_updates_and_maps_are_logged_in_full_once_a_connection(navigator, caplog):
    """The window looks at the device three or four times a connection: the updates and the
    maps go to the log in full the first time and when they change, else one short line each;
    a new connection (another place on USB) logs them in full again."""
    import logging

    storage = OverUsb(navigator, navigator_session())
    said = []
    for address in (7, 7, 8):
        storage.usb.address = address
        hidden_folders.read(storage, [".System"], "test")
        with caplog.at_level(logging.INFO, logger="openexpress"):
            caplog.clear()
            _offered_from(storage)
            _maps_from(storage)
        said.append([r.getMessage() for r in caplog.records
                     if r.getMessage().startswith(("updates:", "maps: states", "maps: the same"))])
    first, again, anew = said
    assert any(line.startswith("updates: 5 offered (") for line in first)
    assert any(line.startswith("updates: 2 in a top folder not seen (.System)") for line in first)
    assert any(line.startswith("maps: states: Made-up City Navigator") for line in first)
    assert again == ["updates: 5 offered as before, 1 of them already on the device",
                     "maps: the same states as before"]
    assert anew == first


def test_after_the_install_the_software_waits_for_the_device_then_is_said_not_installed(
        navigator, drive_downloads, monkeypatch):
    from openexpress import history, known_devices, service

    fake = navigator_session()
    storage = OverUsb(navigator, fake)
    device = service.read_device(storage, found_now=True)
    updates.install(_both(_drive_offered(storage)), storage,
                    unit_id=device.unit_id)
    assert known_devices.load()[device.unit_id].copied_updates
    hidden_folders.forget(storage)
    hidden_folders.read(storage, [".System"], "test")
    items = updates.offered(storage, lower_keys(drive_answer()), device)
    # Its file at the size offered, written by openExpress: copied, waiting for the device.
    assert {u.part_number for u in updates.staged(items)} == {"006-D9001-00", "006-B0000-00"}
    # A connection begun after the copy with the same offer: the device did not install it.
    monkeypatch.setattr(history, "connection_started", lambda device, storage: 4e9)
    items = updates.offered(storage, lower_keys(drive_answer()), device)
    assert updates.staged(items) == []
    assert {u.part_number for u in updates.not_installed(items)} == {"006-D9001-00",
                                                                    "006-B0000-00"}


@pytest.mark.parametrize("language", ["en", "pl"])
def test_the_words_of_a_write_in_both_languages(language):
    from openexpress.gui import updates_page

    with in_language(language):
        u = updates.parse_updates(lower_keys(drive_answer()))[1]
        texts = [hidden_folders.not_written_text(u.title, old_removed=True),
                 hidden_folders.contents_not_read_text(u.title),
                 updates_page.replaces_text(u), updates.power_text()]
    expected = {
        "en": [NOT_WRITTEN_TZ,
               ("Time zone map was written and has the right size, but the device did not let "
                "openExpress read it back, so its contents were not compared. openExpress stops "
                "here and writes nothing else."),
               "The update replaces the file gmaptz.img on the device.", POWER],
        "pl": [("Nie zapisano: Time zone map. Stary plik usunięto, a\u00a0nowego nie ma na "
                "urządzeniu. Niczego więcej nie zmieniono; zainstaluj aktualizację ponownie."),
               ("Zapisano: Time zone map\u00a0– rozmiar się zgadza, ale urządzenie nie pozwoliło "
                "openExpressowi odczytać pliku, więc jego zawartości nie porównano. openExpress na "
                "tym kończy i\u00a0niczego więcej nie zapisuje."),
               "Aktualizacja zastąpi plik gmaptz.img na urządzeniu.",
               ("Urządzenie musi mieć zasilanie, dopóki nie zainstaluje oprogramowania: podłącz "
                "je kablem samochodowym lub do ładowarki USB albo upewnij się, że bateria jest "
                "dobrze naładowana.")]}
    assert texts == expected[language]



# --- installing into the folder read, in the window and on the command line ------------------


def test_the_window_installs_into_the_folder_read_as_the_command_line_does(navigator,
                                                                          drive_downloads):
    from openexpress.gui.updates_page import _install_task

    fake = navigator_session()
    storage = OverUsb(navigator, fake)
    chosen = _both(_drive_offered(storage))
    s = types.SimpleNamespace(storage=storage, client=types.SimpleNamespace(http=None),
                              device=types.SimpleNamespace(unit_id=None), mass_storage=False)
    work, _done, _failed = _install_task(stand_in(updates_cache=chosen, progress=None), s,
                                         chosen)
    report, _bike_accessory = work()
    assert [u.part_number for u in report.written] == ["006-D9001-00", "006-B0000-00"]
    assert fake.steps == TZ_STEPS + SYSTEM_STEPS
    assert fake.data_at(".System/B000000.update") == SYSTEM


# --- a file sent as it is read, checked whole or by samples ------------------------------

def _on_the_computer(tmp_path, data: bytes, name: str = "downloaded.bin"):
    """A downloaded file, as the cache holds it."""
    path = tmp_path / name
    path.write_bytes(data)
    return path


def _write(storage, tmp_path, data, **more):
    return hidden_folders.write(storage, ".System/B000000.update", _on_the_computer(tmp_path, data),
                                hashlib.md5(data).hexdigest(), "Made-up Drive", margin=0, **more)


def _byte_steps(told, head):
    return [(s.done, s.total) for s in told if getattr(s, "head", None) == head]


@pytest.fixture
def small_pieces(monkeypatch):
    """Pieces of 1 KiB sent and read; the check by samples on files over 1 000 bytes, by
    blocks of 100 bytes, so that a few kilobytes show what a map of gigabytes would do."""
    monkeypatch.setattr(ptp, "WRITE_CHUNK", 1024)
    monkeypatch.setattr(ptp, "READ_CHUNK", 1024)
    monkeypatch.setattr(hidden_folders, "FULL_CHECK_UP_TO", 1000)
    monkeypatch.setattr(hidden_folders, "CHECK_BLOCK", 100)


def test_a_write_sends_the_file_as_it_is_read_and_tells_its_bytes(navigator, tmp_path,
                                                                    small_pieces):
    fake = navigator_session()
    new = bytes(range(256)) * 10  # 2 560 bytes: three pieces each way
    told = []
    _write(OverUsb(navigator, fake), tmp_path, new, progress=told.append, copying="Copying",
           checking="Checking")
    assert fake.data_at(".System/B000000.update") == new
    assert _byte_steps(told, "Copying") == [(0, 2560), (1024, 2560), (2048, 2560), (2560, 2560)]
    assert _byte_steps(told, "Checking") == [(0, 2560), (1024, 2560), (2048, 2560), (2560, 2560)]
    assert fake.sent(ptp.GET_OBJECT) == 1 and fake.parts == []  # read back whole by default


def test_a_file_over_a_part_is_read_back_whole_in_parts(navigator, tmp_path, small_pieces,
                                                         monkeypatch):
    """A fēnix 8 cut a GetObject of a whole file short about once in three hundred."""
    monkeypatch.setattr(ptp, "READ_PART", 1000)
    fake = navigator_session()
    new = bytes(range(256)) * 10  # 2 560 bytes: three parts
    _write(OverUsb(navigator, fake), tmp_path, new)
    assert fake.sent(ptp.GET_OBJECT) == 0
    assert fake.parts == [(0, 1000), (1000, 1000), (2000, 560)]
    assert fake.data_at(".System/B000000.update") == new


def test_the_log_tells_the_time_of_the_copy_and_of_the_check_apart(navigator, tmp_path, caplog):
    import logging
    import re

    with caplog.at_level(logging.INFO, logger="openexpress.hidden_folders"):
        _write(OverUsb(navigator, navigator_session()), tmp_path, b"n" * 5000)
    lines = _hidden_folders_log(caplog)
    pace = r"\d+\.\d s(, \d+\.\d MB/s)?"
    assert [line for line in lines if re.fullmatch(
        rf"hidden folders: B000000\.update \(5_000 B\) sent in {pace}", line)]
    assert [line for line in lines if re.fullmatch(
        rf"hidden folders: B000000\.update read back whole \(5_000 B\) in {pace}", line)]


def test_a_large_file_is_checked_by_samples_when_asked(navigator, tmp_path, small_pieces, caplog):
    import logging

    fake = navigator_session()
    new = bytes(range(250)) * 12  # 3 000 bytes: 30 blocks of 100
    told = []
    with caplog.at_level(logging.INFO, logger="openexpress.hidden_folders"):
        _write(OverUsb(navigator, fake), tmp_path, new, check=hidden_folders.SAMPLED,
               progress=told.append, checking="Checking")
    # The first and the last block, and 8 others at random, each once; never the whole file.
    assert fake.sent(ptp.GET_OBJECT) == 0 and len(fake.parts) == 10
    assert fake.parts[0] == (0, 100) and fake.parts[-1] == (2900, 100)
    middle = [offset for offset, _size in fake.parts[1:-1]]
    assert len(set(middle)) == 8 and all(o % 100 == 0 and 100 <= o <= 2800 for o in middle)
    assert _byte_steps(told, "Checking")[-1] == (1000, 1000)
    assert fake.data_at(".System/B000000.update") == new
    assert any("read back by samples (10 blocks, 1_000 B of 3_000 B)" in line
               for line in _hidden_folders_log(caplog))


def test_a_sample_with_other_bytes_removes_the_file(navigator, tmp_path, small_pieces):
    fake = navigator_session()
    fake.corrupt_reads = True
    with pytest.raises(hidden_folders.NotWritten, match="its old file was removed"):
        _write(OverUsb(navigator, fake), tmp_path, b"m" * 3000, check=hidden_folders.SAMPLED)
    assert fake.data_at(".System/B000000.update") is None
    assert fake.steps[-1] == ("delete", ".System/B000000.update")


def test_a_file_up_to_the_limit_is_always_read_back_whole(navigator, tmp_path, small_pieces):
    fake = navigator_session()
    _write(OverUsb(navigator, fake), tmp_path, b"s" * 1000, check=hidden_folders.SAMPLED)
    assert fake.sent(ptp.GET_OBJECT) == 1 and fake.parts == []


def test_a_device_that_does_not_read_parts_is_read_back_whole(navigator, tmp_path,
                                                              small_pieces, caplog):
    import logging

    fake = navigator_session()
    fake.refuse[ptp.GET_PARTIAL_OBJECT] = ptp.OPERATION_NOT_SUPPORTED
    new = b"w" * 3000
    with caplog.at_level(logging.INFO, logger="openexpress.hidden_folders"):
        _write(OverUsb(navigator, fake), tmp_path, new, check=hidden_folders.SAMPLED)
    assert fake.sent(ptp.GET_OBJECT) == 1 and fake.data_at(".System/B000000.update") == new
    assert any("does not read parts (0x2005): read back whole instead" in line
               for line in _hidden_folders_log(caplog))


def test_a_file_that_differs_from_the_one_on_the_computer_is_removed(navigator, tmp_path):
    """Read back whole, the device's bytes are compared with the file on the computer too, not
    only with the MD5 the server gave."""
    fake = navigator_session()
    new = b"d" * 4000
    local = _on_the_computer(tmp_path, new)
    original = fake._handle

    def other_bytes(operation, params, data):
        answer = original(operation, params, data)
        if operation == ptp.GET_OBJECT and answer[0] == ptp.OK:
            return ptp.OK, b"e" + answer[1][1:]
        return answer

    fake._handle = other_bytes
    with pytest.raises(hidden_folders.NotWritten):
        hidden_folders.write(OverUsb(navigator, fake), ".System/B000000.update", local, None,
                             "Made-up Drive", margin=0)
    assert fake.data_at(".System/B000000.update") is None


def test_a_write_into_the_folder_read_tells_its_copy_and_its_check(navigator, drive_downloads):
    from openexpress.progress import checking_text, copying_text

    storage = OverUsb(navigator, navigator_session())
    told = []
    updates.install([_drive_offered(storage)["006-D9001-00"]], storage, progress=told.append)
    heads = [getattr(step, "head", step) for step in told]
    assert heads.index(copying_text("Time zone map")) < heads.index(
        checking_text("Time zone map"))


def test_the_done_message_says_to_keep_a_navigator_powered(navigator):
    from openexpress.gui.updates_page import UpdatesPage

    by = _drive_offered(OverUsb(navigator, navigator_session()))
    software = UpdatesPage._update_done_text(updates.InstallReport(written=[by["006-B0000-00"]]))
    time_zones = UpdatesPage._update_done_text(updates.InstallReport(written=[by["006-D9001-00"]]))
    assert software.endswith("\n\n" + POWER) and POWER not in time_zones


def test_the_command_line_writes_into_the_folder_read_without_a_flag(offered_to, monkeypatch,
                                                                     capsys, drive_downloads):
    from openexpress import cli

    fake = navigator_session()
    storage = OverUsb(offered_to, fake)
    monkeypatch.setattr(cli, "open_storage", lambda args: storage)
    answer = lower_keys(drive_answer())
    monkeypatch.setattr(GarminClient, "software_updates",
                        lambda self, raw, include_reinstalls=False: answer)
    assert main(["updates", "--install", "006-D9001-00", "006-B0000-00"]) == 0
    dry = capsys.readouterr().out.splitlines()
    assert any(line.startswith("Dry run: nothing was downloaded or written") for line in dry)
    assert fake.steps == [] and drive_downloads == []
    assert main(["updates", "--install", "006-D9001-00", "006-B0000-00", "--apply"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert "Copied 2 files." in out and POWER in out
    assert fake.steps == TZ_STEPS + SYSTEM_STEPS
    with pytest.raises(SystemExit) as gone:  # no such option
        main(["updates", "--install", "006-D9001-00", "--hidden-folder-trial"])
    assert gone.value.code == 2


@pytest.mark.needs_display
def test_the_updates_page_offers_install_for_the_folder_read(navigator):
    from openexpress.gui import updates_page

    storage = OverUsb(navigator, navigator_session())
    offered = list(_drive_offered(storage).values())
    status, available, *_rest = _updates_page(offered)
    top = _texts(status.rows[0])
    assert "Install" in top
    (tz,) = available.rows
    assert "Install" in _texts(tz)
    assert "cannot see" not in tz.get_subtitle()
    assert updates_page.replaces_text(offered[1]) == (
        "The update replaces the file gmaptz.img on the device.")


# --- after a copy into the folder read: waiting for the device, as on a watch -----------------


def _window_of(storage, offered, version="8.50"):
    """The stand-in window with the Updates page of the made-up Drive as it shows `offered`,
    its server's offer, once the folder is read; `version`: its software's."""
    s = types.SimpleNamespace(storage=storage, client=types.SimpleNamespace(http=None),
                              device=types.SimpleNamespace(unit_id=None,
                                                           description="Made-up Drive",
                                                           software_version_str=version),
                              mass_storage=False)
    win = stand_in(session=s, updates_cache=offered, updates_beta=False, progress=None,
                   sections={"updates": Section()}, busy=False, _action_buttons=[],
                   _set_update_state=lambda new, staged=(), not_installed=(): None,
                   confirm_update=lambda u: None, load_section=lambda *a, **kw: None)
    window_methods(win, ["_update_row", "_updates_status", "_button", "show_updates",
                         "_show_installed"])
    win.show_updates(offered)
    return win, s


def _copy_in_the_window(navigator, parts, in_the_window):
    """The made-up Drive's .System read, the updates `parts` installed from the window and its
    task ended as the window ends it; the window, what the message Done said, and the device."""
    from openexpress.gui.updates_page import _install_task

    said = []
    in_the_window("inform", lambda win, name, heading, body, **kw:
                  said.append((name, heading, body)))
    fake = navigator_session()
    storage = OverUsb(navigator, fake)
    by = _drive_offered(storage)
    win, s = _window_of(storage, list(by.values()))
    work, done, _failed = _install_task(win, s, [by[p] for p in parts])
    done(work())
    return win, said, fake


@pytest.mark.needs_display
@pytest.mark.parametrize(("parts", "still"), [
    (["006-D9001-00", "006-B0000-00"], None), (["006-B0000-00"], "Still available: 1 update")])
def test_done_after_a_copy_into_the_folder_read_counts_only_what_is_left(
        navigator, drive_downloads, in_the_window, parts, still):
    _win, said, fake = _copy_in_the_window(navigator, parts, in_the_window)
    ((name, heading, body),) = said
    assert (name, heading) == ("update-done", "Done")
    last = body.rsplit("\n\n", 1)[-1]
    assert (last if last.startswith("Still available") else None) == still
    assert fake.data_at(".System/B000000.update") == SYSTEM  # there, the device as it was


@pytest.mark.needs_display
def test_after_a_copy_into_the_folder_read_the_page_shows_it_waiting_for_the_device(
        navigator, drive_downloads, in_the_window):
    from gi.repository import Gtk

    from openexpress.gui.window_tasks import descendants

    win, _said, _fake = _copy_in_the_window(navigator, ["006-B0000-00"], in_the_window)
    status, available, *_rest = win.sections["updates"].groups
    top = _texts(status.rows[0])
    # Copied, waiting for the device to take it once unplugged: no Install; the time zone map,
    # not copied, is counted and offered.
    assert top[0] == "Update ready: unplug the device to install it\u00a0· 1 more available"
    assert not {"Install", "Install All", "Reinstall"} & set(top)
    (tz,) = available.rows
    assert tz.get_title() == "Time zone map"
    assert [w.get_label() for w in descendants(tz) if isinstance(w, Gtk.Button)] == ["Install"]
    # Both copied: nothing more to install.
    win, _said, _fake = _copy_in_the_window(navigator, ["006-D9001-00", "006-B0000-00"],
                                            in_the_window)
    top = _texts(win.sections["updates"].groups[0].rows[0])
    assert top[0] == "Updates ready: unplug the device to install them"
    assert not {"Install", "Install All", "Reinstall"} & set(top)


def test_the_command_line_after_a_copy_says_it_waits_for_the_device(offered_to, monkeypatch,
                                                                   capsys, drive_downloads):
    from openexpress import cli

    fake = navigator_session()
    storage = OverUsb(offered_to, fake)
    monkeypatch.setattr(cli, "open_storage", lambda args: storage)
    answer = lower_keys(drive_answer())
    monkeypatch.setattr(GarminClient, "software_updates",
                        lambda self, raw, include_reinstalls=False: answer)
    assert main(["updates", "--install", "006-D9001-00", "006-B0000-00", "--apply"]) == 0
    written = list(fake.steps)
    capsys.readouterr()
    # The same connection: the files are in .System, the device has not taken them yet.
    assert main(["updates"]) == 0
    out = capsys.readouterr().out
    assert "Available updates" not in out and "Software is up to date." not in out
    lines = out.splitlines()
    assert "Updates copied, waiting for the device to install them:" in lines
    assert "Unplug the device to install them." in lines
    assert main(["updates", "--install", "all", "--apply"]) == 0
    assert "Unplug the device to install them." in capsys.readouterr().out.splitlines()
    assert fake.steps == written  # nothing copied again


NAVIGATOR_ADVICE = {
    "en": ("Unplug the device from the computer, connect it to its car cable or a USB charger, "
           "turn it on and wait until it has installed the update. If the update is still listed "
           "when you plug the device in again, a device report helps find out why."),
    "pl": ("Odłącz urządzenie od komputera, podłącz je kablem samochodowym albo do ładowarki USB, "
           "włącz je i\u00a0poczekaj, aż zainstaluje aktualizację. Jeśli po ponownym podłączeniu "
           "aktualizacja nadal będzie na liście, raport o\u00a0urządzeniu pomoże ustalić "
           "dlaczego.")}
WATCH_ADVICE = {
    "en": ("Check the version on the device (Settings → System → About). Ejecting it again does "
           "not help; a device report helps find out why."),
    "pl": ("Sprawdź wersję na urządzeniu (Ustawienia → System → Informacje). Ponowne wysunięcie nie "
           "pomoże; raport o\u00a0urządzeniu pomoże ustalić dlaczego.")}


@pytest.mark.needs_display
def test_plugged_in_again_with_the_copy_not_installed_a_navigator_is_told_what_it_needs(
        navigator, drive_downloads, in_the_window):
    win, _said, _fake = _copy_in_the_window(navigator, ["006-D9001-00", "006-B0000-00"],
                                            in_the_window)
    for u in win.updates_cache:
        if u.copied_at is not None:
            u.unplugged_since_copy = True  # plugged in again: the files still in .System
    win.sections["updates"].clear()
    win.show_updates(win.updates_cache)
    top = _texts(win.sections["updates"].groups[0].rows[0])
    assert top[0] == "Updates Not Installed by the Device"
    assert NAVIGATOR_ADVICE["en"] in top
    assert not [t for t in top if "Settings → System" in t or "Ejecting" in t]
    assert not {"Install", "Install All", "Reinstall"} & set(top)


@pytest.mark.parametrize("language", ["en", "pl"])
def test_the_advice_for_a_copy_not_installed_in_both_languages(language):
    from openexpress.gui.words import not_installed_advice

    with in_language(language):
        texts = [not_installed_advice(hidden_folder=True), not_installed_advice()]
    assert texts == [NAVIGATOR_ADVICE[language], WATCH_ADVICE[language]]


def test_maps_use_of_a_navigator_comes_from_its_folder_read(navigator):
    storage = OverUsb(navigator, navigator_session())
    found = _maps_from(storage)
    assert maps.used_on_device(found) is None  # .System not read: not known
    hidden_folders.read(storage, maps.hidden_needed(found), "test")
    assert maps.used_on_device(_maps_from(storage)) == len(MAP_FILE)


@pytest.mark.parametrize(("language", "text"), [("en", "1 map on the device"),
                                                ("pl", "1 mapa na urządzeniu")])
def test_the_maps_row_counts_no_update_openexpress_cannot_install(navigator, language, text):
    """A newer City Navigator offered for .System: not "1 update available" on the device page
    (openExpress does not install maps there; the Maps page says so), nor "up to date"."""
    from openexpress.gui.app import MainWindow

    storage = OverUsb(navigator, navigator_session())
    newer = _map("Made-up City Navigator", CITY_NAVIGATOR)
    newer["isreinstall"] = False  # a newer version than the one installed
    hidden_folders.read(storage, [".System/Map"], "test")
    found = _maps_from(storage, newer)
    assert found[0].update_available and not maps.can_install(found[0])

    class Row:
        subtitle = None

        def set_subtitle(self, text):
            self.subtitle = text

    win = stand_in(session=types.SimpleNamespace(), map_state=None, map_update_count=0,
                   map_count=0, _map_check_session=None, map_row=Row())
    for name in ("_render_map_state", "_set_map_state"):
        setattr(win, name, types.MethodType(getattr(MainWindow, name), win))
    with in_language(language):
        win._set_map_state((found, 1))
    assert (win.map_update_count, win.map_row.subtitle) == (0, text)


# --- a component on the device installed again into the folder read, as an update ------------
#
# The made-up Drive at 9.00: the software and the time zone map it was offered are installed, and
# the server offers them as reinstalls; their files are in .System at the size it gives.


def reinstall_answer():
    answer = drive_answer()
    for entry in answer["updates"]:
        entry["installation"]["isReinstall"] = True
    return answer


def drive_at_9(navigator):
    fake = navigator_session(software=SYSTEM)
    fake.objects[fake.handle(".System/gmaptz.img")].data = TZ_NEW
    return fake, OverUsb(navigator, fake)


def _reinstalls(storage, read=True):
    if read:
        hidden_folders.read(storage, [".System"], "test")
    return _by_part(_offered_from(storage, reinstall_answer()))


def test_a_reinstall_into_the_folder_read_is_placed_as_an_update(navigator):
    _fake, storage = drive_at_9(navigator)
    unread = _reinstalls(storage, read=False)
    assert all(u.is_reinstall and u.hidden_folder == ".System" for u in unread.values())
    assert updates.hidden_needed(list(unread.values())) == [".System"]  # read before a write
    by = _reinstalls(storage)
    assert all(u.hidden_read and u.hidden_size_same is True for u in by.values())
    assert updates.hidden_needed(list(by.values())) == []


@pytest.mark.needs_display
def test_the_reinstall_row_and_its_question_say_what_is_replaced(navigator, in_the_window):
    from gi.repository import Adw

    from openexpress.gui.app import MainWindow

    asked = []
    in_the_window("confirm", lambda win, name, heading, body, *answers, **kw:
                  asked.append((heading, body, _texts(kw["extra"]) if kw.get("extra") else [])))
    _fake, storage = drive_at_9(navigator)
    by = _reinstalls(storage)
    win, _s = _window_of(storage, list(by.values()), version="9.00")
    status, advanced = win.sections["updates"].groups
    assert _texts(status.rows[0])[0] == "Software is up to date"
    (components,) = [r for r in advanced.rows if isinstance(r, Adw.ExpanderRow)]
    texts = _texts(components)
    assert texts.count("Reinstall") == 2 * 2  # a button of each row (its label twice)
    assert not any("cannot see" in t for t in texts)
    for part in ("006-D9001-00", "006-B0000-00"):
        MainWindow.confirm_update(win, by[part])
    (tz_heading, tz_body, tz_below), (_heading, system_body, system_below) = asked
    assert tz_heading == "Install Update?"
    assert tz_body.endswith("\n\nThe update replaces the file gmaptz.img on the device.")
    assert "License Agreement (EULA)" in tz_below and "License Agreement (EULA)" not in system_below
    assert system_body.endswith("\n\nThe update replaces the file B000000.update on the device.")
    assert updates.SYSTEM_UPDATE_WARNING in system_below
    assert updates.SYSTEM_UPDATE_WARNING not in tz_below


@pytest.mark.needs_display
@pytest.mark.parametrize(("part", "steps", "power"), [
    ("006-D9001-00", TZ_STEPS, False), ("006-B0000-00", SYSTEM_STEPS, True)])
def test_the_window_reinstalls_into_the_folder_read_as_it_installs_an_update(
        navigator, drive_downloads, in_the_window, part, steps, power):
    from openexpress.gui.updates_page import _install_task

    said = []
    in_the_window("inform", lambda win, name, heading, body, **kw:
                  said.append((name, heading, body)))
    fake, storage = drive_at_9(navigator)
    by = _reinstalls(storage)
    win, s = _window_of(storage, list(by.values()), version="9.00")
    work, done, _failed = _install_task(win, s, [by[part]])
    done(work())
    assert fake.steps == steps  # the old file deleted, the new one written and read back
    ((name, heading, body),) = said
    assert (name, heading) == ("update-done", "Done")
    assert body.startswith("Files copied to the device.\n")
    assert body.endswith(POWER) == power and "Still available" not in body
    assert "the software version stays the same" in body
    assert "should no longer be listed" not in body
    # The page after the copy: nothing waits for the device, nothing "not installed".
    status, advanced = win.sections["updates"].groups
    assert _texts(status.rows[0])[0] == "Software is up to date"
    assert [g.title for g in win.sections["updates"].groups] == ["", "Advanced"]


def test_after_a_reinstall_and_an_unplugging_nothing_waits_or_is_not_installed(
        navigator, drive_downloads, monkeypatch):
    from openexpress import history, known_devices, service

    _fake, storage = drive_at_9(navigator)
    device = service.read_device(storage, found_now=True)
    by = _reinstalls(storage)
    report = updates.install([by["006-D9001-00"]], storage, unit_id=device.unit_id)
    assert [u.part_number for u in report.written] == ["006-D9001-00"]
    # The next connection, begun after the copy, with the same offer: as for a watch, a
    # reinstall is no update that waits or that the device did not install.
    monkeypatch.setattr(history, "connection_started", lambda device, storage: 4e9)
    hidden_folders.forget(storage)
    hidden_folders.read(storage, [".System"], "test")
    items = updates.offered(storage, lower_keys(reinstall_answer()), device)
    assert updates.staged(items) == updates.not_installed(items) == updates.pending(items) == []
    assert not known_devices.load()[device.unit_id].copied_updates
    assert updates.state(updates.pending(items), updates.staged(items),
                         updates.not_installed(items)) == "current"


@pytest.mark.parametrize(("part", "steps", "power"), [
    ("006-D9001-00", TZ_STEPS, False), ("006-B0000-00", SYSTEM_STEPS, True)])
def test_the_command_line_reinstalls_into_the_folder_read(navigator, monkeypatch, capsys,
                                                          drive_downloads, part, steps, power):
    from openexpress import cli

    fake, storage = drive_at_9(navigator)
    monkeypatch.setattr(cli, "open_storage", lambda args: storage)
    answer = lower_keys(reinstall_answer())
    monkeypatch.setattr(GarminClient, "software_updates",
                        lambda self, raw, include_reinstalls=False: answer)
    monkeypatch.setattr(GarminClient, "unit_info", lambda self, unit_id, part_number: {})
    assert main(["updates", "--reinstalls"]) == 0
    assert "cannot see" not in capsys.readouterr().out
    assert main(["updates", "--reinstalls", "--install", part, "--apply"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert "Copied 1 file." in out and (POWER in out) == power
    # The same version stays on the device: the steps do not promise it leaves the list.
    assert "  4. Plug it in again if you want to check it: the software version stays" in out
    assert not any("should no longer be listed" in line for line in out)
    assert fake.steps == steps


def test_a_reinstall_into_a_folder_not_read_is_refused_and_says_why(navigator, monkeypatch,
                                                                     capsys, drive_downloads):
    from openexpress import cli

    fake = navigator_session(software=SYSTEM)
    storage = OverUsb(navigator, None)  # no session of its own can be opened here
    monkeypatch.setattr(cli, "open_storage", lambda args: storage)
    answer = lower_keys(reinstall_answer())
    monkeypatch.setattr(GarminClient, "software_updates",
                        lambda self, raw, include_reinstalls=False: answer)
    monkeypatch.setattr(GarminClient, "unit_info", lambda self, unit_id, part_number: {})
    assert main(["updates", "--reinstalls", "--install", "006-D9001-00", "--apply"]) == 1
    captured = capsys.readouterr()
    assert NOT_INSTALLED_TZ.strip() in captured.out + captured.err
    assert fake.steps == [] and drive_downloads == []


# --- an older version than the device's, in the folder it hides ------------------------------------

def _time_zones_listed_newer(navigator, version="9.50"):
    """The navigator's GarminDevice.xml lists its time zone map at a version higher than the
    one Garmin offers (9.00): Garmin offers an older one."""
    major, minor = version.split(".")
    _xml_with(navigator, (
        f"<UpdateFile><PartNumber>006-D9001-00</PartNumber><Version><Major>{major}</Major>"
        f"<Minor>{minor}</Minor></Version><Path>.System</Path><FileName>gmaptz.img</FileName>"
        "</UpdateFile>"))


def test_an_older_version_of_the_same_size_in_the_folder_read_may_be_reinstalled(navigator):
    _time_zones_listed_newer(navigator)
    _fake, storage = drive_at_9(navigator)  # its gmaptz.img at the size offered
    tz = _reinstalls(storage)["006-D9001-00"]
    assert (tz.installed_version, tz.version) == ("9.50", "9.00")
    assert tz.older_offer == updates.OLDER_SAME_SIZE and not updates.reinstall_refused(tz)
    assert updates.older_offer_caption(tz) == (
        "On the device 9.50 · Garmin offers 9.00, the same size")


def test_an_older_version_of_another_size_in_the_folder_read_is_never_written(
        navigator, drive_downloads):
    _time_zones_listed_newer(navigator)
    fake, storage = drive_at_9(navigator)
    fake.objects[fake.handle(".System/gmaptz.img")].data = b"another time zone map"
    tz = _reinstalls(storage)["006-D9001-00"]
    assert tz.older_offer == updates.OLDER_DIFFERS and updates.reinstall_refused(tz)
    with pytest.raises(updates.UpdateError, match="an older version: not reinstalled"):
        updates.install([tz], storage)
    assert drive_downloads == [] and fake.steps == []


def test_an_older_version_in_a_folder_not_read_is_not_compared(navigator):
    _time_zones_listed_newer(navigator)
    _fake, storage = drive_at_9(navigator)
    tz = _reinstalls(storage, read=False)["006-D9001-00"]
    assert tz.older_offer == updates.OLDER_NOT_COMPARED and updates.reinstall_refused(tz)


@pytest.mark.needs_display
def test_a_second_reinstall_on_the_same_page_names_the_file_it_replaces(
        navigator, drive_downloads, in_the_window):
    from openexpress.gui.app import MainWindow
    from openexpress.gui.updates_page import _install_task

    bodies = []
    in_the_window("inform", lambda *args, **kw: None)
    in_the_window("confirm", lambda win, name, heading, body, *answers, **kw:
                  bodies.append(body))
    _fake, storage = drive_at_9(navigator)
    by = _reinstalls(storage)
    win, s = _window_of(storage, list(by.values()), version="9.00")
    work, done, _failed = _install_task(win, s, [by["006-D9001-00"]])
    done(work())
    MainWindow.confirm_update(win, by["006-D9001-00"])  # Reinstall again, the page as it is
    assert bodies[-1].endswith("\n\nThe update replaces the file gmaptz.img on the device.")


# --- a map into the folder the device hides: the trial on the command line ------------------------

NEW_MAP = {".System/Map/D0000002A.img": b"made-up map, part one " * 40,
           ".System/Map/D0000002B.img": b"made-up map, part two " * 25,
           ".System/JCV/D0000002.jcv": b"made-up junction view " * 10}
SHARED = ".System/Map/Shared.img"  # also of another map on the device: never removed
UNLOCK = (".System/D0000002.gma", b"made-up unlock")
TRIAL_NAME = "Made-up City Navigator"


def _trial_map(state=1, remove=(".System/Map/D0000001A.img", SHARED), files=None):
    """The map the server offers: its new files (NEW_MAP) and the old ones it removes."""
    files = NEW_MAP if files is None else files
    return {
        "displayname": TRIAL_NAME, "maptype": "CityNavigator", "installationstate": state,
        "isreinstall": False, "canuninstall": True,
        "release": {"majorversion": 2027, "minorversion": 20},
        "downloadhosts": {"foregroundprimaryhost": "https://example.org/maps/",
                          "failoverhosts": []},
        "filestoremove": [{"filename": p} for p in remove],
        "installoptions": [{"ispreferred": True, "identifier": {"regionpartnumber": "made-up"},
                            "files": [{"filename": path, "datatype": "X", "deliverableoptions": [{
                                "type": 1, "url": posixpath.basename(path),
                                "md5": hashlib.md5(data).hexdigest(), "sizeinbytes": len(data)}]}
                                      for path, data in files.items()]}]}


def _other_map():
    """Another map on the device, which uses SHARED too."""
    return {**_map("Made-up Other Map", SHARED), "installationstate": 1}


def trial_navigator():
    """The navigator with the old version of the map in .System/Map, a file another map uses,
    and a folder JCV, all hidden."""
    fake = navigator_session()
    fake.add(".System/Map/Shared.img", MAP_FILE, hidden=True)
    fake.add(".System/JCV", hidden=True)
    return fake


@pytest.fixture
def map_downloads(monkeypatch):
    """The downloads of the map's files: each by its MD5 from NEW_MAP, written once; what is
    in the cache already is not downloaded again (as download.fetch does)."""
    by_md5 = {hashlib.md5(d).hexdigest(): d for d in NEW_MAP.values()}
    return _fetching(monkeypatch, lambda name, md5: by_md5[md5], again=False)


def _trial_maps(storage, *entries):
    found, _count = maps.read_maps({"maps": list(entries) or [_trial_map(), _other_map()]})
    maps.mark_waiting(storage, found)
    hidden_folders.read(storage, maps.hidden_needed(found), "test")
    maps.mark_waiting(storage, found)
    return found


def _activate(order):
    import base64

    def activate(identifier):
        order.append("activate")
        return {"unlocks": [{"filename": UNLOCK[0], "gma": base64.b64encode(UNLOCK[1]).decode()}]}
    return activate


def _install_trial(storage, found, order=None, waits=None):
    """The trial of `found`'s map; `order`: what it asks of Garmin noted there, `waits`: the
    pauses between tries of a file noted there (else none taken)."""
    (m,) = [m for m in found if m.name == TRIAL_NAME]
    return maps.install(m, found, storage, activate=_activate([] if order is None else order),
                        hidden_trial=True,
                        sleep=(lambda s: None) if waits is None else waits.append)


def test_without_the_trial_a_map_is_never_written_into_the_folder_the_device_hides(navigator,
                                                                                  map_downloads):
    fake = trial_navigator()
    storage = OverUsb(navigator, fake)
    found = _trial_maps(storage)
    (m,) = [m for m in found if m.name == TRIAL_NAME]
    with pytest.raises(maps.MapError, match="does not install maps into the folder .System yet"):
        maps.install(m, found, storage, activate=lambda i: pytest.fail("activated"))
    assert fake.steps == [] and map_downloads == []


def test_the_trial_downloads_and_activates_before_it_changes_the_device(navigator, map_downloads):
    fake = trial_navigator()
    storage = OverUsb(navigator, fake)
    found = _trial_maps(storage)
    order = []
    real_handle = fake._handle

    def noted(operation, params, data):
        if operation in (ptp.DELETE_OBJECT, ptp.SEND_OBJECT_INFO) and "device" not in order:
            order.append("device")
        return real_handle(operation, params, data)

    fake._handle = noted
    report = _install_trial(storage, found, order)
    assert order == ["activate", "device"] and sorted(map_downloads) == sorted(
        hashlib.md5(d).hexdigest() for d in NEW_MAP.values())
    # One session to write: the old file of this map removed, the shared one kept.
    assert ("delete", ".System/Map/D0000001A.img") in fake.steps
    assert fake.data_at(SHARED) == MAP_FILE and fake.data_at(".System/Map/D0000001A.img") is None
    for path, data in NEW_MAP.items():
        assert fake.data_at(path) == data
        assert ("read", path) in fake.steps  # each read back whole
    assert fake.data_at(UNLOCK[0]) == UNLOCK[1]
    assert sorted(report.written) == sorted(NEW_MAP) and report.unlocks == [UNLOCK[0]]
    assert report.removed == [".System/Map/D0000001A.img"] and report.kept == []


def test_the_trial_logs_the_time_of_each_copy_and_check_apart(navigator, map_downloads, caplog):
    import logging

    storage = OverUsb(navigator, trial_navigator())
    found = _trial_maps(storage)
    with caplog.at_level(logging.INFO, logger="openexpress.hidden_folders"):
        _install_trial(storage, found)
    lines = _hidden_folders_log(caplog)
    for data in NEW_MAP.values():  # by their sizes: the log hides names that look like numbers
        assert any(f"({len(data)} B) sent in " in line for line in lines)
        assert any(f" read back whole ({len(data)} B) in " in line for line in lines)


def test_no_room_on_the_device_changes_nothing_and_downloads_nothing(navigator, map_downloads):
    fake = trial_navigator()
    storage = OverUsb(navigator, fake)
    found = _trial_maps(storage)
    fake.free = 10
    storage.free_space = lambda: 10
    with pytest.raises(maps.MapError, match="Not enough space on the device"):
        _install_trial(storage, found)
    assert fake.steps == [] and map_downloads == []


def test_no_room_on_this_computer_changes_nothing(navigator, map_downloads, monkeypatch):
    fake = trial_navigator()
    storage = OverUsb(navigator, fake)
    found = _trial_maps(storage)
    monkeypatch.setattr(maps, "free_on_computer", lambda: 100)
    with pytest.raises(maps.MapError, match="Not enough space on this computer"):
        _install_trial(storage, found)
    assert fake.steps == [] and map_downloads == []


def test_an_interrupted_write_leaves_a_map_that_says_it_is_incomplete(navigator, map_downloads):
    fake = trial_navigator()
    storage = OverUsb(navigator, fake)
    found = _trial_maps(storage)
    fake.refuse_names["D0000002B.img"] = ptp.STORE_FULL  # the second in the order written (smallest first)
    with pytest.raises(maps.MapError) as stopped:
        _install_trial(storage, found)
    assert str(stopped.value) == (
        "Made-up City Navigator is incomplete on the device: 1 of 3 files was written. Nothing "
        "else was removed, and the downloaded files stay on this computer: install it again and "
        "only what is missing is written.")
    # What was written stays; what was not written whole is not there; the old file is gone.
    assert fake.data_at(".System/JCV/D0000002.jcv") == NEW_MAP[".System/JCV/D0000002.jcv"]
    assert fake.data_at(".System/Map/D0000002B.img") is None
    assert fake.data_at(".System/Map/D0000002A.img") is None
    assert fake.data_at(".System/Map/D0000001A.img") is None and fake.data_at(SHARED) == MAP_FILE
    # Plugged in again: the map says it is incomplete, on the command line and the page.
    fake.reconnect()
    hidden_folders.forget(storage)
    again = _trial_maps(storage)
    (m,) = [x for x in again if x.name == TRIAL_NAME]
    assert m.incomplete and m.status == "incomplete on the device" and not m.update_available


def test_installed_again_only_what_is_missing_is_written(navigator, map_downloads):
    fake = trial_navigator()
    storage = OverUsb(navigator, fake)
    found = _trial_maps(storage)
    fake.refuse_names["D0000002B.img"] = ptp.STORE_FULL
    with pytest.raises(maps.MapError):
        _install_trial(storage, found)
    downloaded = list(map_downloads)
    del fake.refuse_names["D0000002B.img"]
    fake.reconnect()
    fake.steps.clear()
    hidden_folders.forget(storage)
    report = _install_trial(storage, _trial_maps(storage))
    # Nothing downloaded again; the file written before read back, not written again.
    assert map_downloads == downloaded
    assert report.kept == [".System/JCV/D0000002.jcv"]
    assert sorted(report.written) == [".System/Map/D0000002A.img", ".System/Map/D0000002B.img"]
    assert ("info", ".System/JCV/D0000002.jcv") not in fake.steps
    assert ("read", ".System/JCV/D0000002.jcv") in fake.steps
    for path, data in NEW_MAP.items():
        assert fake.data_at(path) == data


def test_a_file_there_of_the_same_size_but_other_bytes_is_written_again(navigator,
                                                                       map_downloads):
    fake = trial_navigator()
    path = ".System/JCV/D0000002.jcv"
    fake.add(path, b"x" * len(NEW_MAP[path]), hidden=True)
    storage = OverUsb(navigator, fake)
    report = _install_trial(storage, _trial_maps(storage))
    assert report.kept == [] and path in report.written and fake.data_at(path) == NEW_MAP[path]


def _refusing(fake, name, times, code=ptp.STORE_FULL):
    """The device refuses the file `name` at SendObjectInfo `times` times, then takes it."""
    real = fake._handle
    left = [times]

    def handle(operation, params, data):
        if operation == ptp.SEND_OBJECT_INFO and left[0] and ptp.read_string(data, 52)[0] == name:
            left[0] -= 1
            return (code,)
        return real(operation, params, data)

    fake._handle = handle


def test_a_file_refused_is_written_again_up_to_three_times(navigator, map_downloads):
    fake = trial_navigator()
    storage = OverUsb(navigator, fake)
    found = _trial_maps(storage)
    _refusing(fake, "D0000002B.img", 2)
    waits = []
    report = _install_trial(storage, found, waits=waits)
    assert waits == [5, 5] and ".System/Map/D0000002B.img" in report.written
    assert fake.data_at(".System/Map/D0000002B.img") == NEW_MAP[".System/Map/D0000002B.img"]


def test_a_file_refused_three_times_stops_the_map(navigator, map_downloads):
    fake = trial_navigator()
    storage = OverUsb(navigator, fake)
    found = _trial_maps(storage)
    _refusing(fake, "D0000002B.img", 3)
    waits = []
    with pytest.raises(maps.MapError, match="1 of 3 files was written"):
        _install_trial(storage, found, waits=waits)
    assert waits == [5, 5]


def test_a_session_that_broke_is_not_tried_again(navigator, map_downloads):
    fake = trial_navigator()
    storage = OverUsb(navigator, fake)
    found = _trial_maps(storage)
    real = fake._handle

    def silent_at_the_second(operation, params, data):
        if operation == ptp.SEND_OBJECT_INFO and ptp.read_string(data, 52)[0] == "D0000002B.img":
            fake.silent = True
        return real(operation, params, data)

    fake._handle = silent_at_the_second
    waits = []
    with pytest.raises(maps.MapError, match="1 of 3 files was written"):
        _install_trial(storage, found, waits=waits)
    assert waits == []  # no answer: the session is gone, nothing more is tried in it


def test_an_unlock_file_refused_is_written_again_and_said_as_for_other_maps(navigator,
                                                                           map_downloads):
    fake = trial_navigator()
    storage = OverUsb(navigator, fake)
    found = _trial_maps(storage)
    _refusing(fake, "D0000002.gma", 6)
    waits = []
    with pytest.raises(maps.UnlockNotWritten) as stopped:
        _install_trial(storage, found, waits=waits)
    assert waits == [5] * 5
    assert str(stopped.value).startswith(
        "The map files are on the device, but its unlock file .System/D0000002.gma could not be "
        "written (")
    for path, data in NEW_MAP.items():  # the map's files stay
        assert fake.data_at(path) == data


def test_the_computer_is_kept_awake_from_the_first_removal_to_the_last_write(
        navigator, map_downloads, no_sleep_blocked):
    fake = trial_navigator()
    storage = OverUsb(navigator, fake)
    found = _trial_maps(storage)
    events = no_sleep_blocked
    real = fake._handle

    def noted(operation, params, data):
        if operation == ptp.DELETE_OBJECT:
            events.append(("delete", None))
        elif operation == ptp.SEND_OBJECT:
            events.append(("send", None))
        return real(operation, params, data)

    fake._handle = noted
    _install_trial(storage, found)
    kinds = [kind for kind, _x in events]
    assert kinds[0] == "block" and kinds[-1] == "release"
    assert kinds.count("block") == 1 and kinds.index("delete") == 1
    assert kinds[-2] == "send"  # the unlock file, written last, inside the block


@pytest.fixture
def trial_cli(navigator, monkeypatch, map_downloads):
    """The command line on the made-up navigator, the map service's answer and the
    activation from the examples above (no server asked)."""
    from openexpress import cli

    fake = trial_navigator()
    storage = OverUsb(navigator, fake)
    monkeypatch.setattr(cli, "open_storage", lambda args: storage)
    ns = types.SimpleNamespace(fake=fake, storage=storage, order=[],
                               answer={"maps": [_trial_map(), _other_map()]}, activation=None)
    monkeypatch.setattr(maps, "check", lambda client, dev, ask=None, country=None:
                        maps.read_maps(ns.answer))

    def activate(self, raw, identifiers):
        if ns.activation is not None:
            ns.order.append("activate")
            return ns.activation
        return _activate(ns.order)(identifiers[0])

    monkeypatch.setattr(GarminClient, "activate_maps", activate)
    monkeypatch.setattr(cli, "_url_salt", lambda: None)
    monkeypatch.setattr(hidden_folders, "WRITE_PAUSE_S", 0)  # the tries are counted above
    return ns


def test_the_command_line_reads_the_folders_maps_need_besides_those_known(trial_cli, capsys):
    """Once the device is known to hide .System, the command line reads it as it opens the
    device; the folders its maps need besides (not named in its GarminDevice.xml) are read
    then too, else its maps could not be read."""
    assert main(["maps"]) == 0  # the first time: .System noted as hidden
    capsys.readouterr()
    trial_cli.fake.reconnect()
    hidden_folders.forget(trial_cli.storage)
    assert main(["maps"]) == 0
    out = capsys.readouterr().out
    assert "cannot be read" not in out and "update available" in out
    assert {".System/Map", ".System/JCV"} <= trial_cli.fake.folders_listed()


def test_the_command_line_trial_dry_run_says_what_would_be_done(trial_cli, capsys,
                                                                map_downloads):
    assert main(["maps", "install", TRIAL_NAME, "--hidden-folder-trial"]) == 0
    out = capsys.readouterr().out
    for path in NEW_MAP:
        assert f"  + {path}  " in out
    assert "  - .System/Map/D0000001A.img  4 B" in out
    assert "  - " + SHARED not in out  # another map uses it: never removed
    assert "Space on this computer for the download:" in out
    assert "Writing and reading back on the device: about" in out
    assert ("From the removal of the first old file until the last one is written, the device "
            "has no complete map") in out
    assert out.endswith(
        "Largest file: .System/Map/D0000002A.img, 880 B\n"
        "Dry run: nothing was downloaded and nothing on the device was changed. Add --apply.\n")
    assert trial_cli.fake.steps == [] and map_downloads == [] and trial_cli.order == []


def test_an_old_file_garmin_lists_that_is_not_on_the_device_is_not_shown_as_removed(
        trial_cli, capsys, caplog):
    """Garmin's list of old files may name one the device does not have (on a Drive 53 EX an
    unlock file of the old version): the dry run lists only the old files there, each with its
    size, and the log counts only those."""
    import logging

    absent = ".System/Map/D0000001A.gma"
    trial_cli.answer = {"maps": [
        _trial_map(remove=(".System/Map/D0000001A.img", absent, SHARED)), _other_map()]}
    assert main(["maps", "install", TRIAL_NAME, "--hidden-folder-trial"]) == 0
    removed = [line for line in capsys.readouterr().out.splitlines() if line.startswith("  - ")]
    assert removed == ["  - .System/Map/D0000001A.img  4 B"]
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="openexpress.maps"):
        assert main(["maps", "install", TRIAL_NAME, "--hidden-folder-trial", "--apply"]) == 0
    planned = [r.getMessage() for r in caplog.records if "(the trial)" in r.getMessage()]
    assert len(planned) == 1 and planned[0].endswith(", 1 old files to remove")


def test_the_command_line_trial_writes_with_apply(trial_cli, capsys):
    assert main(["maps", "install", TRIAL_NAME, "--hidden-folder-trial", "--apply"]) == 0
    out = capsys.readouterr().out
    _keep_output("2-installed", out)
    assert "Wrote 3 map files.\nUnlock files written: 1\n" in out
    for path, data in NEW_MAP.items():
        assert trial_cli.fake.data_at(path) == data


def test_the_command_line_without_the_trial_refuses_the_hidden_folder(trial_cli, capsys):
    assert main(["maps", "install", TRIAL_NAME]) == 1
    assert "does not install maps into the folder .System yet" in capsys.readouterr().err
    assert trial_cli.fake.steps == []


# What the command line says in each case, kept for a look with
# OPENEXPRESS_MAP_TRIAL_OUTPUT=FOLDER (one file each).
MAP_TRIAL_OUTPUT = os.environ.get("OPENEXPRESS_MAP_TRIAL_OUTPUT")


def _keep_output(name: str, text: str) -> None:
    if MAP_TRIAL_OUTPUT:
        folder = pathlib.Path(MAP_TRIAL_OUTPUT)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{name}.txt").write_text(text)


# The files of the Drive's City Navigator in each folder (fakes.DRIVE_MAP; the folder Map in 6
# files, as on the Drive); file names and the other counts are made up.
DRIVE_FOLDERS = {"Map": 6, "JCV": 2, "SID": 16, "SQLite": 4}


def _drive_files(sizes, version):
    files = {}
    for folder, count in DRIVE_FOLDERS.items():
        each = sizes[folder] // count
        ext = {"Map": "img", "JCV": "jcv", "SID": "sid", "SQLite": "db"}[folder]
        for n in range(count):
            files[f".System/{folder}/NA{version}_{n:02d}.{ext}"] = each
    return files


def test_the_command_line_trial_dry_run_on_the_drive_as_it_is(navigator, monkeypatch, capsys,
                                                              map_downloads):
    from openexpress import cli

    old, new = _drive_files(DRIVE_MAP, "2026"), _drive_files(DRIVE_NEW_MAP, "2027")
    fake = navigator_session()
    for folder in DRIVE_FOLDERS:
        fake.add(f".System/{folder}", hidden=True)
    for path, size in old.items():
        handle = fake.add(path, b"", hidden=True)
        fake.objects[handle].listed_size = size
    storage = OverUsb(navigator, fake)
    storage.free_space = lambda: DRIVE_SPACE[0]
    monkeypatch.setattr(maps, "free_on_computer", lambda: 120_000_000_000)
    monkeypatch.setattr(cli, "open_storage", lambda args: storage)
    entry = _trial_map(remove=list(old), files={p: b"" for p in new})
    for f in entry["installoptions"][0]["files"]:
        f["deliverableoptions"][0]["sizeinbytes"] = new[f["filename"]]
    entry["displayname"] = "City Navigator North America NT"
    monkeypatch.setattr(maps, "check", lambda client, dev, ask=None, country=None:
                        maps.read_maps({"maps": [entry]}))
    assert main(["maps", "install", "City Navigator", "--hidden-folder-trial"]) == 0
    out = capsys.readouterr().out
    _keep_output("1-dry-run-drive", out)
    assert "28 files, download 8.3 GB, space on the device 700.0 MB (6.7 GB free)" in out
    assert "  - .System/Map/NA2026_00.img  876.7 MB" in out
    assert ("Writing and reading back on the device: about 21 min at the slowest speed measured "
            "so far") in out
    assert fake.steps == [] and map_downloads == []


def test_the_command_line_trial_stopped_and_installed_again(trial_cli, capsys, map_downloads):
    fake = trial_cli.fake
    fake.refuse_names["D0000002B.img"] = ptp.STORE_FULL
    assert main(["maps", "install", TRIAL_NAME, "--hidden-folder-trial", "--apply"]) == 1
    stopped = capsys.readouterr()
    _keep_output("3-stopped", stopped.out + stopped.err)
    assert "is incomplete on the device: 1 of 3 files was written" in stopped.err
    # Plugged in again: the map says it is incomplete.
    del fake.refuse_names["D0000002B.img"]
    fake.reconnect()
    hidden_folders.forget(trial_cli.storage)
    assert main(["maps"]) == 0
    listed = capsys.readouterr().out
    _keep_output("4-maps-after-replugging", listed)
    assert "incomplete on the device" in listed
    assert listed.count("Reading the folder") == 1  # once: the maps' folders with the known ones
    downloaded = list(map_downloads)
    assert main(["maps", "install", TRIAL_NAME, "--hidden-folder-trial", "--apply"]) == 0
    again = capsys.readouterr().out
    _keep_output("5-installed-again", again)
    assert "1 file was on the device already and read back the same: not written again." in again
    assert map_downloads == downloaded  # nothing downloaded again
    for path, data in NEW_MAP.items():
        assert fake.data_at(path) == data


# --- refused before anything: said by the dry run, again before --apply downloads -----------------

def _refused_both_ways(trial_cli, capsys, map_downloads, reason, keep=None):
    assert main(["maps", "install", TRIAL_NAME, "--hidden-folder-trial"]) == 1
    out = capsys.readouterr().out
    if keep:
        _keep_output(keep, out)
    assert f"Would be refused: {reason}" in out
    assert main(["maps", "install", TRIAL_NAME, "--hidden-folder-trial", "--apply"]) == 1
    assert reason in capsys.readouterr().err
    assert trial_cli.fake.steps == [] and map_downloads == [] and trial_cli.order == []


def test_a_file_over_four_gib_is_refused_before_anything(trial_cli, capsys, map_downloads):
    entry = _trial_map()
    big = entry["installoptions"][0]["files"][0]
    big["deliverableoptions"][0]["sizeinbytes"] = 5 * 1024 ** 3
    trial_cli.answer = {"maps": [entry, _other_map()]}
    _refused_both_ways(trial_cli, capsys, map_downloads,
                       f"{big['filename']} is 5.4 GB: a file over 4 GiB cannot be sent to the "
                       "device.", keep="6-dry-run-refused")


def test_a_folder_not_on_the_device_is_refused_before_anything(trial_cli, capsys, map_downloads):
    files = {**NEW_MAP, ".System/Atlas/D0000002C.img": b"made-up atlas"}
    trial_cli.answer = {"maps": [_trial_map(files=files), _other_map()]}
    _refused_both_ways(trial_cli, capsys, map_downloads,
                       "The folder .System/Atlas is not on the device, and openExpress does "
                       "not create folders there.")


def test_a_file_without_a_checksum_is_refused_before_anything(trial_cli, capsys, map_downloads):
    entry = _trial_map()
    entry["installoptions"][0]["files"][1]["deliverableoptions"][0]["md5"] = "not a checksum"
    trial_cli.answer = {"maps": [entry, _other_map()]}
    _refused_both_ways(trial_cli, capsys, map_downloads,
                       "Map “Made-up City Navigator” cannot be installed: the server gave an "
                       "invalid checksum for one of its files.")


def test_an_unlock_file_without_a_place_is_refused_before_the_device_changes(trial_cli, capsys,
                                                                             map_downloads):
    import base64

    trial_cli.activation = {"unlocks": [{"gma": base64.b64encode(UNLOCK[1]).decode()}]}
    assert main(["maps", "install", TRIAL_NAME, "--hidden-folder-trial", "--apply"]) == 1
    assert ("Made-up City Navigator was not installed: Garmin's activation did not say where on "
            "the device its unlock file goes. Nothing on the device was changed."
            ) in capsys.readouterr().err
    assert trial_cli.order == ["activate"] and trial_cli.fake.steps == []


# --- the operations sent to the device -----------------------------------------------------------


def _commands(fake) -> str:
    """The operations sent to `fake`, each named with its parameters in hex, in order, as one
    line (the commands of the transfers, not the data)."""
    import struct

    sent = []
    for t in fake.transfers:
        if len(t) >= 12:
            length, kind, code, _transaction = struct.unpack_from("<IHHI", t)
            if kind == 1 and length == len(t):
                params = struct.unpack_from(f"<{(length - 12) // 4}I", t, 12)
                sent.append(" ".join([ptp.operation_name(code), *(f"{p:x}" for p in params)]))
    return " | ".join(sent)


# Reading .System through a session of its own, then a session for each of the two files of
# the updates; the trial: the map's folders read, then one session that writes the map.
UPDATES_OPERATIONS = (
    "OpenSession 1 | GetStorageIDs | GetObjectHandles 10001 0 ffffffff | GetObjectInfo 100 | "
    "GetObjectInfo 102 | GetObjectInfo 103 | GetObjectInfo 104 | Garmin GetObjectIds 10001 "
    "ffffffff | GetObjectInfo 106 | GetObjectHandles 10001 0 106 | Garmin GetObjectIds 10001 106 "
    "| GetObjectInfo 107 | GetObjectInfo 108 | GetObjectInfo 109 | GetObjectInfo 10a | "
    "CloseSession | OpenSession 1 | GetStorageIDs | GetObjectHandles 10001 0 ffffffff | "
    "GetObjectInfo 100 | GetObjectInfo 102 | GetObjectInfo 103 | GetObjectInfo 104 | Garmin "
    "GetObjectIds 10001 ffffffff | GetObjectInfo 106 | GetObjectHandles 10001 0 106 | Garmin "
    "GetObjectIds 10001 106 | GetObjectInfo 107 | GetObjectInfo 108 | GetObjectInfo 109 | "
    "GetObjectInfo 10a | GetStorageInfo 10001 | DeleteObject 10a 0 | SendObjectInfo 10001 106 | "
    "SendObject | GetObjectInfo 10d | GetObject 10d | CloseSession | OpenSession 1 | "
    "GetStorageIDs | GetObjectHandles 10001 0 ffffffff | GetObjectInfo 100 | GetObjectInfo 102 | "
    "GetObjectInfo 103 | GetObjectInfo 104 | Garmin GetObjectIds 10001 ffffffff | GetObjectInfo "
    "106 | GetObjectHandles 10001 0 106 | GetObjectInfo 10d | Garmin GetObjectIds 10001 106 | "
    "GetObjectInfo 107 | GetObjectInfo 108 | GetObjectInfo 109 | GetStorageInfo 10001 | "
    "DeleteObject 109 0 | SendObjectInfo 10001 106 | SendObject | GetObjectInfo 10e | GetObject "
    "10e | CloseSession")
TRIAL_OPERATIONS = (
    "OpenSession 1 | GetStorageIDs | GetObjectHandles 10001 0 ffffffff | GetObjectInfo 100 | "
    "GetObjectInfo 102 | GetObjectInfo 103 | GetObjectInfo 104 | Garmin GetObjectIds 10001 "
    "ffffffff | GetObjectInfo 106 | GetObjectHandles 10001 0 106 | Garmin GetObjectIds 10001 106 "
    "| GetObjectInfo 107 | GetObjectInfo 108 | GetObjectInfo 109 | GetObjectInfo 10a | "
    "GetObjectInfo 10e | GetObjectHandles 10001 0 10e | Garmin GetObjectIds 10001 10e | "
    "GetObjectHandles 10001 0 107 | Garmin GetObjectIds 10001 107 | GetObjectInfo 10b | "
    "GetObjectInfo 10d | CloseSession | OpenSession 1 | GetStorageIDs | GetObjectHandles 10001 0 "
    "ffffffff | GetObjectInfo 100 | GetObjectInfo 102 | GetObjectInfo 103 | GetObjectInfo 104 | "
    "Garmin GetObjectIds 10001 ffffffff | GetObjectInfo 106 | GetObjectHandles 10001 0 106 | "
    "Garmin GetObjectIds 10001 106 | GetObjectInfo 107 | GetObjectInfo 108 | GetObjectInfo 109 | "
    "GetObjectInfo 10a | GetObjectInfo 10e | GetStorageIDs | GetObjectHandles 10001 0 ffffffff | "
    "GetObjectInfo 100 | GetObjectInfo 102 | GetObjectInfo 103 | GetObjectInfo 104 | Garmin "
    "GetObjectIds 10001 ffffffff | GetObjectInfo 106 | GetObjectHandles 10001 0 106 | Garmin "
    "GetObjectIds 10001 106 | GetObjectInfo 107 | GetObjectInfo 108 | GetObjectInfo 109 | "
    "GetObjectInfo 10a | GetObjectInfo 10e | GetObjectHandles 10001 0 10e | Garmin GetObjectIds "
    "10001 10e | GetStorageIDs | GetObjectHandles 10001 0 ffffffff | GetObjectInfo 100 | "
    "GetObjectInfo 102 | GetObjectInfo 103 | GetObjectInfo 104 | Garmin GetObjectIds 10001 "
    "ffffffff | GetObjectInfo 106 | GetObjectHandles 10001 0 106 | Garmin GetObjectIds 10001 106 "
    "| GetObjectInfo 107 | GetObjectInfo 108 | GetObjectInfo 109 | GetObjectInfo 10a | "
    "GetObjectInfo 10e | GetObjectHandles 10001 0 107 | Garmin GetObjectIds 10001 107 | "
    "GetObjectInfo 10b | GetObjectInfo 10d | GetStorageInfo 10001 | DeleteObject 10b 0 | "
    "SendObjectInfo 10001 10e | SendObject | GetObjectInfo 10f | GetObject 10f | SendObjectInfo "
    "10001 107 | SendObject | GetObjectInfo 110 | GetObject 110 | SendObjectInfo 10001 107 | "
    "SendObject | GetObjectInfo 111 | GetObject 111 | SendObjectInfo 10001 106 | SendObject | "
    "GetObjectInfo 112 | GetObject 112 | CloseSession")


def test_the_operations_of_installing_updates_into_the_hidden_folder_stay_as_they_are(
        navigator, drive_downloads):
    """Reading .System, then installing the software and the time zone map there: every
    operation sent and its parameters, in order, as written down here."""
    fake = navigator_session()
    storage = OverUsb(navigator, fake)
    updates.install(_both(_drive_offered(storage)), storage)
    assert _commands(fake) == UPDATES_OPERATIONS


def test_the_operations_of_the_map_trial_stay_as_they_are(navigator, map_downloads):
    """Reading the map's folders, then the trial writing the map: every operation sent and its
    parameters, in order, as written down here."""
    fake = trial_navigator()
    storage = OverUsb(navigator, fake)
    _install_trial(storage, _trial_maps(storage))
    assert _commands(fake) == TRIAL_OPERATIONS


def test_a_map_file_right_in_the_hidden_folder_is_read_as_the_others(navigator):
    """A map whose files include one right in .System (not in a folder below it): read through
    the session like those below it, so a map written whole waits for the device to take it
    in. Folders of the top level, and the files right in one, follow one rule."""
    right_in = ".System/D0000002Z.img"
    files = {**NEW_MAP, right_in: b"made-up map, part three " * 8}
    fake = trial_navigator()
    for path, data in files.items():
        fake.add(path, data, hidden=True)
    storage = OverUsb(navigator, fake)
    found = _trial_maps(storage, _trial_map(files=files), _other_map())
    (m,) = [m for m in found if m.name == TRIAL_NAME]
    assert m.hidden_folder == ".System"
    assert m.device_sizes[right_in.casefold()] == len(files[right_in])
    assert m.waiting == maps.WAIT_INSTALL
