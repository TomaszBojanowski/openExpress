"""Where the user goes after a detection (openexpress/gui/navigation.py): the start page's
navigation matrix as the window behaves today, one row per situation, with a short id. Rows
whose id ends in "-Q" and the number of a decision (AGENTS.md) are today's behaviour that
this decision is to change; the change will show in this table. No display, no GTK;
the addresses and unit IDs are made up."""

import re

import pytest

from openexpress.device.gio_storage import ABSENT, MASS_STORAGE, PRESENT, STARTING
from openexpress.gui.navigation import (
    READ_THE_LIST,
    Cause,
    CheckOpen,
    Detect,
    Nothing,
    Page,
    ReadList,
    Seen,
    ShowDevice,
    ShowStart,
    Stay,
    View,
    describe,
    navigate_after,
    plan,
)

W = "mtp://091e_51b4_0000abcdef12"  # the watch
E = "mtp://Garmin_Edge_1050_0000abcdef34"  # the Edge
V = "file:///run/media/made-up-user/GARMIN"  # the Varia, a drive
WATCH_UNIT, EDGE_UNIT, VARIA_UNIT, VENU_UNIT = 1234567890, 3000000002, 3000000003, 3000000004
UNITS = {W: WATCH_UNIT, E: EDGE_UNIT, V: VARIA_UNIT}

S = View(Page.START)
D = View(Page.DEVICE)
R_WATCH = View(Page.REMEMBERED, WATCH_UNIT)  # the watch's read-only view
R = View(Page.REMEMBERED, VENU_UNIT)  # the read-only view of a Venu 2 not connected
ACCOUNT_UNIT = 3000000007
R_ACCOUNT = View(Page.REMEMBERED, ACCOUNT_UNIT, from_account=True)  # a device of the account


def seen(*connected, open=None, ids=True):
    """What was seen, with the unit IDs of the devices read (`ids` False: none has one)."""
    units = frozenset(UNITS[c] for c in connected) if ids else frozenset()
    return Seen(frozenset(connected), open, units, UNITS[open] if ids and open else None)


NONE = seen()

