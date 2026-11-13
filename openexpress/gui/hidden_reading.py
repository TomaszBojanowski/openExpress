"""The window's reading of the folders of the device's top level that ordinary MTP does not show
(a car navigator's .System) through openExpress's own USB session (hidden_folders): once a
connection, right after the device was opened, when what its first checks found (its updates,
its maps) goes to such a folder; again only when the user asks (Check Again, Refresh). The
device leaves the file manager for a moment, so the reading is a task: never during another
one, and after the diagnostics of the opening are read.

Has: nothing; the device's session keeps the folders wanted and whether they are being read
(DeviceSession.hidden_wanted, hidden_reading)."""

import logging

from gi.repository import GLib

from openexpress import hidden_folders
from openexpress.gui.window_tasks import count_again

log = logging.getLogger(__name__)

# How long the reading waits before it is tried again while another task runs.
RETRY_MS = 700


def note_needed(win, s, folders: list[str]) -> None:
    """A first check of the open device `s` (updates, maps) found what goes to `folders`, which
    ordinary MTP does not show: they are read once neither first check runs any more, unless
    this connection was read already."""
    if win.session is not s or not (folders or _wanted(s)):
        return
    for folder in folders:
        s.hidden_wanted.setdefault(folder.casefold(), folder)
    _start(win, s)


def read_again(win) -> bool:
    """Check Again: the folders this connection needs read anew, then the pages read before
    read again (_shown_again). False when it needs none: the caller reads its page itself."""
    s = win.session
    if s is None or not _wanted(s):
        return False
    hidden_folders.forget(s.storage)
    _start(win, s)
    return True


def _wanted(s) -> dict:
    """The folders `s` wants read; none for a session without the field (one made up for a
    page alone)."""
    return getattr(s, "hidden_wanted", None) or {}


def _start(win, s) -> None:
    if (win.session is not s or not s.hidden_wanted or s.hidden_reading
            or hidden_folders.kept(s.storage) is not None):
        return
    if win._update_check_session is s or win._map_check_session is s:
        return  # the other first check may need a folder too: it ends with note_needed
    if win.busy:
        def retry() -> bool:
            _start(win, s)
            return False

        GLib.timeout_add(RETRY_MS, retry)
        return
    folders = sorted(s.hidden_wanted.values(), key=str.casefold)
    s.hidden_reading = True

    def work():
        return hidden_folders.read(s.storage, folders, "updates or maps go there",
                                   progress=win.progress)

    work.leaves_the_file_manager = True

    def done(_listing) -> None:
        s.hidden_reading = False
        if win.session is not s:
            log.info("hidden folders: read after the device was closed, not shown")
            return
        _shown_again(win, s)

    def failed(_e) -> bool:
        s.hidden_reading = False
        return win.session is not s  # the error of a device unplugged meanwhile is not shown

    win.run_task(hidden_folders.reading_text(folders), work, done, failed, device=False)


def _shown_again(win, s) -> None:
    """After the reading: the rows of the device page checked again and the pages read before
    read again, with what was read (Garmin's answers are kept: nothing is asked anew), and the
    device counted again for Device Status."""
    win._check_updates_quietly()
    if win.map_row is not None:
        win._check_maps_quietly()
    for key in ("updates", "maps"):
        section = win.sections.get(key)
        if section is not None and getattr(section, "loaded", False):
            win.load_section(key, force=True)
    count_again(win, s)
