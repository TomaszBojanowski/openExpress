"""A toast of the window keeps all of its words: a long one (a long name of a map or of a course
in it) goes on to a second and a third line instead of being cut."""

import pytest
from fakes import descendants, turn_until

pytestmark = pytest.mark.needs_display


@pytest.fixture
def window(monkeypatch):
    from openexpress import netstatus
    from openexpress.gui import app

    monkeypatch.setattr(app.MainWindow, "refresh_device", lambda self, cause=None: None)
    win = app.MainWindow(None)
    yield win
    netstatus.STATUS.remove_listener(win._net_listener)
    win.destroy()


def test_a_long_toast_goes_on_to_more_lines_instead_of_being_cut(window):
    from gi.repository import Gtk

    words = ("Wrote Garmin/Made up map of a whole country with every hiking trail and road of "
             "it.img (1.2 GB).")

    def shown():
        return [w for w in descendants(window.toasts)
                if isinstance(w, Gtk.Label) and w.get_label() == words and w.get_mapped()]

    window.present()
    window.toast(words)
    assert turn_until(shown)
    (label,) = shown()
    assert label.get_wrap() and label.get_lines() == 3