# (id, cause, page shown, what the user saw, what the detection found, where the user goes)
NAVIGATION = [
    # --- the window opens ------------------------------------------------------------------
    ("N0-start", Cause.START, S, NONE, NONE, Stay(redraw_start=True)),
    ("N1-start", Cause.START, S, NONE, seen(W, open=W), ShowDevice(animate=False)),
    ("N2-start", Cause.START, S, NONE, seen(W, E), Stay(redraw_start=True)),
    # --- a device plugged in (mounted) -----------------------------------------------------
    ("N0-plug-on-S", Cause.PLUGGED, S, NONE, seen(W, open=W), ShowDevice()),
    ("N0-plug-same-on-R", Cause.PLUGGED, R_WATCH, NONE, seen(W, open=W), ShowDevice()),
    # Q1: on a remembered device's view nothing moves but that very device plugged in: one
    # other device is opened in the background, two give the list, under the view.
    ("N0-plug-other-on-R", Cause.PLUGGED, R, NONE, seen(W, open=W), Stay(redraw_start=True)),
    ("N0-plug-two-on-R", Cause.PLUGGED, R, NONE, seen(W, E), Stay(redraw_start=True)),
    # A device without a unit ID is never the one shown.
    ("N0-plug-same-without-id-on-R", Cause.PLUGGED, R_WATCH, NONE, seen(W, open=W, ids=False),
     Stay(redraw_start=True)),
    # Decision 3: the very device shown comes with another one, or while another one is
    # open: the start page with its tile; the device open stays open.
    ("N0-plug-same-and-other-on-R", Cause.PLUGGED, R_WATCH, NONE, seen(W, E), ShowStart()),
    ("N1-plug-same-on-R-while-open", Cause.PLUGGED, R_WATCH, seen(E, open=E),
     seen(W, E, open=E), ShowStart()),
    # Q1: with a device open, plugging in another does not change the view: the open one
    # stays open, the list is read again (on the start page the tiles of both).
    ("N1-plug-on-S", Cause.PLUGGED, S, seen(W, open=W), seen(W, E, open=W),
     Stay(redraw_start=True)),
    ("N1-plug-on-D", Cause.PLUGGED, D, seen(W, open=W), seen(W, E, open=W), Stay()),
    ("N1-plug-on-R", Cause.PLUGGED, R, seen(W, open=W), seen(W, E, open=W), Stay()),
    ("N2-plug-third-on-R", Cause.PLUGGED, R, seen(W, E), seen(W, E, V),
     Stay(redraw_start=True)),
    ("N1-same-found-on-S", Cause.PLUGGED, S, seen(W, open=W), seen(W, open=W),
     Stay(redraw_start=True)),
    ("N1-same-found-on-D", Cause.PLUGGED, D, seen(W, open=W), seen(W, open=W), Stay()),
    # A detection that finds the devices listed (gvfs reporting their mounts at the start).
    ("N2-listed-again-on-S", Cause.PLUGGED, S, seen(W, E), seen(W, E), Stay(redraw_start=True)),
    ("N2-listed-again-on-R", Cause.PLUGGED, R, seen(W, E), seen(W, E), Stay(redraw_start=True)),
    # --- a device unplugged -----------------------------------------------------------------
    ("N1-unplug-open-on-D", Cause.UNPLUGGED_OPEN, D, seen(W, open=W), NONE, ShowStart()),
    ("N1-unplug-open-on-S", Cause.UNPLUGGED_OPEN, S, seen(W, open=W), NONE,
     Stay(redraw_start=True)),
    ("N1-unplug-open-on-R", Cause.UNPLUGGED_OPEN, R, seen(W, open=W), NONE,
     Stay(redraw_start=True)),
    ("N2-unplug-other-on-D", Cause.UNPLUGGED_OTHER, D, seen(W, E, open=W), seen(W, open=W),
     Stay()),
    ("N2-unplug-other-on-S", Cause.UNPLUGGED_OTHER, S, seen(W, E, open=W), seen(W, open=W),
     Stay(redraw_start=True)),
    ("N2-unplug-other-on-R", Cause.UNPLUGGED_OTHER, R, seen(W, E, open=W), seen(W, open=W),
     Stay()),
    ("U2-S", Cause.UNPLUGGED_CHOICE, S, seen(W, E), seen(W, open=W), ShowDevice()),
    # Q1: on a remembered device's view the one left opens, the view stays.
    ("U2-R", Cause.UNPLUGGED_CHOICE, R, seen(W, E), seen(W, open=W), Stay(redraw_start=True)),
    ("U3-S", Cause.UNPLUGGED_CHOICE, S, seen(W, E, V), seen(W, E), Stay(redraw_start=True)),
    ("N2-usb-changed-on-S", Cause.USB_CHANGED, S, seen(W, E), seen(W, E),
     Stay(redraw_start=True)),
    # --- Refresh, Try Again ----------------------------------------------------------------
    ("N0-refresh-on-S-finds-one", Cause.REFRESH_ON_START, S, NONE, seen(W, open=W),
     Stay(redraw_start=True)),
    ("N0-refresh-on-S-finds-none", Cause.REFRESH_ON_START, S, NONE, NONE,
     Stay(redraw_start=True)),
    ("N1-refresh-in-D", Cause.REFRESH_IN_VIEW, D, seen(W, open=W), seen(W, open=W), Stay()),
    ("N2-refresh-in-D-keeps-chosen", Cause.REFRESH_IN_VIEW, D, seen(W, E, open=E),
     seen(W, E, open=E), Stay()),
    ("N2-pending-refresh-on-S", Cause.REFRESH_IN_VIEW, S, seen(W, E, open=E),
     seen(W, E, open=E), Stay(redraw_start=True)),
    # --- a tile clicked ---------------------------------------------------------------------
    ("N2-chosen", Cause.CHOSEN, S, seen(W, E), seen(W, E, open=E), ShowDevice()),
    ("N2-open-tile", Cause.CHOSEN, S, seen(W, E, open=E), seen(W, E, open=E), ShowDevice()),
    ("N2-chosen-another", Cause.CHOSEN, S, seen(W, E, open=W), seen(W, E, open=E),
     ShowDevice()),
    ("N2-choice-failed", Cause.CHOICE_FAILED, S, seen(W, E), seen(W, E),
     Stay(redraw_start=True)),
    # --- the account ------------------------------------------------------------------------
    ("N1-login-on-D", Cause.ACCOUNT_CHANGED, D, seen(W, open=W), seen(W, open=W), Stay()),
    # Q3: a change of account never moves the user: the start page stays, as after Refresh
    # there.
    ("N1-login-back-on-S", Cause.ACCOUNT_CHANGED, S, seen(W, open=W), seen(W, open=W),
     Stay(redraw_start=True)),
    ("N1-logout-on-D", Cause.ACCOUNT_CHANGED, D, seen(W, open=W), seen(W, open=W), Stay()),
    # Q3: the device open before the change is opened again, also with two plugged in.
    ("N2-logout-on-D", Cause.ACCOUNT_CHANGED, D, seen(W, V, open=W), seen(W, V, open=W),
     Stay()),
    ("N2-login-on-D", Cause.ACCOUNT_CHANGED, D, seen(W, V, open=W), seen(W, V, open=W),
     Stay()),
    ("N1-logged-out-on-S", Cause.LOGGED_OUT, S, seen(W, open=W), seen(W, open=W),
     Stay(redraw_start=True)),
    ("N1-logged-out-on-D", Cause.LOGGED_OUT, D, seen(W, open=W), seen(W, open=W), Stay()),
    ("N1-logged-out-on-R", Cause.LOGGED_OUT, R, seen(W, open=W), seen(W, open=W),
     Stay(redraw_start=True)),
    # The exception to Q3: logged out on the view of a device of the account, which has
    # nothing to show without it: the start page.
    ("N0-logged-out-on-R-account", Cause.LOGGED_OUT, R_ACCOUNT, NONE, NONE, ShowStart()),
    ("N1-logged-out-on-R-account", Cause.LOGGED_OUT, R_ACCOUNT, seen(W, open=W),
     seen(W, open=W), ShowStart()),
    ("N0-login-on-R-account", Cause.ACCOUNT_CHANGED, R_ACCOUNT, NONE, NONE,
     Stay(redraw_start=True)),
]


