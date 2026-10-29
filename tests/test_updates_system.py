"""System updates: recognised by their file, not only by the server's flags.

The answer below has the shape of a real one (an Edge 1050 offered a beta bundle): the bundle of
the device software is GUPDATE.GSP without "isprimaryfirmware" or "isrestartrequired", and its
data type, "Firmware", is also that of plain files such as fonts. All values are made up."""

import hashlib
import logging
import types
from pathlib import Path

import pytest
from fakes import RunNow

from openexpress import download, updates
from openexpress.cli import main
from openexpress.connect import GarminClient, lower_keys
from openexpress.device.storage import LocalStorage

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
                        lambda path, local, progress=None, **kw: copied.append(path) or write(
                            path, local, progress, **kw))
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
WARNING_PL = ("Ostrzeżenie: to aktualizacja systemu urządzenia. Naładuj urządzenie i\u00a0nie odłączaj "
              "go podczas kopiowania.")


def _dialog(monkeypatch, part, answer=None):
    """The window's question before `part` is installed; nothing is shown."""
    from gi.repository import Adw

    from openexpress.gui.app import MainWindow

    items = updates.parse_updates(lower_keys(answer or server_answer()))
    asked = []
    monkeypatch.setattr(Adw.AlertDialog, "present", lambda self, parent: asked.append(self))
    win = types.SimpleNamespace(
        session=types.SimpleNamespace(device=types.SimpleNamespace(software_version_str="23.39")),
        updates_cache=items)
    MainWindow.confirm_update(win, next(u for u in items if u.part_number == part))
    (dialog,) = asked
    return dialog


def _below(dialog):
    """What the question shows below its text, in order."""
    extra = dialog.get_extra_child()
    return [] if extra is None else list(_children(extra))


def _question(monkeypatch, part, answer=None):
    """The text of the question, and the warning below it (its text and styles) or None."""
    dialog = _dialog(monkeypatch, part, answer)
    warnings = [(w.get_label(), sorted(w.get_css_classes())) for w in _below(dialog)
                if w.has_css_class("warning")]
    return dialog.get_body(), (warnings[0] if warnings else None)


def test_two_versions_stay_on_one_line():
    import re

    from openexpress.gui import app

    assert VERSIONS == "23.39" + app.ARROW + "23.40"  # what the window builds
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


def _updates_page(items, beta=False, drive=False):
    """The Updates page of a window that is never shown; returns the window, the status at the
    top (icon, its styles, the texts with their styles, the buttons) and the other groups.
    `drive`: the device is in mass storage mode."""
    from openexpress.gui.app import MainWindow

    win = types.SimpleNamespace(
        session=types.SimpleNamespace(device=types.SimpleNamespace(
            description="Test watch", software_version_str="23.39"), mass_storage=drive),
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
    win, status, groups, (install, check) = _updates_page(_items(FONT_ENTRY, BUNDLE_ENTRY))
    assert status == {
        "icon": ("software-update-available-symbolic", 96, False),
        "texts": [("2 updates available", ["title-1"]),  # the words of the device's row
                  (f"{VERSIONS}\u00a0· 12\u00a0B", ["dim-label"]),
                  ("Test Bundle\u00a0· Recommended\u00a0· GUPDATE.GSP", ["caption", "dim-label"])],
        "buttons": [("Install", True), ("Check Again", False)]}
    # Below: only the other updates.
    (available,) = groups
    assert available.title == "Available updates"
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
        "buttons": [("Check Again", False)]}
    (available,) = groups
    assert [r.get_title() for r in available.rows] == ["Test Font", "Test Map Data"]


