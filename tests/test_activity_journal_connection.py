"""When the activity journal is written: at each connection, only after the user's yes, in the
window, after a background sync and after `sync --apply` (never for `--path`); a device on
another Garmin Connect account than the one logged in gets none (logged out, or the account's
list not read: the owner is not known, the journal is kept). Before the answer and after a no
nothing is read for it and no file is made. One line for each connection, whoever reads it.
A folder stands for the device; made-up files only."""

import json
import logging
import types

import pytest
from conftest import DEVICE_XML
from fakes import fit_activity, fit_time

pytestmark = pytest.mark.journal_answer

UNIT = 1234567890  # the unit ID of DEVICE_XML


@pytest.fixture
def device(tmp_path):
    """A folder as a watch with two activities: its device and storage, its reads counted."""
    from openexpress.device.storage import LocalStorage
    from openexpress.device.xmldevice import parse

    root = tmp_path / "device"
    (root / "GARMIN" / "Activity").mkdir(parents=True)
    (root / "GARMIN" / "GarminDevice.xml").write_text(DEVICE_XML)
    for day in (29, 30):
        (root / "GARMIN" / "Activity" / f"2026-10-{day}-07-00-00.fit").write_bytes(
            fit_activity(fit_time(2026, 10, day, 7, 0)))
    storage = LocalStorage(root)
    found = types.SimpleNamespace(root=root, storage=storage, reads=[],
                                  device=parse(storage.read("GARMIN/GarminDevice.xml")))
    plain = storage.read_at

    def counted(path, told=None):
        found.reads.append(path)
        return plain(path, told)
    storage.read_at = counted
    return found


def answer(value) -> None:
    """The user's answer as settings.json keeps it (None: not answered)."""
    from openexpress.settings import Settings

    s = Settings.load()
    s.activity_journal = value
    s.save()


def journal():
    from openexpress import activity_journal

    return activity_journal.path(UNIT)


@pytest.mark.parametrize("value", [None, False])
def test_before_the_yes_nothing_is_read_or_written(device, value):
    from openexpress import activity_journal

    answer(value)
    assert activity_journal.at_connection(device.device, device.storage) is None
    assert device.reads == [] and not journal().exists()


def test_after_the_yes_the_device_is_read(device):
    from openexpress import activity_journal

    answer(True)
    did = activity_journal.at_connection(device.device, device.storage)
    assert did.added == 2 and len(device.reads) == 2 and journal().is_file()


def test_one_line_for_each_connection(device):
    """The window and a background sync of the same connection: one connection line."""
    from openexpress import activity_journal

    answer(True)
    activity_journal.at_connection(device.device, device.storage)
    activity_journal.at_connection(device.device, device.storage)
    assert len(activity_journal.read(journal()).connections) == 1


def test_a_device_on_another_account_gets_none(device):
    from openexpress import activity_journal
    from openexpress.connect import NotAccountDevice, OwnershipUnknown

    answer(True)
    elsewhere = NotAccountDevice("made up: on another account")
    assert activity_journal.at_connection(device.device, device.storage, elsewhere) is None
    assert device.reads == [] and not journal().exists()
    # The account's list not read: the owner is not known, the journal is kept.
    unknown = OwnershipUnknown("made up: no answer")
    assert activity_journal.at_connection(device.device, device.storage, unknown).added == 2


def test_a_device_that_keeps_no_activities_gets_none(tmp_path):
    from openexpress import activity_journal
    from openexpress.device.storage import LocalStorage
    from openexpress.device.xmldevice import parse

    answer(True)
    root = tmp_path / "bike"
    (root / "GARMIN").mkdir(parents=True)
    (root / "GARMIN" / "GarminDevice.xml").write_text(
        DEVICE_XML.replace("<Name>FIT_TYPE_4</Name>", "<Name>FIT_TYPE_999</Name>"))
    storage = LocalStorage(root)
    device = parse(storage.read("GARMIN/GarminDevice.xml"))
    assert activity_journal.at_connection(device, storage) is None
    assert not journal().exists()


def test_a_failure_is_a_line_of_the_log_never_an_error(device, caplog, monkeypatch):
    from openexpress import activity_journal
    from openexpress.device.storage import DeviceError

    answer(True)
    caplog.set_level(logging.INFO, logger="openexpress")

    def broken(*args, **kwargs):
        raise DeviceError("made up: no answer")
    monkeypatch.setattr(device.storage, "walk_files", broken)
    assert activity_journal.at_connection(device.device, device.storage) is None
    assert "activity journal: not written (DeviceError)" in caplog.text


# --- sync --apply ------------------------------------------------------------------------------

def test_sync_apply_brings_the_journal_up_to_date(device_root, xdg, fake_client, monkeypatch):
    from openexpress import cli

    called = []
    monkeypatch.setattr(cli, "_journal_after_sync", lambda args, session: called.append(
        session.device.unit_id))
    assert cli.main(["--path", str(device_root), "baseline", "--apply"]) == 0
    assert cli.main(["--path", str(device_root), "sync", "--apply"]) == 0
    assert called == [UNIT]


@pytest.mark.parametrize("value,written", [(None, False), (False, False), (True, True)])
def test_after_sync_apply_only_after_the_yes(device, value, written):
    from openexpress import cli

    answer(value)
    session = types.SimpleNamespace(device=device.device, storage=device.storage,
                                    ownership_error=None)
    cli._journal_after_sync(types.SimpleNamespace(path=None), session)
    assert journal().exists() is written
    assert bool(device.reads) is written


def test_a_folder_given_by_path_is_no_connection(device):
    from openexpress import cli

    answer(True)
    session = types.SimpleNamespace(device=device.device, storage=device.storage,
                                    ownership_error=None)
    cli._journal_after_sync(types.SimpleNamespace(path=str(device.root)), session)
    assert not journal().exists() and device.reads == []


# --- the background sync -----------------------------------------------------------------------

@pytest.mark.parametrize("value,written", [(None, False), (False, False), (True, True)])
def test_after_a_background_sync_only_after_the_yes(device, monkeypatch, value, written):
    from openexpress import service, watch

    answer(value)
    session = types.SimpleNamespace(device=device.device, storage=device.storage, client=None,
                                    mass_storage=False, logged_in=True, first_sync=False,
                                    ownership_error=None)
    synced = []
    monkeypatch.setattr(watch.service, "open_session", lambda: session)
    monkeypatch.setattr(watch.service, "require_account_device",
                        lambda client, unit, fresh=False, **_kw: None)
    monkeypatch.setattr(watch.service, "run_sync",
                        lambda s, progress=None, **kw: synced.append(s) or service.SyncSummary())
    shown = []
    monkeypatch.setattr(watch.GLib, "idle_add", lambda fn, *args: shown.append(args))
    watch.Watcher()._sync()
    assert synced == [session] and shown[-1][0] == "Test watch synced"
    assert journal().exists() is written
    assert bool(device.reads) is written


def test_settings_say_it_in_one_place(device):
    """What settings.json says decides; nothing else is asked."""
    from openexpress import activity_journal, paths

    answer(True)
    assert activity_journal.kept() is True
    answer(False)
    assert activity_journal.kept() is False
    data = json.loads(paths.settings_file().read_text())
    del data["activity_journal"]
    paths.settings_file().write_text(json.dumps(data))
    assert activity_journal.kept() is False and activity_journal.answered() is None
