import atexit
import ctypes
import functools
import os
import select
import shutil
import signal
import struct
import subprocess
import time
from datetime import datetime
from pathlib import Path

import pytest

# --- the display of the tests: an X server of their own, never the desktop -------------------
#
# Tests that make GTK widgets need a display, and one of them shows a window. By default they
# get an X server of their own, hidden (Xvfb), so that nothing shows on the desktop. Set
# before anything opens a display (importing Gtk does):
#   OPENEXPRESS_TESTS_ON_SCREEN=1   the display of the session, as before;
#   OPENEXPRESS_TESTS_NO_DISPLAY=1  no display at all: the tests that need one are skipped.
# Without Xvfb (or if it does not start), the display of the session, as before.

ON_SCREEN, NO_DISPLAY = "OPENEXPRESS_TESTS_ON_SCREEN", "OPENEXPRESS_TESTS_NO_DISPLAY"
XVFB_SCREEN = "1280x1024x24"
_xvfb: subprocess.Popen | None = None


PR_SET_PDEATHSIG = 1  # linux/prctl.h


def _ends_with(parent: int):
    """For the child, before it runs: the kernel ends it (SIGTERM) once `parent` is gone, also
    when pytest dies without its atexit (a segfault); gone already, it ends at once."""
    def ask() -> None:
        try:
            ctypes.CDLL(None, use_errno=True).prctl(PR_SET_PDEATHSIG, signal.SIGTERM)
        except (OSError, AttributeError):
            return  # no prctl: atexit and pytest_unconfigure as before
        if os.getppid() != parent:
            os._exit(1)

    return ask


def _start_xvfb() -> subprocess.Popen | None:
    """A hidden X server on a free display (it tells which once it is ready), or None."""
    program = shutil.which("Xvfb")
    if program is None:
        return None
    read_end, write_end = os.pipe()
    try:
        server = subprocess.Popen(
            [program, "-displayfd", str(write_end), "-nolisten", "tcp",
             "-screen", "0", XVFB_SCREEN],
            pass_fds=(write_end,), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, preexec_fn=_ends_with(os.getpid()))
    except OSError:
        os.close(read_end)
        os.close(write_end)
        return None
    os.close(write_end)
    number, deadline = b"", time.monotonic() + 10
    while not number.endswith(b"\n") and time.monotonic() < deadline:
        ready, _w, _x = select.select([read_end], [], [], max(0.0, deadline - time.monotonic()))
        chunk = os.read(read_end, 16) if ready else b""
        if not chunk:
            break
        number += chunk
    os.close(read_end)
    if not number.strip().isdigit():
        server.kill()
        server.wait()
        return None
    os.environ["DISPLAY"] = ":" + number.strip().decode()
    os.environ["GDK_BACKEND"] = "x11"
    os.environ.pop("WAYLAND_DISPLAY", None)
    return server


def _stop_xvfb() -> None:
    global _xvfb
    if _xvfb is not None:
        _xvfb.terminate()
        try:
            _xvfb.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _xvfb.kill()
            _xvfb.wait()
        _xvfb = None


if os.environ.get(NO_DISPLAY) == "1":
    os.environ.pop("DISPLAY", None)
    os.environ.pop("WAYLAND_DISPLAY", None)
    os.environ["GDK_BACKEND"] = "x11"  # else GDK finds the session's Wayland socket anyway
    DISPLAY_OF_TESTS = "none (the tests that need one are skipped)"
elif os.environ.get(ON_SCREEN) == "1":
    DISPLAY_OF_TESTS = "the session's"
else:
    _xvfb = _start_xvfb()
    if _xvfb is not None:
        atexit.register(_stop_xvfb)  # also if pytest ends early
        DISPLAY_OF_TESTS = f"a hidden X server ({os.environ['DISPLAY']})"
    else:
        DISPLAY_OF_TESTS = "the session's (no Xvfb)"


def pytest_report_header(config):
    return f"display of the tests: {DISPLAY_OF_TESTS}"


def pytest_unconfigure(config):
    _stop_xvfb()


# Tests check English messages regardless of the system language.
os.environ["OPENEXPRESS_LANG"] = "en"
from openexpress import i18n  # noqa: E402 - after the display and the language are set

