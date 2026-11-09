"""One write check, one quiet deletion and one check of the room on the device, in Storage:
what is written is checked by its size on the device, a file
of another size is deleted (unless it is one other maps use), and the error is the caller's
own class. All data is made up."""

import hashlib
import logging
import os

import pytest
from fakes import other_bytes_drive

from openexpress.device.storage import DeviceError, LocalStorage


class Short(LocalStorage):
    """A device that keeps one byte less than it is given (a copy cut short and not told)."""

    def _write(self, real_path, data):
        super()._write(real_path, data[:-1])

    def _write_file(self, real_path, local, progress=None):
        super()._write_file(real_path, local, progress)
        target = self._p(real_path)
        target.write_bytes(target.read_bytes()[:-1])


class Unknown(LocalStorage):
    """A device that cannot say the size of a file."""

    def size(self, path):
        return None


class StuckFiles(LocalStorage):
    """A device that refuses to delete."""

    def _delete(self, real):
        raise DeviceError("the device refused")


class Room(LocalStorage):
    def __init__(self, root, free):
        super().__init__(root)
        self.free = free

    def free_space(self):
        return self.free


class MadeUpError(Exception):
    pass


def folder(tmp_path, name="device"):
    """An empty folder as the device's storage."""
    path = tmp_path / name
    path.mkdir(exist_ok=True)
    return path


@pytest.fixture
def local(tmp_path):
    path = tmp_path / "local.bin"
    path.write_bytes(b"0123456789")
    return path


def test_a_whole_write_is_kept(tmp_path, local):
    st = LocalStorage(folder(tmp_path))
    assert st.write_checked("GARMIN/A.BIN", b"abc") == "GARMIN/A.BIN"
    assert st.write_file_checked("GARMIN/B.BIN", local) == "GARMIN/B.BIN"
    assert st.read("GARMIN/A.BIN") == b"abc" and st.size("GARMIN/B.BIN") == 10


@pytest.mark.parametrize("storage", [Short, Unknown])
def test_a_write_of_another_size_is_deleted_and_said_in_the_callers_error(tmp_path, local,
                                                                            storage):
    st = storage(folder(tmp_path))
    with pytest.raises(MadeUpError, match="Write verification of GARMIN/A.BIN failed"):
        st.write_checked("GARMIN/A.BIN", b"abc", error=MadeUpError)
    with pytest.raises(MadeUpError, match="Write verification of GARMIN/B.BIN failed"):
        st.write_file_checked("GARMIN/B.BIN", local, error=MadeUpError)
    assert not (tmp_path / "device/GARMIN/A.BIN").exists()
    assert not (tmp_path / "device/GARMIN/B.BIN").exists()


def test_the_error_is_a_device_error_unless_told(tmp_path):
    with pytest.raises(DeviceError):
        Short(folder(tmp_path)).write_checked("GARMIN/A.BIN", b"abc")


def test_a_file_other_maps_use_is_kept_even_of_another_size(tmp_path, local):
    st = Short(folder(tmp_path))
    with pytest.raises(MadeUpError):
        st.write_file_checked("Garmin/Maps/shared.img", local, keep_partial=True,
                              error=MadeUpError)
    assert (tmp_path / "device/Garmin/Maps/shared.img").stat().st_size == 9


def test_a_quiet_deletion_says_a_refusal_only_in_the_log(tmp_path, caplog):
    st = StuckFiles(folder(tmp_path))
    st.write("GARMIN/A.BIN", b"abc")
    with caplog.at_level(logging.WARNING, logger="openexpress.device.storage"):
        assert st.delete_quietly("GARMIN/A.BIN") is False
    assert [r.getMessage() for r in caplog.records] == [
        "Cannot delete GARMIN/A.BIN: the device refused"]
    plain = LocalStorage(folder(tmp_path, "other"))
    plain.write("GARMIN/A.BIN", b"abc")
    assert plain.delete_quietly("GARMIN/A.BIN") is True
    assert plain.delete_quietly("GARMIN/NOTHING.BIN") is True  # nothing there: nothing to do


def test_too_little_room_is_said_in_the_callers_error(tmp_path):
    st = Room(folder(tmp_path), 3_000_000)
    with pytest.raises(MadeUpError) as said:
        st.require_space(2_500_000, 1_000_000, error=MadeUpError)
    assert str(said.value) == "Not enough space on the device: 2.5\u00a0MB needed, 3.0\u00a0MB free"
    assert st.require_space(1_000_000, 1_000_000, error=MadeUpError) == 3_000_000
    assert Room(folder(tmp_path, "unknown"), None).require_space(10**12, 0) is None
    with pytest.raises(DeviceError):
        st.require_space(5_000_000, 0)


# --- a drive: what is written is read back from its medium -------------------------------------

UPDATE = b"update bytes"


