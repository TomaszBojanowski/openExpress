"""The line of the log before updates are copied: which of the offered updates were chosen, which
were not, and which the program left out, with why (made-up part numbers)."""
import logging
import types

import pytest
from fakes import window as stand_in

from openexpress import updates

LOGGER = "openexpress.updates"


def _update(part, version, path=None, **kw):
    return updates.SoftwareUpdate(
        part_number=part, name="Made-up file", version=version, severity="Recommended",
        is_primary=False, is_reinstall=kw.pop("is_reinstall", False), restart_required=False,
        device_path=path or f"Garmin/{part}.bin", url="", md5=None, size=100, order=0,
        release_state="live", **kw)


def test_the_chosen_the_others_and_why_the_program_left_some_out():
    a = _update("006-D1074-00", "43.00")
    b = _update("006-D9267-00", "2.31")
    c = _update("006-D0001-00", "1.00", on_device=True)
    d = _update("006-D0002-00", "2.00", is_reinstall=True)
    meta = _update("006-D1074-00-META", "43.00")
    assert updates.choice_text([a, b, c, d, meta], [a, meta]) == (
        "1 of 4 offered chosen (006-D1074-00 43.00); not chosen: 006-D9267-00 2.31; left out: "
        "006-D0001-00 1.00 (on the device already), 006-D0002-00 2.00 (a reinstall)")


def test_all_chosen_says_no_others():
    a, b = _update("006-D1074-00", "43.00"), _update("006-D9267-00", "2.31")
    assert updates.choice_text([a, b], [a, b]) == (
        "2 of 2 offered chosen (006-D1074-00 43.00, 006-D9267-00 2.31)")


def test_one_for_the_file_of_another_is_put_off():
    a = _update("006-B0000-00", "23.40", "Garmin/GUPDATE.GCD")
    b = _update("006-B0001-00", "6.70", "garmin/gupdate.gcd")
    assert updates.choice_text([a, b], [a]) == (
        "1 of 2 offered chosen (006-B0000-00 23.40); left out: 006-B0001-00 6.70 (put off: "
        "the same file as 006-B0000-00)")


@pytest.mark.parametrize("given", [True, False])
def test_install_logs_the_choice_when_it_is_given_what_was_offered(monkeypatch, caplog, given):
    a, b = _update("006-D1074-00", "43.00"), _update("006-D9267-00", "2.31")
    monkeypatch.setattr(updates, "_install", lambda chosen, storage, **kw: kw["report"])
    with caplog.at_level(logging.INFO, logger=LOGGER):
        updates.install([a], None, **({"offered": [a, b]} if given else {}))
    said = [r.getMessage() for r in caplog.records if r.getMessage().startswith("software update")]
    line = ("software update: 1 of 2 offered chosen (006-D1074-00 43.00); not chosen: "
            "006-D9267-00 2.31")
    assert said == ([line] if given else [])


def test_the_window_gives_what_was_offered(monkeypatch):
    from openexpress.gui.updates_page import _install_task

    a, b = _update("006-D1074-00", "43.00"), _update("006-D9267-00", "2.31")
    seen = {}
    monkeypatch.setattr(updates, "install", lambda chosen, storage, **kw: seen.update(kw) or
                        updates.InstallReport())
    s = types.SimpleNamespace(storage=None, client=types.SimpleNamespace(http=None),
                              device=types.SimpleNamespace(unit_id=1234), mass_storage=False)
    win = stand_in(updates_cache=[a, b], progress=None)
    work, _done, _failed = _install_task(win, s, [a])
    work()
    assert seen["offered"] == [a, b]


def test_the_command_line_gives_what_was_offered(device_root, monkeypatch, capsys):
    from openexpress.cli import main
    from openexpress.connect import GarminClient, lower_keys

    def entry(part, filename):
        return {"partNumber": part, "productName": "Made-up file", "dataType": "Firmware",
                "displayVersion": "1.00", "severity": "Recommended", "isPrimaryFirmware": False,
                "releaseState": "live", "releaseNotes": [],
                "deliverable": {"downloadUrl": "https://example.org/" + filename,
                                "md5": "0" * 32, "sizeInBytes": 10},
                "installation": {"path": "Garmin", "fileName": filename, "isReinstall": False,
                                 "isRestartRequired": False, "order": 0}}

    answer = lower_keys({"updates": [entry("006-D0001-00", "a.bin"), entry("006-D0002-00", "b.bin")]})
    monkeypatch.setattr(GarminClient, "software_updates",
                        lambda self, raw, include_reinstalls=False: answer)
    monkeypatch.setattr(GarminClient, "unit_info", lambda self, unit_id, part_number: {})
    seen = {}
    monkeypatch.setattr(updates, "install", lambda chosen, storage, **kw: seen.update(kw) or
                        updates.InstallReport())
    assert main(["--path", str(device_root), "updates", "--install", "006-D0001-00",
                 "--apply"]) == 0
    assert [u.part_number for u in seen["offered"]] == ["006-D0001-00", "006-D0002-00"]
