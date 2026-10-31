"""`openexpress sync` on a watch connected as a USB drive, and the service under it: synced
both ways as a device over MTP when service.drive_refusal lets it, with the reason when it
does not (its unit-info asked when it is not kept, read only, once); the account asked anew
before the uploads; its files checked as any device's; the files of Garmin Connect's queue
written to the folders its GarminDevice.xml names and read back whole before Garmin Connect
is told, its Connect IQ apps left in the queue; the drive ejected after a sync that wrote to
it. baseline and backup work on a drive; apps, maps and music refuse one. All devices,
answers and errors are made up."""

import logging

import pytest
from fakes import (
    ACTIVITY_URL,
    COURSE,
    MONITORING_URL,
    RTL515_XML,
    DriveAccount,
    SyncAccount,
    drive_session,
)

from openexpress import cli, i18n, last_sync, service, sync
from openexpress.connect import ApiError
from openexpress.device.storage import DeviceError, LocalStorage, UmsStorage
from openexpress.device.xmldevice import parse

gio_storage = pytest.importorskip("openexpress.device.gio_storage")

UNIT = SyncAccount.UNIT

DISABLED_TEXT = ("This device is connected as a USB drive, and Garmin's server says that "
                 "Garmin Connect does not take its data (connect support: Disabled), so it is "
                 "not synced.")
NO_TYPES_TEXT = ("This device is connected as a USB drive and declares no data that goes from "
                 "it to Garmin Connect, so there is nothing to sync.")
NO_ANSWER_TEXT = ("This device is connected as a USB drive, and Garmin's server could not be "
                  "asked whether Garmin Connect takes its data, so it is not synced: ")
NETWORK_ERROR = ApiError("Device info: network error (made-up connection refused)")


def drive_at(root, monkeypatch):
    """The command line finds the device in `root` connected as a USB drive."""
    storage = drive_session(root, None).storage
    monkeypatch.setattr(service, "open_device_or_drive", lambda **_kw: storage)
    return storage


def account_for(monkeypatch, answer, **kw):
    account = DriveAccount(answer, **kw)
    monkeypatch.setattr(cli, "make_client", lambda settings, need_auth: account)
    return account


@pytest.mark.parametrize("answer", [{"connectsupport": "Required", "devicemodel": "Made-up"},
                                    {"devicemodel": "Made-up"}, {}])
def test_a_drive_that_may_sync_gets_the_plan_of_its_uploads(device_root, monkeypatch, capsys,
                                                           log_file, answer):
    """unit-info says "Required", or nothing of Garmin Connect: the dry run as for any
    device, with its files to upload."""
    drive_at(device_root, monkeypatch)
    account = account_for(monkeypatch, answer)
    assert cli.main(["sync"]) == 0
    out = capsys.readouterr().out
    assert "To upload: 3 files" in out and "Dry run: nothing was uploaded" in out
    assert account.asked.count("unit-info") == 1 and "upload" not in account.asked
    support = answer.get("connectsupport", "not given")
    assert (f"sync: a USB drive that may sync (connect support: {support})"
            in log_file.read_text())


def test_its_kept_answer_of_unit_info_is_not_asked_again(device_root, monkeypatch):
    drive_at(device_root, monkeypatch)
    account = account_for(monkeypatch, {"connectsupport": "Required"})
    assert cli.main(["sync"]) == 0
    assert cli.main(["sync"]) == 0
    assert account.asked.count("unit-info") == 1


def test_a_drive_that_garmin_connect_does_not_serve_is_refused_so(device_root, monkeypatch,
                                                                  capsys, log_file):
    drive_at(device_root, monkeypatch)
    account = account_for(monkeypatch, {"connectsupport": "Disabled"})
    assert cli.main(["sync"]) == 1
    assert DISABLED_TEXT in capsys.readouterr().err
    assert account.asked == ["unit-info"]  # nothing of the account, no plan
    assert "sync: a USB drive not synced: connect-disabled" in log_file.read_text()


