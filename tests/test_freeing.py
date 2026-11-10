"""Freeing space on the device (freeing.py): the older memory dumps, copied to this computer
first where they fit, then removed, the newest always kept; the files of updates the device did
not install, but never one in its main folder, which waits to be installed; what the device
refuses or keeps told; and the same from the command line (`diagnostics --free-space`). All
devices, files and sizes are made up."""

import hashlib
import os
import stat
import types
from datetime import timedelta

import pytest
from conftest import DEVICE_XML

from openexpress import diagnostics, freeing, paths
from openexpress.device.storage import DeviceError, DeviceNotResponding, LocalStorage
from openexpress.device.xmldevice import parse

UNIT = 1234567890  # the Id of DEVICE_XML
# The made-up dumps: (path, size, time); the newest first. ramdump1.log is larger than the
# limit of the copies the tests set (LIMIT).
DUMPS = [("GARMIN/Debug/dump/RAM.zip", 90, 1_700_000_400),
         ("GARMIN/Debug/dump/RAM_bak1.zip", 300, 1_700_000_300),
         ("GARMIN/Debug/dump/RAM_bak2.zip", 200, 1_700_000_200),
         ("GARMIN/Debug/ramdump1.log", 2_000, 1_700_000_100)]
LIMIT = 1_000


class Device(LocalStorage):
    """A made-up watch on this computer's disk; `refuses`: names whose removal the device
    refuses; `keeps`: names it says it removed but keeps."""

    def __init__(self, root, refuses=(), keeps=()):
        super().__init__(root)
        self.refuses, self.keeps = refuses, keeps

    def _delete(self, real_path):
        name = os.path.basename(real_path)
        if name in self.refuses:
            raise DeviceError(f"Cannot delete {name}: the device said no")
        if name in self.keeps:
            return
        super()._delete(real_path)


def make_device(tmp_path, dumps=DUMPS, **kw):
    root = tmp_path / "device"
    (root / "GARMIN").mkdir(parents=True)
    (root / "GARMIN" / "GarminDevice.xml").write_text(DEVICE_XML)
    for path, size, when in dumps:
        where = root / path
        where.parent.mkdir(parents=True, exist_ok=True)
        where.write_bytes(bytes([len(path) % 251]) * size)
        os.utime(where, (when, when))
    (root / "GARMIN" / "Debug").mkdir(exist_ok=True)
    (root / "GARMIN" / "Debug" / "ramdump_err1.txt").write_text("what failed")
    storage = Device(root, **kw)
    return storage, parse(storage.read("GARMIN/GarminDevice.xml"))


@pytest.fixture
def small_limit(monkeypatch):
    monkeypatch.setattr(freeing, "REMOVED_LIMIT", LIMIT)


def on_device(storage) -> list[str]:
    return sorted(path for path, _e in diagnostics.dump_files(storage))


def older(storage):
    return freeing.older_dumps([(p, e.size, e.mtime) for p, e in diagnostics.dump_files(storage)])


def update(path, size=4_100, **kw):
    from openexpress import updates

    return updates.SoftwareUpdate(
        part_number="006-D0000-00", name="GPS software", version="6.20", severity="Recommended",
        is_primary=False, is_reinstall=False, restart_required=False, device_path=path, url="",
        md5=None, size=size, order=0, on_device=True, **kw)


# --- which files ------------------------------------------------------------------------------


def test_the_older_dumps_are_all_but_the_newest_by_time():
    assert [p for p, _s, _t in freeing.older_dumps(DUMPS)] == [p for p, _s, _t in DUMPS[1:]]
    assert freeing.older_dumps(list(reversed(DUMPS)))[0][0] == "GARMIN/Debug/dump/RAM_bak1.zip"
    assert freeing.older_dumps(DUMPS[:1]) == []
    assert freeing.older_dumps(None) is None


def test_no_dump_is_offered_when_the_time_of_one_is_not_known():
    """The newest cannot be told."""
    assert freeing.older_dumps([*DUMPS[:2], ("GARMIN/Debug/dump/RAM_bak2.zip", 200, 0)]) is None


