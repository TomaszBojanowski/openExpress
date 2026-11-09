"""The locks of the program's own files (fcntl.flock): which file each takes, whether it waits
or refuses at once, with which words, and that each lets go when its block ends. A lock is
held, as another process would find it, when another open file of it cannot take it."""

import fcntl
import logging

import pytest

from openexpress import applog, cache, known_devices, paths, service

BUSY_SYNC = "A sync is already running in another process."
BUSY_OTHER = "openExpress (Flatpak) is syncing a device now; try again when it is done."


def held(path) -> bool:
    with open(path, "rb") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(f, fcntl.LOCK_UN)
        return False


def test_one_writer_of_the_settings_at_a_time():
    lock = paths.config_dir() / "settings.lock"
    with paths.settings_locked():
        assert held(lock)
    assert not held(lock)
    assert paths.config_dir().stat().st_mode & 0o777 == paths.PRIVATE_DIR


def test_one_changer_of_the_known_devices_at_a_time():
    lock = paths.data_dir() / "devices.lock"
    with known_devices._locked():
        assert held(lock)
    assert not held(lock)
    assert paths.data_dir().stat().st_mode & 0o777 == paths.PRIVATE_DIR


def test_a_sync_holds_its_lock_and_that_of_the_other_install():
    """The other install's lock file, there and free, is held too while this one syncs; it is
    only read (its content stays as it was)."""
    other = paths.other_install_dir() / "sync.lock"
    other.parent.mkdir(parents=True)
    other.write_bytes(b"other")
    own = paths.data_dir() / "sync.lock"
    paths.make_dir(own.parent)
    own.write_text("left over")
    with service.sync_lock():
        assert held(own) and held(other)
        assert own.read_text() == ""  # opened anew for writing
    assert not held(own) and not held(other)
    assert other.read_bytes() == b"other"


def test_the_other_installs_lock_file_is_never_made():
    other = paths.other_install_dir()
    with service.sync_lock():
        assert held(paths.data_dir() / "sync.lock")
    assert not (other / "sync.lock").exists() and not other.exists()


def test_a_second_sync_is_refused_at_once_and_the_first_keeps_its_lock():
    own = paths.data_dir() / "sync.lock"
    with service.sync_lock():
        with pytest.raises(service.SyncBusy) as error, service.sync_lock():
            pytest.fail("two syncs at once")
        assert str(error.value) == BUSY_SYNC
        assert held(own)
    assert not held(own)


def test_the_other_install_syncing_is_logged_and_this_sync_lets_go(caplog):
    other = paths.other_install_dir() / "sync.lock"
    other.parent.mkdir(parents=True)
    with open(other, "w") as theirs:
        fcntl.flock(theirs, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with caplog.at_level(logging.INFO, logger="openexpress"), \
                pytest.raises(service.SyncBusy) as error, service.sync_lock():
            pytest.fail("synced while the other install syncs")
    assert str(error.value) == BUSY_OTHER
    assert "sync: the other install of this computer is syncing" in [
        r.getMessage() for r in caplog.records]
    assert not held(paths.data_dir() / "sync.lock")


def test_an_error_inside_a_lock_is_not_taken_for_a_busy_lock():
    """A BlockingIOError of the work done under a lock that refuses at once goes on as it is:
    only the lock's own refusal is "busy"."""
    with pytest.raises(BlockingIOError), service.sync_lock():
        raise BlockingIOError("of the work")
    assert not held(paths.data_dir() / "sync.lock")
    with pytest.raises(BlockingIOError), cache.using(wait=False):
        raise BlockingIOError("of the work")
    with pytest.raises(BlockingIOError), cache.clearing():
        raise BlockingIOError("of the work")
    assert not held(paths.cache_dir() / cache.LOCK_NAME)


def test_a_use_of_the_cache_that_cannot_wait_is_refused_while_it_is_cleared():
    lock = paths.cache_dir() / cache.LOCK_NAME
    with cache.clearing():
        assert held(lock)
        with pytest.raises(cache.CacheBusy) as error, cache.using(wait=False):
            pytest.fail("used while cleared")
    assert str(error.value) == "The cache is being cleared."
    with cache.using(wait=False):
        assert held(lock)
    assert not held(lock)


def test_the_rotation_of_the_log_lets_go_of_its_lock(tmp_path):
    log = tmp_path / "rotated.log"
    log.write_bytes(b"x" * applog.MAX_BYTES)
    applog.rotate(log)
    assert not log.exists() and (tmp_path / "rotated.log.1").stat().st_size == applog.MAX_BYTES
    assert not held(tmp_path / "rotated.log.lock")