def test_without_an_answer_of_unit_info_the_drive_is_refused_with_the_error(
        device_root, monkeypatch, capsys, log_file):
    drive_at(device_root, monkeypatch)
    account = account_for(monkeypatch, NETWORK_ERROR)
    assert cli.main(["sync"]) == 1
    err = capsys.readouterr().err
    assert NO_ANSWER_TEXT + "Device info: network error (made-up connection refused)" in err
    assert account.asked == ["unit-info"]
    assert "sync: a USB drive not synced: no-unit-info" in log_file.read_text()


def test_a_drive_without_upload_types_is_refused_without_asking_anything(tmp_path, monkeypatch,
                                                                         capsys, log_file):
    """A Varia: its GarminDevice.xml declares no data type, so no server is asked."""
    root = tmp_path / "varia"
    (root / "GARMIN").mkdir(parents=True)
    (root / "GARMIN" / "GarminDevice.xml").write_text(RTL515_XML)
    assert not parse(RTL515_XML.encode()).files
    drive_at(root, monkeypatch)
    account = account_for(monkeypatch, {"connectsupport": "Required"})
    assert cli.main(["sync"]) == 1
    assert NO_TYPES_TEXT in capsys.readouterr().err
    assert account.asked == []
    assert "sync: a USB drive not synced: no-upload-types" in log_file.read_text()


@pytest.mark.parametrize("command", [["apps"], ["music"], ["maps"]])
def test_apps_maps_and_music_refuse_a_drive(device_root, monkeypatch, capsys, command):
    drive_at(device_root, monkeypatch)
    account = account_for(monkeypatch, {"connectsupport": "Required"})
    assert cli.main(command) == 1
    assert gio_storage.mass_storage_text() in capsys.readouterr().err
    assert account.asked == []


def test_a_backup_of_a_drive_copies_its_files_and_writes_nothing_to_it(device_root, tmp_path,
                                                                        monkeypatch, capsys):
    """As over MTP: the files of the device read, written to the computer."""
    before = files(device_root)
    drive_at(device_root, monkeypatch)
    dest = tmp_path / "backups"
    assert cli.main(["backup", "--dest", str(dest)]) == 0
    assert "Dry run: about" in capsys.readouterr().out and not dest.exists()
    assert cli.main(["backup", "--dest", str(dest), "--apply"]) == 0
    (copy,) = (dest / str(UNIT)).iterdir()
    assert (copy / "GarminDevice.xml").read_bytes() == (
        device_root / "GARMIN" / "GarminDevice.xml").read_bytes()
    assert any(p.suffix.lower() == ".fit" for p in copy.rglob("*"))
    assert files(device_root) == before


# --- the queue of Garmin Connect -----------------------------------------------------------

QUEUE_NOTE = "[stays in Garmin Connect's queue]"


def test_the_dry_run_of_a_drive_plans_the_queue_too(device_root, monkeypatch, capsys):
    """As for a device over MTP; a Connect IQ app is told to stay in Garmin Connect's queue
    and is not counted with what comes to the device."""
    drive_at(device_root, monkeypatch)
    account = account_for(monkeypatch, {"connectsupport": "Required"}, messages=[COURSE, APP])
    assert cli.main(["sync"]) == 0
    out = capsys.readouterr().out
    assert "queue" in account.asked and "download" not in account.asked
    assert "To upload: 3 files" in out and "To download from Connect to the device: 1" in out
    (app_line,) = [line for line in out.splitlines() if "IQWatchApps" in line]
    assert app_line.endswith(QUEUE_NOTE)


@pytest.mark.parametrize(("flag", "uploads", "queue"), [("--upload-only", True, False),
                                                         ("--download-only", False, True)])
