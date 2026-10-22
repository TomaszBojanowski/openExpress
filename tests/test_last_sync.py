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
    assert service.describe_counts({"uploaded": 0}) == "nothing to upload"
    assert service.describe_counts({"uploaded": 1, "downloaded": 0}) == "1 new file, 1 uploaded"
    assert service.describe_counts(
        {"uploaded": 7, "skipped_known": 3, "rejected": 1, "downloaded": 2,
         "errors": ["upload: HTTP 500", "download: HTTP 404"]}) == (
        "11 new files, 7 uploaded · 2 received")  # rejected and errors: warnings in the details


def _sync(new, uploaded, received=0):
    """What a sync records: `new` files on the device, `uploaded` of them sent, the rest
    already in Garmin Connect; `received` files from Garmin Connect written to the device."""
    return {"uploaded": uploaded, "skipped_known": new - uploaded, "downloaded": received,
            "lines": [{"kind": "new_files", "types": {"FIT_TYPE_4": new}}] if new else []}


# Every row the window, the command line and the notification can show (PLAN, 21.10.2026):
# the files on the device are new ones, "0 uploaded" is "none uploaded", and nothing new on
# the device is "nothing to upload" (it was "no new files", also next to files received).
ROWS = [
    (_sync(8, 6), "8 new files, 6 uploaded", "8 nowych plików, 6 wysłanych"),
    (_sync(8, 6, 2), "8 new files, 6 uploaded · 2 received",
     "8 nowych plików, 6 wysłanych · 2 odebrane"),
    (_sync(1, 1), "1 new file, 1 uploaded", "1 nowy plik, 1 wysłany"),
    (_sync(3, 3), "3 new files, 3 uploaded", "3 nowe pliki, 3 wysłane"),
    (_sync(22, 22), "22 new files, 22 uploaded", "22 nowe pliki, 22 wysłane"),
    (_sync(25, 1), "25 new files, 1 uploaded", "25 nowych plików, 1 wysłany"),
    (_sync(3, 0), "3 new files, none uploaded", "3 nowe pliki, nic nie wysłano"),
    (_sync(3, 0, 1), "3 new files, none uploaded · 1 received",
     "3 nowe pliki, nic nie wysłano · 1 odebrany"),
    (_sync(0, 0), "nothing to upload", "nic do wysłania"),
    (_sync(0, 0, 7), "nothing to upload · 7 received", "nic do wysłania · 7 odebranych"),
    (_sync(0, 0, 1), "nothing to upload · 1 received", "nic do wysłania · 1 odebrany"),
]


@pytest.mark.parametrize(("data", "english", "polish"), ROWS, ids=[r[1] for r in ROWS])
def test_every_row_in_english_and_polish(data, english, polish):
    """The words of the command line and the notification (plain spaces), and the row of the
    window (when, then the same words; a hard space before "·")."""
    from datetime import datetime

    from openexpress.gui.app import last_sync_words

    data = {**data, "time": datetime(2026, 10, 19, 12, 0).timestamp()}
    now = datetime(2026, 10, 22, 12, 0)
    for language, words, when in (("en", english, "3 days ago"), ("pl", polish, "3 dni temu")):
        i18n.setup(language)
        try:
            assert service.describe_counts(data) == words, language
            assert last_sync_words(data, now) == (
                f"{when} · {words}".replace(" · ", "\u00a0· ")), language
        finally:
            i18n.setup("en")


def _blocked(reason, received=0):
    """A sync that uploaded nothing because of `reason` (service.UploadGate), as recorded."""
    return {"uploaded": 0, "downloaded": received,
            "lines": [{"kind": "not_uploaded", "reason": reason}]}


# The row when nothing was uploaded: why, the same for every reason the upload is blocked
# (PLAN, 22.10.2026; it was "no new files", though the device was not even asked).
BLOCKED_ROWS = [
    (_blocked("no-consent"), "nothing uploaded: no consent in the account",
     "nic nie wysłano: brak zgody na koncie"),
    (_blocked("no-consent", 7), "nothing uploaded: no consent in the account · 7 received",
     "nic nie wysłano: brak zgody na koncie · 7 odebranych"),
    (_blocked("sync-off"), "nothing uploaded: device sync is off in the account",
     "nic nie wysłano: synchronizacja urządzeń wyłączona na koncie"),
    (_blocked("sync-off", 1), "nothing uploaded: device sync is off in the account · 1 received",
     "nic nie wysłano: synchronizacja urządzeń wyłączona na koncie · 1 odebrany"),
    (_blocked("not-checked"), "nothing uploaded: the consent could not be checked",
     "nic nie wysłano: nie udało się sprawdzić zgody"),
    (_blocked("not-checked", 2), "nothing uploaded: the consent could not be checked · 2 received",
     "nic nie wysłano: nie udało się sprawdzić zgody · 2 odebrane"),
    (_blocked("a reason of a newer version"), "nothing uploaded", "nic nie wysłano"),
]


