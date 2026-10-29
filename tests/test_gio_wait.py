"""Waiting for one asynchronous Gio operation (gio_storage._wait).

A worker thread waits on a context of its own; at the time limit the operation is cancelled
(else it goes on in gvfs) and its answer waited for. The command line (the main thread) waits
on the global default context, where Ctrl+C wakes it. All operations are made up
(gio_fake.py)."""

import logging
import threading
import time
import types
from contextlib import contextmanager

import pytest
from gio_fake import GioOp, WindowCallbacks

gio_storage = pytest.importorskip("openexpress.device.gio_storage")
GLib = gio_storage.GLib

LIMIT = 0.2
GRACE = 0.2
URI = "mtp://Made_Up_Device/"
LOGGER = "openexpress.device.gio_storage"
NOT_IN_TIME = "Cannot mount the device: the device did not answer in time"


@pytest.fixture
def fast(monkeypatch):
    """Time limits of a fraction of a second."""
    for name in ("MOUNT_TIMEOUT_S", "UNMOUNT_TIMEOUT_S", "EJECT_TIMEOUT_S"):
        monkeypatch.setattr(gio_storage, name, LIMIT, raising=False)
    monkeypatch.setattr(gio_storage, "CANCEL_WAIT_S", GRACE, raising=False)


class Volume:
    """A volume not mounted, whose mount is the made-up operation."""

    def __init__(self, op):
        self.op = op

    def mount(self, flags, operation, cancellable, callback):
        self.op.start(lambda finish: callback(self, finish), cancellable)

    def mount_finish(self, finish):
        return finish()


class Mount:
    """A mounted device, whose unmount and eject are the made-up operation."""

    def __init__(self, op, ejectable=True):
        self.op, self.ejectable = op, ejectable

    def get_root(self):
        return types.SimpleNamespace(get_uri=lambda: URI)

    def can_eject(self):
        return self.ejectable

    def unmount_with_operation(self, flags, operation, cancellable, callback):
        self.op.start(lambda finish: callback(self, finish), cancellable)

    eject_with_operation = unmount_with_operation

    def unmount_with_operation_finish(self, finish):
        return finish()

    eject_with_operation_finish = unmount_with_operation_finish


class Location:
    """A location to mount again, whose mount is the made-up operation."""

    def __init__(self, op):
        self.op = op

    def mount_enclosing_volume(self, flags, operation, cancellable, callback):
        self.op.start(lambda finish: callback(self, finish), cancellable)

    def mount_enclosing_volume_finish(self, finish):
        return finish()


class GioWith:
    """gio_storage's Gio, with File.new_for_uri giving `location`."""

    def __init__(self, gio, location):
        self._gio = gio
        self.File = types.SimpleNamespace(new_for_uri=lambda uri: location)

    def __getattr__(self, name):
        return getattr(self._gio, name)


def _found(op):
    return gio_storage.FoundDevice(URI, "made-up", False, Volume(op))


def _mounted(monkeypatch, mount):
    monkeypatch.setattr(gio_storage.Gio.VolumeMonitor, "get", staticmethod(
        lambda: types.SimpleNamespace(get_mounts=lambda: [mount], get_volumes=list)))


class Visible:
    """A storage whose GarminDevice.xml is there."""

    def exists(self, path):
        return True


def _operation(kind, op, monkeypatch):
    """The call of gio_storage that runs the made-up operation `op`."""
    if kind == "mount":
        return lambda: gio_storage.mount(_found(op))
    if kind == "unmount":
        _mounted(monkeypatch, Mount(op))
        return lambda: gio_storage._unmount(URI)
    if kind == "mount again":
        monkeypatch.setattr(gio_storage, "Gio", GioWith(gio_storage.Gio, Location(op)))
        return lambda: gio_storage._remount(URI, Visible(), sleep=lambda s: None)
    _mounted(monkeypatch, Mount(op))
    drive = types.SimpleNamespace(device_uri=URI, usb=None, usb_id=None)
    return lambda: gio_storage.eject(drive)


WHAT = {"mount": "Cannot mount the device", "unmount": "Cannot unmount the device",
        "mount again": "Cannot mount the device", "eject": "Cannot eject the device"}


