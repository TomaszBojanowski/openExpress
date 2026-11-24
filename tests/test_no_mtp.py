"""A fēnix 8 with gvfs lacking its MTP backend (gvfs-mtp), or held by KDE's MTP daemon
(kmtpd, in kiod6, which Dolphin starts) so that gvfs cannot offer it: either would leave the
window saying "starting" for 45 s, then asking about a PIN. A missing backend is said at once,
with what to install, in the window and on the command line; the page "not ready" names the
programs that may hold the device. Nothing asks the real gvfs (conftest: with MTP unless a
test says otherwise)."""

import logging
import os
import types
from pathlib import Path

import pytest
from fakes import in_language
from fakes import iterate as _iterate
from test_detection import (  # noqa: F401 - the fixture
    NUMERIC,
    REPLUG_HINT_AFTER_PIN,
    _banner_window,
    plugged,
)

from openexpress.device import gio_storage

NO_MTP_TITLE = "No MTP Support in the System (gvfs-mtp)"
NO_MTP_TEXT = ("openExpress reads the device over MTP through gvfs, and gvfs on this system "
               "has no MTP support. Install it (Fedora: sudo dnf install gvfs-mtp; Arch: sudo "
               "pacman -S gvfs-mtp; Debian and Ubuntu: sudo apt install gvfs-backends), log "
               "out and back in, then plug the device in again.")
HELD = ("KDE's MTP service may be holding the device: it keeps it after Dolphin is closed "
        "and takes it back when the cable is plugged in again. Close Dolphin and run "
        "kquitapp6 kiod6, then unplug the cable and plug it in again.")


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
    """Not known: taken as MTP there, so the window waits with its usual words."""
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
    """In its transitional mode (091e:0003) it may still come as a USB drive: waited for; once
    it is in its normal mode, said at once."""
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


def test_with_mtp_the_window_says_the_device_is_starting():
    from openexpress.gui.app import MainWindow

    assert MainWindow._usb_notice(gio_storage.PRESENT, 10)[0] == "Device Starting…"


def test_no_mtp_in_polish(monkeypatch):
    from openexpress.gui.app import MainWindow

    gvfs_offers(monkeypatch, "file")
    with in_language("pl"):
        title, text, busy = MainWindow._usb_notice(gio_storage.PRESENT, 0)
    assert title == "Brak obsługi MTP w\u00a0systemie (gvfs-mtp)"
    assert text == ("openExpress odczytuje urządzenie przez MTP za pomocą gvfs, a\u00a0w\u00a0tym "
                    "systemie gvfs nie obsługuje MTP. Zainstaluj tę obsługę (Fedora: sudo dnf "
                    "install gvfs-mtp; Arch: sudo pacman -S gvfs-mtp; Debian i\u00a0Ubuntu: sudo "
                    "apt install gvfs-backends), wyloguj się i\u00a0zaloguj ponownie, a\u00a0potem "
                    "podłącz urządzenie jeszcze raz.")
    assert busy is False


# --- the page "not ready": another program may hold the device ---------------------------------

@pytest.mark.parametrize(("normal_s", "replug"), [(5, False), (30, True)])
def test_the_page_not_ready_names_other_programs(normal_s, replug, kde_desktop):
    """After 45 s: the PIN, the advice to plug it in again (after 15 s in its normal mode),
    then other programs (Dolphin's kiod6 may hold the device)."""
    from openexpress.gui.app import MainWindow

    pin = gio_storage.not_ready_text(gio_storage.PRESENT)
    expected = f"{pin} {REPLUG_HINT_AFTER_PIN} {HELD}" if replug else f"{pin} {HELD}"
    assert MainWindow._usb_notice(gio_storage.PRESENT, 46, normal_s) == (
        "Device Not Ready", expected, False)