@pytest.mark.needs_display
def test_a_second_system_update_is_listed_as_system_software():
    second = _entry("006-B0000-00", "Test Software", "Firmware", "Garmin", "GUPDATE.GCD", b"fw")
    _win, status, groups, _buttons = _updates_page(_items(BUNDLE_ENTRY, second))
    assert status["texts"][2][0] == "Test Bundle\u00a0· Recommended\u00a0· GUPDATE.GSP"  # the first on top
    (available,) = groups
    assert [(r.get_title(), r.get_subtitle()) for r in available.rows] == [
        ("System software", "Test Software\u00a0· 23.40\u00a0· 2\u00a0B\u00a0· Recommended\u00a0· GUPDATE.GCD")]


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
                                                 "Oprogramowanie 23.39"]
    assert current["buttons"] == [("Sprawdź ponownie", False)]
    assert [t for t, _c in system["texts"]] == [
        "Dostępne są 2 aktualizacje", f"{VERSIONS}\u00a0· 12\u00a0B",
        "Test Bundle\u00a0· Zalecana\u00a0· GUPDATE.GSP"]
    assert system["buttons"] == [("Zainstaluj", True), ("Sprawdź ponownie", False)]
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
    win = types.SimpleNamespace(
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
    from openexpress.gui import app

    win, _status, _groups, _buttons = _updates_page(
        updates.parse_updates(lower_keys(beta_answer())))
    (box,) = win.sections["updates"].groups[0].rows
    line = list(_children(box))[2]
    assert isinstance(line, app.WRAP_BOX or Gtk.FlowBox)
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
    assert (link.get_uri(), link.get_label()) == (url, "License agreement (EULA)")
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
    assert [b.get_label() for b in _buttons(row)] == ["Install", "Check Again"]


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
    """The "Device software" row and the software label of the device page; `drive`: the
    device is in mass storage mode."""
    from gi.repository import Adw, Gtk

    from openexpress.gui.app import MainWindow

    win = types.SimpleNamespace(
        session=types.SimpleNamespace(device=types.SimpleNamespace(software_version_str="23.39"),
                                      mass_storage=drive),
        update_row=Adw.ActionRow(), software_label=Gtk.Label(),
        _highlight_update=lambda on: None)  # the chip and the colour: test_update_highlight
    win._render_update_state = types.MethodType(MainWindow._render_update_state, win)
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


def test_quiet_check_of_the_device_page_knows_a_waiting_update(offered):
    from openexpress.gui.app import MainWindow

    _copied(offered, "006-V0000-00")
    s = types.SimpleNamespace(
        client=types.SimpleNamespace(software_updates=lambda raw: lower_keys(server_answer())),
        storage=LocalStorage(offered), device=types.SimpleNamespace(raw=b""))
    states = []
    win = types.SimpleNamespace(
        session=s, _update_check_session=None, update_state=None,
        _set_update_state=lambda new, staged=(): states.append(
            ([u.part_number for u in new], [u.part_number for u in staged])),
        in_background=lambda work, done, failed: done(work()))
    MainWindow._check_updates_quietly(win)
    assert states == [(["006-D0000-00"], ["006-V0000-00"])]


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
                            installed=["Test Bundle 23.40 -> Garmin/GUPDATE.GSP"]))
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
    dialog.emit("response", "ok")  # as if "Accept and install" was pressed
    assert [u.part_number for u in copied[0]] == ["006-V0000-00"] and win.shown == ["Done"]
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
    assert win.software_label.get_label() == "Oprogramowanie 23.39"


# --- the progress bar moves while downloading ------------------------------------------------


def _fetch_in_steps(cached=()):
    """A download without the network, in four steps; a file named in `cached` is in the
    cache already and reports nothing. Records the `progress` each call was given."""
    given = []

    def fetch(urls, dest, size, md5, session=None, progress=None):
        given.append(progress)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"x" * size)
        if progress and dest.name not in cached:
            for part in (1, 2, 3, 4):
                progress(size * part // 4, size)
        return dest

    return fetch, given


def _map(*sizes):
    from openexpress import maps

    files = [maps.MapFile(device_path=f"Garmin/Map{n}.img",
                          url=f"https://example.org/map{n}.img", md5=None, size=size)
             for n, size in enumerate(sizes)]
    return maps.UniversalMap(name="Test Map", map_type="TopoActive", state=0,
                             is_reinstall=False, can_uninstall=True, version="2026.11",
                             region="Test", identifier={}, files=files, files_to_remove=[],
                             hosts=[])


def test_a_map_download_moves_one_bar_over_all_its_files(device_root, monkeypatch):
    from openexpress import maps

    fetch, _given = _fetch_in_steps(cached={"Map0.img"})
    monkeypatch.setattr(download, "fetch", fetch)
    m = _map(400, 800)
    bar = []
    maps.install(m, [m], LocalStorage(device_root), lambda ident: {},
                 download_progress=lambda done, total: bar.append((done, total)))
    # The first file came from the cache: counted at once; the second in four steps.
    assert bar == [(400, 1200), (600, 1200), (800, 1200), (1000, 1200), (1200, 1200),
                   (1200, 1200)]


def test_an_update_download_moves_one_bar_over_all_its_files(device_root, tmp_path,
                                                             monkeypatch):
    fetch, _given = _fetch_in_steps()
    monkeypatch.setattr(download, "fetch", fetch)
    monkeypatch.setattr(updates, "cache_path", lambda u: tmp_path / "cache" / u.filename)
    items = updates.parse_updates(lower_keys(server_answer()))
    total = len(FONT) + len(BUNDLE)
    bar = []
    updates.install(items, LocalStorage(device_root),
                    download_progress=lambda done, size: bar.append((done, size)))
    assert all(size == total for _done, size in bar)
    assert [done for done, _size in bar] == sorted(done for done, _size in bar)  # never back
    assert bar[-1] == (total, total)


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
    win = types.SimpleNamespace(
        session=types.SimpleNamespace(
            device=types.SimpleNamespace(software_version_str="23.39", raw=b""),
            storage=LocalStorage(device_root),
            client=types.SimpleNamespace(http=None, consumer=types.SimpleNamespace(url_salt="s"))),
        updates_cache=updates.parse_updates(lower_keys(server_answer())), maps_cache=[m],
        progress=lambda text: None, fraction=lambda done, total: None)

    def run_task(title, fn, done=None, failed=None, button=None):
        tasks.append(fn)
        if title == "Checking the space…":  # the question of the map comes after it
            done(fn())

    win.run_task = run_task
    win._ask_install_map = types.MethodType(MainWindow._ask_install_map, win)
    MainWindow.confirm_update(win, win.updates_cache[0])
    MainWindow.confirm_map(win, m)
    for dialog in asked:
        dialog.emit("response", "ok")
    for fn in tasks:
        fn()
    assert len(calls["kw"]) == 2
    assert all(kw["download_progress"] is win.fraction for kw in calls["kw"])


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