@contextmanager
def window_thread_busy():
    """The main thread holds the global default context and does not turn it, as the window's
    thread busy with something (Gio.Application.run holds it all the time)."""
    context = GLib.MainContext.default()
    assert context.acquire()
    try:
        yield
    finally:
        context.release()


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
    """The main thread turns the global default context until the threads have ended (on the
    old code a worker waited there for an answer only this thread could give it)."""
    context = GLib.MainContext.default()
    deadline = time.monotonic() + limit_s
    while any(t.is_alive() for t in threads) and time.monotonic() < deadline:
        context.iteration(False)
        time.sleep(0.01)


# --- the context ----------------------------------------------------------------------------


def test_a_wait_in_a_worker_does_not_need_the_window_thread(fast):
    op = GioOp("before", delay_s=0.1)
    dev = _found(op)
    with window_thread_busy():
        worker, said = in_a_worker(lambda: gio_storage.mount(dev))
        worker.join(3)
        finished = not worker.is_alive()
    settle(worker)
    assert finished, "the worker waited for the window's thread"
    assert "error" not in said and dev.mounted
    assert op.delivered == ("done", "worker")  # the answer came to the thread that waited
    assert said["thread default"] is None  # its own context taken off again


def test_and_its_time_limit_fires_without_the_window_thread(fast):
    op = GioOp("never")
    dev = _found(op)
    started = time.monotonic()
    with window_thread_busy():
        worker, said = in_a_worker(lambda: gio_storage.mount(dev, timeout_s=LIMIT))
        worker.join(3)
        finished = not worker.is_alive()
    settle(worker)
    assert finished, "the time limit of the worker waited for the window's thread"
    assert time.monotonic() - started < LIMIT + GRACE + 2
    assert str(said["error"]) == NOT_IN_TIME and not dev.mounted


def test_a_worker_wait_runs_no_callback_of_the_window(fast):
    """The loop of the window has ended (nobody holds the global default context): a worker
    that waits must not run the window's callbacks that are still due."""
    window = WindowCallbacks()
    window.add([0, 20, 40, 60])
    try:
        op = GioOp("before", delay_s=0.3)
        worker, said = in_a_worker(lambda: gio_storage.mount(_found(op)))
        worker.join(3)
        in_the_worker = [name for name in window.ran if name == "worker"]
        settle(worker)
        assert "error" not in said
        assert in_the_worker == [], "the window's callbacks ran in the worker"
        for _turn in range(50):  # still due: they run in the main thread
            GLib.MainContext.default().iteration(False)
            time.sleep(0.005)
        assert set(window.ran) <= {threading.main_thread().name}
    finally:
        window.remove_left()


def test_the_wait_of_the_main_thread_turns_the_default_context(fast):
    """The command line waits in its main thread on the global default context: there
    PyGObject wakes it on Ctrl+C, and there the volume monitor gets its signals."""
    window = WindowCallbacks()
    window.add([0])
    try:
        gio_storage.mount(_found(GioOp("before", delay_s=0.1)))
        assert window.ran == [threading.main_thread().name]
    finally:
        window.remove_left()


# --- the time limit and the cancel ----------------------------------------------------------


@pytest.mark.parametrize("kind", ["mount", "unmount", "mount again", "eject"])
def test_a_time_limit_cancels_the_operation_and_waits_for_its_answer(kind, fast, monkeypatch,
                                                                     caplog):
    op = GioOp("on cancel", on_cancel="cancelled", delay_s=0.05, strict=True)
    run = _operation(kind, op, monkeypatch)
    with caplog.at_level(logging.WARNING, logger=LOGGER), \
            pytest.raises(gio_storage.DeviceError) as error:
        run()
    assert str(error.value) == f"{WHAT[kind]}: the device did not answer in time"
    timed_out = error.value.__cause__ if kind == "mount again" else error.value
    assert isinstance(timed_out, gio_storage.GioTimedOut)
    assert op.was_cancelled and op.cancellable.is_cancelled()
    assert op.delivered == ("cancelled", threading.current_thread().name)  # waited for
    # Which answer came after the cancel: "cancelled" only ends our wait, gvfs may go on and
    # unmount the device later.
    assert (f"gio: {kind}: no answer in {LIMIT:g} s, cancelled; its answer: cancelled (our "
            "wait ended, gvfs may go on)") in [r.getMessage() for r in caplog.records]


def test_an_answer_after_the_cancel_that_says_done_is_a_success(fast, caplog):
    """Too late to cancel: it was done, and that is what the program goes on with."""
    dev = _found(GioOp("on cancel", on_cancel="done", delay_s=0.05, strict=True))
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        gio_storage.mount(dev)
    assert dev.mounted
    (said,) = [r.getMessage() for r in caplog.records]
    assert said.startswith(f"gio: mount: no answer in {LIMIT:g} s, cancelled; it ended all "
                           "the same after ")