@pytest.mark.parametrize(("data", "english", "polish"), BLOCKED_ROWS, ids=[r[1] for r in BLOCKED_ROWS])
def test_every_row_of_a_sync_that_uploaded_nothing(data, english, polish):
    test_every_row_in_english_and_polish(data, english, polish)


@pytest.mark.parametrize(("reason", "english", "polish"), [
    ("no-consent",
     ("Nothing was uploaded: the Garmin account has no consent to upload data from devices"),
     ("Nie wysłano danych: na koncie Garmin nie ma zgody na przesyłanie danych "
      "z\u00a0urządzeń")),
    ("sync-off", "Nothing was uploaded: device sync is turned off in the Garmin account",
     "Nie wysłano danych: synchronizacja urządzeń jest wyłączona na koncie Garmin"),
    ("not-checked",
     "Nothing was uploaded: the upload consent could not be checked. Try again later",
     ("Nie wysłano danych: nie udało się sprawdzić zgody na przesyłanie. Spróbuj ponownie "
      "później")),
])
def test_the_details_say_why_in_full_as_before(reason, english, polish):
    for language, words in (("en", english), ("pl", polish)):
        i18n.setup(language)
        try:
            assert service.describe_lines(_blocked(reason)["lines"]) == [words], language
        finally:
            i18n.setup("en")
    assert service.describe_lines(_blocked("a reason of a newer version")["lines"]) == []


def test_an_older_record_of_a_blocked_sync_reads_as_before():
    """Before 22.10.2026 the reason was kept as the words of that moment: they stay the
    details, and the row has no reason to tell."""
    data = {"uploaded": 0, "downloaded": 1, "lines": [
        "Nothing was uploaded: device sync is turned off in the Garmin account"]}
    assert service.describe_counts(data) == "nothing to upload · 1 received"
    assert service.describe_lines(data["lines"]) == data["lines"]


@pytest.mark.parametrize(("count", "words"), [
    (1, "5 nowych plików, 1 wysłany"), (2, "6 nowych plików, 2 wysłane"),
    (5, "9 nowych plików, 5 wysłanych"), (22, "26 nowych plików, 22 wysłane")])
def test_the_row_in_polish(count, words):
    i18n.setup("pl")
    try:
        row = service.describe_counts({"uploaded": count, "skipped_known": 3, "rejected": 1,
                                       "downloaded": 2, "errors": ["x"]})
    finally:
        i18n.setup("en")
    assert row == f"{words} · 2 odebrane"


def test_the_row_of_a_sync_in_polish():
    """The sync of 18.10: 8 files from the device, 6 uploaded, 2 already in Garmin Connect -
    when, told against now, then the numbers; the details name the files and the 2."""
    from datetime import datetime

    from openexpress.gui.app import last_sync_words

    data = {"time": datetime(2026, 10, 18, 21, 14).timestamp(), "uploaded": 6,
            "skipped_known": 2, "downloaded": 0, "rejected": 0, "errors": [], "lines": [
                {"kind": "new_files", "types": {"FIT_TYPE_4": 1, "FIT_TYPE_32": 5,
                                                "FIT_TYPE_44": 1, "FIT_TYPE_58": 1}},
                {"kind": "uploads", "uploaded": 6, "known": 2, "known_checked": 1,
                 "known_server": 1, "rejected": 0, "errors": 0}]}
    i18n.setup("pl")
    try:
        row = last_sync_words(data, datetime(2026, 10, 20, 10, 0))
        details = service.describe_lines(data["lines"])
    finally:
        i18n.setup("en")
    assert row == "2 dni temu\u00a0· 8 nowych plików, 6 wysłanych"  # two days back (by the date)
    assert details == [("Pliki z\u00a0urządzenia: aktywności, monitorowanie ×5, metryki, dziennik "
                        "urządzenia"), "2 pliki były już w\u00a0Garmin Connect"]


@pytest.mark.parametrize(("received", "words"), [(1, "1 odebrany"), (2, "2 odebrane"),
                                                 (5, "5 odebranych")])
def test_the_files_from_connect_in_polish(received, words):
    i18n.setup("pl")
    try:
        row = service.describe_counts({"uploaded": 1, "downloaded": received})
    finally:
        i18n.setup("en")
    assert row == f"1 nowy plik, 1 wysłany · {words}"