@pytest.mark.parametrize("cause, visible, before, after, decision",
                         [row[1:] for row in NAVIGATION], ids=[row[0] for row in NAVIGATION])
def test_where_the_user_goes(cause, visible, before, after, decision):
    assert navigate_after(cause, visible, before, after) == decision


def test_the_ids_are_unique_and_the_marked_rows_are_known():
    ids = [row[0] for row in NAVIGATION]
    assert len(ids) == len(set(ids))
    marked = sorted(i for i in ids if re.search(r"-Q\d+$", i))
    assert marked == []


WATCH_LABEL = W + "/Internal Storage/"  # the open watch's storage, below its mount

# (id, cause, reasons, open device's storage, devices listed, whether it moved, the plan)
PLANS = [
    ("P-start", Cause.START, (), None, (), False, Detect()),
    ("P-refresh-on-S-open", Cause.REFRESH_ON_START, (), WATCH_LABEL, (W,), False, ReadList()),
    ("P-refresh-on-S-none", Cause.REFRESH_ON_START, (), None, (), False, Detect()),
    ("P-refresh-in-view", Cause.REFRESH_IN_VIEW, (), WATCH_LABEL, (W,), False,
     Detect()),
    ("P-account", Cause.ACCOUNT_CHANGED, (), None, (), False, Detect()),
    ("P-choice-failed", Cause.CHOICE_FAILED, (), None, (W, E), False, Detect()),
    ("P-unplugged-open", Cause.UNPLUGGED_OPEN, (), None, (), False, Nothing()),
    # The open device unplugged while another Garmin device is connected (audit 22.10.2026,
    # point 1; seen live with 0.6.11): what is left on USB is looked for, a drive as well as
    # a device in its normal mode. The plan knows only what is left, not what was open: an
    # MTP device left after an MTP device or after a drive is the same row.
    ("P-unplugged-open-mtp-a-drive-left", Cause.UNPLUGGED_OPEN, (), None, (), False,
     Detect()),
    ("P-unplugged-open-mtp-another-mtp-left", Cause.UNPLUGGED_OPEN, (), None, (), False,
     Detect()),
    ("P-unplugged-open-drive-an-mtp-left", Cause.UNPLUGGED_OPEN, (), None, (), False,
     Detect()),
    ("P-unplugged-open-nothing-left", Cause.UNPLUGGED_OPEN, (), None, (), False, Nothing()),
    # One still starting: looked for once in its normal mode (the USB poll); a drive ejected
    # by openExpress is neither opened nor listed until it is unplugged (D3).
    ("P-unplugged-open-one-starting-left", Cause.UNPLUGGED_OPEN, (), None, (), False,
     Nothing()),
    ("P-unplugged-open-an-ejected-drive-left", Cause.UNPLUGGED_OPEN, (), None, (), False,
     Nothing()),
    ("P-chosen", Cause.CHOSEN, (), None, (W, E), False, Nothing()),
    ("P-new-mount-none-open", Cause.PLUGGED, (E + "/",), None, (), False, Detect()),
    # Q1: the device open stays open (not opened again), no list: the list is read again.
    ("P-new-mount-while-open", Cause.PLUGGED, (E + "/",), WATCH_LABEL, (W,), False,
     ReadList()),
    ("P-usb-changed-while-open", Cause.USB_CHANGED, (None,), WATCH_LABEL, (W,), False,
     ReadList()),
    ("P-listed-mount-while-open", Cause.PLUGGED, (E + "/",), WATCH_LABEL, (W, E), False,
     Nothing()),
    ("P-own-mount", Cause.PLUGGED, (W + "/",), WATCH_LABEL, (W,), False, CheckOpen()),
    ("P-own-mount-moved", Cause.PLUGGED, (W + "/",), WATCH_LABEL, (W,), True,
     Detect()),
    # Its own mount and another device's: at the same place on USB the list is read again;
    # at another place it is opened again, and the list read after that.
    ("P-own-and-new-mount", Cause.PLUGGED, (W + "/", E + "/"), WATCH_LABEL, (W,), False,
     ReadList()),
    ("P-own-and-new-mount-moved", Cause.PLUGGED, (W + "/", E + "/"), WATCH_LABEL, (W,), True,
     Detect(read_list=True)),
    # Q2: mounts of devices already listed bring no detection, whether one is open or not.
    ("P-start-mounts-listed", Cause.PLUGGED, (W + "/", E + "/"), None, (W, E), False,
     Nothing()),
    ("P-listed-mount-on-the-list", Cause.PLUGGED, (E + "/",), None, (W, E), False, Nothing()),
    # A device listed without its details (e.g. not mounted yet): its mount is looked at.
    ("P-unread-mount-on-the-list", Cause.PLUGGED, (E + "/",), None, (W, E), False,
     Detect()),
    # With a device open too (Q1): the list is read again, its tile may get its details.
    ("P-unread-mount-on-the-list-while-open", Cause.PLUGGED, (E + "/",), WATCH_LABEL, (W, E),
     False, ReadList()),
    # After 1c55104: another device unplugged, its two mounts removed: the list read once.
    ("P-read-the-list", Cause.UNPLUGGED_OTHER, (READ_THE_LIST, READ_THE_LIST), WATCH_LABEL,
     (W, E), False, ReadList()),
    ("P-read-the-list-and-a-listed-mount", Cause.UNPLUGGED_OTHER, (READ_THE_LIST, E + "/"),
     WATCH_LABEL, (W, E), False, ReadList()),
    ("P-read-the-list-the-open-one-gone", Cause.UNPLUGGED_OTHER, (READ_THE_LIST,), None, (W,),
     False, Detect()),
    ("P-usb-changed", Cause.USB_CHANGED, (None,), None, (W, E), False, Detect()),
    ("P-choice-unplugged", Cause.UNPLUGGED_CHOICE, (None,), None, (W, E), False,
     Detect()),
]


