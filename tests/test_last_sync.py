"""The last sync in words: the row under "Last sync" and the details count the same way, in
the window, on the command line and in the notification of the background sync."""

import pytest
from test_fixes_ui import fake_client  # noqa: F401 - the fixture

from openexpress import i18n, service, sync
from openexpress.cli import main


def _report(uploaded=0, checked=0, server=0, rejected=0, failed=0):
    return sync.UploadReport(
        uploaded=[f"a{n}" for n in range(uploaded)], already_in_connect=[f"b{n}" for n in range(checked)],
        duplicates=[f"c{n}" for n in range(server)], rejected=[f"d{n}" for n in range(rejected)],
        failed=[(f"e{n}", "HTTP 500") for n in range(failed)])


# --- the row -------------------------------------------------------------------------------------


def test_the_row_leaves_out_what_is_zero():
    assert service.describe_counts({"uploaded": 0}) == "0 new files uploaded"
    assert service.describe_counts({"uploaded": 1, "downloaded": 0}) == "1 new file uploaded"
    assert service.describe_counts(
        {"uploaded": 7, "skipped_known": 3, "rejected": 1, "downloaded": 2,
         "errors": ["upload: HTTP 500", "download: HTTP 404"]}) == (
        "7 new files uploaded · 3 already in Connect · 1 rejected by Garmin · 2 received · "
        "2 errors")  # the errors of uploads and downloads together


@pytest.mark.parametrize(("count", "words"), [
    (1, "wysłano 1 nowy plik"), (2, "wysłano 2 nowe pliki"), (5, "wysłano 5 nowych plików"),
    (22, "wysłano 22 nowe pliki")])
def test_the_row_in_polish(count, words):
    i18n.setup("pl")
    try:
        row = service.describe_counts({"uploaded": count, "skipped_known": 3, "rejected": 1,
                                       "downloaded": 2, "errors": ["x"]})
    finally:
        i18n.setup("en")
    assert row == f"{words} · 3 już w Connect · 1 odrzucone przez Garmina · 2 odebrane · 1 błąd"


# --- the details -----------------------------------------------------------------------------


def test_the_details_say_how_files_already_in_connect_were_recognised():
    step = service.uploads_step(_report(uploaded=7, checked=2, server=1, failed=1))
    assert step == {"kind": "uploads", "uploaded": 7, "known": 3, "known_checked": 2,
                    "known_server": 1, "rejected": 0, "errors": 1}
    assert service.describe_lines([step]) == [
        ("New files uploaded: 7 · already in Connect: 3 (2 recognised before sending, 1 by "
         "Garmin) · rejected by Garmin: 0 · upload errors: 1")]
    # Nothing already in Connect: no breakdown of nothing.
    assert service.describe_lines([service.uploads_step(_report(uploaded=2, rejected=1))]) == [
        "New files uploaded: 2 · already in Connect: 0 · rejected by Garmin: 1 · upload errors: 0"]


def test_the_details_in_polish():
    step = service.uploads_step(_report(uploaded=7, checked=2, server=1))
    i18n.setup("pl")
    try:
        (line,) = service.describe_lines([step])
    finally:
        i18n.setup("en")
    assert line == ("Wysłane nowe pliki: 7 · już w Connect: 3 (2 rozpoznane przed wysłaniem, "
                    "1 przez Garmina) · odrzucone przez Garmina: 0 · błędy wysyłania: 0")


# --- one source of words ---------------------------------------------------------------------


def test_the_command_line_says_what_the_window_shows(device_root, xdg, fake_client, capsys):
    assert main(["--path", str(device_root), "sync", "--apply", "--all"]) == 0
    printed = capsys.readouterr().out.splitlines()
    data = service.load_last_sync(1234567890)
    steps = [line for line in data["lines"] if isinstance(line, dict)]
    assert [step["kind"] for step in steps] == ["new_files", "uploads"]
    words = service.describe_lines(steps)
    assert words == ["New files: FIT_TYPE_4×2",
                     ("New files uploaded: 2 · already in Connect: 0 · rejected by Garmin: 0 · "
                      "upload errors: 0")]
    assert all(line in printed for line in words)  # printed exactly as the window shows it
    assert service.describe_counts(data).startswith("2 new files uploaded")


def test_the_notification_says_what_the_window_shows():
    summary = service.SyncSummary(uploaded=1, lines=[
        "First sync.", service.new_files_step([]), service.uploads_step(_report(uploaded=1))])
    assert summary.text.splitlines() == service.describe_lines(summary.lines)  # watch.py uses .text
