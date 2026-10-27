"""What the tasks of the window's pages share (no GTK here, so that every page module can
import it without importing gui/app.py): the mark of a work that writes to the device, that
of a work the window may close during, and the failure handler that reads the page
again."""
import functools
import logging

log = logging.getLogger("openexpress.gui.app")


def writes_to_device(fn):
    """The work of a task that writes to the device: once run_task has run it (also when it
    failed: a part may be written), the free space of the device is read again."""
    @functools.wraps(fn)
    def work(*args, **kwargs):
        return fn(*args, **kwargs)

    work.writes_to_device = True
    return work


class WindowClosed(Exception):
    """The window was closed during a task that lets it close: its work stopped."""


def lets_the_window_close(fn):
    """The work of a task that writes nothing to the device (a backup: it reads the device
    and writes on this computer only): the window may close during it, as nothing on the
    device can be left half-written. Its progress goes through stop_when_closed, so that the
    work stops once the window is closed (the thread of a task keeps the process until it
    ends)."""
    @functools.wraps(fn)
    def work(*args, **kwargs):
        return fn(*args, **kwargs)

    work.lets_the_window_close = True
    return work


def stop_when_closed(win, progress):
    """`progress` of a work that lets the window close: once the window is closed, its next
    step raises WindowClosed instead, with one line in the log."""
    def told(*args):
        if getattr(win, "_closed", False):
            log.info("task: stopped, the window was closed")
            raise WindowClosed
        progress(*args)

    return told


def read_again_after_failure(win, key: str, failed=None):
    """The failure handler of a task that changes what page `key` (maps, apps, music) shows:
    the page is read again, as when the task went well, since a part of it may be done (the
    files of a map without its unlock file, half an own map that could not be deleted, songs
    copied in part, an app list that waited and is gone). `failed` says the error as before
    (else run_task does); its words stay over the page as it is read. Not when the device
    was unplugged, or another one opened, meanwhile: the window reads what is open on its
    own."""
    started = win.session

    def handler(e) -> bool:
        said = bool(failed is not None and failed(e))
        if started is not None and win.session is started:
            win.load_section(key, force=True)
        return said

    return handler
