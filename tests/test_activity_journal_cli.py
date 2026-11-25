"""The activity journal as the command line has it: `setup --activity-journal on|off` (on is
the yes to it; nothing is saved in settings.json until it is answered), `activities journal`
(the journal of the connected device, with --all every journal on this computer: how many
activities, since when, the gaps, the size), `activities journal --delete [--all]` (a dry run
unless --apply). Showing it never adds to it. A folder stands for the device; made-up files
only."""

import json
import logging
import os
from datetime import datetime

import pytest
from conftest import DEVICE_XML
from fakes import fit_activity, fit_time

from openexpress import cli

UNIT = 1234567890  # the unit ID of DEVICE_XML
SEEN = datetime(2026, 11, 25, 18, 0)


@pytest.fixture
def device(tmp_path):
    root = tmp_path / "device"
    (root / "GARMIN" / "Activity").mkdir(parents=True)
    (root / "GARMIN" / "GarminDevice.xml").write_text(DEVICE_XML)
    for n, day in enumerate((28, 29, 30)):
        path = root / "GARMIN" / "Activity" / f"2026-10-{day}-07-00-00.fit"
        path.write_bytes(fit_activity(fit_time(2026, 10, day, 7, 0)))
        os.utime(path, (1_000 + n, 1_000 + n))
    return root


def run(capsys, *argv) -> tuple[int, list[str]]:
    code = cli.main(list(argv))
    return code, capsys.readouterr().out.splitlines()


def journal_of(root):
    """The journal of the folder's device written as a connection writes it."""
    from openexpress import activity_journal
    from openexpress.device.storage import LocalStorage
    from openexpress.device.xmldevice import parse

    storage = LocalStorage(root)
    device = parse(storage.read("GARMIN/GarminDevice.xml"))
    activity_journal.add(device, storage, None, now=SEEN)
    return activity_journal.path(UNIT)


def settings_file():
    from openexpress import paths

    return json.loads(paths.settings_file().read_text())


def test_nothing_is_saved_until_it_is_answered():
    from openexpress.settings import Settings

    s = Settings.load()
    assert s.activity_journal is None
    s.save()
    assert "activity_journal" not in settings_file()


def test_setup_turns_it_on_and_off(capsys, caplog):
    from openexpress.settings import Settings

    caplog.set_level(logging.INFO, logger="openexpress")
    code, lines = run(capsys, "setup", "--activity-journal", "on")
    assert code == 0 and "Activity journal: on" in lines
    assert Settings.load().activity_journal is True and settings_file()["activity_journal"] is True
    assert any(r.getMessage() == "activity journal: on" for r in caplog.records)
    code, lines = run(capsys, "setup", "--activity-journal", "off")
    assert code == 0 and "Activity journal: off" in lines
    assert Settings.load().activity_journal is False


def test_the_journal_of_the_connected_device(capsys, device):
    where = journal_of(device)
    before = where.read_bytes()
    code, lines = run(capsys, "--path", str(device), "activities", "journal")
    assert code == 0
    text = "\n".join(lines)
    assert "3 activities since 2026-10-28" in text
    assert "No gaps" in text
    assert where.read_bytes() == before  # shown, not added to


def test_a_device_without_a_journal(capsys, device):
    code, lines = run(capsys, "--path", str(device), "activities", "journal")
    assert code == 0 and "No activity journal of this device." in lines


def test_every_journal_on_this_computer(capsys, device):
    from openexpress import activity_journal

    journal_of(device)
    other = activity_journal.path(987654321)
    other.parent.mkdir(parents=True)
    other.write_text(json.dumps(activity_journal.connection(SEEN, 0, None, None)) + "\n")
    code, lines = run(capsys, "activities", "journal", "--all")
    assert code == 0
    text = "\n".join(lines)
    assert "3 activities since 2026-10-28" in text and "No activities" in text
    assert str(UNIT) not in text and "987654321" not in text


def test_the_gaps_are_said(capsys, device):
    from openexpress import activity_journal

    where = journal_of(device)
    with where.open("a") as f:
        later = int(datetime(2026, 12, 20).timestamp())
        f.write(json.dumps(activity_journal.connection(SEEN, 5, later, later)) + "\n")
    _code, lines = run(capsys, "--path", str(device), "activities", "journal")
    assert any(line.strip().startswith("Not known: 2026-10-30 to 2026-12-20") for line in lines)


def test_delete_is_a_dry_run_until_apply(capsys, device):
    where = journal_of(device)
    code, lines = run(capsys, "--path", str(device), "activities", "journal", "--delete")
    assert code == 0 and where.exists()
    assert "Would delete the activity journal of Test watch: 3 activities." in lines
    code, lines = run(capsys, "--path", str(device), "activities", "journal", "--delete",
                      "--apply")
    assert code == 0 and not where.exists()
    assert "Deleted the activity journal of Test watch: 3 activities." in lines


def test_delete_every_journal(capsys, device):
    from openexpress import activity_journal

    journal_of(device)
    code, lines = run(capsys, "activities", "journal", "--delete", "--all", "--apply")
    assert code == 0 and activity_journal.journals() == []
    assert "Deleted every activity journal on this computer: 3 activities." in lines


def test_the_command_counts_activities_not_lines(capsys, device):
    """A journal an older write left with each line twice: 3 activities, not 6."""
    where = journal_of(device)
    lines = where.read_text().splitlines()
    activity = [line for line in lines if '"kind": "activity"' in line]
    where.write_text("\n".join(lines + activity) + "\n")
    _code, out = run(capsys, "--path", str(device), "activities", "journal")
    assert any("3 activities since 2026-10-28" in line for line in out)
    _code, out = run(capsys, "activities", "journal", "--all")
    assert any("3 activities since 2026-10-28" in line for line in out)
    _code, out = run(capsys, "--path", str(device), "activities", "journal", "--delete")
    assert "Would delete the activity journal of Test watch: 3 activities." in out