def test_other_programs_in_polish(kde_desktop):
    from openexpress.gui.app import MainWindow

    with in_language("pl"):
        text = MainWindow._usb_notice(gio_storage.PRESENT, 46, 5)[1]
    assert text.endswith("Urządzenie może zajmować usługa MTP w\u00a0KDE: trzyma je także po "
                         "zamknięciu Dolphina i\u00a0bierze z\u00a0powrotem po ponownym "
                         "podłączeniu kabla. Zamknij Dolphina i\u00a0uruchom kquitapp6 kiod6, "
                         "a\u00a0potem odłącz kabel i\u00a0podłącz go ponownie.")


def test_the_command_line_after_45_s_names_other_programs_too(plugged, kde_desktop):
    """The same sentence as the window's page, after the PIN."""
    plugged.plug("51b4")
    with pytest.raises(gio_storage.DeviceError) as error:
        plugged.open()
    assert str(error.value) == f"{gio_storage.not_ready_text(gio_storage.PRESENT)} {HELD}"


MOUNT_FAILED = "Cannot mount the device: Unable to open MTP device “001,009”"


def test_the_command_line_names_other_programs_also_when_mounting_fails(plugged, monkeypatch,
                                                                          kde_desktop):
    """A fēnix 8 held by another process over libmtp (as KDE's kmtpd holds it): gvfs offers its
    volume, and every mount fails at once with "Unable to open MTP device". After 45 s the
    command line says that error and the same sentence as the window's page (the window says
    it from the USB state, whatever the last failure was)."""
    def mount(dev):
        raise gio_storage.DeviceError(MOUNT_FAILED)

    monkeypatch.setattr(gio_storage, "mount", mount)
    plugged.plug("51b4")
    plugged.offered[:] = [gio_storage.FoundDevice(
        NUMERIC, "fēnix 8", False, object(), gio_storage.GarminUsb("51b4", 1, 9))]
    with pytest.raises(gio_storage.DeviceError) as error:
        plugged.open()
    assert str(error.value) == f"{MOUNT_FAILED}. {HELD}"
    assert plugged.clock.now >= gio_storage.WAIT_FOR_DEVICE_S
    assert plugged.notices == ["The device is starting…"]


def test_a_mount_error_before_the_wait_ran_out_is_said_as_it_is(plugged, monkeypatch):
    """Without waiting (the window): the error alone, without the sentence of other programs."""
    monkeypatch.setattr(gio_storage, "mount", lambda dev, timeout_s=30: (_ for _ in ()).throw(
        gio_storage.DeviceError(MOUNT_FAILED)))
    plugged.plug("51b4")
    plugged.offered[:] = [gio_storage.FoundDevice(
        NUMERIC, "fēnix 8", False, object(), gio_storage.GarminUsb("51b4", 1, 9))]
    with pytest.raises(gio_storage.DeviceError) as error:
        plugged.open(wait=False)
    assert str(error.value) == MOUNT_FAILED


def test_a_device_still_starting_after_45_s_gets_no_such_sentence(plugged, kde_desktop):
    """In its transitional mode no program holds it over MTP yet: none named."""
    plugged.plug("0003")
    with pytest.raises(gio_storage.DeviceError) as error:
        plugged.open()
    assert HELD not in str(error.value)


def test_the_readme_names_the_same_packages_and_words():
    """README, "System requirements": the same install commands as the window and the
    command line, and the words the window shows."""
    readme = (Path(__file__).resolve().parent.parent / "README.md").read_text(encoding="utf-8")
    section = readme.split("### System requirements", 1)[1].split("\n## ", 1)[0]
    for command in ("sudo dnf install gvfs-mtp", "sudo pacman -S gvfs-mtp",
                    "sudo apt install gvfs-backends"):
        assert command in NO_MTP_TEXT and f"`{command}`" in section
    assert f"**{NO_MTP_TITLE}**" in section and "**Device Not Ready**" in section
    assert "Dolphin" in section and "kiod6" in section
    # KDE's kmtpd keeps the device after Dolphin is closed and takes it back when it is plugged
    # in again (kio-extras): only quitting kiod6 frees it.
    flat = " ".join(section.split())  # the words, wherever the lines of the paragraph break
    assert "`kquitapp6 kiod6`" in flat and "kmtpd from kio-extras" in flat
    assert "kio-mtp" not in flat and "kquitapp6 kiod6" in HELD


