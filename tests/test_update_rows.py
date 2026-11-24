"""The rows of the Updates page: no file name (it is in the question before an install, in
`openexpress updates -v` and in the log) unless two rows have the same title, no "Unspecified"
(the server gave no importance), the file's path as the row's tooltip, and in "Reinstall
Components" the rows with a button first."""
import types

import pytest
from fakes import device_session, software_update, window_methods
from fakes import window as stand_in


def _page(items):
    """The Updates page of a made-up device built from `items`: its groups (title and rows)."""
    import gi

    gi.require_version("Adw", "1")
    from gi.repository import Adw

    from openexpress.gui.app import MainWindow

    class Group:
        def __init__(self, title):
            self.title, self.rows = title, []

        def add(self, row):
            self.rows.append(row)

    class Section:
        def __init__(self):
            self.groups = []

        def group(self, title="", description=""):
            self.groups.append(Group(title))
            return self.groups[-1]

    win = stand_in(
        session=device_session(device=types.SimpleNamespace(
            description="Made-up device", software_version_str="9.00")),
        sections={"updates": Section()}, busy=False, _action_buttons=[],
        _set_update_state=lambda new, staged=(), not_installed=(): None,
        confirm_update=lambda u: None, load_section=lambda *a, **kw: None)
    window_methods(win, ["_update_row", "_updates_status", "_button"])
    MainWindow.show_updates(win, items)
    groups = {g.title: g.rows for g in win.sections["updates"].groups if g.title}
    return groups, Adw


def _expander_rows(expander, Adw):
    """The rows inside an expandable row, in their order (not its own header row, which bears
    its title)."""
    found, stack = [], [expander]
    while stack:
        widget = stack.pop(0)
        child = widget.get_first_child()
        while child is not None:
            if isinstance(child, Adw.ActionRow):
                if child.get_title() != expander.get_title():
                    found.append(child)
            else:
                stack.append(child)
            child = child.get_next_sibling()
    return found


def _buttons(row):
    """The labels of the buttons a row holds."""
    from gi.repository import Gtk

    labels, stack = [], [row]
    while stack:
        widget = stack.pop()
        if isinstance(widget, Gtk.Button) and widget.get_label():
            labels.append(widget.get_label())
        child = widget.get_first_child()
        while child is not None:
            stack.append(child)
            child = child.get_next_sibling()
    return labels


def test_the_importance_shown():
    from openexpress.gui.updates_page import shown_severity

    shown = [shown_severity(software_update(severity=s))
             for s in ("Unspecified", "unspecified", "", "Recommended", "Critical", "Optional",
                       "Urgent")]
    assert shown == ["", "", "", "Recommended", "Critical", "Optional", "Urgent"]


@pytest.mark.needs_display
def test_a_row_without_its_file_and_without_unspecified_with_the_path_as_tooltip():
    groups, _adw = _page([
        software_update(part_number="006-D0001-00", name="Made-up Font", version="2.10",
                        severity="Unspecified", device_path="Garmin/FNT/006-D0001-00.fnt",
                        size=2048),
        software_update(part_number="006-D0002-00", name="Made-up Voice", version="3.00",
                        severity="Critical", device_path="Garmin/Voice/Made-up.vpm", size=4096)])
    rows = groups["Available Updates"]
    seen = [(r.get_title(), r.get_subtitle(), r.get_tooltip_text()) for r in rows]
    assert seen == [
        ("Made-up Font", "2.10\u00a0· 2.0\u00a0kB", "Garmin/FNT/006-D0001-00.fnt"),
        ("Made-up Voice", "3.00\u00a0· 4.1\u00a0kB\u00a0· Critical", "Garmin/Voice/Made-up.vpm")]