def test_the_copy_takes_the_newest_first_while_they_fit(small_limit):
    copied, too_large = freeing.copy_split(DUMPS[1:])
    assert [p for p, _s, _t in copied] == ["GARMIN/Debug/dump/RAM_bak1.zip",
                                           "GARMIN/Debug/dump/RAM_bak2.zip"]
    assert [p for p, _s, _t in too_large] == ["GARMIN/Debug/ramdump1.log"]


@pytest.mark.parametrize(("path", "waits"), [
    ("GUPDATE.GSP", True), ("Garmin/GUPDATE.GSP", True), ("GARMIN/gupdate.gcd", True),
    ("GARMIN/REMOTESW/GUPDATE.GCD", False), ("GARMIN/RemoteSW/006-B0000-00.GCD", False),
    ("GARMIN/GPS.GCD", False)])
def test_an_update_file_in_the_main_folder_waits_to_be_installed(path, waits):
    """Removing it would cancel the update: it is only shown."""
    assert freeing.waits_to_install(update(path)) is waits


# --- removing the dumps -----------------------------------------------------------------------


def test_the_older_dumps_are_copied_first_then_removed_and_the_newest_stays(tmp_path, clock,
                                                                         small_limit):
    st, dev = make_device(tmp_path)
    before = {p: st.read(p) for p, _s, _t in DUMPS}
    freed = freeing.remove_dumps(st, UNIT, older(st))
    assert on_device(st) == ["GARMIN/Debug/dump/RAM.zip"]
    assert st.exists("GARMIN/Debug/ramdump_err1.txt")  # not a dump: never removed
    assert sorted(p for p, _s in freed.removed) == sorted(p for p, _s, _t in DUMPS[1:])
    assert freed.freed == 2_500
    assert [p for p, _s in freed.not_copied] == ["GARMIN/Debug/ramdump1.log"]
    kept = sorted(p for p in freeing.removed_folder(UNIT).rglob("*") if p.is_file())
    assert [p.name for p in kept] == ["RAM_bak1.zip", "RAM_bak2.zip"]
    for p in kept:
        assert p.read_bytes() == before[f"GARMIN/Debug/dump/{p.name}"]
        assert stat.S_IMODE(p.stat().st_mode) == 0o600


def test_the_copies_go_apart_from_the_diagnostic_copies_and_with_the_device(tmp_path, clock):
    """Not under the 50 MB of the diagnostic copies, which hold the error logs the crash
    history reads; in the device's folder of diagnostic copies, so that Copies on This Computer
    counts them, Open Folder shows them and Forget Device removes them too."""
    where = freeing.removed_folder(UNIT)
    assert paths.device_dir(UNIT) in where.parents
    assert where.parent == diagnostics.folder(UNIT) and where.name == "removed"


def test_only_the_files_asked_and_still_the_same_are_removed(tmp_path, clock, small_limit):
    """The device is listed again first: a dump of another size now, or one that is the newest
    now (the newer one went), stays."""
    st, dev = make_device(tmp_path)
    asked = older(st)
    (st.root / "GARMIN/Debug/dump/RAM_bak2.zip").write_bytes(b"x" * 250)
    os.utime(st.root / "GARMIN/Debug/dump/RAM_bak2.zip", (1_700_000_200, 1_700_000_200))
    (st.root / "GARMIN/Debug/dump/RAM.zip").unlink()
    freed = freeing.remove_dumps(st, UNIT, asked)
    assert on_device(st) == ["GARMIN/Debug/dump/RAM_bak1.zip", "GARMIN/Debug/dump/RAM_bak2.zip"]
    assert [p for p, _s in freed.removed] == ["GARMIN/Debug/ramdump1.log"]


def test_a_dump_whose_copy_failed_is_not_removed(tmp_path, clock, small_limit, monkeypatch):
    st, dev = make_device(tmp_path)
    real_read = st.read

    def read(path):
        if path.endswith("RAM_bak1.zip"):
            raise DeviceError("Cannot read RAM_bak1.zip")
        return real_read(path)

    monkeypatch.setattr(st, "read", read)
    freed = freeing.remove_dumps(st, UNIT, older(st))
    assert "GARMIN/Debug/dump/RAM_bak1.zip" in on_device(st)
    assert freed.copy_failed == ["GARMIN/Debug/dump/RAM_bak1.zip"]
    assert "GARMIN/Debug/dump/RAM_bak2.zip" not in on_device(st)