# --- both notices fit the narrowest window (360 px) ------------------------------------------------

from test_narrow_window import NARROWEST, _min_width, _tiles, narrow_window  # noqa: E402, F401


@pytest.mark.needs_display
@pytest.mark.filterwarnings("ignore:'asyncio.*:DeprecationWarning")
@pytest.mark.parametrize("language", ["en", "pl"])
@pytest.mark.parametrize("notice", ["no MTP", "not ready, KDE", "not ready, no access"])
def test_the_notices_fit_the_narrowest_window(narrow_window, monkeypatch, tmp_path, language,
                                              notice):
    """On top of the start page and on a page of the device view (no device open), folded."""
    from openexpress.gui.app import MainWindow

    if notice == "no MTP":
        gvfs_offers(monkeypatch, "file")
    elif notice == "not ready, KDE":
        monkeypatch.setenv("XDG_CURRENT_DESKTOP", "KDE")
    else:
        a_watch_on_usb(monkeypatch, tmp_path)
        a_node_this_user_may_not_open(monkeypatch, tmp_path)
    win = narrow_window(language)
    words = MainWindow._usb_notice(gio_storage.PRESENT, 46, 30)
    assert (words[0] == gio_storage.no_mtp_title()) is (notice == "no MTP")
    win.start.set_empty(*words)
    win.start.show(_tiles(), True)
    for section in win.sections.values():
        section.set_empty(*words)
    _iterate(0.05)
    assert _min_width(win.start.page) <= NARROWEST
    win.navigation.set_animate_transitions(False)
    win.navigation.push(win.device_view)
    _iterate(0.05)
    assert _min_width(win.device_view) <= NARROWEST


# --- who may hold the device: its node first, KDE only where KDE is seen --------------------

CONNECTED = "The Garmin device is connected, but it does not offer its files."
NO_ACCESS = ("This user has no access to the device on USB. Run openExpress as the user logged "
             "in at this computer's screen, not over SSH, or give this user access to the device "
             "(a udev rule).")
NO_ACCESS_NODE = ("This user has no access to the device on USB ({node}). Run openExpress as the "
                  "user logged in at this computer's screen, not over SSH, or give this user "
                  "access to the device (a udev rule).")


def a_watch_on_usb(monkeypatch, tmp_path):
    """A fēnix 8 on USB in its normal mode, at bus 1, device 9 (as `plugged` puts it)."""
    root = tmp_path / "sys"
    device = root / "1-9"
    device.mkdir(parents=True)
    for attr, value in {"busnum": 1, "devnum": 9, "idVendor": "091e", "idProduct": "51b4"}.items():
        (device / attr).write_text(f"{value}\n")
    monkeypatch.setattr(gio_storage, "SYSFS_USB", root)


def a_node_this_user_may_not_open(monkeypatch, tmp_path):
    """The device's node in /dev/bus/usb (bus 1, device 9): the desktop session's user may open
    it, this one may not (as over SSH)."""
    nodes = tmp_path / "dev-bus-usb"
    node = nodes / "001" / "009"
    node.parent.mkdir(parents=True)
    node.write_bytes(b"")
    monkeypatch.setattr(gio_storage, "USB_NODES", nodes)
    real = os.access

    def access(path, mode, *args, **kwargs):
        return False if Path(path) == node else real(path, mode, *args, **kwargs)

    monkeypatch.setattr(os, "access", access)
    return node


def a_node_this_user_may_open(monkeypatch, tmp_path):
    nodes = tmp_path / "dev-bus-usb"
    node = nodes / "001" / "009"
    node.parent.mkdir(parents=True)
    node.write_bytes(b"")
    monkeypatch.setattr(gio_storage, "USB_NODES", nodes)
    return node


