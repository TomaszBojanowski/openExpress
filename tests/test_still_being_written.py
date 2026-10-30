"""A FIT file the device was still writing when it was read (cut short at the size the device
gave when it was connected) is not uploaded nor counted as done: the next sync reads it again.
The sync summary says so in one line (window and command line). All devices, files and answers
are made up."""

import json
import types
from pathlib import Path

from fakes import SyncAccount

from openexpress import cli, i18n, service, sync
from openexpress.device.storage import LocalStorage
from openexpress.device.xmldevice import parse
from openexpress.settings import Settings

UNIT = SyncAccount.UNIT
NB = " "
LINE = ("1 file was still being written by the device – disconnect and reconnect it, then "
        "sync again")


def _cut_monitoring(root: Path) -> bytes:
    monitoring = root / "GARMIN/Monitor/M1.FIT"
    whole = monitoring.read_bytes()
    monitoring.write_bytes(whole[:-4])
    return whole


def _session(root: Path, account):
    storage = LocalStorage(root)
    return service.DeviceSession(storage, parse(storage.read("GARMIN/GarminDevice.xml")),
                                 Settings(), account)


def test_a_sync_says_it_and_leaves_the_file_for_the_next_one(device_root):
    whole = _cut_monitoring(device_root)
    sync.SyncState(UNIT).save()
    summary = service.run_sync(_session(device_root, SyncAccount(verified_unit_id=None)))
    assert summary.uploaded == 2  # the two activities
    assert {"kind": "still_written", "count": 1} in service.load_last_sync(UNIT)["lines"]
    assert not sync.SyncState(UNIT).is_completed("FIT_TYPE_32", "M1.FIT")
    (device_root / "GARMIN/Monitor/M1.FIT").write_bytes(whole)
    summary = service.run_sync(_session(device_root, SyncAccount(verified_unit_id=None)))
    assert summary.uploaded == 1  # now whole
    assert "still_written" not in {line.get("kind") for line in summary.lines
                                   if isinstance(line, dict)}


def test_the_command_line_says_so_once(device_root, monkeypatch, capsys):
    _cut_monitoring(device_root)
    account = SyncAccount(verified_unit_id=None)
    monkeypatch.setattr(cli, "make_client", lambda settings, need_auth: account)
    sync.SyncState(UNIT).save()
    assert cli.main(["--path", str(device_root), "sync", "--apply"]) == 0
    assert capsys.readouterr().out.splitlines().count(LINE) == 1


def test_the_words_of_the_line():
    assert service._describe_step({"kind": "still_written", "count": 1}) == [(LINE, True)]
    assert service._describe_step({"kind": "still_written", "count": 3}) == [(
        ("3 files were still being written by the device – disconnect and reconnect it, then "
         "sync again"), True)]
    i18n.setup("pl")
    try:
        words = [service._describe_step({"kind": "still_written", "count": n})[0][0]
                 for n in (1, 3, 5)]
    finally:
        i18n.setup("en")
    rest = f"przez urządzenie – odłącz je i{NB}podłącz ponownie, potem zsynchronizuj jeszcze raz"
    assert words == [f"1 plik był jeszcze zapisywany {rest}",
                     f"3 pliki były jeszcze zapisywane {rest}",
                     f"5 plików było jeszcze zapisywanych {rest}"]


def test_the_synthetic_pattern_is_what_a_sync_writes():
    """tests/data/last_sync/format-0.7.11-still-written.json, a synthetic pattern (no real sync
    has written one yet), has the lines a sync writes for a device with two new files, one of
    them still being written."""
    pattern = json.loads((Path(__file__).parent / "data/last_sync"
                          / "format-0.7.11-still-written.json").read_text(encoding="utf-8"))
    report = types.SimpleNamespace(uploaded=[0], already_in_connect=[], duplicates=[],
                                   rejected=[], failed=[])
    assert pattern["lines"] == [
        service.new_files_step([types.SimpleNamespace(datatype="FIT_TYPE_32"),
                                types.SimpleNamespace(datatype="FIT_TYPE_77")]),
        service.uploads_step(report),
        service.still_written_step(1)]
