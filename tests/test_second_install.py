"""A second install of openExpress on this computer: the version from source reads (never writes)
what the Flatpak keeps of the same device. A file the Flatpak recorded as sent is not sent
again, a message of the queue it wrote but could not acknowledge is only acknowledged, its lock
held stops this sync before anything is read, and when it synced the device last the sync says
so once. In the Flatpak none of it runs. Made-up records only."""

import fcntl
import hashlib
import json
import os
import time

import pytest
from fakes import COURSE, SyncAccount, SyncClient

from openexpress import cli, paths, service, sync
from openexpress.connect import ApiError
from openexpress.device.storage import LocalStorage
from openexpress.device.xmldevice import parse

UNIT = SyncAccount.UNIT  # the test device (tests/conftest.py)
FLATPAK_SYNCED = 1_790_000_000.0  # when the Flatpak synced the device last (made up)
SENT = {"FIT_TYPE_4": ["2026-10-01-10-00-00.fit", "2026-10-02-10-00-00.fit"],
        "FIT_TYPE_32": ["m1.fit"]}


def flatpak_keeps(state=None, unacked=None, last_sync=FLATPAK_SYNCED):
    """The Flatpak's records of the test device, in the folder this test has for it."""
    device = paths.flatpak_data_dir() / "devices" / str(UNIT)
    (device / "sync").mkdir(parents=True)
    if state is not None:
        (device / "sync" / "state.json").write_text(json.dumps(state))
    if unacked is not None:
        (device / "sync" / "unacked.json").write_text(json.dumps(unacked))
    if last_sync is not None:
        (device / "last_sync.json").write_text(json.dumps({"time": last_sync, "unit_id": UNIT}))
    return device


def snapshot(folder):
    return sorted((os.path.relpath(os.path.join(top, name), folder),
                   os.stat(os.path.join(top, name)).st_mtime_ns,
                   open(os.path.join(top, name), "rb").read())
                  for top, _folders, names in os.walk(folder) for name in names)


@pytest.fixture
def device(device_root, monkeypatch):
    monkeypatch.setattr(service, "open_device_or_drive",
                        lambda **_kw: LocalStorage(device_root))
    return device_root


@pytest.fixture
def account(monkeypatch):
    account = SyncAccount()
    monkeypatch.setattr(cli, "make_client", lambda settings, need_auth: account)
    return account


def sentence(when: float) -> str:
    day = time.strftime("%Y-%m-%d", time.localtime(when))
    return ("This device was also synced by openExpress (Flatpak), last on "
            f"{day}; files it sent are not sent again")


