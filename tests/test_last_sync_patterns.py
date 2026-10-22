"""The row "Last sync", from the file to the screen, on records of real syncs and on the shapes
older versions wrote (tests/data/last_sync/, the unit IDs made up). For each one the exact
words, in English and in Polish: the row (when, then the numbers) and the details,
none twice, none against the row. The window shows what last_sync.describe gives, also when
it shows a sync again (the file changes: the background service, or a sync of its own).

The rule (AGENTS.md): every change of this row comes with a new record of a real sync here
and its exact words below."""

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from openexpress import i18n, service

PATTERNS = Path(__file__).resolve().parent / "data" / "last_sync"

NB = " "
NOTHING_NEW = {"No new data to upload", "Brak nowych danych do wysłania"}

# name: {language: (the numbers, [(a line of the details, whether it is a warning)])}
EXPECTED = {
    # Today's sync of an Edge 1050 (Flatpak 0.6.13): the record has two steps, the window
    # showed them twice, under "No new data to upload" of the sync before.
    "real-flatpak-edge-1050-0.6.13.json": {
        "en": ("16 new files, 12 uploaded", [
            (("Files from the device: monitoring ×2, segment list, Garmin Coach plan ×9, "
             "other data (FIT_TYPE_57) ×4"), False),
            ("4 files were already in Garmin Connect", False)]),
        "pl": ("16 nowych plików, 12 wysłanych", [
            ((f"Pliki z{NB}urządzenia: monitorowanie ×2, lista segmentów, plan Garmin Coach ×9, "
             "inne dane (FIT_TYPE_57) ×4"), False),
            (f"4 pliki były już w{NB}Garmin Connect", False)])},
    "real-flatpak-fenix-8-0.6.3.json": {
        "en": ("8 new files, 6 uploaded", [
            (("Files from the device: activities, monitoring, segment list, settings change "
             "log, device log, training status data, primary training device data, Garmin "
             "Coach plan"), False),
            ("2 files were already in Garmin Connect", False)]),
        "pl": ("8 nowych plików, 6 wysłanych", [
            ((f"Pliki z{NB}urządzenia: aktywności, monitorowanie, lista segmentów, dziennik "
             f"zmian ustawień, dziennik urządzenia, dane o{NB}wytrenowaniu, dane urządzenia "
             "głównego, plan Garmin Coach"), False),
            (f"2 pliki były już w{NB}Garmin Connect", False)])},
    # Written by the version from source in Polish, in words (with full stops): shown as it
    # was written, in any language, but "Brak nowych danych do wysłania" says what the row
    # says already ("nothing to upload").
    "real-source-edge-1050-0.6.3.json": {
        "en": ("nothing to upload · 7 received", [
            ("Oznaczono 221 istniejących plików jako już wysłane.", False),
            ("Zapisano na urządzeniu 7 plików z Connect.", False),
            ("TrueUp: dzienne podsumowania uzgodnione z innymi urządzeniami Garmin.", False)]),
        "pl": ("nic do wysłania · 7 odebranych", [
            ("Oznaczono 221 istniejących plików jako już wysłane.", False),
            ("Zapisano na urządzeniu 7 plików z Connect.", False),
            ("TrueUp: dzienne podsumowania uzgodnione z innymi urządzeniami Garmin.", False)])},
    "real-source-fenix-8-0.6.6.json": {
        "en": ("nothing to upload", [
            ("TrueUp: dzienne podsumowania uzgodnione z innymi urządzeniami Garmin", False)]),
        "pl": ("nic do wysłania", [
            ("TrueUp: dzienne podsumowania uzgodnione z innymi urządzeniami Garmin", False)])},
    "format-0.4.8-en.json": {
        "en": ("5 new files, 3 uploaded · 1 received", [
            ("New files: FIT_TYPE_32×2, FIT_TYPE_4×3", False),
            ("Uploaded 3, already in Connect 2, rejected 0, errors 0.", False),
            ("Wrote 1 file from Connect to the device.", False)]),
        "pl": ("5 nowych plików, 3 wysłane · 1 odebrany", [
            ("New files: FIT_TYPE_32×2, FIT_TYPE_4×3", False),
            ("Uploaded 3, already in Connect 2, rejected 0, errors 0.", False),
            ("Wrote 1 file from Connect to the device.", False)])},
    "format-0.4.8-nothing-new-en.json": {
        "en": ("nothing to upload · 2 received", [
            ("Wrote 2 files from Connect to the device.", False)]),
        "pl": ("nic do wysłania · 2 odebrane", [
            ("Wrote 2 files from Connect to the device.", False)])},
    "format-0.6.10-blocked-en.json": {
        "en": ("nothing to upload · 1 received", [
            ("Nothing was uploaded: device sync is turned off in the Garmin account", False)]),
        "pl": ("nic do wysłania · 1 odebrany", [
            ("Nothing was uploaded: device sync is turned off in the Garmin account", False)])},
    "format-0.6.11-not-uploaded.json": {
        "en": ("nothing uploaded: no consent in the account", [
            (("Nothing was uploaded: the Garmin account has no consent to upload data from "
             "devices"), False)]),
        "pl": ("nic nie wysłano: brak zgody na koncie", [
            (("Nie wysłano danych: na koncie Garmin nie ma zgody na przesyłanie danych "
             f"z{NB}urządzeń"), False)])},
    "format-0.6.13-warnings.json": {
        "en": ("3 new files, 1 uploaded", [
            ("Files from the device: activities ×3", False),
            ("1 file was rejected by Garmin", True),
            ("1 file could not be uploaded", True),
            ("FIT_TYPE_4/2026-10-01-10-00-00.fit: HTTP 500", True)]),
        "pl": ("3 nowe pliki, 1 wysłany", [
            (f"Pliki z{NB}urządzenia: aktywności ×3", False),
            ("1 plik odrzucony przez Garmina", True),
            ("1 pliku nie udało się wysłać", True),
            ("FIT_TYPE_4/2026-10-01-10-00-00.fit: HTTP 500", True)])},
}
CASES = [(name, language) for name in EXPECTED for language in ("en", "pl")]


