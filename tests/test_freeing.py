"""Freeing space on the device (freeing.py): the older memory dumps, copied to this computer
first where they fit, then removed, the newest always kept; the files of updates the device did
not install, but never one in its main folder, which waits to be installed; what the device
refuses or keeps told; and the same from the command line (`diagnostics --free-space`). All
devices, files and sizes are made up."""

import hashlib
import os
import stat
import threading
import types
from datetime import timedelta

import pytest
from conftest import DEVICE_XML

from openexpress import diagnostics, freeing, known_devices, paths
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


def listing(storage):
    return [(p, e.size, e.mtime) for p, e in diagnostics.dump_files(storage)]


def older(storage):
    return freeing.older_dumps(listing(storage))


def asked(storage):
    """The removal as a question names it, from the device as it is now."""
    return freeing.dump_plan(listing(storage))


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
    copied, without_copy = freeing.copy_split(DUMPS[1:])
    assert [p for p, _s, _t in copied] == ["GARMIN/Debug/dump/RAM_bak1.zip",
                                           "GARMIN/Debug/dump/RAM_bak2.zip"]
    assert [p for p, _s, _t in without_copy] == ["GARMIN/Debug/ramdump1.log"]


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
    freed = freeing.remove_dumps(st, UNIT, asked(st))
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


@pytest.mark.parametrize("change", ["size", "newest gone", "one gone", "one more"])
def test_nothing_is_removed_when_the_dumps_are_not_those_of_the_question(tmp_path, clock,
                                                                         small_limit, change):
    """The device is listed again first: a dump of another size now, the newest gone (another
    is the newest now), one of the older ones gone, a new one: nothing is removed nor copied,
    and the caller learns why (ListChanged)."""
    st, dev = make_device(tmp_path)
    question = asked(st)
    before = on_device(st)
    if change == "size":
        (st.root / "GARMIN/Debug/dump/RAM_bak2.zip").write_bytes(b"x" * 250)
        os.utime(st.root / "GARMIN/Debug/dump/RAM_bak2.zip", (1_700_000_200, 1_700_000_200))
    elif change == "newest gone":
        (st.root / "GARMIN/Debug/dump/RAM.zip").unlink()
    elif change == "one gone":
        (st.root / "GARMIN/Debug/dump/RAM_bak1.zip").unlink()
    else:
        (st.root / "GARMIN/Debug/dump/RAM_bak3.zip").write_bytes(b"n" * 30)
        os.utime(st.root / "GARMIN/Debug/dump/RAM_bak3.zip", (1_700_000_050, 1_700_000_050))
        before.append("GARMIN/Debug/dump/RAM_bak3.zip")
    expected = sorted(p for p in before if change != "newest gone" or not p.endswith("/RAM.zip")
                      if change != "one gone" or not p.endswith("RAM_bak1.zip"))
    with pytest.raises(freeing.ListChanged):
        freeing.remove_dumps(st, UNIT, question)
    assert on_device(st) == expected
    assert not freeing.removed_folder(UNIT).exists() or not any(
        freeing.removed_folder(UNIT).rglob("*.zip"))


def test_a_dump_whose_copy_failed_is_not_removed(tmp_path, clock, small_limit, monkeypatch):
    st, dev = make_device(tmp_path)
    real_read = st.read

    def read(path):
        if path.endswith("RAM_bak1.zip"):
            raise DeviceError("Cannot read RAM_bak1.zip")
        return real_read(path)

    monkeypatch.setattr(st, "read", read)
    freed = freeing.remove_dumps(st, UNIT, asked(st))
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
    freed = freeing.remove_dumps(st, UNIT, asked(st))
    assert "GARMIN/Debug/dump/RAM_bak2.zip" in on_device(st)
    assert freed.copy_failed == ["GARMIN/Debug/dump/RAM_bak2.zip"]
    assert not list(freeing.removed_folder(UNIT).rglob("RAM_bak2.zip"))


def test_what_the_device_refuses_or_keeps_is_told(tmp_path, clock, small_limit):
    st, dev = make_device(tmp_path, refuses=("RAM_bak1.zip",), keeps=("RAM_bak2.zip",))
    freed = freeing.remove_dumps(st, UNIT, asked(st))
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
        freeing.remove_dumps(st, UNIT, asked(st))
    # Nothing removed; the copies made before the device stopped are of dumps still on it,
    # not their only ones: none is kept (they would push out copies that are).
    assert len(on_device(st)) == 4
    assert not list(freeing.removed_folder(UNIT).rglob("*.zip"))


def test_the_copies_keep_the_limit_the_oldest_removal_going_first(tmp_path, clock,
                                                                 small_limit):
    st, dev = make_device(tmp_path)
    freeing.remove_dumps(st, UNIT, asked(st))  # 500 B copied
    clock.current += timedelta(minutes=5)
    more = [("GARMIN/Debug/dump/RAM.zip", 90, 1_700_000_900),
            ("GARMIN/Debug/dump/RAM_bak1.zip", 600, 1_700_000_800)]
    st2, _dev = make_device(tmp_path / "again", dumps=more)
    freeing.remove_dumps(st2, UNIT, asked(st2))  # 600 B: the first removal goes
    kept = [p.name for p in freeing.removed_folder(UNIT).rglob("*") if p.is_file()]
    assert kept == ["RAM_bak1.zip"]
    assert sum(p.stat().st_size for p in freeing.removed_folder(UNIT).rglob("*")
               if p.is_file()) == 600


def test_the_copies_count_in_the_copies_on_this_computer(tmp_path, clock, small_limit):
    st, dev = make_device(tmp_path)
    assert diagnostics.copies_facts(UNIT)[:2] == (0, 0)
    freeing.remove_dumps(st, UNIT, asked(st))
    count, size, last = diagnostics.copies_facts(UNIT)
    assert (count, size) == (1, 500) and last is not None


def test_the_log_names_no_file(tmp_path, clock, small_limit, caplog):
    st, dev = make_device(tmp_path)
    caplog.set_level("INFO", logger="openexpress.freeing")
    freeing.remove_dumps(st, UNIT, asked(st))
    said = " ".join(r.getMessage() for r in caplog.records)
    assert "3 removed" in said and "2 copied first" in said
    assert "RAM_bak" not in said and "ramdump" not in said


# --- the device log after a removal ----------------------------------------------------------


def test_after_a_removal_the_device_log_tells_the_state_after_it(tmp_path, clock, small_limit):
    """The line of this connection is put right in place (the number of connections stays):
    the kinds of file, the space and the dumps counted again, and when; the page from the
    records tells the truth without connecting the device again."""
    from openexpress import history, status

    st, dev = make_device(tmp_path)
    history.note_connection(dev, st)
    clock.current += timedelta(minutes=1)
    freeing.remove_dumps(st, UNIT, asked(st))
    status.recount(dev, st)
    lines = history.read(UNIT).lines
    assert len(lines) == 1 and lines[0]["dumps"] == {"count": 1, "bytes": 90}
    made = status.read_kept(UNIT)
    assert made.dumps_at_last == (1, 90)
    assert made.categories["logs"] == 90 + len("what failed")  # RAM.zip and the text beside
    assert made.recounted and made.counted_at == clock.current


def test_the_command_line_puts_the_device_log_right_after_a_removal(tmp_path, clock,
                                                                    small_limit,
                                                                    no_update_check, capsys):
    from openexpress import cli, history

    st, dev = make_device(tmp_path)
    history.note_connection(dev, st)  # as the command line notes a device it finds connected
    assert cli._free_space_command(st, dev, apply=True) == 0  # as for a device found connected
    lines = history.read(UNIT).lines
    assert len(lines) == 1 and lines[0]["dumps"] == {"count": 1, "bytes": 90}
    assert "counted" in lines[0]


# --- removing an update file ------------------------------------------------------------------


