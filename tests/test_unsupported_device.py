"""A Garmin device that offers neither MTP nor a USB drive that stays (HID alone, or HID beside a
drive: an inReach Mini of the first generation) is told at once to be one openExpress cannot
use, in the window and on the command line, without the wait and the advice for a device that
is not ready. Devices that offer MTP, or are drives, never are. All of /sys is made up."""
import logging
import shutil
import types

import pytest
from fakes import garmin_on_usb, replace_in_window
from fakes import window as stand_in

from openexpress import i18n
from openexpress.device import gio_storage
from openexpress.errors import OrdinaryStop

NAME = "Garmin USB inReach"
SERIAL = "MADEUPSERIAL"
WHY = ("openExpress works with devices that connect as a media device (MTP) or a USB drive. "
       "This one uses a different connection. Connected: Garmin USB inReach (USB 091e:0b89).")
WINDOW_TEXT = f"{WHY} To report it, choose “Report a Device or Problem…” in the main menu."
CLI_TEXT = f"{WHY} To report it, run openexpress report."
TITLE = "Device Not Supported"
SHORT = "Garmin USB inReach\u00a0· Unsupported connection"  # on the start page
WARNING = "dialog-warning-symbolic"  # not the program's icon, whose arrows say "wait"


@pytest.fixture
def usb(tmp_path, monkeypatch):
    folder = tmp_path / "usb"
    folder.mkdir()
    monkeypatch.setattr(gio_storage, "SYSFS_USB", folder)
    return folder


def _mini(usb, classes=("03", "08"), devnum=9, **files):
    """An inReach Mini of the first generation: HID and a drive, or HID only."""
    return garmin_on_usb(usb, "1-4", "0b89", classes, devnum, product=NAME, serial=SERIAL,
                         **files)


@pytest.mark.parametrize(("product_id", "classes", "state"), [
    ("0b89", ("03", "08"), gio_storage.UNSUPPORTED),  # the Mini's first minute
    ("0b89", ("03",), gio_storage.UNSUPPORTED),       # and afterwards
    ("20ee", ("08",), gio_storage.MASS_STORAGE),      # a Varia RTL515
    ("0d11", ("08",), gio_storage.MASS_STORAGE),      # an inReach Mini 2: a drive
    ("0d24", ("08",), gio_storage.MASS_STORAGE),      # an Instinct 2S as a drive
    ("51b4", ("06",), gio_storage.PRESENT),           # a watch over PTP/MTP
    ("5158", ("ff",), gio_storage.PRESENT),           # an Edge over MTP, vendor-specific
    ("51b5", ("06", "03"), gio_storage.PRESENT),      # MTP with HID beside it
    ("51b6", ("08", "ff"), gio_storage.PRESENT),      # a drive with a vendor-specific one
    ("0003", ("ff",), gio_storage.STARTING),          # a watch starting
    ("51b7", (None,), gio_storage.PRESENT),           # a class that cannot be read
    ("51b8", ("03", None), gio_storage.PRESENT),      # HID beside one that cannot be read
    ("51b9", (), gio_storage.PRESENT),                # no interface
], ids=["mini-hid-and-drive", "mini-hid-only", "varia-drive", "inreach-mini-2-drive",
        "instinct-2s-drive", "watch-ptp", "edge-vendor-mtp", "mtp-and-hid", "drive-and-vendor",
        "starting-0003", "unreadable-class", "hid-and-unreadable", "no-interface"])
def test_only_hid_or_hid_beside_a_drive_is_not_supported(usb, product_id, classes, state):
    garmin_on_usb(usb, "1-4", product_id, classes)
    assert gio_storage.usb_state() == state


@pytest.mark.parametrize(("other", "state"), [
    (("51b4", ("06",)), gio_storage.PRESENT),       # a watch beside it: waited for as today
    (("0003", ("ff",)), gio_storage.STARTING),      # one starting
    (("20ee", ("08",)), gio_storage.MASS_STORAGE),  # a drive beside it: as today
])
def test_beside_another_garmin_device_that_one_counts(usb, other, state):
    _mini(usb)
    garmin_on_usb(usb, "1-5", *other, devnum=10)
    assert gio_storage.usb_state() == state


def test_the_log_says_it_once_without_its_name_or_serial(usb, caplog):
    _mini(usb)
    with caplog.at_level(logging.INFO, logger="openexpress"):
        for _ in range(3):
            gio_storage.usb_state()
    said = [r.getMessage() for r in caplog.records if "not supported" in r.getMessage()]
    line = ("USB: a Garmin device 091e:0b89 with interfaces 03, 08: HID without MTP, not "
            "supported")
    assert said == [line]
    assert NAME not in caplog.text and SERIAL not in caplog.text


def test_the_words_say_why_then_name_it_as_it_names_itself_on_usb(usb):
    _mini(usb)
    assert gio_storage.unsupported_title() == TITLE
    assert gio_storage.unsupported_caption() == SHORT
    assert gio_storage.unsupported_text() == WINDOW_TEXT
    assert gio_storage.unsupported_text(window=False) == CLI_TEXT
    assert gio_storage.not_ready_text(gio_storage.UNSUPPORTED) == CLI_TEXT


def test_without_a_name_on_usb_it_is_a_garmin_device(usb):
    garmin_on_usb(usb, "1-4", "0b89", ("03",))
    assert "Connected: Garmin device (USB 091e:0b89)." in gio_storage.unsupported_text()
    assert gio_storage.unsupported_caption() == "Garmin device\u00a0· Unsupported connection"