def test_a_copy_that_does_not_match_is_not_kept_and_its_dump_stays(tmp_path, clock,
                                                                  small_limit, monkeypatch):
    st, dev = make_device(tmp_path)
    real_write = paths.write_private

    def write(path, data):
        real_write(path, data[:-1] if path.name == "RAM_bak2.zip" else data)

    monkeypatch.setattr(freeing.paths, "write_private", write)
    freed = freeing.remove_dumps(st, UNIT, older(st))
    assert "GARMIN/Debug/dump/RAM_bak2.zip" in on_device(st)
    assert freed.copy_failed == ["GARMIN/Debug/dump/RAM_bak2.zip"]
    assert not list(freeing.removed_folder(UNIT).rglob("RAM_bak2.zip"))


def test_what_the_device_refuses_or_keeps_is_told(tmp_path, clock, small_limit):
    st, dev = make_device(tmp_path, refuses=("RAM_bak1.zip",), keeps=("RAM_bak2.zip",))
    freed = freeing.remove_dumps(st, UNIT, older(st))
    assert [p for p, _s in freed.removed] == ["GARMIN/Debug/ramdump1.log"]
    assert dict(freed.kept) == {
        "GARMIN/Debug/dump/RAM_bak1.zip": "Cannot delete RAM_bak1.zip: the device said no",
        "GARMIN/Debug/dump/RAM_bak2.zip": "the device kept it"}
    assert freed.freed == 2_000


def test_a_device_that_stops_responding_stops_the_removal(tmp_path, clock, small_limit,
                                                         monkeypatch):
    st, dev = make_device(tmp_path)

    def stalls(real_path):
        raise DeviceNotResponding("Cannot delete: no answer")

    monkeypatch.setattr(st, "_delete", stalls)
    with pytest.raises(DeviceNotResponding):
        freeing.remove_dumps(st, UNIT, older(st))


def test_the_copies_keep_the_limit_the_oldest_removal_going_first(tmp_path, clock,
                                                                 small_limit):
    st, dev = make_device(tmp_path)
    freeing.remove_dumps(st, UNIT, older(st))  # 500 B copied
    clock.current += timedelta(minutes=5)
    more = [("GARMIN/Debug/dump/RAM.zip", 90, 1_700_000_900),
            ("GARMIN/Debug/dump/RAM_bak1.zip", 600, 1_700_000_800)]
    st2, _dev = make_device(tmp_path / "again", dumps=more)
    freeing.remove_dumps(st2, UNIT, older(st2))  # 600 B: the first removal goes
    kept = [p.name for p in freeing.removed_folder(UNIT).rglob("*") if p.is_file()]
    assert kept == ["RAM_bak1.zip"]
    assert sum(p.stat().st_size for p in freeing.removed_folder(UNIT).rglob("*")
               if p.is_file()) == 600


def test_the_copies_count_in_the_copies_on_this_computer(tmp_path, clock, small_limit):
    st, dev = make_device(tmp_path)
    assert diagnostics.copies_facts(UNIT)[:2] == (0, 0)
    freeing.remove_dumps(st, UNIT, older(st))
    count, size, last = diagnostics.copies_facts(UNIT)
    assert (count, size) == (1, 500) and last is not None


def test_the_log_names_no_file(tmp_path, clock, small_limit, caplog):
    st, dev = make_device(tmp_path)
    caplog.set_level("INFO", logger="openexpress.freeing")
    freeing.remove_dumps(st, UNIT, older(st))
    said = " ".join(r.getMessage() for r in caplog.records)
    assert "3 removed" in said and "2 copied first" in said
    assert "RAM_bak" not in said and "ramdump" not in said


# --- removing an update file ------------------------------------------------------------------


