"""System updates: recognised by their file, not only by the server's flags.

The answer below has the shape of a real one (an Edge 1050 offered a beta bundle): the bundle of
the device software is GUPDATE.GSP without "isprimaryfirmware" or "isrestartrequired", and its
data type, "Firmware", is also that of plain files such as fonts. All values are made up."""

import hashlib
import logging
import types
from datetime import datetime
from pathlib import Path

import pytest
from fakes import RunNow, other_bytes_drive, window_methods
from fakes import window as stand_in

from openexpress import download, updates
from openexpress.cli import main
from openexpress.connect import GarminClient, lower_keys
from openexpress.device.storage import LocalStorage
from openexpress.device.xmldevice import UpdateFile

BUNDLE = b"bundle bytes"
# Two versions as the window shows them: on one line (no-break spaces around the arrow).
VERSIONS = "23.39\u00a0→\u00a023.40"
FONT = b"font"


def _entry(part, name, datatype, path, filename, data, order=0, **flags):
    """One offered item, with the keys as the server sends them (camelCase)."""
    return {
        "partNumber": part, "productName": name, "dataType": datatype,
        "displayVersion": "23.40", "severity": "Recommended",
        "isPrimaryFirmware": flags.get("primary", False),
        "releaseState": flags.get("release_state", "live"),
        "releaseNotes": [],
        "deliverable": {"downloadUrl": "https://example.org/" + filename,
                        "md5": hashlib.md5(data).hexdigest().upper(),
                        "sizeInBytes": len(data)},
        "installation": {"path": path, "fileName": filename, "isReinstall": False,
                         "isRestartRequired": flags.get("restart", False), "order": order},
    }


def server_answer():
    """What the update server sends: a system bundle that says nothing about being one, and
    a plain file of the same data type that is listed first and installed first."""
    return {"updates": [
        _entry("006-D0000-00", "Test Font", "Firmware", "Garmin\\Font", "006-D0000-00.fnt", FONT,
               order=5),
        _entry("006-V0000-00", "Test Bundle", "Firmware", "Garmin", "GUPDATE.GSP", BUNDLE),
    ]}


@pytest.fixture
def offered(device_root, monkeypatch):
    answer = lower_keys(server_answer())  # as GarminClient returns it
    monkeypatch.setattr(GarminClient, "software_updates",
                        lambda self, raw, include_reinstalls=False: answer)
    # An install asks the kind of device (the data-loss warning): an ordinary one.
    monkeypatch.setattr(GarminClient, "unit_info", lambda self, unit_id, part_number: {})
    return device_root


@pytest.fixture
def no_download(monkeypatch):
    def fetch(*_a, **_kw):
        raise AssertionError("nothing may be downloaded")

    monkeypatch.setattr(download, "fetch", fetch)


def test_a_system_update_is_known_by_its_file():
    bundle, font = updates.parse_updates(lower_keys(server_answer()))[::-1]
    assert (bundle.is_primary, bundle.restart_required) == (False, False)
    assert bundle.is_system
    assert not font.is_system  # the same data type ("Firmware"), a plain file


@pytest.mark.parametrize(("path", "filename", "flags", "system", "installs"), [
    ("Garmin", "GUPDATE.GCD", {}, True, True),
    ("GARMIN", "gupdate.gsp", {}, True, True),        # any letter case
    ("", "GUPDATE.GCD", {}, True, True),              # at the top of the storage
    # Another folder: copied as the device's software is, but the software of a part of the
    # device (an Instinct 2S has its GPS chip's in GARMIN/REMOTESW/GUPDATE.GCD).
    ("Garmin\\Other", "GUPDATE.GSP", {}, False, True),
    ("GARMIN\\REMOTESW", "GUPDATE.GCD", {"restart": True}, False, True),
    ("Garmin", "GUPDATE.GSP.bak", {}, False, False),
    ("Garmin", "XGUPDATE.GCD", {}, False, False),
    ("Garmin", "GUPDATE.RGN", {}, False, False),
    ("Garmin", "settings.fit", {"primary": True}, True, True),  # the server's flags count
    ("Garmin", "settings.fit", {"restart": True}, False, True),
    ("Garmin", "settings.fit", {}, False, False),
])
def test_which_files_are_system_updates(path, filename, flags, system, installs):
    """`is_system`: the device's own software; `installs_when_disconnected`: what is copied
    last, with the warning, and installed by the device once it is disconnected."""
    answer = lower_keys({"updates": [_entry("006-X0000-00", "X", "CONFIG", path, filename, b"x",
                                            **flags)]})
    (u,) = updates.parse_updates(answer)
    assert (u.is_system, u.installs_when_disconnected) == (system, installs)


def test_the_device_software_is_known_by_the_device_part_number():
    """An update of the part number the device itself has (its GarminDevice.xml's model) is
    its software, wherever the server puts its file."""
    answer = lower_keys({"updates": [_entry("006-B0000-00", "X", "Firmware", "Garmin\\Other",
                                            "fw.bin", b"x")]})
    (alone,) = updates.parse_updates(answer)
    (with_device,) = updates.parse_updates(answer, _device_with_parts())
    assert (alone.is_system, with_device.is_system) == (False, True)


def test_a_system_update_is_copied_last_and_asks_for_a_restart(device_root, tmp_path,
                                                               monkeypatch):
    items = updates.parse_updates(lower_keys(server_answer()))
    assert [u.part_number for u in updates.sort_for_install(items)] == [
        "006-D0000-00", "006-V0000-00"]  # the bundle last, although its order is lower

    def fetch(urls, dest, size, md5, session=None, progress=None):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(BUNDLE if dest.name == "GUPDATE.GSP" else FONT)
        return dest

    monkeypatch.setattr(download, "fetch", fetch)
    monkeypatch.setattr(updates, "cache_path", lambda u: tmp_path / "cache" / u.filename)
    copied = []
    storage = LocalStorage(device_root)
    write = storage.write_file
    monkeypatch.setattr(storage, "write_file",
                        lambda path, local, progress=None, **kw: copied.append(path) or write(
                            path, local, progress, **kw))
    report = updates.install(items, storage)
    assert copied == ["Garmin/Font/006-D0000-00.fnt", "Garmin/GUPDATE.GSP"]
    assert any(u.installs_when_disconnected for u in report.written)
    assert (device_root / "GARMIN/GUPDATE.GSP").read_bytes() == BUNDLE


def test_plain_files_alone_ask_for_no_restart(device_root, tmp_path, monkeypatch):
    (font,) = [u for u in updates.parse_updates(lower_keys(server_answer())) if not u.is_system]

    def fetch(urls, dest, size, md5, session=None, progress=None):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(FONT)
        return dest

    monkeypatch.setattr(download, "fetch", fetch)
    monkeypatch.setattr(updates, "cache_path", lambda u: tmp_path / "cache" / u.filename)
    report = updates.install([font], LocalStorage(device_root))
    assert report.written and not any(u.installs_when_disconnected for u in report.written)


def test_an_update_the_drive_keeps_with_other_bytes_is_not_told_copied(device_root, tmp_path,
                                                                        monkeypatch):
    """Read back from the drive's medium and compared with the server's MD5: other bytes of
    the same size fail the install, and the file is not left for the device."""
    (font,) = [u for u in updates.parse_updates(lower_keys(server_answer())) if not u.is_system]

    def fetch(urls, dest, size, md5, session=None, progress=None):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(FONT)
        return dest

    monkeypatch.setattr(download, "fetch", fetch)
    monkeypatch.setattr(updates, "cache_path", lambda u: tmp_path / "cache" / u.filename)
    with pytest.raises(updates.UpdateError, match="Write verification"):
        updates.install([font], other_bytes_drive()(device_root))
    assert LocalStorage(device_root).resolve(font.device_path) is None


