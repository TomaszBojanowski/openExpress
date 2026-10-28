"""Made-up asynchronous Gio operations that answer as Gio does: once, always (also when
cancelled), in the main context that was the default of the thread when the operation began
(step 4 after the audit of 0.7.9, A8). Imported by the tests as `from gio_fake import …`."""

import threading

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

WHEN = ("at once", "next turn", "before", "on cancel", "never")


def answer_as_gio(callback, delay_s: float = 0.0):
    """Call `callback()` as Gio gives an answer: from the main context that is the default of
    this thread now (that of the thread that began the operation). Returns the source."""
    source = GLib.idle_source_new() if delay_s <= 0 else GLib.timeout_source_new(
        max(1, round(delay_s * 1000)))
    source.set_callback(lambda *_a: callback() and False)
    source.attach(GLib.MainContext.ref_thread_default())
    return source


GIO_ERRORS = {"cancelled": (Gio.IOErrorEnum.CANCELLED, "Operation was cancelled"),
              "already mounted": (Gio.IOErrorEnum.ALREADY_MOUNTED, "Location is already mounted")}


def gio_error(word: str, message: str = "made-up refusal") -> GLib.Error:
    """The GLib.Error of an answer: "cancelled", "already mounted", or any other word: a
    refusal with `message`."""
    code, text = GIO_ERRORS.get(word, (Gio.IOErrorEnum.FAILED, message))
    return GLib.Error.new_literal(Gio.io_error_quark(), text, code)


class GioOp:
    """One operation; `start` is what gio_storage._wait begins it with.

    when: "at once" (within start: Gio never does that, but _wait copes), "next turn",
    "before" (`delay_s` after it began), "on cancel" (only once cancelled, `delay_s` after the
    cancel), "never" (the cancel ignored).
    says: "done", "error" (`message`) or "already mounted"; `on_cancel`: "done" (too late to
    cancel), "cancelled" or "error".
    `strict`: an operation begun without a Gio.Cancellable fails at once.

    `delivered` is what it really gave, once its answer ran: (word, name of the thread), or
    None; `began_in` the thread that began it."""

    def __init__(self, when="next turn", says="done", delay_s=0.0, on_cancel="cancelled",
                 message="made-up refusal", strict=False):
        assert when in WHEN
        self.when, self.says, self.delay_s = when, says, delay_s
        self.on_cancel, self.message, self.strict = on_cancel, message, strict
        self.delivered = None
        self.began_in = None
        self.cancellable = None
        self.was_cancelled = False
        self.sources = []
        self._lock = threading.Lock()

    def start(self, done, cancellable=None):
        if self.strict and cancellable is None:
            raise AssertionError("the operation was started without a way to cancel it")
        self.began_in = threading.current_thread()
        self.cancellable = cancellable
        if self.when == "at once":
            self._give(done, self.says)
            return
        if self.when in ("next turn", "before"):
            delay = 0.0 if self.when == "next turn" else self.delay_s
            self.sources.append(answer_as_gio(lambda: self._give(done, self.says), delay))
        if cancellable is not None:
            context = GLib.MainContext.ref_thread_default()
            # g_cancellable_connect (PyGObject gives it no arguments); at once when
            # already cancelled
            cancellable.connect(lambda *_a: self._cancelled(done, context))

    def _cancelled(self, done, context):
        self.was_cancelled = True
        if self.when != "on cancel":
            return  # "never": ignored; "next turn", "before": its answer is on its way
        source = GLib.timeout_source_new(max(1, round(self.delay_s * 1000)))
        source.set_callback(lambda *_a: self._give(done, self.on_cancel) and False)
        source.attach(context)
        self.sources.append(source)

    def _give(self, done, word):
        with self._lock:
            if self.delivered is not None:
                return False
            self.delivered = (word, threading.current_thread().name)

        def finish():
            if word != "done":
                raise gio_error(word, self.message)
            return True

        done(finish)
        return False

    def clean(self):
        """Its answers that never ran (the waiting gave up on it) are taken away."""
        for source in self.sources:
            source.destroy()


class WindowCallbacks:
    """Callbacks of the window due on the global default context (`GLib.idle_add`,
    `GLib.timeout_add`): each notes the thread it ran in (`ran`). Those that have not run are
    taken away by `remove_left`, by their IDs, never looked up: a source destroyed meanwhile in
    another thread must not be looked for (GLib: a programmer error)."""

    def __init__(self):
        self.ran: list[str] = []
        self._ids: dict[int, bool] = {}
        self._lock = threading.Lock()

    def add(self, delays_ms):
        for delay in delays_ms:
            box = {}

            def note(box=box):
                with self._lock:
                    self._ids[box["id"]] = True
                    self.ran.append(threading.current_thread().name)
                return False

            with self._lock:  # its ID is known before it can run
                box["id"] = (GLib.idle_add(note) if delay <= 0
                             else GLib.timeout_add(delay, note))
                self._ids[box["id"]] = False

    def remove_left(self):
        with self._lock:
            left = [source_id for source_id, ran in self._ids.items() if not ran]
            for source_id in left:
                self._ids[source_id] = True
        for source_id in left:
            GLib.source_remove(source_id)
