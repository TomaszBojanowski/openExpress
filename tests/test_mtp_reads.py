"""Reads from a device over MTP (GioStorage): each step, one answer of gvfs, is waited for at
most STALL_S. A device that gives no answer by then stopped responding: the read ends with
DeviceNotResponding, which says to unplug the device and plug it in again, and which the
device layer never takes for a missing file. The device and gvfs are made up
(gio_fake.MtpFiles); the last test reads a folder on the disk through the real Gio."""

import logging
import re
import time

import pytest
from fakes import STALLED, in_language
from gio_fake import MTIME, GioOf, GioOp, GvfsDevice, MtpFiles, Volume, in_a_worker, settle

from openexpress.device import storage

gio_storage = pytest.importorskip("openexpress.device.gio_storage")

LIMIT = 0.15
GRACE = 0.1
LOGGER = "openexpress.device.gio_storage"
URI = "mtp://Made_Up_Device/"
CHUNK = 256 * 1024  # what a file is read in: the most gvfs reads at a time


def a_file(size):
    """Bytes that tell their places apart."""
    return (bytes(range(251)) * (size // 251 + 1))[:size]


@pytest.fixture
def fast(monkeypatch):
    """Time limits of a fraction of a second."""
    monkeypatch.setattr(gio_storage, "STALL_S", LIMIT, raising=False)
    monkeypatch.setattr(gio_storage, "CANCEL_WAIT_S", GRACE)


@pytest.fixture
def device():
    """make(tree, space, plan): the storage of a made-up device over MTP, and its files
    (MtpFiles); answers still due are taken away after the test."""
    made = []

    def make(tree, space=None, plan=None, most=None):
        files = MtpFiles(tree, space, plan, most)
        made.append(files)
        st = gio_storage.GioStorage(URI + "Internal")
        st.root = files.file()
        return st, files

    yield make
    for files in made:
        files.clean()


def run_in(where, fn):
    """fn() in the main thread (the command line) or in a worker thread (a task of the
    window); its value, or its error raised."""
    if where == "main":
        return fn()
    worker, said = in_a_worker(fn)
    settle(worker)
    assert not worker.is_alive(), "the read never ended"
    assert said["thread default"] is None  # its own context taken off again
    if "error" in said:
        raise said["error"]
    return said["value"]


def activities(count):
    return {"GARMIN/Activity": None} | {f"GARMIN/Activity/{n:03d}.fit": 1000 + n
                                         for n in range(count)}


# --- listings ------------------------------------------------------------------------------


@pytest.mark.parametrize("where", ["main", "worker"])
@pytest.mark.parametrize("count", [0, 1, 100, 250])
def test_a_listing_in_portions_gives_every_entry(device, fast, where, count):
    """In a worker every portion is waited for in the context where the listing began: gvfs
    gives its entries only there."""
    st, files = device(activities(count))
    entries = run_in(where, lambda: st.listdir("garmin/activity"))
    assert sorted(entries, key=lambda e: e.name) == [storage.Entry(f"{n:03d}.fit", False, 1000 + n, MTIME)
                               for n in range(count)]
    portions = -(-count // gio_storage.LIST_PORTION) + 1  # the last one empty: the end
    assert files.calls[-(portions + 2):] == ["list"] + ["portion"] * portions + ["close"]


def test_a_listing_slower_in_all_than_the_limit_ends_well(device, fast):
    """The limit counts from the last answer: every portion comes in time, all of them
    together take longer than the limit."""
    plan = {("portion", n): GioOp("before", delay_s=LIMIT / 2) for n in range(1, 5)}
    st, _files = device(activities(300), plan=plan)
    started = time.monotonic()
    assert len(run_in("worker", lambda: st._list("GARMIN/Activity"))) == 300
    assert time.monotonic() - started > LIMIT


NO_ANSWER = {
    "never": lambda: GioOp("never", strict=True),
    "cancelled": lambda: GioOp("on cancel", delay_s=0.05, strict=True),
    "refused after the cancel": lambda: GioOp("on cancel", on_cancel="error", delay_s=0.05,
                                              strict=True),
}


@pytest.mark.parametrize("how", NO_ANSWER)
@pytest.mark.parametrize("step", [("list", 1), ("portion", 1), ("portion", 2), ("close", 1)])
def test_a_listing_step_with_no_answer_is_a_device_that_stopped_responding(device, fast, step,
                                                                          how):
    """A step of a listing (opening it, a portion, closing it) with no answer within the
    limit: whatever comes after the cancel, the device stopped responding. A listing that
    stalled is not closed: that would wait on the same device."""
    op = NO_ANSWER[how]()
    st, files = device(activities(150), plan={step: op})
    started = time.monotonic()
    with pytest.raises(storage.DeviceNotResponding) as error:
        run_in("worker", lambda: st._list("GARMIN/Activity"))
    took = time.monotonic() - started
    assert str(error.value) == f"Cannot list GARMIN/Activity: {STALLED}"
    assert LIMIT - 0.05 <= took < LIMIT + GRACE + 1
    assert op.was_cancelled
    assert files.calls.count("close") == (1 if step[0] == "close" else 0)


@pytest.mark.parametrize("step", [("list", 1), ("portion", 1)])
def test_a_refused_listing_says_why(device, step):
    st, files = device(activities(3), plan={step: GioOp(says="error")})
    with pytest.raises(storage.DeviceError) as error:
        st._list("GARMIN/Activity")
    assert str(error.value) == "Cannot list GARMIN/Activity: made-up refusal"
    assert not isinstance(error.value, storage.DeviceNotResponding)
    assert files.calls.count("close") == (1 if step[0] == "portion" else 0)  # once open


def test_a_listing_whose_close_is_refused_is_whole(device):
    st, _files = device(activities(3), plan={("close", 1): GioOp(says="error")})
    assert len(st._list("GARMIN/Activity")) == 3


# --- queries and the space -----------------------------------------------------------------


def query(st, read, path):
    return st.info(path) if read == "info" else st._size_now(path)


@pytest.mark.parametrize("read", ["info", "size now"])
def test_a_query_with_no_answer_is_a_device_that_stopped_responding(device, fast, read):
    st, _files = device({"GARMIN/x.fit": 10}, plan={("query", 1): GioOp("never", strict=True)})
    with pytest.raises(storage.DeviceNotResponding) as error:
        run_in("worker", lambda: query(st, read, "GARMIN/x.fit"))
    assert str(error.value) == f"Cannot read GARMIN/x.fit: {STALLED}"


@pytest.mark.parametrize("where", ["main", "worker"])
def test_a_query_says_what_is_there(device, fast, where):
    st, _files = device({"GARMIN/x.fit": 10})
    assert run_in(where, lambda: [
        st.info("GARMIN/x.fit"), st.info("GARMIN"), st._size_now("GARMIN/x.fit"),
        st.info("GARMIN/y.fit"), st._size_now("GARMIN/y.fit")]) == [
        storage.Entry("x.fit", False, 10, MTIME), storage.Entry("GARMIN", True, 0, MTIME), 10,
        None, None]  # a file that is not there: None


def test_a_refused_query_says_why(device):
    plan = {("query", 1): GioOp(says="error"), ("query", 2): GioOp(says="error")}
    st, _files = device({"GARMIN/x.fit": 10}, plan=plan)
    with pytest.raises(storage.DeviceError) as error:
        st.info("GARMIN/x.fit")
    assert str(error.value) == "Cannot read GARMIN/x.fit: made-up refusal"
    assert not isinstance(error.value, storage.DeviceNotResponding)
    assert st._size_now("GARMIN/x.fit") is gio_storage._UNKNOWN  # its size not known


@pytest.mark.parametrize("read", ["free_space", "capacity"])
def test_the_space_with_no_answer_is_a_device_that_stopped_responding(device, fast, read):
    st, _files = device({}, space={"filesystem::free": 5, "filesystem::size": 9},
                        plan={("space", 1): GioOp("never", strict=True)})
    with pytest.raises(storage.DeviceNotResponding) as error:
        run_in("worker", getattr(st, read))
    assert str(error.value) == f"Cannot read the space on the device: {STALLED}"


def test_the_space_as_the_device_says_it(device):
    st, _files = device({}, space={"filesystem::free": 0, "filesystem::size": 1000})
    assert st.free_space() == 0 and st.capacity() == 1000
    st, _files = device({}, space={})
    assert st.free_space() is None and st.capacity() is None  # not said: not known
    st, _files = device({}, space={"filesystem::size": 0})
    assert st.capacity() is None  # a size of 0: not known
    st, _files = device({}, plan={("space", 1): GioOp(says="error")})
    assert st.free_space() is None  # refused: not known


# --- files ---------------------------------------------------------------------------------


@pytest.mark.parametrize("where", ["main", "worker"])
@pytest.mark.parametrize("size", [0, 1000, 2 * CHUNK, 5 * CHUNK + 7])
def test_a_file_is_read_whole_in_chunks(device, fast, where, size):
    data = a_file(size)
    st, files = device({"GARMIN/x.fit": data})
    assert run_in(where, lambda: st.read("GARMIN/x.fit")) == data
    assert gio_storage.READ_CHUNK == CHUNK
    chunks = -(-size // CHUNK) + 1  # the last one empty: the end
    assert files.asked == [CHUNK] * chunks
    assert files.calls[-(chunks + 2):] == ["open"] + ["chunk"] * chunks + ["close file"]


def test_a_file_given_in_smaller_pieces_is_read_whole(device, fast):
    """gvfs may give less than asked for: the read goes on to an empty answer."""
    data = a_file(2 * CHUNK)
    st, files = device({"GARMIN/x.fit": data}, most=CHUNK // 2 + 1)
    assert run_in("worker", lambda: st.read("GARMIN/x.fit")) == data
    assert files.calls.count("chunk") == 5  # four pieces, then the end


def test_a_read_slower_in_all_than_the_limit_ends_well(device, fast):
    """The limit counts from the last answer: every chunk comes in time, all of them together
    take longer than the limit."""
    plan = {("chunk", n): GioOp("before", delay_s=LIMIT / 2) for n in range(1, 6)}
    data = a_file(4 * CHUNK)
    st, _files = device({"GARMIN/x.fit": data}, plan=plan)
    started = time.monotonic()
    assert run_in("worker", lambda: st.read("GARMIN/x.fit")) == data
    assert time.monotonic() - started > LIMIT


@pytest.mark.parametrize("how", NO_ANSWER)
@pytest.mark.parametrize("step", [("open", 1), ("chunk", 1), ("chunk", 3), ("close file", 1)])
def test_a_read_step_with_no_answer_is_a_device_that_stopped_responding(device, fast, step,
                                                                       how):
    """A step of a read (opening the file, a chunk, closing it) with no answer within the
    limit: the device stopped responding. A file whose read stalled is not closed."""
    op = NO_ANSWER[how]()
    st, files = device({"GARMIN/x.fit": a_file(3 * CHUNK)}, plan={step: op})
    started = time.monotonic()
    with pytest.raises(storage.DeviceNotResponding) as error:
        run_in("worker", lambda: st.read("GARMIN/x.fit"))
    took = time.monotonic() - started
    assert str(error.value) == f"Cannot read GARMIN/x.fit: {STALLED}"
    assert LIMIT - 0.05 <= took < LIMIT + GRACE + 1
    assert op.was_cancelled
    assert files.calls.count("close file") == (1 if step[0] == "close file" else 0)


@pytest.mark.parametrize("step", [("open", 1), ("chunk", 2)])
def test_a_refused_read_says_why(device, step):
    st, files = device({"GARMIN/x.fit": a_file(3 * CHUNK)}, plan={step: GioOp(says="error")})
    with pytest.raises(storage.DeviceError) as error:
        st.read("GARMIN/x.fit")
    assert str(error.value) == "Cannot read GARMIN/x.fit: made-up refusal"
    assert not isinstance(error.value, storage.DeviceNotResponding)
    assert files.calls.count("close file") == (1 if step[0] == "chunk" else 0)  # once open


def test_a_read_whose_close_is_refused_is_whole(device):
    st, _files = device({"GARMIN/x.fit": a_file(10)}, plan={("close file", 1): GioOp(says="error")})
    assert st.read("GARMIN/x.fit") == a_file(10)


@pytest.mark.parametrize("count, asked", [(512, [512]), (CHUNK + 10, [CHUNK, 10]),
                                          (3 * CHUNK + 1, [CHUNK, CHUNK, CHUNK, 1])])
def test_read_start_reads_its_count_only(device, fast, count, asked):
    data = a_file(3 * CHUNK + 100)
    st, files = device({"Garmin/x.img": data})
    assert run_in("worker", lambda: st.read_start("Garmin/x.img", count)) == data[:count]
    assert files.asked == asked and files.calls[-1] == "close file"


def test_read_start_asks_for_what_is_still_missing(device):
    """A start that comes in two pieces."""
    st, files = device({"Garmin/x.img": a_file(600)}, most=300)
    assert st.read_start("Garmin/x.img", 512) == a_file(512)
    assert files.asked == [512, 212] and files.calls[-1] == "close file"


def test_read_start_of_a_shorter_file_gives_all_of_it(device):
    st, files = device({"Garmin/x.img": b"abc"})
    assert st.read_start("Garmin/x.img", 512) == b"abc"
    assert files.asked == [512, 509] and files.calls[-1] == "close file"


@pytest.mark.parametrize("close", ["done", "error"])
def test_read_start_closes_the_file_after_an_error(device, close):
    plan = {("chunk", 2): GioOp(says="error")}
    if close == "error":
        plan[("close file", 1)] = GioOp(says="error")
    st, files = device({"Garmin/x.img": a_file(600)}, plan=plan, most=10)
    with pytest.raises(storage.DeviceError) as error:
        st.read_start("Garmin/x.img", 512)
    assert str(error.value) == "Cannot read Garmin/x.img: made-up refusal"
    assert files.asked == [512, 502] and files.calls[-1] == "close file"


def test_read_start_of_a_file_that_cannot_be_opened(device):
    st, files = device({"Garmin/x.img": b"abc"}, plan={("open", 1): GioOp(says="error")})
    with pytest.raises(storage.DeviceError, match="Cannot read Garmin/x.img: made-up refusal"):
        st.read_start("Garmin/x.img", 512)
    assert files.asked == [] and "close file" not in files.calls


def test_read_start_stops_at_a_stall(device, fast):
    st, files = device({"Garmin/x.img": a_file(3 * CHUNK)},
                       plan={("chunk", 2): GioOp("never", strict=True)})
    with pytest.raises(storage.DeviceNotResponding) as error:
        run_in("worker", lambda: st.read_start("Garmin/x.img", 2 * CHUNK))
    assert str(error.value) == f"Cannot read Garmin/x.img: {STALLED}"
    assert files.asked == [CHUNK, CHUNK] and "close file" not in files.calls


# --- the error -----------------------------------------------------------------------------


def test_its_words_say_what_to_do_once():
    error = storage.DeviceNotResponding.during("Cannot list GARMIN")
    assert isinstance(error, storage.DeviceError)
    assert str(error) == f"Cannot list GARMIN: {STALLED}"
    assert storage.replug_text(error) == str(error)  # the advice not added again


def test_its_words_in_polish():
    with in_language("pl"):
        error = storage.DeviceNotResponding.during("Nie można odczytać GARMIN/x.fit")
        said, replug = str(error), storage.replug_text(error)
    assert said == ("Nie można odczytać GARMIN/x.fit: urządzenie przestało odpowiadać. Odłącz "
                    "je i\u00a0podłącz ponownie.")
    assert replug == said


# --- the device layer passes it on -----------------------------------------------------------


class Stalled(storage.LocalStorage):
    """A storage whose listings reach their limit, as those of a device that stopped
    responding."""

    def _list(self, real_path):
        raise storage.DeviceNotResponding.during(f"Cannot list {real_path or '/'}")


class Refusing(storage.LocalStorage):
    def _list(self, real_path):
        raise storage.DeviceError(f"Cannot list {real_path or '/'}: made-up refusal")


CALLS = {
    "exists": lambda st: st.exists("GARMIN/GarminDevice.xml"),
    "listdir": lambda st: st.listdir("GARMIN/Activity"),
    "walk_files": lambda st: st.walk_files("GARMIN"),
    "read": lambda st: st.read("GARMIN/GarminDevice.xml"),
    "read_start": lambda st: st.read_start("GARMIN/GarminDevice.xml", 10),
    "size": lambda st: st.size("GARMIN/GarminDevice.xml"),
    "sizes": lambda st: st.sizes(["GARMIN/GarminDevice.xml"]),
    "delete": lambda st: st.delete("GARMIN/x.fit"),
}


@pytest.mark.parametrize("call", CALLS)
def test_a_device_that_stopped_responding_is_not_a_missing_file(tmp_path, call):
    with pytest.raises(storage.DeviceNotResponding):
        CALLS[call](Stalled(tmp_path))


def test_a_refused_listing_is_a_missing_file(tmp_path):
    st = Refusing(tmp_path)
    assert not st.exists("GARMIN/GarminDevice.xml")
    assert st.listdir("GARMIN") == [] and st.walk_files("GARMIN") == []
    assert st.size("GARMIN/x.fit") is None and st.sizes(["GARMIN/x.fit"]) == {
        "GARMIN/x.fit": None}
    with pytest.raises(storage.DeviceError, match="File not found on the device"):
        st.read("GARMIN/GarminDevice.xml")


class StalledDevice:
    """The storage of a device that stopped responding, as mounting it again looks at it."""

    usb = None  # its place on USB not known

    def __init__(self):
        self.asked = []

    def exists(self, path):
        self.asked.append(path)
        raise storage.DeviceNotResponding.during("Cannot list /")


def test_mounting_again_does_not_wait_on_a_device_that_stopped_responding(monkeypatch):
    """Its GarminDevice.xml is looked for once, not again and again for REMOUNT_READY_S."""
    gvfs = GvfsDevice(URI)  # mounted: mounting it again answers "mounted already"
    monkeypatch.setattr(gio_storage, "Gio", GioOf(gio_storage.Gio, gvfs))
    st, slept = StalledDevice(), []
    with pytest.raises(storage.DeviceNotResponding):
        gio_storage._remount(URI, st, sleep=slept.append)
    assert st.asked == ["GARMIN/GarminDevice.xml"] and slept == []


def test_the_state_of_a_device_that_stopped_responding_is_not_guessed(monkeypatch):
    monkeypatch.setattr(gio_storage, "Gio", GioOf(gio_storage.Gio, GvfsDevice(URI)))
    with pytest.raises(storage.DeviceNotResponding):
        gio_storage._device_state(URI, StalledDevice())


def test_a_refresh_stops_at_a_device_that_stopped_responding(monkeypatch):
    """Not "refresh not done": the device is not read again."""
    monkeypatch.setattr(gio_storage, "_quiet_until", {})
    monkeypatch.setattr(gio_storage, "_quiet_usb", {})
    monkeypatch.setattr(gio_storage, "_unmount", lambda uri: None)

    def mount_again(uri, st, sleep=None):
        raise storage.DeviceNotResponding.during("Cannot list /")

    monkeypatch.setattr(gio_storage, "_remount", mount_again)
    st = gio_storage.GioStorage(URI + "Internal")
    st.usb, st.device_uri = gio_storage.GarminUsb("51b4", 3, 14), URI
    with pytest.raises(storage.DeviceNotResponding):
        st.refresh()


@pytest.mark.parametrize("write", ["write", "write_file"])
def test_a_write_cleans_nothing_up_after_the_device_stopped_responding(device, fast, monkeypatch,
                                                                       tmp_path, write):
    """It stopped responding when the size of the file there was asked, before the copy:
    nothing was touched, and a clean-up would wait on the same device."""
    monkeypatch.setattr(gio_storage, "in_flatpak", lambda: False)
    st, files = device({"GARMIN/NewFiles/x.fit": 3},
                       plan={("query", 1): GioOp("never", strict=True)})
    copied = []
    monkeypatch.setattr(st, "_copy_local", lambda *a: copied.append(a))
    local = tmp_path / "x.fit"
    local.write_bytes(b"NEW")
    with pytest.raises(storage.DeviceNotResponding):
        run_in("worker", lambda: st.write("GARMIN/NewFiles/x.fit", b"NEW") if write == "write"
               else st.write_file("GARMIN/NewFiles/x.fit", local))
    assert copied == [] and files.calls.count("query") == 1


# --- after a stall: every operation fails at once ------------------------------------------


AFTER = {
    "listing": lambda st, local: st.listdir("GARMIN"),
    "query": lambda st, local: st.info("GARMIN/Activity/000.fit"),
    "size now": lambda st, local: st._size_now("GARMIN/Activity/000.fit"),
    "read": lambda st, local: st.read_at("GARMIN/Activity/000.fit"),
    "read start": lambda st, local: st._read_start("GARMIN/Activity/000.fit", 10),
    "free space": lambda st, local: st.free_space(),
    "capacity": lambda st, local: st.capacity(),
    "write": lambda st, local: st.write("GARMIN/NewFiles/x.fit", b"NEW"),
    "write a file": lambda st, local: st.write_file("GARMIN/NewFiles/x.fit", local),
    "bytes to a known path": lambda st, local: st._write("GARMIN/NewFiles/x.fit", b"NEW"),
    "a file to a known path": lambda st, local: st._write_file("GARMIN/NewFiles/x.fit", local),
    "delete": lambda st, local: st._delete("GARMIN/Activity/000.fit"),
    "make a folder": lambda st, local: st._mkdir("GARMIN/New"),
    "refresh": lambda st, local: st.refresh(),
    "PTP session": lambda st, local: st.exclusive_ptp(),
}


@pytest.mark.parametrize("operation", AFTER)
def test_after_a_stall_every_operation_of_the_storage_fails_at_once(device, fast, monkeypatch,
                                                                    tmp_path, operation):
    """gvfs stays blocked on the device until it is unplugged: nothing more is asked of it,
    a write neither (nothing copied, the device not unmounted), and the same error is said."""
    monkeypatch.setattr(gio_storage, "in_flatpak", lambda: False)
    monkeypatch.setattr(gio_storage, "_unmount", lambda uri: pytest.fail("unmounted"))
    st, files = device(activities(3), space={"filesystem::free": 5},
                       plan={("list", 1): GioOp("never", strict=True)})
    st.usb, st.device_uri = gio_storage.GarminUsb("51b4", 3, 14), URI
    with pytest.raises(storage.DeviceNotResponding) as first:
        run_in("worker", lambda: st._list("GARMIN/Activity"))
    copied = []
    monkeypatch.setattr(st, "_copy_local", lambda *a: copied.append(a))
    local = tmp_path / "x.fit"
    local.write_bytes(b"NEW")
    asked = list(files.calls)
    started = time.monotonic()
    with pytest.raises(storage.DeviceNotResponding) as again:
        run_in("worker", lambda: AFTER[operation](st, local))
    assert time.monotonic() - started < LIMIT
    assert str(again.value) == str(first.value) == f"Cannot list GARMIN/Activity: {STALLED}"
    assert files.calls == asked and copied == []


def test_a_new_storage_of_the_same_device_reads_as_usual(device, fast):
    """A device plugged in again is a new storage: what the old one kept does not stop it."""
    st, files = device(activities(3), plan={("list", 1): GioOp("never", strict=True)})
    with pytest.raises(storage.DeviceNotResponding):
        run_in("worker", lambda: st._list("GARMIN/Activity"))
    again = gio_storage.GioStorage(URI + "Internal")
    again.root = files.file()
    assert len(run_in("worker", lambda: again._list("GARMIN/Activity"))) == 3


# --- the log ---------------------------------------------------------------------------------


def logged(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == LOGGER]


MOUNTED = {
    "by this process": r"mounted by this process \d+\.\d s before",
    "before this process": r"mounted already when this process found it \d+\.\d s before",
    "not known": r"not known when it was mounted",
}


@pytest.mark.parametrize("before", ["a listing", "nothing"])
@pytest.mark.parametrize("mounted", MOUNTED)
def test_a_stall_logs_what_it_was_and_how_long_the_device_had_been_quiet(
        device, fast, monkeypatch, caplog, mounted, before):
    """What it was, how long the device had not been asked before it, how long the storage had
    been open and who mounted the device: never a path, a name or the device's address."""
    monkeypatch.setattr(gio_storage, "_mounted_here", {}, raising=False)
    monkeypatch.setattr(gio_storage, "_found_mounted", {}, raising=False)
    if mounted != "not known":
        gio_storage.mount(gio_storage.FoundDevice(URI, "made-up", mounted == "before this process",
                                                  Volume(GioOp())))
    st, _files = device({"GARMIN/Activity/x.fit": a_file(10)},
                        plan={("open", 1): GioOp("never", strict=True)})
    st.device_uri = URI
    if before == "a listing":
        run_in("worker", lambda: st._list("GARMIN/Activity"))
    with caplog.at_level(logging.DEBUG, logger=LOGGER), \
            pytest.raises(storage.DeviceNotResponding):
        run_in("worker", lambda: st._read("GARMIN/Activity/x.fit"))
    last = r"\d+\.\d s before this step" if before == "a listing" else "none yet"
    pattern = (rf"device: no answer to a read in {LIMIT:g} s, the device stopped responding; "
               rf"its last answer: {last}; the storage opened \d+\.\d s before this step; "
               + MOUNTED[mounted])
    said = logged(caplog)
    assert [line for line in said if re.fullmatch(pattern, line)], said
    assert not [line for line in said if "GARMIN" in line or "x.fit" in line or "Made_Up" in line]


@pytest.mark.parametrize(("plan", "read", "step"), [
    (("open", 1), lambda st: st._read("GARMIN/Activity/x.fit"), "read"),
    (("space", 1), lambda st: st.free_space(), "query of the free space")])
def test_a_stall_is_one_warning_that_names_its_step(device, fast, caplog, plan, read, step):
    """One warning says the stall, its step named as a step; what the cancel was answered is
    a line of information."""
    st, _files = device({"GARMIN/Activity/x.fit": a_file(10)},
                        space={"filesystem::free": 5}, plan={plan: GioOp("never", strict=True)})
    with caplog.at_level(logging.DEBUG, logger=LOGGER), \
            pytest.raises(storage.DeviceNotResponding):
        run_in("worker", lambda: read(st))
    warnings = [r.getMessage() for r in caplog.records
                if r.name == LOGGER and r.levelno >= logging.WARNING]
    assert len(warnings) == 1, warnings
    assert warnings[0].startswith(f"device: no answer to a {step} in ")


def test_a_step_that_ends_after_its_cancel_is_a_warning(device, fast, caplog):
    """A step of a read past its limit that ends all the same once cancelled is no stall, so
    the storage says nothing of it: the wait itself warns of it."""
    st, _files = device({"GARMIN/Activity/x.fit": a_file(10)},
                        plan={("open", 1): GioOp("on cancel", on_cancel="done", delay_s=0.02)})
    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        run_in("worker", lambda: st._read("GARMIN/Activity/x.fit"))
    warnings = [r.getMessage() for r in caplog.records
                if r.name == LOGGER and r.levelno >= logging.WARNING]
    assert len(warnings) == 1 and "it ended all the same" in warnings[0], warnings


def test_a_device_plugged_in_again_has_no_mount_time_of_before(device, fast, monkeypatch, caplog):
    """Mounted by this process, unplugged and plugged in again at the same address of gvfs: a
    new device number on USB, so the log tells no time of the mount before."""
    monkeypatch.setattr(gio_storage, "_mounted_here", {}, raising=False)
    monkeypatch.setattr(gio_storage, "_found_mounted", {}, raising=False)
    gio_storage.mount(gio_storage.FoundDevice(URI, "made-up", False, Volume(GioOp()),
                                              usb=gio_storage.GarminUsb("51b4", 3, 14)))
    st, _files = device({"GARMIN/Activity/x.fit": a_file(10)},
                        plan={("open", 1): GioOp("never", strict=True)})
    st.device_uri, st.usb = URI, gio_storage.GarminUsb("51b4", 3, 15)
    with caplog.at_level(logging.DEBUG, logger=LOGGER), \
            pytest.raises(storage.DeviceNotResponding):
        run_in("worker", lambda: st._read("GARMIN/Activity/x.fit"))
    said = [line for line in logged(caplog) if "stopped responding;" in line]
    assert said and all(line.endswith("not known when it was mounted") for line in said), said


def test_a_slow_step_logs_one_line(device, fast, monkeypatch, caplog):
    """More than SLOW_STEP_S without an answer, within the limit: what it was and how long."""
    monkeypatch.setattr(gio_storage, "SLOW_STEP_S", 0.05, raising=False)
    st, _files = device(activities(3), plan={("portion", 1): GioOp("before", delay_s=0.1)})
    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        run_in("worker", lambda: st._list("GARMIN/Activity"))
    said = logged(caplog)
    assert len(said) == 1 and re.fullmatch(r"device: a listing took 0\.\d s", said[0]), said


def test_quick_steps_log_nothing(device, fast, caplog):
    """Every step of a listing, a query, a read and the space, answered at once: nothing in the
    log, also at DEBUG (a sync makes thousands of them)."""
    st, _files = device({"GARMIN/Activity/x.fit": a_file(10)}, space={"filesystem::free": 5})
    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        run_in("worker", lambda: (st.listdir("GARMIN/Activity"), st.info("GARMIN/Activity/x.fit"),
                                  st.read("GARMIN/Activity/x.fit"), st.free_space()))
    assert logged(caplog) == []


# --- the real Gio ----------------------------------------------------------------------------


@pytest.mark.parametrize("where", ["main", "worker"])
def test_a_folder_on_the_disk_is_read_through_the_real_gio(tmp_path, where):
    """The calls as the real Gio takes them: a listing of more than a portion, queries, the
    space, and files read whole and in part, of a folder on the disk."""
    folder = tmp_path / "GARMIN" / "Activity"
    folder.mkdir(parents=True)
    for n in range(250):
        (folder / f"{n:03d}.fit").write_bytes(b"x" * n)
    big = a_file(3 * CHUNK + 5)
    (tmp_path / "GARMIN" / "big.img").write_bytes(big)
    st = gio_storage.GioStorage(tmp_path.as_uri())
    listed, info, gone, size, no_size, free, capacity = run_in(where, lambda: (
        st.listdir("garmin/activity"), st.info("GARMIN/Activity/007.fit"), st.info("GARMIN/x"),
        st._size_now("GARMIN/Activity/009.fit"), st._size_now("GARMIN/x"), st.free_space(),
        st.capacity()))
    assert sorted(e.name for e in listed) == [f"{n:03d}.fit" for n in range(250)]
    assert (info.name, info.is_dir, info.size) == ("007.fit", False, 7) and info.mtime > 0
    assert gone is None and size == 9 and no_size is None
    assert capacity >= free >= 0
    assert run_in(where, lambda: (st.read("GARMIN/big.img"), st.read_start("GARMIN/big.img",
                                                                           CHUNK + 3),
                                  st.read("GARMIN/Activity/000.fit"))) == (big, big[:CHUNK + 3],
                                                                          b"")