def test_the_one_way_syncs_of_a_drive_go_as_over_mtp(device_root, monkeypatch, capsys, flag,
                                                     uploads, queue):
    drive_at(device_root, monkeypatch)
    account = account_for(monkeypatch, {"connectsupport": "Required"}, messages=[COURSE])
    assert cli.main(["sync", flag]) == 0
    out = capsys.readouterr().out
    assert ("To upload:" in out) is uploads and ("To download from Connect" in out) is queue
    assert ("queue" in account.asked) is queue


def test_a_device_not_a_drive_has_the_queue_in_its_dry_run(device_root, monkeypatch,
                                                                  capsys):
    monkeypatch.setattr(service, "open_device_or_drive",
                        lambda **_kw: LocalStorage(device_root))
    account = account_for(monkeypatch, {"connectsupport": "Required"}, messages=[COURSE, APP])
    assert cli.main(["sync"]) == 0
    out = capsys.readouterr().out
    assert "queue" in account.asked and "unit-info" not in account.asked
    assert "To download from Connect to the device: 2" in out and QUEUE_NOTE not in out


def test_the_note_of_an_app_left_in_the_queue_is_said_in_polish_too(device_root, monkeypatch,
                                                                    capsys):
    drive_at(device_root, monkeypatch)
    account_for(monkeypatch, {"connectsupport": "Required"}, messages=[APP])
    try:
        assert cli.main(["--lang", "pl", "sync"]) == 0
    finally:
        i18n.setup("en")
    assert "[zostaje w kolejce Garmin Connect]" in capsys.readouterr().out


# --- uploads (sync --apply) -----------------------------------------------------------------


def files(root):
    """Every file under `root`: its path, size, time of change and bytes."""
    return {(str(p.relative_to(root)), p.stat().st_size, p.stat().st_mtime_ns, p.read_bytes())
            for p in root.rglob("*") if p.is_file()}


def test_sync_apply_on_a_drive_uploads_its_files_and_writes_the_queue(
        device_root, monkeypatch, capsys, log_file):
    """Garmin's word on the device and the account asked anew come before the first upload;
    then the course of the queue comes to the folder GarminDevice.xml names and Garmin Connect
    is told; no other file of the drive changes."""
    sync.SyncState(UNIT).save()  # synced here before: no first-sync choice
    before = files(device_root)
    drive_at(device_root, monkeypatch)
    account = account_for(monkeypatch, {"connectsupport": "Required"}, messages=[COURSE])
    ejects(monkeypatch)
    assert cli.main(["sync", "--apply"]) == 0
    assert sorted(url for url, _size, _last in account.uploads) == [
        ACTIVITY_URL, ACTIVITY_URL, MONITORING_URL]
    asked = account.asked
    assert asked.index("unit-info") < asked.index("account, fresh") < asked.index("upload")
    assert asked.index("upload") < asked.index("queue") < asked.index("acknowledge")
    after = files(device_root)
    assert before <= after
    (new,) = after - before
    assert new[0].startswith("GARMIN/NewFiles/course_") and new[3] == b"COURSE"
    assert account.acks == [COURSE["messageid"]]
    assert "3 uploaded" in capsys.readouterr().out
    log = log_file.read_text()
    assert "uploads: 3 uploaded, 0 duplicates, 0 already in Connect" in log
    assert "downloads: 1 written, 0 empty, 0 failed, 0 not acknowledged" in log


# --- ejected after a sync that wrote to it -------------------------------------------------

NOT_EJECTED_FILES = ("Do not unplug the cable yet: the device may not see the files from Garmin "
                     "Connect. Close any program that uses the drive and eject it in the file "
                     "manager.")


def ejects(monkeypatch, refusal=None):
    """gio_storage.eject noting what it was given; with `refusal` the system refuses."""
    asked = []

    def eject(storage):
        asked.append(storage)
        if refusal is not None:
            raise DeviceError(refusal)

    monkeypatch.setattr(gio_storage, "eject", eject)
    return asked


