"""One sync for the window, the background service and the command line: with the same device
and account, the window and `openexpress sync --apply` give the same last_sync.json and
state.json, files written to the device, requests to Garmin Connect, outcome (done, done with
errors, or a first sync waiting for a choice) and log lines. All answers are made up."""

import json
import re
import shutil
import types
from dataclasses import dataclass, field

import pytest
from fakes import (
    ACTIVITY_URL,
    COURSE,
    MONITORING_URL,
    NOT_TAKEN,
    RunNow,
    SyncAccount,
)

from openexpress import applog, cli, last_sync, service, sync
from openexpress.device.storage import LocalStorage
from openexpress.device.xmldevice import parse
from openexpress.settings import Settings

UNIT = SyncAccount.UNIT


@dataclass
class Case:
    account: dict = field(default_factory=dict)
    synced_before: bool = True  # an activity of the device was uploaded by an earlier sync
    choice: str | None = None  # the first sync: "all" (the window's answer, sync --all)


CASES = {
    "a sync": Case({"messages": [COURSE]}),
    "a file written, not confirmed in Connect": Case({"messages": [COURSE], "ack_fails": {5}}),
    "a file for another tracker": Case({"statuses": {MONITORING_URL: sync.TRACKER_CONFLICT}}),
    "an item the device does not take": Case({"messages": [COURSE, NOT_TAKEN]}),
    "no consent": Case({"consent": False, "messages": [COURSE]}),
    "the first sync": Case({"messages": [COURSE]}, synced_before=False),
    "the first sync, everything": Case({"messages": [COURSE]}, synced_before=False,
                                       choice="all"),
}


def by_the_window(root, account, case):
    """The window's Sync button (MainWindow.start_sync), its task run at once."""
    from openexpress.gui import app

    storage = LocalStorage(root)
    device = parse(storage.read("GARMIN/GarminDevice.xml"))
    said = []
    win = types.SimpleNamespace(
        session=service.DeviceSession(storage, device, Settings(), account),
        run_task=RunNow(error="record"), progress=lambda *a: None, toast=said.append,
        _show_last_sync=lambda: None, _count_activities_again=lambda: None,
        ask_first_sync=lambda: said.append("first sync"), show_device=lambda: None)
    applog.setup("gui")
    try:
        app.MainWindow.start_sync(win, case.choice)
    finally:
        applog.close()
    if win.run_task.errors or said == ["Sync finished with errors"]:
        return "errors"
    return {"Sync finished": "done", "first sync": "choice"}[said[0]]


def by_the_command_line(root, account, case, monkeypatch):
    monkeypatch.setattr(cli, "make_client", lambda settings, need_auth: account)
    flags = ["--all"] if case.choice == "all" else []
    try:
        code = cli.main(["--path", str(root), "sync", "--apply", *flags])
    finally:
        applog.close()
    return {0: "done", 1: "errors", 3: "choice"}[code]


def run(name, case, how, device_root, tmp_path, monkeypatch, *more):
    """A sync of a copy of the test device, with folders of its own; what it left."""
    base = tmp_path / name
    root = base / "device"
    shutil.copytree(device_root, root)
    for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME"):
        monkeypatch.setenv(var, str(base / var.lower()))
    if case.synced_before:
        state = sync.SyncState(UNIT)
        state.mark_completed(sync.ACTIVITY_TYPE, "2026-10-01-10-00-00.fit")
        state.save()
    account = SyncAccount(verified_unit_id=None, **case.account)
    outcome = how(root, account, case, *more)
    record = service.load_last_sync(UNIT)
    state = sync.state_path(UNIT)
    return {
        "outcome": outcome,
        "record": None if record is None else {k: v for k, v in record.items() if k != "time"},
        "state": json.loads(state.read_text()) if state.exists() else None,
        "not acknowledged": sorted(sync.SyncState(UNIT).unacked()),
        "written": sorted(p.name.split("_")[0] for p in (root / "GARMIN/NewFiles").iterdir()),
        "uploads": account.uploads, "acknowledged": account.acks, "asked": account.asked,
        "log": sync_log(base / "xdg_state_home" / "openexpress" / applog.LOG_NAME),
    }


def sync_log(path):
    """The lines of the sync in a log file: those of service and sync, as LEVEL logger:
    message, the times of the moment left out."""
    if not path.exists():
        return []
    said = []
    for line in path.read_text().splitlines():
        rest = line.split("] ", 1)[-1]
        if rest.split(" ", 2)[1] in ("openexpress.service:", "openexpress.sync:"):
            said.append(re.sub(r"\d+\.\d+ s\b", "… s", rest))
    return said


def both(name, device_root, tmp_path, monkeypatch):
    case = CASES[name]
    return (run("window", case, by_the_window, device_root, tmp_path, monkeypatch),
            run("cli", case, by_the_command_line, device_root, tmp_path, monkeypatch,
                monkeypatch))


@pytest.mark.parametrize("name", CASES)
def test_the_window_and_the_command_line_sync_the_same(name, device_root, tmp_path,
                                                       monkeypatch):
    """The same records, files, requests (in the same order: Garmin Connect's queue after
    the uploads) and outcome."""
    window, command = both(name, device_root, tmp_path, monkeypatch)
    del window["log"], command["log"]
    assert command == window


