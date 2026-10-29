"""An unmount for a PTP session going on after its cancel (seen on an Edge 1050, time limit
1 ms): gvfs ends it a moment later, saying "mounted already" until then. The device is mounted
again once seen unmounted, else the error says to unplug it; on the command line and in the
window. The device is made up (gio_fake.GvfsDevice)."""

import os
import random
import threading
import time

import pytest
from gio_fake import GioOf, GvfsDevice

gio_storage = pytest.importorskip("openexpress.device.gio_storage")
GLib = gio_storage.GLib

URI = "mtp://Made_Up_Device/"
NOT_IN_TIME = "Cannot unmount the device: the device did not answer in time"
NOT_BACK = ("The device did not come back after an unmount that did not finish in time. "
            "Unplug it and plug it in again.")
SETTLED = 0.2  # SETTLED_S of these tests: the unmount takes effect well within it


@pytest.fixture
def gvfs(monkeypatch):
    """A made-up device in gvfs, as gio_storage sees it, with times of a fraction of a
    second (the unmount gets 1 ms)."""
    made = []
    for name, value in (("UNMOUNT_TIMEOUT_S", 0.001), ("MOUNT_TIMEOUT_S", 1.0),
                        ("CANCEL_WAIT_S", 0.2), ("SETTLED_S", SETTLED),
                        ("SETTLE_POLL_S", 0.01), ("SETTLE_LIMIT_S", 2.0),
                        ("REMOUNT_READY_S", 1)):
        monkeypatch.setattr(gio_storage, name, value, raising=False)
    monkeypatch.setattr(gio_storage, "_quiet_until", {})
    monkeypatch.setattr(gio_storage, "_quiet_usb", {})
    real_gio = gio_storage.Gio

    def make(**kw):
        device = GvfsDevice(URI, **kw)
        monkeypatch.setattr(gio_storage, "Gio", GioOf(real_gio, device))
        made.append(device)
        return device

    yield make
    for device in made:
        device.stop()
    turn_default(0.05)  # the signals of the monitor still due


def turn_default(seconds: float) -> None:
    """The main thread turns the global default context (the signals of the monitor)."""
    context = GLib.MainContext.default()
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        context.iteration(False)
        time.sleep(0.005)


def session(device):
    """A PTP session of `device`, as an app install takes it: what it ended with."""
    try:
        with gio_storage._exclusive(URI, 3, 14, device):
            return AssertionError("the session ran")
    except BaseException as e:  # noqa: BLE001 - told to the test
        return e


def as_the_window(work):
    """`work` in a worker thread while the main thread holds the global default context and
    turns it, as the window does."""
    said = {}
    worker = threading.Thread(target=lambda: said.update(value=work()), name="worker",
                              daemon=True)
    context = GLib.MainContext.default()
    assert context.acquire()
    try:
        worker.start()
        deadline = time.monotonic() + 20
        while worker.is_alive() and time.monotonic() < deadline:
            context.iteration(False)
            time.sleep(0.005)
    finally:
        context.release()
    assert not worker.is_alive(), "the session did not end"
    return said["value"]


def run_in(path, work):
    return work() if path == "cli" else as_the_window(work)


@pytest.mark.parametrize("path", ["cli", "window"])
def test_an_unmount_that_goes_on_after_its_cancel_leaves_the_device_mounted(gvfs, path):
    device = gvfs(unmount_takes_s=0.05)
    ended = run_in(path, lambda: session(device))
    assert isinstance(ended, gio_storage.GioTimedOut) and str(ended) == NOT_IN_TIME
    turn_default(SETTLED)  # the device stays as it was left
    assert device.events == ["unmounted", "mounted"]
    assert device.mounted and device.shown and device.files_seen


@pytest.mark.parametrize("path", ["cli", "window"])
def test_an_unmount_stopped_by_its_cancel_leaves_the_device_as_it_was(gvfs, path):
    """gvfs honoured the cancel: the device stays mounted, and nothing mounts it again."""
    device = gvfs(unmount_takes_s=0.05, cancel_stops=True)
    ended = run_in(path, lambda: session(device))
    assert str(ended) == NOT_IN_TIME
    turn_default(SETTLED)
    assert device.events == [] and device.mounted and device.shown


@pytest.mark.parametrize("path", ["cli", "window"])
def test_a_device_that_does_not_come_back_says_so(gvfs, path):
    """Mounted again, its files stay unseen: after the tries the error says what to do."""
    device = gvfs(unmount_takes_s=0.05, comes_back=False)
    ended = run_in(path, lambda: session(device))
    assert isinstance(ended, gio_storage.RemountFailed) and str(ended) == NOT_BACK
    assert ended.says_replug


def test_ctrl_c_during_an_unmount_that_goes_on_leaves_the_device_mounted(gvfs, monkeypatch):
    """Ctrl+C on the command line while gvfs unmounts: it goes on, and the device is
    mounted again once it is seen unmounted; Ctrl+C stays Ctrl+C."""
    device = gvfs(unmount_takes_s=0.05)

    def interrupted(uri):
        device.mount.unmount_with_operation(None, None, gio_storage.Gio.Cancellable(),
                                            lambda *_a: None)  # gvfs has it
        raise KeyboardInterrupt

    monkeypatch.setattr(gio_storage, "_unmount", interrupted)
    ended = session(device)
    assert isinstance(ended, KeyboardInterrupt)
    turn_default(SETTLED)
    assert device.events == ["unmounted", "mounted"] and device.mounted and device.shown


# --- under load -----------------------------------------------------------------------------


def race_under_load(gvfs, seed: int, runs: int) -> list[str]:
    """`runs` sessions, the command line and the window in turn, each with a device whose
    unmount takes effect at a random moment after the cancel (well within SETTLED), honours the
    cancel now and then, and once in a while does not come back. What was wrong."""
    rng = random.Random(seed)
    wrong = []
    before = threading.active_count()
    for number in range(runs):
        path = ("cli", "window")[number % 2]
        stops, back = rng.random() < 0.2, rng.random() < 0.9
        device = gvfs(unmount_takes_s=rng.uniform(0.005, SETTLED * 0.4), cancel_stops=stops,
                      comes_back=back)
        started = time.monotonic()
        ended = run_in(path, lambda device=device: session(device))
        took = time.monotonic() - started
        turn_default(SETTLED)
        said = f"run {number} ({path}, cancel honoured: {stops}, comes back: {back})"
        if not stops and not back:
            if not (isinstance(ended, gio_storage.DeviceNotBack) and str(ended) == NOT_BACK):
                wrong.append(f"{said}: ended with {ended!r}")
        else:
            if not (type(ended) is gio_storage.GioTimedOut and str(ended) == NOT_IN_TIME):
                wrong.append(f"{said}: ended with {ended!r}")
            if not (device.mounted and device.shown and device.files_seen):
                wrong.append(f"{said}: left without a mount ({device.events})")
            expected = [] if stops else ["unmounted", "mounted"]
            if device.events != expected:
                wrong.append(f"{said}: {device.events}")
        if took > 10:
            wrong.append(f"{said}: took {took:.1f} s")
        device.stop()
    if threading.active_count() != before:
        wrong.append(f"{threading.active_count() - before} thread(s) left")
    return wrong


def test_unmounts_that_go_on_under_load(gvfs):
    assert race_under_load(gvfs, 2026, runs=8) == []


@pytest.mark.stress
def test_unmounts_that_go_on_under_long_load(gvfs):
    seed = int(os.environ.get("OPENEXPRESS_STRESS_SEED", 2026))
    assert race_under_load(gvfs, seed, runs=100) == []