# The entries listed without their details, by the id of the row (none in the others).
UNREAD = {"P-unread-mount-on-the-list": (E,), "P-unread-mount-on-the-list-while-open": (E,)}
# What is left on USB once the open device is gone (gio_storage.usb_state()), and whether a
# drive ejected by openExpress waits, by the id of the row (the others: not told).
LEFT_ON_USB = {"P-unplugged-open-nothing-left": ABSENT,
               "P-unplugged-open-mtp-a-drive-left": MASS_STORAGE,
               "P-unplugged-open-mtp-another-mtp-left": PRESENT,
               "P-unplugged-open-drive-an-mtp-left": PRESENT,
               "P-unplugged-open-one-starting-left": STARTING,
               "P-unplugged-open-an-ejected-drive-left": MASS_STORAGE}
EJECTED_WAITS = {"P-unplugged-open-an-ejected-drive-left"}


@pytest.mark.parametrize("row_id, cause, reasons, open_label, listed, moved, expected",
                         PLANS, ids=[row[0] for row in PLANS])
def test_whether_to_look_for_devices(row_id, cause, reasons, open_label, listed, moved,
                                     expected):
    asked = []

    def open_moved():
        asked.append(True)
        return moved

    more = {"unread": frozenset(UNREAD[row_id])} if row_id in UNREAD else {}
    if row_id in LEFT_ON_USB:
        more["left_on_usb"] = LEFT_ON_USB[row_id]
    if row_id in EJECTED_WAITS:
        more["ejected_waits"] = True
    assert plan(cause, reasons, open_label=open_label, listed=frozenset(listed),
                open_moved=open_moved, **more) == expected
    assert len(asked) <= 1