def test_the_file_of_an_update_not_installed_is_removed(tmp_path):
    st, dev = make_device(tmp_path, dumps=[])
    where = st.root / "GARMIN" / "RemoteSW" / "006-D0000-00.GCD"
    where.parent.mkdir(parents=True)
    where.write_bytes(b"u" * 4_100)
    freed = freeing.remove_update(st, update("GARMIN/RemoteSW/006-D0000-00.GCD"))
    assert not where.exists()
    assert freed.freed == 4_100


def test_an_update_file_that_waits_is_never_removed(tmp_path):
    st, dev = make_device(tmp_path, dumps=[])
    (st.root / "GARMIN" / "GUPDATE.GSP").write_bytes(b"s" * 10)
    with pytest.raises(ValueError):
        freeing.remove_update(st, update("GARMIN/GUPDATE.GSP"))
    assert (st.root / "GARMIN" / "GUPDATE.GSP").exists()


def test_an_update_file_the_device_keeps_is_told(tmp_path):
    st, dev = make_device(tmp_path, dumps=[], keeps=("006-D0000-00.GCD",))
    where = st.root / "GARMIN" / "RemoteSW" / "006-D0000-00.GCD"
    where.parent.mkdir(parents=True)
    where.write_bytes(b"u" * 10)
    freed = freeing.remove_update(st, update("GARMIN/RemoteSW/006-D0000-00.GCD"))
    assert freed.removed == [] and dict(freed.kept) == {
        "GARMIN/RemoteSW/006-D0000-00.GCD": "the device kept it"}


# --- the command line -------------------------------------------------------------------------


def run_cli(st, *args, capsys):
    from openexpress import cli

    code = cli.main(["diagnostics", "--path", str(st.root), *args])
    return code, capsys.readouterr().out


@pytest.fixture
def no_update_check(monkeypatch):
    monkeypatch.setattr(freeing, "updates_not_installed",
                        lambda client, storage, device, fresh=False: ([], None))


def test_the_command_line_lists_only_what_it_would_free_in_a_dry_run(
        tmp_path, clock, small_limit, no_update_check, capsys):
    """Only the section of the space to free, in the future tense, and one sentence of the
    dry run."""
    st, dev = make_device(tmp_path)
    code, out = run_cli(st, "--free-space", capsys=capsys)
    assert code == 0
    assert out.splitlines() == [
        "Space to free on the device:",
        "  older memory dumps: 3 (2.5 kB); the newest stays",
        "    to copy before the removal: 2 (500 B)",
        "    to remove without a copy, too large: 1 (2.0 kB)",
        "  in all: 2.5 kB",
        ("Dry run: nothing was copied or removed. Add --apply to copy and remove the files "
         "listed; that cannot be undone.")]
    assert len(on_device(st)) == 4


def test_with_apply_the_command_line_only_frees_space(tmp_path, clock, small_limit,
                                                      no_update_check, capsys):
    """The copy before the removal, never the ordinary copy of the diagnostic files."""
    st, dev = make_device(tmp_path)
    code, out = run_cli(st, "--free-space", "--apply", capsys=capsys)
    assert code == 0
    # The steps, then what was done; no line of the ordinary copy.
    assert out.splitlines()[-1] == (
        "Removed: 3 memory dumps, 2.5 kB freed; 2 copied to this computer first.")
    assert not [line for line in out.splitlines() if line.startswith(("Copied:", "Dry run"))]
    assert on_device(st) == ["GARMIN/Debug/dump/RAM.zip"]
    assert not (diagnostics.folder(UNIT) / "snapshots").exists()


@pytest.mark.parametrize("other", ["--dumps", "--crash-logs"])
def test_free_space_is_an_action_of_its_own(tmp_path, capsys, other):
    from openexpress import cli

    st, dev = make_device(tmp_path)
    with pytest.raises(SystemExit) as stopped:
        cli.main(["diagnostics", "--path", str(st.root), "--free-space", other])
    assert stopped.value.code == 2


