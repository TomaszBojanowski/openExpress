"""The details of "Last sync" in the language of the moment they are shown, not of the moment
of the sync: a sync keeps each line as a kind and its numbers or names, put into words when
shown. Kept as words, the lines of the first sync's choice, the tracker conflict, the Connect
IQ apps, TrueUp and the items the device does not accept would stay in the language of the
sync: a sync in Polish would show TrueUp in Polish in an English window. A record of an older
version keeps its words. All data here is made up."""

import json
import types
from pathlib import Path

import pytest
from fakes import step_words
from test_received_from_queue import APP, TRUEUP, UNIT, _synced_once

from openexpress import ciq, i18n, last_sync, service

PATTERNS = Path(__file__).resolve().parent / "data" / "last_sync"
NB = "\u00a0"

# An item of the queue whose data type the made-up device does not take.
NOT_TAKEN = {"messageid": 11, "devicexmldatatype": "FIT_TYPE_250",
             "metadata": {"messageurl": "other/11", "messagename": "Something"}}

SECOND = {  # the details of the second sync, in each language
    "en": ["Installed Connect IQ apps: Face It",
           ("1 Connect IQ app waits in the queue: openExpress cannot install apps on this "
            "device over USB yet"),
           "TrueUp: daily totals aligned with your other Garmin devices",
           "Skipped 1 item from Garmin Connect (not accepted by the device)"],
    "pl": ["Zainstalowane aplikacje Connect IQ: Face It",
           (f"1 aplikacja Connect IQ czeka w{NB}kolejce: nie można jeszcze instalować "
            "aplikacji na tym urządzeniu przez USB"),
           f"TrueUp: dzienne podsumowania uzgodnione z{NB}innymi urządzeniami Garmin",
           f"Pominięto 1 element z{NB}Garmin Connect (nieobsługiwany przez urządzenie)"],
}


def _details(record, language):
    i18n.setup(language)
    try:
        return [text for text, _warning in last_sync.describe(record).steps]
    finally:
        i18n.setup("en")


@pytest.mark.parametrize("written_in, shown_in", [("pl", "en"), ("en", "pl")])
def test_details_are_put_into_words_in_the_language_they_are_shown_in(
        written_in, shown_in, device_root, xdg, monkeypatch):
    monkeypatch.setattr(ciq, "install_queued", lambda *a, **kw: ciq.IqInstallReport(
        installed=["Face It"], for_phone=["Another app"]))
    i18n.setup(written_in)
    try:
        session = _synced_once(device_root)  # the first sync: existing files marked
        first = service.load_last_sync(UNIT)
        session.client.messages = [APP, TRUEUP, NOT_TAKEN]
        service.run_sync(session)
        second = service.load_last_sync(UNIT)
    finally:
        i18n.setup("en")
    (marked,) = _details(first, shown_in)
    if shown_in == "en":
        assert marked.startswith("Marked ") and marked.endswith(" as already uploaded")
    else:
        assert marked.startswith("Oznaczono ") and marked.endswith(" jako już wysłane")
    assert _details(second, shown_in) == SECOND[shown_in]
    assert all(isinstance(line, dict) for line in first["lines"] + second["lines"])


def test_a_record_of_an_older_version_keeps_its_words():
    """A line kept as words (a sync of an older version in Polish) is shown as it was written."""
    record = {"time": 1800000000.0, "uploaded": 0, "downloaded": 1, "lines": [
        f"TrueUp: dzienne podsumowania uzgodnione z{NB}innymi urządzeniami Garmin"]}
    for language in ("en", "pl"):
        assert _details(record, language) == [
            f"TrueUp: dzienne podsumowania uzgodnione z{NB}innymi urządzeniami Garmin"]


def test_the_pattern_of_0_6_20_is_what_the_steps_of_a_sync_write():
    """format-0.6.20-details-as-data.json: each line as the step that writes it."""
    record = json.loads((PATTERNS / "format-0.6.20-details-as-data.json").read_text())
    report = types.SimpleNamespace(uploaded=["a"], already_in_connect=[], duplicates=[],
                                   rejected=[], failed=[])
    assert record["lines"] == [
        service.marked_known_step(12),
        service.new_files_step([types.SimpleNamespace(datatype="FIT_TYPE_4")] * 2),
        service.uploads_step(report),
        service.tracker_conflict_step(1),
        service.apps_installed_step(["Face It"]),
        service.apps_waiting_step(1, flatpak=False),
        service.trueup_step(),
        service.not_accepted_step(1),
    ]


@pytest.mark.parametrize("line", [
    {"kind": "marked_known", "count": "12"}, {"kind": "tracker_conflict"},
    {"kind": "apps_installed", "names": []}, {"kind": "apps_installed", "names": [3]},
    {"kind": "apps_waiting", "count": -1}, {"kind": "not_accepted", "count": True},
    {"kind": "received_files"}, {"kind": "received_files", "types": {}},
    {"kind": "received_files", "types": ["FIT_TYPE_44"]},
    {"kind": "received_files", "types": {"FIT_TYPE_44": "3"}},
    {"kind": "received_files", "types": {"FIT_TYPE_44": -1}},
    {"kind": "received_files", "types": {"FIT_TYPE_44": True}},
    {"kind": "received_files", "types": {"FIT_TYPE_44": 0}},
])
def test_a_damaged_line_says_nothing(line):
    """A line whose numbers or names are not what a sync writes is left out, never shown
    half or raised."""
    assert step_words([line]) == []
