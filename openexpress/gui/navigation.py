"""Where the user goes after a detection, decided here in full and nowhere else: whether a
detection is needed at all (plan) and, once it has a result, which page the window shows
(navigate_after). Both are pure: no window, no session, no GTK, nothing read from USB or
asked of a server; addresses are compared in memory only and never written to the log,
nor is a unit ID. `before` is what the user saw (the devices on the start page and the
device open when the detection started), not what is known now; `after` is what the
detection found. The table in tests/test_navigation.py is the source of truth: each row
is one situation of the window, with today's outcome, and the rows marked with a decision
(Q1-Q3 in the notes) are those that are to change."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import Enum

from openexpress.device.gio_storage import MASS_STORAGE, PRESENT

# A trigger that asks only for the list of devices connected to be read again (another
# device than the open one went away); never an address. The same as the window's.
READ_THE_LIST = "read the list of devices again"


class Cause(Enum):
    """Why the window looks for devices (or why it decides where the user goes)."""
    START = "window start"
    PLUGGED = "device mounted"
    UNPLUGGED_OPEN = "open device unplugged"
    UNPLUGGED_OTHER = "another device unplugged"
    UNPLUGGED_CHOICE = "a device on the list unplugged"
    USB_CHANGED = "USB changed"
    REFRESH_ON_START = "refresh on the start page"
    REFRESH_IN_VIEW = "refresh in the device view"
    CHOSEN = "tile chosen"
    CHOICE_FAILED = "the device chosen could not be opened"
    ACCOUNT_CHANGED = "account changed"
    LOGGED_OUT = "logged out"  # a change of account, after which the account tells nothing


class Page(Enum):
    START = "start page"
    DEVICE = "device view"
    REMEMBERED = "remembered device's view"


@dataclass(frozen=True)
class View:
    """The page shown; for the read-only view of a device not connected, which one it is
    (by its unit ID: compared in memory only, never shown or written) and whether it shows
    what the account says of it (`from_account`), not what this computer remembers."""
    page: Page
    shown_unit_id: int | None = field(default=None, repr=False)
    from_account: bool = False


@dataclass(frozen=True)
class Seen:
    """Devices by their addresses: those connected, and the one open (or None); and by their
    unit IDs (compared in memory only, never shown or written): those connected that have
    one, and the open one's (None: none open, or it has none)."""
    connected: frozenset[str] = frozenset()
    open: str | None = None
    units: frozenset[int] = field(default=frozenset(), repr=False)
    open_unit: int | None = field(default=None, repr=False)


# --- where the user goes: what the window does with it ---------------------------------------

@dataclass(frozen=True)
class Stay:
    """The page shown stays; `redraw_start`: the start page is drawn again (shown, or under
    the view of a remembered device)."""
    redraw_start: bool = False


@dataclass(frozen=True)
class ShowDevice:
    """The view of the device open, above the start page (the first time without an
    animation)."""
    animate: bool = True


@dataclass(frozen=True)
class ShowStart:
    """Back to the start page."""


Decision = Stay | ShowDevice | ShowStart


# --- whether to look for devices --------------------------------------------------------------

# `ignored` (each plan): how many mounts of devices already listed were left aside, and
# `moved` (Detect): the open device is at another place on USB - for the log only, never
# part of what the plan is.

@dataclass(frozen=True)
class Nothing:
    """No detection: nothing new."""
    ignored: int = field(default=0, compare=False)


@dataclass(frozen=True)
class ReadList:
    """Only the list of devices connected is read again; the open device stays."""
    ignored: int = field(default=0, compare=False)


@dataclass(frozen=True)
class Detect:
    """A detection (the device open now is opened again when it is still there, among
    several); `read_list`: the list of devices connected is read again after it (another
    device came as well)."""
    read_list: bool = False
    ignored: int = field(default=0, compare=False)
    moved: bool = field(default=False, compare=False)


@dataclass(frozen=True)
class CheckOpen:
    """Only the open device's own mount was reported: whether it still answers (a
    detection only if it does not)."""
    ignored: int = field(default=0, compare=False)


Plan = Nothing | ReadList | Detect | CheckOpen

# Causes that come by the window's timer, with the triggers so far (`reasons`).
_TIMED = (Cause.PLUGGED, Cause.UNPLUGGED_OTHER, Cause.UNPLUGGED_CHOICE, Cause.USB_CHANGED)
# Causes whose detection runs with the device open still open: if it is found again, the
# page shown stays (the window's "reopening"). Those of the timer, and Refresh and the
# like; the others drop the open device first, or open the one chosen.
_REOPENING = (*_TIMED, Cause.REFRESH_IN_VIEW)


