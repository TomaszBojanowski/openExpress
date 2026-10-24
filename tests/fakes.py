"""Fakes shared by the tests (imported as `from fakes import …`, like ptp_fake). Only made-up
data; nothing here reaches a device, the network or the disk of this computer."""

import threading
from collections import namedtuple

Call = namedtuple("Call", "title fn done failed device in_page button")


class RunNow:
    """run_task of the window (gui/app.py), run at once: the work on this thread (or, with
    `thread=True`, in another, waited for), then `done(result)` outside any try, as the
    window calls it; an error of the work goes to `failed(error)`, and one it does not take
    to `error`: "raise" (the default: an error nobody expected fails the test), "record"
    (its words in `errors`) or a function (title, words), as the window's `error`. A
    `button` spins during the work and is back before done or failed, as in the window; a
    work marked writes_to_device is noted in `written` (the window reads the free space
    again then). Every call is kept in `calls` (`titles` for their titles); `on_call(call)`
    runs before the work and `before_done()` between it and done or failed. Unlike the
    window: no check of `busy`, no bar, no pending refresh afterwards."""

    def __init__(self, error="raise", thread=False, on_call=None, before_done=None):
        self.error, self.thread = error, thread
        self.on_call, self.before_done = on_call, before_done
        self.calls: list[Call] = []
        self.errors: list[str] = []
        self.written: list[str] = []

    @property
    def titles(self) -> list[str]:
        return [call.title for call in self.calls]

    def __call__(self, title, fn, done=None, failed=None, device=True, in_page=False,
                 button=None):
        call = Call(title, fn, done, failed, device, in_page, button)
        self.calls.append(call)
        if self.on_call is not None:
            self.on_call(call)
        restore = None
        if button is not None:
            from openexpress.gui.app import show_working

            restore = show_working(button)
        result, raised = [], []

        def work():
            try:
                result.append(fn())
            except Exception as e:  # noqa: BLE001 - as the window's run_task: to `failed`
                raised.append(e)

        if self.thread:
            worker = threading.Thread(target=work)
            worker.start()
            worker.join()
        else:
            work()
        if self.before_done is not None:
            self.before_done()
        if restore is not None:
            restore(True)
        try:
            if raised:
                self._failed(title, raised[0], failed)
            elif done is not None:
                done(result[0])
        finally:
            if getattr(fn, "writes_to_device", False):
                self.written.append(title)

    def _failed(self, title, error, failed) -> None:
        if failed is not None and failed(error):
            return
        if self.error == "raise":
            raise error
        words = str(error) or type(error).__name__
        if self.error == "record":
            self.errors.append(words)
        else:
            self.error(title, words)
