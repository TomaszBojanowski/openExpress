"""tools/stress_device.py, the stress test of gvfs waits on a real device, without a device: it
refuses while openExpress runs, lets only an Edge 1050 run over 5 cycles, only reads (PTP
session, storage; no network), keeps its short time limit to its run and sums up how the device
settled. The device, its folders and answers are made up."""

import contextlib
import importlib.util
import socket
import sys
import types
from pathlib import Path

import pytest
from conftest import DEVICE_XML

from openexpress.device import gio_storage
from openexpress.device.storage import LocalStorage
from openexpress.paths import APP_ID

ROOT = Path(__file__).resolve().parents[1]
URI = "mtp://Garmin_Made_Up_0123456789ab/"
EDGE_XML = DEVICE_XML.replace("006-B0000-00", "006-B4440-00", 1)
FENIX_XML = DEVICE_XML.replace("006-B0000-00", "006-B4532-00", 1)


@pytest.fixture
def stress(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location("stress_device",
                                                  ROOT / "tools" / "stress_device.py")
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "stress_device", module)  # as when it runs
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "_bus_has_owner", lambda name: False)
    proc = tmp_path / "proc"
    proc.mkdir()
    monkeypatch.setattr(module, "PROC", proc)
    return module


class Device:
    """A made-up MTP device: mounted or not, unmounted and mounted by the made-up gio_storage
    calls; a PTP session of its own unmounts it, reads, and mounts it again."""

    def __init__(self, xml=EDGE_XML):
        self.xml, self.mounted, self.asked = xml.encode(), True, []

    def found(self):
        return [gio_storage.FoundDevice(URI, "Made up", self.mounted, object())]

    def unmount(self, uri):
        self.asked.append(("unmount", gio_storage.UNMOUNT_TIMEOUT_S))
        self.mounted = False

    def mount(self, found, timeout_s=None):
        self.asked.append(("mount",))
        self.mounted = True

    def remount(self, uri, storage, sleep=None):
        self.asked.append(("mount again",))
        self.mounted = True

    def storage(self, uri=None, **_kw):
        device = self

        class Storage:
            def read(self, path):
                assert path == "GARMIN/GarminDevice.xml"
                return device.xml

            def exists(self, path):
                return device.mounted

            def free_space(self):
                return 1_000

            @contextlib.contextmanager
            def exclusive_ptp(self):
                gio_storage._unmount(URI)
                try:
                    yield Session()
                finally:
                    gio_storage._remount(URI, self)

        return Storage()


class Session:
    def storages(self):
        return [1]

    def lookup(self, store, path):
        return types.SimpleNamespace(is_dir=path == "GARMIN", size=0 if path == "GARMIN" else 9)

    def send_file(self, *a, **kw):
        raise AssertionError("written")

    delete = read_file = transact = send_file


@pytest.fixture
def device(monkeypatch):
    made_up = Device()
    monkeypatch.setattr(gio_storage, "find_devices", made_up.found)
    monkeypatch.setattr(gio_storage, "_unmount", made_up.unmount)
    monkeypatch.setattr(gio_storage, "mount", made_up.mount)
    monkeypatch.setattr(gio_storage, "_remount", made_up.remount)
    monkeypatch.setattr(gio_storage, "open_device_storage", made_up.storage)
    return made_up


# --- it refuses -------------------------------------------------------------------------------


def test_it_refuses_while_the_window_or_the_service_runs(stress, monkeypatch, capsys):
    monkeypatch.setattr(stress, "_bus_has_owner", lambda name: name in (APP_ID, APP_ID + ".Watch"))
    monkeypatch.setattr(gio_storage, "find_devices", lambda: pytest.fail("a device looked for"))
    assert stress.main(["--cycles", "1"]) == 2
    said = capsys.readouterr().err
    assert "the window of openExpress" in said and "the background service of openExpress" in said


def test_it_refuses_while_another_openexpress_process_runs(stress, monkeypatch, capsys):
    (stress.PROC / "123").mkdir()
    (stress.PROC / "123" / "cmdline").write_bytes(b"/usr/bin/python3\0-m\0openexpress\0info\0")
    (stress.PROC / "124").mkdir()
    (stress.PROC / "124" / "cmdline").write_bytes(b"bash\0")
    (stress.PROC / "125").mkdir()
    (stress.PROC / "125" / "cmdline").write_bytes(b"/app/bin/python3\0/app/bin/openexpress\0")
    monkeypatch.setattr(gio_storage, "find_devices", lambda: pytest.fail("a device looked for"))
    assert stress.main([]) == 2
    said = capsys.readouterr().err
    assert "PID 123" in said and "PID 125" in said and "PID 124" not in said


def test_only_the_edge_1050_may_have_long_runs(stress, device, monkeypatch, capsys):
    assert stress.cycle_limit("006-B4440-00") == 200
    assert stress.cycle_limit("006-B4532-00") == stress.cycle_limit("006-B0000-00") == 5
    device.xml = FENIX_XML.encode()
    assert stress.main(["--cycles", "6"]) == 2
    assert "at most 5 cycles" in capsys.readouterr().err
    assert device.asked == []  # nothing done to it


