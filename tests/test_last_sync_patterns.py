"""The row "Last Sync" and its details, from the file to the screen, on records of real syncs
and on shapes older versions wrote (tests/data/last_sync/, unit IDs made up): the exact
English and Polish words, no line twice, none against the row.

Every change of this row comes with a new record here and its exact words below."""

import json
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fakes import descendants, window_methods
from fakes import window as stand_in

from openexpress import i18n, service, sync

PATTERNS = Path(__file__).resolve().parent / "data" / "last_sync"

NB = "\u00a0"
# The local day of the other install's last sync in format-other-install.json.
OTHER_DAY = time.strftime("%Y-%m-%d", time.localtime(1790000000.0))
OTHER_DAY_PL = time.strftime("%d.%m.%Y", time.localtime(1790000000.0))
NOTHING_NEW = {"No new data to upload", "Brak nowych danych do wysłania"}

# name: {language: (the numbers, [(a line of the details, whether it is a warning)])}
EXPECTED = {
    # No new file on the device, a course from Garmin Connect written: the row of a device that
    # has new activities by now (test_the_row_says_what_is_to_upload_now).
    # The version from source syncs a device the Flatpak synced after it (the day is the local
    # day of 1790000000).
    "format-other-install.json": {
        "en": ("nothing to upload", [
            ((f"This device was also synced by openExpress (Flatpak), last on {OTHER_DAY}; "
              "files it sent are not sent again"), True)]),
        "pl": ("nic do wysłania", [
            ((f"To urządzenie synchronizował też openExpress (Flatpak), ostatnio {OTHER_DAY_PL}; "
              "pliki, które wysłał, nie są wysyłane ponownie"), True)])},
    # A test drive (a USB drive taken for a Garmin drive): its files uploaded, Garmin Connect's
    # queue and TrueUp left to the watch it stands in for.
    "format-test-drive.json": {
        "en": ("2 new files, 2 uploaded", [
            (("A test drive: Garmin Connect's queue and TrueUp are left to the device it "
              "stands in for"), False),
            ("Files from the device: activities ×2", False)]),
        "pl": ("2 nowe pliki, 2 wysłane", [
            ((f"Dysk testowy: kolejka Garmin Connect i{NB}TrueUp zostają dla urządzenia, "
              "które zastępuje"), False),
            (f"Pliki z{NB}urządzenia: aktywności ×2", False)])},
    "format-nothing-new-one-course-received.json": {
        "en": ("nothing to upload · 1 received", [
            ("1 file from Garmin Connect: courses", False)]),
        "pl": ("nic do wysłania · 1 odebrany", [
            (f"1 plik z{NB}Garmin Connect: kursy", False)])},
    # A sync of an Edge 1050 (real-flatpak-edge-1050-0.6.13.json): the record has two steps,
    # shown once each, not under "No new data to upload" of the sync before.
    "real-flatpak-edge-1050-0.6.13.json": {
        "en": ("16 new files, 12 uploaded", [
            (("Files from the device: monitoring ×2, segment list, activity profile backups "
             "×4, Garmin Coach plan ×9"), False),
            ("4 files were already in Garmin Connect", False)]),
        "pl": ("16 nowych plików, 12 wysłanych", [
            ((f"Pliki z{NB}urządzenia: monitorowanie ×2, lista segmentów, kopie profili "
             "aktywności ×4, plan Garmin Coach ×9"), False),
            (f"4 pliki były już w{NB}Garmin Connect", False)])},
    "real-flatpak-fenix-8-0.6.3.json": {
        "en": ("8 new files, 6 uploaded", [
            (("Files from the device: activities, monitoring, segment list, settings change "
             "log, device log, training status data, primary training device data, Garmin "
             "Coach plan"), False),
            ("2 files were already in Garmin Connect", False)]),
        "pl": ("8 nowych plików, 6 wysłanych", [
            ((f"Pliki z{NB}urządzenia: aktywności, monitorowanie, lista segmentów, dziennik "
             f"zmian ustawień, dziennik urządzenia, dane stanu wytrenowania, dane urządzenia "
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
             "devices; turn it on in Garmin Connect"), False)]),
        "pl": ("nic nie wysłano: brak zgody na koncie", [
            (("Nie wysłano danych: na koncie Garmin nie ma zgody na przesyłanie danych "
             f"z{NB}urządzeń; włącz ją w{NB}Garmin Connect"), False)])},
    # `sync --download-only --apply` of the Edge 1050 by the version from source
    # (real-source-edge-1050-0.6.15-download-only.json): nothing received; TrueUp in the
    # words it was written in (with its hard space).
    "real-source-edge-1050-0.6.15-download-only.json": {
        "en": ("upload skipped", [
            (f"TrueUp: dzienne podsumowania uzgodnione z{NB}innymi urządzeniami Garmin", False)]),
        "pl": ("wysyłanie pominięte", [
            (f"TrueUp: dzienne podsumowania uzgodnione z{NB}innymi urządzeniami Garmin", False)])},
    # sync --apply --download-only: the uploads were not looked at, so not "nothing to
    # upload"; no line of its own, the row says it.
    "format-0.6.16-upload-skipped.json": {
        "en": ("upload skipped · 5 received", []),
        "pl": ("wysyłanie pominięte · 5 odebranych", [])},
    # sync --apply --type FIT_TYPE_4: the numbers are of the activities only, and the row says
    # so; no line of its own.
    "format-0.6.16-only-types.json": {
        "en": ("activities only: 3 new files, 3 uploaded", [
            ("Files from the device: activities ×3", False)]),
        "pl": ("tylko aktywności: 3 nowe pliki, 3 wysłane", [
            (f"Pliki z{NB}urządzenia: aktywności ×3", False)])},
    # A sync of the fēnix 8 by the version from source: one new file uploaded, and TrueUp
    # received (counted in the row), in the words it was written in (with its hard space).
    "real-source-fenix-8-0.6.18-trueup-received.json": {
        "en": ("1 new file, 1 uploaded · 1 received", [
            ("Files from the device: segment list", False),
            (f"TrueUp: dzienne podsumowania uzgodnione z{NB}innymi urządzeniami Garmin", False)]),
        "pl": ("1 nowy plik, 1 wysłany · 1 odebrany", [
            (f"Pliki z{NB}urządzenia: lista segmentów", False),
            (f"TrueUp: dzienne podsumowania uzgodnione z{NB}innymi urządzeniami Garmin", False)])},
    # A sync whose device had nothing new and that wrote TrueUp from the queue: TrueUp counts
    # as received, so the row does not say "nothing to upload" alone; no real sync has made it
    # yet (tests/test_fixes_ui.py: what the command writes).
    "format-0.6.19-trueup-received.json": {
        "en": ("nothing to upload · 1 received", [
            ("TrueUp: daily totals aligned with your other Garmin devices", False)]),
        "pl": ("nic do wysłania · 1 odebrany", [
            ("TrueUp: daily totals aligned with your other Garmin devices", False)])},
    # Every line of the details kept as data (format-0.6.20-details-as-data.json): words of
    # the language of the moment.
    "format-0.6.20-details-as-data.json": {
        "en": ("2 new files, 1 uploaded · 2 received", [
            ("Marked 12 existing files as already uploaded", False),
            ("Files from the device: activities ×2", False),
            ("Skipped 1 file: another device is your primary wearable in Garmin Connect", False),
            ("Installed Connect IQ apps: Face It", False),
            (("1 Connect IQ app waits in the queue: openExpress cannot install apps on this "
              "device over USB yet"), False),
            ("TrueUp: daily totals aligned with your other Garmin devices", False),
            ("Skipped 1 item from Garmin Connect (not accepted by the device)", False)]),
        "pl": ("2 nowe pliki, 1 wysłany · 2 odebrane", [
            ("Oznaczono 12 istniejących plików jako już wysłane", False),
            (f"Pliki z{NB}urządzenia: aktywności ×2", False),
            (("Pominięto 1 plik: Twoim podstawowym urządzeniem typu wearable "
              f"w{NB}Garmin Connect jest inne urządzenie"), False),
            ("Zainstalowane aplikacje Connect IQ: Face It", False),
            ((f"1 aplikacja Connect IQ czeka w{NB}kolejce: nie można jeszcze instalować "
              "aplikacji na tym urządzeniu przez USB"), False),
            (f"TrueUp: dzienne podsumowania uzgodnione z{NB}innymi urządzeniami Garmin", False),
            (f"Pominięto 1 element z{NB}Garmin Connect (nieobsługiwany przez urządzenie)",
             False)])},
    # A sync of an Edge 1050 (real-flatpak-edge-1050-0.6.20-trueup-metrics.json): three
    # metrics files and TrueUp from the queue, the files only counted.
    "real-flatpak-edge-1050-0.6.20-trueup-metrics.json": {
        "en": ("8 new files, 8 uploaded · 4 received", [
            (("Files from the device: monitoring ×3, segment list, settings change log, "
              "Garmin Coach plan ×3"), False),
            ("TrueUp: daily totals aligned with your other Garmin devices", False)]),
        "pl": ("8 nowych plików, 8 wysłanych · 4 odebrane", [
            ((f"Pliki z{NB}urządzenia: monitorowanie ×3, lista segmentów, dziennik zmian "
              "ustawień, plan Garmin Coach ×3"), False),
            (f"TrueUp: dzienne podsumowania uzgodnione z{NB}innymi urządzeniami Garmin",
             False)])},
    # The same sync as this version writes it: the files of the queue named by their type.
    "format-0.6.21-received-files.json": {
        "en": ("8 new files, 8 uploaded · 4 received", [
            (("Files from the device: monitoring ×3, segment list, settings change log, "
              "Garmin Coach plan ×3"), False),
            ("3 files from Garmin Connect: metrics ×3", False),
            ("TrueUp: daily totals aligned with your other Garmin devices", False)]),
        "pl": ("8 nowych plików, 8 wysłanych · 4 odebrane", [
            ((f"Pliki z{NB}urządzenia: monitorowanie ×3, lista segmentów, dziennik zmian "
              "ustawień, plan Garmin Coach ×3"), False),
            (f"3 pliki z{NB}Garmin Connect: zestawienia ×3", False),
            (f"TrueUp: dzienne podsumowania uzgodnione z{NB}innymi urządzeniami Garmin",
             False)])},
    # A sync of a bike computer that uploaded the backups of its activity profiles (FIT file
    # type 57): the type by its name, not its code (format-activity-profile-backups.json; made
    # up).
    "format-activity-profile-backups.json": {
        "en": ("6 new files, 6 uploaded", [
            (("Files from the device: monitoring, activity profile backups ×4, Garmin Coach "
              "plan"), False)]),
        "pl": ("6 nowych plików, 6 wysłanych", [
            ((f"Pliki z{NB}urządzenia: monitorowanie, kopie profili aktywności ×4, plan "
              "Garmin Coach"), False)])},
    "format-0.7.5-ecg-not-uploaded.json": {
        "en": ("1 new file, 1 uploaded", [
            ("Files from the device: monitoring", False),
            ("1 ECG recording was not uploaded: ECG recordings need a Garmin Health account",
             False)]),
        "pl": ("1 nowy plik, 1 wysłany", [
            (f"Pliki z{NB}urządzenia: monitorowanie", False),
            ("1 nagranie EKG nie zostało wysłane: nagrania EKG wymagają konta Garmin Health",
             False)])},
    # The wait for TrueUp stopped by the user (format-0.7.7-trueup-stopped.json; made up, as
    # no real sync has had one).
    "format-0.7.7-trueup-stopped.json": {
        "en": ("1 new file, 1 uploaded", [
            ("Files from the device: monitoring", False),
            ("TrueUp: waiting stopped; it comes with the next sync", False)]),
        "pl": ("1 nowy plik, 1 wysłany", [
            (f"Pliki z{NB}urządzenia: monitorowanie", False),
            (f"TrueUp: czekanie przerwane; przyjdzie z{NB}następną synchronizacją", False)])},
    # A sync stopped after its uploads (format-0.7.10-stopped.json; made up, as no real sync
    # has kept one): the error that stopped it among its errors, in the words of the moment
    # of the sync.
    "format-0.7.10-stopped.json": {
        "en": ("2 new files, 2 uploaded", [
            ("Files from the device: activities, monitoring", False),
            ("The sync stopped before the end: Connect file queue: HTTP 503", True)]),
        "pl": ("2 nowe pliki, 2 wysłane", [
            (f"Pliki z{NB}urządzenia: aktywności, monitorowanie", False),
            ("The sync stopped before the end: Connect file queue: HTTP 503", True)])},
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
    # A file the device was still writing when it was read, cut short: not uploaded, read again
    # by the next sync (format-0.7.11-still-written.json; made up, as no real sync has kept
    # one).
    "format-0.7.11-still-written.json": {
        "en": ("2 new files, 1 uploaded", [
            ("Files from the device: monitoring, Garmin Coach plan", False),
            (("1 file was still being written by the device – unplug it and plug it in again, "
              "then sync again"), True)]),
        "pl": ("2 nowe pliki, 1 wysłany", [
            (f"Pliki z{NB}urządzenia: monitorowanie, plan Garmin Coach", False),
            ((f"1 plik był jeszcze zapisywany przez urządzenie{NB}– odłącz je i{NB}podłącz "
              "ponownie, potem zsynchronizuj jeszcze raz"), True)])},
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


NOW_TO_UPLOAD = {
    "en": f"3 days ago{NB}· nothing to upload{NB}· 1 received{NB}· 2 activities to upload now",
    "pl": (f"3 dni temu{NB}· nic do wysłania{NB}· 1 odebrany{NB}· 2 aktywności do wysłania "
           "teraz"),
}


@pytest.mark.parametrize("language", ["en", "pl"], indirect=True)
def test_the_row_says_what_is_to_upload_now(language):
    """What the last sync found, then what the device has to upload now, apart: the row of the
    device page and the line of `openexpress status` (gui.words.with_activities_to_upload)."""
    from openexpress import last_sync
    from openexpress.gui import words

    record = _record("format-nothing-new-one-course-received.json")
    to_sync = sync.ActivitiesToSync(record["unit_id"], frozenset({"a.fit", "b.fit"}), 2)
    row = last_sync.describe(record).row(datetime.fromtimestamp(record["time"])
                                         + timedelta(days=3))
    assert words.with_activities_to_upload(row, record, to_sync) == NOW_TO_UPLOAD[language]


def _window(unit_id):
    import types

    from gi.repository import Adw


    # The device synced here (its state.json: the one source of "never synced").
    sync.SyncState(unit_id).save()
    win = stand_in(
        session=types.SimpleNamespace(device=types.SimpleNamespace(part_number="006-B0000-00", unit_id=unit_id),
                                      ownership_error=None),
        last_sync_row=Adw.ExpanderRow(title="Last sync"), _last_sync_children=[])
    window_methods(win, ["_show_last_sync"])
    return win


def _shown(row):
    """(the subtitle of the row, [(a line of its details, whether it is a warning)])."""
    from gi.repository import Gtk

    lines = [(w.get_label(), w.has_css_class("warning")) for w in descendants(row)
             if isinstance(w, Gtk.Label) and w.has_css_class("caption")]
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
    """The device page shows the sync before (its words "Brak nowych danych do wysłania"),
    then the Edge's sync is written and shown again by the device page and by the change of
    the file: the row shows that sync alone, once."""
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
        mass_storage=False, logged_in=True, first_sync=False, client=None, storage=None,
        device=types.SimpleNamespace(part_number="006-B0000-00", unit_id=record["unit_id"], description="Test watch"))
    monkeypatch.setattr(watch.service, "open_session", lambda: session)
    monkeypatch.setattr(watch.service, "require_account_device", lambda client, unit, fresh=False, **_kw: None)
    monkeypatch.setattr(watch.service, "run_sync", lambda s, progress=None, **kw: summary)
    monkeypatch.setattr(watch.GLib, "idle_add", lambda fn, *args: shown.append(args))
    watch.Watcher()._sync()
    told = last_sync.describe(summary.record(record["unit_id"]))
    assert shown[-1] == ("Test watch synced", told.notification)
    assert told.notification.splitlines() == [
        "3 new files, 1 uploaded", "Files from the device: activities ×3",
        "1 file was rejected by Garmin", "1 file could not be uploaded",
        "Errors: FIT_TYPE_4/2026-10-01-10-00-00.fit: HTTP 500"]
    unnamed = last_sync.describe(summary.record())
    assert unnamed.counts == told.counts and unnamed.text == told.text