def test_a_drive_is_ejected_after_a_sync_that_wrote_to_it(device_root, monkeypatch, capsys):
    """So that the watch takes the files, as after an update; the cable may go out after."""
    sync.SyncState(UNIT).save()
    storage = drive_at(device_root, monkeypatch)
    account_for(monkeypatch, {"connectsupport": "Required"}, messages=[COURSE])
    asked = ejects(monkeypatch)
    assert cli.main(["sync", "--apply"]) == 0
    assert asked == [storage]
    out = capsys.readouterr().out
    assert "Ejecting the device…" in out and gio_storage.ejected_text() in out


@pytest.mark.parametrize("command", [["sync", "--apply"], ["sync"]])
def test_a_drive_nothing_was_written_to_is_not_ejected(device_root, monkeypatch, command):
    """Nothing in the queue (only uploads), or a dry run with a course in it."""
    sync.SyncState(UNIT).save()
    drive_at(device_root, monkeypatch)
    account_for(monkeypatch, {"connectsupport": "Required"},
                messages=[COURSE] if command == ["sync"] else [])
    asked = ejects(monkeypatch)
    assert cli.main(command) == 0
    assert asked == []


def test_a_device_not_a_drive_is_not_ejected(device_root, monkeypatch):
    sync.SyncState(UNIT).save()
    monkeypatch.setattr(service, "open_device_or_drive",
                        lambda **_kw: LocalStorage(device_root))
    account_for(monkeypatch, {"connectsupport": "Required"}, messages=[COURSE])
    asked = ejects(monkeypatch)
    assert cli.main(["sync", "--apply"]) == 0
    assert asked == []


def test_a_drive_the_system_does_not_eject_says_the_cable_must_stay_in(device_root,
                                                                       monkeypatch, capsys):
    sync.SyncState(UNIT).save()
    drive_at(device_root, monkeypatch)
    account_for(monkeypatch, {"connectsupport": "Required"}, messages=[COURSE])
    ejects(monkeypatch, refusal="made-up: the drive is busy")
    assert cli.main(["sync", "--apply"]) == 1
    err = capsys.readouterr().err
    assert "The device was not ejected: made-up: the drive is busy" in err
    assert NOT_EJECTED_FILES in err


def test_a_drive_not_on_the_account_uploads_nothing(device_root, monkeypatch, capsys):
    sync.SyncState(UNIT).save()
    before = files(device_root)
    drive_at(device_root, monkeypatch)
    account = account_for(monkeypatch, {"connectsupport": "Required"},
                          devices=[{"unitid": 3000000099}])
    assert cli.main(["sync", "--apply"]) == 1
    assert "This device is not registered to your Garmin account." in capsys.readouterr().err
    assert account.uploads == [] and "upload" not in account.asked
    assert files(device_root) == before


def test_a_fit_file_cut_short_on_a_drive_is_not_uploaded(device_root, monkeypatch, capsys,
                                                        log_file):
    """Checked as on any device (fit.incompleteness): kept for the next sync, the rest
    uploaded."""
    monitoring = device_root / "GARMIN" / "Monitor" / "M1.FIT"
    monitoring.write_bytes(monitoring.read_bytes()[:-4])
    sync.SyncState(UNIT).save()
    before = files(device_root)
    drive_at(device_root, monkeypatch)
    account = account_for(monkeypatch, {"connectsupport": "Required"})
    assert cli.main(["sync", "--apply"]) == 0
    assert [url for url, _size, _last in account.uploads] == [ACTIVITY_URL, ACTIVITY_URL]
    assert "1 file was still being written by the device" in capsys.readouterr().out
    assert "upload skipped: FIT_TYPE_32 still being written by the device" in (
        log_file.read_text())
    assert files(device_root) == before