def test_whether_it_moved_is_asked_only_for_its_own_mount():
    """/sys is read only when the open device's own mount decides."""
    def no(*_a):
        raise AssertionError("asked")

    plan(Cause.PLUGGED, (E + "/",), open_label=WATCH_LABEL, listed=frozenset({W}),
         open_moved=no)
    plan(Cause.PLUGGED, (READ_THE_LIST,), open_label=WATCH_LABEL, listed=frozenset({W}),
         open_moved=no)


@pytest.mark.parametrize("cause, visible, before, after, decision",
                         [row[1:] for row in NAVIGATION], ids=[row[0] for row in NAVIGATION])
def test_the_log_line_tells_no_address_and_no_unit_id(cause, visible, before, after, decision):
    line = describe(cause, visible, before, after, decision)
    assert line.startswith(f"navigation: {cause.value}, on the {visible.page.value}, "
                           f"{len(before.connected)} → {len(after.connected)} devices: ")
    for secret in (W, E, V, "0000abcdef", "made-up-user",
                   *(str(u) for u in (WATCH_UNIT, EDGE_UNIT, VARIA_UNIT, VENU_UNIT,
                                      ACCOUNT_UNIT))):
        assert secret not in line


@pytest.mark.parametrize("decision, visible, words", [
    (ShowDevice(), S, "the device view"),
    (ShowStart(), R, "back to the start page"),
    (Stay(redraw_start=True), S, "the start page stays, drawn again"),
    (Stay(redraw_start=True), R, ("the remembered device's view stays, the start page drawn "
                                  "under it")),
    (Stay(), D, "the device view stays"),
])
def test_the_log_line_tells_where_the_user_goes(decision, visible, words):
    line = describe(Cause.UNPLUGGED_OTHER, visible, seen(W, E, open=W), seen(W, open=W),
                    decision)
    assert line == (f"navigation: another device unplugged, on the {visible.page.value}, "
                    f"2 → 1 devices: {words}")


def test_the_unit_id_of_a_remembered_view_is_never_shown():
    assert str(VENU_UNIT) not in repr(R) and str(VENU_UNIT) not in str(R)


def test_the_unit_ids_of_what_was_seen_are_never_shown():
    what = seen(W, E, open=W)
    for unit in (WATCH_UNIT, EDGE_UNIT):
        assert str(unit) not in repr(what) and str(unit) not in str(what)
