"""openexpress sync: the first sync's choice, what it says of its numbers, and its exit codes."""
import contextlib
from pathlib import Path

import pytest
import requests
from fakes import (
    FakeClient,
    no_answer,
    step_words,
)

from openexpress import service
from openexpress.cli import main


def _state_file(device_root):
    from openexpress.sync import SyncState

    return SyncState(1234567890).file


# --- inter-process lock ------------------------------------------------------------------


@pytest.mark.parametrize("argv", [["sync", "--apply", "--all"], ["baseline", "--apply"]])
def test_apply_respects_sync_lock(device_root, xdg, fake_client, monkeypatch, capsys, argv):
    @contextlib.contextmanager
    def busy():
        raise service.SyncBusy("A sync is already running in another process.")
        yield  # pragma: no cover

    monkeypatch.setattr(service, "sync_lock", busy)
    assert main(["--path", str(device_root), *argv]) == 1
    assert "already running in another process" in capsys.readouterr().err
    assert fake_client["client"].uploads == []
    assert not _state_file(device_root).exists()


# --- first-sync guard --------------------------------------------------------------------


def test_first_sync_guard_without_candidates(device_root, xdg, fake_client, capsys):
    # Only a type with nothing to upload: the guard must still stop the first sync.
    rc = main(["--path", str(device_root), "sync", "--apply", "--type", "FIT_TYPE_99"])
    assert rc == 3
    assert "baseline --apply" in capsys.readouterr().err
    assert not _state_file(device_root).exists()


def test_the_first_sync_choice_has_an_exit_code_of_its_own(device_root, xdg, fake_client,
                                                           capsys):
    """3, not 2: 2 is a wrong command line (argparse), which a script must tell apart from
    "choose baseline or sync --all first"."""
    assert main(["--path", str(device_root), "sync", "--apply"]) == 3
    assert "baseline --apply" in capsys.readouterr().err
    with pytest.raises(SystemExit) as wrong:
        main(["--path", str(device_root), "sync", "--no-such-option"])
    assert wrong.value.code == 2
    assert not _state_file(device_root).exists()


def test_download_only_first_run_does_not_create_state(device_root, xdg, fake_client):
    assert main(["--path", str(device_root), "sync", "--apply", "--download-only"]) == 0
    assert not _state_file(device_root).exists()
    # The guard still applies afterwards.
    assert main(["--path", str(device_root), "sync", "--apply"]) == 3


COURSES = [{"messageid": n, "messagetype": "file", "devicexmldatatype": "FIT_TYPE_6",
            "metadata": {"messageurl": f"course/{n}", "messagename": f"Route {n}"}}
           for n in (1, 2)]


def test_download_only_records_that_the_upload_was_skipped(device_root, xdg, fake_client,
                                                           capsys):
    """sync --apply --download-only does not look at the uploads: its record says so
    ("upload skipped · 2 received"), not "nothing to upload". It is
    the shape of its pattern in tests/data/last_sync, which no real sync has made yet."""
    import json

    client = fake_client["client"]
    client.messages = COURSES
    client.download_message = lambda url, absolute: b"COURSE"
    assert main(["--path", str(device_root), "sync", "--apply", "--download-only"]) == 0
    assert "Upload skipped · 2 received." in capsys.readouterr().out.splitlines()
    record = service.load_last_sync(1234567890)
    pattern = json.loads((Path(__file__).parent / "data/last_sync"
                          / "format-0.6.16-upload-skipped.json").read_text(encoding="utf-8"))
    # The two courses also get a line of their own, after the step of the pattern, as in
    # format-0.6.21-received-files.json.
    assert sorted(record) == sorted(pattern) and record["lines"] == [
        *pattern["lines"], service.received_files_step(["FIT_TYPE_6"] * 2)]
    assert (record["uploaded"], record["downloaded"], record["errors"]) == (0, 2, [])


def test_a_sync_of_some_types_says_its_numbers_are_of_them_only(device_root, xdg, fake_client,
                                                                capsys):
    """sync --apply --type FIT_TYPE_4 counts only the activities: the row says "activities
    only: …". The record has the shape of its pattern in tests/data/last_sync."""
    import json

    assert main(["--path", str(device_root), "sync", "--apply", "--all",
                 "--type", "FIT_TYPE_4"]) == 0
    assert "Activities only: 2 new files, 2 uploaded." in capsys.readouterr().out.splitlines()
    record = service.load_last_sync(1234567890)
    pattern = json.loads((Path(__file__).parent / "data/last_sync"
                          / "format-0.6.16-only-types.json").read_text(encoding="utf-8"))
    assert sorted(record) == sorted(pattern)
    assert [line["kind"] for line in record["lines"]] == [
        line["kind"] for line in pattern["lines"]]
    assert record["lines"][0] == {"kind": "only_types", "types": ["FIT_TYPE_4"]}


