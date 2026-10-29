"""Forgetting a device in the window: a button at the bottom of the read-only view of
a remembered device, a question that says what goes, files not uploaded yet given up only
when ticked, backups deleted only when ticked; then back to the start page, where only its
tile goes. All unit IDs here are made up."""

import json
import types

import pytest

FENIX, EDGE = 3000000001, 3000000002
BODY = ("openExpress forgets what it keeps of this device on this computer: what was "
        "uploaded from it, its last sync, the maps of your own copied to it and the copies "
        "of its diagnostic files. Nothing "
        "changes on the device or on your Garmin account.\n\n"
        "Connected again, it is a new device: its first sync asks again whether to upload "
        "everything or new data only.")


def remembered(unit_id=FENIX, description="fēnix 8", part="006-B4532-00"):
    from openexpress import known_devices

    return known_devices.remember(unit_id, description, part, "23.39", "mtp", now=1.0)


def pending_of(unit_id, count):
    from openexpress import paths

    for n in range(count):
        path = paths.device_dir(unit_id) / "sync" / "pending" / "FIT_TYPE_4" / f"ACT{n}.FIT"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fit")


def last_sync_of(unit_id):
    from openexpress import paths

    path = paths.device_dir(unit_id) / "last_sync.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"unit_id": unit_id, "time": 1.0}))
    return path


class Navigation:
    def __init__(self, visible):
        self.visible, self.popped = visible, []

    def get_visible_page(self):
        return self.visible

    def pop_to_page(self, page):
        self.popped.append(page)
        self.visible = page


def window(unit=FENIX, connected=(), session=None):
    """The window's parts that forgetting uses, with its real code; background work runs at
    once."""
    from openexpress.gui.forget_device import ForgetDevice

    def in_background(fn, on_done, on_error=None):
        try:
            result = fn()
        except Exception as e:  # noqa: BLE001 - as the window's: to on_error
            on_error(e)
            return
        on_done(result)

    errors, toasts = [], []
    win = types.SimpleNamespace(
        _remembered_unit=unit, _remembered_from_account=False, session=session,
        connected=types.SimpleNamespace(devices=list(connected)),
        remembered=types.SimpleNamespace(page="remembered view"),
        start=types.SimpleNamespace(page="start page"),
        errors=errors, error=lambda title, text: errors.append((title, text)),
        toasts=toasts, toast=toasts.append, in_background=in_background)
    win.navigation = Navigation(win.remembered.page)
    for name, member in vars(ForgetDevice).items():
        if callable(member):
            setattr(win, name, types.MethodType(member, win))
    return win


@pytest.fixture
def asked(monkeypatch):
    """The questions presented, recorded; nothing is shown."""
    from gi.repository import Adw

    questions = []
    monkeypatch.setattr(Adw.AlertDialog, "present", lambda self, parent: questions.append(self))
    return questions


def checks(dialog):
    """The check buttons of the question, by their labels."""
    from gi.repository import Gtk

    found = {}

    def walk(widget):
        if isinstance(widget, Gtk.CheckButton):
            found[widget.get_label()] = widget
        child = widget.get_first_child()
        while child is not None:
            walk(child)
            child = child.get_next_sibling()

    extra = dialog.get_extra_child()
    if extra is not None:
        walk(extra)
    return found


def answer(dialog, response):
    """An answer as a click gives it in libadwaita 1.9: "closed" first, then "response"."""
    dialog.emit("closed")
    dialog.emit("response", response)


# --- the button ----------------------------------------------------------------------------


@pytest.mark.needs_display
def test_the_view_of_a_remembered_device_offers_to_forget_it():
    """At the bottom of the view of a device remembered here; not on the view of a device only
    on the account (nothing of it is kept here)."""
    from openexpress.gui.app import MainWindow
    from openexpress.gui.start import RememberedPage

    win = types.SimpleNamespace(
        remembered=RememberedPage(), navigation=types.SimpleNamespace(push=lambda page: None),
        _device_textures={}, _pictures_asked=set(), _account=None,
        _on_start_page=lambda: True,
        in_background=lambda fn, on_done, on_error=None: on_done(fn()))
    for name in ("_show_remembered", "_fill_remembered", "_show_account", "_fill_account",
                 "_last_sync_text", "_show_page_picture"):
        setattr(win, name, types.MethodType(getattr(MainWindow, name), win))
    assert not win.remembered.forget_group.get_visible()
    win._show_remembered(remembered())
    assert win.remembered.forget_group.get_visible()
    assert win.remembered.forget_button.get_label() == "Forget Device…"
    assert win.remembered.forget_button.has_css_class("destructive-action")
    win._show_account({"unitid": EDGE, "productdisplayname": "Edge 1050"})
    assert not win.remembered.forget_group.get_visible()


# --- the question ----------------------------------------------------------------------------


