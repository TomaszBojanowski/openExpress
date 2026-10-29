"""Garmin marks some kinds of device (the "segmenttype" of unit-info: Wellness, Vivomove) as
losing the data they hold when their software is updated. openExpress then warns before
every update of such a device, in the window and on the command line, and says to sync
first; it does not hold the update back. All answers are made up."""

import logging
import types

import pytest
from fakes import window as stand_in
from test_updates_system import WARNING_EN, _below, server_answer

from openexpress import updates
from openexpress.cli import main
from openexpress.connect import ApiError, GarminClient, lower_keys
from openexpress.device.storage import LocalStorage
from openexpress.device.xmldevice import parse

WINDOW_EN = ("Warning: installing an update erases the data stored on this device. Sync it with "
             "Garmin Connect first, or the data not uploaded yet is lost.")
CLI_EN = ("Warning: installing an update erases the data stored on this device. Sync it first "
          "(openexpress sync --apply), or the data not uploaded yet is lost.")
DRIVE_EN = ("Warning: installing an update erases the data stored on this device. openExpress "
            "does not sync a device connected as a USB drive: sync it with the Garmin Connect "
            "app first, or the data not uploaded yet is lost.")
PLAIN_FILE, BUNDLE = "006-D0000-00", "006-V0000-00"
WINDOW_PL = ("Ostrzeżenie: instalacja aktualizacji kasuje dane zapisane na tym urządzeniu. "
             "Najpierw zsynchronizuj je z\u00a0Garmin Connect, inaczej niewysłane jeszcze dane "
             "przepadną.")
DRIVE_PL = ("Ostrzeżenie: instalacja aktualizacji kasuje dane zapisane na tym urządzeniu. "
            "openExpress nie synchronizuje urządzenia podłączonego jako dysk USB: najpierw "
            "zsynchronizuj je w\u00a0aplikacji Garmin Connect, inaczej niewysłane jeszcze dane "
            "przepadną.")


@pytest.mark.parametrize(("info", "loses"), [
    ({"segmenttype": "Wellness"}, True), ({"segmenttype": "vivomove"}, True),
    ({"segmenttype": "VIVOMOVE"}, True), ({"segmenttype": "Fitness"}, False),
    ({"segmenttype": "BikeAccessory"}, False), ({}, False), (None, False),
    ({"segmenttype": 5}, False), ("Wellness", False)])
def test_which_kinds_lose_their_data_on_an_update(info, loses):
    assert updates.loses_data_on_update(info) is loses


@pytest.fixture
def offered(device_root, monkeypatch):
    answer = lower_keys(server_answer())
    monkeypatch.setattr(GarminClient, "software_updates",
                        lambda self, raw, include_reinstalls=False: answer)
    return device_root


def _kind(monkeypatch, segment, asked=None):
    """unit-info answers with this kind of device."""
    def unit_info(self, unit_id, part_number):
        if asked is not None:
            asked.append(part_number)
        return {"segmenttype": segment}

    monkeypatch.setattr(GarminClient, "unit_info", unit_info)


def test_cli_warns_before_any_update_of_a_device_that_loses_its_data(offered, monkeypatch,
                                                                     capsys):
    asked = []
    _kind(monkeypatch, "Wellness", asked)
    # A plain file, not the device software: still warned, in the dry run too.
    assert main(["--path", str(offered), "updates", "--install", PLAIN_FILE]) == 0
    out = capsys.readouterr().out.splitlines()
    assert CLI_EN in out
    assert out.index(CLI_EN) < out.index(next(line for line in out
                                              if line.startswith("Dry run:")))
    assert len(asked) == 1


