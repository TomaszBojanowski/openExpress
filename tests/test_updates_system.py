"""System updates: recognised by their file as well as by the flags of the server.

The answer below has the shape of a real one (an Edge 1050 offered a beta bundle, 14.10.2026):
the bundle with the device software comes as GUPDATE.GSP without "isprimaryfirmware" and
without "isrestartrequired", and its data type, "Firmware", is also that of plain files such
as fonts. All values are made up."""

import hashlib
import types

import pytest

from openexpress import download, updates
from openexpress.cli import main
from openexpress.connect import GarminClient, lower_keys
from openexpress.device.storage import LocalStorage

BUNDLE = b"bundle bytes"
FONT = b"font"


def _entry(part, name, datatype, path, filename, data, order=0, **flags):
    """One offered item, with the keys as the server sends them (camelCase)."""
    return {
        "partNumber": part, "productName": name, "dataType": datatype,
        "displayVersion": "23.40", "severity": "Recommended",
        "isPrimaryFirmware": flags.get("primary", False),
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


@pytest.mark.parametrize(("path", "filename", "flags", "system"), [
    ("Garmin", "GUPDATE.GCD", {}, True),
    ("GARMIN", "gupdate.gsp", {}, True),        # any letter case
    ("Garmin\\Other", "GUPDATE.GSP", {}, True),  # the name counts, not the folder
    ("Garmin", "GUPDATE.GSP.bak", {}, False),
    ("Garmin", "XGUPDATE.GCD", {}, False),
    ("Garmin", "GUPDATE.RGN", {}, False),
    ("Garmin", "settings.fit", {"primary": True}, True),  # the flags of the server still count
    ("Garmin", "settings.fit", {"restart": True}, True),
    ("Garmin", "settings.fit", {}, False),
])
def test_which_files_are_system_updates(path, filename, flags, system):
    answer = lower_keys({"updates": [_entry("006-X0000-00", "X", "CONFIG", path, filename, b"x",
                                            **flags)]})
    (u,) = updates.parse_updates(answer)
    assert u.is_system is system


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
                        lambda path, local, progress=None: copied.append(path) or write(
                            path, local, progress))
    report = updates.install(items, storage)
    assert copied == ["Garmin/Font/006-D0000-00.fnt", "Garmin/GUPDATE.GSP"]
    assert report.restart_required
    assert (device_root / "GARMIN/GUPDATE.GSP").read_bytes() == BUNDLE


def test_plain_files_alone_ask_for_no_restart(device_root, tmp_path, monkeypatch):
    (font,) = [u for u in updates.parse_updates(lower_keys(server_answer())) if not u.is_system]

    def fetch(urls, dest, size, md5, session=None, progress=None):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(FONT)
        return dest

    monkeypatch.setattr(download, "fetch", fetch)
    monkeypatch.setattr(updates, "cache_path", lambda u: tmp_path / "cache" / u.filename)
    assert not updates.install([font], LocalStorage(device_root)).restart_required


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


# --- the window asks before it installs ---------------------------------------------------------

WARNING_EN = ("Warning: this is a device system update. Charge the device and do not disconnect "
              "it while copying.")
WARNING_PL = ("Ostrzeżenie: to aktualizacja systemu urządzenia. Naładuj urządzenie i nie odłączaj "
              "go podczas kopiowania.")


def _question(monkeypatch, part):
    """The text of the window's question before `part` is installed; nothing is shown."""
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gtk

    from openexpress.gui.app import MainWindow

    if not Gtk.init_check():
        pytest.skip("no display")
    items = updates.parse_updates(lower_keys(server_answer()))
    asked = []
    monkeypatch.setattr(Adw.AlertDialog, "present", lambda self, parent: asked.append(self))
    win = types.SimpleNamespace(session=types.SimpleNamespace(), updates_cache=items)
    MainWindow.confirm_update(win, next(u for u in items if u.part_number == part))
    (dialog,) = asked
    return dialog.get_body()


def test_the_window_warns_before_a_system_update(monkeypatch):
    body = _question(monkeypatch, "006-V0000-00")
    assert body.endswith("\n\n" + WARNING_EN)  # the same words as on the command line


def test_the_window_does_not_warn_before_a_plain_file(monkeypatch):
    body = _question(monkeypatch, "006-D0000-00")
    assert "Warning" not in body and "disconnect" not in body


def test_the_warning_in_polish(monkeypatch):
    from openexpress import i18n

    i18n.setup("pl")
    try:
        body = _question(monkeypatch, "006-V0000-00")
    finally:
        i18n.setup("en")
    assert body.endswith("\n\n" + WARNING_PL)


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


def _children(widget):
    child = widget.get_first_child()
    while child is not None:
        yield child
        child = child.get_next_sibling()