def offered(monkeypatch, st, *updates_):
    """The update server, asked anew before a removal, still offers these updates; each with its
    file as the device shows it now (as freeing.plan lists it for the question)."""
    for u in updates_:
        u.listed_file = freeing.listed_file(st, u)
    monkeypatch.setattr(freeing, "updates_not_installed",
                        lambda client, storage, device, fresh=False: (list(updates_), None))
    return updates_[0] if updates_ else None


def test_the_file_of_an_update_not_installed_is_removed(tmp_path, monkeypatch):
    st, dev = make_device(tmp_path, dumps=[])
    where = st.root / "GARMIN" / "RemoteSW" / "006-D0000-00.GCD"
    where.parent.mkdir(parents=True)
    where.write_bytes(b"u" * 3_900)  # the server's size is 4_100: what was freed is the file's
    u = offered(monkeypatch, st, update("GARMIN/RemoteSW/006-D0000-00.GCD"))
    freed = freeing.remove_update(st, u, client=None, device=dev)
    assert not where.exists()
    assert freed.freed == 3_900


def test_an_update_file_that_waits_is_never_removed(tmp_path):
    st, dev = make_device(tmp_path, dumps=[])
    (st.root / "GARMIN" / "GUPDATE.GSP").write_bytes(b"s" * 10)
    with pytest.raises(ValueError):
        freeing.remove_update(st, update("GARMIN/GUPDATE.GSP"), client=None, device=dev)
    assert (st.root / "GARMIN" / "GUPDATE.GSP").exists()


def test_an_update_file_the_device_keeps_is_told(tmp_path, monkeypatch):
    st, dev = make_device(tmp_path, dumps=[], keeps=("006-D0000-00.GCD",))
    where = st.root / "GARMIN" / "RemoteSW" / "006-D0000-00.GCD"
    where.parent.mkdir(parents=True)
    where.write_bytes(b"u" * 10)
    u = offered(monkeypatch, st, update("GARMIN/RemoteSW/006-D0000-00.GCD"))
    freed = freeing.remove_update(st, u, client=None, device=dev)
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
        "    to remove without a copy, as the copies would not fit in 1.0 kB: 1 (2.0 kB)",
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
    (st.root / "GARMIN" / "RemoteSW").mkdir()
    (st.root / "GARMIN" / "RemoteSW" / "GPS.GCD").write_bytes(b"g" * 40)
    offered(monkeypatch, st, update("GARMIN/GUPDATE.GSP", size=10),
            update("GARMIN/RemoteSW/GPS.GCD", size=40))
    code, out = run_cli(st, "--free-space", capsys=capsys)
    assert "  update file not installed by the device: GPS software 6.20 (40 B)" in out
    assert "  waiting to be installed, not removed: GPS software 6.20 (GUPDATE.GSP)" in out
    code, out = run_cli(st, "--free-space", "--apply", capsys=capsys)
    assert code == 0
    assert not (st.root / "GARMIN" / "RemoteSW" / "GPS.GCD").exists()
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
                                    space=None)
    made = status.read(session)
    assert [u.device_path for u in made.not_installed] == [u.device_path for u in found]
    assert [p for p, _s, _t in made.older_dumps()] == [p for p, _s, _t in DUMPS[1:]]
    code, out = run_cli(st, "--free-space", capsys=capsys)
    assert "  update file not installed by the device: GPS software 6.20 (60 B)" in out
    assert "  waiting to be installed, not removed: GPS software 6.20 (GUPDATE.GSP)" in out


def test_the_removed_copies_hash_as_the_dumps(tmp_path, clock, small_limit):
    st, dev = make_device(tmp_path)
    sums = {p: hashlib.sha256(st.read(p)).hexdigest() for p, _s, _t in DUMPS[1:3]}
    freeing.remove_dumps(st, UNIT, asked(st))
    for p in freeing.removed_folder(UNIT).rglob("*.zip"):
        assert hashlib.sha256(p.read_bytes()).hexdigest() == sums[f"GARMIN/Debug/dump/{p.name}"]



# --- what is a dump, which is the newest, what may be removed ----------------------------------


def test_only_memory_dumps_are_listed_counted_and_removed(tmp_path, clock, small_limit):
    """A file of the folder of dumps that is no dump (a hidden file, a text, a file being
    written) is not listed, not counted as a dump and never removed; nor is it taken for the
    newest dump, so the newest dump stays."""
    others = [("GARMIN/Debug/dump/._RAM.zip", 40, 1_700_000_900),
              ("GARMIN/Debug/dump/notes.txt", 10, 1_700_000_800),
              ("GARMIN/Debug/dump/RAM.zip.part", 20, 1_700_000_700),
              ("GARMIN/Debug/ramdump_err1.txt", 11, 1_700_000_600)]
    st, dev = make_device(tmp_path, dumps=[*DUMPS, *others])
    assert on_device(st) == sorted(p for p, _s, _t in DUMPS)
    assert [p for p, _s in diagnostics.dumps_on_device(st)] == sorted(p for p, _s, _t in DUMPS)
    freeing.remove_dumps(st, UNIT, asked(st))
    assert on_device(st) == ["GARMIN/Debug/dump/RAM.zip"]
    for path, _size, _when in others:
        assert (st.root / path).exists(), path


def test_a_link_in_the_folder_of_dumps_is_not_followed(tmp_path, clock, small_limit):
    """A folder on this computer (a test drive, --path) may hold links: a dump-named file it
    leads to elsewhere is neither listed nor removed."""
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "RAM_bak9.zip").write_bytes(b"k" * 50)
    os.utime(outside / "RAM_bak9.zip", (1_700_000_000, 1_700_000_000))
    st, dev = make_device(tmp_path)
    (st.root / "GARMIN/Debug/dump/far").symlink_to(outside, target_is_directory=True)
    (st.root / "GARMIN/Debug/dump/RAM_bak8.zip").symlink_to(outside / "RAM_bak9.zip")
    assert on_device(st) == sorted(p for p, _s, _t in DUMPS)
    freeing.remove_dumps(st, UNIT, asked(st))
    assert (outside / "RAM_bak9.zip").exists()


@pytest.mark.parametrize("times", [
    (0, 1_700_000_300),  # not known
    (1_700_000_400, 315_532_800),  # a clock not set: 1980
    (1_700_000_400, 4_000_000_000),  # far ahead of this computer
    (1_700_000_400, 1_700_000_400),  # the two newest at the same time
], ids=["zero", "before 2010", "future", "tie"])
def test_nothing_is_offered_when_the_times_of_the_dumps_cannot_be_trusted(tmp_path, clock,
                                                                          small_limit, times):
    dumps = [("GARMIN/Debug/dump/RAM.zip", 90, times[0]),
             ("GARMIN/Debug/dump/RAM_bak1.zip", 300, times[1]),
             ("GARMIN/Debug/dump/RAM_bak2.zip", 200, 1_700_000_200)]
    st, dev = make_device(tmp_path, dumps=dumps)
    assert older(st) is None
    assert asked(st) is None
    plan = freeing.plan(st, dev, client=None)
    assert plan.older is None and plan.dumps_plan is None and plan.size == 0


def test_two_removals_at_once_never_lose_a_copy(tmp_path, clock, small_limit):
    """The window and the command line remove at once: the second waits for the first (the
    lock of the device's records), finds the dumps changed and removes nothing; every dump the
    first removed after a copy keeps its copy."""
    import threading

    second: dict = {}

    class First(Device):
        started = False

        def _delete(self, real_path):
            super()._delete(real_path)
            if not First.started:
                First.started = True
                other = Device(self.root)

                def run():
                    try:
                        freeing.remove_dumps(other, UNIT, question)
                    except freeing.ListChanged as e:
                        second["error"] = e

                second["thread"] = threading.Thread(target=run)
                second["thread"].start()

    dumps = [("GARMIN/Debug/dump/RAM.zip", 10, 1_700_000_500),
             ("GARMIN/Debug/dump/RAM_bak1.zip", 300, 1_700_000_400),
             ("GARMIN/Debug/dump/RAM_bak2.zip", 300, 1_700_000_300),
             ("GARMIN/Debug/dump/RAM_bak3.zip", 300, 1_700_000_200)]
    st, dev = make_device(tmp_path, dumps=dumps)
    st = First(st.root)
    question = asked(st)
    freed = freeing.remove_dumps(st, UNIT, question)
    second["thread"].join(10)
    assert isinstance(second.get("error"), freeing.ListChanged)
    kept = sorted(p.name for p in freeing.removed_folder(UNIT).rglob("*.zip"))
    assert kept == sorted(posixpath_name(p) for p, _s in freed.copied)
    assert len(freed.copied) == 3 and on_device(st) == ["GARMIN/Debug/dump/RAM.zip"]