# --- the details -----------------------------------------------------------------------------


def test_the_details_leave_how_files_were_recognised_to_the_log():
    """The step keeps how the files already in Connect were recognised; the words say how
    many there were, and what went wrong only when anything did (the log of the sync tells
    the rest)."""
    step = service.uploads_step(_report(uploaded=7, checked=2, server=1, failed=1))
    assert step == {"kind": "uploads", "uploaded": 7, "known": 3, "known_checked": 2,
                    "known_server": 1, "rejected": 0, "errors": 1}
    assert service.describe_lines([step]) == [
        "3 files were already in Garmin Connect", "1 file could not be uploaded"]
    # Nothing already in Connect: nothing said of it.
    assert service.describe_lines([service.uploads_step(_report(uploaded=2, rejected=1))]) == [
        "1 file was rejected by Garmin"]


def test_the_details_in_polish():
    step = service.uploads_step(_report(uploaded=7, checked=2, server=1))
    i18n.setup("pl")
    try:
        (line,) = service.describe_lines([step])
    finally:
        i18n.setup("en")
    assert line == "3 pliki były już w\u00a0Garmin Connect"


def test_what_went_wrong_is_a_warning():
    step = service.uploads_step(_report(uploaded=1, checked=1, rejected=2, failed=1))
    assert service.describe_steps([step]) == [
        ("1 file was already in Garmin Connect", False),
        ("2 files were rejected by Garmin", True),
        ("1 file could not be uploaded", True)]


@pytest.mark.needs_display
def test_the_window_marks_the_warnings(xdg):
    """Under "Last sync": the details as one row of a few lines, the warnings with an icon
    and in the colour of warnings, the other lines as they are; the full date in the row's
    tooltip."""
    import types

    import gi

    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gtk

    from openexpress.gui.app import MainWindow

    service.save_last_sync(A, service.SyncSummary(
        uploaded=1, errors=["download: HTTP 404"],
        lines=[service.new_files_step([types.SimpleNamespace(datatype="FIT_TYPE_4")] * 3),
               service.uploads_step(_report(uploaded=1, checked=1, rejected=1))]))
    row = Adw.ExpanderRow(title="Last sync")
    win = types.SimpleNamespace(
        last_sync_row=row, _last_sync_children=[],
        session=types.SimpleNamespace(ownership_error=None,
                                      device=types.SimpleNamespace(unit_id=A)))
    MainWindow._show_last_sync(win)
    assert row.get_subtitle().endswith("\u00a0· 3 new files, 1 uploaded")
    assert row.get_tooltip_text()  # the full date
    (details,) = win._last_sync_children  # one row
    shown, item = [], details.get_first_child()
    while item is not None:  # a label, or the icon and the label of a warning
        label = item if isinstance(item, Gtk.Label) else item.get_last_child()
        shown.append((label.get_label(), label.has_css_class("warning"),
                      item is not label and item.get_first_child().has_css_class("warning")))
        # Secondary text: smaller; dimmed, but a warning in the colour of warnings only.
        assert label.has_css_class("caption")
        assert label.has_css_class("dim-label") is not label.has_css_class("warning")
        item = item.get_next_sibling()
    assert shown == [("Files from the device: activities ×3", False, False),
                     ("1 file was already in Garmin Connect", False, False),
                     ("1 file was rejected by Garmin", True, True),
                     ("download: HTTP 404", True, True)]


@pytest.mark.needs_display
@pytest.mark.parametrize("enabled", [False, True])
def test_the_device_page_switches_the_automatic_sync(enabled):
    """A switch, on or off as the background sync is; turning it asks for the change (a
    fake here: the background sync of this computer is never touched), and a change that
    fails puts it back."""
    import types

    from openexpress.gui.app import MainWindow

    asked = []
    win = types.SimpleNamespace(_background_enabled=lambda: enabled,
                                set_background=lambda on, result: asked.append((on, result)))
    row = MainWindow._auto_sync_row(win)
    assert row.get_title() == "Sync automatically when connected"
    assert row.get_active() is enabled and asked == []
    row.set_active(not enabled)
    ((wanted, result),) = asked
    assert wanted is (not enabled)
    result(False)  # it failed: the switch is put back, and nothing more is asked
    assert row.get_active() is enabled and len(asked) == 1


