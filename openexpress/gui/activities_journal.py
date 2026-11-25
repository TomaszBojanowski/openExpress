"""The activity journal in the window (activity_journal.py keeps it): brought up to date in the
background once a device is opened.
Has: nothing of its own; whether the open device's journal was read is in its session
(DeviceSession.journal_read)."""
from __future__ import annotations

import logging

from openexpress import activity_journal

log = logging.getLogger(__name__)


def read_journal(win) -> None:
    """The open device's activity journal brought up to date in the background, once a session
    (after its diagnostics are read: device_page.read_diagnostics), when it is kept: after the
    user's yes, never for a device on another Garmin Connect account. Before the yes and after
    a no nothing starts. The page does not change with it."""
    s = win.session
    if s is None or not activity_journal.kept() or s.journal_read:
        return
    if activity_journal.on_another_account(s.ownership_error):
        return
    s.journal_read = True

    def work():
        return activity_journal.at_connection(s.device, s.storage, s.ownership_error)

    win.in_background(work, lambda _did: None, lambda _error: None)
