"""A device that hides a folder of its top level from MTP (a car navigator's .System): what
openExpress cannot see it does not take for missing, and it writes nothing there.

The made-up device (device_root) has no .System at all, as ordinary MTP shows a navigator, and
visible Voice and Vehicle folders. Garmin's update server offers its software and a time-zone
map in .System, a voice and a vehicle it does not have (add-ons: not installed, optional), and a
new part in a new folder of its GARMIN folder (recommended, not installed yet); the universal map
service lists a map whose files are in .System/Map and one in the visible Garmin/Maps. All parts,
names and values are made up."""

import contextlib
import hashlib
import types

import pytest
from fakes import maps_window, window_methods
from fakes import window as stand_in
from ptp_fake import FakeDevice

from openexpress import download, hidden_folders, maps, updates
from openexpress.cli import main
from openexpress.connect import GarminClient, lower_keys
from openexpress.device import ptp
from openexpress.device.storage import LocalStorage

SYSTEM = b"made-up navigator software"
NEW_PART = b"made-up new part"


def _entry(part, name, path, filename, data, severity="Recommended", installed=True,
           primary=False, locale=None):
    """One offered item, the keys as the server sends them."""
    return {
        "partNumber": part, "productName": name, "dataType": "Firmware",
        "displayVersion": "9.00", "severity": severity, "isPrimaryFirmware": primary,
        "locale": locale, "releaseState": "live", "releaseNotes": [],
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
    from openexpress import service

    storage = LocalStorage(root)
    device = service.read_device(storage, found_now=False)
    return storage, updates.offered(storage, lower_keys(server_answer()), device,
                                    forget_installed=False)


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


@pytest.fixture
def downloads(monkeypatch, tmp_path):
    fetched = []

    def fetch(urls, dest, size, md5, session=None, progress=None):
        fetched.append(dest.name)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(NEW_PART if dest.name == "new.bin" else SYSTEM)
        return dest

    monkeypatch.setattr(download, "fetch", fetch)
    monkeypatch.setattr(updates, "cache_path", lambda u: tmp_path / "cache" / u.filename)
    return fetched


def test_the_install_into_a_top_folder_not_seen_is_refused_before_anything(navigator, downloads):
    storage, items = _offered(navigator)
    system = _by_part(items)["006-B0000-00"]
    with pytest.raises(updates.UpdateError) as refused:
        updates.install([system], storage)
    assert str(refused.value) == ("Not installed: Made-up Drive 9.00 goes to .System, which "
                                  "openExpress cannot see on the device.")
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


NOT_INSTALLED_SYSTEM = ("      Not installed: Made-up Drive 9.00 goes to .System, which openExpress "
                        "cannot see on the device.")
NOT_INSTALLED_TZ = ("      Not installed: Time zone map 9.00 goes to .System, which openExpress "
                    "cannot see on the device.")
NOTICE = ("openExpress cannot see the folder .System, where this device keeps its software, so it "
          "installs nothing there. Some devices, such as car navigators, hide it from the "
          "ordinary USB connection.")
ADD_ONS = ("2 add-ons not on the device are not offered: openExpress does not install add-ons "
           "yet.")


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


class _Group:
    def __init__(self, title):
        self.title, self.rows = title, []

    def add(self, row):
        self.rows.append(row)


class _Section:
    def __init__(self):
        self.groups = []

    def group(self, title="", description=""):
        self.groups.append(_Group(title))
        return self.groups[-1]


def _updates_page(items):
    import types

    from openexpress.gui.app import MainWindow

    win = stand_in(
        session=types.SimpleNamespace(device=types.SimpleNamespace(
            description="Made-up Drive", software_version_str="8.50")),
        sections={"updates": _Section()}, busy=False, _action_buttons=[],
        _set_update_state=lambda new, staged=(), not_installed=(): None,
        confirm_update=lambda u: None, load_section=lambda *a, **kw: None)
    window_methods(win, ["_update_row", "_updates_status", "_button"])
    MainWindow.show_updates(win, items)
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
    `fail_at`: a folder whose listing fails as a device unplugged meanwhile does."""

    def __init__(self):
        super().__init__()
        self.listed, self.fail_at = [], None

    def _handle(self, operation, params, data):
        if operation in (ptp.GET_OBJECT_HANDLES, ptp.GARMIN_GET_OBJECT_IDS):
            parent = params[2] if operation == ptp.GET_OBJECT_HANDLES else params[1]
            self.listed.append((operation, parent))
            if self.fail_at is not None and parent == self.handle(self.fail_at):
                raise ptp.PtpError("No such device (it may have been disconnected)")
        return super()._handle(operation, params, data)

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


CITY_NAVIGATOR = (".System/Map/D0000001A.img",)


def _offered_from(storage, answer=None):
    from openexpress import service

    device = service.read_device(storage, found_now=False)
    return updates.offered(storage, lower_keys(answer or server_answer()), device,
                           forget_installed=False)


def _maps_from(storage, *entries):
    found, _count = maps.read_maps({"maps": list(entries) or [
        _map("Made-up City Navigator", CITY_NAVIGATOR[0])]})
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
    assert [u.part_number for u in updates.installable(pending)] == ["006-D9004-00"]


def test_the_device_s_software_at_the_size_offered_stays_an_update(navigator):
    storage = OverUsb(navigator, navigator_session(software=SYSTEM))
    hidden_folders.read(storage, [".System"], "test")
    system = _by_part(_offered_from(storage))["006-B0000-00"]
    # Its contents not compared: taken neither for installed nor for copied and waiting.
    assert system.hidden_size_same is True and not system.on_device
    assert updates.pending([system]) == [system] and updates.staged([system]) == []


def test_an_update_into_a_folder_read_is_still_refused(navigator, downloads):
    fake = navigator_session()
    storage = OverUsb(navigator, fake)
    hidden_folders.read(storage, [".System"], "test")
    system = _by_part(_offered_from(storage))["006-B0000-00"]
    with pytest.raises(updates.UpdateError) as refused:
        updates.install([system], storage)
    assert str(refused.value) == ("Not installed yet: Made-up Drive 9.00 goes to .System, which "
                                  "openExpress only reads.")
    assert downloads == [] and storage.sessions == 1
    assert fake.sent(ptp.SEND_OBJECT_INFO) == fake.sent(ptp.DELETE_OBJECT) == 0


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
                            [_map("Made-up City Navigator", CITY_NAVIGATOR[0])])
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
                             [_map("Made-up City Navigator", CITY_NAVIGATOR[0])])
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
                             [_map("Made-up City Navigator", CITY_NAVIGATOR[0])], run=run)
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


def test_a_map_in_a_folder_read_has_its_state_and_is_never_written(navigator):
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
        "Map “Made-up City Navigator” cannot be installed: openExpress does not install into "
        ".System yet.")
    with pytest.raises(maps.MapError) as refused:
        maps.uninstall(city, [city], storage)
    assert str(refused.value) == ("Map “Made-up City Navigator” cannot be removed: openExpress "
                                  "does not change the folder .System yet.")
    assert fake.sent(ptp.DELETE_OBJECT) == 0


def test_a_new_map_into_a_folder_not_seen_is_refused_before_anything(navigator, monkeypatch):
    from openexpress import download

    monkeypatch.setattr(download, "fetch", lambda *a, **kw: pytest.fail("downloaded"))
    storage = OverUsb(navigator)
    entry = {**_map("Made-up New Region", ".System/Map/D0000002A.img"), "installationstate": 0,
             "isreinstall": False}
    (new,) = _maps_from(storage, entry)
    assert new.hidden_folder == ".System" and not maps.can_install(new)
    with pytest.raises(maps.MapError, match="does not install into .System yet"):
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


def test_device_status_counts_what_was_read_in_the_folders_not_seen(navigator):
    from openexpress import history, service

    xml = navigator / "GARMIN" / "GarminDevice.xml"
    xml.write_text(xml.read_text().replace("</MassStorageMode>",
                                           NAVIGATOR_TYPES + "</MassStorageMode>"))
    storage = OverUsb(navigator, navigator_session())
    device = service.read_device(storage, found_now=False)
    before = history.count(device, storage)
    assert (before["maps"], before["updates"]) == (0, 0)  # not seen: nothing counted
    hidden_folders.read(storage, [".System", ".System/Map"], "test")
    after = history.count(device, storage)
    assert (after["maps"], after["updates"]) == (len(MAP_FILE), len(OLDER_SYSTEM))
    assert storage.sessions == 1  # counted from what the one session read


@pytest.mark.parametrize("language", ["en", "pl"])
def test_the_new_words_in_both_languages(language):
    from openexpress import i18n

    i18n.setup(language)
    try:
        u = updates.parse_updates(lower_keys(server_answer()))[0]
        u.hidden_folder = ".System"
        texts = [updates.not_installed_here_text(u), updates.hidden_software_text(".System"),
                 updates.add_ons_text(2)]
    finally:
        i18n.setup("en")
    expected = {
        "en": [("Not installed: Made-up Drive 9.00 goes to .System, which openExpress cannot see "
                "on the device."), NOTICE, ADD_ONS],
        "pl": [("Nie zainstalowano: Made-up Drive 9.00 trafia do folderu .System, którego "
                "openExpress nie widzi na urządzeniu."),
               ("openExpress nie widzi folderu .System, w\u00a0którym to urządzenie trzyma swoje "
                "oprogramowanie, więc niczego tam nie instaluje. Niektóre urządzenia, np. "
                "nawigacje samochodowe, ukrywają go przed zwykłym połączeniem USB."),
               ("Pominięto 2 dodatki, których nie ma na urządzeniu: openExpress jeszcze nie "
                "instaluje dodatków.")]}
    assert texts == expected[language]


# --- what the pages and the command line say of a folder read -------------------------------------

READ_NOTICE = ("openExpress reads the folder .System through its own USB connection, but does not "
               "install into it yet.")
NO_SESSION_LINE = ("openExpress cannot open its own USB session with this device, so it cannot "
                   "read the folder .System.")


@pytest.mark.needs_display
def test_the_updates_page_says_what_was_read_and_installs_nothing_there(navigator):
    from gi.repository import Adw, Gtk

    from openexpress.gui.window_tasks import descendants

    storage = OverUsb(navigator, navigator_session())
    hidden_folders.read(storage, [".System"], "test")
    status, available, advanced = _updates_page(_offered_from(storage))
    top = _texts(status.rows[0])
    assert READ_NOTICE in top and "On the device: B000000.update of another size" in top
    assert NOTICE not in top and "Install" not in top
    assert [r.get_title() for r in available.rows] == ["New Part"]
    # The time zone map at the size offered is among the components on the device, with no
    # button: its folder is only read.
    (components,) = [r for r in advanced.rows if isinstance(r, Adw.ExpanderRow)]
    (tz,) = [r for r in descendants(components)
             if isinstance(r, Adw.ActionRow) and r.get_title() == "Time zone map"]
    assert tz.get_subtitle().endswith(
        "\nNot installed yet: it goes to .System, which openExpress only reads"
        "\nOn the device at the same size; its contents not compared")
    assert not [w for w in descendants(tz) if isinstance(w, Gtk.Button)]


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
    found, count = maps.read_maps({"maps": [_map("Made-up City Navigator", CITY_NAVIGATOR[0])]})
    maps.mark_waiting(storage, found)
    MainWindow.show_maps(win, (found, count))
    (on_device,) = win.sections["maps"].groups
    (row,) = [r for r in on_device.rows if r.get_title() == "Made-up City Navigator"]
    assert row.get_subtitle().endswith("\nopenExpress does not install into .System yet")
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
    assert ("      Not installed yet: Made-up Drive 9.00 goes to .System, which openExpress only "
            "reads.") in out
    assert READ_NOTICE in out and "On the device: B000000.update of another size." in out
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
        {"maps": [_map("Made-up City Navigator", CITY_NAVIGATOR[0])]}))
    assert main(["maps"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert "    openExpress does not install into .System yet." in out and storage.sessions == 1
    assert main(["maps", "remove", "Made-up City Navigator", "--apply"]) == 1
    said = capsys.readouterr()
    assert ("Map “Made-up City Navigator” cannot be removed: openExpress does not change the "
            "folder .System yet.") in said.out + said.err
    assert storage.fake.sent(ptp.DELETE_OBJECT) == 0 and not said.out.startswith("Removing")


@pytest.mark.parametrize("language", ["en", "pl"])
def test_the_words_of_a_folder_read_in_both_languages(language):
    from openexpress import i18n
    from openexpress.gui import updates_page

    i18n.setup(language)
    try:
        u = updates.parse_updates(lower_keys(server_answer()))[0]
        u.hidden_folder, u.hidden_session = ".System", hidden_folders.READ
        m = maps.read_maps({"maps": [_map("Made-up City Navigator", CITY_NAVIGATOR[0])]})[0][0]
        m.hidden_folder = ".System"
        texts = [updates.not_installed_here_text(u), updates.read_only_software_text(".System"),
                 updates.other_size_text(u), hidden_folders.reading_text([".System"]),
                 hidden_folders.no_session_text(".System", hidden_folders.NO_SESSION),
                 updates_page.read_only_folder_text(".System"),
                 maps.cannot_install_reason(m), maps.removal_refused(m)]
    finally:
        i18n.setup("en")
    expected = {
        "en": [("Not installed yet: Made-up Drive 9.00 goes to .System, which openExpress only "
                "reads."), READ_NOTICE, "On the device: B000000.update of another size.",
               "Reading the folder .System…", NO_SESSION_LINE,
               "Not installed yet: it goes to .System, which openExpress only reads",
               "openExpress does not install into .System yet",
               ("Map “Made-up City Navigator” cannot be removed: openExpress does not change the "
                "folder .System yet.")],
        "pl": [("Jeszcze nie do zainstalowania: Made-up Drive 9.00 trafia do folderu .System, "
                "który openExpress tylko odczytuje."),
               ("openExpress odczytuje folder .System przez własne połączenie USB, ale jeszcze "
                "niczego w\u00a0nim nie instaluje."),
               "Na urządzeniu: B000000.update o\u00a0innym rozmiarze.",
               "Odczytywanie folderu .System…",
               ("openExpress nie może otworzyć własnej sesji USB z\u00a0tym urządzeniem, więc nie "
                "odczyta folderu .System."),
               ("Jeszcze nie do zainstalowania: trafia do folderu .System, który openExpress tylko "
                "odczytuje"),
               "openExpress jeszcze nie instaluje w\u00a0folderze .System",
               ("Nie można usunąć mapy „Made-up City Navigator”: openExpress jeszcze nie zmienia "
                "folderu .System.")]}
    assert texts == expected[language]


# --- the window and the command line read and count the same ----------------------------------


def _with_types(root):
    """The made-up navigator's GarminDevice.xml naming its maps in .System/Map and its software
    in .System, as Device Status counts them."""
    xml = root / "GARMIN" / "GarminDevice.xml"
    xml.write_text(xml.read_text().replace("</MassStorageMode>",
                                           NAVIGATOR_TYPES + "</MassStorageMode>"))
    return root


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
                                                                         monkeypatch, capsys):
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
def test_a_watch_opens_no_session_for_a_command(device_root, hidden, monkeypatch, capsys):
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
    from openexpress import i18n

    storage = OverUsb(navigator, navigator_session())
    hidden_folders.read(storage, [".System"], "test")
    offered = _offered_from(storage)
    # Without the new part in a folder that is seen, nothing offered can be installed.
    only_hidden = [u for u in offered if u.part_number != "006-D9004-00"]
    i18n.setup(language)
    try:
        status, *_groups = _updates_page(only_hidden)
        none_installable = _texts(status.rows[0])
        status, *_groups = _updates_page(offered)
        one_installable = _texts(status.rows[0])
    finally:
        i18n.setup("en")
    assert title in none_installable and title not in one_installable


# --- own maps: never into a folder the device hides --------------------------------------------

OWN_MAPS_HIDDEN = """
  <DataType><Name>NMaps</Name>
   <File><Location><Path>.System/Map</Path><FileExtension>img</FileExtension></Location>
    <TransferDirection>InputToUnit</TransferDirection></File></DataType>
"""


def _own_maps_in_system(root):
    xml = root / "GARMIN" / "GarminDevice.xml"
    xml.write_text(xml.read_text().replace("</MassStorageMode>",
                                           OWN_MAPS_HIDDEN + "</MassStorageMode>"))
    return root


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
    with pytest.raises(maps.MapError, match="does not install into .System yet"):
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
    assert any(r.get_subtitle() == "openExpress does not install into .System yet"
               for r in own.rows if hasattr(r, "get_subtitle"))