@pytest.mark.parametrize("name", CASES)
def test_the_window_and_the_command_line_log_the_same_sync(name, device_root, tmp_path,
                                                           monkeypatch):
    """The lines of service and sync in the log: its start, its end and its errors too."""
    window, command = both(name, device_root, tmp_path, monkeypatch)
    assert window["log"][0].startswith("INFO openexpress.service: sync: start (")
    assert command["log"] == window["log"]


def test_what_the_cases_tell_apart(device_root, tmp_path, monkeypatch):
    """The cases, synced by the window, are what their names say."""
    seen = {name: run(name, case, by_the_window, device_root, tmp_path, monkeypatch)
            for name, case in CASES.items()}
    kinds = {name: [line["kind"] for line in kept["record"]["lines"]] if kept["record"]
             else None for name, kept in seen.items()}
    assert kinds == {
        "a sync": ["new_files", "uploads", "received_files"],
        "a file written, not confirmed in Connect": ["new_files", "uploads", "received_files"],
        "a file for another tracker": ["new_files", "uploads", "tracker_conflict"],
        "an item the device does not take": ["new_files", "uploads", "received_files",
                                             "not_accepted"],
        "no consent": ["not_uploaded", "received_files"],
        "the first sync": None,
        "the first sync, everything": ["new_files", "uploads", "received_files"],
    }
    assert {name: kept["outcome"] for name, kept in seen.items()} == {
        **{name: "done" for name in CASES},
        "a file written, not confirmed in Connect": "errors", "the first sync": "choice"}
    assert seen["a file written, not confirmed in Connect"]["record"]["errors"] == [
        "FIT_TYPE_6: A course: written, but Garmin Connect was not notified (will retry)"]
    assert seen["a file written, not confirmed in Connect"]["not acknowledged"] == ["5"]
    assert seen["the first sync"]["state"] is None and not seen["the first sync"]["written"]
    assert seen["the first sync"]["asked"] == ["account", "upload settings"]
    # Garmin Connect's queue is read after the uploads, as Garmin Express syncs.
    assert seen["a sync"]["asked"] == [
        "account", "upload settings", "consent", "device sync", "matches", "upload", "upload",
        "queue", "download", "acknowledge"]


# --- what the command line prints -------------------------------------------------------------


@pytest.mark.parametrize("name", [name for name, case in CASES.items()
                                  if case.synced_before or case.choice])
def test_the_command_line_says_each_step_in_the_words_of_the_window(
        name, device_root, tmp_path, monkeypatch, capsys):
    """After `sync --apply`: each step of the sync as the details under "Last sync" say it,
    each error, then the numbers of the row as a sentence."""
    kept = run("cli", CASES[name], by_the_command_line, device_root, tmp_path, monkeypatch,
               monkeypatch)
    out = capsys.readouterr().out.splitlines()
    told = last_sync.describe(kept["record"])
    for text, _warning in told.steps:
        assert text in out
    for error in told.errors:
        assert "  " + "ERROR " + error in out
    assert told.sentence in out
    assert out[-1] == ("Done." if kept["outcome"] == "done" else "Done with 1 error.")


def test_the_command_line_tells_a_file_not_confirmed_in_connect_as_an_error(
        device_root, tmp_path, monkeypatch, capsys):
    """Written to the device, but Garmin Connect was not told: an error, as in the window
    ("Sync finished with errors"), so exit code 1."""
    kept = run("cli", CASES["a file written, not confirmed in Connect"], by_the_command_line,
               device_root, tmp_path, monkeypatch, monkeypatch)
    assert kept["outcome"] == "errors"
    assert ("  ERROR FIT_TYPE_6: A course: written, but Garmin Connect was not notified "
            "(will retry)") in capsys.readouterr().out.splitlines()


@pytest.mark.parametrize("apply", [False, True])
def test_the_uploads_are_planned_once(apply, device_root, tmp_path, monkeypatch):
    """The dry run plans the uploads once, and so does `sync --apply` (it planned them a
    second time under the lock before)."""
    planned = []
    plan_uploads = sync.plan_uploads

    def counted(*args, **kw):
        planned.append(1)
        return plan_uploads(*args, **kw)

    monkeypatch.setattr(sync, "plan_uploads", counted)
    account = SyncAccount(messages=[COURSE], verified_unit_id=None)
    sync.SyncState(UNIT).save()
    monkeypatch.setattr(cli, "make_client", lambda settings, need_auth: account)
    assert cli.main(["--path", str(device_root), "sync", *(["--apply"] if apply else [])]) == 0
    assert len(planned) == 1 and account.asked.count("queue") == 1