def a_process(monkeypatch, tmp_path, name):
    """A process of this user in a made-up /proc."""
    proc = tmp_path / "proc"
    (proc / "4242").mkdir(parents=True)
    (proc / "4242" / "comm").write_text(f"{name}\n")
    (proc / "self").mkdir()  # not a process number: passed over
    monkeypatch.setattr(gio_storage, "PROC", proc)
    return proc


def test_without_kde_the_command_line_names_no_kde_service(plugged, monkeypatch, tmp_path):
    """gvfs never offered the watch (its node was not this user's to open, as over SSH) and,
    after the wait, the command line named KDE's MTP service and Dolphin on a computer
    without KDE."""
    a_node_this_user_may_not_open(monkeypatch, tmp_path)
    plugged.plug("51b4")
    with pytest.raises(gio_storage.DeviceError) as error:
        plugged.open()
    said = str(error.value)
    assert "KDE" not in said and "kquitapp6" not in said


def test_a_node_this_user_may_not_open_is_named(plugged, monkeypatch, tmp_path):
    """What kept gvfs from the watch there: this user may not open its node. The advice about
    its screen and its PIN helps nothing then: only that it is connected, and the access, with
    the node on the command line."""
    node = a_node_this_user_may_not_open(monkeypatch, tmp_path)
    plugged.plug("51b4")
    with pytest.raises(gio_storage.DeviceError) as error:
        plugged.open()
    assert str(error.value) == f"{CONNECTED} {NO_ACCESS_NODE.format(node=node)}"


def test_no_access_comes_before_kde(plugged, monkeypatch, tmp_path, kde_desktop):
    """On KDE too: a node this user may not open is what keeps gvfs from the device."""
    node = a_node_this_user_may_not_open(monkeypatch, tmp_path)
    plugged.plug("51b4")
    with pytest.raises(gio_storage.DeviceError) as error:
        plugged.open()
    assert str(error.value) == f"{CONNECTED} {NO_ACCESS_NODE.format(node=node)}"


def test_neither_no_access_nor_kde_no_sentence_about_other_programs(plugged, monkeypatch,
                                                                    tmp_path):
    """The node may be opened and no KDE is seen: the PIN, and nothing about other programs."""
    a_node_this_user_may_open(monkeypatch, tmp_path)
    plugged.plug("51b4")
    with pytest.raises(gio_storage.DeviceError) as error:
        plugged.open()
    assert str(error.value) == gio_storage.not_ready_text(gio_storage.PRESENT)


def test_a_mount_error_after_the_wait_without_a_sentence_is_said_as_it_is(plugged, monkeypatch):
    """No node known (a Flatpak without --device=usb) and no KDE: the error alone."""
    monkeypatch.setattr(gio_storage, "mount", lambda dev, timeout_s=30: (_ for _ in ()).throw(
        gio_storage.DeviceError(MOUNT_FAILED)))
    plugged.plug("51b4")
    plugged.offered[:] = [gio_storage.FoundDevice(
        NUMERIC, "fēnix 8", False, object(), gio_storage.GarminUsb("51b4", 1, 9))]
    with pytest.raises(gio_storage.DeviceError) as error:
        plugged.open()
    assert str(error.value) == MOUNT_FAILED


@pytest.mark.parametrize("name", ["kiod6", "kiod5"])
def test_kde_is_seen_by_a_kiod_of_this_user(plugged, monkeypatch, tmp_path, name):
    """Outside the Flatpak KDE's MTP service shows as a kiod process, whatever the
    environment says (over SSH it names no desktop)."""
    a_process(monkeypatch, tmp_path, name)
    plugged.plug("51b4")
    with pytest.raises(gio_storage.DeviceError) as error:
        plugged.open()
    assert str(error.value).endswith(HELD)


