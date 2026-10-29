"""How much a copy of the app list differs from the device's order: the fewest apps to move
(those outside the longest common subsequence of the apps in both lists), not the positions
that change (one app moved to the bottom changes them all). Apps only in one of the lists are
said apart. All data is made up."""

import types
import uuid

import pytest
from fakes import window as stand_in
from test_ciq_music import (  # noqa: F401 - fixtures
    STORE_ID,
    apps_cli,
    copy_names,
    iq_device,
    out_bin,
)

from openexpress import ciq, i18n

NB = "\u00a0"
WIDGETS = [(uuid.UUID(int=n + 1), 2, f"Widget {n}", False, "") for n in range(6)]
STORE = (STORE_ID, 1, "Connect IQ Store", False, "DADE7E4")
EXTRA = (uuid.UUID(int=99), 2, "Only in the copy", False, "")


def _plan(now, copy):
    return ciq.plan_order(ciq.parse_out_bin(out_bin(now)), ciq.parse_out_bin(out_bin(copy)))


# --- the measure ---------------------------------------------------------------------------------


def test_one_app_from_the_top_to_the_bottom_is_one_app_moved():
    now = [STORE, *WIDGETS]
    plan = _plan(now, [STORE, *WIDGETS[1:], WIDGETS[0]])
    assert len(plan.moved) == 6  # every position of the widgets changes
    assert ciq.apps_moved(plan) == 1
    assert ciq.order_difference(plan) == "1 app moved"


def test_two_apps_swapped_far_apart_are_two_apps_moved():
    swapped = list(WIDGETS)
    swapped[0], swapped[5] = swapped[5], swapped[0]
    plan = _plan([STORE, *WIDGETS], [STORE, *swapped])
    assert ciq.apps_moved(plan) == 2 and ciq.order_difference(plan) == "2 apps moved"


def test_the_same_order_is_none_moved():
    plan = _plan([STORE, *WIDGETS], [STORE, *WIDGETS])
    assert ciq.apps_moved(plan) == 0 and ciq.order_difference(plan) == "0 apps moved"


def test_apps_only_in_the_copy_or_only_on_the_device_are_said_apart():
    """They are not moved (the order is of the apps in both); said after the count."""
    now = [STORE, *WIDGETS]
    plan = _plan(now, [STORE, EXTRA, *WIDGETS[1:], WIDGETS[0]])
    assert ciq.apps_moved(plan) == 1
    assert ciq.order_difference(plan) == f"1 app moved{NB}· 1 not on the device"
    plan = _plan(now, [STORE, *WIDGETS[2:], WIDGETS[1]])  # Widget 0 not in the copy
    assert ciq.apps_moved(plan) == 1
    assert ciq.order_difference(plan) == f"1 app moved{NB}· 1 not in the copy"


def test_the_measure_in_polish():
    i18n.setup("pl")
    try:
        # One and two apps to the bottom; all six the other way round (five moved).
        orders = [WIDGETS[1:] + WIDGETS[:1], WIDGETS[2:] + WIDGETS[:2], WIDGETS[::-1]]
        said = [ciq.order_difference(_plan([STORE, *WIDGETS], [STORE, *order]))
                for order in orders]
        extra = ciq.order_difference(_plan([STORE, *WIDGETS],
                                           [STORE, EXTRA, *WIDGETS[1:], WIDGETS[0]]))
    finally:
        i18n.setup("en")
    assert said == ["1 aplikacja przesunięta", "2 aplikacje przesunięte",
                    "5 aplikacji przesuniętych"]
    assert extra == f"1 aplikacja przesunięta{NB}· 1 nie ma na urządzeniu"


def test_the_rows_of_the_dialog_say_the_measure():
    from openexpress.gui.app import MainWindow

    copy = types.SimpleNamespace(name="GARMIN-APPS 2026-10-12 (17.37.37)")
    one = _plan([STORE, *WIDGETS], [STORE, *WIDGETS[1:], WIDGETS[0]])
    same = _plan([STORE, *WIDGETS], [STORE, *WIDGETS])
    assert MainWindow._order_rows([(copy, one), (copy, same)]) == [
        ("2026-10-12 (17.37.37)", "1 app moved", True),
        ("2026-10-12 (17.37.37)", "The same order as now", False)]


def test_the_command_line_says_the_same_measure(iq_device, device_root, backups, apps_cli,
                                                capsys):
    from openexpress.cli import main

    st, dev = iq_device
    (device_root / "GARMIN/APPS/OUT.BIN").write_bytes(out_bin([STORE, *WIDGETS]))
    folder = backups / str(dev.unit_id) / "GARMIN-APPS 2026-10-12 (17.37.37) Test watch"
    folder.mkdir(parents=True)
    (folder / "OUT.BIN").write_bytes(out_bin([STORE, EXTRA, *WIDGETS[1:], WIDGETS[0]]))
    assert main(["--path", str(st.root), "apps", "order"]) == 0
    out = capsys.readouterr().out
    assert ("GARMIN-APPS 2026-10-12 (17.37.37) Test watch: 8 entries, 1 app moved · 1 not on "
            "the device") in out