def test_cli_lists_the_system_update_first_and_marks_it(offered, no_download, capsys):
    assert main(["--path", str(offered), "updates"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[:4] == [
        "Test watch, software 23.39",
        "Available updates: 2",
        "  006-V0000-00         23.40  Recommended       12 B  Test Bundle [PRIMARY]",
        "  006-D0000-00         23.40  Recommended        4 B  Test Font"]


def test_cli_warns_before_a_system_update(offered, no_download, capsys):
    assert main(["--path", str(offered), "updates", "--install", "006-V0000-00"]) == 0
    out = capsys.readouterr().out
    assert ("Warning: this is a device system update. Charge the device and do not disconnect "
            "it while copying.") in out
    assert "Dry run: nothing was downloaded or written." in out
    assert not (offered / "GARMIN/GUPDATE.GSP").exists()


def test_cli_does_not_warn_before_a_plain_file(offered, no_download, capsys):
    assert main(["--path", str(offered), "updates", "--install", "006-D0000-00"]) == 0
    out = capsys.readouterr().out
    assert "Warning: this is a device system update" not in out
    assert "Dry run: nothing was downloaded or written." in out


# --- the software of a part of the device (its GPS chip) ----------------------------------------

GPS = b"gps software"
# At the same file name as the device's own software, in another folder, and it asks for a
# restart: still a part with a version of its own, which says nothing of the device's.
GPS_ENTRY = {**_entry("006-B0001-00", "Test GPS Software", "Firmware", "GARMIN\\REMOTESW",
                      "GUPDATE.GCD", GPS, restart=True), "displayVersion": "6.70"}


def _device_with_parts():
    """The device of these tests (software 23.39, part number 006-B0000-00) with the versions
    of its parts as its GarminDevice.xml lists them."""
    return types.SimpleNamespace(
        description="Test watch", part_number="006-B0000-00", software_version_str="23.39",
        unit_id=None, update_files=[
            UpdateFile("006-B0000-00", "23.39", "GARMIN", "GUPDATE.GCD"),
            UpdateFile("006-B0001-00", "6.10", "GARMIN/REMOTESW", "GUPDATE.GCD")])


def test_the_one_update_of_a_part_is_told_by_its_name():
    from openexpress.gui import words

    (gps,) = updates.parse_updates(lower_keys({"updates": [GPS_ENTRY]}), _device_with_parts())
    assert (gps.is_system, gps.installs_when_disconnected) == (False, True)
    assert gps.installed_version == "6.10"
    assert words.updates_available(1, gps) == "Update available: Test GPS Software 6.70"


def test_cli_lists_the_software_of_a_part_by_its_name(device_root, monkeypatch, no_download,
                                                       capsys):
    answer = lower_keys({"updates": [GPS_ENTRY]})
    monkeypatch.setattr(GarminClient, "software_updates",
                        lambda self, raw, include_reinstalls=False: answer)
    assert main(["--path", str(device_root), "updates"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[:3] == [
        "Test watch, software 23.39",
        "Available updates: 1",
        "  006-B0001-00          6.70  Recommended       12 B  Test GPS Software"]  # no [PRIMARY]


@pytest.mark.needs_display
def test_the_software_of_a_part_is_shown_with_its_own_versions():
    device = _device_with_parts()
    items = updates.parse_updates(lower_keys({"updates": [GPS_ENTRY]}), device)
    _win, status, groups, _buttons = _updates_page(items, device=device)
    texts = ([text for text, _styles in status["texts"]]
             + [r.get_subtitle() for g in groups for r in g.rows])
    assert not any("23.39\u00a0→" in text for text in texts)  # never the device's software
    assert status["texts"][:2] == [("Update available: Test GPS Software 6.70", ["title-1"]),
                                   ("Software 23.39", ["dim-label"])]
    (available,) = groups
    assert [(r.get_title(), r.get_subtitle()) for r in available.rows] == [
        ("Test GPS Software",
         "6.10\u00a0→\u00a06.70\u00a0· 12\u00a0B\u00a0· Recommended\u00a0· GUPDATE.GCD")]


@pytest.mark.needs_display
def test_the_question_names_the_versions_of_a_part(monkeypatch):
    body, warning = _question(monkeypatch, "006-B0001-00", {"updates": [GPS_ENTRY]},
                              _device_with_parts())
    assert body.splitlines()[0] == "Test GPS Software 6.10\u00a0→\u00a06.70"
    # Copied with the same care, and said as what it is: not the device's system.
    assert warning == (PART_WARNING_EN, ["heading", "warning"])


def test_cli_warns_before_the_software_of_a_part_as_such(device_root, monkeypatch, no_download,
                                                        capsys):
    answer = lower_keys({"updates": [GPS_ENTRY]})
    monkeypatch.setattr(GarminClient, "software_updates",
                        lambda self, raw, include_reinstalls=False: answer)
    monkeypatch.setattr(GarminClient, "unit_info", lambda self, unit_id, part_number: {})
    assert main(["--path", str(device_root), "updates", "--install", "006-B0001-00"]) == 0
    out = capsys.readouterr().out
    assert PART_WARNING_EN in out and WARNING_EN not in out


# --- two updates for one file of the device ----------------------------------------------------

PART = b"part software"
# Two updates the server puts at one file of the device: the device's own software, and the
# software of a part of it.
SAME_FILE = [
    {**_entry("006-B0001-00", "Test Part Software", "Firmware", "Garmin", "GUPDATE.GCD", PART,
              order=0, restart=True), "displayVersion": "6.70"},
    _entry("006-B0000-00", "Test Software", "Firmware", "GARMIN", "gupdate.gcd", BUNDLE,
           order=1, primary=True),
]


def _fetch_by_part(monkeypatch, tmp_path):
    """Downloads that give each update its own bytes (their addresses differ), from nowhere."""
    by_url = {e["deliverable"]["downloadUrl"]: e for e in SAME_FILE}
    assert len(by_url) == len(SAME_FILE)

    def fetch(urls, dest, size, md5, session=None, progress=None):
        dest.parent.mkdir(parents=True, exist_ok=True)
        entry = by_url[urls[0]]
        dest.write_bytes(PART if entry["partNumber"] == "006-B0001-00" else BUNDLE)
        return dest

    monkeypatch.setattr(download, "fetch", fetch)
    monkeypatch.setattr(updates, "cache_path",
                        lambda u: tmp_path / "cache" / u.part_number / u.filename)


def test_two_updates_for_one_file_are_copied_one_at_a_time(device_root, tmp_path, monkeypatch,
                                                           caplog):
    """Copied together, the second would replace the first before the device installed it:
    only the device's own software is copied, the other waits for the next connection."""
    _fetch_by_part(monkeypatch, tmp_path)
    items = updates.parse_updates(lower_keys({"updates": SAME_FILE}))
    with caplog.at_level(logging.INFO, logger="openexpress.updates"):
        report = updates.install(items, LocalStorage(device_root))
    assert [u.part_number for u in report.written if not u.is_metadata] == ["006-B0000-00"]
    # The name the server gives, in its letter case.
    assert [p.name for p in (device_root / "GARMIN").iterdir()
            if p.name.casefold() == "gupdate.gcd"] == ["gupdate.gcd"]
    assert (device_root / "GARMIN/gupdate.gcd").read_bytes() == BUNDLE
    assert ("software update: 006-B0001-00 6.70 put off, it goes to the file of 006-B0000-00 "
            "23.40 (Garmin/GUPDATE.GCD): one at a time") in [r.getMessage() for r in caplog.records]


def test_cli_says_which_update_waits_for_the_next_connection(device_root, tmp_path, monkeypatch,
                                                            capsys, caplog):
    _fetch_by_part(monkeypatch, tmp_path)
    answer = lower_keys({"updates": SAME_FILE})
    monkeypatch.setattr(GarminClient, "software_updates",
                        lambda self, raw, include_reinstalls=False: answer)
    monkeypatch.setattr(GarminClient, "unit_info", lambda self, unit_id, part_number: {})
    with caplog.at_level(logging.INFO, logger="openexpress.updates"):
        assert main(["--path", str(device_root), "updates", "--install", "all", "--apply"]) == 0
    assert ("software update: 006-B0001-00 6.70 put off, it goes to the file of 006-B0000-00 "
            "23.40 (Garmin/GUPDATE.GCD): one at a time") in [r.getMessage() for r in caplog.records]
    out = capsys.readouterr().out
    assert ("Not now: Test Part Software 6.70 goes to the same file on the device as Test "
            "Software 23.40. Install it once the device has installed Test Software: disconnect "
            "the device, then connect it again.") in out
    assert "Copied 1 file." in out
    assert (device_root / "GARMIN/gupdate.gcd").read_bytes() == BUNDLE


def test_a_new_version_wins_over_a_reinstall_of_its_part_for_one_file():
    """The server may offer a part again (a reinstall) beside its new version, at one file:
    the new version is copied, the reinstall waits, and neither drops the other."""
    old = {**_entry("006-B0000-00", "Test Software", "Firmware", "Garmin", "GUPDATE.GCD", b"old",
                    primary=True), "displayVersion": "23.39"}
    old["installation"]["isReinstall"] = True
    new = _entry("006-B0000-00", "Test Software", "Firmware", "Garmin", "GUPDATE.GCD", BUNDLE,
                 primary=True)
    items = updates.parse_updates(lower_keys({"updates": [old, new]}))
    kept, put_off = updates.one_per_file(items)
    assert [u.version for u in kept] == ["23.40"]
    assert [(u.version, other.version) for u, other in put_off] == [("23.39", "23.40")]


def test_cli_does_not_write_over_an_update_that_waits_for_the_device(device_root, monkeypatch,
                                                                    no_download, capsys):
    """The GPS update copied already waits in its file for the device: the CLI writes no
    other update to that file, and says when it can."""
    sensor = {**_entry("006-B0002-00", "Test Sensor Software", "Firmware", "GARMIN\\REMOTESW",
                       "gupdate.gcd", b"sensor", restart=True), "displayVersion": "1.20"}
    (device_root / "GARMIN" / "REMOTESW").mkdir()
    (device_root / "GARMIN" / "REMOTESW" / "GUPDATE.GCD").write_bytes(GPS)
    answer = lower_keys({"updates": [GPS_ENTRY, sensor]})
    monkeypatch.setattr(GarminClient, "software_updates",
                        lambda self, raw, include_reinstalls=False: answer)
    monkeypatch.setattr(GarminClient, "unit_info", lambda self, unit_id, part_number: {})
    assert main(["--path", str(device_root), "updates", "--install", "all", "--apply"]) == 0
    out = capsys.readouterr().out
    assert ("Not now: Test Sensor Software 1.20 goes to the same file on the device as Test GPS "
            "Software 6.70. Install it once the device has installed Test GPS Software: "
            "disconnect the device, then connect it again.") in out
    assert (device_root / "GARMIN" / "REMOTESW" / "GUPDATE.GCD").read_bytes() == GPS


@pytest.mark.needs_display
def test_a_copy_that_wrote_nothing_is_not_shown_copied_nor_ejected(monkeypatch, in_the_window):
    """Only what the install wrote is shown on the device, and a drive is ejected only after
    something was written to it."""
    from gi.repository import Adw

    from openexpress.gui.app import MainWindow
    from openexpress.updates import InstallReport

    items = updates.parse_updates(lower_keys(server_answer()))
    win, _status, _groups, _buttons = _updates_page(items, drive=True)
    given = []
    monkeypatch.setattr(updates, "install",
                        lambda chosen, storage, **kw: given.append(chosen) or InstallReport())
    asked = []
    monkeypatch.setattr(Adw.AlertDialog, "present", lambda self, parent: asked.append(self))
    win.session.client = types.SimpleNamespace(http=None)
    win.session.storage = None
    win.updates_beta, win.shown = False, []
    win.run_task = RunNow()
    win.error = lambda title, text: win.shown.append(title)
    win.progress = win.fraction = None
    for name in ("show_updates", "_show_installed"):
        setattr(win, name, types.MethodType(getattr(MainWindow, name), win))
    in_the_window("is_bike_accessory", lambda client, device, ask: False)
    MainWindow.confirm_update(win, items[1])
    asked[0].emit("response", "ok")
    assert given == [[items[1]]]  # the update clicked, nothing else of its part number
    assert win.ejected == []
    assert not any(u.on_device for u in items)
    assert win.shown == []  # no "Done"


@pytest.mark.needs_display
def test_an_update_for_the_file_of_one_copied_already_waits(monkeypatch):
    """While a copied update waits in a file for the device, another one for that file has
    no Install: copying it would replace the first."""
    sensor = {**_entry("006-B0002-00", "Test Sensor Software", "Firmware", "GARMIN\\REMOTESW",
                       "gupdate.gcd", b"sensor", restart=True), "displayVersion": "1.20"}
    gps, other = _items(GPS_ENTRY, sensor)
    gps.on_device = True  # copied, the device has not installed it yet
    _win, status, groups, _buttons = _updates_page([gps, other])
    assert status["texts"][0][0].startswith("Update ready")
    (available,) = groups
    (row,) = available.rows
    assert row.get_title() == "Test Sensor Software"
    assert row.get_subtitle().splitlines()[1] == (
        "Can be installed once the device has installed Test GPS Software")
    assert _row_buttons(row) == []


def _row_buttons(row):
    """The labels of the buttons of a row."""
    from gi.repository import Gtk

    def buttons(widget):
        child = widget.get_first_child()
        while child is not None:
            if isinstance(child, Gtk.Button):
                yield child
            yield from buttons(child)
            child = child.get_next_sibling()

    return [b.get_label() for b in buttons(row)]


# --- an update copied before that the server offers again ---------------------------------------

UNIT = 1234567890  # of the GarminDevice.xml of these tests (conftest)
COPIED_AT = datetime(2026, 11, 1, 11, 37).timestamp()
COPIED_WHEN = "2026-11-01 11:37"


def _remember_the_device():
    from openexpress import known_devices

    known_devices.remember(UNIT, "Test watch", "006-B0000-00", "23.39", "ums")


def _offered_now(root, *entries):
    """What the server offers the device in `root` (its own GarminDevice.xml), compared with
    it."""
    from openexpress.device.xmldevice import parse

    device = parse((root / "GARMIN" / "GarminDevice.xml").read_bytes())
    return updates.offered(LocalStorage(root), lower_keys({"updates": list(entries)}), device)


def test_a_copied_update_is_kept_until_the_server_offers_it_no_more(device_root, tmp_path,
                                                                   monkeypatch):
    """What openExpress copied is kept with the device (devices.json: only the part number,
    the version and when), as long as the server offers it: then the device did not install
    it. Offered no more, it is forgotten."""
    from openexpress import known_devices

    _remember_the_device()
    (font,) = [u for u in updates.parse_updates(lower_keys(server_answer())) if not u.is_system]

    def fetch(urls, dest, size, md5, session=None, progress=None):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(FONT)
        return dest

    monkeypatch.setattr(download, "fetch", fetch)
    monkeypatch.setattr(updates, "cache_path", lambda u: tmp_path / "cache" / u.filename)
    updates.install([font], LocalStorage(device_root), unit_id=UNIT)
    (key,) = dict(known_devices.load()[UNIT].copied_updates)
    assert key == "006-D0000-00.23.40"
    (again,) = _offered_now(device_root, FONT_ENTRY)
    assert again.copied_at == dict(known_devices.load()[UNIT].copied_updates)[key]
    assert _offered_now(device_root) == []
    assert known_devices.load()[UNIT].copied_updates == ()


@pytest.mark.parametrize(("on_the_device", "changed", "said"), [
    (None, False, ""),
    (bytes(len(GPS)), True, ("; the file at its place has other bytes of the same size: the "
                             "device changed or refused it")),
], ids=["not-there", "other-bytes"])
def test_an_update_copied_before_and_offered_again_is_said(device_root, caplog, on_the_device,
                                                           changed, said):
    """Offered again after it was copied: the device did not install it. A file of its size
    with other bytes at its place: the device changed it, or refused it."""
    from openexpress import known_devices

    _remember_the_device()
    known_devices.note_copied(UNIT, ["006-B0001-00.6.70"], now=COPIED_AT)
    if on_the_device is not None:
        (device_root / "GARMIN" / "REMOTESW").mkdir()
        (device_root / "GARMIN" / "REMOTESW" / "GUPDATE.GCD").write_bytes(on_the_device)
    with caplog.at_level(logging.INFO, logger="openexpress.updates"):
        (gps,) = _offered_now(device_root, GPS_ENTRY)
    assert (gps.copied_at, gps.changed_on_device, gps.on_device) == (COPIED_AT, changed, False)
    assert (f"software update: 006-B0001-00 6.70 copied 2026-11-01T11:37 is offered again{said}"
            in [r.getMessage() for r in caplog.records])


def test_cli_tells_an_update_copied_before_and_offered_again(device_root, monkeypatch,
                                                            no_download, capsys):
    from openexpress import known_devices

    _remember_the_device()
    known_devices.note_copied(UNIT, ["006-B0001-00.6.70"], now=COPIED_AT)
    answer = lower_keys({"updates": [GPS_ENTRY]})
    monkeypatch.setattr(GarminClient, "software_updates",
                        lambda self, raw, include_reinstalls=False: answer)
    assert main(["--path", str(device_root), "updates"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[2:4] == [
        "  006-B0001-00          6.70  Recommended       12 B  Test GPS Software",
        f"      Copied {COPIED_WHEN}: check on the device that it is installed."]


def test_a_check_that_keeps_nothing_forgets_nothing(device_root):
    """`openexpress status --online` keeps nothing: what the server no longer offers is not
    forgotten by its check."""
    from openexpress import known_devices

    _remember_the_device()
    known_devices.note_copied(UNIT, ["006-B0001-00.6.70"], now=COPIED_AT)
    before = known_devices.path().read_bytes()
    from openexpress.device.xmldevice import parse

    device = parse((device_root / "GARMIN" / "GarminDevice.xml").read_bytes())
    updates.offered(LocalStorage(device_root), {"updates": []}, device, forget_installed=False)
    assert known_devices.path().read_bytes() == before



def _font_on_the_device(root):
    (root / "GARMIN" / "Font").mkdir(parents=True, exist_ok=True)
    (root / "GARMIN" / "Font" / "006-D0000-00.fnt").write_bytes(FONT)


@pytest.mark.parametrize(("copied", "waits"), [(True, True), (False, False)],
                         ids=["copied-by-openexpress", "there-otherwise"])
def test_a_component_copied_waits_for_the_device_until_it_is_offered_no_more(device_root,
                                                                            copied, waits):
    """A component openExpress copied that the server still offers, with the very same file
    on the device: the device has not taken it in yet, and it waits as a copied update of the
    device's software does (staged). Its file there without such a copy is no update (a map
    support file the device briefly does not list). Offered no more: installed."""
    from openexpress import known_devices

    _remember_the_device()
    if copied:
        known_devices.note_copied(UNIT, ["006-D0000-00.23.40"], now=COPIED_AT)
    _font_on_the_device(device_root)
    items = _offered_now(device_root, FONT_ENTRY)
    assert updates.pending(items) == []
    assert [u.part_number for u in updates.staged(items)] == (["006-D0000-00"] if waits else [])
    assert updates.staged(_offered_now(device_root)) == []  # installed: offered no more


def test_cli_says_a_copied_component_waits_for_the_device(device_root, monkeypatch, capsys):
    from openexpress import known_devices

    _remember_the_device()
    known_devices.note_copied(UNIT, ["006-D0000-00.23.40"], now=COPIED_AT)
    _font_on_the_device(device_root)
    answer = lower_keys({"updates": [FONT_ENTRY]})
    monkeypatch.setattr(GarminClient, "software_updates",
                        lambda self, raw, include_reinstalls=False: answer)
    assert main(["--path", str(device_root), "updates"]) == 0
    out = capsys.readouterr().out
    assert "Update copied, waiting for the device to install it:" in out
    assert "Test Font" in out.split("Update copied, waiting")[1]

def test_what_was_copied_before_a_failure_is_kept(device_root, tmp_path, monkeypatch):
    """A copy that fails after some updates were written: those are kept as copied."""
    from openexpress import known_devices

    _remember_the_device()
    items = updates.parse_updates(lower_keys(server_answer()))  # the font, then the bundle

    def fetch(urls, dest, size, md5, session=None, progress=None):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(BUNDLE if dest.name == "GUPDATE.GSP" else FONT)
        return dest

    monkeypatch.setattr(download, "fetch", fetch)
    monkeypatch.setattr(updates, "cache_path", lambda u: tmp_path / "cache" / u.filename)
    storage = LocalStorage(device_root)
    write = storage.write_file_checked

    def bundle_fails(path, local, progress=None, **kw):
        if path.endswith("GUPDATE.GSP"):
            raise updates.UpdateError("the cable came out")
        return write(path, local, progress, **kw)

    monkeypatch.setattr(storage, "write_file_checked", bundle_fails)
    with pytest.raises(updates.UpdateError):
        updates.install(items, storage, unit_id=UNIT)
    assert list(dict(known_devices.load()[UNIT].copied_updates)) == ["006-D0000-00.23.40"]


@pytest.mark.needs_display
def test_a_part_in_the_top_folder_is_never_shown_against_the_device_software():
    """A part's system file in the device's top folder, whose version GarminDevice.xml does
    not list: only its own version, as the device's software version is not its."""
    from openexpress.gui.updates_page import versions_text

    part = {**_entry("006-B0001-00", "Test Part Software", "Firmware", "GARMIN", "GUPDATE.GCD",
                     PART), "displayVersion": "6.70"}
    device = types.SimpleNamespace(description="Test watch", part_number="006-B0000-00",
                                   software_version_str="23.39", unit_id=None, update_files=[])
    (u,) = updates.parse_updates(lower_keys({"updates": [part]}), device)
    assert u.installed_version is None
    assert versions_text(u, device) == "6.70"


@pytest.mark.needs_display
@pytest.mark.parametrize(("changed", "note"), [
    (False, f"Copied {COPIED_WHEN}, check on the device that it is installed"),
    (True, f"The device changed or refused the file copied {COPIED_WHEN}"),
])
def test_the_window_tells_an_update_copied_before_and_offered_again(changed, note):
    """Its row says when it was copied, and Reinstall instead of Install."""
    device = _device_with_parts()
    (gps,) = updates.parse_updates(lower_keys({"updates": [GPS_ENTRY]}), device)
    gps.copied_at, gps.changed_on_device = COPIED_AT, changed
    _win, _status, groups, _buttons = _updates_page([gps], device=device)
    (available,) = groups
    (row,) = available.rows
    assert row.get_subtitle().splitlines()[1] == note
    assert _row_buttons(row) == ["Reinstall"]


@pytest.mark.needs_display
def test_the_device_software_copied_before_and_offered_again_is_told_at_the_top():
    (bundle,) = _items(BUNDLE_ENTRY)
    bundle.copied_at = COPIED_AT
    _win, status, _groups, _buttons = _updates_page([bundle])
    assert (f"Copied {COPIED_WHEN}, check on the device that it is installed",
            ["caption", "dim-label"]) in status["texts"]
    assert status["buttons"] == [("Reinstall", False), ("Check Again", False)]


# --- the window asks before it installs ---------------------------------------------------------

WARNING_EN = ("Warning: this is a device system update. Charge the device and do not disconnect "
              "it while copying.")
PART_WARNING_EN = ("Warning: this updates the software of a part of the device. Charge the device "
                   "and do not disconnect it while copying.")
WARNING_PL = ("Ostrzeżenie: to aktualizacja systemu urządzenia. Naładuj urządzenie i\u00a0nie odłączaj "
              "go podczas kopiowania.")


def _dialog(monkeypatch, part, answer=None, device=None):
    """The window's question before `part` is installed; nothing is shown. `device`: its
    GarminDevice.xml, if not the plain one of these tests."""
    from gi.repository import Adw

    from openexpress.gui.app import MainWindow

    items = updates.parse_updates(lower_keys(answer or server_answer()), device)
    asked = []
    monkeypatch.setattr(Adw.AlertDialog, "present", lambda self, parent: asked.append(self))
    win = stand_in(
        session=types.SimpleNamespace(
            device=device or types.SimpleNamespace(software_version_str="23.39")),
        updates_cache=items)
    MainWindow.confirm_update(win, next(u for u in items if u.part_number == part))
    (dialog,) = asked
    return dialog


def _below(dialog):
    """What the question shows below its text, in order."""
    extra = dialog.get_extra_child()
    return [] if extra is None else list(_children(extra))


def _question(monkeypatch, part, answer=None, device=None):
    """The text of the question, and the warning below it (its text and styles) or None."""
    dialog = _dialog(monkeypatch, part, answer, device)
    warnings = [(w.get_label(), sorted(w.get_css_classes())) for w in _below(dialog)
                if w.has_css_class("warning")]
    return dialog.get_body(), (warnings[0] if warnings else None)


def test_two_versions_stay_on_one_line():
    import re

    from openexpress.gui import app
    from openexpress.gui.updates_page import ARROW

    assert VERSIONS == "23.39" + ARROW + "23.40"  # what the window builds
    assert not re.search(r"[ \n]→|→[ \n]", VERSIONS)  # no place to break around the arrow
    # Every arrow between two values in the window goes through the constant, in each module
    # of the window (gui/*.py), not only in app.py; navigation.py has no text of the window,
    # only its line of the log ("N → M devices"), which has plain spaces anyway.
    for module in sorted(Path(app.__file__).parent.glob("*.py")):
        if module.name != "navigation.py":
            assert not re.search(r"\} → \{", module.read_text(encoding="utf-8")), module.name


@pytest.mark.needs_display
def test_the_window_warns_before_a_system_update(monkeypatch):
    body, warning = _question(monkeypatch, "006-V0000-00")
    # The same words as on the command line, below the text: in the warning colour, bold.
    assert warning == (WARNING_EN, ["heading", "warning"])
    assert "Warning" not in body


@pytest.mark.needs_display
def test_the_window_does_not_warn_before_a_plain_file(monkeypatch):
    body, warning = _question(monkeypatch, "006-D0000-00")
    assert warning is None
    assert "Warning" not in body and "disconnect" not in body


@pytest.mark.needs_display
def test_the_warning_in_polish(monkeypatch):
    from openexpress import i18n

    i18n.setup("pl")
    try:
        body, warning = _question(monkeypatch, "006-V0000-00")
    finally:
        i18n.setup("en")
    assert warning == (WARNING_PL, ["heading", "warning"])
    assert body.startswith(f"Test Bundle {VERSIONS}\nRozmiar: 12\u00a0B\n")


@pytest.mark.needs_display
def test_the_question_names_both_versions_of_a_system_update(monkeypatch):
    body, _warning = _question(monkeypatch, "006-V0000-00")
    assert body.splitlines()[0] == f"Test Bundle {VERSIONS}"
    body, _warning = _question(monkeypatch, "006-D0000-00")
    assert body.splitlines()[0] == "Test Font 23.40"  # another update: its name and version


# --- the status at the top of the Updates page ---------------------------------------------------


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

    def clear(self):
        self.groups = []


def _children(widget):
    child = widget.get_first_child()
    while child is not None:
        yield child
        child = child.get_next_sibling()


def _labels(widget):
    """The labels of a line made of parts, in their order, also inside the boxes that hold
    some of them together."""
    if hasattr(widget, "get_label"):
        return [widget]
    return [label for child in _children(widget) for label in _labels(child)]


def _text(widget):
    """A line of the status and its styles; a line made of parts (a beta version) as one text,
    with the styles of each part."""
    if hasattr(widget, "get_label"):
        return widget.get_label(), sorted(widget.get_css_classes())
    parts = _labels(widget)
    return "".join(p.get_label() for p in parts), [sorted(p.get_css_classes()) for p in parts]


def _updates_page(items, beta=False, drive=False, device=None):
    """The Updates page of a window that is never shown; returns the window, the status at the
    top (icon, its styles, the texts with their styles, the buttons) and the other groups.
    `drive`: the device is in mass storage mode; `device`: its GarminDevice.xml, if not the
    plain one of these tests."""
    from openexpress.gui.app import MainWindow

    win = stand_in(
        session=types.SimpleNamespace(device=device or types.SimpleNamespace(
            description="Test watch", software_version_str="23.39", unit_id=None),
            mass_storage=drive),
        sections={"updates": _Section()}, busy=False, _action_buttons=[], asked=[], loads=[],
        ejected=[], _set_update_state=lambda new, staged=(): None)
    win.confirm_update = win.asked.append
    win.eject_device = lambda s, button=None: win.ejected.append(s)
    win.load_section = lambda *a, **kw: win.loads.append((a, kw))
    for name in ("_updates_status", "_update_row", "_button"):
        setattr(win, name, types.MethodType(getattr(MainWindow, name), win))
    MainWindow.show_updates(win, items, beta)
    return (win, *_read_page(win))


def _read_page(win):
    """The status at the top of the Updates page, the other groups and the status buttons."""
    top, *groups = win.sections["updates"].groups
    (box,) = top.rows
    image, *labels, row = _children(box)
    status = {
        "icon": (image.get_icon_name(), image.get_pixel_size(), image.has_css_class("success")),
        "texts": [_text(w) for w in labels],
        "buttons": [(b.get_label(), b.has_css_class("suggested-action")) for b in _buttons(row)],
    }
    return status, groups, _buttons(row)


def _buttons(row):
    """The buttons of the status, also when each is in a FlowBoxChild (an older libadwaita)."""
    from gi.repository import Gtk

    return [b.get_child() if isinstance(b, Gtk.FlowBoxChild) else b for b in _children(row)]


def _items(*answer_entries):
    return updates.parse_updates(lower_keys({"updates": list(answer_entries)}))


BUNDLE_ENTRY = _entry("006-V0000-00", "Test Bundle", "Firmware", "Garmin", "GUPDATE.GSP", BUNDLE)
FONT_ENTRY = _entry("006-D0000-00", "Test Font", "Firmware", "Garmin\\Font", "006-D0000-00.fnt",
                    FONT)


@pytest.mark.needs_display
def test_status_when_the_software_is_up_to_date():
    reinstall = _entry("006-D0001-00", "Old Font", "Firmware", "Garmin\\Font", "old.fnt", FONT)
    reinstall["installation"]["isReinstall"] = True
    win, status, groups, (check,) = _updates_page(_items(reinstall))
    assert status == {
        "icon": ("object-select-symbolic", 96, True),
        "texts": [("Software is up to date", ["title-1"]),  # a title: no full stop
                  ("Software 23.39", ["dim-label"])],
        "buttons": [("Check Again", False)]}
    assert [g.title for g in groups] == ["Advanced"]  # no list of updates; reinstalls in Advanced
    check.emit("clicked")
    assert win.loads == [(("updates",), {"force": True})]


@pytest.mark.needs_display
def test_status_with_a_system_update():
    win, status, groups, (install, _all, _check) = _updates_page(_items(FONT_ENTRY, BUNDLE_ENTRY))
    assert status == {
        "icon": ("software-update-available-symbolic", 96, False),
        "texts": [("2 updates available", ["title-1"]),  # the words of the device's row
                  (f"{VERSIONS}\u00a0· 12\u00a0B", ["dim-label"]),
                  ("Test Bundle\u00a0· Recommended\u00a0· GUPDATE.GSP", ["caption", "dim-label"])],
        "buttons": [("Install", True), ("Install All", False), ("Check Again", False)]}
    # Below: only the other updates.
    (available,) = groups
    assert available.title == "Available Updates"
    assert [(r.get_title(), r.get_subtitle()) for r in available.rows] == [
        ("Test Font", "23.40\u00a0· 4\u00a0B\u00a0· Recommended\u00a0· 006-D0000-00.fnt")]
    install.emit("clicked")
    assert [u.part_number for u in win.asked] == ["006-V0000-00"]  # the question with the warning


@pytest.mark.needs_display
def test_status_with_plain_updates_only():
    other = _entry("006-D0002-00", "Test Map Data", "Etyp", "Garmin\\SID", "DA00200.sid", b"sid")
    _win, status, groups, _buttons = _updates_page(_items(FONT_ENTRY, other))
    assert status == {
        "icon": ("software-update-available-symbolic", 96, False),
        "texts": [("2 updates available", ["title-1"]),
                  ("Software 23.39", ["dim-label"])],
        "buttons": [("Install All", True), ("Check Again", False)]}
    (available,) = groups
    assert [r.get_title() for r in available.rows] == ["Test Font", "Test Map Data"]


@pytest.mark.needs_display
def test_a_second_system_update_is_listed_as_system_software():
    second = _entry("006-B0000-00", "Test Software", "Firmware", "Garmin", "GUPDATE.GCD", b"fw")
    _win, status, groups, _buttons = _updates_page(_items(BUNDLE_ENTRY, second))
    assert status["texts"][2][0] == "Test Bundle\u00a0· Recommended\u00a0· GUPDATE.GSP"  # the first on top
    (available,) = groups
    assert [(r.get_title(), r.get_subtitle()) for r in available.rows] == [
        ("System Software", "Test Software\u00a0· 23.40\u00a0· 2\u00a0B\u00a0· Recommended\u00a0· GUPDATE.GCD")]


@pytest.mark.needs_display
def test_the_status_in_polish():
    from openexpress import i18n

    i18n.setup("pl")
    try:
        _w, current, _g, _b = _updates_page([])
        _w, system, groups, _b = _updates_page(_items(BUNDLE_ENTRY, FONT_ENTRY))
        _w, plain, _g, _b = _updates_page(_items(FONT_ENTRY))
        second = _entry("006-B0000-00", "Test Software", "Firmware", "Garmin", "GUPDATE.GCD",
                        b"fw")
        _w, _s, (listed,), _b = _updates_page(_items(BUNDLE_ENTRY, second))
    finally:
        i18n.setup("en")
    assert [t for t, _c in current["texts"]] == ["Oprogramowanie jest aktualne",
                                                 "Wersja oprogramowania 23.39"]
    assert current["buttons"] == [("Sprawdź ponownie", False)]
    assert [t for t, _c in system["texts"]] == [
        "Dostępne są 2 aktualizacje", f"{VERSIONS}\u00a0· 12\u00a0B",
        "Test Bundle\u00a0· Zalecana\u00a0· GUPDATE.GSP"]
    assert system["buttons"] == [("Zainstaluj", True), ("Zainstaluj wszystkie", False),
                                 ("Sprawdź ponownie", False)]
    assert groups[0].title == "Dostępne aktualizacje"
    assert plain["texts"][0][0] == "Dostępna aktualizacja: Test Font 23.40"  # as on the row
    assert listed.rows[0].get_title() == "Oprogramowanie systemowe"


# --- beta versions ---------------------------------------------------------------------------------

BETA = "public_beta_phased_rollout"  # as the server names a beta offered to some devices


def beta_answer(bundle_state=BETA):
    """The answer for a device in Garmin's beta program: the device may get beta versions
    ("accessLevel"), and the bundle is one ("releaseState"); the plain file is not."""
    answer = server_answer()
    answer["accessLevel"] = "public_beta"
    answer["updates"][1]["releaseState"] = bundle_state
    return answer


@pytest.mark.parametrize(("state", "beta"), [
    (BETA, True), ("Public_Beta", True), ("live", False), ("Live", False), ("archived", False),
    ("", False), (None, False)])
def test_which_versions_are_beta(state, beta):
    answer = lower_keys({"updates": [_entry("006-X0000-00", "X", "Firmware", "Garmin", "x.bin",
                                            b"x", release_state=state)]})
    (u,) = updates.parse_updates(answer)
    assert u.is_beta is beta


@pytest.mark.parametrize(("level", "offered"), [
    ("public_beta", True), ("PUBLIC_BETA", True), ("live", False), ("", False), (None, False)])
def test_whether_beta_versions_are_offered(level, offered):
    assert updates.beta_offered(lower_keys({"accessLevel": level, "updates": []})) is offered
    assert updates.beta_offered({"updates": []}) is False  # an answer without the field


@pytest.fixture
def offered_beta(device_root, monkeypatch):
    def answer(bundle_state=BETA):
        found = lower_keys(beta_answer(bundle_state))
        monkeypatch.setattr(GarminClient, "software_updates",
                            lambda self, raw, include_reinstalls=False: found)
        monkeypatch.setattr(GarminClient, "unit_info", lambda self, unit_id, part_number: {})
        return device_root

    return answer


def test_cli_marks_a_beta_version(offered_beta, no_download, capsys):
    assert main(["--path", str(offered_beta()), "updates"]) == 0
    assert capsys.readouterr().out.splitlines()[:5] == [
        "Test watch, software 23.39",
        "Garmin offers beta software for this device.",
        "Available updates: 2",
        "  006-V0000-00         23.40  Recommended       12 B  Test Bundle [PRIMARY] [BETA]",
        "  006-D0000-00         23.40  Recommended        4 B  Test Font"]


def test_cli_says_beta_versions_are_offered_when_none_is_listed(offered_beta, no_download,
                                                                capsys):
    assert main(["--path", str(offered_beta("live")), "updates"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[1] == "Garmin offers beta software for this device."
    assert not any("[BETA]" in line for line in out)


def test_cli_marks_beta_in_polish(offered_beta, no_download, capsys):
    from openexpress import i18n

    try:
        assert main(["--path", str(offered_beta()), "--lang", "pl", "updates"]) == 0
    finally:
        i18n.setup("en")
    out = capsys.readouterr().out.splitlines()
    assert out[1] == "Garmin oferuje dla tego urządzenia oprogramowanie w wersji beta."
    assert out[3].endswith("Test Bundle [GŁÓWNE] [BETA]")


@pytest.mark.needs_display
def test_status_with_a_beta_system_update():
    items = updates.parse_updates(lower_keys(beta_answer()))
    _win, status, _groups, _buttons = _updates_page(items, beta=True)
    assert status["texts"] == [
        ("2 updates available", ["title-1"]),
        # "Beta" in the warning colour, not dimmed like the rest of the line.
        (f"{VERSIONS}\u00a0· Beta\u00a0· 12\u00a0B",
         [["dim-label"], ["heading", "warning"], ["dim-label"], ["dim-label"]]),
        ("Test Bundle\u00a0· Recommended\u00a0· GUPDATE.GSP", ["caption", "dim-label"]),
        ("Garmin offers beta software for this device.", ["caption", "dim-label"])]


@pytest.mark.needs_display
def test_status_says_beta_versions_are_offered_when_none_is_listed():
    # The device is in the beta program, but the system update offered is not a beta.
    items = updates.parse_updates(lower_keys(beta_answer("live")))
    _win, status, _groups, _buttons = _updates_page(items, beta=True)
    assert [t for t, _c in status["texts"]][1:] == [
        f"{VERSIONS}\u00a0· 12\u00a0B", "Test Bundle\u00a0· Recommended\u00a0· GUPDATE.GSP",
        "Garmin offers beta software for this device."]
    # Up to date: the line is there as well.
    _win, status, _groups, _buttons = _updates_page([], beta=True)
    assert [t for t, _c in status["texts"]] == [
        "Software is up to date", "Software 23.39",
        "Garmin offers beta software for this device."]


@pytest.mark.needs_display
def test_status_without_beta_has_no_beta_line():
    _win, status, _groups, _buttons = _updates_page([])
    assert not any("beta" in t.casefold() for t, _c in status["texts"])


def test_the_page_learns_from_the_answer_whether_beta_versions_are_offered(device_root):
    from openexpress.gui.app import MainWindow

    shown = []
    win = stand_in(
        session=types.SimpleNamespace(
            storage=LocalStorage(device_root), device=types.SimpleNamespace(raw=b"<Device/>"),
            unit_info_answer={},  # asked when the device was opened
            client=types.SimpleNamespace(
                software_updates=lambda raw, include_reinstalls=False: lower_keys(beta_answer()))),
        show_updates=lambda items, beta=False: shown.append(([u.part_number for u in items], beta)))
    win._load = lambda key, title, work, show, on_error=None: show(work())
    MainWindow.load_updates(win)
    assert shown == [(["006-D0000-00", "006-V0000-00"], True)]


def _beta_question(monkeypatch, bundle_state):
    return _question(monkeypatch, "006-V0000-00", beta_answer(bundle_state))


@pytest.mark.needs_display
def test_the_window_says_it_is_a_beta_before_installing(monkeypatch):
    body, warning = _beta_question(monkeypatch, BETA)
    assert body.endswith("\n\nThis is a beta version.")  # the warning follows, below the text
    assert warning == (WARNING_EN, ["heading", "warning"])
    body, _warning = _beta_question(monkeypatch, "live")
    assert "beta" not in body.casefold()


@pytest.mark.needs_display
def test_beta_in_the_window_in_polish(monkeypatch):
    from openexpress import i18n

    i18n.setup("pl")
    try:
        body, warning = _beta_question(monkeypatch, BETA)
        items = updates.parse_updates(lower_keys(beta_answer()))
        _w, status, _g, _b = _updates_page(items, beta=True)
    finally:
        i18n.setup("en")
    assert body.endswith("\n\nTo jest wersja beta.")
    assert warning == (WARNING_PL, ["heading", "warning"])
    assert [t for t, _c in status["texts"]] == [
        "Dostępne są 2 aktualizacje", f"{VERSIONS}\u00a0· Beta\u00a0· 12\u00a0B",
        "Test Bundle\u00a0· Zalecana\u00a0· GUPDATE.GSP",
        "Garmin oferuje dla tego urządzenia oprogramowanie w\u00a0wersji beta."]


def test_cli_marks_a_beta_version_in_what_will_be_installed(offered_beta, no_download, capsys):
    root = offered_beta()
    assert main(["--path", str(root), "updates", "--install", "006-V0000-00"]) == 0
    assert ("  006-V0000-00 23.40  Test Bundle [BETA]  (12 B) -> Garmin/GUPDATE.GSP"
            in capsys.readouterr().out.splitlines())
    # Not a beta: no mark.
    assert main(["--path", str(root), "updates", "--install", "006-D0000-00"]) == 0
    assert ("  006-D0000-00 23.40  Test Font  (4 B) -> Garmin/Font/006-D0000-00.fnt"
            in capsys.readouterr().out.splitlines())


def test_cli_marks_a_beta_version_in_what_will_be_installed_in_polish(offered_beta, no_download,
                                                                      capsys):
    from openexpress import i18n

    try:
        assert main(["--path", str(offered_beta()), "--lang", "pl", "updates", "--install",
                     "006-V0000-00"]) == 0
    finally:
        i18n.setup("en")
    out = capsys.readouterr().out.splitlines()
    assert "Do zainstalowania:" in out
    assert "  006-V0000-00 23.40  Test Bundle [BETA]  (12 B) -> Garmin/GUPDATE.GSP" in out


@pytest.mark.needs_display
def test_the_beta_mark_is_a_part_of_its_own_only_for_a_beta():
    from gi.repository import Gtk

    for state, beta in ((BETA, True), ("live", False)):
        _win, status, _groups, _buttons = _updates_page(
            updates.parse_updates(lower_keys(beta_answer(state))))
        text, styles = status["texts"][1]
        if beta:
            assert text == f"{VERSIONS}\u00a0· Beta\u00a0· 12\u00a0B"
            assert styles[1] == ["heading", "warning"] and "dim-label" not in styles[1]
        else:  # one label
            assert (text, styles) == (f"{VERSIONS}\u00a0· 12\u00a0B", ["dim-label"])
    # The line of parts is centred like the labels around it, and its parts go on to the next
    # line in a narrow window: a separator ends a part, it never starts one.
    from openexpress.gui.window_tasks import WRAP_BOX

    win, _status, _groups, _buttons = _updates_page(
        updates.parse_updates(lower_keys(beta_answer())))
    (box,) = win.sections["updates"].groups[0].rows
    line = list(_children(box))[2]
    assert isinstance(line, WRAP_BOX or Gtk.FlowBox)
    assert line.get_halign() == Gtk.Align.CENTER
    parts = [_text(part)[0] for part in _children(line)]
    assert parts == [f"{VERSIONS}\u00a0· ", "Beta\u00a0· ", "12\u00a0B"]
    assert not any(part.lstrip().startswith("·") for part in parts)


# --- the license in the question before an update ----------------------------------------------


def _with_license(url, part="006-V0000-00"):
    answer = server_answer()
    for entry in answer["updates"]:
        if entry["partNumber"] == part:
            entry["eulaUrl"] = url
    return answer


@pytest.mark.needs_display
def test_the_license_is_a_link_above_the_warning(monkeypatch):
    from gi.repository import Gtk

    url = "https://example.org/license.txt"
    dialog = _dialog(monkeypatch, "006-V0000-00", _with_license(url))
    link, warning = _below(dialog)  # the warning last, right above the buttons
    assert isinstance(link, Gtk.LinkButton)
    assert (link.get_uri(), link.get_label()) == (url, "License Agreement (EULA)")
    assert warning.get_label() == WARNING_EN
    assert "License" not in dialog.get_body() and url not in dialog.get_body()


@pytest.mark.needs_display
def test_the_license_link_takes_any_letter_case_of_https(monkeypatch):
    from gi.repository import Gtk

    url = "HTTPS://example.org/license.txt"
    (link,) = _below(_dialog(monkeypatch, "006-D0000-00", _with_license(url, "006-D0000-00")))
    assert isinstance(link, Gtk.LinkButton) and link.get_uri() == url  # no warning: a plain file


@pytest.mark.needs_display
def test_no_license_no_link(monkeypatch):
    from gi.repository import Gtk

    (warning,) = _below(_dialog(monkeypatch, "006-V0000-00"))
    assert not isinstance(warning, Gtk.LinkButton) and warning.get_label() == WARNING_EN
    assert _dialog(monkeypatch, "006-D0000-00").get_extra_child() is None  # nothing below


@pytest.mark.needs_display
@pytest.mark.parametrize("url", ["file:///etc/passwd", "http://example.org/license.txt",
                                 "javascript:alert(1)"])
def test_a_license_that_is_not_a_web_address_is_only_text(monkeypatch, url):
    from gi.repository import Gtk

    text, _warning = _below(_dialog(monkeypatch, "006-V0000-00", _with_license(url)))
    assert not isinstance(text, Gtk.LinkButton)
    assert isinstance(text, Gtk.Label) and text.get_label() == f"License: {url}"
    assert not text.get_use_markup()


@pytest.mark.needs_display
def test_the_license_in_polish(monkeypatch):
    from openexpress import i18n

    i18n.setup("pl")
    try:
        link, warning = _below(_dialog(monkeypatch, "006-V0000-00",
                                       _with_license("https://example.org/license.txt")))
        text, _warning = _below(_dialog(monkeypatch, "006-V0000-00",
                                        _with_license("file:///etc/passwd")))
    finally:
        i18n.setup("en")
    assert link.get_label() == "Umowa licencyjna (EULA)"
    assert warning.get_label() == WARNING_PL
    assert text.get_label() == "Licencja: file:///etc/passwd"


# --- a system update copied already, waiting for the device to install it -------------------------


def _copied(device_root, *parts):
    """The device has the very file of these offered updates (as after `updates --install`)."""
    files = {"006-V0000-00": ("GUPDATE.GSP", BUNDLE), "006-D0000-00": ("Font/006-D0000-00.fnt", FONT)}
    for part in parts:
        name, data = files[part]
        (device_root / "GARMIN" / name).parent.mkdir(parents=True, exist_ok=True)
        (device_root / "GARMIN" / name).write_bytes(data)


def test_only_a_system_update_on_the_device_is_staged(device_root):
    _copied(device_root, "006-V0000-00", "006-D0000-00")
    answer = server_answer()
    answer["updates"].append(_entry("006-B0000-00", "Old Software", "Firmware", "Garmin",
                                    "GUPDATE.GCD", b"old"))
    answer["updates"][-1]["installation"]["isReinstall"] = True
    answer["updates"].append(_entry("006-V0000-00-META", "Update information", "UpdateMetadata",
                                    "Garmin", "GUPDATE.GSP", BUNDLE))
    items = updates.mark_present(LocalStorage(device_root),
                                 updates.parse_updates(lower_keys(answer)))
    # The bundle: offered as an update, and the same file is on the device. The font is there
    # too but is no system update; a reinstall and a -META entry are never waiting.
    assert [u.part_number for u in updates.staged(items)] == ["006-V0000-00"]
    assert updates.pending(items) == []



# --- the log names the offered updates ------------------------------------------------------


def _offered_log(caplog):
    from openexpress import applog

    return [applog.redact(r.getMessage()) for r in caplog.records
            if r.getMessage().startswith("updates: ")]


def test_the_log_names_the_offered_updates_and_counts_the_components(device_root, caplog):
    _copied(device_root, "006-V0000-00", "006-D0000-00")
    answer = server_answer()
    answer["updates"].append(_entry("006-D1111-00", "Test Map", "Etyp", "Garmin\\SID",
                                    "new.sid", b"new"))
    answer["updates"].append(_entry("006-B0000-00", "Old Software", "Firmware", "Garmin",
                                    "GUPDATE.GCD", b"old"))
    answer["updates"][-1]["installation"]["isReinstall"] = True
    answer["updates"].append(_entry("006-V0000-00-META", "Update information", "UpdateMetadata",
                                    "Garmin", "GUPDATE.GSP", BUNDLE))
    with caplog.at_level(logging.INFO, logger="openexpress.updates"):
        updates.mark_present(LocalStorage(device_root), updates.parse_updates(lower_keys(answer)),
                             161)
    # In the server's order, each with its file on the device (from the device's folder) and
    # whether it is there; no reinstall, no -META entry and no address.
    assert _offered_log(caplog) == [
        ("updates: 3 offered (006-D0000-00 23.40 at Garmin/Font/006-D0000-00.fnt: matches, "
         "006-V0000-00 23.40 at Garmin/GUPDATE.GSP: matches, 006-D1111-00 23.40 at "
         "Garmin/SID/new.sid: not on the device), 2 of them already on the device; "
         "161 components listed")]


def test_the_log_names_twenty_updates_at_most(device_root, caplog):
    answer = {"updates": [_entry(f"006-D{n:04d}-00", f"Item {n}", "Firmware", "Garmin",
                                 f"f{n}.bin", b"x") for n in range(1, 26)]}
    with caplog.at_level(logging.INFO, logger="openexpress.updates"):
        updates.mark_present(LocalStorage(device_root), updates.parse_updates(lower_keys(answer)),
                             68)
    line = _offered_log(caplog)[0]
    assert line.startswith("updates: 25 offered (006-D0001-00 23.40 at Garmin/f1.bin: not on "
                           "the device, ")
    assert ("006-D0020-00 23.40 at Garmin/f20.bin: not on the device, … and 5 more), 0 of them"
            in line and "006-D0021-00" not in line)


def test_the_log_without_offers_says_zero_offered(device_root, caplog):
    with caplog.at_level(logging.INFO, logger="openexpress.updates"):
        updates.mark_present(LocalStorage(device_root), [], 168)
        updates.mark_present(LocalStorage(device_root), [])  # the count not known
    assert _offered_log(caplog) == [
        "updates: 0 offered, 0 of them already on the device; 168 components listed",
        "updates: 0 offered, 0 of them already on the device"]


@pytest.mark.parametrize("part, version", [
    ("006-DA699-00", "2.70"), ("006-B4532-00", "23.39"), ("006-V4440-00", "33.12")])
def test_part_numbers_and_versions_pass_the_log_filter(part, version):
    from openexpress import applog

    text = f"{part} {version} on the device"
    assert applog.redact(text) == text


def test_the_component_count_never_fails():
    assert updates.component_count(types.SimpleNamespace(update_files=[1, 2, 3])) == 3
    assert updates.component_count(types.SimpleNamespace()) is None
    assert updates.component_count(None) is None


def test_nothing_is_staged_before_the_file_is_copied(device_root):
    items = updates.mark_present(LocalStorage(device_root),
                                 updates.parse_updates(lower_keys(server_answer())))
    assert updates.staged(items) == []


def test_cli_says_a_copied_update_waits_for_the_device(offered, no_download, capsys):
    _copied(offered, "006-V0000-00")
    assert main(["--path", str(offered), "updates"]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "Test watch, software 23.39",
        "Available updates: 1",
        "  006-D0000-00         23.40  Recommended        4 B  Test Font",
        "Update copied, waiting for the device to install it:",
        "  006-V0000-00         23.40  Recommended       12 B  Test Bundle [PRIMARY]",
        "Disconnect the device to install it.",
        "",
        "Install: openexpress updates --install PART_NUMBER [--reinstalls] --apply"]


def test_cli_is_not_up_to_date_while_a_copied_update_waits(offered, no_download, capsys):
    _copied(offered, "006-V0000-00", "006-D0000-00")
    assert main(["--path", str(offered), "updates"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert "Software is up to date." not in out
    assert out == [
        "Test watch, software 23.39",
        "Update copied, waiting for the device to install it:",
        "  006-V0000-00         23.40  Recommended       12 B  Test Bundle [PRIMARY]",
        "Disconnect the device to install it.",
        "Already on the device: 1"]  # the font: no update, and not waiting for anything


def test_cli_install_all_says_what_is_left_to_do(offered, no_download, capsys):
    _copied(offered, "006-V0000-00", "006-D0000-00")
    assert main(["--path", str(offered), "updates", "--install", "all"]) == 0
    assert capsys.readouterr().out.splitlines()[1:] == [
        "Skipped 2 files that are already on the device.",
        "Nothing to install: every offered file is already on the device.",
        "Disconnect the device to install it."]


def test_cli_install_all_without_a_copied_system_update_says_nothing_more(offered, no_download,
                                                                         capsys):
    _copied(offered, "006-D0000-00")  # only the font is there
    assert main(["--path", str(offered), "updates", "--install", "all"]) == 0
    assert "Disconnect the device" not in capsys.readouterr().out


def test_cli_copied_update_in_polish(offered, no_download, capsys):
    from openexpress import i18n

    _copied(offered, "006-V0000-00", "006-D0000-00")
    try:
        assert main(["--path", str(offered), "--lang", "pl", "updates"]) == 0
        out = capsys.readouterr().out.splitlines()
        assert main(["--path", str(offered), "--lang", "pl", "updates", "--install", "all"]) == 0
        out_all = capsys.readouterr().out.splitlines()
    finally:
        i18n.setup("en")
    assert out[1:4] == [
        "Aktualizacja skopiowana, czeka na instalację przez urządzenie:",
        "  006-V0000-00         23.40  Zalecana          12 B  Test Bundle [GŁÓWNE]",
        "Odłącz urządzenie, aby ją zainstalować."]
    assert out_all[-1] == "Odłącz urządzenie, aby ją zainstalować."


# --- the window: an update that is copied already is ready to install -----------------------------


def _staged_items(bundle_state="live", font_on_device=True):
    """The bundle copied already (on the device, still offered); the font on the device too, or
    still to be installed."""
    items = updates.parse_updates(lower_keys(beta_answer(bundle_state)))
    font, bundle = items
    bundle.on_device, font.on_device = True, font_on_device
    return items


@pytest.mark.needs_display
def test_status_of_an_update_that_waits_for_the_device():
    _win, status, groups, _buttons = _updates_page(_staged_items())
    assert status == {
        "icon": ("software-update-available-symbolic", 96, False),
        "texts": [("Update ready: disconnect the device to install it", ["title-1"]),
                  (VERSIONS, ["dim-label"]),  # what to do is in the title
                  ("Test Bundle\u00a0· Recommended\u00a0· GUPDATE.GSP", ["caption", "dim-label"])],
        "buttons": [("Check Again", False)]}  # nothing to install: no "Install"
    # Not a component to reinstall: only the font is there.
    (advanced,) = groups
    assert advanced.title == "Advanced"
    assert advanced.rows[0].get_subtitle().startswith("1 component already on the device")


@pytest.mark.needs_display
def test_with_an_older_libadwaita_the_beta_line_is_one_text_that_wraps(in_the_window):
    """Without Adw.WrapBox (libadwaita before 1.7): "Beta" in bold, and a hard space before
    each separator, so that a separator never starts a line; the buttons in a FlowBox."""
    from gi.repository import Gtk


    in_the_window("WRAP_BOX", None)
    win, _status, _groups, _shown = _updates_page(
        updates.parse_updates(lower_keys(beta_answer())))
    (box,) = win.sections["updates"].groups[0].rows
    _image, _title, line, *_rest, row = _children(box)
    assert line.get_text() == f"{VERSIONS}\u00a0· Beta\u00a0· 12\u00a0B"
    assert "<b>Beta</b>" in line.get_label() and line.get_wrap()
    assert isinstance(row, Gtk.FlowBox)
    assert [b.get_label() for b in _buttons(row)] == ["Install", "Install All", "Check Again"]


@pytest.mark.needs_display
def test_status_of_a_beta_that_waits_for_the_device():
    _win, status, _groups, _buttons = _updates_page(_staged_items(BETA), beta=True)
    assert status["texts"][1] == (
        f"{VERSIONS}\u00a0· Beta", [["dim-label"], ["heading", "warning"]])
    assert status["texts"][3][0] == "Garmin offers beta software for this device."


@pytest.mark.needs_display
def test_status_of_an_update_that_waits_on_a_drive_offers_to_eject_it():
    """A device in mass storage mode takes the update once it is ejected: Eject, which does
    that; an MTP device is only to be disconnected, as above."""
    win, status, _groups, buttons = _updates_page(_staged_items(), drive=True)
    assert status["texts"][:2] == [("Update ready: eject the device to install it", ["title-1"]),
                                   (VERSIONS, ["dim-label"])]
    assert status["buttons"] == [("Eject", True), ("Check Again", False)]
    buttons[0].emit("clicked")
    assert win.ejected == [win.session]


@pytest.mark.needs_display
def test_status_of_a_waiting_update_lists_what_is_still_to_install():
    _win, status, groups, _buttons = _updates_page(_staged_items(font_on_device=False))
    assert status["texts"][0][0] == (
        "Update ready: disconnect the device to install it\u00a0· 1 more available")
    (available,) = groups  # the font, to install before the device is disconnected
    assert [r.get_title() for r in available.rows] == ["Test Font"]


def _overview(items=None, drive=False):
    """The "Device Software" row and the software label of the device page; `drive`: the
    device is in mass storage mode."""
    from gi.repository import Adw, Gtk

    from openexpress.gui.app import MainWindow

    win = stand_in(
        session=types.SimpleNamespace(device=types.SimpleNamespace(software_version_str="23.39"),
                                      mass_storage=drive),
        update_row=Adw.ActionRow(), software_label=Gtk.Label(),
        _highlight_update=lambda on: None)  # the chip and the colour: test_update_highlight
    window_methods(win, ["_render_update_state"])
    return win, MainWindow


@pytest.mark.needs_display
def test_device_page_says_the_update_waits_for_the_device():
    import gi

    gi.require_version("Gtk", "4.0")

    items = _staged_items()
    win, MainWindow = _overview()
    MainWindow._set_update_state(win, updates.pending(items), updates.staged(items))
    assert win.update_state == "ready"
    assert win.update_row.get_subtitle() == "Update ready: disconnect the device to install it"
    assert win.software_label.get_label() == "Software 23.39"  # its chip says it
    # Something else to install too: ready wins, and says how many more.
    items = _staged_items(font_on_device=False)
    MainWindow._set_update_state(win, updates.pending(items), updates.staged(items))
    assert (win.update_state, win.update_row.get_subtitle()) == (
        "ready", "Update ready: disconnect the device to install it\u00a0· 1 more available")


@pytest.mark.needs_display
def test_device_page_says_an_update_on_a_drive_waits_to_be_ejected():
    items = _staged_items()
    win, MainWindow = _overview(drive=True)
    MainWindow._set_update_state(win, updates.pending(items), updates.staged(items))
    assert win.update_row.get_subtitle() == "Update ready: eject the device to install it"
    assert win.software_label.get_label() == "Software 23.39"



def _two_waiting():
    """Two components copied by openExpress that wait for the device."""
    items = _items(FONT_ENTRY, MAP_DATA)
    for u in items:
        u.on_device, u.copied_at = True, COPIED_AT
    return items


@pytest.mark.needs_display
@pytest.mark.parametrize(("drive", "words"), [
    (False, "Updates ready: disconnect the device to install them"),
    (True, "Updates ready: eject the device to install them")], ids=["mtp", "drive"])
def test_device_page_says_several_updates_wait_for_the_device(drive, words):
    items = _two_waiting()
    win, MainWindow = _overview(drive=drive)
    MainWindow._set_update_state(win, updates.pending(items), updates.staged(items))
    assert (win.update_state, win.update_row.get_subtitle()) == ("ready", words)


@pytest.mark.parametrize(("waiting", "words"), [
    (1, "Aktualizacja gotowa: odłącz urządzenie, aby ją zainstalować"),
    (2, "Aktualizacje gotowe: odłącz urządzenie, aby je zainstalować"),
    (5, "Aktualizacje gotowe: odłącz urządzenie, aby je zainstalować"),
    (12, "Aktualizacje gotowe: odłącz urządzenie, aby je zainstalować"),
    (22, "Aktualizacje gotowe: odłącz urządzenie, aby je zainstalować")])
def test_updates_ready_in_polish(waiting, words):
    from openexpress import i18n
    from openexpress.gui.words import update_state_text

    i18n.setup("pl")
    try:
        said = update_state_text("ready", waiting=waiting)
        on_a_drive = update_state_text("ready", waiting=waiting, mass_storage=True)
    finally:
        i18n.setup("en")
    assert said == words
    assert on_a_drive == words.replace("odłącz", "wysuń")

def test_quiet_check_of_the_device_page_knows_a_waiting_update(offered):
    from openexpress.gui.app import MainWindow

    _copied(offered, "006-V0000-00")
    s = types.SimpleNamespace(
        client=types.SimpleNamespace(software_updates=lambda raw, include_reinstalls=False: (
            asked.append(include_reinstalls), lower_keys(server_answer()))[1]),
        storage=LocalStorage(offered), device=types.SimpleNamespace(raw=b"", unit_id=None))
    states, asked = [], []
    win = stand_in(
        session=s, _update_check_session=None, update_state=None,
        _set_update_state=lambda new, staged=(): states.append(
            ([u.part_number for u in new], [u.part_number for u in staged])),
        in_background=lambda work, done, failed: done(work()))
    MainWindow._check_updates_quietly(win)
    assert states == [(["006-D0000-00"], ["006-V0000-00"])]
    # Asked as the page Updates asks, reinstalls too: one kept answer for both (answers).
    assert asked == [True]


@pytest.mark.needs_display
def test_after_copying_the_page_says_ready_without_asking_again(monkeypatch):
    from gi.repository import Adw

    from openexpress.gui.app import MainWindow
    from openexpress.updates import InstallReport

    items = updates.parse_updates(lower_keys(server_answer()))
    win, status, _groups, _buttons = _updates_page(items)
    assert status["texts"][0][0] == "2 updates available"

    def nothing(*_a, **_kw):
        raise AssertionError("neither the server nor the device may be asked again")

    copied = []
    monkeypatch.setattr(updates, "install",
                        lambda chosen, storage, **kw: copied.append(chosen) or InstallReport(
                            written=list(chosen)))
    asked = []
    monkeypatch.setattr(Adw.AlertDialog, "present", lambda self, parent: asked.append(self))
    win.session.client = types.SimpleNamespace(http=None, software_updates=nothing)
    win.session.storage = types.SimpleNamespace(read=nothing, size=nothing)
    win.updates_beta, win.shown = False, []
    win.run_task = RunNow()
    win.error = lambda title, text: win.shown.append(title)
    win.progress = win.fraction = None
    win._update_done_text = MainWindow._update_done_text
    for name in ("show_updates", "_show_installed"):
        setattr(win, name, types.MethodType(getattr(MainWindow, name), win))
    MainWindow.confirm_update(win, items[1])
    (dialog,) = asked
    dialog.emit("response", "ok")  # as if "Accept and Install" was pressed
    assert [u.part_number for u in copied[0]] == ["006-V0000-00"]
    assert win.shown == [] and asked[-1].get_heading() == "Done"  # a message, not an error
    assert asked[-1].get_body().endswith("\n\nStill available: 1 update")  # the font
    status, groups, _buttons = _read_page(win)
    assert status["texts"][0][0] == (
        "Update ready: disconnect the device to install it\u00a0· 1 more available")
    assert status["buttons"] == [("Check Again", False)]
    assert [r.get_title() for r in groups[0].rows] == ["Test Font"]  # still to install


@pytest.mark.needs_display
def test_ready_in_polish():
    from openexpress import i18n

    i18n.setup("pl")
    try:
        _w, status, _g, _b = _updates_page(_staged_items(BETA), beta=True)
        win, MainWindow = _overview()
        items = _staged_items()
        MainWindow._set_update_state(win, updates.pending(items), updates.staged(items))
    finally:
        i18n.setup("en")
    assert [t for t, _c in status["texts"]][:2] == [
        "Aktualizacja gotowa: odłącz urządzenie, aby ją zainstalować",
        f"{VERSIONS}\u00a0· Beta"]
    assert status["buttons"] == [("Sprawdź ponownie", False)]
    assert win.update_row.get_subtitle() == (
        "Aktualizacja gotowa: odłącz urządzenie, aby ją zainstalować")
    assert win.software_label.get_label() == "Wersja oprogramowania 23.39"


# --- the progress bar moves while downloading ------------------------------------------------


def _fetch_in_steps(cached=()):
    """A download without the network, in four steps; a file named in `cached` is in the
    cache already and reports nothing. Records the `progress` each call was given. The bytes
    match the MD5 asked for, as a real download's do."""
    given = []
    known = {hashlib.md5(data).hexdigest(): data for data in (FONT, BUNDLE)}

    def fetch(urls, dest, size, md5, session=None, progress=None):
        given.append(progress)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(known.get((md5 or "").lower(), b"x" * size))
        if progress and dest.name not in cached:
            for part in (1, 2, 3, 4):
                progress(size * part // 4, size)
        return dest

    return fetch, given


def _map(*sizes):
    from openexpress import maps

    # A made-up MD5 for each file: a map with a file without one is never installed.
    files = [maps.MapFile(device_path=f"Garmin/Map{n}.img",
                          url=f"https://example.org/map{n}.img", md5=f"{n + 1:032x}", size=size)
             for n, size in enumerate(sizes)]
    return maps.UniversalMap(name="Test Map", map_type="TopoActive", state=0,
                             is_reinstall=False, can_uninstall=True, version="2026.11",
                             region="Test", identifier={}, files=files, files_to_remove=[],
                             hosts=[])


def test_a_map_download_moves_one_bar_over_all_its_files(device_root, monkeypatch):
    from openexpress import maps

    m = _map(400, 800)
    fetch, _given = _fetch_in_steps(cached={maps.cache_file(m.files[0]).name})
    monkeypatch.setattr(download, "fetch", fetch)
    bar = []
    maps.install(m, [m], LocalStorage(device_root), lambda ident: {},
                 progress=lambda step: bar.append((step.done, step.total))
                 if getattr(step, "head", "").startswith("Downloading") else None)
    # The first file came from the cache: counted at once; the second in four steps.
    assert bar == [(400, 1200), (600, 1200), (800, 1200), (1000, 1200), (1200, 1200),
                   (1200, 1200)]


def test_the_download_of_each_update_moves_forward_to_its_size(device_root, tmp_path,
                                                                monkeypatch):
    fetch, _given = _fetch_in_steps()
    monkeypatch.setattr(download, "fetch", fetch)
    monkeypatch.setattr(updates, "cache_path", lambda u: tmp_path / "cache" / u.filename)
    items = updates.parse_updates(lower_keys(server_answer()))
    bars: dict[str, list] = {}
    updates.install(items, LocalStorage(device_root), progress=lambda step: bars.setdefault(
        step.head, []).append((step.done, step.total))
        if getattr(step, "head", "").startswith("Downloading") else None)
    assert sorted(bar[-1] for bar in bars.values()) == sorted(
        [(len(FONT), len(FONT)), (len(BUNDLE), len(BUNDLE))])
    for bar in bars.values():
        assert [done for done, _size in bar] == sorted(done for done, _size in bar)  # never back


def test_without_a_bar_downloads_report_nothing(device_root, tmp_path, monkeypatch):
    from openexpress import maps

    fetch, given = _fetch_in_steps()
    monkeypatch.setattr(download, "fetch", fetch)
    monkeypatch.setattr(updates, "cache_path", lambda u: tmp_path / "cache" / u.filename)
    m = _map(10)
    maps.install(m, [m], LocalStorage(device_root), lambda ident: {})
    updates.install(updates.parse_updates(lower_keys(server_answer())), LocalStorage(device_root))
    assert given == [None, None, None]  # no progress function for any download


@pytest.mark.needs_display
def test_the_window_gives_the_download_its_bar(device_root, monkeypatch):
    from gi.repository import Adw

    from openexpress import maps
    from openexpress.gui.app import MainWindow

    asked, tasks, calls = [], [], {}
    monkeypatch.setattr(Adw.AlertDialog, "present", lambda self, parent: asked.append(self))

    def install(*args, **kwargs):
        calls.setdefault("kw", []).append(kwargs)

    monkeypatch.setattr(updates, "install", install)
    monkeypatch.setattr(maps, "install", install)
    m = _map(10)
    win = stand_in(
        session=types.SimpleNamespace(
            device=types.SimpleNamespace(software_version_str="23.39", raw=b"", unit_id=None),
            storage=LocalStorage(device_root),
            client=types.SimpleNamespace(http=None, consumer=types.SimpleNamespace(url_salt="s"))),
        updates_cache=updates.parse_updates(lower_keys(server_answer())), maps_cache=[m],
        progress=lambda text: None, fraction=lambda done, total: None)

    def run_task(title, fn, done=None, failed=None, button=None):
        tasks.append(fn)
        if title == "Checking the Space…":  # the question of the map comes after it
            done(fn())

    win.run_task = run_task
    window_methods(win, ["_ask_install_map"])
    MainWindow.confirm_update(win, win.updates_cache[0])
    MainWindow.confirm_map(win, m)
    for dialog in asked:
        dialog.emit("response", "ok")
    for fn in tasks:
        fn()
    assert len(calls["kw"]) == 2
    # Each tells its steps, the bar with them ("Downloading … · 1.2 MB of 3.4 MB").
    for kw in calls["kw"]:
        assert kw["progress"] is win.progress


# --- a failed copy keeps the old file -----------------------------------------------------------


def disk_full_halfway(monkeypatch, name=""):
    """Writing a temporary copy (".<name>.openexpress-part", of a file whose name holds
    `name`) fails halfway: the disk is full. Other files are written as usual."""
    import errno
    import os

    from openexpress.device.storage import PART_SUFFIX

    real_open, real_write, parts = os.open, os.write, set()

    def open_(path, *args, **kwargs):
        fd = real_open(path, *args, **kwargs)
        if str(path).endswith(PART_SUFFIX) and name in Path(path).name:
            parts.add(fd)
        return fd

    def write(fd, data):
        if fd in parts:
            real_write(fd, bytes(data)[:max(1, len(data) // 2)])
            raise OSError(errno.ENOSPC, "No space left on device")
        return real_write(fd, data)

    monkeypatch.setattr(os, "open", open_)
    monkeypatch.setattr(os, "write", write)


def the_font(tmp_path, monkeypatch):
    (font,) = [u for u in updates.parse_updates(lower_keys(server_answer())) if not u.is_system]

    def fetch(urls, dest, size, md5, session=None, progress=None):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(FONT)
        return dest

    monkeypatch.setattr(download, "fetch", fetch)
    monkeypatch.setattr(updates, "cache_path", lambda u: tmp_path / "cache" / u.filename)
    return font


def test_a_failed_copy_keeps_the_old_file_on_a_drive(device_root, tmp_path, monkeypatch):
    """A drive (USB mass storage, --path) writes a new file under a temporary name and
    renames it when it is whole. A copy that fails halfway (disk full) leaves the old,
    working file as it was and no temporary file: the old one is never deleted."""
    from openexpress.device.storage import PART_SUFFIX, DeviceError

    font = the_font(tmp_path, monkeypatch)
    old = device_root / "GARMIN" / "Font" / "006-D0000-00.fnt"
    old.parent.mkdir(parents=True)
    old.write_bytes(b"OLD")
    disk_full_halfway(monkeypatch)
    with pytest.raises(DeviceError):
        updates.install([font], LocalStorage(device_root))
    assert old.read_bytes() == b"OLD"
    assert not [p.name for p in old.parent.iterdir() if p.name.endswith(PART_SUFFIX)]


def test_a_drive_needs_room_for_the_new_file_beside_the_old(device_root, tmp_path, monkeypatch):
    """On a drive the old file stays until the new one is whole: the free space must hold
    all of the new file (and the margin), whatever the old one takes."""
    font = the_font(tmp_path, monkeypatch)
    old = device_root / "GARMIN" / "Font" / "006-D0000-00.fnt"
    old.parent.mkdir(parents=True)
    old.write_bytes(b"OLD")
    storage = LocalStorage(device_root)
    monkeypatch.setattr(storage, "free_space", lambda: font.size + updates.SPACE_MARGIN - 1)
    assert updates.check_space(storage, [font])[0] == font.size
    with pytest.raises(updates.UpdateError, match="Not enough space"):
        updates.install([font], storage)
    monkeypatch.setattr(storage, "free_space", lambda: font.size + updates.SPACE_MARGIN)
    updates.install([font], storage)
    assert old.read_bytes() == FONT


def _small_drive(device_root, tmp_path, monkeypatch, size):
    """A drive of 1.3 MB with 929.8 kB free (as the Varia RTL515), and an update of `size`
    bytes offered for it."""
    data = bytes(range(256)) * (size // 256) + bytes(size % 256)
    (update,) = _items(_entry("006-D2222-00", "Test Part", "Firmware", "GARMIN\\REMOTESW",
                              "D2222.GCD", data))

    def fetch(urls, dest, size, md5, session=None, progress=None):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        return dest

    monkeypatch.setattr(download, "fetch", fetch)
    monkeypatch.setattr(updates, "cache_path", lambda u: tmp_path / "cache" / u.filename)
    storage = LocalStorage(device_root)
    monkeypatch.setattr(storage, "capacity", lambda: 1_300_000)
    monkeypatch.setattr(storage, "free_space", lambda: 929_800)
    return storage, update, data


def test_the_room_kept_free_is_a_part_of_a_small_drive(device_root, tmp_path, monkeypatch):
    """2 MiB kept free is more than the whole drive of a Varia (1.3 MB): 5% of the drive
    is kept free instead. 204 kB fit into the 929.8 kB free."""
    storage, update, data = _small_drive(device_root, tmp_path, monkeypatch, 204_100)
    updates.install([update], storage)
    assert (device_root / "GARMIN" / "REMOTESW" / "D2222.GCD").read_bytes() == data
    assert updates.space_margin(storage) == 65_000


def test_an_update_too_big_for_a_small_drive_says_what_is_kept_free(device_root, tmp_path,
                                                                     monkeypatch):
    storage, update, _data = _small_drive(device_root, tmp_path, monkeypatch, 1_200_000)
    with pytest.raises(updates.UpdateError) as error:
        updates.install([update], storage)
    assert str(error.value) == ("Not enough space on the device: 1.2\u00a0MB needed and 65.0\u00a0kB "
                                "kept free, but only 929.8\u00a0kB free")
    assert not (device_root / "GARMIN" / "REMOTESW").exists()


def test_the_room_kept_free_on_a_big_or_unknown_storage_is_2_mib(device_root, monkeypatch):
    storage = LocalStorage(device_root)
    monkeypatch.setattr(storage, "capacity", lambda: 32_000_000_000)  # a watch
    assert updates.space_margin(storage) == 2 * 1024 * 1024 == updates.SPACE_MARGIN
    monkeypatch.setattr(storage, "capacity", lambda: None)  # not known
    assert updates.space_margin(storage) == updates.SPACE_MARGIN


def test_over_mtp_the_old_file_still_counts_as_freed(device_root, tmp_path, monkeypatch):
    """Guards the MTP way (gvfs deletes the old file before it sends the new one): the old
    file's size still counts as freed there."""
    font = the_font(tmp_path, monkeypatch)
    old = device_root / "GARMIN" / "Font" / "006-D0000-00.fnt"
    old.parent.mkdir(parents=True)
    old.write_bytes(b"OLD")

    class OverMtp(LocalStorage):
        replaces_atomically = False

    assert updates.check_space(OverMtp(device_root), [font])[0] == font.size - 3


# --- the file of an update gets exactly the name the server gives, as in Garmin Express -------


class _OverMtp(LocalStorage):
    """The MTP way: gvfs deletes the old file before it sends the new one."""

    replaces_atomically = False


def _offered_from_nowhere(tmp_path, monkeypatch):
    """The bundle (a system update, Garmin/GUPDATE.GSP) and the font of server_answer(), each
    downloaded with its own content."""
    bundle, font = sorted(updates.parse_updates(lower_keys(server_answer())),
                          key=lambda u: not u.is_system)
    files = {bundle.filename: BUNDLE, font.filename: FONT}

    def fetch(urls, dest, size, md5, session=None, progress=None):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(files[dest.name])
        return dest

    monkeypatch.setattr(download, "fetch", fetch)
    monkeypatch.setattr(updates, "cache_path", lambda u: tmp_path / "cache" / u.filename)
    return bundle, font


def _logged(caplog, prefix):
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith(prefix)]


def _gupdate_files(folder):
    return sorted(p.name for p in folder.iterdir() if p.name.upper().startswith("GUPDATE"))


@pytest.mark.parametrize("storage_class", [LocalStorage, _OverMtp])
def test_a_file_in_another_letter_case_gives_way_to_the_name_from_the_server(
        device_root, tmp_path, monkeypatch, caplog, storage_class):
    """An Edge 1050 with GUPDATE.gsp of 2_442_874 B on it, the server giving GUPDATE.GSP:
    written over it, the update keeps the old name and is not installed. As Garmin Express:
    the old file is deleted first, the new one has exactly the server's name, and the log
    names the file on the device too."""
    bundle, _font = _offered_from_nowhere(tmp_path, monkeypatch)
    garmin = device_root / "GARMIN"
    (garmin / "GUPDATE.gsp").write_bytes(bytes(2_442_874))
    storage = storage_class(device_root)
    with caplog.at_level(logging.INFO, logger="openexpress.updates"):
        updates.mark_present(storage, [bundle])
        updates.install([bundle], storage)
    assert _gupdate_files(garmin) == ["GUPDATE.GSP"]
    assert (garmin / "GUPDATE.GSP").read_bytes() == BUNDLE
    assert (garmin / "GUPDATE.GSP").stat().st_size == bundle.size
    assert _logged(caplog, "updates: ") == [
        ("updates: 1 offered (006-V0000-00 23.40 at Garmin/GUPDATE.GSP (on the device: "
         "GARMIN/GUPDATE.gsp): size differs (2_442_874 B on the device, 12 B offered)), 0 of "
         "them already on the device")]
    assert _logged(caplog, "software update: ") == [
        ("software update: 006-V0000-00 23.40: deleted GARMIN/GUPDATE.gsp (2_442_874 B), its "
         "file in another letter case"),
        "software update: 006-V0000-00 23.40 (12 B) copied to GARMIN/GUPDATE.GSP"]


@pytest.mark.parametrize("storage_class", [LocalStorage, _OverMtp])
def test_a_system_update_deletes_a_leftover_gupdate_tmp_first(device_root, tmp_path,
                                                              monkeypatch, caplog, storage_class):
    """As Garmin Express for devices that take updates in parts: GUPDATE.TMP (any letter case)
    next to the file of a system update is deleted before it is written. Another update leaves
    such a file where it is."""
    bundle, font = _offered_from_nowhere(tmp_path, monkeypatch)
    garmin = device_root / "GARMIN"
    (garmin / "gupdate.tmp").write_bytes(bytes(1234))
    (garmin / "Font").mkdir(exist_ok=True)
    (garmin / "Font" / "GUPDATE.TMP").write_bytes(b"not next to a system update")
    storage = storage_class(device_root)
    with caplog.at_level(logging.INFO, logger="openexpress.updates"):
        updates.install([font, bundle], storage)
    assert _gupdate_files(garmin) == ["GUPDATE.GSP"]
    assert (garmin / "GUPDATE.GSP").read_bytes() == BUNDLE
    assert (garmin / "Font" / "GUPDATE.TMP").read_bytes() == b"not next to a system update"
    assert ("software update: 006-V0000-00 23.40: deleted GARMIN/gupdate.tmp (1_234 B), a "
            "leftover of an earlier update") in _logged(caplog, "software update: ")


@pytest.mark.parametrize("storage_class", [LocalStorage, _OverMtp])
def test_without_a_file_at_its_place_an_update_gets_the_name_from_the_server(
        device_root, tmp_path, monkeypatch, caplog, storage_class):
    """Nothing to delete: the copy only. A folder in another letter case (GARMIN for the
    server's Garmin) is the device's own and is written into as it is; the log does not name
    the file twice for it."""
    bundle, _font = _offered_from_nowhere(tmp_path, monkeypatch)
    storage = storage_class(device_root)
    with caplog.at_level(logging.INFO, logger="openexpress.updates"):
        updates.install([bundle], storage)
        updates.mark_present(storage, [bundle])
    assert _gupdate_files(device_root / "GARMIN") == ["GUPDATE.GSP"]
    assert _logged(caplog, "software update: ") == [
        "software update: 006-V0000-00 23.40 (12 B) copied to GARMIN/GUPDATE.GSP"]
    assert _logged(caplog, "updates: ") == [
        ("updates: 1 offered (006-V0000-00 23.40 at Garmin/GUPDATE.GSP: matches), 1 of them "
         "already on the device")]


# --- names from the server never lead out of the cache -----------------------------------------


@pytest.mark.parametrize("field,value", [("part_number", "../x"), ("version", "1/2"),
                                         ("device_path", "Garmin/.."), ("device_path", "Garmin/"),
                                         ("part_number", "")])
def test_a_name_from_the_server_that_is_no_path_is_refused(device_root, tmp_path, monkeypatch,
                                                           field, value):
    """The cache of an update is cache/software/<part number>/<version>/<file name>, all as
    the server gives them (the file name is the last part of its path on the device): a part
    that is no plain name refuses the update before anything is downloaded, and nothing is
    written outside the cache."""
    import dataclasses

    from openexpress import paths

    (font,) = [u for u in updates.parse_updates(lower_keys(server_answer())) if not u.is_system]
    crafted = dataclasses.replace(font, **{field: value})
    fetched = []
    monkeypatch.setattr(download, "fetch", lambda *a, **k: fetched.append(a))
    before = {p for p in tmp_path.rglob("*") if p.is_file()}
    with pytest.raises(updates.UpdateError, match="cannot be used as a path"):
        updates.cache_path(crafted)
    with pytest.raises(updates.UpdateError, match="cannot be used as a path"):
        updates.install([crafted], LocalStorage(device_root))
    assert fetched == []
    new = {p for p in tmp_path.rglob("*") if p.is_file()} - before
    cache = paths.cache_dir().resolve()
    assert all(p.resolve().is_relative_to(cache) for p in new)  # at most its lock
    assert not any(p.resolve().is_relative_to(cache / "software") for p in new)
    assert updates.cache_path(font).resolve().is_relative_to(
        (paths.cache_dir() / "software").resolve())


# --- Install all -------------------------------------------------------------------------------

MAP_DATA = _entry("006-D0002-00", "Test Map Data", "Etyp", "Garmin\\SID", "DA00200.sid", b"sid")
# A second update for the file of the font: copied only once the device has installed the font.
FONT_PATCH = _entry("006-D0003-00", "Test Font Patch", "Firmware", "Garmin\\Font",
                    "006-D0000-00.fnt", FONT)


@pytest.mark.needs_display
@pytest.mark.parametrize(("drive", "entries", "offered"), [
    (True, (FONT_ENTRY, MAP_DATA), True),
    (True, (FONT_ENTRY,), False),           # one update: its own Install
    (False, (FONT_ENTRY, MAP_DATA), True),
    (False, (FONT_ENTRY,), False),
], ids=["drive", "drive-one-update", "mtp", "mtp-one-update"])
def test_install_all_is_offered_with_more_than_one_update(drive, entries, offered):
    _win, status, _groups, _buttons = _updates_page(_items(*entries), drive=drive)
    assert (("Install All", True) in status["buttons"]) is offered


@pytest.mark.needs_display
def test_install_all_over_mtp_copies_every_update_in_one_go(monkeypatch, caplog):
    """Over MTP as on a drive: one question with the list and the size, one copy of them all
    (of two for one file the first; the other waits for the device), every one chosen in the
    line of the log, then Done with the steps and what is still available; nothing ejected."""
    from gi.repository import Adw

    from openexpress.gui.app import MainWindow

    items = _items(BUNDLE_ENTRY, FONT_ENTRY, MAP_DATA, FONT_PATCH)
    win, status, _groups, buttons = _updates_page(items)
    assert status["buttons"] == [("Install", True), ("Install All", False),
                                 ("Check Again", False)]
    copies = []

    def copy(chosen, storage, report, **kw):
        copies.append([u.part_number for u in chosen])
        report.written += chosen
        return report

    monkeypatch.setattr(updates, "_install", copy)
    asked = []
    monkeypatch.setattr(Adw.AlertDialog, "present", lambda self, parent: asked.append(self))
    win.session.client = types.SimpleNamespace(http=None)
    win.session.storage = None
    win.updates_beta = False
    win.run_task = RunNow()
    win.error = lambda title, text: None
    win.progress = win.fraction = None
    for name in ("show_updates", "_show_installed"):
        setattr(win, name, types.MethodType(getattr(MainWindow, name), win))
    (install_all,) = [b for b in buttons if b.get_label() == "Install All"]
    install_all.emit("clicked")
    (question,) = asked
    assert question.get_heading() == "Install Updates?"
    body = question.get_body().splitlines()
    assert body == [f"Test Bundle {VERSIONS}", "Test Font 23.40", "Test Map Data 23.40",
                    "Test Font Patch: Can be installed once the device has installed Test Font",
                    "", "Size: 19\u00a0B"]
    with caplog.at_level(logging.INFO, logger="openexpress.updates"):
        question.emit("response", "ok")
    assert copies == [["006-V0000-00", "006-D0000-00", "006-D0002-00"]]  # one copy
    said = [r.getMessage() for r in caplog.records if r.getMessage().startswith("software update")]
    line = ("software update: 3 of 4 offered chosen (006-V0000-00 23.40, 006-D0000-00 23.40, "
            "006-D0002-00 23.40); left out: 006-D0003-00 23.40 (put off: the same file as "
            "006-D0000-00)")
    assert said == [line]
    assert win.ejected == []
    done = asked[-1]
    assert done.get_heading() == "Done"
    assert done.get_body().startswith("Files copied to the device.\nTo finish the installation:")
    assert done.get_body().endswith("\n\nStill available: 1 update")  # the font's patch



@pytest.mark.needs_display
@pytest.mark.parametrize("drive", [False, True], ids=["mtp", "drive"])
@pytest.mark.parametrize("how", ["one", "all"])
def test_after_copying_components_the_page_says_ready_not_up_to_date(monkeypatch, in_the_window,
                                                                     drive, how):
    """Copied components wait for the device as a copied system update does: the page, the
    row "Device Software" and the tile say the update is ready (disconnect, or eject a
    drive), not that the software is up to date; Install or Install All, over MTP or on a
    drive."""
    from gi.repository import Adw

    from openexpress.gui.app import MainWindow
    from openexpress.updates import InstallReport

    items = _items(FONT_ENTRY, MAP_DATA)
    win, _status, _groups, buttons = _updates_page(items, drive=drive)
    states = []
    win._set_update_state = lambda new, staged=(): states.append(
        ([u.part_number for u in new], [u.part_number for u in staged]))
    monkeypatch.setattr(updates, "install", lambda chosen, storage, **kw: InstallReport(
        written=list(chosen)))
    asked = []
    monkeypatch.setattr(Adw.AlertDialog, "present", lambda self, parent: asked.append(self))
    in_the_window("is_bike_accessory", lambda client, device, ask: False)
    win.session.client = types.SimpleNamespace(http=None)
    win.session.storage = None
    win.updates_beta = False
    win.run_task = RunNow()
    win.error = lambda title, text: None
    win.progress = win.fraction = None
    win.eject_device = lambda *a, **kw: win.ejected.append(a)
    for name in ("show_updates", "_show_installed", "confirm_update"):
        setattr(win, name, types.MethodType(getattr(MainWindow, name), win))
    if how == "all":
        (install_all,) = [b for b in buttons if b.get_label() == "Install All"]
        install_all.emit("clicked")
    else:
        win.confirm_update(items[0])
    asked[0].emit("response", "ok")
    status, _groups, _buttons = _read_page(win)
    word = "eject" if drive else "disconnect"
    if how == "all":
        assert states[-1] == ([], ["006-D0000-00", "006-D0002-00"])
        assert [text for text, _styles in status["texts"]] == [
            f"Updates ready: {word} the device to install them", "Software 23.39",
            "Test Font\u00a0· Test Map Data"]
    else:
        assert states[-1] == (["006-D0002-00"], ["006-D0000-00"])
        assert [text for text, _styles in status["texts"]] == [
            f"Update ready: {word} the device to install it\u00a0· 1 more available", "23.40",
            "Test Font\u00a0· Recommended\u00a0· 006-D0000-00.fnt"]

@pytest.mark.needs_display
def test_install_all_copies_every_update_then_ejects_the_drive_once(monkeypatch, in_the_window):
    """A drive ejected after each update needs a new connection for the next: Install all
    asks once, copies all of them in one go and ejects the drive once."""
    from gi.repository import Adw

    from openexpress.gui.app import MainWindow
    from openexpress.updates import InstallReport

    items = _items(BUNDLE_ENTRY, FONT_ENTRY, MAP_DATA)
    win, status, _groups, buttons = _updates_page(items, drive=True)
    assert status["buttons"] == [("Install", True), ("Install All", False),
                                 ("Check Again", False)]
    given = []
    monkeypatch.setattr(updates, "install", lambda chosen, storage, **kw: given.append(
        list(chosen)) or InstallReport(written=list(chosen)))
    asked = []
    monkeypatch.setattr(Adw.AlertDialog, "present", lambda self, parent: asked.append(self))
    in_the_window("is_bike_accessory", lambda client, device, ask: False)
    win.session.client = types.SimpleNamespace(http=None)
    win.session.storage = None
    win.updates_beta = False
    win.run_task = RunNow()
    win.error = lambda title, text: None
    win.progress = win.fraction = None
    win.eject_device = lambda *a, **kw: win.ejected.append(a)
    for name in ("show_updates", "_show_installed"):
        setattr(win, name, types.MethodType(getattr(MainWindow, name), win))
    (install_all,) = [b for b in buttons if b.get_label() == "Install All"]
    install_all.emit("clicked")
    (question,) = asked
    assert question.get_heading() == "Install Updates?"
    assert question.get_body().splitlines()[:4] == [
        f"Test Bundle {VERSIONS}", "Test Font 23.40", "Test Map Data 23.40", ""]
    question.emit("response", "ok")
    assert [[u.part_number for u in chosen] for chosen in given] == [
        ["006-V0000-00", "006-D0000-00", "006-D0002-00"]]
    assert len(win.ejected) == 1
    assert all(u.on_device for u in items)
