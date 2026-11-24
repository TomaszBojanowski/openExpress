"""The background sync: turning it on and off, and the watcher with one device and with several."""

import logging
import types

import pytest

from openexpress.device.storage import DeviceError


class SyncThread:
    def __init__(self, target, args=(), daemon=None):
        self.target, self.args = target, args

    def start(self):
        self.target(*self.args)


def test_set_enabled_reports_errors(monkeypatch):
    from openexpress import background

    monkeypatch.setattr(background, "in_flatpak", lambda: False)
    monkeypatch.setattr(background.threading, "Thread", SyncThread)
    monkeypatch.setattr(background.GLib, "idle_add", lambda f, *a: f(*a))

    def boom(**kw):
        raise OSError("read-only")

    monkeypatch.setattr(background.desktop, "install", boom)
    results = []
    background.set_enabled(True, lambda ok, err: results.append((ok, err)))
    assert results == [(False, "read-only")]

    def no_systemctl(*a, **kw):
        raise FileNotFoundError("systemctl")

    monkeypatch.setattr(background.subprocess, "run", no_systemctl)
    background.set_enabled(False, lambda ok, err: results.append((ok, err)))
    assert results[-1][0] is False and "systemctl" in results[-1][1]
    assert background.is_enabled() is False


def test_watcher_pending_and_deferred_quit(monkeypatch):
    from openexpress import watch

    monkeypatch.setattr(watch.threading, "Thread", SyncThread)
    monkeypatch.setattr(watch.GLib, "idle_add", lambda f, *a: f(*a))
    w = watch.Watcher()
    released = []
    monkeypatch.setattr(w, "release", lambda: released.append(True))
    runs = []

    def fake_sync():
        runs.append(1)
        if len(runs) == 1:
            w.start_sync()  # re-plugged during the sync

    monkeypatch.setattr(w, "_sync", fake_sync)
    w.start_sync()
    assert len(runs) == 2 and not w.running

    runs.clear()

    def quit_during():
        runs.append(1)
        w.start_sync()
        w.request_quit()
        assert not released  # deferred while syncing

    monkeypatch.setattr(w, "_sync", quit_during)
    w.start_sync()
    assert runs == [1] and released == [True]


def test_watcher_retries_open_session(monkeypatch):
    from openexpress import watch

    calls = []
    session = types.SimpleNamespace(device=None, storage=None)  # a made-up session

    def open_session():
        calls.append(1)
        if len(calls) < 3:
            raise DeviceError("not ready")
        return session

    monkeypatch.setattr(watch.service, "open_session", open_session)
    w = watch.Watcher()
    sleeps = []
    assert w._open_session(sleep=sleeps.append) is session
    assert sleeps == [5, 5]
    calls.clear()
    monkeypatch.setattr(watch.service, "open_session",
                        lambda: (_ for _ in ()).throw(DeviceError("gone")))
    with pytest.raises(DeviceError):
        w._open_session(sleep=sleeps.append)


def test_watcher_does_nothing_with_several_devices(monkeypatch, caplog):
    """With several devices connected the background service asks once, does not
    try again, syncs nothing and shows no notification; one line in the log says why. Only
    a device chosen in the window is synced."""
    from openexpress import watch
    from openexpress.device.gio_storage import SeveralDevices

    opened, shown = [], []

    def open_session():
        opened.append(1)
        raise SeveralDevices("Several devices connected, choose one with --device-uri: …")

    monkeypatch.setattr(watch.service, "open_session", open_session)
    monkeypatch.setattr(watch.service, "run_sync",
                        lambda *a, **kw: pytest.fail("nothing may be synced"))
    monkeypatch.setattr(watch.GLib, "idle_add", lambda fn, *args: shown.append(args))
    w = watch.Watcher()
    with caplog.at_level(logging.INFO, logger="openexpress.watch"):
        w._sync()
    assert opened == [1] and shown == []  # asked once: no waiting, no retry
    assert [r.getMessage() for r in caplog.records] == [
        "Not syncing: several devices are connected"]


def test_watcher_stops_when_a_second_device_comes_while_it_waits(monkeypatch, caplog):
    """The first try finds one device that is not ready; by the next one a second
    device is connected (SeveralDevices): no more tries, nothing synced, no notification,
    one line in the log."""
    from openexpress import watch
    from openexpress.device.gio_storage import SeveralDevices

    opened, shown = [], []

    def open_session():
        opened.append(1)
        if len(opened) == 1:
            raise DeviceError("not ready")  # one device, still on its way
        raise SeveralDevices("Several devices connected, choose one with --device-uri: …")

    monkeypatch.setattr(watch, "OPEN_RETRY_DELAY_S", 0)
    monkeypatch.setattr(watch.service, "open_session", open_session)
    monkeypatch.setattr(watch.service, "run_sync",
                        lambda *a, **kw: pytest.fail("nothing may be synced"))
    monkeypatch.setattr(watch.GLib, "idle_add", lambda fn, *args: shown.append(args))
    w = watch.Watcher()
    with caplog.at_level(logging.INFO, logger="openexpress.watch"):
        w._sync()
    assert opened == [1, 1] and shown == []
    assert [r.getMessage() for r in caplog.records] == [
        "Watch not ready, retrying in 0 s", "Not syncing: several devices are connected"]
