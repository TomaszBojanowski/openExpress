"""Made-up asynchronous Gio operations that answer as Gio does: once, always (also when
cancelled), in the main context that was the default of the thread when the operation began.
Imported by the tests as `from gio_fake import …`."""

import threading

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

WHEN = ("at once", "next turn", "before", "on cancel", "never")


def answer_as_gio(callback, delay_s: float = 0.0, context=None):
    """Call `callback()` as Gio gives an answer: from the main context that is the default of
    this thread now (that of the thread that began the operation), or from `context`, the one
    kept when it began. Returns the source."""
    source = GLib.idle_source_new() if delay_s <= 0 else GLib.timeout_source_new(
        max(1, round(delay_s * 1000)))
    source.set_callback(lambda *_a: callback() and False)
    source.attach(context or GLib.MainContext.ref_thread_default())
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


class GvfsDevice:
    """An MTP device in gvfs as gio_storage sees it, for an unmount that goes on after its
    cancel, as gvfs lets it. Its unmount and its mount answer through
    D-Bus: a cancel ends the wait at once ("cancelled"), while gvfs goes on and the unmount
    takes effect `unmount_takes_s` after it began, unless `cancel_stops` (gvfs honoured the
    cancel). The volume monitor (`get_mounts`) shows a change once the main thread has had
    its signal (an idle of the global default context, as in a program); the files (`exists`)
    are as they are in gvfs. `comes_back=False`: mounted again, its files stay unseen.
    `events`: "unmounted", "mounted", in order."""

    def __init__(self, uri, unmount_takes_s=0.2, cancel_stops=False, comes_back=True):
        self.uri, self.unmount_takes_s = uri, unmount_takes_s
        self.cancel_stops, self.comes_back = cancel_stops, comes_back
        self.mounted = self.shown = self.files_seen = True
        self.events: list[str] = []
        self.timers: list[threading.Timer] = []
        self._lock = threading.Lock()
        self.mount = _GvfsMount(self)
        self.location = _GvfsLocation(self)

    def _become(self, mounted: bool, files_seen: bool = True) -> None:
        with self._lock:
            self.mounted, self.files_seen = mounted, files_seen
            self.events.append("mounted" if mounted else "unmounted")
        GLib.idle_add(lambda: setattr(self, "shown", mounted) or False)  # the monitor's signal

    # the volume monitor
    def get_mounts(self):
        return [self.mount] if self.shown else []

    def get_volumes(self):
        return []

    # the storage of the device
    def exists(self, path):
        return self.mounted and self.files_seen

    def stop(self):
        for timer in self.timers:
            timer.cancel()
            timer.join(2)


class _GvfsMount:
    def __init__(self, device):
        self.device = device

    def get_root(self):
        uri = self.device.uri
        return type("Root", (), {"get_uri": staticmethod(lambda: uri)})()

    def unmount_with_operation(self, flags, operation, cancellable, callback):
        device, context = self.device, GLib.MainContext.ref_thread_default()
        said = {"answered": False, "cancelled": False}
        lock = threading.Lock()

        def answer(word):
            with lock:
                if said["answered"]:
                    return  # the reply of gvfs to a wait that has ended
                said["answered"] = True
            answer_as_gio(lambda: callback(self, word), context=context)

        def unmounted():  # gvfs, in its own time
            if not (device.cancel_stops and said["cancelled"]):
                device._become(False)
            answer("done")

        timer = threading.Timer(device.unmount_takes_s, unmounted)
        timer.daemon = True
        device.timers.append(timer)
        timer.start()
        if cancellable is not None:
            cancellable.connect(lambda *_a: (said.update(cancelled=True), answer("cancelled")))

    def unmount_with_operation_finish(self, word):
        if word != "done":
            raise gio_error(word)
        return True


class _GvfsLocation:
    def __init__(self, device):
        self.device = device

    def mount_enclosing_volume(self, flags, operation, cancellable, callback):
        device = self.device
        if device.mounted:
            word = "already mounted"
        else:
            device._become(True, files_seen=device.comes_back)
            word = "done"
        answer_as_gio(lambda: callback(self, word))

    def mount_enclosing_volume_finish(self, word):
        if word != "done":
            raise gio_error(word)
        return True


class GioOf:
    """gio_storage's Gio with the file of the device and its volume monitor made up."""

    def __init__(self, gio, device):
        self._gio, self._device = gio, device
        self.File = type("File", (), {"new_for_uri": staticmethod(lambda uri: device.location)})
        self.VolumeMonitor = type("VolumeMonitor", (), {"get": staticmethod(lambda: device)})

    def __getattr__(self, name):
        return getattr(self._gio, name)
