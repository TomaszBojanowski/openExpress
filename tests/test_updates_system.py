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


def _dialog(monkeypatch, part, answer=None):
    """The window's question before `part` is installed; nothing is shown."""
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gtk

    from openexpress.gui.app import MainWindow

    if not Gtk.init_check():
        pytest.skip("no display")
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


def test_the_window_warns_before_a_system_update(monkeypatch):
    body, warning = _question(monkeypatch, "006-V0000-00")
    # The same words as on the command line, below the text: in the warning colour, bold.
    assert warning == (WARNING_EN, ["heading", "warning"])
    assert "Warning" not in body


def test_the_window_does_not_warn_before_a_plain_file(monkeypatch):
    body, warning = _question(monkeypatch, "006-D0000-00")
    assert warning is None
    assert "Warning" not in body and "disconnect" not in body


def test_the_warning_in_polish(monkeypatch):
    from openexpress import i18n

    i18n.setup("pl")
    try:
        body, warning = _question(monkeypatch, "006-V0000-00")
    finally:
        i18n.setup("en")
    assert warning == (WARNING_PL, ["heading", "warning"])
    assert body.startswith("Test Bundle 23.39 → 23.40\nRozmiar: 12 B\n")


def test_the_question_names_both_versions_of_a_system_update(monkeypatch):
    body, _warning = _question(monkeypatch, "006-V0000-00")
    assert body.splitlines()[0] == "Test Bundle 23.39 → 23.40"
    body, _warning = _question(monkeypatch, "006-D0000-00")
    assert body.splitlines()[0] == "Test Font 23.40"  # not the device software: as before


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


def _text(widget):
    """A line of the status and its styles; a line made of parts (a beta version) as one text,
    with the styles of each part."""
    parts = list(_children(widget)) if not hasattr(widget, "get_label") else []
    if not parts:
        return widget.get_label(), sorted(widget.get_css_classes())
    return "".join(p.get_label() for p in parts), [sorted(p.get_css_classes()) for p in parts]


def _updates_page(items, beta=False):
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
    MainWindow.show_updates(win, items, beta)
    top, *groups = win.sections["updates"].groups
    (box,) = top.rows
    image, *labels, row = _children(box)
    status = {
        "icon": (image.get_icon_name(), image.get_pixel_size(), image.has_css_class("success")),
        "texts": [_text(w) for w in labels],
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


def test_status_with_a_beta_system_update():
    items = updates.parse_updates(lower_keys(beta_answer()))
    _win, status, _groups, _buttons = _updates_page(items, beta=True)
    assert status["texts"] == [
        ("Software update available", ["title-1"]),
        # "Beta" in the warning colour, not dimmed like the rest of the line.
        ("23.39 → 23.40 · Beta · 12 B", [["dim-label"], ["heading", "warning"], ["dim-label"]]),
        ("Test Bundle · Recommended · GUPDATE.GSP", ["caption", "dim-label"]),
        ("Garmin offers beta software for this device.", ["caption", "dim-label"])]


def test_status_says_beta_versions_are_offered_when_none_is_listed():
    # The device is in the beta program, but the system update offered is not a beta.
    items = updates.parse_updates(lower_keys(beta_answer("live")))
    _win, status, _groups, _buttons = _updates_page(items, beta=True)
    assert [t for t, _c in status["texts"]][1:] == [
        "23.39 → 23.40 · 12 B", "Test Bundle · Recommended · GUPDATE.GSP",
        "Garmin offers beta software for this device."]
    # Up to date: the line is there as well.
    _win, status, _groups, _buttons = _updates_page([], beta=True)
    assert [t for t, _c in status["texts"]] == [
        "Software is up to date", "Test watch · version 23.39",
        "Garmin offers beta software for this device."]


def test_status_without_beta_has_no_beta_line():
    _win, status, _groups, _buttons = _updates_page([])
    assert not any("beta" in t.casefold() for t, _c in status["texts"])


def test_the_page_learns_from_the_answer_whether_beta_versions_are_offered(device_root):
    from openexpress.gui.app import MainWindow

    shown = []
    win = types.SimpleNamespace(
        session=types.SimpleNamespace(
            storage=LocalStorage(device_root), device=types.SimpleNamespace(raw=b"<Device/>"),
            client=types.SimpleNamespace(
                software_updates=lambda raw, include_reinstalls=False: lower_keys(beta_answer()))),
        show_updates=lambda items, beta=False: shown.append(([u.part_number for u in items], beta)))
    win._load = lambda key, title, work, show: show(work())
    MainWindow.load_updates(win)
    assert shown == [(["006-D0000-00", "006-V0000-00"], True)]


def _beta_question(monkeypatch, bundle_state):
    return _question(monkeypatch, "006-V0000-00", beta_answer(bundle_state))


def test_the_window_says_it_is_a_beta_before_installing(monkeypatch):
    body, warning = _beta_question(monkeypatch, BETA)
    assert body.endswith("\n\nThis is a beta version.")  # the warning follows, below the text
    assert warning == (WARNING_EN, ["heading", "warning"])
    body, _warning = _beta_question(monkeypatch, "live")
    assert "beta" not in body.casefold()


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
        "Dostępna aktualizacja oprogramowania", "23.39 → 23.40 · Beta · 12 B",
        "Test Bundle · Zalecana · GUPDATE.GSP",
        "Garmin oferuje dla tego urządzenia oprogramowanie w wersji beta."]


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


