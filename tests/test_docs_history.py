"""The documents (README.md, CONTRIBUTING.md, docs/*.md) say what the program does, not when it
changed: no dates and no versions of openExpress (0.x.y), but for the table of docs/devices.md,
whose last column says on which system and version a user reported a device. Versions of other
software (a device's software, libadwaita 1.5, Flatpak 1.16, GNOME 50) are facts and stay."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCUMENTS = [ROOT / "README.md", ROOT / "CONTRIBUTING.md", *sorted((ROOT / "docs").glob("*.md"))]
MONTHS = ("January|February|March|April|May|June|July|August|September|October|November|"
          "December")
DATE = re.compile(rf"\b\d{{1,2}} ({MONTHS}) \d{{4}}\b|\b({MONTHS}) (\d{{1,2}}, )?\d{{4}}\b"
                  r"|\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}\.\d{1,2}\.\d{4}\b")
VERSION = re.compile(r"\b0\.\d+\.\d+\b")


def history(path: Path) -> list[str]:
    """What in a document says when: its dates, and its versions of openExpress outside the
    table of docs/devices.md."""
    found = []
    name = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path.name
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        for match in DATE.finditer(line):
            found.append(f"{name}:{number}: {match.group(0)}")
        if not (path.name == "devices.md" and line.startswith("|")):
            for match in VERSION.finditer(line):
                found.append(f"{name}:{number}: {match.group(0)}")
    return found


def test_the_documents_say_no_dates_and_no_versions_of_openexpress():
    assert [hit for path in DOCUMENTS for hit in history(path)] == []


def test_the_guard_finds_a_date_or_a_version_of_openexpress(tmp_path):
    made_up = tmp_path / "made_up.md"
    made_up.write_text("tested on 14 October 2026\nas in October 2026 and 2026-10-14\n"
                       "since 0.6.12\n| a row | Flatpak 0.6.1 |\nGNOME 50, libadwaita 1.5\n",
                       encoding="utf-8")
    hits = [hit.split(": ", 1)[1] for hit in history(made_up)]
    assert hits == ["14 October 2026", "October 2026", "2026-10-14", "0.6.12", "0.6.1"]
