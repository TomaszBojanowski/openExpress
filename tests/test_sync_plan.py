"""A sync in two steps (26.10.2026): service.plan_sync works out what a sync will do (the
account, the upload settings, the first sync, the consents, what to upload) and writes
nothing; service.apply_sync does it, reading Garmin Connect's queue after the uploads.
service.run_sync runs both under the lock of the sync. All answers here are made up."""

import pytest
from fakes import COURSE, SyncAccount

from openexpress import service, sync
from openexpress.auth import store
from openexpress.device.storage import LocalStorage
from openexpress.device.xmldevice import parse
from openexpress.settings import Settings

UNIT = SyncAccount.UNIT


def session(device_root, account):
    st = LocalStorage(device_root)
    return service.DeviceSession(st, parse(st.read("GARMIN/GarminDevice.xml")), Settings(),
                                 account)


def tree(root):
    return sorted((str(p.relative_to(root)), p.stat().st_size) for p in root.rglob("*"))


def test_a_plan_writes_nothing_and_its_sync_writes_it(device_root, xdg):
    """The first sync, new data only: the plan knows the files the baseline marks and that
    nothing is left to upload; nothing is written, uploaded or asked of the queue until the
    sync is done."""
    account = SyncAccount(messages=[COURSE], verified_unit_id=None)
    s = session(device_root, account)
    before = tree(device_root)
    plan = service.plan_sync(s, service.SyncOptions(first_run="baseline"))
    assert len(plan.baseline) == 3  # its two activities and its monitoring file
    assert plan.uploads.candidates == [] and plan.gate.allowed and plan.queue is None
    assert not sync.state_path(UNIT).exists() and service.load_last_sync(UNIT) is None
    assert tree(device_root) == before
    assert account.asked == ["account", "upload settings", "consent", "device sync"]

    summary = service.apply_sync(s, plan)
    assert summary.lines == [service.marked_known_step(3),
                             service.received_files_step(["FIT_TYPE_6"])]
    assert sync.state_path(UNIT).exists() and account.uploads == []
    assert account.asked[4:] == ["queue", "download", "acknowledge"]
    assert service.load_last_sync(UNIT) is None  # run_sync keeps the record


def test_the_first_sync_waits_for_a_choice_before_anything_else(device_root, xdg):
    account = SyncAccount(messages=[COURSE], verified_unit_id=None)
    with pytest.raises(service.FirstSyncChoiceNeeded):
        service.plan_sync(session(device_root, account), service.SyncOptions())
    assert account.asked == ["account", "upload settings"]


def test_a_sync_needs_a_login(device_root, xdg):
    account = SyncAccount(verified_unit_id=None)
    account.auth = None
    with pytest.raises(store.NotLoggedIn):
        service.plan_sync(session(device_root, account), service.SyncOptions(first_run="all"))
    assert account.asked == []


def test_a_preview_needs_no_login_nor_the_choice(device_root, xdg):
    """What the dry run of the command line shows when not logged in: the uploads only,
    of all the files of a first sync."""
    account = SyncAccount(verified_unit_id=None)
    account.auth = None
    plan = service.plan_sync(session(device_root, account), service.SyncOptions(preview=True))
    assert len(plan.uploads.candidates) == 3 and plan.gate is None and plan.queue is None
    assert account.asked == ["upload settings"]
    with pytest.raises(ValueError):
        service.apply_sync(session(device_root, account), plan)  # only to be shown


def test_a_preview_logged_in_reads_the_queue(device_root, xdg):
    """A dry run uploads nothing, so the queue it reads is the one a sync would read."""
    account = SyncAccount(messages=[COURSE], verified_unit_id=None)
    plan = service.plan_sync(session(device_root, account), service.SyncOptions(preview=True))
    assert [item.datatype for item in plan.queue.items] == ["FIT_TYPE_6"]
    assert account.asked == ["account", "upload settings", "consent", "device sync", "queue"]


def test_downloads_only_looks_at_no_upload(device_root, xdg):
    """sync --download-only: no upload settings, no consent, no first-sync choice, and the
    first sync stays to be chosen (no state.json)."""
    account = SyncAccount(messages=[COURSE], verified_unit_id=None)
    s = session(device_root, account)
    plan = service.plan_sync(s, service.SyncOptions(uploads=False))
    assert plan.uploads is None and plan.gate is None and plan.config is None
    summary = service.apply_sync(s, plan)
    assert summary.lines == [service.upload_skipped_step(),
                             service.received_files_step(["FIT_TYPE_6"])]
    assert not sync.state_path(UNIT).exists()
    assert account.asked == ["account", "queue", "download", "acknowledge"]


def test_uploads_only_reads_no_queue(device_root, xdg):
    account = SyncAccount(messages=[COURSE], verified_unit_id=None)
    s = session(device_root, account)
    summary = service.apply_sync(s, service.plan_sync(
        s, service.SyncOptions(first_run="all", downloads=False)))
    assert summary.uploaded == 3 and "queue" not in account.asked


def test_some_types_only(device_root, xdg):
    account = SyncAccount(verified_unit_id=None)
    s = session(device_root, account)
    plan = service.plan_sync(s, service.SyncOptions(first_run="all",
                                                    only_types=frozenset({"FIT_TYPE_4"})))
    assert {c.datatype for c in plan.uploads.candidates} == {"FIT_TYPE_4"}
    summary = service.apply_sync(s, plan)
    assert summary.lines[0] == service.only_types_step(["FIT_TYPE_4"])


def test_run_sync_plans_and_syncs_under_the_lock(device_root, xdg, monkeypatch):
    """The window calls run_sync as before; it plans once, under the lock of the sync."""
    held = []
    lock = service.sync_lock

    def watched():
        held.append("lock")
        return lock()

    plan_sync = service.plan_sync
    monkeypatch.setattr(service, "sync_lock", watched)
    monkeypatch.setattr(service, "plan_sync",
                        lambda *a, **kw: held.append("plan") or plan_sync(*a, **kw))
    account = SyncAccount(messages=[COURSE], verified_unit_id=None)
    summary = service.run_sync(session(device_root, account), "all")
    assert held == ["lock", "plan"] and summary.uploaded == 3 and summary.downloaded == 1
    assert service.load_last_sync(UNIT)["uploaded"] == 3