def _updates_page(items):
    """The Updates page of a window that is never shown; returns the window, the status at the
    top (icon, its styles, the texts with their styles, the buttons) and the other groups."""
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Gtk

    from openexpress.gui.app import MainWindow

    if not Gtk.init_check():
        pytest.skip("no display")
    win = types.SimpleNamespace(
        session=types.SimpleNamespace(device=types.SimpleNamespace(
            description="Test watch", software_version_str="23.39")),
        sections={"updates": _Section()}, busy=False, _action_buttons=[], asked=[], loads=[],
        _set_update_state=lambda new: None)
    win.confirm_update = win.asked.append
    win.load_section = lambda *a, **kw: win.loads.append((a, kw))
    for name in ("_updates_status", "_update_row", "_button"):
        setattr(win, name, types.MethodType(getattr(MainWindow, name), win))
    MainWindow.show_updates(win, items)
    top, *groups = win.sections["updates"].groups
    (box,) = top.rows
    image, *labels, row = _children(box)
    status = {
        "icon": (image.get_icon_name(), image.get_pixel_size(), image.has_css_class("success")),
        "texts": [(w.get_label(), sorted(w.get_css_classes())) for w in labels],
        "buttons": [(b.get_label(), b.has_css_class("suggested-action")) for b in _children(row)],
    }
    return win, status, groups, list(_children(row))


def _items(*answer_entries):
    return updates.parse_updates(lower_keys({"updates": list(answer_entries)}))


BUNDLE_ENTRY = _entry("006-V0000-00", "Test Bundle", "Firmware", "Garmin", "GUPDATE.GSP", BUNDLE)
FONT_ENTRY = _entry("006-D0000-00", "Test Font", "Firmware", "Garmin\\Font", "006-D0000-00.fnt",
                    FONT)


def test_status_when_the_software_is_up_to_date():
    reinstall = _entry("006-D0001-00", "Old Font", "Firmware", "Garmin\\Font", "old.fnt", FONT)
    reinstall["installation"]["isReinstall"] = True
    win, status, groups, (check,) = _updates_page(_items(reinstall))
    assert status == {
        "icon": ("object-select-symbolic", 96, True),
        "texts": [("Software is up to date", ["title-1"]),  # a title: no full stop
                  ("Test watch · version 23.39", ["dim-label"])],
        "buttons": [("Check Again", False)]}
    assert [g.title for g in groups] == ["Advanced"]  # no list of updates; reinstalls as before
    check.emit("clicked")
    assert win.loads == [(("updates",), {"force": True})]


def test_status_with_a_system_update():
    win, status, groups, (install, check) = _updates_page(_items(FONT_ENTRY, BUNDLE_ENTRY))
    assert status == {
        "icon": ("software-update-available-symbolic", 96, False),
        "texts": [("Software update available", ["title-1"]),
                  ("23.39 → 23.40 · 12 B", ["dim-label"]),
                  ("Test Bundle · Recommended · GUPDATE.GSP", ["caption", "dim-label"])],
        "buttons": [("Install", True), ("Check Again", False)]}
    # Below: only the other updates.
    (available,) = groups
    assert available.title == "Available updates"
    assert [(r.get_title(), r.get_subtitle()) for r in available.rows] == [
        ("Test Font", "23.40 · 4 B · Recommended · 006-D0000-00.fnt")]
    install.emit("clicked")
    assert [u.part_number for u in win.asked] == ["006-V0000-00"]  # the question with the warning


def test_status_with_plain_updates_only():
    other = _entry("006-D0002-00", "Test Map Data", "Etyp", "Garmin\\SID", "DA00200.sid", b"sid")
    _win, status, groups, _buttons = _updates_page(_items(FONT_ENTRY, other))
    assert status == {
        "icon": ("software-update-available-symbolic", 96, False),
        "texts": [("2 updates available", ["title-1"]),
                  ("Test watch · version 23.39", ["dim-label"])],
        "buttons": [("Check Again", False)]}
    (available,) = groups
    assert [r.get_title() for r in available.rows] == ["Test Font", "Test Map Data"]


def test_a_second_system_update_is_listed_as_system_software():
    second = _entry("006-B0000-00", "Test Software", "Firmware", "Garmin", "GUPDATE.GCD", b"fw")
    _win, status, groups, _buttons = _updates_page(_items(BUNDLE_ENTRY, second))
    assert status["texts"][2][0] == "Test Bundle · Recommended · GUPDATE.GSP"  # the first on top
    (available,) = groups
    assert [(r.get_title(), r.get_subtitle()) for r in available.rows] == [
        ("System software", "Test Software · 23.40 · 2 B · Recommended · GUPDATE.GCD")]


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
                                                 "Test watch · wersja 23.39"]
    assert current["buttons"] == [("Sprawdź ponownie", False)]
    assert [t for t, _c in system["texts"]] == [
        "Dostępna aktualizacja oprogramowania", "23.39 → 23.40 · 12 B",
        "Test Bundle · Zalecana · GUPDATE.GSP"]
    assert system["buttons"] == [("Zainstaluj", True), ("Sprawdź ponownie", False)]
    assert groups[0].title == "Dostępne aktualizacje"
    assert plain["texts"][0][0] == "Dostępna jest 1 aktualizacja"
    assert listed.rows[0].get_title() == "Oprogramowanie systemowe"