def test_what_the_command_line_tells_is_not_repeated_on_its_terminal(
        device_root, tmp_path, monkeypatch, capsys, log_file):
    """The errors of a sync and why it stopped are in the log as the window writes them;
    the command line tells them itself, once, not again as warnings of the log on stderr."""
    monkeypatch.setattr(cli, "make_client", lambda settings, need_auth: account)
    account = SyncAccount(statuses={ACTIVITY_URL: 500}, verified_unit_id=None)
    sync.SyncState(UNIT).save()
    assert cli.main(["--path", str(device_root), "sync", "--apply"]) == 1
    said = capsys.readouterr()
    assert "  ERROR FIT_TYPE_4/2026-10-02-10-00-00.fit: HTTP 500" in said.out.splitlines()
    assert "sync error" not in said.err
    assert "WARNING openexpress.service: sync error: FIT_TYPE_4: HTTP 500" in log_file.read_text()

    sync.state_path(UNIT).unlink()  # the first sync waits for a choice: no warning either
    assert cli.main(["--path", str(device_root), "sync", "--apply"]) == 3
    err = capsys.readouterr().err
    assert "baseline --apply" in err and "WARNING" not in err
    # An ordinary stop: one line of INFO in the log, no "stopped after".
    log = log_file.read_text()
    assert "INFO openexpress.service: sync: waiting for the first-sync choice" in log
    assert "FirstSyncChoiceNeeded" not in log


@pytest.mark.parametrize("name, words", [
    ("a file for another tracker",
     "Skipped 1 file: another device is your active tracker in Garmin Connect"),
    ("an item the device does not take",
     "Skipped 1 item from Garmin Connect (not accepted by the device)"),
])
def test_the_command_line_has_the_steps_of_the_window(name, words, device_root, tmp_path,
                                                      monkeypatch, capsys):
    run("cli", CASES[name], by_the_command_line, device_root, tmp_path, monkeypatch,
        monkeypatch)
    assert words in capsys.readouterr().out.splitlines()


@pytest.mark.parametrize("lang, numbers, nothing, row, done", [
    ("en", "2 new files, 2 uploaded · 1 received.", "Nothing to upload.", "nothing to upload",
     "Done."),
    ("pl", "2 nowe pliki, 2 wysłane · 1 odebrany.", "Nic do wysłania.", "nic do wysłania",
     "Gotowe."),
])
def test_the_numbers_after_a_sync_are_a_sentence_of_their_own(
        lang, numbers, nothing, row, done, device_root, xdg, monkeypatch, capsys):
    """The end of `sync --apply`: the numbers of the sync as a sentence (a capital letter, a
    full stop), before "Done."; the row under "Last sync" keeps them as it showed them."""
    from openexpress import i18n

    account = SyncAccount(messages=[COURSE], verified_unit_id=None)
    monkeypatch.setattr(cli, "make_client", lambda settings, need_auth: account)
    state = sync.SyncState(UNIT)
    state.mark_completed(sync.ACTIVITY_TYPE, "2026-10-01-10-00-00.fit")
    state.save()
    command = ["--lang", lang, "--path", str(device_root), "sync", "--apply"]
    try:
        assert cli.main(command) == 0
        first = capsys.readouterr().out.splitlines()
        account.messages = []
        assert cli.main(command) == 0
        second = capsys.readouterr().out.splitlines()
        shown = last_sync.describe(service.load_last_sync(UNIT)).counts
    finally:
        applog.close()
        i18n.setup("en")
    assert first[-3:] == [numbers, "", done]
    assert second[-3:] == [nothing, "", done]
    assert shown == row


def test_what_was_uploaded_is_told_when_the_sync_stops_after_it(device_root, xdg,
                                                                monkeypatch, capsys):
    """`sync --apply` stopped by an error after its uploads (here Garmin Connect's queue
    cannot be read): what the uploads did is printed before the error, their steps, the
    files that failed and those Connect did not confirm; not the numbers nor "Done"."""
    import requests

    from openexpress.connect import UploadResult

    account = SyncAccount(statuses={MONITORING_URL: 500}, verified_unit_id=None)
    sent = account.upload_file

    def upload_file(url, data, **kw):
        if url == ACTIVITY_URL:  # taken, its processing not confirmed
            sent(url, data, **kw)
            return UploadResult(201, {}, "https://up.example/activity-service/1", 0)
        return sent(url, data, **kw)

    def offline(unit_id):
        raise requests.ConnectionError("offline")

    account.upload_file, account.device_messages = upload_file, offline
    account.poll_location = lambda url, delay: 202
    state = sync.SyncState(UNIT)
    state.mark_completed(sync.ACTIVITY_TYPE, "2026-10-01-10-00-00.fit")
    state.save()
    monkeypatch.setattr(cli, "make_client", lambda settings, need_auth: account)
    try:
        assert cli.main(["--path", str(device_root), "sync", "--apply"]) == 1
    finally:
        applog.close()
    said = capsys.readouterr()
    out = said.out.splitlines()
    assert out[-5:] == [
        "",
        "Files from the device: activities, monitoring",
        "1 file could not be uploaded",
        "  ERROR FIT_TYPE_32/M1.FIT: HTTP 500",
        "  Warning: Connect did not confirm processing of FIT_TYPE_4/2026-10-02-10-00-00.fit"]
    assert said.err.splitlines()[-1].startswith("Network error: ")
