"""#13 (CachyOS/KDE, fēnix 8, 0.6.15): gvfs had no MTP backend (no gvfs-mtp), and KDE's
kiod6 (Dolphin) held the device; the window said "starting" for 45 s, then asked about a
PIN. Now a missing MTP backend is said at once, with what to install, in the window and on
the command line; and the page "not ready" names other programs that may hold the device.
Nothing here asks the real gvfs (conftest: it has MTP, unless a test says otherwise)."""

import logging
import types

import pytest
from test_detection import (  # noqa: F401 - the fixture
    REPLUG_HINT_AFTER_PIN,
    _banner_window,
    plugged,
)

from openexpress import i18n
from openexpress.device import gio_storage

NO_MTP_TITLE = "No MTP support in the system (gvfs-mtp)"
NO_MTP_TEXT = ("openExpress reads the device over MTP through gvfs, and gvfs on this system "
               "has no MTP support. Install it (Fedora: sudo dnf install gvfs-mtp; Arch: sudo "
               "pacman -S gvfs-mtp; Debian and Ubuntu: sudo apt install gvfs-backends), log "
               "out and back in, then plug the device in again.")
HELD = ("Another program may be holding the device (e.g. Dolphin or Nautilus): close it, then "
        "unplug the cable and plug it back in.")


def gvfs_offers(monkeypatch, *schemes):
    """What GIO says it can open here: the backends of gvfs (made up)."""
    monkeypatch.setattr(gio_storage, "_vfs", lambda: types.SimpleNamespace(
        get_supported_uri_schemes=lambda: list(schemes)))


@pytest.mark.parametrize(("schemes", "missing"), [
    (("file", "mtp", "sftp", "smb"), False),  # gvfs with gvfs-mtp
    (("file", "sftp", "smb"), True),          # gvfs without it
    (("file", "resource"), True),             # no gvfs at all: GIO opens local files only
])
def test_an_mtp_backend_missing_from_gvfs_is_seen(monkeypatch, schemes, missing):
    gvfs_offers(monkeypatch, *schemes)
    assert gio_storage.mtp_missing() is missing


def test_a_question_that_fails_is_not_taken_for_no_mtp(monkeypatch):
    """Not known: as before (the wait and its words)."""
    def broken():
        raise RuntimeError("no session bus")

    monkeypatch.setattr(gio_storage, "_vfs", broken)
    assert gio_storage.mtp_missing() is False


def test_the_command_line_says_it_at_once(plugged, monkeypatch):
    gvfs_offers(monkeypatch, "file", "sftp")
    plugged.plug("51b4")
    with pytest.raises(gio_storage.NotThereYet) as error:
        plugged.open()
    assert str(error.value) == NO_MTP_TEXT
    assert plugged.clock.now < 2  # not after 45 s


def test_a_device_still_starting_is_waited_for_then_told(plugged, monkeypatch):
    """In its transitional mode (091e:0003) it may still come as a USB drive: waited for as
    before; once it is in its normal mode, said at once."""
    gvfs_offers(monkeypatch, "file", "sftp")
    plugged.plug("0003")
    plugged.steps[10] = lambda: plugged.plug("51b4")
    with pytest.raises(gio_storage.NotThereYet) as error:
        plugged.open()
    assert str(error.value) == NO_MTP_TEXT
    assert 10 <= plugged.clock.now < 12


def test_the_window_says_it_at_once_and_logs_it_once(plugged, monkeypatch, caplog):
    from openexpress.gui.app import MainWindow

    gvfs_offers(monkeypatch, "file", "sftp")
    win, shown, _refreshed = _banner_window(monkeypatch)
    plugged.plug("51b4")
    with caplog.at_level(logging.INFO, logger="openexpress.device.gio_storage"):
        for now in (100, 102, 160):  # at once, and also after 45 s: no "starting", no PIN
            plugged.clock.now = now
            MainWindow._poll_usb(win, clock=plugged.clock)
            assert shown[-1] == (NO_MTP_TITLE, NO_MTP_TEXT, False), now
    lines = [r.getMessage() for r in caplog.records if "MTP backend" in r.getMessage()]
    assert lines == ["gvfs: no MTP backend (it offers: file, sftp)"]


def test_with_mtp_the_window_waits_as_before():
    from openexpress.gui.app import MainWindow

    assert MainWindow._usb_notice(gio_storage.PRESENT, 10)[0] == "The device is starting…"


def test_no_mtp_in_polish(monkeypatch):
    from openexpress.gui.app import MainWindow

    gvfs_offers(monkeypatch, "file")
    i18n.setup("pl")
    try:
        title, text, busy = MainWindow._usb_notice(gio_storage.PRESENT, 0)
    finally:
        i18n.setup("en")
    assert title == "Brak obsługi MTP w\u00a0systemie (gvfs-mtp)"
    assert text == ("openExpress odczytuje urządzenie przez MTP za pomocą gvfs, a\u00a0w\u00a0tym "
                    "systemie gvfs nie obsługuje MTP. Zainstaluj tę obsługę (Fedora: sudo dnf "
                    "install gvfs-mtp; Arch: sudo pacman -S gvfs-mtp; Debian i\u00a0Ubuntu: sudo "
                    "apt install gvfs-backends), wyloguj się i\u00a0zaloguj ponownie, a\u00a0potem "
                    "podłącz urządzenie jeszcze raz.")
    assert busy is False


# --- the page "not ready": another program may hold the device ---------------------------------

@pytest.mark.parametrize(("normal_s", "replug"), [(5, False), (30, True)])
def test_the_page_not_ready_names_other_programs(normal_s, replug):
    """After 45 s: the PIN, the advice to plug it in again (after 15 s in its normal mode),
    then other programs (Dolphin's kiod6 held the device in #13)."""
    from openexpress.gui.app import MainWindow

    pin = gio_storage.not_ready_text(gio_storage.PRESENT)
    expected = f"{pin} {REPLUG_HINT_AFTER_PIN} {HELD}" if replug else f"{pin} {HELD}"
    assert MainWindow._usb_notice(gio_storage.PRESENT, 46, normal_s) == (
        "The device is not ready", expected, False)


def test_other_programs_in_polish():
    from openexpress.gui.app import MainWindow

    i18n.setup("pl")
    try:
        text = MainWindow._usb_notice(gio_storage.PRESENT, 46, 5)[1]
    finally:
        i18n.setup("en")
    assert text.endswith("Urządzenie może zajmować inny program (np. Dolphin albo Nautilus): "
                         "zamknij go, a\u00a0potem odłącz kabel i\u00a0podłącz go ponownie.")


def test_the_command_line_after_45_s_is_as_before(plugged):
    """Only the window's page gets the sentence; the command line says what it said."""
    plugged.plug("51b4")
    with pytest.raises(gio_storage.DeviceError) as error:
        plugged.open()
    assert HELD not in str(error.value)