def test_the_second_install_plans_nothing_the_flatpak_sent_and_says_so(
        device, account, capsys):
    """The dry run of the version from source: nothing to upload (the Flatpak sent all of
    it), no question of a first sync, and the sentence."""
    flatpak_keeps(state={"completed": SENT, "singleton_md5": {}})
    assert cli.main(["sync"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert "To upload: 0 files (already uploaded earlier: 3)" in out
    assert sentence(FLATPAK_SYNCED) in out


def test_a_sync_of_the_second_install_sends_nothing_again_and_keeps_the_sentence(
        device, account, capsys):
    flatpak = flatpak_keeps(state={"completed": SENT, "singleton_md5": {}})
    before = snapshot(flatpak)
    assert cli.main(["sync", "--apply"]) == 0
    assert "upload" not in account.asked
    record = json.loads((paths.device_dir(UNIT) / "last_sync.json").read_text())
    assert {"kind": "other_install", "at": FLATPAK_SYNCED} in record["lines"]
    assert (sentence(FLATPAK_SYNCED), True) in service.describe_steps(record["lines"])
    # This install's own record now holds what the Flatpak sent; the Flatpak's is untouched.
    own = json.loads((paths.device_dir(UNIT) / "sync" / "state.json").read_text())
    assert own["completed"]["FIT_TYPE_4"] == SENT["FIT_TYPE_4"]
    assert snapshot(flatpak) == before


def test_no_sentence_when_this_install_synced_the_device_after_the_flatpak(device, account):
    flatpak_keeps(state={"completed": SENT, "singleton_md5": {}})
    own = paths.device_dir(UNIT)
    own.mkdir(parents=True, exist_ok=True)
    (own / "last_sync.json").write_text(json.dumps({"time": FLATPAK_SYNCED + 60}))
    assert cli.main(["sync", "--apply"]) == 0
    record = json.loads((own / "last_sync.json").read_text())
    assert not any(isinstance(line, dict) and line.get("kind") == "other_install"
                   for line in record["lines"])


def test_a_single_file_is_not_sent_again_only_when_its_content_is_the_same(device_root):
    st = LocalStorage(device_root)
    dev = parse(st.read("GARMIN/GarminDevice.xml"))
    settings = st.read("GARMIN/Settings/Settings.fit")
    config = {"datatypes": [{"datatypename": "FIT_TYPE_2", "uploadlocations": [
        {"url": "https://up.example/settings", "requiresauthentication": True}]}]}
    key = "FIT_TYPE_2:garmin/settings/settings.fit"
    flatpak_keeps(state={"completed": {}, "singleton_md5": {
        key: hashlib.md5(settings).hexdigest()}})
    assert sync.plan_uploads(dev, st, config, sync.state_of(UNIT)).candidates == []
    (paths.flatpak_data_dir() / "devices" / str(UNIT) / "sync" / "state.json").write_text(
        json.dumps({"completed": {}, "singleton_md5": {key: "an older content"}}))
    planned = sync.plan_uploads(dev, st, config, sync.state_of(UNIT)).candidates
    assert [c.datatype for c in planned] == ["FIT_TYPE_2"]


def test_a_message_the_flatpak_wrote_but_could_not_acknowledge_is_only_acknowledged(
        device_root):
    st = LocalStorage(device_root)
    dev = parse(st.read("GARMIN/GarminDevice.xml"))
    flatpak_keeps(unacked=[str(COURSE["messageid"])])
    client = SyncClient()
    report = sync.run_downloads(sync.plan_downloads(dev, [COURSE]), st, client,
                                state=sync.state_of(UNIT))
    assert client.acks == [COURSE["messageid"]] and report.written == []
    assert list((device_root / "GARMIN" / "NewFiles").iterdir()) == []


BUSY = "openExpress (Flatpak) is syncing a device now; try again when it is done."


@pytest.mark.parametrize("command", [["sync", "--apply"], ["baseline", "--apply"]],
                         ids=" ".join)
def test_the_flatpak_syncing_stops_this_install_before_it_writes(device, account, capsys,
                                                                 command):
    """Its sync stops before anything is read; baseline (which takes the same lock) too, with
    words that suit both."""
    flatpak = paths.flatpak_data_dir()
    flatpak.mkdir(parents=True)
    with open(flatpak / "sync.lock", "w") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        code = cli.main(command)
    assert code != 0
    assert BUSY in capsys.readouterr().err
    assert "upload" not in account.asked
    assert not (paths.device_dir(UNIT) / "sync" / "state.json").exists()


def test_in_the_flatpak_there_is_no_other_install(monkeypatch):
    monkeypatch.setattr(paths, "in_flatpak", lambda: True)
    assert paths.other_install_dir() is None
    monkeypatch.setattr(paths, "in_flatpak", lambda: False)
    assert paths.other_install_dir() == paths.flatpak_data_dir()


def test_an_acknowledgement_of_a_message_gone_already_counts_as_done(device_root):
    """Garmin Connect answers 404 to a message it no longer has (the other install
    acknowledged it): done, not "not acknowledged" for ever."""
    st = LocalStorage(device_root)
    dev = parse(st.read("GARMIN/GarminDevice.xml"))

    class Gone(SyncClient):
        def ack_message(self, mid):
            raise ApiError("not found", 404)

    flatpak_keeps(unacked=[str(COURSE["messageid"])])
    state = sync.state_of(UNIT)
    report = sync.run_downloads(sync.plan_downloads(dev, [COURSE]), st, Gone(), state=state)
    assert report.ack_failed == [] and report.written == []
    assert state.unacked() == set()


def test_an_empty_message_gone_already_counts_as_empty_not_failed(device_root):
    """Its file gone (204/404) and the message too when it is acknowledged (404: the phone, or
    the other install, took it meanwhile): an empty message, not a failure."""
    st = LocalStorage(device_root)
    dev = parse(st.read("GARMIN/GarminDevice.xml"))

    class Gone(SyncClient):
        def download_message(self, url, absolute):
            return None

        def ack_message(self, mid):
            raise ApiError("not found", 404)

    report = sync.run_downloads(sync.plan_downloads(dev, [COURSE]), st, Gone(),
                                state=sync.state_of(UNIT))
    assert report.failed == [] and len(report.empty) == 1 and report.written == []


def test_a_device_the_flatpak_synced_is_synced_and_its_records_change_the_stamp():
    """Its tile and page say no "Never synced", and a count of its activities kept against
    the sync state counts again once the Flatpak syncs it (its state.json changes)."""
    assert not sync.synced_here(UNIT) and service.sync_stamp(UNIT) is None
    device = flatpak_keeps(state={"completed": SENT, "singleton_md5": {}})
    assert sync.synced_here(UNIT)
    first = service.sync_stamp(UNIT)
    assert first is not None
    (device / "sync" / "state.json").write_text(json.dumps(
        {"completed": {**SENT, "FIT_TYPE_4": [*SENT["FIT_TYPE_4"], "new.fit"]},
         "singleton_md5": {}}))
    assert service.sync_stamp(UNIT) != first


def test_the_flatpaks_message_numbers_never_go_into_this_installs_record(device_root):
    st = LocalStorage(device_root)
    dev = parse(st.read("GARMIN/GarminDevice.xml"))
    flatpak_keeps(unacked=[str(COURSE["messageid"]), "999"])  # 999: gone from the queue
    state = sync.state_of(UNIT)
    sync.run_downloads(sync.plan_downloads(dev, [COURSE]), st, SyncClient(), state=state)
    assert "999" not in state.unacked()


def test_the_activities_of_a_device_the_flatpak_synced_are_counted(device_root):
    """Its page and tile count what is left to upload, not "never synced here"."""
    st = LocalStorage(device_root)
    dev = parse(st.read("GARMIN/GarminDevice.xml"))
    flatpak_keeps(state={"completed": SENT, "singleton_md5": {}})
    assert sync.activities_to_sync(dev, st).count == 0


@pytest.mark.parametrize("when", ["true", "1e400", "\"yesterday\""])
def test_a_time_of_the_flatpaks_last_sync_no_clock_can_show_is_none(when):
    device = flatpak_keeps(state={"completed": SENT, "singleton_md5": {}}, last_sync=None)
    (device / "last_sync.json").write_text('{"time": ' + when + "}")
    assert sync.other_install_records(UNIT).last_sync is None