def test_the_menu_item_is_named_with_the_menu_s_own_words(usb):
    """The words of the item, taken from the program, in each language."""
    from openexpress.i18n import _

    _mini(usb)
    for language, quoted in (("en", "“{}”"), ("pl", "„{}”")):
        i18n.setup(language)
        try:
            item, text = _("Report a Device or Problem…"), gio_storage.unsupported_text()
        finally:
            i18n.setup("en")
        assert quoted.format(item) in text


def test_the_words_in_polish(usb):
    _mini(usb)
    i18n.setup("pl")
    try:
        title, short = gio_storage.unsupported_title(), gio_storage.unsupported_caption()
        window, cli = gio_storage.unsupported_text(), gio_storage.unsupported_text(window=False)
    finally:
        i18n.setup("en")
    why = ("openExpress działa z\u00a0urządzeniami, które łączą się jako urządzenie multimedialne "
           "(MTP) lub dysk USB. To urządzenie używa innego połączenia. Podłączone: Garmin USB "
           "inReach (USB 091e:0b89).")
    assert title == "Nieobsługiwane urządzenie"
    assert short == "Garmin USB inReach\u00a0· nieobsługiwane połączenie"
    assert window == (f"{why} Aby to zgłosić, wybierz „Zgłoś urządzenie lub problem…” "
                      "w\u00a0menu głównym.")
    assert cli == f"{why} Aby to zgłosić, uruchom openexpress report."


def test_the_command_line_says_it_at_once(usb, monkeypatch):
    """No 45 s, no "The device is starting…", no PIN, no KDE: the notice at once."""
    _mini(usb)
    monkeypatch.setattr(gio_storage, "find_devices", list)
    slept, notices = [], []
    with pytest.raises(gio_storage.UnsupportedDevice) as error:
        gio_storage.open_device_storage(wait=True, notify=notices.append, sleep=slept.append,
                                        clock=lambda: 0.0)
    assert isinstance(error.value, gio_storage.NotThereYet)
    assert isinstance(error.value, OrdinaryStop)  # a notice: no "Error:"
    assert str(error.value) == CLI_TEXT
    assert (slept, notices) == ([], [])


def test_a_command_ends_with_the_notice_and_exit_code_1(usb, capsys):
    from openexpress.cli import main

    _mini(usb, classes=("03",))
    assert main(["info"]) == 1
    err = capsys.readouterr().err
    assert err.strip() == CLI_TEXT
    assert "Error" not in err and "PIN" not in err and "kiod6" not in err


def test_the_window_says_it_at_once_and_never_more(usb):
    """Not busy, from the first moment, and the same after 45 s and more: no PIN, no KDE."""
    from openexpress.gui.app import MainWindow

    _mini(usb)
    for waited_s, normal_s in ((0, 0), (50, 0), (50, 30)):
        assert MainWindow._usb_notice(gio_storage.UNSUPPORTED, waited_s, normal_s) == (
            TITLE, WINDOW_TEXT, False)


def test_the_window_says_it_from_its_first_look_and_after_hid_only_comes_back(usb, monkeypatch):
    """The Mini leaves USB after its first minute and comes back with HID only: the same
    notice, and nothing is looked for. The start page says it in short, the pages of the
    device view in full; both with the warning icon."""
    from openexpress.gui.app import MainWindow
    from openexpress.gui.connected import Connected

    asked, on_start, on_pages = [], [], []
    replace_in_window(monkeypatch, "schedule_detection",
                      lambda win, ms, reason=None, cause=None: asked.append(ms))
    win = stand_in(
        session=None, busy=False, _usb_state=gio_storage.ABSENT, _usb_since=0.0,
        _usb_title="", _usb_notice=MainWindow._usb_notice, _update_actions=lambda: None,
        start=types.SimpleNamespace(set_empty=lambda *notice, icon=None: on_start.append(
            (*notice, icon))),
        connected=Connected(devices=[]),
        sections={"watch": types.SimpleNamespace(set_empty=lambda *notice, icon=None:
                                                 on_pages.append((*notice, icon)))})
    now = [0.0]
    _mini(usb)
    MainWindow._poll_usb(win, lambda: now[0])
    assert on_start[-1] == (TITLE, SHORT, False, WARNING)
    assert on_pages[-1] == (TITLE, WINDOW_TEXT, False, WARNING)
    shutil.rmtree(usb / "1-4")  # off USB for a moment
    now[0] += 1
    MainWindow._poll_usb(win, lambda: now[0])
    _mini(usb, classes=("03",), devnum=10)
    now[0] += 1
    MainWindow._poll_usb(win, lambda: now[0])
    assert on_start[-1] == (TITLE, SHORT, False, WARNING)
    assert on_pages[-1] == (TITLE, WINDOW_TEXT, False, WARNING)
    assert asked == []


@pytest.mark.needs_display
def test_the_warning_icon_on_the_start_page_and_the_pages_then_the_program_s_again():
    """The notice's icon follows what it says: the warning for a device not supported, the
    program's icon for anything else (no device, one starting)."""
    from openexpress.gui.device_page import Section
    from openexpress.gui.start import APP_ID, StartPage

    section = Section("watch", "Device", "computer-symbolic", "Detecting…")
    start = StartPage()
    section.set_empty(TITLE, WINDOW_TEXT, False, icon=WARNING)
    start.set_empty(TITLE, SHORT, False, icon=WARNING)
    assert section.empty.get_icon_name() == WARNING
    assert start.notice_icon.get_icon_name() == WARNING
    section.set_empty("Connect Your Device", "…", False)
    start.set_empty("Connect Your Device", "…", False)
    assert section.empty.get_icon_name() == APP_ID
    assert start.notice_icon.get_icon_name() == APP_ID
