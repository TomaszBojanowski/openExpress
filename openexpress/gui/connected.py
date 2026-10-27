"""The Garmin devices known to be plugged in, for the window: one list (`devices`, the
entries of the detection, each with its address in `found.uri`) instead of the window's list
to choose from and its list of the devices connected while one is open. `choosing` is a flag
of its own and not the length of that list: once a device is chosen, or its opening failed,
there is no list to choose from any more, yet the devices connected are still known (the
mounts of those already listed bring nothing new, the start page draws their tiles).
`usb_when_listed` is what was on USB when the list was made, so that a device plugged in or
out without a mount event is noticed. `drawn` is what the start page last showed, and may be
older than `devices` (they are read again without drawing while another page is shown): it
tells whether the user saw the devices connected now. Nothing here opens, mounts or asks
anything; addresses are compared in memory only."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field


def uris(summaries) -> frozenset[str]:
    """The addresses of these entries, without the trailing slash."""
    return frozenset(c.found.uri.rstrip("/") for c in summaries)


def unit_ids(summaries) -> frozenset[int]:
    """The unit IDs of these entries (compared in memory only), without those that have
    none."""
    return frozenset(u for c in summaries if (u := getattr(c, "unit_id", None)))


def open_address(s) -> str | None:
    """The address of the open device (compared in memory only), or None."""
    if s is None:
        return None
    return str(getattr(getattr(s, "storage", None), "device_uri", "") or "")


def open_unit(s) -> int | None:
    """The unit ID of the open device (compared in memory only, never shown), or None."""
    return getattr(getattr(s, "device", None), "unit_id", None) or None


@dataclass
class Connected:
    devices: list = field(default_factory=list)
    choosing: bool = False
    usb_when_listed: list[str] | None = None
    drawn: frozenset[str] = frozenset()

    def listing(self, summaries, usb: list[str] | None) -> None:
        """Several devices found and none opened: the list to choose from, and what was on
        USB then."""
        self.devices = list(summaries)
        self.choosing = True
        self.usb_when_listed = usb

    def stop_choosing(self) -> None:
        """A device was chosen (opened, or its opening failed), or one device was found: no
        list to choose from; the devices connected stay known."""
        self.choosing = False

    def found_alone(self) -> None:
        """A detection found one device connected and opened it: no other device is known
        (its tile comes from the session), and there is no list to choose from."""
        self.devices = []
        self.choosing = False

    def clear(self) -> None:
        """No device open and none to choose from. What was on USB when the list was made
        stays (it is read only while choosing)."""
        self.devices = []
        self.choosing = False

    def opened_in_place(self, found, summary) -> None:
        """The device of `found`, chosen from the list without its details (a drive mounted
        once chosen: neither its unit ID nor the address it has now were known), is open:
        `summary`, the open one, takes the place of its entry, so that it is one of the
        devices connected (keep_if_open) and the others stay."""
        self.devices = [summary if c.found is found or c.found.uri == found.uri else c
                        for c in self.devices]

    def keep_if_open(self, is_open: Callable[[object], bool]) -> None:
        """A device was opened: the devices connected stay known if the open one is among
        them (chosen from the list); else the one device connected is the open one (D7), and
        its tile comes from the session."""
        if not any(is_open(c) for c in self.devices):
            self.devices = []

    def reread(self, found) -> None:
        """The devices connected, read again while one is open."""
        self.devices = list(found)

    def listed(self, uri: str) -> bool:
        """Whether this address is one of the devices connected."""
        return uri.rstrip("/") in uris(self.devices)

    def unread(self) -> frozenset[str]:
        """The addresses of the devices connected whose details were not read (e.g. not
        mounted yet), without the trailing slash."""
        return uris(c for c in self.devices if not c.ok)

    def is_choice(self, uri: str) -> bool:
        """Whether this address is on the list to choose from (only while there is one)."""
        return self.choosing and self.listed(uri)

    def usb_changed(self, now: list[str]) -> bool:
        """Whether the Garmin devices on USB changed since the list was made (or since the
        last change); the new state is kept."""
        if now == self.usb_when_listed:
            return False
        self.usb_when_listed = now
        return True

    def for_start(self, open_summary=None, is_open: Callable[[object], bool] | None = None
                  ) -> tuple[list, bool]:
        """The devices for the start page and whether the notice of the USB state goes with
        them. No device open (`open_summary` None): the list to choose from, or none and the
        notice. One open: the devices connected, with the open one (`open_summary`) first
        when it is not among them (`is_open`)."""
        if open_summary is None:
            shown = self.devices if self.choosing else []
            return shown, not shown
        if is_open is not None and any(is_open(c) for c in self.devices):
            return self.devices, False
        return [open_summary, *self.devices], False

    def drew(self, summaries) -> None:
        """The start page drew the tiles of these devices."""
        self.drawn = uris(summaries)

    def changed_since_drawn(self, summaries) -> bool:
        """Whether these devices differ from those the start page drew last."""
        return uris(summaries) != self.drawn