@pytest.mark.needs_display
def test_rows_of_the_same_title_keep_their_files_name():
    """The server named only the type of data: two rows read the same, the file tells them
    apart."""
    groups, _adw = _page([
        software_update(part_number="006-D0001-00", name="Map support file", version="2.70",
                        severity="Recommended", device_path="Garmin/006-D0001-00.sid", size=2000),
        software_update(part_number="006-D0002-00", name="Map support file", version="2.80",
                        severity="Recommended", device_path="Garmin/006-D0002-00.sid", size=2000),
        software_update(part_number="006-D0003-00", name="Made-up Voice", version="1.00",
                        severity="Recommended", device_path="Garmin/Voice/Made-up.vpm",
                        size=2000)])
    subtitles = [r.get_subtitle() for r in groups["Available Updates"]]
    assert subtitles == [
        "2.70\u00a0· 2.0\u00a0kB\u00a0· Recommended\u00a0· 006-D0001-00.sid",
        "2.80\u00a0· 2.0\u00a0kB\u00a0· Recommended\u00a0· 006-D0002-00.sid",
        "1.00\u00a0· 2.0\u00a0kB\u00a0· Recommended"]


def _navigator_component(name, on_device, offered, path, part, older):
    """A component of a made-up car navigator such as the Drive 53 EX: on the device at
    `on_device`, offered again by Garmin at `offered`, its file in `older` (updates.OLDER_…)
    or, with `older` None, the very version on the device."""
    from openexpress import updates

    u = software_update(part_number=part, name=name, version=offered, severity="Unspecified",
                        device_path=path, size=26_400, is_reinstall=True)
    u.installed_version = on_device
    u.older_offer = getattr(updates, older) if older else None
    return u


@pytest.mark.needs_display
def test_reinstall_components_of_a_navigator_rows_with_a_button_first():
    """As on the Drive 53 EX: components Garmin offers at an older version than the device's
    (a file that differs, or one that could not be compared: no button, at the end), the same
    file (a button), and components at the device's version (a button); each part by title."""
    groups, adw = _page([
        _navigator_component("Proximity Alert Text, All languages, GLX", "3.90", "3.60",
                             "Text/ProxAlerts.txt", "006-D0001-00", "OLDER_NOT_COMPARED"),
        _navigator_component("Vehicle, 3D Arrow-Blue", "2.40", "2.20.0",
                             "Vehicle/triangle_blue.srf", "006-D0578-00", "OLDER_DIFFERS"),
        _navigator_component("Vehicle, Compact-Blue", "2.40", "2.20.0", "Vehicle/euro_blue.srf",
                             "006-D0578-01", "OLDER_SAME_FILE"),
        _navigator_component("Localization Data", "2.10", "2.10", "Text/Localization.dat",
                             "006-D0002-00", None),
        _navigator_component("Address Points", "1.50", "1.50", "Text/Address.db",
                             "006-D0003-00", None)])
    (expander,) = groups["Advanced"]
    rows = _expander_rows(expander, adw)
    seen = [(r.get_title(), r.get_subtitle(), _buttons(r)) for r in rows]
    assert seen == [
        ("Address Points", "1.50\u00a0· 26.4\u00a0kB", ["Reinstall"]),
        ("Localization Data", "2.10\u00a0· 26.4\u00a0kB", ["Reinstall"]),
        ("Vehicle, Compact-Blue",
         "On the device 2.40\u00a0· Garmin offers 2.20, the same file\u00a0· 26.4\u00a0kB",
         ["Reinstall"]),
        ("Proximity Alert Text, All languages, GLX",
         ("On the device 3.90\u00a0· Garmin offers 3.60, an older version; the file on the "
          "device could not be compared\u00a0· 26.4\u00a0kB"), []),
        ("Vehicle, 3D Arrow-Blue",
         "On the device 2.40\u00a0· Garmin offers 2.20, an older version\u00a0· 26.4\u00a0kB", [])]
    assert [r.get_tooltip_text() for r in rows] == [
        "Text/Address.db", "Text/Localization.dat", "Vehicle/euro_blue.srf",
        "Text/ProxAlerts.txt", "Vehicle/triangle_blue.srf"]
