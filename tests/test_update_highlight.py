"""An update to install stands out on the device page (24.10.2026): the chip of the device's
tile ("Update available", warning) under its software version, in the place of the grey
"· update available" of that line; the row "Device software" says how many in the colour of a
warning, with a dot of that colour before its arrow. Once installed (copied, waiting for the
device) or with none to install: as the other rows. A beta counts as any update. All devices
and updates are made up."""

import time
import types

import pytest
from test_fixes_ui import page_window
from test_update_chips import _tile
from test_updates_system import beta_answer

from openexpress import updates
from openexpress.connect import lower_keys


def _offered():
    """A plain file and a beta system bundle, both to install."""
    items = updates.parse_updates(lower_keys(beta_answer()))
    assert [u.is_beta for u in items] == [False, True]
    return items


def _installed():
    """The same, copied to the device: the bundle waits for it to be disconnected."""
    items = _offered()
    for u in items:
        u.on_device = True
    return items


def _device_page():
    """The device page of a connected device (MTP) as the window builds it; nothing is
    asked."""
    from gi.repository import Adw

    from openexpress.gui.app import MainWindow

    win, section = page_window("watch")
    device = types.SimpleNamespace(description="fēnix 8 - 47mm, Solar",
                                   software_version_str="23.39", part_number="006-B4532-00",
                                   unit_id=None)
    win.session = types.SimpleNamespace(device=device, storage=None, logged_in=False,
                                        ownership_error=None, account_devices=None,
                                        mass_storage=False, space=(None, None))
    win._account, win.update_state, win.update_count, win.map_state = None, None, 0, None
    win._update_check_session = None
    for name in ("_show_device_picture", "_check_updates_quietly", "_check_maps_quietly",
                 "_show_last_sync", "_render_map_state", "select_section", "backup"):
        setattr(win, name, lambda *a, **kw: None)
    win._auto_sync_row = lambda: Adw.ActionRow(title="Sync automatically when connected")
    for name in ("show_overview", "_set_update_state", "_render_update_state",
                 "_highlight_update"):
        setattr(win, name, types.MethodType(getattr(MainWindow, name), win))
    win.show_overview()
    return win, section


def _children(widget):
    child = widget.get_first_child()
    while child is not None:
        yield child
        child = child.get_next_sibling()


def _descendants(widget):
    for child in _children(widget):
        yield child
        yield from _descendants(child)


def _visible(widget) -> bool:
    """Shown when its window is (the window of these tests never is)."""
    from gi.repository import Gtk

    while widget is not None and not isinstance(widget, Gtk.Window):
        if not widget.get_visible():
            return False
        widget = widget.get_parent()
    return True


def _chips(widget):
    from gi.repository import Gtk

    return [w for w in _descendants(widget) if isinstance(w, Gtk.Label)
            and w.has_css_class("oe-chip") and _visible(w)]


def _subtitle(row):
    from gi.repository import Gtk

    (label,) = [w for w in _descendants(row) if isinstance(w, Gtk.Label)
                and w.has_css_class("subtitle")]
    return label


def _suffixes(row):
    """The visible widgets at the end of the row, in their order: (kind, text, classes)."""
    from gi.repository import Gtk

    (box,) = [w for w in _descendants(row) if w.has_css_class("suffixes")]
    found = []
    for w in _children(box):
        if not w.get_visible():
            continue
        if isinstance(w, Gtk.Image):
            found.append(("icon", w.get_icon_name(), set(w.get_css_classes())))
        else:
            found.append((type(w).__name__.lower(), getattr(w, "get_label", lambda: None)(),
                          set(w.get_css_classes())))
    return found


def _tile_chip():
    from openexpress.gui import start

    ((words, style, _tooltip),) = start.chips(_tile(update_found=time.time()))
    return words, style


@pytest.mark.needs_display
def test_an_update_to_install_gives_the_device_page_the_chip_of_its_tile():
    """Under "Software 23.39": the tile's chip, the same words and colour; the line of the
    version no longer says it. Among the updates a beta, counted as any other."""
    win, section = _device_page()
    assert _chips(section.page) == []  # still checking
    win._set_update_state(updates.pending(_offered()), [])
    assert win.software_label.get_label() == "Software 23.39"
    (chip,) = _chips(section.page)
    assert (chip.get_label(), "warning") == _tile_chip()
    assert {"oe-chip", "warning"} <= set(chip.get_css_classes())
    labels = [w for w in _children(win.software_label.get_parent())]
    assert labels.index(chip) == labels.index(win.software_label) + 1  # just under it


@pytest.mark.needs_display
def test_the_row_device_software_says_it_in_the_warning_colour_with_a_dot_before_its_arrow():
    win, _section = _device_page()
    win._set_update_state(updates.pending(_offered()), [])
    row = win.update_row
    assert row.get_subtitle() == "2 updates available"
    assert "warning" in _subtitle(row).get_css_classes()
    (dot, arrow) = _suffixes(row)
    assert dot == ("label", "\u25cf", {"warning"})
    assert arrow[:2] == ("icon", "go-next-symbolic")
    assert "warning" not in arrow[2]


@pytest.mark.needs_display
def test_once_installed_or_with_none_the_page_is_as_before():
    """The update copied (the device installs it once disconnected), a check that finds
    none, one that fails: no chip, no dot, the subtitle as the other rows'."""
    win, section = _device_page()

    def shown():
        return (len(_chips(section.page)), len(_suffixes(win.update_row)),
                "warning" in _subtitle(win.update_row).get_css_classes())

    win._set_update_state(updates.pending(_offered()), [])
    assert shown() == (1, 2, True)
    items = _installed()
    win._set_update_state(updates.pending(items), updates.staged(items))
    assert win.update_state == "ready"
    assert win.software_label.get_label() == "Software 23.39\u00a0· update ready to install"
    assert shown() == (0, 1, False)
    win._set_update_state(updates.pending(_offered()), [])
    win._set_update_state([], [])  # up to date
    assert win.update_row.get_subtitle() == "Software is up to date"
    assert shown() == (0, 1, False)
    win._set_update_state(updates.pending(_offered()), [])
    win.update_state = "failed"
    win._render_update_state()
    assert shown() == (0, 1, False)


@pytest.mark.needs_display
def test_in_polish():
    from openexpress import i18n

    i18n.setup("pl")
    try:
        win, section = _device_page()
        said = []
        for count in (1, 2, 5):
            win._set_update_state(updates.pending(_offered())[:1] * count, [])
            said.append(win.update_row.get_subtitle())
        (chip,) = _chips(section.page)
        words = chip.get_label(), win.software_label.get_label(), _tile_chip()[0]
    finally:
        i18n.setup("en")
    assert said == ["Dostępna jest 1 aktualizacja", "Dostępne są 2 aktualizacje",
                    "Dostępnych jest 5 aktualizacji"]
    assert words == ("Aktualizacja dostępna", "Oprogramowanie 23.39", "Aktualizacja dostępna")
