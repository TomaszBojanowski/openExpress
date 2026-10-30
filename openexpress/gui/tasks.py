"""What the tasks of the window's pages share (no GTK here, so that every page module can
import it without importing gui/app.py): the mark of a work that writes to the device, that
of a work the window may close during, the failure handler that reads the page again, and
what Refresh asks Garmin's servers anew."""
import functools
import logging

from openexpress import answers

log = logging.getLogger(__name__)


def forget_on_refresh(session) -> None:
    """Refresh (the button, F5, Try Again) asks Garmin's servers anew: the account's devices,
    the primary device, the account's country, and the software and maps of the device open
    (answers)."""
    answers.ANSWERS.forget((answers.ACCOUNT_DEVICES, answers.PRIMARY_TRAINING,
                            answers.ACCOUNT_COUNTRY))
    device = getattr(session, "device", None)
    answers.forget_device((answers.SOFTWARE_UPDATES, answers.UNIVERSAL_MAPS),
                          getattr(device, "unit_id", None))


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
    """Marks the work of a task that writes nothing to the device (a backup reads the device
    and writes on this computer only): the window may close during it, nothing on the device
    can be left half-written. Its progress goes through stop_when_closed, so that it
    stops once the window is closed (a task's thread keeps the process until it ends)."""
    @functools.wraps(fn)
    def work(*args, **kwargs):
        return fn(*args, **kwargs)

    work.lets_the_window_close = True
    return work


def stop_when_closed(win, progress):
    """`progress` of a work that lets the window close: once the window is closed, its next
    step raises WindowClosed instead, with one line in the log."""
    def told(*args):
        if win._closed:
            log.info("task: stopped, the window was closed")
            raise WindowClosed
        progress(*args)

    return told


def read_again_after_failure(win, key: str, failed=None):
    """Returns the failure handler of a task that changes page `key` (maps, apps, music): it
    reads the page again, since a part may be done. `failed(e)`: the caller's handler, run
    first; its answer is returned (false: run_task shows the error) and its words stay over
    the page. Not when the session changed meanwhile (unplugged, another device opened)."""
    started = win.session

    def handler(e) -> bool:
        said = bool(failed is not None and failed(e))
        if started is not None and win.session is started:
            # As when the task went well: the files of a map without its unlock file, half an
            # own map that could not be deleted, songs copied in part, an app list that waited
            # and is gone.
            win.load_section(key, force=True)
        return said

    return handler
