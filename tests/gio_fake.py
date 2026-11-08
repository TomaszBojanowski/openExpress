"""Made-up asynchronous Gio operations that answer as Gio does: once, always (also when
cancelled), in the main context that was the default of the thread when the operation began.
Imported by the tests as `from gio_fake import …`."""

import posixpath
import threading
import time

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
              "already mounted": (Gio.IOErrorEnum.ALREADY_MOUNTED, "Location is already mounted"),
              "not found": (Gio.IOErrorEnum.NOT_FOUND, "No such file or directory")}


def gio_error(word: str, message: str = "made-up refusal") -> GLib.Error:
    """The GLib.Error of an answer: "cancelled", "already mounted", "not found", or any other
    word: a refusal with `message`."""
    code, text = GIO_ERRORS.get(word, (Gio.IOErrorEnum.FAILED, message))
    return GLib.Error.new_literal(Gio.io_error_quark(), text, code)


class GioOp:
    """One operation; `start` is what gio_storage._wait begins it with.

    when: "at once" (within start; Gio never does), "next turn", "before" (`delay_s` after
    start), "on cancel" (`delay_s` after the cancel), "never" (the cancel ignored).
    says: "done", "error" (`message`), "already mounted" or "not found"; `on_cancel`: "done"
    (too late), "cancelled" or "error". `strict`: fails without a Gio.Cancellable.
    Leaves `delivered` (word, thread name)."""

    def __init__(self, when="next turn", says="done", delay_s=0.0, on_cancel="cancelled",
                 message="made-up refusal", strict=False):
        assert when in WHEN
        self.when, self.says, self.delay_s = when, says, delay_s
        self.on_cancel, self.message, self.strict = on_cancel, message, strict
        self.delivered = None
        self.cancellable = None
        self.was_cancelled = False
        self.sources = []
        self._lock = threading.Lock()

    def start(self, done, cancellable=None, context=None):
        """Begins it; its answer comes in `context`, or in the thread's default main context
        now when None."""
        if self.strict and cancellable is None:
            raise AssertionError("the operation was started without a way to cancel it")
        self.cancellable = cancellable
        if self.when == "at once":
            self._give(done, self.says)
            return
        if self.when in ("next turn", "before"):
            delay = 0.0 if self.when == "next turn" else self.delay_s
            self.sources.append(answer_as_gio(lambda: self._give(done, self.says), delay,
                                              context))
        if cancellable is not None:
            context = context or GLib.MainContext.ref_thread_default()
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


class Volume:
    """A volume not mounted, whose mount is the made-up operation."""

    def __init__(self, op):
        self.op = op

    def mount(self, flags, operation, cancellable, callback):
        self.op.start(lambda finish: callback(self, finish), cancellable)

    def mount_finish(self, finish):
        return finish()


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
    """An MTP device in gvfs as gio_storage sees it. A cancel answers the unmount at once
    ("cancelled"), but gvfs goes on: the unmount takes effect `unmount_takes_s` after it began,
    unless `cancel_stops`. The volume monitor (`get_mounts`) shows a change once the main
    thread has had its signal; `exists` follows gvfs at once. `comes_back=False`: mounted
    again, its files stay unseen. Leaves `events`."""

    usb = None  # its place on USB not known

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


# --- the files of an MTP device, read through gvfs -------------------------------------------

MTIME = 1_600_000_000  # the time of every made-up file


class MtpFiles:
    """The files of a device over MTP as gvfs gives them to GioStorage, asynchronously: each
    call answers once (also when cancelled), in the main context that was the thread's default
    when it began. A listing gives its entries in portions of the size asked for, in the
    context where the listing began: gvfs hands them to its enumerator there, so a portion
    waited for in another context never comes.

    A file is read as a stream, in chunks of the size asked for, at most `most` bytes an
    answer when given (gvfs reads at most 256 KiB at a time).

    `tree`: real path -> None for a folder, else a file: its bytes, or its size (then zeros);
    "" is the root and the folders on the way need no entry. `space`: the filesystem
    attributes of the storage. `plan`: (kind, n) -> the GioOp the n-th call of a kind (from 1)
    answers as, instead of at the next turn; kinds: "list" (opening a listing), "portion",
    "close", "query", "space", "open" (opening a file), "chunk", "close file". Leaves `calls`,
    the kinds in the order called, and `asked`, the bytes asked for by each chunk."""

    def __init__(self, tree, space=None, plan=None, most=None):
        self.tree = dict(tree)
        self.space = dict(space or {})
        self.plan = dict(plan or {})
        self.most = most
        self.calls: list[str] = []
        self.asked: list[int] = []
        self._ops: list[GioOp] = []

    def file(self, path=""):
        return _MtpFile(self, path)

    def is_folder(self, path):
        return path == "" or (path in self.tree and self.tree[path] is None) or any(
            key.startswith(path + "/") for key in self.tree)

    def there(self, path):
        return self.is_folder(path) or path in self.tree

    def contents(self, path):
        value = self.tree[path]
        return value if isinstance(value, bytes) else bytes(value)

    def info(self, path):
        info = Gio.FileInfo.new()
        info.set_name(posixpath.basename(path))
        folder = self.is_folder(path)
        info.set_file_type(Gio.FileType.DIRECTORY if folder else Gio.FileType.REGULAR)
        info.set_size(0 if folder else len(self.contents(path)))
        info.set_modification_date_time(GLib.DateTime.new_from_unix_utc(MTIME))
        return info

    def children(self, path):
        prefix = path + "/" if path else ""
        names = sorted({key[len(prefix):].split("/")[0] for key in self.tree
                        if key.startswith(prefix) and key != path})
        return [self.info(prefix + name) for name in names]

    def answer(self, kind, missing=False):
        """The made-up operation of the next call of `kind`."""
        self.calls.append(kind)
        op = self.plan.get((kind, self.calls.count(kind))) or GioOp(
            says="not found" if missing else "done")
        self._ops.append(op)
        return op

    def clean(self):
        for op in self._ops:
            op.clean()


