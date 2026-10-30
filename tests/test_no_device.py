"""No Garmin device on USB at all is the state the program waits in, not a failure: the window
started without a device logs one line of INFO that says so ("no Garmin device is
connected"), without "failed" and without naming MTP; the user is told the same words. A
device on USB that does not come (starting, held, no gvfs-mtp) is still told as an error. All
devices are made up."""
import pytest
from test_detection import plugged  # noqa: F401 - the fixture
from test_task_failure_log import _at_once, _lines

from openexpress.device import gio_storage
from openexpress.errors import OrdinaryStop


def test_no_garmin_device_on_usb_is_an_ordinary_stop(plugged):
    with pytest.raises(gio_storage.NoDevice) as stopped:
        plugged.open()
    assert isinstance(stopped.value, gio_storage.NotThereYet)  # caught where that is
    assert isinstance(stopped.value, OrdinaryStop)
    assert str(stopped.value) == gio_storage.not_ready_text(gio_storage.ABSENT)
    assert "MTP" not in stopped.value.log_reason


def test_a_device_on_usb_that_does_not_come_is_still_an_error(plugged):
    plugged.plug("0003")  # starting
    with pytest.raises(gio_storage.NotThereYet) as error:
        plugged.open()
    assert not isinstance(error.value, OrdinaryStop)


def test_the_window_started_without_a_device_logs_it_mildly(plugged, monkeypatch, log_file):
    from fakes import window as stand_in

    from openexpress import applog
    from openexpress.gui import app
    from openexpress.gui.detection import detect_devices

    _at_once(monkeypatch)
    monkeypatch.setattr(gio_storage, "find_ums_devices", list)
    applog.setup("gui")
    told, failed = [], []
    win = stand_in(busy=False, session=None, set_busy=lambda *a, **kw: None,
                   error=lambda title, text: told.append(text),
                   _run_pending_refresh=lambda: None)
    app.MainWindow.run_task(win, "Detecting the device…", lambda: detect_devices(None),
                            on_error=lambda e: failed.append(e) or True)
    applog.close()
    (line,) = _lines(log_file, "no Garmin device is connected")
    assert " INFO " in line and "failed" not in line and "MTP" not in line
    assert not _lines(log_file, "task failed")
    assert len(failed) == 1 and isinstance(failed[0], gio_storage.NotThereYet)
    assert told == []  # no message: the window shows its page without a device


def test_the_command_line_tells_the_user_the_same_words(monkeypatch, capsys, log_file):
    from openexpress import cli

    def no_device(*a, **kw):
        raise gio_storage.NoDevice(gio_storage.not_ready_text(gio_storage.ABSENT))

    monkeypatch.setattr(cli, "open_device", no_device)
    assert cli.main(["info"]) == 1
    assert "Error: No Garmin device" in capsys.readouterr().err
    assert _lines(log_file, "stopped: no Garmin device is connected")