TRUEUP = {"messageid": 9, "messagetype": "true_up", "devicexmldatatype": "FIT_TYPE_39",
          "metadata": {"messageurl": "trueup/9"}}


def test_a_sync_that_only_wrote_trueup_counts_it_received(device_root, xdg, fake_client,
                                                          capsys):
    """Nothing new on the device, TrueUp from the queue written to it:
    "nothing to upload · 1 received", not "nothing to upload" alone. The record has the
    shape of its pattern in tests/data/last_sync."""
    import json

    xml = device_root / "GARMIN/GarminDevice.xml"  # the device takes TrueUp (as a fēnix 8)
    xml.write_text(xml.read_text().replace(
        "</MassStorageMode>",
        "<DataType><Name>FIT_TYPE_39</Name><File><Location><Path>Garmin/NewFiles</Path>"
        "<FileExtension>FIT</FileExtension></Location><TransferDirection>InputToUnit"
        "</TransferDirection></File></DataType></MassStorageMode>"))
    assert main(["--path", str(device_root), "baseline", "--apply"]) == 0
    client = fake_client["client"]
    client.messages = [TRUEUP]
    client.download_message = lambda url, absolute: b"TRUEUP"
    capsys.readouterr()
    assert main(["--path", str(device_root), "sync", "--apply"]) == 0
    assert "Nothing to upload · 1 received." in capsys.readouterr().out.splitlines()
    record = service.load_last_sync(1234567890)
    pattern = json.loads((Path(__file__).parent / "data/last_sync"
                          / "format-0.6.19-trueup-received.json").read_text(encoding="utf-8"))
    assert sorted(record) == sorted(pattern)
    # The line is kept as data (as in format-0.6.20-details-as-data.json), in the words of
    # the pattern (format-0.6.19-trueup-received.json) when shown.
    assert record["lines"] == [service.trueup_step()]
    assert step_words(record["lines"]) == pattern["lines"]
    assert (record["uploaded"], record["downloaded"], record["errors"]) == (0, 1, [])


def test_the_command_line_names_the_files_from_garmin_connect(device_root, xdg, fake_client,
                                                              capsys):
    """sync --apply: a course from the queue written to the device gets the same line as in
    the window, printed and kept as data in the record."""
    assert main(["--path", str(device_root), "baseline", "--apply"]) == 0
    client = fake_client["client"]
    client.messages = [{"messageid": 5, "devicexmldatatype": "FIT_TYPE_6",
                        "metadata": {"messageurl": "course", "messagename": "A course"}}]
    client.download_message = lambda url, absolute: b"COURSE"
    capsys.readouterr()
    assert main(["--path", str(device_root), "sync", "--apply"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert "1 file from Garmin Connect: courses" in out
    assert "Nothing to upload · 1 received." in out
    record = service.load_last_sync(1234567890)
    assert record["lines"] == [service.received_files_step(["FIT_TYPE_6"])]


def test_only_some_types_is_not_said_when_the_account_let_nothing_be_uploaded():
    """Nothing uploaded because of the account: the row says why, not which types."""
    record = {"lines": [service.only_types_step(["FIT_TYPE_4"]),
                        {"kind": "not_uploaded", "reason": service.NO_CONSENT}]}
    assert service.describe_counts(record) == "nothing uploaded: no consent in the account"


def test_sync_all_uploads_and_records_last_sync(device_root, xdg, fake_client, capsys):
    assert main(["--path", str(device_root), "sync", "--apply", "--all"]) == 0
    assert _state_file(device_root).exists()
    assert len(fake_client["client"].uploads) == 2
    data = service.load_last_sync(1234567890)
    assert data["uploaded"] == 2 and data["errors"] == []
    assert service.describe_counts(data) == "2 new files, 2 uploaded"  # the window's own words
    assert step_words(data["lines"]) == ["Files from the device: activities ×2"]


# --- errors and exit codes ---------------------------------------------------------------


def test_sync_upload_errors_give_exit_code_1(device_root, xdg, fake_client, capsys):
    fake_client["client"] = FakeClient(status=500)
    assert main(["--path", str(device_root), "sync", "--apply", "--all"]) == 1
    data = service.load_last_sync(1234567890)
    assert data["errors"]


def test_network_error_is_a_clean_user_error(device_root, xdg, fake_client, capsys):
    """The upload settings not fetched: the dry run says why in one line (the uploads
    skipped, the rest of the plan shown), no traceback."""
    fake_client["client"] = FakeClient(fail_config=no_answer(requests.ConnectionError("boom")))
    assert main(["--path", str(device_root), "sync"]) == 0
    said = capsys.readouterr()
    assert "could not be reached (no connection)." in said.out
    assert "Traceback" not in said.out + said.err