def test_the_fenix_is_warned_about(stress, device, monkeypatch, capsys):
    device.xml = FENIX_XML.encode()
    waited = []
    monkeypatch.setattr(stress, "WARNING_PAUSE_S", 0)
    monkeypatch.setattr(stress.time, "sleep", lambda s: waited.append(s))
    assert stress.main(["--cycles", "1", "--path", "cli", "--settle", "0"]) == 0
    assert "RTL Trap" in capsys.readouterr().out


def test_a_device_not_mounted_is_refused(stress, device, capsys):
    device.mounted = False
    assert stress.main([]) == 2
    assert "not mounted" in capsys.readouterr().err


# --- it only reads ----------------------------------------------------------------------------


def test_its_ptp_session_only_reads(stress):
    session = stress.ReadOnlySession(Session())
    assert session.storages() == [1]
    for name in ("send_file", "delete", "read_file", "transact"):
        with pytest.raises(stress.ReadOnlyRefused):
            getattr(session, name)


def test_its_storage_refuses_every_write(stress, tmp_path):
    root = tmp_path / "device"
    (root / "GARMIN").mkdir(parents=True)
    (root / "GARMIN" / "x.bin").write_bytes(b"as it was")
    storage = stress.guard_storage(LocalStorage(root))
    with pytest.raises(stress.ReadOnlyRefused):
        storage.write("GARMIN/x.bin", b"x")
    with pytest.raises(stress.ReadOnlyRefused):
        storage.delete("GARMIN/x.bin")
    with pytest.raises(stress.ReadOnlyRefused):
        storage.write("NEW/y.bin", b"y")
    assert (root / "GARMIN" / "x.bin").read_bytes() == b"as it was"
    assert sorted(p.name for p in root.iterdir()) == ["GARMIN"]


def test_the_network_is_closed_to_it(stress, tmp_path):
    lookup = socket.getaddrinfo
    with stress.network_closed():
        with pytest.raises(OSError):
            socket.create_connection(("example.org", 443))
        with pytest.raises(OSError):
            socket.getaddrinfo("example.org", 443)
        with socket.socket(socket.AF_UNIX) as unix, pytest.raises(FileNotFoundError):
            unix.connect(str(tmp_path / "none"))  # a Unix socket goes through (D-Bus, gvfs)
    assert socket.getaddrinfo is lookup


# --- a run ------------------------------------------------------------------------------------


def test_two_cycles_one_in_each_way(stress, device, capsys):
    assert stress.main(["--cycles", "2", "--settle", "0"]) == 0
    said = capsys.readouterr().out
    assert "Device: Test watch (006-B4440-00)" in said
    assert "Cycles: 2 of 2" in said and "Errors: 0" in said
    assert [a[0] for a in device.asked] == ["unmount", "mount again", "unmount", "mount"] * 2
    for step in ("unmount", "mount again", "mount", "open", "cycle"):
        assert f"  {step}: " in said
    assert device.mounted


def test_an_error_is_told_without_the_address_of_the_device(stress, device, monkeypatch,
                                                            capsys):
    def busy(uri):
        raise gio_storage.DeviceError(f"Cannot unmount the device: {URI} is busy")

    monkeypatch.setattr(gio_storage, "_unmount", busy)
    assert stress.main(["--cycles", "1", "--path", "cli", "--settle", "0"]) == 1
    said = capsys.readouterr().out
    assert "Errors: 2" in said and "0123456789ab" not in said


def test_the_short_time_limit_is_for_its_run_only(stress, device):
    before = gio_storage.UNMOUNT_TIMEOUT_S
    assert stress.main(["--cycles", "1", "--path", "cli", "--settle", "0",
                        "--short-timeout", "0.05"]) == 0
    assert ("unmount", 0.05) in device.asked
    assert gio_storage.UNMOUNT_TIMEOUT_S == before == 30.0


def test_the_summary_says_how_the_device_settled(stress):
    """After an unmount stopped at its time limit: which answer came, how the
    device settled, whether it was mounted again or did not come back; from the log lines of
    gio_storage."""
    import logging

    limits = stress.Limits()
    log = logging.getLogger(gio_storage.__name__)
    for level, line in (
            (logging.WARNING, ("gio: unmount: no answer in 0.001 s, cancelled; its answer: "
                               "cancelled (our wait ended, gvfs may go on)")),
            (logging.INFO, ("PTP: the unmount did not finish in time: seeing how the device "
                            "settles")),
            (logging.INFO, "PTP: the device is unmounted (after 2.1 s)"),
            (logging.INFO, "PTP: mounting the device again (try 1 of 3)"),
            (logging.INFO, "PTP: the device is mounted (after 2.0 s)"),
            (logging.INFO, "PTP: the device is not settled (after 15.0 s)"),
            (logging.INFO, "PTP: the device did not come back")):
        limits.handle(log.makeRecord(log.name, level, __file__, 1, line, (), None))
    assert dict(limits.seen) == {
        "unmount: cancelled, its answer: cancelled": 1,
        "PTP: the device settled unmounted": 1,
        "PTP: mounted again (try 1)": 1,
        "PTP: the device settled mounted": 1,
        "PTP: the device did not settle": 1,
        "PTP: the device did not come back": 1}