def test_an_error_after_the_cancel_says_what_happened(fast, caplog):
    dev = _found(GioOp("on cancel", on_cancel="error", delay_s=0.05, strict=True))
    with caplog.at_level(logging.WARNING, logger=LOGGER), \
            pytest.raises(gio_storage.GioTimedOut) as error:
        gio_storage.mount(dev)
    assert str(error.value) == "Cannot mount the device: made-up refusal"
    assert not dev.mounted
    assert [r.getMessage() for r in caplog.records] == [
        f"gio: mount: no answer in {LIMIT:g} s, cancelled; its answer: failed"]


def test_no_answer_even_after_the_cancel_gives_up(fast, caplog):
    op = GioOp("never", strict=True)
    started = time.monotonic()
    with caplog.at_level(logging.WARNING, logger=LOGGER), \
            pytest.raises(gio_storage.GioTimedOut) as error:
        gio_storage.mount(_found(op))
    took = time.monotonic() - started
    assert str(error.value) == NOT_IN_TIME
    assert LIMIT + GRACE - 0.05 <= took < LIMIT + GRACE + 1
    assert op.was_cancelled and op.delivered is None
    assert [r.getMessage() for r in caplog.records] == [(
        f"gio: mount: no answer in {LIMIT:g} s, cancelled; no answer {GRACE:g} s after the "
        "cancel either, given up")]


def test_an_answer_given_at_once_needs_no_wait(monkeypatch):
    """Gio never answers within the call that begins an operation, but a wait must not stand
    still to its time limit if something did."""
    monkeypatch.setattr(gio_storage, "MOUNT_TIMEOUT_S", 5.0, raising=False)
    dev = _found(GioOp("at once"))
    started = time.monotonic()
    gio_storage.mount(dev, timeout_s=5)
    assert dev.mounted and time.monotonic() - started < 1


def test_an_answer_before_the_limit_is_as_it_was(fast, caplog):
    with caplog.at_level(logging.DEBUG, logger=LOGGER), \
            pytest.raises(gio_storage.DeviceError) as error:
        gio_storage.mount(_found(GioOp("before", says="error", delay_s=0.05, strict=True)))
    assert str(error.value) == "Cannot mount the device: made-up refusal"
    assert not isinstance(error.value, gio_storage.GioTimedOut)
    (said,) = [r.getMessage() for r in caplog.records]
    assert said.startswith("gio: mount answered in ") and said.endswith(
        " (main loop of this thread)")


def test_mount_says_its_time_limit_as_every_wait(fast):
    """Mounting waits as every other operation, with the same words at its time limit."""
    with pytest.raises(gio_storage.DeviceError) as error:
        gio_storage.mount(_found(GioOp("never")), timeout_s=LIMIT)
    assert str(error.value) == NOT_IN_TIME


def test_no_source_of_the_wait_is_left_in_the_default_context(fast, monkeypatch):
    """The wait of a worker puts nothing in the global default context."""
    added = []
    real = gio_storage.GLib

    class Counting:
        def __getattr__(self, name):
            value = getattr(real, name)
            if name in ("timeout_add", "timeout_add_seconds", "idle_add"):
                return lambda *a, **kw: added.append(value(*a, **kw)) or added[-1]
            return value

    monkeypatch.setattr(gio_storage, "GLib", Counting())
    worker, said = in_a_worker(lambda: gio_storage.mount(_found(GioOp("before", delay_s=0.05))))
    worker.join(3)
    settle(worker)
    assert "error" not in said and added == []


def test_mounting_again_a_device_already_mounted_is_fine(monkeypatch, caplog):
    """After a PTP session, or after an unmount that did not end, the device may be mounted
    already: that is what mounting it again is for, not a failure."""
    op = GioOp("next turn", says="already mounted")
    monkeypatch.setattr(gio_storage, "Gio", GioWith(gio_storage.Gio, Location(op)))
    with caplog.at_level(logging.INFO, logger=LOGGER):
        gio_storage._remount(URI, Visible(), sleep=lambda s: None)
    assert op.delivered[0] == "already mounted"
    assert "PTP: the device is mounted already" in [r.getMessage() for r in caplog.records]