def posixpath_name(path: str) -> str:
    return path.rsplit("/", 1)[-1]


def test_a_test_drive_keeps_its_copies_apart_from_the_devices(tmp_path, clock, small_limit):
    """A test drive carries a real device's unit ID: the copies of the dumps removed from it go
    to its own records, with a limit of their own, and never push out the device's."""
    real, dev = make_device(tmp_path, dumps=[("GARMIN/Debug/dump/RAM.zip", 10, 1_700_000_500),
                                             ("GARMIN/Debug/dump/RAM_bak1.zip", 600,
                                              1_700_000_400)])
    freeing.remove_dumps(real, UNIT, asked(real))
    clock.current += timedelta(minutes=5)

    class TestDrive(Device):
        test_drive = True

    root = tmp_path / "drive"
    (root / "GARMIN/Debug/dump").mkdir(parents=True)
    (root / "GARMIN/GarminDevice.xml").write_text(DEVICE_XML)
    for name, size, when in (("RAM.zip", 10, 1_700_000_900), ("RAM_bak1.zip", 700, 1_700_000_800)):
        (root / "GARMIN/Debug/dump" / name).write_bytes(b"t" * size)
        os.utime(root / "GARMIN/Debug/dump" / name, (when, when))
    drive = TestDrive(root)
    freeing.remove_dumps(drive, UNIT, asked(drive))
    assert [p.stat().st_size for p in freeing.removed_folder(UNIT).rglob("*.zip")] == [600]
    assert [p.stat().st_size for p in freeing.removed_folder(UNIT, True).rglob("*.zip")] == [700]
    assert freeing.removed_folder(UNIT, True) != freeing.removed_folder(UNIT)
    assert diagnostics.copies_facts(UNIT)[0] == 1 and diagnostics.copies_facts(UNIT, True)[0] == 1


def test_an_update_file_that_changed_since_the_question_is_not_removed(tmp_path, monkeypatch):
    """Looked at again before the removal: another size or time now (a newer version copied
    from the Updates page meanwhile), or the server no longer offering it: nothing removed, and
    the caller learns why."""
    st, dev = make_device(tmp_path, dumps=[])
    where = st.root / "GARMIN" / "RemoteSW" / "006-D0000-00.GCD"
    where.parent.mkdir(parents=True)
    where.write_bytes(b"u" * 4_100)
    os.utime(where, (1_700_000_000, 1_700_000_000))
    u = offered(monkeypatch, st, update("GARMIN/RemoteSW/006-D0000-00.GCD"))
    where.write_bytes(b"n" * 999)
    with pytest.raises(freeing.ListChanged):
        freeing.remove_update(st, u, client=None, device=dev)
    assert where.read_bytes() == b"n" * 999
    u = offered(monkeypatch, st, update("GARMIN/RemoteSW/006-D0000-00.GCD"))
    monkeypatch.setattr(freeing, "updates_not_installed",
                        lambda client, storage, device, fresh=False: ([], None))
    with pytest.raises(freeing.ListChanged):
        freeing.remove_update(st, u, client=None, device=dev)
    assert where.exists()


def test_a_removal_cut_short_keeps_what_it_did_for_its_caller(tmp_path, clock, small_limit):
    """The caller's result is filled while it goes: when the device stops after a first
    removal, what was copied and removed is known all the same."""
    class Stops(Device):
        def _delete(self, real_path):
            if real_path.endswith("RAM_bak2.zip"):
                raise DeviceNotResponding("stopped")
            super()._delete(real_path)

    st, dev = make_device(tmp_path)
    st = Stops(st.root)
    freed = freeing.Freed()
    with pytest.raises(DeviceNotResponding):
        freeing.remove_dumps(st, UNIT, asked(st), freed=freed)
    assert [p for p, _s in freed.removed] == ["GARMIN/Debug/dump/RAM_bak1.zip"]
    # The copy of the dump the device still holds is dropped; that of the removed one stays.
    assert [p for p, _s in freed.copied] == ["GARMIN/Debug/dump/RAM_bak1.zip"]
    assert sorted(p.name for p in freeing.removed_folder(UNIT).rglob("*.zip")) == [
        "RAM_bak1.zip"]


def test_the_command_line_tells_what_it_removed_when_interrupted(tmp_path, clock, small_limit,
                                                                 capsys, monkeypatch):
    """Ctrl+C after the dumps were removed, during an update file: what was removed is told and
    put in the device log before the interruption goes on."""
    st, dev = make_device(tmp_path)
    (st.root / "GARMIN" / "RemoteSW").mkdir()
    (st.root / "GARMIN" / "RemoteSW" / "GPS.GCD").write_bytes(b"g" * 40)
    offered(monkeypatch, st, update("GARMIN/RemoteSW/GPS.GCD", size=40))

    def interrupted(*_a, **_kw):
        raise KeyboardInterrupt

    noted = []
    monkeypatch.setattr(freeing, "remove_update", interrupted)
    from openexpress import history

    monkeypatch.setattr(history, "note_counted", lambda *a, **kw: noted.append(1))
    code, out = run_cli(st, "--free-space", "--apply", capsys=capsys)
    assert code == 130
    assert "Removed: 3 memory dumps, 2.5 kB freed; 2 copied to this computer first." in out
    assert noted == [1]


def test_the_command_line_says_the_dumps_changed_and_removes_nothing(tmp_path, clock, small_limit,
                                                                     no_update_check, capsys,
                                                                     monkeypatch):
    st, dev = make_device(tmp_path)
    real = freeing.remove_dumps

    def changing(storage, unit_id, asked_, **kw):
        (storage.root / "GARMIN/Debug/dump/RAM_bak1.zip").unlink()  # gone meanwhile
        return real(storage, unit_id, asked_, **kw)

    monkeypatch.setattr(freeing, "remove_dumps", changing)
    code, out = run_cli(st, "--free-space", "--apply", capsys=capsys)
    assert code == 1
    assert "The files on the device changed since the question; nothing was removed." in out
    assert "Removed:" not in out
    assert len(on_device(st)) == 3


# --- the copies before a removal: what goes wrong leaves the dump -----------------------------


def test_a_full_disk_leaves_the_dump_on_the_device(tmp_path, clock, small_limit, monkeypatch):
    st, dev = make_device(tmp_path)

    def full(path, data):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(freeing.paths, "write_private", full)
    freed = freeing.remove_dumps(st, UNIT, asked(st))
    assert sorted(freed.copy_failed) == ["GARMIN/Debug/dump/RAM_bak1.zip",
                                         "GARMIN/Debug/dump/RAM_bak2.zip"]
    assert "GARMIN/Debug/dump/RAM_bak1.zip" in on_device(st)
    assert "GARMIN/Debug/dump/RAM_bak2.zip" in on_device(st)


def test_an_interruption_while_copying_removes_nothing(tmp_path, clock, small_limit,
                                                       monkeypatch):
    st, dev = make_device(tmp_path)
    real = freeing.paths.write_private
    calls = []

    def write(path, data):
        calls.append(path)
        if len(calls) == 2:
            raise KeyboardInterrupt
        real(path, data)

    monkeypatch.setattr(freeing.paths, "write_private", write)
    with pytest.raises(KeyboardInterrupt):
        freeing.remove_dumps(st, UNIT, asked(st))
    assert len(on_device(st)) == 4


