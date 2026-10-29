"""A tile clicked shows that its device is being opened (a spinner over its picture, or in place
of the arrow of a row) for the 4.5-5 s the first opening of a watch can take; a second click on
it meanwhile says nothing, a click on another tile "Another operation is in progress". Every
device here is made up."""

import types

import pytest

from openexpress.device.gio_storage import DeviceSummary, FoundDevice

ANOTHER = "Another operation is in progress"


def _found(name: str) -> FoundDevice:
    return FoundDevice(f"mtp://made-up-{name}/", name, True, kind="mtp")


class Start:
    """The start page as the window uses it here: which tile shows its opening."""

    def __init__(self):
        self.opening = []

    def set_opening(self, key) -> None:
        self.opening.append(key)


def _window():
    from openexpress.gui.app import MainWindow

    tasks, toasts = [], []
    win = types.SimpleNamespace(
        busy=False, toast=toasts.append, start=Start(), tasks=tasks, toasts=toasts,
        _start_entries=[(object(), _found("a")), (object(), _found("b"))])

    def run_task(title, fn, done=None, failed=None, **kw):
        tasks.append((title, done, failed))
        win.busy = True  # until the test ends it

    win.run_task = run_task
    for name in ("open_choice", "_open_tile"):
        setattr(win, name, types.MethodType(getattr(MainWindow, name), win))
    return win


def test_a_second_click_on_the_tile_being_opened_says_nothing():
    win = _window()
    win._open_tile(0)
    win._open_tile(0)
    assert len(win.tasks) == 1 and win.toasts == []
    assert win.start.opening == [0]
    win._open_tile(1)  # another device: "Another operation is in progress"
    assert len(win.tasks) == 1 and win.toasts == [ANOTHER]


@pytest.mark.parametrize("ends", ["done", "failed"])
def test_the_tile_shows_its_opening_until_it_ends(ends):
    win = _window()
    win.connected = types.SimpleNamespace(stop_choosing=lambda: None)
    win.show_device = lambda: None
    win.error = lambda title, text: None
    win.refresh_device = lambda cause=None: None
    win._open_tile(1)
    ((_title, done, failed),) = win.tasks
    win.busy = False
    if ends == "done":
        done(types.SimpleNamespace())
    else:
        assert failed(OSError("unplugged")) is True
    assert win.start.opening == [1, None]
    win._open_tile(1)  # opened, or failed: a click opens it again
    assert len(win.tasks) == 2 and win.toasts == []


def test_an_opening_not_from_a_tile_shows_no_tile_opening():
    win = _window()
    win.open_choice(_found("a"))
    assert win.start.opening == [] and len(win.tasks) == 1


def _summary(name: str, unit_id: int) -> DeviceSummary:
    return DeviceSummary(_found(name), name, "006-B0000-00", "1.00", unit_id)


def _shown(page, count: int):
    from openexpress import devicelist

    entries = devicelist.entries([_summary(f"Watch {i}", 3000000100 + i)
                                  for i in range(count)], {}, None, False)
    page.show([(tile, "1.00") for tile, _source in entries], False)
    return page.tiles()


def _opening_seen(item) -> bool:
    return item.opening.get_visible()


@pytest.mark.needs_display
@pytest.mark.parametrize("view", ["grid", "list"])
def test_the_spinner_shows_on_the_tile_or_the_row_only(view):
    from gi.repository import Gtk

    from openexpress.gui import start

    page = start.StartPage()
    page.view = view
    items = _shown(page, 2)
    height = items[0].measure(Gtk.Orientation.VERTICAL, 200)[1]
    page.set_opening(items[1].key)
    assert [_opening_seen(i) for i in items] == [False, True]
    assert items[0].measure(Gtk.Orientation.VERTICAL, 200)[1] == height  # nothing moves
    if view == "list":
        assert not items[1].arrow.get_visible() and items[0].arrow.get_visible()
    page.set_opening(None)
    assert [_opening_seen(i) for i in items] == [False, False]
    if view == "list":
        assert items[1].arrow.get_visible()


@pytest.mark.needs_display
def test_the_spinner_stays_when_the_page_is_drawn_again():
    from openexpress.gui import start

    page = start.StartPage()
    page.view = "grid"
    items = _shown(page, 2)
    page.set_opening(items[0].key)
    page.view = "list"  # built again
    items = _shown(page, 2)
    assert [_opening_seen(i) for i in items] == [True, False]