@pytest.mark.parametrize(("variable", "value"), [
    ("XDG_CURRENT_DESKTOP", "KDE"), ("XDG_SESSION_DESKTOP", "KDE"),
    ("DESKTOP_SESSION", "plasma"), ("KDE_FULL_SESSION", "true")])
def test_kde_is_seen_by_the_environment(monkeypatch, variable, value):
    """Inside the Flatpak, which sees no process outside it: what the desktop says of itself."""
    monkeypatch.setenv(variable, value)
    assert gio_storage.kde_seen() == "the desktop"


def test_other_desktops_and_processes_are_not_kde(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "GNOME")
    a_process(monkeypatch, tmp_path, "nautilus")
    assert gio_storage.kde_seen() is None


def test_the_look_for_kiod_holds_a_while(monkeypatch, tmp_path):
    """The window asks on each look at USB: /proc is read again only after KDE_LOOK_S."""
    proc = a_process(monkeypatch, tmp_path, "sshd")
    assert gio_storage.kde_seen(clock=lambda: 100.0) is None
    (proc / "4242" / "comm").write_text("kiod6\n")
    assert gio_storage.kde_seen(clock=lambda: 100.0 + gio_storage.KDE_LOOK_S - 1) is None
    assert gio_storage.kde_seen(clock=lambda: 100.0 + gio_storage.KDE_LOOK_S) == "kiod6"


def test_the_log_says_once_what_was_found(monkeypatch, tmp_path, caplog):
    """The node (its bus and device number only) and whether it may be opened, KDE seen or
    not; once while it stays the same."""
    a_watch_on_usb(monkeypatch, tmp_path)
    node = a_node_this_user_may_not_open(monkeypatch, tmp_path)
    with caplog.at_level(logging.INFO, logger="openexpress.device.gio_storage"):
        for _look in range(3):
            gio_storage.held_elsewhere()
    lines = [r.getMessage() for r in caplog.records if "not offered by gvfs" in r.getMessage()]
    assert lines == [(f"device: not offered by gvfs; USB nodes: {node} without access; KDE: not "
                      "seen; told: access")]


@pytest.mark.parametrize("normal_s", [5, 30])
def test_the_page_not_ready_without_access_says_only_that(monkeypatch, tmp_path, normal_s):
    """No screen, PIN or cable to try (also after 15 s in its normal mode), and no node: a
    path in the window helps nobody; the command line and the log name it."""
    from openexpress.gui.app import MainWindow

    a_watch_on_usb(monkeypatch, tmp_path)
    a_node_this_user_may_not_open(monkeypatch, tmp_path)
    assert MainWindow._usb_notice(gio_storage.PRESENT, 46, normal_s) == (
        "Device Not Ready", f"{CONNECTED} {NO_ACCESS}", False)


def test_the_page_not_ready_says_nothing_of_other_programs_without_reason():
    from openexpress.gui.app import MainWindow

    pin = gio_storage.not_ready_text(gio_storage.PRESENT)
    assert MainWindow._usb_notice(gio_storage.PRESENT, 46, 5) == ("Device Not Ready", pin, False)


def test_no_access_in_polish(monkeypatch, tmp_path):
    from openexpress.gui.app import MainWindow

    a_watch_on_usb(monkeypatch, tmp_path)
    node = a_node_this_user_may_not_open(monkeypatch, tmp_path)
    with in_language("pl"):
        text = MainWindow._usb_notice(gio_storage.PRESENT, 46, 5)[1]
    assert node.exists() and text == (
        "Urządzenie Garmin jest podłączone, ale nie udostępnia plików. Ten użytkownik nie ma "
        "dostępu do urządzenia podłączonego przez USB. Uruchom openExpress jako użytkownik zalogowany przy "
        "ekranie tego komputera, a\u00a0nie przez SSH, albo nadaj temu użytkownikowi dostęp do "
        "urządzenia (reguła udev).")