def test_the_same_name_in_two_folders_keeps_both_copies_whole(tmp_path, clock, small_limit):
    st, dev = make_device(tmp_path, dumps=[
        ("GARMIN/Debug/dump/RAM.zip", 10, 1_700_000_500),
        ("GARMIN/Debug/dump/old/RAM_bak1.zip", 100, 1_700_000_400),
        ("GARMIN/Debug/dump/RAM_bak1.zip", 200, 1_700_000_300)])
    data = {p: st.read(p) for p in ("GARMIN/Debug/dump/old/RAM_bak1.zip",
                                    "GARMIN/Debug/dump/RAM_bak1.zip")}
    freeing.remove_dumps(st, UNIT, asked(st))
    kept = {p.name: p.read_bytes() for p in freeing.removed_folder(UNIT).rglob("*.zip")}
    assert sorted(kept) == ["RAM_bak1.zip", "_RAM_bak1.zip"]
    assert sorted(kept.values()) == sorted(data.values())


def test_the_new_log_lines_name_no_file_and_no_device(tmp_path, clock, small_limit, caplog):
    """What a removal, its refusal by the device, a list that changed and a removal from a test
    drive write to the log: counts and sizes, never a file name or the unit ID."""
    st, dev = make_device(tmp_path, refuses=("RAM_bak2.zip",))
    with caplog.at_level("INFO"):
        question = asked(st)
        freeing.remove_dumps(st, UNIT, question)
        with pytest.raises(freeing.ListChanged):
            freeing.remove_dumps(st, UNIT, question)
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "freeing:" in text
    for word in ("RAM", "ramdump", "GARMIN", str(UNIT), str(tmp_path)):
        assert word not in text, word


# --- what else is never removed, and copies that are not the only ones ------------------------


@pytest.mark.parametrize("path", ["GARMIN/SID/MADEUP01.sid", "GARMIN/GPS/MADEUP.GCD",
                                  "GARMIN/MADEUP.GCD", "GARMIN/RemoteSW/.MADEUP.GCD",
                                  "GARMIN/Maps/MADEUP.SID"],
                         ids=["map support", "satellite data", "main folder", "hidden", ".sid"])
def test_an_update_file_outside_what_may_be_removed_is_neither_offered_nor_removed(
        tmp_path, monkeypatch, path):
    st, dev = make_device(tmp_path, dumps=[])
    where = st.root / path
    where.parent.mkdir(parents=True, exist_ok=True)
    where.write_bytes(b"s" * 500)
    u = offered(monkeypatch, st, update(path, size=500))
    assert freeing.plan(st, dev, client=None).updates == []
    with pytest.raises(ValueError):
        freeing.remove_update(st, u, client=None, device=dev)
    assert where.exists()


def test_the_copy_of_a_dump_the_device_kept_pushes_out_no_older_copy(tmp_path, clock,
                                                                    small_limit):
    first, dev = make_device(tmp_path, dumps=[("GARMIN/Debug/dump/RAM.zip", 10, 1_700_000_500),
                                              ("GARMIN/Debug/dump/RAM_bak1.zip", 300,
                                               1_700_000_400),
                                              ("GARMIN/Debug/dump/RAM_bak2.zip", 200,
                                               1_700_000_300)])
    freeing.remove_dumps(first, UNIT, asked(first))
    clock.current += timedelta(minutes=5)
    root = tmp_path / "second"
    (root / "GARMIN/Debug/dump").mkdir(parents=True)
    (root / "GARMIN/GarminDevice.xml").write_text(DEVICE_XML)
    for name, size, when in (("RAM.zip", 10, 1_700_000_900), ("RAM_bak1.zip", 600, 1_700_000_800)):
        (root / "GARMIN/Debug/dump" / name).write_bytes(b"k" * size)
        os.utime(root / "GARMIN/Debug/dump" / name, (when, when))
    second = Device(root, refuses=("RAM_bak1.zip",))
    freed = freeing.remove_dumps(second, UNIT, asked(second))
    assert freed.removed == [] and freed.copied == []
    sizes = sorted(p.stat().st_size for p in freeing.removed_folder(UNIT).rglob("*.zip"))
    assert sizes == [200, 300]  # the only copies of the first removal stay


def test_a_second_removal_waits_for_the_first_and_removes_nothing(tmp_path, clock,
                                                                  small_limit):
    """Started while the first one copies (nothing removed yet): without the lock both would
    remove; with it the second waits, finds the dumps changed and removes nothing."""
    import threading

    second: dict = {}

    class First(Device):
        def read(self, path):
            data = super().read(path)
            if "thread" not in second:
                other = Device(self.root)

                def run():
                    try:
                        second["freed"] = freeing.remove_dumps(other, UNIT, question)
                    except freeing.ListChanged as e:
                        second["error"] = e

                second["thread"] = threading.Thread(target=run)
                second["thread"].start()
                second["thread"].join(0.5)  # it runs while the first copies, unless it waits
            return data

    st, dev = make_device(tmp_path)
    st = First(st.root)
    question = asked(st)
    freed = freeing.remove_dumps(st, UNIT, question)
    second["thread"].join(10)
    assert "error" in second and "freed" not in second
    assert len(freed.removed) == 3
    kept = sorted(p.name for p in freeing.removed_folder(UNIT).rglob("*"))
    assert sorted(posixpath_name(p) for p, _s in freed.copied) == [
        n for n in kept if n.endswith((".zip", ".log"))]


def test_a_removal_cut_short_is_in_the_log_with_what_it_removed(tmp_path, clock, small_limit,
                                                                 caplog):
    class Stops(Device):
        def _delete(self, real_path):
            if real_path.endswith("RAM_bak2.zip"):
                raise DeviceNotResponding("stopped")
            super()._delete(real_path)

    st, dev = make_device(tmp_path)
    st = Stops(st.root)
    with caplog.at_level("INFO"), pytest.raises(DeviceNotResponding):
        freeing.remove_dumps(st, UNIT, asked(st))
    said = [r.getMessage() for r in caplog.records if r.name == "openexpress.freeing"]
    assert any("memory dumps: 1 removed" in m for m in said), said


# --- links, a device that stops, the order and the folders of the copies ----------------------


class StopsAt(Device):
    """A made-up watch that stops responding when `stop_at` is to be removed: from then on
    every operation fails at once, as a stalled device over MTP does."""

    def __init__(self, root, stop_at=None, **kw):
        super().__init__(root, **kw)
        self.stop_at, self.stopped = stop_at, False

    def _check(self):
        if self.stopped:
            raise DeviceNotResponding("no answer")

    def _delete(self, real_path):
        self._check()
        if self.stop_at and real_path.endswith(self.stop_at):
            self.stopped = True
            self._check()
        super()._delete(real_path)

    def _list(self, real_path):
        self._check()
        return super()._list(real_path)

    def _read(self, real_path):
        self._check()
        return super()._read(real_path)


def test_the_command_line_puts_the_device_log_right_when_the_device_stops_half_way(
        tmp_path, clock, small_limit, capsys):
    """The device stops after one dump is removed: what was removed is told, and the device
    log's line is put right by it (the device cannot be counted again)."""
    from openexpress import cli, history

    made, dev = make_device(tmp_path)
    st = StopsAt(made.root, stop_at="RAM_bak2.zip")
    assert history.note_connection(dev, st)
    assert history.read(UNIT).lines[-1]["dumps"]["count"] == 4
    with pytest.raises(DeviceNotResponding):
        cli._free_space(st, dev, freeing.plan(st, dev, client=None), None)
    assert "Removed: 1 memory dump" in capsys.readouterr().out
    assert history.read(UNIT).lines[-1]["dumps"] == {"count": 3, "bytes": 2_290}


def test_the_command_line_names_a_refused_file_also_when_the_device_then_stops(
        tmp_path, clock, small_limit, capsys):
    from openexpress import cli

    made, dev = make_device(tmp_path)
    st = StopsAt(made.root, stop_at="ramdump1.log", refuses=("RAM_bak1.zip",))
    with pytest.raises(DeviceNotResponding):
        cli._free_space(st, dev, freeing.plan(st, dev, client=None), None)
    out = capsys.readouterr().out
    assert "Removed: 1 memory dump" in out
    assert "not removed: RAM_bak1.zip: Cannot delete RAM_bak1.zip: the device said no" in out