def plan(cause: Cause, reasons: Sequence[str | None] = (), *, open_label: str | None = None,
         listed: frozenset[str] = frozenset(), unread: frozenset[str] = frozenset(),
         open_moved: Callable[[], bool] = lambda: False, left_on_usb: str | None = None,
         ejected_waits: bool = False) -> Plan:
    """Whether to look for devices, and how. `reasons`: the triggers of the timer (the
    address of a mount reported, None for a change on USB, READ_THE_LIST); `open_label`:
    the address of the open device's storage (None: none open); `listed`: the addresses of
    the devices connected, and `unread`: those of them listed without their details (e.g.
    not mounted yet), all without the trailing slash; `open_moved`: whether the open device
    is no longer at its place on USB (asked only when it matters). A device open stays open
    (Q1): another device mounted or seen on USB only has the list read again.
    `left_on_usb`, for the open device unplugged: what is on USB once it is gone (the state of
    gio_storage.usb_state()), and `ejected_waits`: whether a drive ejected by openExpress is
    still there."""
    if cause not in _TIMED:
        if cause is Cause.UNPLUGGED_OPEN:
            # Another Garmin device left is looked for (D7): one in its normal mode, and a
            # drive, which sends no event as it is mounted already; not one still starting
            # (looked for once in its normal mode), nor a drive ejected by openExpress (D3).
            if left_on_usb == PRESENT or (left_on_usb == MASS_STORAGE and not ejected_waits):
                return Detect()
            return Nothing()
        if cause is Cause.CHOSEN:
            return Nothing()  # no detection: the one chosen is opened
        if cause is Cause.REFRESH_ON_START and open_label is not None:
            return ReadList()  # the list (and the account's) read again, nothing opened
        return Detect()

    def on_open(r: str) -> bool:
        return open_label is not None and open_label.startswith(r.rstrip("/") + "/")

    read_the_list = READ_THE_LIST in reasons
    rest = [r for r in reasons if r != READ_THE_LIST]
    if open_label is not None and read_the_list and not rest:
        return ReadList()
    # Mounts of devices already listed with their details bring nothing new (Q2), whether
    # one is open or not: gvfs reports them as the window starts, and twice for one device.
    # One listed without them (not mounted yet, not readable) is looked at: its tile may
    # get its details now.
    skipped = listed - unread
    known = [r for r in rest if r is not None and not on_open(r) and r.rstrip("/") in skipped]
    rest = [r for r in rest if r not in known]
    ignored = len(known)
    # (The list to read again with no device open any more: the open one went away too.)
    if known and not rest and not (read_the_list and open_label is None):
        return ReadList(ignored=ignored) if read_the_list else Nothing(ignored=ignored)
    if open_label is None:
        return Detect(ignored=ignored)
    # Another device mounted, or seen on USB (a trigger without an address): the open one
    # stays open, the list is read again (Q1).
    others = [r for r in rest if r is None or not on_open(r)]
    if len(others) < len(rest) and open_moved():
        # Its own mount too, at another place on USB (the same address): opened again,
        # then the list read again if another device came as well.
        return Detect(read_list=bool(others), ignored=ignored, moved=True)
    if others or not rest:
        return ReadList(ignored=ignored)
    return CheckOpen(ignored=ignored)


def navigate_after(cause: Cause, visible: View, before: Seen, after: Seen) -> Decision:
    """Where the user goes once the window knows what is connected (`after`), from the
    page shown (`visible`) and what the user saw (`before`)."""
    if cause is Cause.UNPLUGGED_OTHER:
        # Only the list was read again: the start page is drawn if shown, nothing moves.
        return Stay(redraw_start=visible.page is Page.START)
    if visible.page is Page.REMEMBERED:
        # Q1: other devices coming or going move nothing; the start page is drawn under the
        # view (one device opened in the background, or the list). The very device shown
        # (by its unit ID; one without is never it) brings its view, opened alone, or the
        # start page with its tile (with another device, or while another one is open).
        if cause is Cause.LOGGED_OUT and visible.from_account:
            return ShowStart()  # without the account the view has nothing to show (Q3)
        shown = visible.shown_unit_id
        if shown is not None and shown == after.open_unit:
            return ShowDevice()
        if shown is not None and shown in after.units:
            return ShowStart()
        if after.open is not None and before.open is not None and cause in _REOPENING:
            return Stay()  # the device open stays open: only the list read again
        return Stay(redraw_start=True)
    if after.open is None:
        # The list to choose from, or no device at all.
        if visible.page is Page.START:
            return Stay(redraw_start=True)
        return ShowStart()  # the device shown is gone
    if visible.page is Page.DEVICE:
        return Stay()
    # Refresh on the start page and a change of account (Q3) never move the user.
    stays = cause in (Cause.REFRESH_ON_START, Cause.ACCOUNT_CHANGED, Cause.LOGGED_OUT) or (
        cause in _REOPENING and before.open is not None)
    if stays:
        return Stay(redraw_start=visible.page is Page.START)
    return ShowDevice(animate=cause is not Cause.START)


def describe(cause: Cause, visible: View, before: Seen, after: Seen,
             decision: Decision) -> str:
    """The line of the log for a decision: its cause, the page shown, how many devices
    before and after, and where the user goes. Never an address or a unit ID."""
    if isinstance(decision, ShowDevice):
        outcome = "the device view"
    elif isinstance(decision, ShowStart):
        outcome = "back to the start page"
    elif decision.redraw_start and visible.page is Page.START:
        outcome = "the start page stays, drawn again"
    elif decision.redraw_start:
        outcome = f"the {visible.page.value} stays, the start page drawn under it"
    else:
        outcome = f"the {visible.page.value} stays"
    return (f"navigation: {cause.value}, on the {visible.page.value}, "
            f"{len(before.connected)} → {len(after.connected)} devices: {outcome}")