@pytest.mark.parametrize(("result", "lines", "errors"), [
    ({"written": "GARMIN/NewFiles/x.FIT"},
     ["TrueUp: daily totals aligned with your other Garmin devices"], []),
    ({"note": "Connect has not prepared TrueUp data yet"}, [], []),  # in the log only
    ({"note": "the device does not support TrueUp"}, [], []),
    ({"note": "empty TrueUp file"}, [], []),
    (RuntimeError("HTTP 500"), [], ["TrueUp: HTTP 500"]),  # a failure is among the errors
])
def test_trueup_says_only_what_it_wrote(monkeypatch, result, lines, errors):
    import types

    from openexpress import trueup

    def run(*_a, **_k):
        if isinstance(result, Exception):
            raise result
        res = trueup.TrueUpResult()
        for key, value in result.items():
            setattr(res, key, value)
        return res

    monkeypatch.setattr(trueup, "run", run)
    said_errors = []
    s = types.SimpleNamespace(device=None, storage=None, client=None)
    assert service._trueup(s, [], lambda m: None, said_errors) == lines
    assert said_errors == errors


# --- one source of words ---------------------------------------------------------------------


def test_the_command_line_says_what_the_window_shows(device_root, xdg, fake_client, capsys):
    assert main(["--path", str(device_root), "sync", "--apply", "--all"]) == 0
    printed = capsys.readouterr().out.splitlines()
    data = service.load_last_sync(1234567890)
    steps = [line for line in data["lines"] if isinstance(line, dict)]
    assert [step["kind"] for step in steps] == ["new_files", "uploads"]
    words = service.describe_lines(steps)
    assert words == ["Files from the device: activities ×2"]
    assert all(line in printed for line in words)  # printed exactly as the window shows it
    assert service.describe_counts(data) == "2 new files, 2 uploaded"
    assert "2 new files, 2 uploaded" in printed  # the numbers of the row, then the details


def test_the_notification_says_what_the_window_shows():
    summary = service.SyncSummary(uploaded=1, lines=[
        "First sync.", service.new_files_step([]), service.uploads_step(_report(uploaded=1))])
    # watch.py uses .text: the numbers of the row, then the details.
    assert summary.text.splitlines() == [
        service.describe_counts({"uploaded": 1, "lines": summary.lines}),
        *service.describe_lines(summary.lines)]
    assert summary.text.splitlines() == ["1 new file, 1 uploaded", "First sync."]


# --- the types of new files ------------------------------------------------------------------


def test_new_files_are_named_by_their_type():
    step = {"kind": "new_files", "types": {
        "FIT_TYPE_70": 1, "BACKUP_SUPPLEMENTARY": 1, "FIT_TYPE_32": 3, "WELLNESS_TYPE_1": 2,
        "FIT_TYPE_4": 2, "BACKUP_PRIMARY": 1, "FIT_TYPE_68": 1}}
    # The named types in a fixed order, the two backups counted together, then the others.
    assert service.describe_lines([step]) == [
        ("Files from the device: activities ×2, monitoring ×3, HRV status, device backup ×2, "
         "other data (FIT_TYPE_70), other data (WELLNESS_TYPE_1) ×2")]


@pytest.mark.parametrize(("datatype", "name"), sorted(service.TYPE_NAMES.items()))
def test_every_named_type_has_a_readable_name(datatype, name):
    assert service.type_name(datatype) == name and "_" not in name


def test_new_files_in_polish():
    step = {"kind": "new_files", "types": {"FIT_TYPE_4": 1, "FIT_TYPE_49": 1, "FIT_TYPE_61": 1,
                                           "FIT_TYPE_79": 1, "BACKUP_PRIMARY": 1,
                                           "FIT_TYPE_70": 1}}
    i18n.setup("pl")
    try:
        (line,) = service.describe_lines([step])
    finally:
        i18n.setup("en")
    assert line == ("Pliki z\u00a0urządzenia: aktywności, sen, EKG, zaburzenia snu, kopia zapasowa "
                    "urządzenia, inne dane (FIT_TYPE_70)")


def test_the_device_level_monitoring_is_the_device_log():
    """Type 58 is what the device logs about itself, not the wearer's monitoring (type 32):
    "device log", told apart from the settings change log and the primary device's data."""
    assert service.type_name("FIT_TYPE_58") == "device log"
    assert service.type_name("FIT_TYPE_32") == "monitoring"
    i18n.setup("pl")
    try:
        assert service.type_name("FIT_TYPE_58") == "dziennik urządzenia"
    finally:
        i18n.setup("en")