@pytest.mark.parametrize("linked", ["GARMIN/Debug", "GARMIN"])
def test_dumps_behind_a_linked_folder_are_neither_offered_nor_removed(tmp_path, clock,
                                                                      small_limit, linked):
    """A folder of a drive or of --path may be a link to a folder elsewhere on this computer:
    nothing behind it is the device's."""
    outside = tmp_path / "elsewhere"
    root = tmp_path / "device"
    if linked == "GARMIN/Debug":
        for path, size, when in DUMPS:
            where = outside / path.replace("GARMIN/Debug/", "Debug/")
            where.parent.mkdir(parents=True, exist_ok=True)
            where.write_bytes(b"d" * size)
            os.utime(where, (when, when))
        (root / "GARMIN").mkdir(parents=True)
        (root / "GARMIN" / "Debug").symlink_to(outside / "Debug", target_is_directory=True)
    else:
        made, _dev = make_device(tmp_path / "made")
        made.root.rename(outside)
        root.mkdir()
        (root / "GARMIN").symlink_to(outside / "GARMIN", target_is_directory=True)
    before = sorted(p for p in outside.rglob("*") if p.is_file())
    st = LocalStorage(root)
    assert listing(st) == [] and freeing.dump_plan(listing(st)) is None
    assert sorted(p for p in outside.rglob("*") if p.is_file()) == before


def test_an_update_file_behind_a_linked_folder_is_neither_offered_nor_removed(tmp_path,
                                                                              monkeypatch):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "GPS.GCD").write_bytes(b"g" * 40)
    st, dev = make_device(tmp_path, dumps=[])
    (st.root / "GARMIN" / "RemoteSW").symlink_to(outside, target_is_directory=True)
    u = offered(monkeypatch, st, update("GARMIN/RemoteSW/GPS.GCD", 40))
    assert u.listed_file is None
    with pytest.raises(freeing.ListChanged):
        freeing.remove_update(st, u, None, dev)
    assert (outside / "GPS.GCD").exists()


def test_an_update_file_removed_before_the_device_stopped_is_in_the_log(tmp_path, monkeypatch,
                                                                        caplog):
    class StopsAfter(Device):
        stopped = False

        def _delete(self, real_path):
            super()._delete(real_path)
            self.stopped = True

        def _list(self, real_path):
            if self.stopped:
                raise DeviceNotResponding("no answer")
            return super()._list(real_path)

    made, dev = make_device(tmp_path, dumps=[])
    (made.root / "GARMIN" / "RemoteSW").mkdir()
    (made.root / "GARMIN" / "RemoteSW" / "GPS.GCD").write_bytes(b"g" * 40)
    st = StopsAfter(made.root)
    u = offered(monkeypatch, st, update("GARMIN/RemoteSW/GPS.GCD", 40))
    freed = freeing.Freed()
    with caplog.at_level("INFO"), pytest.raises(DeviceNotResponding):
        freeing.remove_update(st, u, None, dev, freed=freed)
    assert freed.removed == [("GARMIN/RemoteSW/GPS.GCD", 40)]
    said = [r.getMessage() for r in caplog.records if r.name == "openexpress.freeing"]
    assert "freeing: an update file removed (40 B)" in said


def test_an_update_file_gone_before_the_delete_is_not_freed_by_this_removal(tmp_path,
                                                                            monkeypatch):
    """Removed by something else a moment before (a file manager, another program): this
    removal freed nothing."""
    class Other(Device):
        def delete(self, path, strict=False):
            (self.root / path).unlink()
            return super().delete(path, strict)

    made, dev = make_device(tmp_path, dumps=[])
    (made.root / "GARMIN" / "RemoteSW").mkdir()
    (made.root / "GARMIN" / "RemoteSW" / "GPS.GCD").write_bytes(b"g" * 40)
    st = Other(made.root)
    u = offered(monkeypatch, st, update("GARMIN/RemoteSW/GPS.GCD", 40))
    freed = freeing.remove_update(st, u, None, dev)
    assert freed.removed == [] and freed.freed == 0


def test_the_oldest_removal_goes_first_whatever_the_clock_said(tmp_path, small_limit, clock):
    """A removal made while this computer's clock was years ahead is still the oldest."""
    for n, when in enumerate((clock.current + timedelta(days=3 * 365),
                              clock.current, clock.current + timedelta(days=1))):
        clock.current = when
        st, _dev = make_device(tmp_path / f"dev{n}", dumps=[
            ("GARMIN/Debug/dump/RAM.zip", 10, 1_700_000_900 + n * 1000),
            (f"GARMIN/Debug/dump/RAM_bak{n + 1}.zip", 400, 1_700_000_800 + n * 1000)])
        freeing.remove_dumps(st, UNIT, asked(st))
    kept = sorted(p.name for p in freeing.removed_folder(UNIT).rglob("*.zip"))
    assert kept == ["RAM_bak2.zip", "RAM_bak3.zip"]


def test_the_folders_of_the_removed_copies_are_their_owners_alone(tmp_path, clock, small_limit):
    st, _dev = make_device(tmp_path, dumps=DUMPS[:2])
    old = os.umask(0o022)
    try:
        freeing.remove_dumps(st, UNIT, asked(st))
    finally:
        os.umask(old)
    where = freeing.removed_folder(UNIT)
    modes = {stat.S_IMODE(p.stat().st_mode) for p in [where.parent, where, *where.iterdir()]}
    assert modes == {0o700}


# --- Forget Device and a removal at once ------------------------------------------------------


def test_forget_device_waits_for_a_removal_and_leaves_none_of_its_copies(tmp_path, clock):
    """Forget Device while Copy and Remove copies the dumps (the window forgets, the command line
    removes): it waits until the removal is done, then deletes its copies with the rest; the
    removal never makes the device's folder again behind it."""
    import threading

    from openexpress import forget, known_devices

    root = tmp_path / "device"
    (root / "GARMIN" / "Debug" / "dump").mkdir(parents=True)
    (root / "GARMIN" / "GarminDevice.xml").write_text(DEVICE_XML)
    for name, size, when in (("RAM.zip", 10, 1_700_000_500), ("RAM_bak1.zip", 300, 1_700_000_400),
                             ("RAM_bak2.zip", 200, 1_700_000_300)):
        (root / "GARMIN" / "Debug" / "dump" / name).write_bytes(b"d" * size)
        os.utime(root / "GARMIN" / "Debug" / "dump" / name, (when, when))
    known_devices.remember(UNIT, "Test watch", "006-B0000-00", "23.39", "mtp", now=1.0)
    forgetting: dict = {}

    class Copying(LocalStorage):
        def read(self, path):
            data = super().read(path)
            if "thread" not in forgetting:
                forgetting["thread"] = threading.Thread(
                    target=lambda: forgetting.setdefault("done", forget.forget_device(UNIT)))
                forgetting["thread"].start()
                forgetting["thread"].join(0.5)  # forgets during the copy, unless it waits
            return data

    st = Copying(root)
    asked = freeing.dump_plan([(p, e.size, e.mtime) for p, e in diagnostics.dump_files(st)])
    freed = freeing.remove_dumps(st, UNIT, asked)
    forgetting["thread"].join(10)
    assert len(freed.copied) == 2 and forgetting["done"] is True
    assert not paths.device_dir(UNIT).exists()


# --- the checks of each step: the copy, the times, the limit, the update files -------------


def update_file(st, name="006-D0000-00.GCD", data=b"u" * 4_100, when=1_700_000_000):
    where = st.root / "GARMIN" / "RemoteSW" / name
    where.parent.mkdir(parents=True, exist_ok=True)
    where.write_bytes(data)
    os.utime(where, (when, when))
    return where


