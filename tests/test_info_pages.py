"""A page whose device has none of what it shows (Activities, Courses, Music, Apps not signed in,
Maps of a device without maps, Device Status without diagnostic data) has one look, that of
information: the page's icon and its name, why in one sentence (Device Status: the storage under
it); never the page of an error with Try Again, which stays for an error (an ownership not
known). A made-up device that declares none of it; nothing reaches a device or a server."""

import pytest
from fakes import device_session, page_window, window_methods

pytestmark = pytest.mark.needs_display


def nothing():
    """A made-up device that declares no activities, courses, music nor maps."""
    from openexpress.device.xmldevice import GarminDevice

    return GarminDevice(3000000001, "006-B0000-00", 100, "Made-up Scale", [])


def told(sec) -> tuple[str, str, str, str]:
    """What the page shows: which of its views, its icon, title and words."""
    shown = sec.stack.get_visible_child_name()
    page = sec.info if shown == "info" else sec.error
    return shown, page.get_icon_name(), page.get_title(), page.get_description()


def test_activities():
    from openexpress.gui.app import MainWindow

    win, sec = page_window("activities")
    win.session = device_session(device=nothing())
    MainWindow.load_activities(win)
    assert told(sec) == ("info", sec.icon, sec.title, "This device does not keep activities")


def test_courses():
    from openexpress.gui.app import MainWindow

    win, sec = page_window("courses")
    win.session = device_session(device=nothing())
    MainWindow.load_courses(win)
    assert told(sec) == ("info", sec.icon, sec.title,
                         "This device does not make courses of GPX files")


def test_music():
    from openexpress.gui.app import MainWindow

    win, sec = page_window("music")
    win.session = device_session(device=nothing())
    MainWindow.load_music(win)
    assert told(sec) == ("info", sec.icon, sec.title,
                         "This device does not play music, podcasts or audiobooks")


def test_apps_not_signed_in():
    from openexpress.gui.app import MainWindow

    win, sec = page_window("apps")
    win.session = device_session(device=nothing(), logged_in=False, ownership_error=None)
    MainWindow.load_apps(win)
    assert told(sec) == ("info", sec.icon, sec.title, "Sign in to manage apps")


def test_maps_of_a_device_without_maps():
    """Loaded as the window loads it: the page of information stays after the load."""
    import types

    win, sec = page_window("maps")
    # Garmin's map service does not serve it (made up), and it declares nothing that may be
    # maps: the device has none.
    client = types.SimpleNamespace(unit_info=lambda *a, **kw: {})
    win.session = device_session(device=nothing(), client=client)
    win._set_map_state = lambda *a, **kw: None
    win.map_state, win._map_check_session = None, None
    window_methods(win, ["load_maps", "show_maps"])
    win.load_maps()
    assert told(sec) == ("info", sec.icon, sec.title, "This device has no maps")


def test_device_status_without_diagnostic_data():
    from gi.repository import Adw

    from openexpress import status
    from openexpress.gui import status_page

    win, sec = page_window("status")
    facts = status.Facts(free=1_000_000, capacity=4_000_000, categories={}, counted_at=None,
                         crashes=[], errors_known=True, dumps=[], events=[], hidden_logs=False,
                         copies=(0, 0, None), has_diagnostics=False)
    status_page._empty_page(sec, facts)
    assert told(sec) == ("info", sec.icon, sec.title,
                         ("This device keeps no error logs or memory dumps that openExpress can "
                          "read"))
    assert isinstance(sec.info.get_child(), Adw.Clamp)  # its storage under the words


def test_an_ownership_not_known_is_an_error_with_try_again():
    from openexpress.connect import OwnershipUnknown
    from openexpress.gui.app import MainWindow

    win, sec = page_window("apps")
    win.session = device_session(device=nothing(), logged_in=True,
                                 ownership_error=OwnershipUnknown("made-up network error"))
    MainWindow.load_apps(win)
    assert told(sec) == ("error", "dialog-warning-symbolic", "Something Went Wrong",
                         "made-up network error")
    assert sec.retry.get_parent() is not None and sec.retry.get_visible()


def test_one_function_draws_them_all():
    """The same drawing: a page drawn as another one leaves nothing of it behind (the storage
    of Device Status)."""
    from gi.repository import Adw

    from openexpress.gui.device_page import Section

    sec = Section("status", "Device Status", "x-symbolic", "Reading…")
    sec.show_info("Words", child=Adw.Clamp())
    sec.show_info("Other words")
    assert (sec.info.get_child(), sec.info.get_description()) == (None, "Other words")