def _record(name):
    return json.loads((PATTERNS / name).read_text(encoding="utf-8"))


@pytest.fixture
def language(request):
    i18n.setup(request.param)
    yield request.param
    i18n.setup("en")


def test_every_record_of_the_folder_has_its_words():
    """A record added to tests/data/last_sync comes with its words here."""
    assert sorted(p.name for p in PATTERNS.glob("*.json")) == sorted(EXPECTED)


@pytest.mark.parametrize(("name", "language"), CASES, indirect=["language"])
def test_every_record_in_words(name, language):
    from openexpress import last_sync

    record = _record(name)
    counts, details = EXPECTED[name][language]
    told = last_sync.describe(record)
    assert told.counts == counts
    assert told.details == details
    when = datetime.fromtimestamp(record["time"])
    ago = {"en": "3 days ago", "pl": "3 dni temu"}[language]
    assert told.row(when + timedelta(days=3)) == f"{ago}{NB}· {counts}".replace(" · ", f"{NB}· ")
    texts = [text for text, _warning in details]
    assert len(texts) == len(set(texts))  # none twice
    assert not {t.rstrip(".") for t in texts} & NOTHING_NEW  # the row says it
    # The command line and the notification: the same numbers and the same lines.
    assert told.text.splitlines() == [counts, *(t for t, warning in told.steps)]


def _window(unit_id):
    import types

    from gi.repository import Adw

    from openexpress.gui.app import MainWindow

    win = types.SimpleNamespace(
        session=types.SimpleNamespace(device=types.SimpleNamespace(unit_id=unit_id),
                                      ownership_error=None),
        last_sync_row=Adw.ExpanderRow(title="Last sync"), _last_sync_children=[])
    win._show_last_sync = types.MethodType(MainWindow._show_last_sync, win)
    return win


def _shown(row):
    """(the subtitle of the row, [(a line of its details, whether it is a warning)])."""
    from gi.repository import Gtk

    lines = []

    def walk(widget):
        child = widget.get_first_child()
        while child is not None:
            if isinstance(child, Gtk.Label) and child.has_css_class("caption"):
                lines.append((child.get_label(), child.has_css_class("warning")))
            walk(child)
            child = child.get_next_sibling()

    walk(row)
    return row.get_subtitle(), lines


def _put(record, when):
    """A record of the device as the sync writes it (paths.write_private)."""
    from openexpress import paths

    record = {**record, "time": when.timestamp()}
    paths.write_private(service.device_last_sync_path(record["unit_id"]),
                        json.dumps(record, ensure_ascii=False))


@pytest.mark.needs_display
@pytest.mark.parametrize("language", ["en", "pl"], indirect=True)
def test_from_the_file_to_the_screen(xdg, language):
    """As on 23.10.2026: the device page showed the sync before (its words "Brak nowych
    danych do wysłania"), then the Edge's sync was written and shown again by the device
    page and by the change of the file: the row shows that sync alone, once."""
    now = datetime.now()
    before = _record("real-source-edge-1050-0.6.3.json")
    today = _record("real-flatpak-edge-1050-0.6.13.json")
    assert before["unit_id"] == today["unit_id"]  # the same device
    win = _window(today["unit_id"])
    _put(before, now - timedelta(days=4))
    win._show_last_sync()
    _put(today, now - timedelta(days=3))
    win._show_last_sync()  # the device page drawn after the sync
    win._show_last_sync()  # the file changed: shown again
    counts, details = EXPECTED["real-flatpak-edge-1050-0.6.13.json"][language]
    ago = {"en": "3 days ago", "pl": "3 dni temu"}[language]
    assert _shown(win.last_sync_row) == (f"{ago}{NB}· {counts}", details)


def test_the_notification_of_the_background_sync_says_what_the_row_says(monkeypatch):
    """The notification after a sync in the background: the numbers and the lines of the row,
    then the first errors, from the same words."""
    import types

    from openexpress import last_sync, watch

    record = _record("format-0.6.13-warnings.json")
    summary = service.SyncSummary(uploaded=record["uploaded"], rejected=record["rejected"],
                                  errors=list(record["errors"]), lines=list(record["lines"]))
    shown = []
    session = types.SimpleNamespace(
        mass_storage=False, logged_in=True, first_sync=False, client=None,
        device=types.SimpleNamespace(unit_id=record["unit_id"], description="Test watch"))
    monkeypatch.setattr(watch.service, "open_session", lambda: session)
    monkeypatch.setattr(watch.service, "require_account_device", lambda client, unit: None)
    monkeypatch.setattr(watch.service, "run_sync", lambda s, progress=None, **kw: summary)
    monkeypatch.setattr(watch.GLib, "idle_add", lambda fn, *args: shown.append(args))
    watch.Watcher()._sync()
    told = last_sync.describe(summary.record(record["unit_id"]))
    assert shown[-1] == ("Sync finished", told.notification)
    assert told.notification.splitlines() == [
        "3 new files, 1 uploaded", "Files from the device: activities ×3",
        "1 file was rejected by Garmin", "1 file could not be uploaded",
        "Errors: FIT_TYPE_4/2026-10-01-10-00-00.fit: HTTP 500"]
    assert summary.counts == told.counts and summary.text == told.text