def test_a_copy_of_the_same_length_but_other_bytes_keeps_its_dump(tmp_path, clock, small_limit,
                                                                 monkeypatch):
    """The copy is checked by its SHA-256, not by its length alone: a copy of the right length
    whose bytes differ is not kept, and its dump stays on the device."""
    st, _dev = make_device(tmp_path)
    real_write = paths.write_private

    def write(path, data):
        wrong = bytes([data[0] ^ 0xFF]) + data[1:]
        real_write(path, wrong if path.name == "RAM_bak2.zip" else data)

    monkeypatch.setattr(freeing.paths, "write_private", write)
    freed = freeing.remove_dumps(st, UNIT, asked(st))
    assert freed.copy_failed == ["GARMIN/Debug/dump/RAM_bak2.zip"]
    assert "GARMIN/Debug/dump/RAM_bak2.zip" in on_device(st)
    assert not list(freeing.removed_folder(UNIT).rglob("RAM_bak2.zip"))


def test_a_dump_more_than_a_day_ahead_of_this_computer_offers_nothing(clock):
    """A device clock set days ahead: which dump is the newest cannot be told; within a day it
    can (the device's zone may be ahead of this computer's)."""
    def dumps(ahead):
        return [("GARMIN/Debug/dump/RAM.zip", 90, (clock.current + ahead).timestamp()),
                ("GARMIN/Debug/dump/RAM_bak1.zip", 300, 1_700_000_300)]

    assert freeing.older_dumps(dumps(timedelta(hours=12))) is not None
    assert freeing.older_dumps(dumps(timedelta(days=2))) is None


def test_dumps_that_take_exactly_the_room_of_the_copies_are_all_copied(small_limit):
    """The limit is what the copies may take: copies that fill it exactly fit."""
    older = [("GARMIN/Debug/dump/RAM_bak1.zip", 600, 1_700_000_300),
             ("GARMIN/Debug/dump/RAM_bak2.zip", LIMIT - 600, 1_700_000_200)]
    copied, without_copy = freeing.copy_split(older)
    assert (len(copied), without_copy) == (2, [])


def test_an_update_file_of_the_same_size_but_another_time_is_not_removed(tmp_path, monkeypatch):
    """Looked at again by its size and its time: a file written again meanwhile with the same
    size (another version of the same length) is not the file the question named."""
    st, dev = make_device(tmp_path, dumps=[])
    where = update_file(st)
    u = offered(monkeypatch, st, update("GARMIN/RemoteSW/006-D0000-00.GCD"))
    update_file(st, data=b"n" * 4_100, when=1_700_000_500)
    with pytest.raises(freeing.ListChanged):
        freeing.remove_update(st, u, client=None, device=dev)
    assert where.read_bytes() == b"n" * 4_100


def test_an_update_file_stays_when_the_update_server_asked_anew_no_longer_offers_it(
        tmp_path, monkeypatch):
    """The answer kept from before may be old (the device installed the update meanwhile): the
    removal asks the update server anew and removes nothing it no longer offers."""
    st, dev = make_device(tmp_path, dumps=[])
    where = update_file(st)
    u = update("GARMIN/RemoteSW/006-D0000-00.GCD")
    u.listed_file = freeing.listed_file(st, u)
    monkeypatch.setattr(freeing, "updates_not_installed",
                        lambda client, storage, device, fresh=False: ([] if fresh else [u], None))
    with pytest.raises(freeing.ListChanged):
        freeing.remove_update(st, u, client=None, device=dev)
    assert where.exists()


@pytest.mark.parametrize("path", ["GARMIN/GPS/MADEUP/MADEUP.GCD", "GARMIN/SID/MADEUP/MADEUP.GCD"],
                         ids=["satellite data", "map support"])
def test_a_file_in_a_folder_under_satellite_data_or_map_support_is_never_removed(
        tmp_path, monkeypatch, path):
    """Garmin/GPS and Garmin/SID are never freed, nor any folder under them."""
    st, dev = make_device(tmp_path, dumps=[])
    where = st.root / path
    where.parent.mkdir(parents=True)
    where.write_bytes(b"s" * 500)
    u = offered(monkeypatch, st, update(path, size=500))
    assert freeing.plan(st, dev, client=None).updates == []
    with pytest.raises(ValueError):
        freeing.remove_update(st, u, client=None, device=dev)
    assert where.exists()


def test_with_apply_the_command_line_lists_from_the_update_server_asked_anew(
        tmp_path, clock, small_limit, capsys, monkeypatch):
    """--apply lists what it removes from an answer asked anew, not one kept from before: an
    update the server no longer offers (installed meanwhile) is neither listed nor tried."""
    st, _dev = make_device(tmp_path, dumps=[])
    where = update_file(st, "GPS.GCD", b"g" * 40)
    u = update("GARMIN/RemoteSW/GPS.GCD", size=40)
    u.listed_file = freeing.listed_file(st, u)
    monkeypatch.setattr(freeing, "updates_not_installed",
                        lambda client, storage, device, fresh=False: ([] if fresh else [u], None))
    code, out = run_cli(st, "--free-space", "--apply", capsys=capsys)
    assert (code, out.splitlines()) == (0, ["Space to free on the device:", "  nothing to remove"])
    assert where.exists()


def test_an_update_server_error_for_one_file_is_told_and_the_other_files_go_on(
        tmp_path, clock, small_limit, capsys, monkeypatch):
    """--apply: the update server failing while one file is looked at again leaves that file,
    says why, ends with exit code 1, and the other files are still removed."""
    st, _dev = make_device(tmp_path, dumps=[])
    gps, wifi = update_file(st, "GPS.GCD", b"g" * 40), update_file(st, "WIFI.GCD", b"w" * 40)
    found = [update("GARMIN/RemoteSW/GPS.GCD", size=40),
             update("GARMIN/RemoteSW/WIFI.GCD", size=40)]
    for u in found:
        u.listed_file = freeing.listed_file(st, u)
    calls = []

    def server(client, storage, device, fresh=False):
        calls.append(fresh)
        if len(calls) == 2:  # the first file looked at again before its removal
            return [], "no connection to the update server"
        return list(found), None

    monkeypatch.setattr(freeing, "updates_not_installed", server)
    code, out = run_cli(st, "--free-space", "--apply", capsys=capsys)
    assert code == 1
    assert "  not removed: GPS.GCD: no connection to the update server" in out.splitlines()
    assert gps.exists() and not wifi.exists()


# --- a dump gone meanwhile, a folder that cannot be listed, one dump, no unit ID ---------------


def test_the_copy_of_a_dump_gone_from_the_device_meanwhile_is_kept(tmp_path, clock, small_limit,
                                                                   monkeypatch):
    """Removed by something else after its copy was made: not freed by this removal, and its
    copy, now its only one, stays."""
    st, _dev = make_device(tmp_path, dumps=DUMPS[:2])
    real_read = st.read

    def read(path):
        data = real_read(path)
        if path.endswith("RAM_bak1.zip"):
            (st.root / path).unlink()
        return data

    plan = asked(st)
    monkeypatch.setattr(st, "read", read)
    freed = freeing.remove_dumps(st, UNIT, plan)
    assert (freed.removed, freed.gone) == ([], ["GARMIN/Debug/dump/RAM_bak1.zip"])
    assert [p.name for p in freeing.removed_folder(UNIT).rglob("*.zip")] == ["RAM_bak1.zip"]


class ListingFails(Device):
    """The folder of dumps cannot be listed while a file is removed (a DeviceError, not a
    stall)."""

    removing = False

    def _list(self, real_path):
        if self.removing and real_path.casefold() == "garmin/debug/dump":
            raise DeviceError("Cannot list GARMIN/Debug/dump")
        return super()._list(real_path)

    def delete(self, path, strict=False):
        self.removing = True
        try:
            return super().delete(path, strict)
        finally:
            self.removing = False