i18n.setup("en")


# --- tests that make GTK widgets need a display ------------------------------------------------


@functools.cache
def display_available() -> bool:
    """Whether GTK has a display. Importing Gtk makes PyGObject open it, once; without one a
    widget then raises or crashes, while a later Gtk.init_check() still answers True."""
    try:
        import gi

        gi.require_version("Gtk", "4.0")
        gi.require_version("Gdk", "4.0")
        from gi.repository import Gdk, Gtk  # noqa: F401 - importing Gtk opens the display
    except (ImportError, ValueError):
        return False
    if Gdk.Display.get_default() is None:
        return False
    # The widgets in libadwaita's style, as the program's Adw.Application has them (e.g. the
    # size of the texts of a title or a caption): the same for every test, whatever its order.
    gi.require_version("Adw", "1")
    from gi.repository import Adw

    Adw.init()
    return True


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "needs_display: makes GTK widgets; skipped when there is no display")
    config.addinivalue_line(
        "markers", "real_volume_monitor: the session's Gio.VolumeMonitor instead of the quiet "
                   "one of the tests (it lists the mounts of this computer)")


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item):
    # Before any fixture: a fixture (e.g. window_device) may make the widgets.
    if item.get_closest_marker("needs_display") and not display_available():
        pytest.skip("no display")


# --- no disk of the computer running the tests is a Garmin device ---------------------------


@pytest.fixture(scope="session")
def _empty_sys_class_block(tmp_path_factory):
    return tmp_path_factory.mktemp("sys-class-block")


@pytest.fixture(autouse=True)
def no_block_devices(_empty_sys_class_block, monkeypatch):
    """/sys/class/block is an empty folder, unless a test makes its own: a USB drive plugged
    into this computer is never taken for a Garmin device in mass storage mode."""
    try:
        from openexpress.device import gio_storage
    except (ImportError, ValueError):  # no PyGObject: nothing reads /sys/class/block
        return
    monkeypatch.setattr(gio_storage, "SYSFS_BLOCK", _empty_sys_class_block)


@pytest.fixture(scope="session")
def _empty_sys_bus_usb(tmp_path_factory):
    return tmp_path_factory.mktemp("sys-bus-usb")


@pytest.fixture(autouse=True)
def no_usb_devices(_empty_sys_bus_usb, monkeypatch):
    """/sys/bus/usb/devices is an empty folder, unless a test makes its own: a Garmin device
    plugged into this computer while the tests run is never looked for on USB."""
    try:
        from openexpress.device import gio_storage
    except (ImportError, ValueError):  # no PyGObject: nothing reads /sys/bus/usb
        return
    monkeypatch.setattr(gio_storage, "SYSFS_USB", _empty_sys_bus_usb)


class QuietVolumeMonitor:
    """Gio.VolumeMonitor of the tests: no mounts, volumes or drives, and no signal of this
    computer; a test emits the signals it wants (`emit`), and the handlers get the monitor
    first, as GObject's do."""

    def __init__(self):
        self.handlers: dict[int, tuple[str, object, tuple]] = {}
        self._next = 0

    def get_mounts(self):
        return []

    def get_volumes(self):
        return []

    def get_connected_drives(self):
        return []

    def connect(self, signal, handler, *args):
        self._next += 1
        self.handlers[self._next] = (signal, handler, args)
        return self._next

    def disconnect(self, handler_id):
        self.handlers.pop(handler_id, None)

    handler_disconnect = disconnect

    def emit(self, signal, *values):
        for name, handler, args in list(self.handlers.values()):
            if name == signal:
                handler(self, *values, *args)


def stop_windows(monitor: QuietVolumeMonitor) -> None:
    """Every handler connected to the monitor is disconnected, and a window of the program
    among their owners has its timers removed (the USB poll, the texts told against now): a
    window a test made does nothing once its test has ended."""
    from gi.repository import GLib

    context = GLib.MainContext.default()
    for handler_id, (_signal, handler, _args) in list(monitor.handlers.items()):
        win = getattr(handler, "__self__", None)
        for name in ("_usb_poll", "_relative_timer"):
            source_id = getattr(win, name, None)
            source = context.find_source_by_id(source_id) if source_id else None
            if source is not None and not source.is_destroyed():
                source.destroy()
        if getattr(win, "_relative_timer", None) is not None:
            win._relative_timer = None
        monitor.disconnect(handler_id)


