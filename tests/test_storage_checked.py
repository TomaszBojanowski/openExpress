"""One write check, one quiet deletion and one check of the room on the device, in Storage
(step 2 of the audit of 0.7.9): what is written is checked by its size on the device, a file
of another size is deleted (unless it is one other maps use), and the error is the caller's
own class. All data is made up."""

import logging

import pytest

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
