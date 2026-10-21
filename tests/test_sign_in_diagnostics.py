"""The sign-in window tells the log what it runs on and what happens in it (issue #10), never
the address the ticket is in; a page whose web process ended offers to try again and the
sign-in without the window; a crash of the program leaves Python's trace in crash.log, which
the report attaches filtered. No network: the WebView is a stand-in, its signals are emitted
by the test."""

import functools
import logging
import types

import pytest

from openexpress import applog, paths, report
from openexpress.auth import sso
from openexpress.settings import Settings

TICKET_URL = ("https://sso.garmin.com/sso/embed?ticket=ST-0000000-made-up-cas"
              "&service=https%3A%2F%2Fconnect.garmin.com%2Fmodern")


@functools.cache
def _fake_view_class():
    """A widget in the place of WebKit.WebView: it records the handlers connected to it (to be
    called by the test) and the addresses it is told to load; it loads nothing."""
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk

    class FakeView(Gtk.Box):
        def __init__(self, network_session=None, **_kw):
            super().__init__()
            self.handlers, self.loaded, self.uri = {}, [], None

        def connect(self, signal, handler, *args):
            self.handlers.setdefault(signal, []).append(handler)
            return len(self.handlers)

        def fire(self, signal, *args):
            return [handler(self, *args) for handler in self.handlers.get(signal, [])]

        def get_uri(self):
            return self.uri

        def load_uri(self, uri):
            self.loaded.append(uri)

        def get_settings(self):
            return types.SimpleNamespace(get_hardware_acceleration_policy=lambda: (
                types.SimpleNamespace(value_nick="on-demand")))

    return FakeView


def _nick(name):
    return types.SimpleNamespace(value_nick=name)


@pytest.fixture
def sign_in_window(monkeypatch, xdg):
    """The real sign-in window with the stand-in WebView, shown on the display of the tests:
    (window, view, the tickets it handed over)."""
    import gi

    gi.require_version("WebKit", "6.0")
    from gi.repository import WebKit

    monkeypatch.setattr(WebKit, "WebView", _fake_view_class())
    monkeypatch.setattr(WebKit.NetworkSession, "new_ephemeral", staticmethod(lambda: None))
    monkeypatch.setattr(WebKit.WebContext, "get_default", staticmethod(
        lambda: types.SimpleNamespace(set_preferred_languages=lambda _l: None)))
    tickets = []
    win = sso.create_login_window(Settings(), tickets.append)
    view = win.get_child()
    yield win, view, tickets
    win.destroy()


@pytest.mark.needs_display
def test_the_sign_in_window_says_what_it_runs_on(sign_in_window, caplog):
    win, _view, _tickets = sign_in_window
    with caplog.at_level(logging.INFO, logger="openexpress.auth.sso"):
        win.present()  # realized: its renderer is known now
    (line,) = [r.getMessage() for r in caplog.records
               if r.getMessage().startswith("login: sign-in window: ")]
    for part in ("WebKit 2.", ", GTK 4.", ", GDK Gdk", ", GSK Gsk", ", session ", ", runtime ",
                 ", GPU ", ", acceleration on-demand", ", env "):
        assert part in line, (part, line)
    assert "://" not in line  # no address
    assert sso.sign_in_marker().exists()  # open: a crash now is noted at the next start
    win.destroy()
    assert not sso.sign_in_marker().exists()


@pytest.mark.needs_display
def test_the_page_is_logged_by_its_host_only(sign_in_window, caplog):
    """Every step of the page, by its host: the ticket is in the address, never in the log."""
    win, view, tickets = sign_in_window
    view.uri = "https://sso.garmin.com/mobile/sso/en_US/sign-in?clientId=made-up"
    with caplog.at_level(logging.INFO, logger="openexpress.auth.sso"):
        view.fire("load-changed", _nick("started"))
        view.fire("load-failed", _nick("started"), TICKET_URL,
                  types.SimpleNamespace(domain="WebKitNetworkError", code=302))
        view.uri = TICKET_URL
        view.fire("load-changed", _nick("committed"))
    lines = [r.getMessage() for r in caplog.records]
    assert "login: page started (sso.garmin.com)" in lines
    assert "login: the page did not load (sso.garmin.com): WebKitNetworkError 302" in lines
    assert "login: ticket received, the sign-in window closes" in lines
    assert len(tickets) == 1 and tickets[0].ticket.startswith("ST-")
    assert all("ST-" not in line and "ticket=" not in line for line in lines)


@pytest.mark.needs_display
def test_closing_the_window_is_logged(sign_in_window, caplog):
    win, _view, tickets = sign_in_window
    win.present()
    with caplog.at_level(logging.INFO, logger="openexpress.auth.sso"):
        win.close()
    assert "login: sign-in window closed by the user" in [r.getMessage() for r in caplog.records]
    assert tickets == [None]


@pytest.mark.needs_display
@pytest.mark.parametrize("flatpak, command", [
    (True, "flatpak run io.github.tomaszbojanowski.OpenExpress login --manual"),
    (False, "openexpress login --manual")])