@pytest.fixture(autouse=True)
def quiet_volume_monitor(request, monkeypatch):
    """Gio.VolumeMonitor.get() gives a QuietVolumeMonitor, one for each test: a Garmin device
    plugged in while the tests run is neither listed nor reported to a window of the tests,
    and the windows that listened to it stop when the test ends (stop_windows). A test marked
    real_volume_monitor gets the session's, and cleans up after itself."""
    if request.node.get_closest_marker("real_volume_monitor"):
        yield None
        return
    try:
        from gi.repository import Gio
    except (ImportError, ValueError):  # no PyGObject: no volume monitor
        yield None
        return
    monitor = QuietVolumeMonitor()
    monkeypatch.setattr(Gio.VolumeMonitor, "get", staticmethod(lambda: monitor))
    yield monitor
    stop_windows(monitor)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """No test reaches the network: a name lookup or a connection beyond this computer is
    refused and noted, and the test fails, also when the code under test catches the error
    (as the command line does). The tests' own servers listen on 127.0.0.1; the display and
    D-Bus use Unix sockets."""
    import socket

    tried = []
    lookup, connect, connect_ex = (socket.getaddrinfo, socket.socket.connect,
                                   socket.socket.connect_ex)

    def local(host) -> bool:
        host = str(host or "")
        return host in ("", "localhost", "::1") or host.startswith("127.")

    def getaddrinfo(host, *args, **kwargs):
        if not local(host):
            tried.append(str(host))
            raise socket.gaierror(socket.EAI_NONAME, "the tests do not reach the network")
        return lookup(host, *args, **kwargs)

    def refused(sock, address) -> bool:
        if sock.family in (socket.AF_INET, socket.AF_INET6) and not local(address[0]):
            tried.append(str(address[0]))
            return True
        return False

    def guarded_connect(self, address):
        if refused(self, address):
            raise OSError("the tests do not reach the network")
        return connect(self, address)

    def guarded_connect_ex(self, address):
        if refused(self, address):
            raise OSError("the tests do not reach the network")
        return connect_ex(self, address)

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", guarded_connect_ex)
    yield
    if tried:
        pytest.fail("the test tried to reach the network: " + ", ".join(sorted(set(tried))))


@pytest.fixture(autouse=True)
def gvfs_has_mtp(monkeypatch):
    """No test asks the real gvfs which backends it has (#13): it has MTP, unless a test
    says otherwise, and nothing of it has been logged yet."""
    try:
        from openexpress.device import gio_storage
    except (ImportError, ValueError):  # no PyGObject: nothing asks gvfs
        return
    import types

    monkeypatch.setattr(gio_storage, "_vfs", lambda: types.SimpleNamespace(
        get_supported_uri_schemes=lambda: ["file", "mtp"]), raising=False)
    monkeypatch.setattr(gio_storage, "_no_mtp_told", False, raising=False)


@pytest.fixture(autouse=True)
def no_drive_ejected(monkeypatch):
    """openExpress remembers the Garmin drives it ejected for as long as it runs: every test
    starts with none."""
    try:
        from openexpress.device import gio_storage
    except (ImportError, ValueError):  # no PyGObject: nothing is ejected
        return
    monkeypatch.setattr(gio_storage, "_ejected", [])


@pytest.fixture(autouse=True)
def no_kinds_heard(monkeypatch):
    """What Garmin said of the kinds of models (and of their map update types) is kept for as
    long as openExpress runs: every test starts with nothing heard."""
    try:
        from openexpress import devicelist
    except (ImportError, ValueError):  # no PyGObject: nothing hears it
        return
    monkeypatch.setattr(devicelist, "_kind_evidence", {})
    monkeypatch.setattr(devicelist, "_kind_told", set())
    monkeypatch.setattr(devicelist, "_map_type_told", set())