def test_names_for_the_training_status_primary_device_and_coach_files():
    """Types 72, 74 and 77 seen in real syncs, and 41 named after what it is; any other type
    stays "other data"."""
    step = {"kind": "new_files", "types": {"FIT_TYPE_77": 2, "FIT_TYPE_70": 1, "FIT_TYPE_74": 1,
                                           "FIT_TYPE_41": 1, "FIT_TYPE_72": 1}}
    assert service.describe_lines([step]) == [
        ("Files from the device: settings change log, training status data, primary training "
         "device data, Garmin Coach plan ×2, other data (FIT_TYPE_70)")]
    i18n.setup("pl")
    try:
        (line,) = service.describe_lines([step])
    finally:
        i18n.setup("en")
    assert line == ("Pliki z\u00a0urządzenia: dziennik zmian ustawień, dane o\u00a0wytrenowaniu, dane "
                    "urządzenia głównego, plan Garmin Coach ×2, inne dane (FIT_TYPE_70)")


# --- each device keeps its own last sync -----------------------------------------------------


A, B = 3000000001, 3000000002  # made up


def test_each_device_keeps_its_own_last_sync(xdg):
    service.save_last_sync(A, service.SyncSummary(uploaded=7))
    service.save_last_sync(B, service.SyncSummary(uploaded=2))
    assert service.load_last_sync(A)["uploaded"] == 7  # not replaced by the sync of B
    assert service.load_last_sync(B)["uploaded"] == 2
    assert service.load_last_sync()["unit_id"] == B  # the shared file: the newest sync


def test_a_last_sync_written_by_an_earlier_version_still_counts(xdg):
    import json

    service.last_sync_path().parent.mkdir(parents=True, exist_ok=True)
    service.last_sync_path().write_text(json.dumps({"time": 1, "unit_id": A, "uploaded": 4}))
    assert service.load_last_sync(A)["uploaded"] == 4
    assert service.load_last_sync(B) is None
    assert not service.device_last_sync_path(A).exists()  # read where it is, not moved


def test_the_devices_own_file_comes_first(xdg):
    import json

    service.save_last_sync(A, service.SyncSummary(uploaded=5))
    service.last_sync_path().write_text(json.dumps({"time": 1, "unit_id": A, "uploaded": 1}))
    assert service.load_last_sync(A)["uploaded"] == 5


def test_the_window_shows_the_last_sync_of_its_device_after_another_one(xdg):
    import types

    from openexpress.gui.app import MainWindow

    service.save_last_sync(A, service.SyncSummary(uploaded=3))
    service.save_last_sync(B, service.SyncSummary(uploaded=1))
    said = []
    row = types.SimpleNamespace(set_subtitle=said.append, set_enable_expansion=lambda on: None,
                                remove=lambda child: None, add_row=lambda child: None,
                                set_tooltip_text=lambda text: None)
    win = types.SimpleNamespace(
        last_sync_row=row, _last_sync_children=[],
        session=types.SimpleNamespace(ownership_error=None,
                                      device=types.SimpleNamespace(unit_id=A)))
    MainWindow._show_last_sync(win)
    assert len(said) == 1 and said[0].endswith("3 new files, 3 uploaded")


UNIT = 3000000001


@pytest.mark.parametrize("field, value", [
    ("time", "yesterday"), ("time", None), ("time", True), ("time", float("nan")),
    ("time", 1e300), ("uploaded", None), ("downloaded", "2"), ("rejected", 1.5),
    ("skipped_known", False), ("errors", "a text"), ("lines", 5),
])
def test_a_record_of_the_last_sync_of_an_unexpected_shape_reads_as_none(xdg, field, value):
    """Only a file edited by hand has it: "Not synced yet" rather than a window that cannot
    draw the row (a value of the wrong type stopped the drawing)."""
    import json

    from openexpress import paths
    from openexpress.gui.app import last_sync_words

    good = {"time": 1761000000.0, "unit_id": UNIT, "uploaded": 1, "downloaded": 0,
            "skipped_known": 0, "rejected": 0, "errors": [], "lines": []}
    path = service.device_last_sync_path(UNIT)
    paths.write_private(path, json.dumps(good))
    assert last_sync_words(service.load_last_sync(UNIT))  # read and drawn
    paths.write_private(path, json.dumps({**good, field: value}))
    data = service.load_last_sync(UNIT)
    assert data is None


@pytest.mark.parametrize("text", ["[]", "3", '"a text"', "null"])
def test_gui_settings_of_an_unexpected_shape_read_as_none(xdg, text):
    """gui.json that is not an object (edited by hand): the defaults, not a window that does
    not start."""
    from openexpress import paths
    from openexpress.gui import app

    paths.write_private(app.gui_settings_path(), text)
    settings = app.load_gui_settings()
    assert settings == {}
    assert app.window_geometry(settings) == (*app.DEFAULT_WINDOW, False)