def _finished(result):
    finish, value = result
    finish()
    return value


class _MtpFile:
    """A Gio.File of the device: what GioStorage asks of one."""

    def __init__(self, files, path):
        self.files, self.path = files, path

    def get_uri(self):
        return "mtp://Made_Up_Device/Internal/" + self.path

    def resolve_relative_path(self, relative):
        return _MtpFile(self.files, posixpath.join(self.path, relative) if self.path
                        else relative)

    def enumerate_children_async(self, attributes, flags, priority, cancellable, callback):
        files = self.files
        folder = files.is_folder(self.path)
        op = files.answer("list", missing=not folder)
        listing = _MtpListing(files, files.children(self.path) if folder else [],
                              GLib.MainContext.ref_thread_default())
        op.start(lambda finish: callback(self, (finish, listing)), cancellable)

    def query_info_async(self, attributes, flags, priority, cancellable, callback):
        files = self.files
        there = files.there(self.path)
        op = files.answer("query", missing=not there)
        info = files.info(self.path) if there else None
        op.start(lambda finish: callback(self, (finish, info)), cancellable)

    def read_async(self, priority, cancellable, callback):
        files = self.files
        a_file = files.there(self.path) and not files.is_folder(self.path)
        op = files.answer("open", missing=not a_file)
        stream = _MtpStream(files, files.contents(self.path) if a_file else b"")
        op.start(lambda finish: callback(self, (finish, stream)), cancellable)

    def query_filesystem_info_async(self, attributes, priority, cancellable, callback):
        op = self.files.answer("space")
        info = Gio.FileInfo.new()
        for name, value in self.files.space.items():
            if name in attributes.split(","):
                info.set_attribute_uint64(name, value)
        op.start(lambda finish: callback(self, (finish, info)), cancellable)

    enumerate_children_finish = query_info_finish = query_filesystem_info_finish = (
        read_finish) = staticmethod(_finished)


class _MtpStream:
    """A file opened for reading (a Gio.FileInputStream of gvfs)."""

    def __init__(self, files, data):
        self.files, self.data, self.at = files, data, 0

    def read_bytes_async(self, count, priority, cancellable, callback):
        files = self.files
        files.asked.append(count)
        op = files.answer("chunk")
        op.start(lambda finish: callback(self, (finish, self._take(count))), cancellable)

    def _take(self, count):
        if self.files.most is not None:
            count = min(count, self.files.most)
        chunk = self.data[self.at:self.at + count]
        self.at += len(chunk)
        return GLib.Bytes.new(chunk)

    def close_async(self, priority, cancellable, callback):
        op = self.files.answer("close file")
        op.start(lambda finish: callback(self, (finish, True)), cancellable)

    read_bytes_finish = close_finish = staticmethod(_finished)


class _MtpListing:
    """A listing under way (a Gio.FileEnumerator of gvfs): its portions come in `context`,
    where it began."""

    def __init__(self, files, infos, context):
        self.files, self.left, self.context = files, list(infos), context

    def next_files_async(self, count, priority, cancellable, callback):
        op = self.files.answer("portion")
        op.start(lambda finish: callback(self, (finish, self._take(count))), cancellable,
                 context=self.context)

    def _take(self, count):
        portion, self.left = self.left[:count], self.left[count:]
        return portion

    def close_async(self, priority, cancellable, callback):
        op = self.files.answer("close")
        op.start(lambda finish: callback(self, (finish, True)), cancellable)

    next_files_finish = close_finish = staticmethod(_finished)


# --- a worker thread, as the window's tasks ---------------------------------------------------


def in_a_worker(fn, name="worker"):
    said = {}

    def run():
        try:
            said["value"] = fn()
        except BaseException as e:  # noqa: BLE001 - told to the test
            said["error"] = e
        said["thread default"] = GLib.MainContext.get_thread_default()

    worker = threading.Thread(target=run, name=name, daemon=True)
    worker.start()
    return worker, said


def settle(*threads, limit_s=10.0):
    """The main thread turns the global default context until the threads have ended, so that
    a worker that wrongly waits on that context, for an answer only this thread can give it,
    still ends: the test then fails on what it checks instead of hanging."""
    context = GLib.MainContext.default()
    deadline = time.monotonic() + limit_s
    while any(t.is_alive() for t in threads) and time.monotonic() < deadline:
        context.iteration(False)
        time.sleep(0.01)