def test_a_system_update_gets_both_warnings(offered, monkeypatch, capsys):
    _kind(monkeypatch, "Vivomove")
    assert main(["--path", str(offered), "updates", "--install", BUNDLE]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out.index(WARNING_EN) + 1 == out.index(CLI_EN)


def test_the_warning_does_not_hold_the_update_back(offered, monkeypatch, capsys):
    _kind(monkeypatch, "Vivomove")
    installed = []
    monkeypatch.setattr(updates, "install", lambda chosen, storage, **_kw: (
        installed.append(chosen), types.SimpleNamespace(installed=[]))[1])
    assert main(["--path", str(offered), "updates", "--install", PLAIN_FILE, "--apply"]) == 0
    assert CLI_EN in capsys.readouterr().out.splitlines()
    assert len(installed) == 1


def test_cli_says_nothing_of_the_data_for_another_kind(offered, monkeypatch, capsys):
    _kind(monkeypatch, "Fitness")
    assert main(["--path", str(offered), "updates", "--install", PLAIN_FILE]) == 0
    assert "erases" not in capsys.readouterr().out


def test_cli_installs_without_a_data_warning_when_unit_info_fails(offered, monkeypatch, capsys, caplog):
    def fails(self, unit_id, part_number):
        raise ApiError("Device info: HTTP 503")

    monkeypatch.setattr(GarminClient, "unit_info", fails)
    with caplog.at_level(logging.INFO, logger="openexpress"):
        assert main(["--path", str(offered), "updates", "--install", PLAIN_FILE]) == 0
    out = capsys.readouterr().out
    assert "erases" not in out and "Dry run:" in out
    assert any("unit-info" in r.getMessage() for r in caplog.records)


def test_the_list_of_updates_asks_nothing_more(offered, monkeypatch, capsys):
    """Only an install asks what kind of device it is; the list stays one request."""
    def never(self, unit_id, part_number):
        raise AssertionError("unit-info was asked")

    monkeypatch.setattr(GarminClient, "unit_info", never)
    assert main(["--path", str(offered), "updates"]) == 0


def _warnings(monkeypatch, part, **session):
    """The warnings below the window's question before `part` is installed (texts and styles),
    for a session with these values; nothing is shown and nothing is asked of a server."""
    from gi.repository import Adw

    from openexpress.gui.app import MainWindow

    items = updates.parse_updates(lower_keys(server_answer()))
    asked = []
    monkeypatch.setattr(Adw.AlertDialog, "present", lambda self, parent: asked.append(self))
    win = stand_in(session=types.SimpleNamespace(
        device=types.SimpleNamespace(software_version_str="23.39"), **session),
        updates_cache=items)
    MainWindow.confirm_update(win, next(u for u in items if u.part_number == part))
    (dialog,) = asked
    return [(w.get_label(), sorted(w.get_css_classes())) for w in _below(dialog)
            if w.has_css_class("warning")]


@pytest.mark.needs_display
def test_the_window_warns_before_an_update_that_erases_the_device(monkeypatch):
    assert _warnings(monkeypatch, PLAIN_FILE, unit_info_answer={"segmenttype": "Vivomove"}) == [
        (WINDOW_EN, ["heading", "warning"])]


@pytest.mark.needs_display
def test_the_window_says_how_many_activities_wait(monkeypatch):
    for count, words in ((1, "1 activity on the device has not been uploaded yet."),
                         (3, "3 activities on the device have not been uploaded yet.")):
        assert _warnings(monkeypatch, PLAIN_FILE, unit_info_answer={"segmenttype": "Wellness"},
                         to_sync=types.SimpleNamespace(count=count)) == [
            (f"{WINDOW_EN} {words}", ["heading", "warning"])]
    # None (never synced here) and 0 say no number.
    for count in (None, 0):
        assert _warnings(monkeypatch, PLAIN_FILE, unit_info_answer={"segmenttype": "Wellness"},
                         to_sync=types.SimpleNamespace(count=count)) == [
            (WINDOW_EN, ["heading", "warning"])]


@pytest.mark.needs_display
def test_the_window_before_a_system_update_has_both_warnings(monkeypatch):
    assert _warnings(monkeypatch, BUNDLE, unit_info_answer={"segmenttype": "Wellness"}) == [
        (WARNING_EN, ["heading", "warning"]), (WINDOW_EN, ["heading", "warning"])]


@pytest.mark.needs_display
def test_the_window_has_other_words_for_a_drive(monkeypatch):
    assert _warnings(monkeypatch, PLAIN_FILE, unit_info_answer={"segmenttype": "Wellness"},
                     mass_storage=True, to_sync=types.SimpleNamespace(count=3)) == [
        (DRIVE_EN, ["heading", "warning"])]


@pytest.mark.needs_display
def test_the_question_never_asks_the_server(monkeypatch):
    """The question is made on the window's thread: it reads only the answer kept with the
    session; without one, it does not warn."""
    def never(unit_id, part_number):
        raise AssertionError("unit-info was asked")

    assert _warnings(monkeypatch, PLAIN_FILE, unit_info_answer=None,
                     client=types.SimpleNamespace(unit_info=never)) == []
    assert _warnings(monkeypatch, PLAIN_FILE,
                     unit_info_answer={"segmenttype": "Fitness"}) == []


def _session(device_root, unit_info):
    storage = LocalStorage(device_root)
    return types.SimpleNamespace(
        storage=storage, device=parse(storage.read("GARMIN/GarminDevice.xml")),
        unit_info_answer=None, client=types.SimpleNamespace(
            software_updates=lambda raw, include_reinstalls=False: {}, unit_info=unit_info))


def _check_for_updates(session):
    """The work the window does in the background when it checks for updates."""
    from openexpress.gui.app import MainWindow

    works = []
    win = stand_in(session=session, _load=lambda key, text, work, done,
                   on_error=None: works.append(work))
    MainWindow.load_updates(win)
    (work,) = works
    return work()


def test_the_check_for_updates_also_learns_the_kind_of_device(device_root):
    s = _session(device_root, lambda unit_id, part_number: {"segmenttype": "Wellness"})
    _check_for_updates(s)
    assert s.unit_info_answer == {"segmenttype": "Wellness"}


def test_a_failed_unit_info_does_not_fail_the_check(device_root, caplog):
    def fails(unit_id, part_number):
        raise ApiError("Device info: HTTP 503")

    s = _session(device_root, fails)
    with caplog.at_level(logging.INFO, logger="openexpress"):
        items, _beta = _check_for_updates(s)
    assert items == [] and s.unit_info_answer is None
    assert any("unit-info" in r.getMessage() for r in caplog.records)


@pytest.mark.needs_display
def test_the_warning_in_polish(monkeypatch):
    from openexpress import i18n

    i18n.setup("pl")
    try:
        found = _warnings(monkeypatch, PLAIN_FILE, unit_info_answer={"segmenttype": "Wellness"},
                          to_sync=types.SimpleNamespace(count=5))
        drive = _warnings(monkeypatch, PLAIN_FILE, unit_info_answer={"segmenttype": "Wellness"},
                          mass_storage=True)
    finally:
        i18n.setup("en")
    assert found == [(WINDOW_PL + " 5 aktywności na urządzeniu nie zostało jeszcze wysłanych.",
                       ["heading", "warning"])]
    assert drive == [(DRIVE_PL, ["heading", "warning"])]