def test_a_dump_whose_folder_cannot_be_listed_stays_and_is_told(tmp_path, clock, small_limit):
    made, _dev = make_device(tmp_path, dumps=DUMPS[:2])
    st = ListingFails(made.root)
    freed = freeing.remove_dumps(st, UNIT, asked(st))
    assert "GARMIN/Debug/dump/RAM_bak1.zip" in on_device(st)
    assert freed.removed == [] and freed.gone == []
    assert freed.kept == [("GARMIN/Debug/dump/RAM_bak1.zip", "Cannot list GARMIN/Debug/dump")]
    assert not list(freeing.removed_folder(UNIT).rglob("*.zip"))


def test_the_command_line_says_a_dump_stayed_when_its_folder_could_not_be_listed(
        tmp_path, clock, small_limit, monkeypatch, capsys, no_update_check):
    made, _dev = make_device(tmp_path, dumps=DUMPS[:2])
    removing = []
    real_list, real_delete = LocalStorage._list, LocalStorage.delete

    def listing(self, real_path):
        if removing and real_path.casefold() == "garmin/debug/dump":
            raise DeviceError("Cannot list GARMIN/Debug/dump")
        return real_list(self, real_path)

    def delete(self, path, strict=False):
        removing.append(path)
        try:
            return real_delete(self, path, strict)
        finally:
            removing.clear()

    monkeypatch.setattr(LocalStorage, "_list", listing)
    monkeypatch.setattr(LocalStorage, "delete", delete)
    code, out = run_cli(made, "--free-space", "--apply", capsys=capsys)
    assert (made.root / "GARMIN/Debug/dump/RAM_bak1.zip").exists()
    assert code == 1
    assert "  not removed: RAM_bak1.zip: Cannot list GARMIN/Debug/dump" in out.splitlines()


def test_the_command_line_says_when_the_files_were_gone_already(tmp_path, clock, small_limit,
                                                                monkeypatch, capsys,
                                                                no_update_check):
    made, _dev = make_device(tmp_path, dumps=DUMPS[:2])
    real_delete = LocalStorage.delete

    def gone_first(self, path, strict=False):
        (self.root / path).unlink()  # something else removed it a moment before
        return real_delete(self, path, strict)

    monkeypatch.setattr(LocalStorage, "delete", gone_first)
    code, out = run_cli(made, "--free-space", "--apply", capsys=capsys)
    assert code == 0
    assert "Nothing removed: the files were no longer on the device." in out.splitlines()


def test_one_dump_whose_time_is_not_known_has_no_older_dumps():
    """A dump alone is the newest whatever its time: there is no older one."""
    assert freeing.older_dumps([("GARMIN/Debug/dump/RAM.zip", 90, 0)]) == []


def test_a_device_without_a_unit_id_has_no_dumps_removed(tmp_path, clock, small_limit):
    """Its copies would have no folder of the device: the page and the command line offer no
    removal of its dumps, and none is made."""
    from openexpress import paths, status

    st, _dev = make_device(tmp_path, dumps=DUMPS[:2])
    xml = st.root / "GARMIN" / "GarminDevice.xml"
    xml.write_text(DEVICE_XML.replace(f"<Id>{UNIT}</Id>", ""))
    dev = parse(st.read("GARMIN/GarminDevice.xml"))
    assert not dev.unit_id
    assert freeing.plan(st, dev, client=None).dumps_plan is None
    s = types.SimpleNamespace(device=dev, storage=st, diagnostics=None, space=None, client=None)
    assert status.read(s).dumps_removable is False
    with pytest.raises(ValueError):
        freeing.remove_dumps(st, dev.unit_id, asked(st))
    assert not list((paths.data_dir() / "devices").rglob("*.zip"))


# --- a copy cut short, two removals of one update file ----------------------------------------


def test_a_copy_shorter_than_the_dump_listed_keeps_its_dump(tmp_path, clock, small_limit,
                                                             monkeypatch):
    """A read cut short (gvfs telling a size it no longer has) gives a copy that matches what
    was read but not the dump: the copy is checked against the dump's size too, not kept, and
    the dump stays on the device."""
    st, _dev = make_device(tmp_path)
    real_read = st.read

    def read(path):
        data = real_read(path)
        return data[:-1] if path.endswith("RAM_bak2.zip") else data

    monkeypatch.setattr(st, "read", read)
    freed = freeing.remove_dumps(st, UNIT, asked(st))
    assert freed.copy_failed == ["GARMIN/Debug/dump/RAM_bak2.zip"]
    assert "GARMIN/Debug/dump/RAM_bak2.zip" in on_device(st)
    assert not list(freeing.removed_folder(UNIT).rglob("RAM_bak2.zip"))


def test_a_second_removal_of_the_same_update_file_waits_and_removes_nothing(tmp_path,
                                                                          monkeypatch):
    """The window and the command line remove the same update file at once: the second waits
    for the first (the lock of the device's records), finds the file gone and frees nothing,
    so the file is never counted as freed twice."""
    second: dict = {}

    class First(Device):
        def _delete(self, real_path):
            if "thread" not in second:
                other = Device(self.root)

                def run():
                    try:
                        second["freed"] = freeing.remove_update(other, u, client=None,
                                                                device=dev)
                    except freeing.ListChanged as e:
                        second["error"] = e

                second["thread"] = threading.Thread(target=run)
                second["thread"].start()
                second["thread"].join(0.5)  # it removes the file meanwhile, unless it waits
            super()._delete(real_path)

    made, dev = make_device(tmp_path, dumps=[])
    st = First(made.root)
    update_file(st)
    u = offered(monkeypatch, st, update("GARMIN/RemoteSW/006-D0000-00.GCD"))
    freed = freeing.remove_update(st, u, client=None, device=dev)
    second["thread"].join(10)
    assert "error" in second and "freed" not in second
    assert freed.freed == 4_100


# --- copying stopped half-way, no unit ID on the command line, a copy given with --path --------


def test_ctrl_c_while_copying_leaves_no_copy_of_a_dump_still_on_the_device(
        tmp_path, clock, small_limit, monkeypatch):
    st, dev = make_device(tmp_path)
    real = freeing.paths.write_private
    calls = []

    def write(path, data):
        calls.append(path)
        if len(calls) == 2:
            raise KeyboardInterrupt
        real(path, data)

    monkeypatch.setattr(freeing.paths, "write_private", write)
    freed = freeing.Freed()
    with pytest.raises(KeyboardInterrupt):
        freeing.remove_dumps(st, UNIT, asked(st), freed=freed)
    assert len(on_device(st)) == 4  # nothing removed
    left = [p.name for p in freeing.removed_folder(UNIT).rglob("*") if p.is_file()]
    assert left == [], f"copies of dumps still on the device were kept: {left}"
    assert freed.copied == [], f"told as copied though nothing removed: {freed.copied}"


def test_device_stopping_while_copying_leaves_no_copy(tmp_path, clock, small_limit, monkeypatch):
    st, dev = make_device(tmp_path)
    real_read = st.read
    n = []

    def read(path):
        n.append(path)
        if len(n) == 2:
            raise DeviceNotResponding("no answer")
        return real_read(path)

    monkeypatch.setattr(st, "read", read)
    plan = asked(st)
    with pytest.raises(DeviceNotResponding):
        freeing.remove_dumps(st, UNIT, plan)
    assert len(on_device(st)) == 4
    left = [p.name for p in freeing.removed_folder(UNIT).rglob("*") if p.is_file()]
    assert left == [], f"copies of dumps still on the device were kept: {left}"
    # and they count as a copy on this computer
    assert diagnostics.copies_facts(UNIT)[0] == 0, diagnostics.copies_facts(UNIT)


def test_the_log_of_a_removal_that_stopped_counts_only_what_went_without_a_copy(
        tmp_path, clock, small_limit, monkeypatch, caplog):
    """The log of a removal that stopped counts as removed without a copy only the dumps that went."""
    st, dev = make_device(tmp_path)

    def stalls(real_path):
        raise DeviceNotResponding("no answer")

    monkeypatch.setattr(st, "_delete", stalls)
    freed = freeing.Freed()
    with caplog.at_level("INFO"), pytest.raises(DeviceNotResponding):
        freeing.remove_dumps(st, UNIT, asked(st), freed=freed)
    assert len(on_device(st)) == 4  # nothing removed
    said = [r.getMessage() for r in caplog.records if r.name == "openexpress.freeing"]
    line = next(m for m in said if m.startswith("freeing: memory dumps:"))
    assert "0 without a copy" in line, line
    assert freed.not_copied == [], freed.not_copied