# --- the dialog opens at its top, nothing chosen ------------------------------------------------


def _turn(done, seconds=3.0):
    import time

    from gi.repository import GLib

    end = time.monotonic() + seconds
    while not done() and time.monotonic() < end:
        GLib.MainContext.default().iteration(False)
    return done()


@pytest.mark.needs_display
def test_the_list_of_copies_opens_at_its_top_with_none_chosen(iq_device, device_root, backups,
                                                             monkeypatch):
    """Twelve copies, more than the dialog shows at once: in a window, the list is at its
    top, its first row whole, no row chosen and Next not to be pressed."""
    from fakes import RunNow
    from gi.repository import Adw, Gtk
    from test_dialogs_released import _presented_in_a_window

    from openexpress.gui import app

    st, dev = iq_device
    (device_root / "GARMIN/APPS/OUT.BIN").write_bytes(out_bin([STORE, *WIDGETS]))
    for n in range(12):
        order = list(WIDGETS)
        order.insert(n % 6, order.pop((n + 1) % 6))
        folder = backups / str(dev.unit_id) / f"GARMIN-APPS 2026-10-12 ({10 + n}.00.00) Test"
        folder.mkdir(parents=True)
        (folder / "OUT.BIN").write_bytes(out_bin([STORE, *order] + [
            (uuid.UUID(int=200 + n), 2, f"Extra {n}", False, "")]))
    host = _presented_in_a_window(monkeypatch, Adw.AlertDialog)
    host.set_default_size(900, 800)
    host.present()
    shown = []
    present = Adw.AlertDialog.present
    monkeypatch.setattr(Adw.AlertDialog, "present",
                        lambda self, parent: (shown.append(self), present(self, parent)))
    real = ciq.order_choices
    monkeypatch.setattr(ciq, "order_choices", lambda device, state: real(device, state, backups))
    win = stand_in(
        session=types.SimpleNamespace(device=dev, storage=st), run_task=RunNow(),
        error=lambda title, text: pytest.fail(text), _order_blocked=lambda state: None,
        _order_rows=app.MainWindow._order_rows, confirm_order=lambda *a: None)
    try:
        app.MainWindow.choose_order(win)
        (dialog,) = shown
        assert _turn(dialog.get_mapped)
        _turn(lambda: False, 0.5)
        scroller = dialog.get_extra_child()
        box = next(w for w in _descendants(scroller) if isinstance(w, Gtk.ListBox))
        adjustment = scroller.get_vadjustment()
        assert adjustment.get_upper() > adjustment.get_page_size()  # it does scroll
        assert adjustment.get_value() == adjustment.get_lower()
        first = box.get_row_at_index(0)
        ok, bounds = first.compute_bounds(scroller)
        assert ok and bounds.get_y() >= 0
        assert bounds.get_y() + bounds.get_height() <= adjustment.get_page_size()
        assert box.get_selected_row() is None
        assert not dialog.get_response_enabled("next")
        box.select_row(box.get_row_at_index(2))  # chosen by the user: Next
        assert dialog.get_response_enabled("next")
    finally:
        for d in shown:
            d.force_close()
        host.destroy()


def _descendants(widget):
    child = widget.get_first_child()
    while child is not None:
        yield child
        yield from _descendants(child)
        child = child.get_next_sibling()


# --- the question and `apps order --from` say the same measure first --------------------------


def test_the_question_says_the_apps_moved_first_and_the_positions_below():
    """"Restore this order?": the measure of the list of copies first, then the positions
    that change, as details."""
    from openexpress.gui.app import MainWindow

    plan = _plan([STORE, *WIDGETS], [STORE, EXTRA, *WIDGETS[1:], WIDGETS[0]])
    lines = MainWindow._order_summary(plan, "A note.").splitlines()
    assert lines[0] == f"1 app moved{NB}· 1 not on the device"
    assert lines[1] == "6 positions would change (widget: 6):"
    assert lines[2] == "  2: Widget 1 (now: Widget 0)"
    assert "Only in the copy, ignored: Only in the copy" in lines


def test_apps_order_from_says_the_same(iq_device, device_root, backups, apps_cli, capsys):
    from openexpress.cli import main

    st, dev = iq_device
    (device_root / "GARMIN/APPS/OUT.BIN").write_bytes(out_bin([STORE, *WIDGETS]))
    folder = backups / str(dev.unit_id) / "GARMIN-APPS 2026-10-12 (17.37.37) Test watch"
    folder.mkdir(parents=True)
    (folder / "OUT.BIN").write_bytes(out_bin([STORE, *WIDGETS[1:], WIDGETS[0]]))
    assert main(["--path", str(st.root), "apps", "order", "--from", "17.37.37"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "Order of the apps from the copy GARMIN-APPS 2026-10-12 (17.37.37) Test watch"
    assert out[1] == "1 app moved"
    assert out[2] == "6 positions would change (widget: 6):"