def test_what_garmin_connect_has_already_is_a_success(device_root, monkeypatch, capsys,
                                                     log_file):
    """An activity Connect has (sent by the phone) is known before sending; a file Garmin
    answers 409 for is a duplicate. Both are told as already in Garmin Connect; no error."""
    sync.SyncState(UNIT).save()
    before = files(device_root)
    drive_at(device_root, monkeypatch)
    account = account_for(monkeypatch, {"connectsupport": "Required"}, matches={"1100000000"},
                          statuses={MONITORING_URL: 409})
    assert cli.main(["sync", "--apply"]) == 0
    out = capsys.readouterr().out
    assert "3 new files, 1 uploaded" in out and "2 files were already in Garmin Connect" in out
    assert "ERROR" not in out
    assert sorted(url for url, _size, _last in account.uploads) == [ACTIVITY_URL,
                                                                     MONITORING_URL]
    assert "uploads: 1 uploaded, 1 duplicates, 1 already in Connect" in log_file.read_text()
    assert files(device_root) == before


@pytest.mark.parametrize("found", ["drive", "mtp"])
def test_the_first_sync_offers_both_choices_on_a_drive_too(device_root, monkeypatch, capsys,
                                                           found):
    """`baseline` only reads the files and marks them on the computer: a drive is offered it
    as a device over MTP is; nothing is uploaded before the choice."""
    if found == "drive":
        drive_at(device_root, monkeypatch)
    else:
        monkeypatch.setattr(service, "open_device_or_drive",
                            lambda **_kw: LocalStorage(device_root))
    account = account_for(monkeypatch, {"connectsupport": "Required"})
    assert cli.main(["sync", "--apply"]) == 3
    err = capsys.readouterr().err
    assert "openexpress baseline --apply" in err and "openexpress sync --apply --all" in err
    assert account.uploads == [] and "upload" not in account.asked


def test_baseline_on_a_drive_marks_its_files_and_writes_nothing_to_it(device_root, monkeypatch,
                                                                      capsys):
    before = files(device_root)
    drive_at(device_root, monkeypatch)
    account = account_for(monkeypatch, {"connectsupport": "Required"})
    assert cli.main(["baseline"]) == 0
    assert "Dry run: 3 files would be marked as already uploaded." in capsys.readouterr().out
    assert not sync.synced_here(UNIT)
    assert cli.main(["baseline", "--apply"]) == 0
    assert "Marked 3 files as already uploaded." in capsys.readouterr().out
    assert sync.synced_here(UNIT) and account.uploads == []
    assert files(device_root) == before


@pytest.mark.parametrize(("answer", "said"), [({"connectsupport": "Disabled"}, DISABLED_TEXT),
                                              (NETWORK_ERROR, NO_ANSWER_TEXT)])
def test_a_sync_of_a_drive_asks_garmin_before_anything_else(device_root, answer, said):
    """service.run_sync itself, whoever calls it: refused before the account or an upload."""
    account = DriveAccount(answer)
    with pytest.raises(service.MassStorageMode) as refused:
        service.run_sync(drive_session(device_root, account),
                         options=service.SyncOptions(drive=True))
    assert str(refused.value).startswith(said)
    assert account.asked == ["unit-info"] and account.uploads == []


@pytest.mark.parametrize("options", [None, "default"])
def test_a_sync_of_a_drive_is_refused_unless_asked_for_a_drive(device_root, options):
    """Only the command line and the window ask for a drive (SyncOptions.drive); the
    background service never does: a drive is not synced then."""
    options = {None: None, "default": service.SyncOptions()}[options]
    account = DriveAccount({"connectsupport": "Required"})
    with pytest.raises(service.MassStorageMode,
                       match="A device connected as a USB drive is not synced."):
        service.run_sync(drive_session(device_root, account), options=options)
    assert account.asked == [] and account.uploads == []


def test_a_sync_asked_for_a_drive_uploads_and_writes_the_queue(device_root):
    """The files go to Garmin Connect, a course of the queue comes to the folder its
    GarminDevice.xml names, and Garmin Connect is told it was received."""
    sync.SyncState(UNIT).save()
    account = DriveAccount({"connectsupport": "Required"}, messages=[COURSE])
    summary = service.run_sync(drive_session(device_root, account),
                               options=service.SyncOptions(drive=True))
    assert (summary.uploaded, summary.downloaded, summary.errors) == (3, 1, [])
    (written,) = (device_root / "GARMIN" / "NewFiles").iterdir()
    assert written.name.startswith("course_") and written.read_bytes() == b"COURSE"
    assert account.acks == [COURSE["messageid"]]