def test_the_beta_mark_is_a_part_of_its_own_only_for_a_beta():
    from gi.repository import Gtk

    for state, beta in ((BETA, True), ("live", False)):
        _win, status, _groups, _buttons = _updates_page(
            updates.parse_updates(lower_keys(beta_answer(state))))
        text, styles = status["texts"][1]
        if beta:
            assert text == "23.39 → 23.40 · Beta · 12 B"
            assert styles[1] == ["heading", "warning"] and "dim-label" not in styles[1]
        else:  # one label, as before
            assert (text, styles) == ("23.39 → 23.40 · 12 B", ["dim-label"])
    # The line of parts is centred like the labels around it.
    win, _status, _groups, _buttons = _updates_page(
        updates.parse_updates(lower_keys(beta_answer())))
    (box,) = win.sections["updates"].groups[0].rows
    line = list(_children(box))[2]
    assert isinstance(line, Gtk.Box) and line.get_halign() == Gtk.Align.CENTER


# --- the license in the question before an update ----------------------------------------------


def _with_license(url, part="006-V0000-00"):
    answer = server_answer()
    for entry in answer["updates"]:
        if entry["partNumber"] == part:
            entry["eulaUrl"] = url
    return answer


def test_the_license_is_a_link_above_the_warning(monkeypatch):
    from gi.repository import Gtk

    url = "https://example.org/license.txt"
    dialog = _dialog(monkeypatch, "006-V0000-00", _with_license(url))
    link, warning = _below(dialog)  # the warning last, right above the buttons
    assert isinstance(link, Gtk.LinkButton)
    assert (link.get_uri(), link.get_label()) == (url, "License agreement (EULA)")
    assert warning.get_label() == WARNING_EN
    assert "License" not in dialog.get_body() and url not in dialog.get_body()


def test_the_license_link_takes_any_letter_case_of_https(monkeypatch):
    from gi.repository import Gtk

    url = "HTTPS://example.org/license.txt"
    (link,) = _below(_dialog(monkeypatch, "006-D0000-00", _with_license(url, "006-D0000-00")))
    assert isinstance(link, Gtk.LinkButton) and link.get_uri() == url  # no warning: a plain file


def test_no_license_no_link(monkeypatch):
    from gi.repository import Gtk

    (warning,) = _below(_dialog(monkeypatch, "006-V0000-00"))
    assert not isinstance(warning, Gtk.LinkButton) and warning.get_label() == WARNING_EN
    assert _dialog(monkeypatch, "006-D0000-00").get_extra_child() is None  # nothing below


@pytest.mark.parametrize("url", ["file:///etc/passwd", "http://example.org/license.txt",
                                 "javascript:alert(1)"])
def test_a_license_that_is_not_a_web_address_is_only_text(monkeypatch, url):
    from gi.repository import Gtk

    text, _warning = _below(_dialog(monkeypatch, "006-V0000-00", _with_license(url)))
    assert not isinstance(text, Gtk.LinkButton)
    assert isinstance(text, Gtk.Label) and text.get_label() == f"License: {url}"
    assert not text.get_use_markup()


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