@pytest.mark.needs_display
def test_forget_asks_first_and_says_what_goes(asked):
    """Nothing goes before the answer; Cancel (also the default and Escape) keeps it all."""
    from gi.repository import Adw

    from openexpress import known_devices

    remembered()
    sync = last_sync_of(FENIX)
    win = window()
    win.ask_forget()
    (d,) = asked
    assert d.get_heading() == "Forget fēnix 8?"
    assert d.get_body() == BODY
    assert checks(d) == {}
    assert d.get_response_label("forget") == "Forget"
    assert d.get_response_appearance("forget") == Adw.ResponseAppearance.DESTRUCTIVE
    assert d.get_default_response() == d.get_close_response() == "cancel"
    assert set(known_devices.load()) == {FENIX} and sync.exists()
    answer(d, "cancel")
    assert set(known_devices.load()) == {FENIX} and sync.exists()
    assert win.navigation.popped == [] and win.errors == []


@pytest.mark.needs_display
def test_forgetting_goes_back_to_the_start_page(asked):
    """Answered: everything this installation keeps of it goes, a toast says so, and the
    window goes back to the start page (which draws itself anew: _on_popped)."""
    from openexpress import known_devices, paths

    remembered()
    remembered(EDGE, "Edge 1050", "006-B4440-00")
    last_sync_of(FENIX)
    win = window()
    win.ask_forget()
    answer(asked[0], "forget")
    assert set(known_devices.load()) == {EDGE}
    assert not paths.device_dir(FENIX).exists()
    assert win.toasts == ["Forgot fēnix 8."]
    assert win.navigation.popped == ["start page"]
    assert win._remembered_unit is None and win.errors == []


@pytest.mark.needs_display
def test_files_not_uploaded_must_be_given_up_first(asked):
    """Files not uploaded yet: the question says so, and Forget waits for the tick that gives
    them up."""
    from openexpress import known_devices

    remembered()
    pending_of(FENIX, 2)
    win = window()
    win.ask_forget()
    (d,) = asked
    assert d.get_body() == BODY + (
        "\n\n2 files of this device have not been uploaded yet and would be lost.")
    (tick,) = checks(d).values()
    assert tick.get_label() == "Discard the 2 files not uploaded yet"
    assert not tick.get_active() and not d.get_response_enabled("forget")
    tick.set_active(True)
    assert d.get_response_enabled("forget")
    tick.set_active(False)
    assert not d.get_response_enabled("forget")
    tick.set_active(True)
    answer(d, "forget")
    assert known_devices.load() == {}


@pytest.mark.needs_display
@pytest.mark.parametrize("ticked", [False, True])
def test_the_backups_go_only_when_ticked(asked, backups, ticked):
    from openexpress import known_devices

    remembered()
    for name in ("2026-10-01 (10.00.00)", "2026-10-02 (10.00.00)"):
        (backups / str(FENIX) / name).mkdir(parents=True)
    win = window()
    win.ask_forget()
    (d,) = asked
    (tick,) = checks(d).values()
    assert tick.get_label() == "Also delete its 2 backups"
    assert not tick.get_active() and d.get_response_enabled("forget")
    tick.set_active(ticked)
    answer(d, "forget")
    assert known_devices.load() == {}
    assert (backups / str(FENIX)).exists() is not ticked


@pytest.mark.needs_display
def test_the_question_is_let_go_once_closed(asked):
    """The window holds the question while it is open, and no longer (what is ticked goes
    with the ticks and the answer, not with the window)."""
    remembered()
    win = window()
    win.ask_forget()
    assert win._forget_dialog is asked[0]
    asked[0].emit("closed")
    assert win._forget_dialog is None and not hasattr(win, "_forget_choice")


# --- refusals ----------------------------------------------------------------------------------


@pytest.mark.needs_display
def test_a_sync_running_is_told_and_nothing_goes(asked):
    from openexpress import known_devices, service

    remembered()
    win = window()
    win.ask_forget()
    with service.sync_lock():
        answer(asked[0], "forget")
    assert win.errors == [("Forget Device",
                           "A sync is running. Forget the device once it has finished.")]
    assert set(known_devices.load()) == {FENIX}
    assert win.navigation.popped == [] and win.toasts == []


@pytest.mark.needs_display
def test_a_device_connected_meanwhile_is_not_forgotten(asked):
    """Plugged in while its view was open: told to unplug it, nothing asked."""
    from openexpress import known_devices

    remembered()
    win = window(connected=[types.SimpleNamespace(unit_id=FENIX)])
    win.ask_forget()
    assert asked == []
    assert win.errors == [("Forget Device",
                           "fēnix 8 is connected: unplug it to forget it.")]
    assert set(known_devices.load()) == {FENIX}


@pytest.mark.needs_display
def test_the_device_open_is_not_forgotten(asked):
    remembered()
    session = types.SimpleNamespace(device=types.SimpleNamespace(unit_id=FENIX))
    win = window(session=session)
    win.ask_forget()
    assert asked == [] and len(win.errors) == 1


@pytest.mark.needs_display
def test_nothing_is_asked_for_a_device_not_remembered(asked):
    """The view of a device only on the account, or one forgotten meanwhile (e.g. with the
    command line): nothing to forget."""
    remembered(EDGE, "Edge 1050", "006-B4440-00")
    win = window()
    win.ask_forget()
    win._remembered_unit, win._remembered_from_account = EDGE, True
    win.ask_forget()
    assert asked == [] and win.errors == []