APP = {"messageid": 7, "devicexmldatatype": "IQWatchApps",
       "metadata": {"messageurl": "app/7", "messagename": "Made-up app",
                    "appdetails": {"id": "2a6b4b8e-9a37-4a3e-8f0f-2e7d1f0b9c11"}}}


def test_connect_iq_apps_of_the_queue_stay_in_it_on_a_drive(device_root, caplog):
    """Garmin Express refuses a protected app on a drive, and which apps are protected is
    not read here: an app waits in Garmin Connect's queue, one line says so, and the course of
    the queue is written all the same."""
    sync.SyncState(UNIT).save()
    apps = device_root / "GARMIN" / "APPS"
    before = sorted(str(p.relative_to(apps)) for p in apps.rglob("*"))
    account = DriveAccount({"connectsupport": "Required"}, messages=[COURSE, APP])
    with caplog.at_level(logging.INFO, logger=service.log.name):
        summary = service.run_sync(drive_session(device_root, account),
                                   options=service.SyncOptions(drive=True))
    assert summary.errors == [] and summary.downloaded == 1
    assert account.acks == [COURSE["messageid"]]  # the app's message stays in the queue
    assert sorted(str(p.relative_to(apps)) for p in apps.rglob("*")) == before
    said = [text for text, _warning in last_sync.describe(summary.record(UNIT)).steps]
    assert ("1 Connect IQ app waits in the queue: openExpress cannot install apps on this "
            "device over USB yet") in said
    assert "sync: 1 Connect IQ app of the queue left in it (a USB drive)" in caplog.messages


TRUEUP = {"messageid": 9, "messagetype": "true_up", "devicexmldatatype": "FIT_TYPE_39",
          "metadata": {"messageurl": "trueup/9"}}
TAKES_TRUEUP = ("<DataType><Name>FIT_TYPE_39</Name><File><Location><Path>Garmin/NewFiles</Path>"
                "<FileExtension>FIT</FileExtension></Location><TransferDirection>InputToUnit"
                "</TransferDirection></File></DataType>")


def test_trueup_of_the_queue_is_written_to_a_drive_as_over_mtp(device_root):
    """The same steps as over MTP and in Garmin Express: fetched, acknowledged, written to
    the folder its GarminDevice.xml names; told in the details."""
    xml = device_root / "GARMIN" / "GarminDevice.xml"
    xml.write_text(xml.read_text().replace("</MassStorageMode>",
                                           TAKES_TRUEUP + "</MassStorageMode>"))
    sync.SyncState(UNIT).save()
    account = DriveAccount({"connectsupport": "Required"}, messages=[TRUEUP])
    account.download_message = lambda url, absolute: b"TRUEUP" if "trueup" in url else None
    summary = service.run_sync(drive_session(device_root, account),
                               options=service.SyncOptions(drive=True))
    assert summary.errors == [] and summary.downloaded == 1
    (written,) = (device_root / "GARMIN" / "NewFiles").iterdir()
    assert written.read_bytes() == b"TRUEUP" and account.acks == [TRUEUP["messageid"]]
    assert service.trueup_step() in summary.lines


class GarblingDrive(UmsStorage):
    """A drive that keeps other bytes than it was given, of the same size."""

    def _write(self, real_path, data):
        super()._write(real_path, bytes(b ^ 0xFF for b in data))


def test_a_file_of_the_queue_kept_otherwise_by_the_drive_is_not_acknowledged(device_root):
    """Read back whole before Garmin Connect is told: another content (of the same size) is
    deleted and not acknowledged, so the next sync brings it again."""
    sync.SyncState(UNIT).save()
    account = DriveAccount({"connectsupport": "Required"}, messages=[COURSE])
    summary = service.run_sync(drive_session(device_root, account, GarblingDrive),
                               options=service.SyncOptions(drive=True))
    assert summary.downloaded == 0 and account.acks == []
    assert any("Write verification of" in error for error in summary.errors)
    assert list((device_root / "GARMIN" / "NewFiles").iterdir()) == []


