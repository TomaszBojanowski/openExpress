"""`openexpress report` and `report --log`, and the window's report, when no device can be
opened (not supported, not ready, starting, a drive not mounted, several, none): a report of
what is on USB and of the system, never a serial number; the log as always. All of /sys is
made up."""
import pytest
from fakes import garmin_on_usb

from openexpress import report
from openexpress.device import gio_storage

SERIAL = "MADEUPSERIAL"
MINI = ('- Garmin devices on USB: 091e:0b89 "Garmin USB inReach", interfaces: HID (03), mass '
        'storage (08)')


@pytest.fixture
def usb(tmp_path, monkeypatch):
    folder = tmp_path / "usb"
    folder.mkdir()
    monkeypatch.setattr(gio_storage, "SYSFS_USB", folder)
    return folder


def _mini(usb):
    garmin_on_usb(usb, "1-4", "0b89", ("03", "08"), product="Garmin USB inReach", serial=SERIAL)


def _device_section(text: str) -> list[str]:
    return text.split("### System", 1)[0].strip().splitlines()


def test_the_report_of_a_device_not_supported_says_what_is_on_usb(usb, capsys):
    from openexpress.cli import main

    _mini(usb)
    assert main(["report"]) == 0
    out, err = capsys.readouterr()
    assert _device_section(out) == [
        "### Device",
        "- Not opened: not supported (HID without MTP)",
        MINI,
        "- MTP support in gvfs: yes"]
    assert "- openExpress: " in out and "- Python: " in out and "- OS: " in out
    assert "### Features" not in out  # no GarminDevice.xml declares any
    assert "No device was opened: the report says what is connected over USB." in err
    assert "%5BDevice%5D%20Garmin%20USB%20inReach" in err  # the form's title names it
    assert SERIAL not in out + err


@pytest.mark.parametrize(("place", "args", "reason"), [
    (None, [], "no Garmin device on USB"),
    (("51b4", ("06",)), ["--no-wait"], "not ready (on USB, not offered over MTP)"),
    (("0003", ("ff",)), ["--no-wait"], "starting (in its transitional mode)"),
    (("20ee", ("08",)), [], "a USB drive that is not mounted"),
], ids=["none", "not-ready", "starting", "drive-not-mounted"])
def test_each_reason_why_no_device_was_opened(usb, capsys, place, args, reason):
    from openexpress.cli import main

    if place is not None:
        garmin_on_usb(usb, "1-4", *place)
    assert main([*args, "report"]) == 0
    section = _device_section(capsys.readouterr().out)
    assert section[1] == f"- Not opened: {reason}"
    if place is None:
        assert section[2] == "- Garmin devices on USB: none"


def test_several_devices_are_said(usb, monkeypatch, capsys):
    from openexpress.cli import main

    two = [gio_storage.FoundDevice(f"mtp://made-up-{n}/", "mtp", True,
                                   usb=gio_storage.GarminUsb("51b4", 1, n)) for n in (9, 10)]
    monkeypatch.setattr(gio_storage, "find_devices", lambda: two)
    garmin_on_usb(usb, "1-4", "51b4", ("06",))
    garmin_on_usb(usb, "1-5", "51b4", ("06",), devnum=10)
    assert main(["report"]) == 0
    assert _device_section(capsys.readouterr().out)[1] == (
        "- Not opened: several Garmin devices are connected")


def test_report_log_gives_the_log_without_a_device(usb, log_file, capsys):
    """The end of the log, fenced, as with a device; the report goes into the form's
    address, as with a device."""
    from urllib.parse import unquote

    from openexpress.cli import main

    _mini(usb)
    assert main(["report", "--log"]) == 0
    out, err = capsys.readouterr()
    assert out.startswith("~~~") and "start: report --log" in out
    assert "report: no device opened (UnsupportedDevice), a report of what is on USB" in out
    assert "- Not opened: not supported (HID without MTP)" in unquote(err)
    assert SERIAL not in out + err


def test_a_folder_given_with_path_still_has_to_be_a_device(tmp_path, capsys):
    """--path names what to report on: a folder that is no device is an error."""
    from openexpress.cli import main

    assert main(["--path", str(tmp_path / "nothing"), "report"]) == 1
    assert "### Device" not in capsys.readouterr().out


def test_the_interfaces_are_named_and_an_unknown_one_said_as_it_is(usb):
    garmin_on_usb(usb, "1-4", "51b4", ("06", "ff", None, "0e"), product="Edge 1050")
    text = report.build_without_device()
    assert _device_section(text)[2] == (
        '- Garmin devices on USB: 091e:51b4 "Edge 1050", interfaces: PTP/MTP (06), '
        "vendor-specific (ff), unknown, other (0e)")
    assert report.model_on_usb() == "Edge 1050"


def test_two_devices_on_usb_and_one_without_a_name(usb):
    _mini(usb)
    garmin_on_usb(usb, "1-5", "20ee", ("08",), devnum=10)
    assert _device_section(report.build_without_device())[2] == (
        f'{MINI}; 091e:20ee, interfaces: mass storage (08)')


@pytest.mark.needs_display
def test_the_window_shows_the_report_without_a_device(usb, monkeypatch):
    from fakes import window as stand_in
    from fakes import window_methods
    from gi.repository import Adw

    _mini(usb)
    shown = []
    monkeypatch.setattr(Adw.AlertDialog, "present", lambda self, parent: shown.append(self))
    win = window_methods(stand_in(session=None, get_width=lambda: 1000),
                         ["show_report", "_report_view"])
    win.show_report()
    (dialog,) = shown
    assert dialog.get_body().startswith(
        "No device is open: the report says what is connected over USB. Open on GitHub")
    view = dialog.get_extra_child()
    assert view is not None
    buffer = next(w for w in _descendants(view) if hasattr(w, "get_buffer")).get_buffer()
    text = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False)
    assert _device_section(text) == [
        "### Device", "- Not opened: not supported (HID without MTP)", MINI,
        "- MTP support in gvfs: yes"]
    assert "Logged in" not in text  # the window without a device does not know it
    assert dialog.has_response("copy") and SERIAL not in text


def _descendants(widget):
    child = widget.get_first_child()
    while child is not None:
        yield child
        yield from _descendants(child)
        child = child.get_next_sibling()