def test_the_command_line_tells_a_refusal_with_exit_code_1(tmp_path, clock, small_limit,
                                                           no_update_check, capsys,
                                                           monkeypatch):
    st, dev = make_device(tmp_path)
    real = freeing.remove_dumps

    def refusing(storage, unit_id, asked, **kw):
        return real(Device(storage.root, refuses=("RAM_bak1.zip",)), unit_id, asked, **kw)

    monkeypatch.setattr(freeing, "remove_dumps", refusing)
    code, out = run_cli(st, "--free-space", "--apply", capsys=capsys)
    assert code == 1
    assert ("  not removed: RAM_bak1.zip: Cannot delete RAM_bak1.zip: the device said no"
            in out)


def test_the_command_line_shows_an_update_that_waits_and_removes_one_that_does_not(
        tmp_path, clock, small_limit, capsys, monkeypatch):
    st, dev = make_device(tmp_path, dumps=[])
    (st.root / "GARMIN" / "GUPDATE.GSP").write_bytes(b"s" * 10)
    (st.root / "GARMIN" / "GPS.GCD").write_bytes(b"g" * 40)
    found = [update("GARMIN/GUPDATE.GSP", size=10), update("GARMIN/GPS.GCD", size=40)]
    monkeypatch.setattr(freeing, "updates_not_installed",
                        lambda client, storage, device, fresh=False: (found, None))
    code, out = run_cli(st, "--free-space", capsys=capsys)
    assert "  update file not installed by the device: GPS software 6.20 (40 B)" in out
    assert "  waiting to be installed, not removed: GPS software 6.20 (GUPDATE.GSP)" in out
    code, out = run_cli(st, "--free-space", "--apply", capsys=capsys)
    assert code == 0
    assert not (st.root / "GARMIN" / "GPS.GCD").exists()
    assert (st.root / "GARMIN" / "GUPDATE.GSP").exists()


def test_the_command_line_frees_the_dumps_when_the_updates_cannot_be_checked(
        tmp_path, clock, small_limit, capsys, monkeypatch):
    st, dev = make_device(tmp_path)
    monkeypatch.setattr(freeing, "updates_not_installed",
                        lambda client, storage, device, fresh=False:
                        ([], "no connection to the update server"))
    code, out = run_cli(st, "--free-space", capsys=capsys)
    assert code == 0
    assert "  update files: not checked (no connection to the update server)" in out
    assert "  older memory dumps: 3 (2.5 kB); the newest stays" in out


def test_the_page_and_the_command_line_list_the_same_files(tmp_path, clock, small_limit,
                                                           capsys, monkeypatch):
    """One list from one function (freeing.plan): the update files the device did not install
    come from the update server's answer for both, not from the window's page Updates."""
    from openexpress import status

    st, dev = make_device(tmp_path)
    (st.root / "GARMIN" / "SQL").mkdir()
    (st.root / "GARMIN" / "SQL" / "MapRegionGrouping.db").write_bytes(b"m" * 60)
    found = [update("Garmin/SQL/MapRegionGrouping.db", size=60),
             update("GARMIN/GUPDATE.GSP", size=10)]
    monkeypatch.setattr(freeing, "updates_not_installed",
                        lambda client, storage, device, fresh=False: (found, None))
    session = types.SimpleNamespace(device=dev, storage=st, client=object(), diagnostics=None,
                                    crash_history=[], space=None)
    made = status.read(session)
    assert [u.device_path for u in made.not_installed] == [u.device_path for u in found]
    assert [p for p, _s, _t in made.older_dumps()] == [p for p, _s, _t in DUMPS[1:]]
    code, out = run_cli(st, "--free-space", capsys=capsys)
    assert "  update file not installed by the device: GPS software 6.20 (60 B)" in out
    assert "  waiting to be installed, not removed: GPS software 6.20 (GUPDATE.GSP)" in out


def test_the_removed_copies_hash_as_the_dumps(tmp_path, clock, small_limit):
    st, dev = make_device(tmp_path)
    sums = {p: hashlib.sha256(st.read(p)).hexdigest() for p, _s, _t in DUMPS[1:3]}
    freeing.remove_dumps(st, UNIT, older(st))
    for p in freeing.removed_folder(UNIT).rglob("*.zip"):
        assert hashlib.sha256(p.read_bytes()).hexdigest() == sums[f"GARMIN/Debug/dump/{p.name}"]

