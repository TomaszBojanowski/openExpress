"""The tests see no device of the computer running them (review of 24.10.2026, area 6): a
watch plugged in while they run is not looked for on its USB bus, not listed by the session's
volume monitor, and the windows the tests make stop listening when their test ends."""

from pathlib import Path

import pytest

REAL_SYSFS_USB = Path("/sys/bus/usb/devices")


def test_the_usb_devices_of_this_computer_are_not_seen():
    from openexpress.device import gio_storage

    assert gio_storage.SYSFS_USB != REAL_SYSFS_USB
    assert gio_storage.SYSFS_USB.is_dir() and list(gio_storage.SYSFS_USB.iterdir()) == []
    assert gio_storage._garmin_on_usb() == []
    assert gio_storage.usb_state() == gio_storage.ABSENT


def test_the_volume_monitor_of_the_tests_lists_nothing_and_signals_work():
    from gi.repository import Gio

    monitor = Gio.VolumeMonitor.get()
    assert Gio.VolumeMonitor.get() is monitor  # one for the test, as the real one is
    assert monitor.get_mounts() == [] and monitor.get_volumes() == []
    assert monitor.get_connected_drives() == []
    heard = []
    handler = monitor.connect("mount-added", lambda m, mount: heard.append((m, mount)))
    monitor.emit("mount-added", "a mount")
    monitor.emit("mount-removed", "another")
    assert heard == [(monitor, "a mount")]
    monitor.disconnect(handler)
    monitor.emit("mount-added", "again")
    assert heard == [(monitor, "a mount")]


@pytest.mark.real_volume_monitor
def test_the_real_volume_monitor_only_when_asked_for():
    from gi.repository import Gio

    assert isinstance(Gio.VolumeMonitor.get(), Gio.VolumeMonitor)


@pytest.mark.needs_display
def test_a_window_of_a_test_stops_listening_when_the_test_ends(monkeypatch):
    """What the volume monitor of the tests does when a test ends (stop_windows): a real
    window made by the test is disconnected from it, and its timers (the USB poll, the
    texts told against now) are removed, so it does nothing in a later test."""
    import time

    from conftest import stop_windows
    from gi.repository import Gio, GLib

    from openexpress import netstatus
    from openexpress.gui.app import MainWindow

    monkeypatch.setattr(MainWindow, "refresh_device", lambda self, cause=None: None)
    monitor = Gio.VolumeMonitor.get()
    win = MainWindow(None)
    try:
        win.present()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and getattr(win, "_relative_timer", None) is None:
            GLib.MainContext.default().iteration(False)
        context = GLib.MainContext.default()
        assert win._usb_poll and context.find_source_by_id(win._usb_poll) is not None
        relative = win._relative_timer
        assert relative and context.find_source_by_id(relative) is not None
        assert {signal for signal, _h, _a in monitor.handlers.values()} == {
            "mount-added", "mount-removed"}
        stop_windows(monitor)
        assert monitor.handlers == {}
        assert context.find_source_by_id(win._usb_poll) is None
        assert context.find_source_by_id(relative) is None and win._relative_timer is None
    finally:
        netstatus.STATUS.remove_listener(win._net_listener)
        win.destroy()