DEVICE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<Device xmlns="http://www.garmin.com/xmlschemas/GarminDevice/v2">
 <Model><PartNumber>006-B0000-00</PartNumber><SoftwareVersion>2339</SoftwareVersion>
  <Description>Test watch</Description></Model>
 <Id>1234567890</Id>
 <MassStorageMode>
  <DataType><Name>FIT_TYPE_4</Name>
   <File><Location><Path>Garmin/Activity</Path><FileExtension>FIT</FileExtension></Location>
    <TransferDirection>OutputFromUnit</TransferDirection></File>
   <File><Location><Path>Garmin/NewFiles</Path><FileExtension>FIT</FileExtension></Location>
    <TransferDirection>InputToUnit</TransferDirection></File>
  </DataType>
  <DataType><Name>FIT_TYPE_32</Name>
   <File><Location><Path>Garmin/Monitor</Path><FileExtension>FIT</FileExtension></Location>
    <TransferDirection>OutputFromUnit</TransferDirection></File>
  </DataType>
  <DataType><Name>FIT_TYPE_2</Name>
   <File><Location><Path>Garmin/Settings</Path><FileExtension>FIT</FileExtension></Location>
    <TransferDirection>OutputFromUnit</TransferDirection></File>
  </DataType>
  <DataType><Name>FIT_TYPE_6</Name>
   <File><Location><Path>Garmin/NewFiles</Path><FileExtension>FIT</FileExtension></Location>
    <TransferDirection>InputToUnit</TransferDirection></File>
  </DataType>
  <DataType><Name>IQWatchApps</Name>
   <File><Location><Path>GARMIN/APPS/MEDIA</Path><FileExtension>PRG</FileExtension></Location>
    <TransferDirection>InputOutput</TransferDirection></File>
  </DataType>
  <UpdateFile><PartNumber>006-B0000-10</PartNumber><Version><Major>23</Major><Minor>39</Minor></Version>
   <Path>Garmin</Path><FileName>GUPDATE.GCD</FileName></UpdateFile>
 </MassStorageMode>
</Device>
"""


def make_fit(time_created: int, file_type: int = 4) -> bytes:
    """Simplest valid FIT file with a file_id message."""
    definition = bytes([0x40, 0, 0]) + struct.pack("<H", 0) + bytes([2, 0, 1, 0x00, 4, 4, 0x86])
    data = bytes([0x00, file_type]) + struct.pack("<I", time_created)
    body = definition + data
    header = struct.pack("<BBHI4sH", 14, 0x20, 2132, len(body), b".FIT", 0)
    return header + body + b"\x00\x00"


@pytest.fixture(autouse=True)
def private_dirs(tmp_path, monkeypatch):
    """No test reads or writes the real data, settings or cache of openExpress: every test
    has its own (the log: `log_file`). The same folders as `xdg` gives by name."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))


