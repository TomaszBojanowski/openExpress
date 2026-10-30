"""`openexpress sync` on a watch connected as a USB drive: its dry run shows the plan of its
uploads when service.drive_sync_refusal lets it sync, and the refusal says which reason
stopped it. Its unit-info is asked when it is not kept (read only), once. Its sync only
uploads: Garmin Connect's queue is not asked, and --download-only is refused. The other
commands still refuse a drive. All devices, answers and errors are made up."""

import pytest
from fakes import COURSE, RTL515_XML, SyncAccount

from openexpress import cli, i18n, service
from openexpress.connect import ApiError, GarminClient
from openexpress.device.storage import LocalStorage, UmsStorage
from openexpress.device.xmldevice import parse
from openexpress.settings import Settings

gio_storage = pytest.importorskip("openexpress.device.gio_storage")

DISABLED_TEXT = ("This device is connected as a USB drive, and Garmin's server says that "
                 "Garmin Connect does not take its data (connect support: Disabled), so it is "
                 "not synced.")
NO_TYPES_TEXT = ("This device is connected as a USB drive and declares no data that goes from "
                 "it to Garmin Connect, so there is nothing to sync.")
NO_ANSWER_TEXT = ("This device is connected as a USB drive, and Garmin's server could not be "
                  "asked whether Garmin Connect takes its data, so it is not synced: ")
NETWORK_ERROR = ApiError("Device info: network error (made-up connection refused)")
UPLOADS_ONLY_TEXT = ("On a USB drive the sync only uploads: nothing is downloaded from Garmin "
                     "Connect to the device.")
DOWNLOAD_ONLY_TEXT = ("This device is connected as a USB drive: its sync only uploads, so "
                      "there is nothing to do with --download-only.")


class DriveAccount(SyncAccount):
    """Garmin Connect as a sync asks it, and Garmin's update server answering unit-info with
    `answer` (an exception: raised). unit-info goes through the kept answers as the real
    client's does; `asked` notes each question that reached the server."""

    unit_info = GarminClient.unit_info

    def __init__(self, answer, **kw):
        super().__init__(verified_unit_id=None, **kw)
        self.settings = Settings()
        self.answer = answer

    def _omt(self, method, path, what, quiet=False, **kw):
        self.asked.append("unit-info")
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def drive_at(root, monkeypatch):
    """The command line finds the device in `root` connected as a USB drive."""
    storage = UmsStorage(root, root.as_uri() + "/",
                         usb=gio_storage.GarminUsb("4f42", 1, 9, serial="MADE-UP-SERIAL-8"))
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


@pytest.mark.parametrize("command", [["baseline"], ["apps"], ["music"], ["maps"], ["backup"]])
def test_the_other_commands_still_refuse_a_drive(device_root, monkeypatch, capsys, command):
    drive_at(device_root, monkeypatch)
    account = account_for(monkeypatch, {"connectsupport": "Required"})
    assert cli.main(command) == 1
    assert gio_storage.mass_storage_text() in capsys.readouterr().err
    assert account.asked == []


def test_sync_apply_on_a_drive_that_may_sync_still_uploads_nothing(device_root, monkeypatch,
                                                                    capsys):
    """Until a sync of a drive uploads (service.run_sync): refused, nothing uploaded and
    nothing written to the drive."""
    before = sorted((p.relative_to(device_root), p.stat().st_size, p.stat().st_mtime_ns)
                    for p in device_root.rglob("*"))
    drive_at(device_root, monkeypatch)
    account = account_for(monkeypatch, {"connectsupport": "Required"}, messages=[COURSE])
    assert cli.main(["sync", "--apply"]) == 1
    assert "A device connected as a USB drive is not synced." in capsys.readouterr().err
    assert account.uploads == [] and "upload" not in account.asked
    assert "queue" not in account.asked
    assert sorted((p.relative_to(device_root), p.stat().st_size, p.stat().st_mtime_ns)
                  for p in device_root.rglob("*")) == before


# --- only uploads ---------------------------------------------------------------------------


@pytest.mark.parametrize("flags", [[], ["--upload-only"]])
def test_the_dry_run_of_a_drive_only_uploads(device_root, monkeypatch, capsys, flags):
    """Garmin Connect's queue is not asked, though it has a course for the device, and the
    dry run says in one line that nothing is downloaded instead of planning the downloads."""
    drive_at(device_root, monkeypatch)
    account = account_for(monkeypatch, {"connectsupport": "Required"}, messages=[COURSE])
    assert cli.main(["sync", *flags]) == 0
    out = capsys.readouterr().out
    assert "queue" not in account.asked and "download" not in account.asked
    assert "To download from Connect" not in out
    assert out.count(UPLOADS_ONLY_TEXT) == 1 and "To upload: 3 files" in out


def test_download_only_on_a_drive_is_refused_before_any_request(device_root, monkeypatch,
                                                                capsys, log_file):
    drive_at(device_root, monkeypatch)
    account = account_for(monkeypatch, {"connectsupport": "Required"}, messages=[COURSE])
    assert cli.main(["sync", "--download-only"]) == 1
    err = capsys.readouterr().err
    assert DOWNLOAD_ONLY_TEXT in err and err.count("\n") == 1
    assert account.asked == []
    assert "sync: a USB drive not synced: download-only" in log_file.read_text()


def test_a_device_not_a_drive_still_has_the_queue_in_its_dry_run(device_root, monkeypatch,
                                                                  capsys):
    monkeypatch.setattr(service, "open_device_or_drive",
                        lambda **_kw: LocalStorage(device_root))
    account = account_for(monkeypatch, {"connectsupport": "Required"}, messages=[COURSE])
    assert cli.main(["sync"]) == 0
    out = capsys.readouterr().out
    assert "queue" in account.asked and "unit-info" not in account.asked
    assert "To download from Connect to the device: 1" in out and UPLOADS_ONLY_TEXT not in out


def test_the_words_of_only_uploading_are_said_in_polish_too(device_root, monkeypatch, capsys):
    drive_at(device_root, monkeypatch)
    account_for(monkeypatch, {"connectsupport": "Required"})
    try:
        assert cli.main(["--lang", "pl", "sync"]) == 0
        assert cli.main(["--lang", "pl", "sync", "--download-only"]) == 1
    finally:
        i18n.setup("en")
    said = capsys.readouterr()
    assert ("Na dysku USB synchronizacja tylko wysyła: nic nie jest pobierane z Garmin Connect "
            "na urządzenie.") in said.out
    assert ("To urządzenie jest podłączone jako dysk USB: jego synchronizacja tylko wysyła, "
            "więc z --download-only nie ma nic do zrobienia.") in said.err


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