def test_a_file_the_drive_keeps_with_other_bytes_is_deleted_and_said(tmp_path):
    """A drive may keep a file at its size with other bytes: read back from its medium, the
    file is deleted and the caller's error says so before anything is told of it."""
    src = tmp_path / "update.gcd"
    src.write_bytes(UPDATE)
    root = folder(tmp_path)
    drive = other_bytes_drive()(root)
    with pytest.raises(MadeUpError, match="Write verification of GARMIN/update.gcd failed"):
        drive.write_file_checked("GARMIN/update.gcd", src, error=MadeUpError)
    assert not (root / "GARMIN" / "update.gcd").exists()
    with pytest.raises(MadeUpError, match="Write verification of GARMIN/queue.fit failed"):
        drive.write_checked("GARMIN/queue.fit", UPDATE, error=MadeUpError)
    assert not (root / "GARMIN" / "queue.fit").exists()


def test_a_file_read_back_is_compared_with_the_servers_md5(tmp_path):
    src = tmp_path / "update.gcd"
    src.write_bytes(UPDATE)
    drive = LocalStorage(folder(tmp_path))
    md5 = hashlib.md5(UPDATE).hexdigest().upper()  # as the server writes it
    assert drive.write_file_checked("GARMIN/update.gcd", src, md5=md5) == "GARMIN/update.gcd"
    with pytest.raises(MadeUpError, match="Write verification of GARMIN/other.gcd failed"):
        drive.write_file_checked("GARMIN/other.gcd", src, md5="0" * 32, error=MadeUpError)
    assert not (drive.root / "GARMIN" / "other.gcd").exists()


def test_what_is_read_back_comes_from_the_medium_not_the_cache(tmp_path, monkeypatch):
    """The system's cache of a file written and synced is dropped before the file is read back,
    so that its bytes come from the drive."""
    told = []
    advise = os.posix_fadvise
    monkeypatch.setattr(os, "posix_fadvise", lambda fd, offset, length, advice: told.append(
        (offset, length, advice)) or advise(fd, offset, length, advice))
    src = tmp_path / "update.gcd"
    src.write_bytes(UPDATE)
    drive = LocalStorage(folder(tmp_path))
    drive.write_file_checked("GARMIN/update.gcd", src)
    drive.write_checked("GARMIN/queue.fit", UPDATE)
    assert told == [(0, 0, os.POSIX_FADV_DONTNEED)] * 2  # the whole file, each time


def test_a_long_check_of_a_drive_tells_its_bytes_as_the_copy_does(tmp_path, monkeypatch):
    """What is read back from the drive goes to `checking` (done, size), from 0 to the whole
    file: a check of its own, not a bar standing at its end. A check too short to be seen
    says nothing."""
    from openexpress.device import storage

    src = tmp_path / "map.img"
    src.write_bytes(bytes(2 * storage.COPY_CHUNK + 5))
    size = src.stat().st_size
    drive = LocalStorage(folder(tmp_path))
    checked = []
    drive.write_file_checked("GARMIN/a.img", src, checking=lambda *step: checked.append(step))
    assert checked == []  # shorter than CHECK_TOLD_FROM
    monkeypatch.setattr(storage, "CHECK_TOLD_FROM", size)
    drive.write_file_checked("GARMIN/b.img", src, checking=lambda *step: checked.append(step))
    assert checked == [(0, size), (storage.COPY_CHUNK, size), (2 * storage.COPY_CHUNK, size),
                       (size, size)]


# --- what the log says of a check that failed ---------------------------------------------------


def test_a_write_of_another_size_says_both_sizes_and_whether_it_was_deleted(tmp_path, local,
                                                                            caplog):
    with caplog.at_level(logging.WARNING, logger="openexpress.device.storage"):
        with pytest.raises(DeviceError):
            Short(folder(tmp_path)).write_checked("GARMIN/A.BIN", b"abc")
        with pytest.raises(DeviceError):
            Short(folder(tmp_path, "kept")).write_file_checked("GARMIN/B.BIN", local,
                                                               keep_partial=True)
        with pytest.raises(DeviceError):
            Unknown(folder(tmp_path, "unknown")).write_checked("GARMIN/C.BIN", b"abc")
    assert [r.getMessage() for r in caplog.records] == [
        "device: a file written has 2 B instead of 3 B; deleted",
        "device: a file written has 9 B instead of 10 B",
        "device: a file written has no size instead of 3 B; deleted"]
    assert (tmp_path / "kept/GARMIN/B.BIN").stat().st_size == 9


def test_a_file_read_back_with_other_bytes_says_its_size_and_whether_it_was_deleted(tmp_path,
                                                                                    caplog):
    src = tmp_path / "update.gcd"
    src.write_bytes(UPDATE)
    root = folder(tmp_path)
    drive = other_bytes_drive()(root)
    with caplog.at_level(logging.WARNING, logger="openexpress.device.storage"):
        with pytest.raises(MadeUpError, match="Write verification of GARMIN/update.gcd failed"):
            drive.write_file_checked("GARMIN/update.gcd", src, keep_partial=True,
                                     error=MadeUpError)
        with pytest.raises(DeviceError, match="Write verification of GARMIN/queue.fit failed"):
            drive.write_checked("GARMIN/queue.fit", UPDATE)
    assert [r.getMessage() for r in caplog.records] == [
        "drive: a file of 12 B read back with other bytes than were written",
        "drive: a file of 12 B read back with other bytes than were written; deleted"]
    assert (root / "GARMIN" / "update.gcd").read_bytes() == bytes(len(UPDATE))  # kept
    assert not (root / "GARMIN" / "queue.fit").exists()