# --- the start page ----------------------------------------------------------------------------


def _tile(name, connected=False):
    from openexpress.devicelist import Tile

    return Tile(name, "006-B4440-00", "33.13", "mtp", connected, False, False,
                None if connected else 1.0, "", None, None)


@pytest.mark.needs_display
@pytest.mark.parametrize("view, count", [("auto", 3), ("list", 3), ("auto", 9)])
def test_a_tile_gone_is_taken_away_alone(view, count):
    """One device gone from a section laid out the same way without it: only its item goes;
    the others keep their widgets and get their new places."""
    from openexpress.gui.start import StartPage

    page = StartPage()
    page.view = view
    connected = (_tile("Edge 1050", connected=True), "Version 33.13")
    rest = [(_tile(f"Watch {n}"), "Last seen 1.10.2026") for n in range(count)]
    page.show([connected, *rest], notice=False)
    before = page.tiles()
    page.show([connected, *rest[:1], *rest[2:]], notice=False)
    after = page.tiles()
    assert after == [before[0], before[1], *before[3:]]
    assert [item.key for item in after] == list(range(count))


@pytest.mark.needs_display
def test_a_section_laid_out_anew_without_it_is_built_again():
    """Seven remembered devices are a list, six a grid: the section is built again."""
    from openexpress import devicelist
    from openexpress.gui.start import StartPage

    page = StartPage()
    rest = [(_tile(f"Watch {n}"), "Last seen 1.10.2026")
            for n in range(devicelist.GRID_MAX + 1)]
    page.show(rest, notice=False)
    before = page.tiles()
    page.show(rest[1:], notice=False)
    after = page.tiles()
    assert not set(after) & set(before)
    assert page.remembered_grid.get_visible() and not page.remembered_list.get_visible()


@pytest.mark.needs_display
def test_the_button_of_the_window_asks(monkeypatch, asked):
    """In the window itself: the view of a remembered device shows the button, and it asks."""
    from openexpress import netstatus
    from openexpress.gui import app
    from openexpress.gui.app import MainWindow

    known = remembered()
    monkeypatch.setattr(MainWindow, "refresh_device", lambda self, cause=None: None)
    monkeypatch.setattr(app.GLib, "timeout_add_seconds", lambda s, fn: 0)
    win = MainWindow(None)
    try:
        win._fill_remembered(known)
        assert win.remembered.forget_group.get_visible()
        win.remembered.forget_button.emit("clicked")
        assert [d.get_heading() for d in asked] == ["Forget fēnix 8?"]
    finally:
        netstatus.STATUS.remove_listener(win._net_listener)
        win.destroy()


def _button_labelled(widget, label):
    """The button of a dialog with these words (its responses are buttons)."""
    from gi.repository import Gtk

    if isinstance(widget, Gtk.Button) and widget.get_label() == label:
        return widget
    child = widget.get_first_child()
    while child is not None:
        found = _button_labelled(child, label)
        if found is not None:
            return found
        child = child.get_next_sibling()
    return None


def _turn_until(done, seconds=5.0):
    import time

    from gi.repository import GLib

    deadline = time.monotonic() + seconds
    while not done() and time.monotonic() < deadline:
        GLib.MainContext.default().iteration(False)
    return done()


@pytest.mark.needs_display
def test_clicking_forget_in_the_window_forgets(monkeypatch):
    """The question shown in the window and its button Forget clicked, as libadwaita does it:
    "closed" comes before "response" (libadwaita 1.9), so the choice must not be
    taken from what "closed" lets go of. The device is forgotten and the window goes back to
    the start page."""
    from openexpress import known_devices, netstatus, paths
    from openexpress.gui import app
    from openexpress.gui.app import MainWindow

    known = remembered(EDGE, "Venu 2", "006-B3703-00")
    last_sync_of(EDGE)
    monkeypatch.setattr(MainWindow, "refresh_device", lambda self, cause=None: None)
    monkeypatch.setattr(MainWindow, "_update_relative_timer", lambda self: None)
    monkeypatch.setattr(app.GLib, "timeout_add_seconds", lambda s, fn: 0)
    win = MainWindow(None)
    try:
        win.present()
        win.navigation.set_animate_transitions(False)
        win._show_remembered(known)
        assert win.navigation.get_visible_page() is win.remembered.page
        win.remembered.forget_button.emit("clicked")
        dialog = win._forget_dialog
        assert _turn_until(lambda: dialog.get_mapped())
        _button_labelled(dialog, "Forget").emit("clicked")
        assert _turn_until(lambda: known_devices.load() == {})
        assert not paths.device_dir(EDGE).exists()
        assert _turn_until(lambda: win.navigation.get_visible_page() is win.start.page)
        assert win._forget_dialog is None
    finally:
        netstatus.STATUS.remove_listener(win._net_listener)
        win.destroy()