def test_without_unit_id_the_command_line_says_why_nothing_is_removed(
        tmp_path, clock, small_limit, no_update_check, capsys):
    st, _dev = make_device(tmp_path, dumps=DUMPS[:2])
    xml = st.root / "GARMIN" / "GarminDevice.xml"
    xml.write_text(DEVICE_XML.replace(f"<Id>{UNIT}</Id>", ""))
    assert not parse(st.read("GARMIN/GarminDevice.xml")).unit_id
    code, dry = run_cli(st, "--free-space", capsys=capsys)
    code, out = run_cli(st, "--free-space", "--apply", capsys=capsys)
    assert len(on_device(st)) == 2  # nothing removed (as designed)
    # The dry run promised: "older memory dumps: 1 …; in all …; Add --apply to copy and remove
    # the files listed". --apply must at least say that nothing was removed, and why.
    assert "older memory dumps: 1" not in dry or out.strip(), (
        f"dry run lists the dump for removal, --apply prints nothing: dry={dry!r} out={out!r} "
        f"code={code}")


def test_dumps_not_listed_are_not_said_to_have_times_that_cannot_be_trusted(
        tmp_path, clock, small_limit, no_update_check, capsys, monkeypatch):
    from openexpress.device import storage as storage_mod
    from openexpress.device.storage import DeviceError

    st, _dev = make_device(tmp_path, dumps=DUMPS)
    real = storage_mod.LocalStorage._list

    def _list(self, real_path):
        if real_path.casefold() == "garmin/debug":
            raise DeviceError("Cannot list GARMIN/Debug")
        return real(self, real_path)

    monkeypatch.setattr(storage_mod.LocalStorage, "_list", _list)
    code, out = run_cli(st, "--free-space", capsys=capsys)
    assert "the newest cannot be told" not in out, out


def test_a_run_on_a_copy_never_pushes_out_the_real_devices_only_copies(
        tmp_path, clock, small_limit, no_update_check, capsys):
    real_dumps = [("GARMIN/Debug/dump/RAM.zip", 90, 1_700_000_400),
                  ("GARMIN/Debug/dump/RAM_bak1.zip", 300, 1_700_000_300),
                  ("GARMIN/Debug/dump/RAM_bak2.zip", 300, 1_700_000_200)]
    st, _dev = make_device(tmp_path / "real", dumps=real_dumps)
    freeing.remove_dumps(st, UNIT, asked(st))  # removed from the real device: 600 B copied
    first = freeing.removals(UNIT)
    assert len(first) == 1
    copy_dumps = [("GARMIN/Debug/dump/RAM.zip", 90, 1_700_100_400),
                  ("GARMIN/Debug/dump/RAM_bak1.zip", 250, 1_700_100_300),
                  ("GARMIN/Debug/dump/RAM_bak2.zip", 250, 1_700_100_200)]
    copy, _ = make_device(tmp_path / "copy", dumps=copy_dumps)  # a copy of a device's files
    code, out = run_cli(copy, "--free-space", "--apply", capsys=capsys)
    assert code == 0, out
    after = freeing.removals(UNIT)
    assert first[0] in after, (
        "the copies of the dumps removed from the real device (their only copies) were pushed "
        f"out by a run on a copy: before {[p.name for p in first]}, after "
        f"{[p.name for p in after]}")


# --- numbering after a dropped removal, the limit's edge, copied updates, nothing removed -----


def remove_one_copied(tmp_path, n, size):
    """A removal from a made-up device whose newest dump stays and whose one older dump,
    RAM_bak<n>.zip of `size` bytes, is copied first."""
    st, _dev = make_device(tmp_path / f"dev{n}", dumps=[
        ("GARMIN/Debug/dump/RAM.zip", 10, 1_700_000_900 + n * 1000),
        (f"GARMIN/Debug/dump/RAM_bak{n}.zip", size, 1_700_000_800 + n * 1000)])
    freeing.remove_dumps(st, UNIT, asked(st))


def kept_copies():
    return sorted(p.name for p in freeing.removed_folder(UNIT).rglob("*.zip"))


def test_the_oldest_removal_goes_first_also_after_an_older_one_was_dropped(tmp_path, clock,
                                                                         small_limit):
    """A removal is numbered on from the highest number left, not by how many are left: once
    the oldest went for the limit, a removal made while this computer's clock was behind is
    still the newest, and the next one to go is the oldest left."""
    remove_one_copied(tmp_path, 1, 400)
    clock.current += timedelta(minutes=5)
    remove_one_copied(tmp_path, 2, 700)  # over the limit: the first removal goes
    assert kept_copies() == ["RAM_bak2.zip"]
    clock.current -= timedelta(days=365)  # this computer's clock set a year back
    remove_one_copied(tmp_path, 3, 200)
    clock.current += timedelta(minutes=5)
    remove_one_copied(tmp_path, 4, 200)  # over the limit again: the second removal goes
    assert kept_copies() == ["RAM_bak3.zip", "RAM_bak4.zip"]


def test_copies_that_take_exactly_the_limit_all_stay(tmp_path, clock, small_limit):
    """The limit is what the copies may take: removals whose copies fill it exactly all stay."""
    remove_one_copied(tmp_path, 1, 400)
    clock.current += timedelta(minutes=5)
    remove_one_copied(tmp_path, 2, LIMIT - 400)
    assert kept_copies() == ["RAM_bak1.zip", "RAM_bak2.zip"]


def test_what_can_be_freed_leaves_the_record_of_the_updates_copied_alone(tmp_path, clock):
    """Only read: the page and the command line asking for the update files the device did not
    install never forget what was copied to it, even an update the server no longer offers (the
    Updates page does that)."""
    now = clock.current.timestamp()
    known_devices.remember(UNIT, "Made-up watch", "006-B0000-00", "1.00", "mtp", now=now - 900)
    known_devices.note_copied(UNIT, ["006-D0000-00.6.20"], now=now - 600)
    st, dev = make_device(tmp_path, dumps=[])
    server = types.SimpleNamespace(
        software_updates=lambda raw, include_reinstalls=True, fresh=False: {"updates": []})
    for fresh in (False, True):  # the page and a dry run; --apply
        freeing.plan(st, dev, server, fresh=fresh)
    assert dict(known_devices.load()[UNIT].copied_updates) == {"006-D0000-00.6.20": now - 600}


def test_the_command_line_says_nothing_was_removed_only_when_no_file_was(tmp_path, clock,
                                                                         small_limit,
                                                                         monkeypatch, capsys):
    """One update file gone before its removal (something else removed it), the other one
    removed: what was removed is told, never that nothing was."""
    st, _dev = make_device(tmp_path, dumps=[])
    update_file(st, "GPS.GCD", b"g" * 40)
    wifi = update_file(st, "WIFI.GCD", b"w" * 40)
    offered(monkeypatch, st, update("GARMIN/RemoteSW/GPS.GCD", size=40),
            update("GARMIN/RemoteSW/WIFI.GCD", size=40))
    real_delete = LocalStorage.delete

    def gone_first(self, path, strict=False):
        if path.endswith("GPS.GCD"):
            (self.root / path).unlink()  # something else removed it a moment before
        return real_delete(self, path, strict)

    monkeypatch.setattr(LocalStorage, "delete", gone_first)
    code, out = run_cli(st, "--free-space", "--apply", capsys=capsys)
    assert code == 0 and not wifi.exists()
    assert "Removed: the file of GPS software 6.20, 40 B freed." in out.splitlines()
    assert "Nothing removed: the files were no longer on the device." not in out.splitlines()