def test_a_page_whose_web_process_ended_offers_to_try_again(sign_in_window, monkeypatch,
                                                            caplog, flatpak, command):
    """The page's web process ended (it crashed, say): the window says so, with the command
    that signs in without it, and Try Again loads the sign-in page anew."""
    from gi.repository import Gtk

    monkeypatch.setattr(paths, "in_flatpak", lambda: flatpak)
    win, view, _tickets = sign_in_window
    with caplog.at_level(logging.INFO, logger="openexpress.auth.sso"):
        view.fire("web-process-terminated", _nick("crashed"))
    assert "login: the page's web process ended (crashed)" in [
        r.getMessage() for r in caplog.records]
    stopped = win.get_child()
    assert stopped is not view

    def widgets(widget):
        child = widget.get_first_child()
        while child is not None:
            yield child
            yield from widgets(child)
            child = child.get_next_sibling()

    labels = [w.get_label() for w in widgets(stopped) if isinstance(w, Gtk.Label)]
    assert "The sign-in page stopped working" in labels and command in labels
    (again,) = [w for w in widgets(stopped) if isinstance(w, Gtk.Button)]
    assert again.get_label() == "Try Again"
    loaded = list(view.loaded)
    again.emit("clicked")
    assert win.get_child() is view  # the page back in the window
    assert view.loaded == [*loaded, sso.sign_in_url(Settings())]


def test_the_runtime_and_the_graphics_are_read_from_the_system(tmp_path):
    info = tmp_path / "flatpak-info"
    info.write_text("[Application]\nname=io.github.tomaszbojanowski.OpenExpress\n"
                    "runtime=runtime/org.gnome.Platform/x86_64/50\n")
    assert sso.flatpak_runtime(info) == "org.gnome.Platform/x86_64/50"
    assert sso.flatpak_runtime(tmp_path / "none") == "none"
    drm = tmp_path / "drm"
    for card, vendor in (("card0", "0x10de"), ("card1", "0x8086"), ("card1-DP-1", "0x8086")):
        (drm / card / "device").mkdir(parents=True)
        (drm / card / "device" / "vendor").write_text(vendor + "\n")
    assert sso.gpu_vendors(drm) == "0x10de NVIDIA, 0x8086 Intel"
    assert sso.gpu_vendors(tmp_path / "no-drm") == "none"


def test_the_rendering_variables_set_are_told(monkeypatch):
    for name in sso.RENDERING_ENV:
        monkeypatch.delenv(name, raising=False)
    assert sso.rendering_env() == "none"
    monkeypatch.setenv("WEBKIT_DISABLE_DMABUF_RENDERER", "1")
    monkeypatch.setenv("GSK_RENDERER", "cairo")
    assert sso.rendering_env() == "GSK_RENDERER=cairo WEBKIT_DISABLE_DMABUF_RENDERER=1"


# --- a crash of the program: crash.log, and the next start ---------------------------------


def test_a_crash_is_written_to_crash_log(monkeypatch, log_file):
    import faulthandler

    enabled = []
    monkeypatch.setattr(applog, "_crash_file", None)
    monkeypatch.setattr(faulthandler, "is_enabled", lambda: False)
    monkeypatch.setattr(faulthandler, "enable", lambda file, all_threads: enabled.append(file))
    applog.enable_crash_log()
    (file,) = enabled
    try:
        assert file.name == str(applog.crash_path())
        assert (applog.crash_path().stat().st_mode & 0o777) == 0o600
    finally:
        file.close()


def test_a_faulthandler_enabled_already_is_left_alone(monkeypatch):
    """E.g. PYTHONFAULTHANDLER=1, or pytest's own: its traces go where they went."""
    import faulthandler

    monkeypatch.setattr(faulthandler, "is_enabled", lambda: True)
    monkeypatch.setattr(faulthandler, "enable", lambda **_kw: pytest.fail("taken over"))
    applog.enable_crash_log()
    assert not applog.crash_path().exists()


def test_the_report_attaches_the_crash_log_filtered(monkeypatch):
    import os

    home = os.path.expanduser("~")
    path = applog.crash_path()
    paths.make_dir(path.parent)
    path.write_text("Fatal Python error: Segmentation fault\n\n"
                    "Current thread 0x00007f00 (most recent call first):\n"
                    f'  File "{home}/garmin-linux/openexpress/auth/sso.py", line 118 in finish\n')
    crash = applog.crash_tail()
    assert crash[0] == "Fatal Python error: Segmentation fault"
    assert all(home not in line for line in crash)
    text = report.with_log("report", ["a line"], crash)
    assert "### Crash log (last 4 lines of crash.log)" in text and "sso.py" in text
    assert report.with_log("report", ["a line"], []) == report.with_log("report", ["a line"])


def test_an_unclean_end_with_the_sign_in_window_open_is_told_once(caplog):
    marker = sso.sign_in_marker()
    paths.make_dir(marker.parent)
    marker.touch()
    applog.crash_path().write_text("Fatal Python error: Aborted\n")  # after the window opened
    with caplog.at_level(logging.INFO, logger="openexpress.auth.sso"):
        sso.note_unfinished_sign_in()
        sso.note_unfinished_sign_in()
    lines = [r.getMessage() for r in caplog.records]
    assert lines == [("login: the last run ended while the sign-in window was open; "
                      "crash.log has its trace (report --log attaches it)")]
    assert not marker.exists()
