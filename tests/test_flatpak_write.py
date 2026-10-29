"""Writing to the watch from the Flatpak goes through the host's `gio copy`."""
import subprocess
import types
from pathlib import Path

import pytest

from openexpress.device import gio_storage
from openexpress.device.storage import DeviceError


class FakeTarget:
    def __init__(self, uri):
        self.uri = uri

    def get_uri(self):
        return self.uri

    def query_info(self, *a):
        """No file there before the copy (what Gio says of a target that does not exist)."""
        from gi.repository import Gio, GLib

        raise GLib.Error.new_literal(Gio.io_error_quark(), "not there",
                                     int(Gio.IOErrorEnum.NOT_FOUND))


@pytest.fixture
def flatpak(monkeypatch, xdg):
    # The cache of the test (its own, see conftest) is in the home folder, as the real one:
    # the host sees what is staged there.
    monkeypatch.setenv("HOME", str(xdg))
    calls = []

    def run(cmd, **kw):
        src = Path(cmd[-2])
        calls.append((cmd, src.read_bytes() if src.exists() else None))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(gio_storage, "in_flatpak", lambda: True)
    monkeypatch.setattr(gio_storage.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(gio_storage.subprocess, "run", run)
    st = gio_storage.GioStorage.__new__(gio_storage.GioStorage)
    st._f = lambda real: FakeTarget("mtp://watch/" + real)
    st._attempts = {}  # as __init__ makes it
    return st, calls


def test_write_bytes_uses_host_copy(flatpak):
    st, calls = flatpak
    st._write("GARMIN/NewFiles/x.FIT", b"DATA")
    cmd, content = calls[0]
    assert cmd[:4] == ["flatpak-spawn", "--host", "gio", "copy"]
    assert cmd[-1] == "mtp://watch/GARMIN/NewFiles/x.FIT"
    assert content == b"DATA"
    # The staging file is in a host-visible place and is removed afterwards.
    assert gio_storage._host_visible(Path(cmd[-2]))
    assert not Path(cmd[-2]).exists()


def test_write_file_stages_sandbox_private_files(flatpak, tmp_path, monkeypatch):
    st, calls = flatpak
    private = Path("/tmp") / f"oe-private-{tmp_path.name}.bin"
    private.write_bytes(b"MAP")
    try:
        monkeypatch.setattr(gio_storage, "_host_visible",
                            lambda p: not str(p).startswith("/tmp/"))
        st._write_file("GARMIN/map.img", private)
        cmd, content = calls[0]
        assert cmd[-2] != str(private) and content == b"MAP"
    finally:
        private.unlink()


@pytest.mark.parametrize("name", ["-x.mp3", "Name: x.mp3"])
def test_the_host_gets_the_file_by_its_whole_path(monkeypatch, tmp_path, name):
    """A file given as it was typed (`music add .` lists the folder by relative names): the
    host's gio would take "-x.mp3" for an option and "Name: x.mp3" for an address. It gets
    the whole path, the one _host_visible looked at."""
    commands = []
    monkeypatch.setattr(gio_storage.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(gio_storage.subprocess, "run", lambda cmd, **kw: (
        commands.append(cmd), subprocess.CompletedProcess(cmd, 0, "", ""))[1])
    (tmp_path / name).write_bytes(b"MP3")
    monkeypatch.chdir(tmp_path)
    gio_storage.host_copy(Path(name), "mtp://watch/Music/x.mp3", "Cannot write x.mp3")
    assert commands == [["flatpak-spawn", "--host", "gio", "copy",
                         str((tmp_path / name).resolve()), "mtp://watch/Music/x.mp3"]]


def test_host_copy_error_is_reported(flatpak, monkeypatch):
    st, _calls = flatpak
    monkeypatch.setattr(gio_storage.subprocess, "run",
                        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", "boom"))
    with pytest.raises(DeviceError, match="boom"):
        st._write("x.FIT", b"1")


# --- progress of a copy that runs on the host --------------------------------------------------

# What `gio copy -p` printed through flatpak-spawn for a 4 GB file (measured, Polish system):
# every update begins with a carriage return and an erase-line code, sizes are rounded and
# use a narrow no-break space, the first update has two different units, and the last line,
# with one size, comes twice.
CR = "\r\x1b[K"
MEASURED = [
    CR + "Skopiowano 1,0 MB z 4,0 GB (56 bajtów/s; średnio: 5,4 GB/s)",
    CR + "Skopiowano 2,1 GB z 4,0 GB (10,3 GB/s; średnio: 10,3 GB/s)",
    CR + "Skopiowano 3,3 GB z 4,0 GB (6,3 GB/s; średnio: 8,3 GB/s)",
    CR + "Skopiowano 4,0 GB (średnio: 8,1 GB/s)",
    CR + "Skopiowano 4,0 GB (średnio: 8,1 GB/s)",
    "\n",
]
SIZE = 4_000_000_000


@pytest.mark.parametrize("update, fraction", [
    (MEASURED[0], 1.0e6 / 4.0e9),            # two different units, in the first update
    (MEASURED[1], 2.1 / 4.0),
    (MEASURED[2], 3.3 / 4.0),
    ("\r\x1b[KCopied 1.0 MB out of 2.0 GB (63 bytes/s; average: 6.5 GB/s)", 0.0005),
    ("Copied 12.3 MB out of 51.0 MB (4.5 MB/s)", 12.3 / 51.0),
    ("Copied 512 bytes out of 3.0 kB (1.0 kB/s)", 512 / 3000),
    ("Copié 1,5 Mo sur 2,0 Go (3,0 Mo/s)", 1.5e6 / 2.0e9),    # units spelled another way
    ("Copied 4.0 GB out of 4.0 GB", 1.0),
    ("Copied 1.02 GB out of 1.0 GB", 1.0),                    # rounding: never above the whole
])
def test_fraction_is_the_first_size_over_the_second(update, fraction):
    assert gio_storage.copy_fraction(update) == pytest.approx(fraction)


@pytest.mark.parametrize("update", [
    MEASURED[3],                                 # the last line: one size, then a speed
    "Copied 4.0 GB (average: 13.5 GB/s)",
    "", "\x1b[K", "Copying…", "12 of 40",        # nothing, or no units
    "Скопировано 1,0 МБ из 4,0 ГБ",              # units in another alphabet: not guessed
    "Copied 5.0 GB out of 1.0 GB",               # more than the whole
    "Copied 1.0 MB out of 0 bytes",
    "Skopiowano 1,0 MB z 4",           # cut before the unit of the second size
])
def test_an_update_that_cannot_be_read_gives_nothing(update):
    assert gio_storage.copy_fraction(update) is None


class FakeProcess:
    """Stands in for the host's `gio copy -p`: writes `chunks` to its stdout one by one."""

    def __init__(self, chunks, code=0, stderr=b""):
        import io
        import os
        import threading

        read_end, write_end = os.pipe()
        self.stdout = os.fdopen(read_end, "rb", buffering=0)
        self.stderr = io.BytesIO(stderr)
        self.code, self.killed = code, False

        def feed():
            for chunk in chunks:
                os.write(write_end, chunk)
            os.close(write_end)

        threading.Thread(target=feed, daemon=True).start()

    def wait(self):
        return self.code

    def kill(self):
        self.killed = True


@pytest.fixture
def host(monkeypatch, tmp_path):
    """`host_copy` with a made-up host: `host.answer(chunks, …)` sets what gio prints."""
    local = tmp_path / "map.img"
    with open(local, "wb") as f:
        f.truncate(SIZE)  # a sparse file: only its size matters
    state = types.SimpleNamespace(local=local, commands=[], process=None)

    def popen(cmd, **kw):
        state.commands.append(cmd)
        state.process = FakeProcess(*state.args, **state.kwargs)
        return state.process

    def answer(chunks, **kwargs):
        state.args, state.kwargs = (chunks,), kwargs

    state.answer = answer
    monkeypatch.setattr(gio_storage.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(gio_storage.subprocess, "Popen", popen)
    monkeypatch.setattr(gio_storage.subprocess, "run",
                        lambda *a, **k: pytest.fail("the copy ran without -p"))
    return state


def copy_with_progress(host):
    seen = []
    gio_storage.host_copy(host.local, "mtp://watch/GARMIN/map.img", "Cannot write map.img",
                          lambda done, total: seen.append((done, total)))
    return seen


def test_progress_is_read_while_the_copy_runs(host):
    host.answer([u.encode() for u in MEASURED])
    seen = copy_with_progress(host)
    assert host.commands == [["flatpak-spawn", "--host", "gio", "copy", "-p", str(host.local),
                              "mtp://watch/GARMIN/map.img"]]
    # Three steps; the last line, with one size, reports nothing (the caller reports the end).
    assert seen == [(1_000_000, SIZE), (2_100_000_000, SIZE), (3_300_000_000, SIZE)]


@pytest.mark.parametrize("cut", [1, 3, 7, 16, 40, 41, 97])
def test_output_cut_anywhere_gives_the_same_progress(host, cut):
    # The pipe cuts in the middle of an update and in the middle of a character ("ó", the
    # narrow no-break space): every size of chunk has to give the same steps, in order.
    whole = "".join(MEASURED).encode()
    host.answer([whole[i:i + cut] for i in range(0, len(whole), cut)])
    seen = copy_with_progress(host)
    assert [done for done, _total in seen] == sorted({done for done, _total in seen})
    assert seen[-1] == (3_300_000_000, SIZE) and all(total == SIZE for _done, total in seen)
    assert {1_000_000, 2_100_000_000, 3_300_000_000} <= {done for done, _total in seen}


def test_the_carriage_return_alone_in_a_chunk(host):
    # As measured: the carriage return of the last line came on its own, the text after it.
    host.answer([MEASURED[1].encode(), b"\r", b"\x1b[K" + MEASURED[3][3:].encode() * 2, b"\n"])
    assert copy_with_progress(host) == [(2_100_000_000, SIZE)]


def test_progress_never_goes_back(host):
    host.answer([MEASURED[2].encode(), MEASURED[1].encode(), MEASURED[2].encode()])
    assert copy_with_progress(host) == [(3_300_000_000, SIZE)]


@pytest.mark.parametrize("chunks", [
    [],                                                   # gio prints nothing (a small file)
    [MEASURED[3].encode(), b"\n"],                        # only the last line
    ["\r\x1b[KСкопировано 1,0 МБ из 4,0 ГБ".encode()],     # a language that cannot be read
    [b"\xff\xfe garbage \x00\x01"],
])
def test_output_that_cannot_be_read_leaves_the_bar_alone(host, chunks):
    host.answer(chunks)
    assert copy_with_progress(host) == []  # no error, no progress: the copy goes on, no bar


def test_a_failed_copy_is_reported_with_what_gio_said(host):
    host.answer([MEASURED[0].encode()], code=1, stderr="gio: mtp://…: Brak miejsca\n".encode())
    with pytest.raises(DeviceError) as error:
        copy_with_progress(host)
    assert str(error.value) == "Cannot write map.img: gio: mtp://…: Brak miejsca"


def test_a_missing_permission_is_still_explained(host):
    host.answer([], code=1, stderr=b"Portal call failed: org.freedesktop.Flatpak not allowed")
    with pytest.raises(DeviceError) as error:
        copy_with_progress(host)
    assert "the Flatpak is missing the permission to copy files" in str(error.value)


def test_a_lot_of_error_output_does_not_block_the_copy(host):
    host.answer([u.encode() for u in MEASURED], code=1, stderr=b"x" * 2_000_000)
    with pytest.raises(DeviceError):
        copy_with_progress(host)


def test_a_copy_that_takes_too_long_is_stopped(host, monkeypatch):
    import threading

    monkeypatch.setattr(gio_storage, "HOST_COPY_TIMEOUT_S", 0.05)
    release = threading.Event()

    class Stuck(FakeProcess):
        def __init__(self):
            import io
            import os

            self.read_end, self.write_end = os.pipe()
            self.stdout = os.fdopen(self.read_end, "rb", buffering=0)
            self.stderr, self.code, self.killed = io.BytesIO(b""), -9, False

        def kill(self):
            import os

            self.killed = True
            os.close(self.write_end)  # the pipe closes when the process dies
            release.set()

    stuck = Stuck()
    monkeypatch.setattr(gio_storage.subprocess, "Popen", lambda cmd, **kw: stuck)
    with pytest.raises(DeviceError) as error:
        copy_with_progress(host)
    assert stuck.killed and "timed out" in str(error.value)


def test_without_a_progress_function_the_copy_runs_without_gio_progress(flatpak, tmp_path, monkeypatch):
    st, calls = flatpak
    monkeypatch.setattr(gio_storage.subprocess, "Popen",
                        lambda *a, **k: pytest.fail("-p without anyone to tell"))
    local = tmp_path / "x.bin"
    local.write_bytes(b"DATA")
    monkeypatch.setattr(gio_storage, "_host_visible", lambda p: True)
    st._write_file("GARMIN/x.bin", local)
    assert calls[0][0] == ["flatpak-spawn", "--host", "gio", "copy", str(local),
                           "mtp://watch/GARMIN/x.bin"]


def test_write_file_passes_the_progress_on_and_reports_the_end(host, monkeypatch):
    monkeypatch.setattr(gio_storage, "in_flatpak", lambda: True)
    monkeypatch.setattr(gio_storage, "_host_visible", lambda p: True)
    st = gio_storage.GioStorage.__new__(gio_storage.GioStorage)
    st._f = lambda real: FakeTarget("mtp://watch/" + real)
    st._attempts = {}  # as __init__ makes it
    host.answer([u.encode() for u in MEASURED])
    seen = []
    st._write_file("GARMIN/map.img", host.local, lambda done, total: seen.append((done, total)))
    assert seen == [(1_000_000, SIZE), (2_100_000_000, SIZE), (3_300_000_000, SIZE),
                    (SIZE, SIZE)]  # the end is reported once the copy has succeeded


def test_a_copy_whose_output_cannot_be_read_is_ended_and_reaped(host, monkeypatch):
    """The output of `gio copy` cannot be read: the process is killed and waited for, so
    that it is not left behind as a zombie."""

    class Unreadable(FakeProcess):
        def __init__(self):
            import io

            # No descriptor: reading it fails (EBADF), as a broken pipe would.
            self.stdout = types.SimpleNamespace(fileno=lambda: -1)
            self.stderr, self.code, self.killed = io.BytesIO(b""), -9, False
            self.waited = []

        def wait(self, timeout=None):
            self.waited.append(timeout)
            return self.code

    broken = Unreadable()
    monkeypatch.setattr(gio_storage.subprocess, "Popen", lambda cmd, **kw: broken)
    with pytest.raises(DeviceError, match="Cannot write map.img"):
        copy_with_progress(host)
    assert broken.killed and broken.waited == [5]
