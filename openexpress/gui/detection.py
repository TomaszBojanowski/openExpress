"""The detection of the devices of the window and where the user goes after it. Today the
helpers of navigation that the start page and the detection share: what the window knows
after a detection (seen_open), why a decision is made (_take_asked), the page shown
(visible_view) and the decision with its line of the log (decided). Stage 10 of analysis 17
adds the detection itself here.

Has: _asked and _pending_cause, taken by _take_asked.
Uses: navigation, start, remembered (the pages), _remembered_unit,
_remembered_from_account."""
from __future__ import annotations

import logging

from openexpress.gui.connected import open_address, open_unit, unit_ids, uris
from openexpress.gui.navigation import Cause, Page, Seen, View, describe, navigate_after

# The window's logger: the lines of navigation stay where they were before the split.
log = logging.getLogger("openexpress.gui.app")


def seen_open(s, connected) -> Seen:
    """What the window knows after a detection or a read of the list: the devices connected
    (`connected`, entries) with the open one among them."""
    address, unit = open_address(s), open_unit(s)
    return Seen(uris(connected) | ({address.rstrip("/")} if address else set()), address,
                unit_ids(connected) | ({unit} if unit else set()), unit)


def _take_asked(win, own: Cause) -> tuple[Cause, str | None]:
    """Why the decision made now is made and the device open when it was asked: those of
    the detection that ends here (if one), else `own`. A Refresh on the start page asked
    while a task ran keeps the start page for what that task brings as well, once."""
    asked = getattr(win, "_asked", None)
    win._asked = None
    cause, open_before = asked if asked is not None else (own, None)
    if getattr(win, "_pending_cause", None) is Cause.REFRESH_ON_START:
        cause, win._pending_cause = Cause.REFRESH_ON_START, Cause.REFRESH_IN_VIEW
    return cause, open_before


def decided(win, cause: Cause, before: Seen, after: Seen):
    """Where the user goes (navigation.navigate_after), with one line of the log."""
    view = visible_view(win)
    decision = navigate_after(cause, view, before, after)
    log.info("%s", describe(cause, view, before, after, decision))
    return decision


def visible_view(win) -> View:
    """The page shown, for navigation (the remembered device by its unit ID, in memory)."""
    page = win.navigation.get_visible_page()
    if page is win.start.page:
        return View(Page.START)
    if page is win.remembered.page:
        return View(Page.REMEMBERED, getattr(win, "_remembered_unit", None),
                    getattr(win, "_remembered_from_account", False))
    return View(Page.DEVICE)
