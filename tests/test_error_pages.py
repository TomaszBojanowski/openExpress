"""The pages that say why they show nothing (a device in mass storage mode, the ownership of
the device not known, a device without media): the description of an Adw.StatusPage is
markup, and a text with "<" in it (a network error names "<path>", "<hex>" or
"<urllib3… object at …>") left it empty. It is escaped now, and shown as it is (review of
24.10.2026). All texts are made up."""

import types

import pytest

NETWORK = ("Cannot check the device on your account: network error (HTTPSConnectionPool: "
           "<urllib3.connection.HTTPSConnection object at 0x7f0000000000>: failed to resolve "
           "host, see <path>)")


def _section(key):
    from openexpress.gui.app import Section

    return Section(key, key.title(), "x-symbolic", "Loading…")


def _shown(section) -> list[str]:
    """The texts of the labels of the page "error", as they are drawn."""
    from gi.repository import Gtk

    out, stack = [], [section.error]
    while stack:
        widget = stack.pop()
        if isinstance(widget, Gtk.Label):
            out.append(widget.get_text())
        child = widget.get_first_child()
        while child is not None:
            stack.append(child)
            child = child.get_next_sibling()
    return out


@pytest.mark.needs_display
def test_the_apps_page_shows_an_ownership_error_with_brackets():
    from openexpress.connect import OwnershipUnknown
    from openexpress.gui.app import MainWindow

    section = _section("apps")
    win = types.SimpleNamespace(
        sections={"apps": section},
        session=types.SimpleNamespace(logged_in=True, ownership_error=OwnershipUnknown(NETWORK)))
    MainWindow.load_apps(win)
    assert NETWORK in _shown(section)


@pytest.mark.needs_display
def test_a_page_not_for_a_drive_shows_its_words_with_brackets(monkeypatch):
    from openexpress.gui import app

    words = "A drive <made up> & its words"
    monkeypatch.setattr(app, "mass_storage_text", lambda: words)
    section = _section("maps")
    app.show_not_for_mass_storage(section)
    assert words in _shown(section)


@pytest.mark.needs_display
def test_the_music_page_without_media_shows_its_words_with_brackets(monkeypatch):
    from openexpress import music
    from openexpress.gui.app import MainWindow

    words = "No media <made up> & more"
    monkeypatch.setattr(music, "supported_kinds", lambda device: [])
    monkeypatch.setattr(music, "no_media_message", lambda: words)
    section = _section("music")
    win = types.SimpleNamespace(sections={"music": section},
                                session=types.SimpleNamespace(device=None))
    MainWindow.load_music(win)
    assert words in _shown(section)