@pytest.fixture
def xdg(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    return tmp_path


@pytest.fixture(autouse=True)
def program_locale(monkeypatch):
    """cli.main may set LC_ALL for the program (i18n.ensure_utf8_locale): never beyond the
    test; nor the language names it keeps from before."""
    from openexpress import i18n

    monkeypatch.setattr(i18n, "_names_before", None, raising=False)
    before = os.environ.get("LC_ALL")
    yield
    if before is None:
        os.environ.pop("LC_ALL", None)
    else:
        os.environ["LC_ALL"] = before


@pytest.fixture(autouse=True)
def log_file(tmp_path, monkeypatch) -> Path:
    """No test writes to the real log file; the log set up by a test is closed after it."""
    from openexpress import applog

    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.delenv(applog.ENV, raising=False)
    monkeypatch.setattr(applog, "_hidden", {})
    monkeypatch.setattr(applog, "_media", {})
    yield tmp_path / "state" / "openexpress" / applog.LOG_NAME
    applog.close()


@pytest.fixture(autouse=True)
def network_state():
    """Every test starts with no answers of Garmin's servers noted, and no listeners."""
    from openexpress import netstatus

    netstatus.STATUS = netstatus.Status()
    yield netstatus.STATUS
    netstatus.STATUS = netstatus.Status()


@pytest.fixture(autouse=True)
def backups(tmp_path, monkeypatch) -> Path:
    """No test writes to the real backup folder: copies of the app list go here."""
    from openexpress import paths

    folder = tmp_path / "Backups"
    monkeypatch.setattr(paths, "default_backup_dir", lambda: folder)
    return folder


@pytest.fixture(autouse=True)
def usb_nodes(tmp_path, monkeypatch) -> Path:
    """No test looks at the real USB device nodes: /dev/bus/usb is an empty folder here."""
    folder = tmp_path / "dev-bus-usb"
    try:
        from openexpress.device import gio_storage
    except ImportError:  # without GObject nothing opens a USB device anyway
        return folder
    monkeypatch.setattr(gio_storage, "USB_NODES", folder)
    # Nor what an earlier test saw on USB (the places of Garmin drives, #19).
    monkeypatch.setattr(gio_storage, "_drive_places", {})
    monkeypatch.setattr(gio_storage, "_said_0003", {})
    monkeypatch.setattr(gio_storage, "_without_drive", frozenset())
    # Nor the connections whose error logs an earlier test read (diagnostics.at_open).
    from openexpress import diagnostics

    monkeypatch.setattr(diagnostics, "_done", {})
    return folder


# The moment of the made-up clock (`clock`): noon of a day far from a change of the clocks,
# so that a day back is "yesterday" at the same hour, whatever the computer's clock says.
NOON = datetime(2026, 10, 21, 12, 0)


class _Clock(datetime):
    """datetime with now() at the made-up moment `current` (naive, local time)."""

    current = NOON

    @classmethod
    def now(cls, tz=None):
        return cls.current if tz is None else cls.current.astimezone(tz)

    @classmethod
    def today(cls):
        return cls.current


@pytest.fixture
def clock(monkeypatch):
    """One made-up clock for a test that tells a time against now: datetime.now() of the
    window (gui.app, gui.device_page), of its words (gui.words), of i18n and of the device's
    error logs (diagnostics), and time.time(), all at
    `clock.current` (NOON; a test moves it: clock.current += timedelta(days=2)). The
    computer's clock then never decides the words (a day of 25 hours when the clocks go
    back, the first minute after midnight). A moment a day back is best told by the wall
    clock: (clock.current - timedelta(days=1)).timestamp()."""
    import importlib

    _Clock.current = NOON
    for name in ("openexpress.i18n", "openexpress.gui.words", "openexpress.gui.app",
                 "openexpress.gui.device_page", "openexpress.diagnostics"):
        try:
            module = importlib.import_module(name)
        except (ImportError, ValueError):  # no PyGObject: no window to tell the time
            continue
        monkeypatch.setattr(module, "datetime", _Clock)
    monkeypatch.setattr(time, "time", lambda: _Clock.current.timestamp())
    return _Clock


@pytest.fixture
def in_the_window(monkeypatch):
    """in_the_window(name, value): the name replaced in every module of the window that holds
    it (fakes.replace_in_window), not in gui.app alone; no test replaces a name in gui.app
    itself (guard 3, test_dead_patches)."""
    from fakes import replace_in_window

    return functools.partial(replace_in_window, monkeypatch)


@pytest.fixture
def device_root(tmp_path) -> Path:
    root = tmp_path / "device"
    g = root / "GARMIN"
    for d in ("Activity", "Monitor", "Settings", "NewFiles", "APPS/MEDIA"):
        (g / d).mkdir(parents=True)
    (g / "GarminDevice.xml").write_text(DEVICE_XML)
    (g / "Activity" / "2026-10-01-10-00-00.fit").write_bytes(make_fit(1100000000))
    (g / "Activity" / "2026-10-02-10-00-00.fit").write_bytes(make_fit(1100086400))
    (g / "Activity" / "notes.txt").write_text("not FIT")
    (g / "Monitor" / "M1.FIT").write_bytes(make_fit(1100000500, 32))
    (g / "Settings" / "Settings.fit").write_bytes(make_fit(1000, 2))
    return root


@pytest.fixture(autouse=True)
def plain_http_to_the_test_servers(monkeypatch):
    """The program downloads over HTTPS only; the tests' local servers speak plain HTTP."""
    from openexpress import download

    monkeypatch.setattr(download, "PLAIN_HTTP_HOSTS", frozenset({"127.0.0.1", "localhost"}))