def test_a_drive_that_keeps_what_it_was_given_passes_the_check(tmp_path):
    drive = UmsStorage(tmp_path, tmp_path.as_uri() + "/")
    assert drive.write_checked("GARMIN/NewFiles/a.fit", b"WHOLE") == "GARMIN/NewFiles/a.fit"
    assert (tmp_path / "GARMIN" / "NewFiles" / "a.fit").read_bytes() == b"WHOLE"
    with pytest.raises(DeviceError, match="Write verification of"):
        GarblingDrive(tmp_path, tmp_path.as_uri() + "/").write_checked("GARMIN/b.fit", b"WHOLE")
    assert not (tmp_path / "GARMIN" / "b.fit").exists()


# --- opening a drive (the window's check_session) -------------------------------------------


def test_opening_a_drive_that_may_sync_asks_the_account_as_over_mtp(device_root):
    account = DriveAccount({"connectsupport": "Required"})
    s = service.check_session(drive_session(device_root, account))
    assert s.drive_refusal is None and s.ownership_error is None
    assert s.account_devices == [{"unitid": UNIT}]
    assert account.asked == ["unit-info", "account"]


def test_opening_a_drive_not_on_the_account_keeps_the_refusal(device_root):
    from openexpress.connect import NotAccountDevice

    account = DriveAccount({"connectsupport": "Required"}, devices=[{"unitid": 3000000099}])
    s = service.check_session(drive_session(device_root, account))
    assert isinstance(s.ownership_error, NotAccountDevice) and s.drive_refusal is None


@pytest.mark.parametrize(("answer", "reason", "error"), [
    ({"connectsupport": "Disabled"}, service.CONNECT_DISABLED, ""),
    (NETWORK_ERROR, service.NO_UNIT_INFO, "Device info: network error (made-up connection refused)"),
])
def test_opening_a_drive_that_is_not_synced_keeps_why_and_asks_no_account(device_root, answer,
                                                                          reason, error):
    account = DriveAccount(answer)
    s = service.check_session(drive_session(device_root, account))
    assert s.drive_refusal == (reason, error) and account.asked == ["unit-info"]
    assert s.account_devices is None and s.ownership_error is None


def test_opening_a_drive_without_upload_types_asks_nothing(tmp_path):
    root = tmp_path / "varia"
    (root / "GARMIN").mkdir(parents=True)
    (root / "GARMIN" / "GarminDevice.xml").write_text(RTL515_XML)
    account = DriveAccount({"connectsupport": "Required"})
    s = service.check_session(drive_session(root, account))
    assert s.drive_refusal == (service.NO_UPLOAD_TYPES, "") and account.asked == []


def test_the_activities_of_a_drive_are_counted(device_root):
    """As those of a device over MTP: listed in its activity folder, none read."""
    sync.SyncState(UNIT).save()
    device = parse((device_root / "GARMIN" / "GarminDevice.xml").read_bytes())
    counted = sync.activities_to_sync(device, UmsStorage(device_root, device_root.as_uri()))
    assert counted is not None and counted.count == 2


def test_the_refusals_are_said_in_polish_too():
    i18n.setup("pl")
    try:
        said = {reason: service.drive_sync_refusal_text(reason, "błąd")
                for reason in (service.NO_UPLOAD_TYPES, service.CONNECT_DISABLED,
                               service.NO_UNIT_INFO)}
    finally:
        i18n.setup("en")
    assert all("dysk" in text and "USB" in text for text in said.values())
    assert said[service.NO_UNIT_INFO].endswith(": błąd")
    assert "Disabled" in said[service.CONNECT_DISABLED]
