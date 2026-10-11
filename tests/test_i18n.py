import re
import shutil
import subprocess
from pathlib import Path

import pytest

from openexpress import i18n

ROOT = Path(__file__).resolve().parent.parent
PO_FILES = sorted((ROOT / "po").glob("*.po"))
PLACEHOLDER = re.compile(r"\{\w+\}|%\(\w+\)\w|%s")


@pytest.fixture
def polish():
    i18n.setup("pl")
    yield
    i18n.setup("en")


def test_polish_catalog(polish):
    assert i18n.current_language() == "pl"
    assert i18n._("Install") == "Zainstaluj"
    assert i18n.pgettext("tab", "Sync") == "Synchronizacja"
    assert i18n.ngettext("{count} file", "{count} files", 5).format(count=5).startswith("5 ")
    assert "pl" in i18n.available_languages()


def test_english_is_identity():
    i18n.setup("en")
    assert i18n._("Install") == "Install"
    assert i18n.current_language() == "en"


def test_cli_help_in_polish(polish, capsys):
    from openexpress.cli import main

    with pytest.raises(SystemExit):
        main(["--lang", "pl", "--help"])
    out = capsys.readouterr().out
    assert out.startswith("użycie: openexpress")
    i18n.setup("en")


def _entries(po: Path):
    for block in po.read_text(encoding="utf-8").split("\n\n")[1:]:
        if block.lstrip().startswith("#~"):
            continue
        fields: dict[str, str] = {}
        key = None
        for line in block.splitlines():
            m = re.match(r'^(msgctxt|msgid|msgid_plural|msgstr(?:\[\d\])?) "(.*)"$', line)
            if m:
                key = m.group(1)
                fields[key] = m.group(2)
            elif line.startswith('"') and key:
                fields[key] += line[1:-1]
        if "msgid" in fields:
            yield fields


@pytest.mark.parametrize("po", PO_FILES, ids=lambda p: p.stem)
def test_placeholders_match(po):
    for e in _entries(po):
        source = set(PLACEHOLDER.findall(e["msgid"] + e.get("msgid_plural", "")))
        for key, value in e.items():
            if key.startswith("msgstr") and value:
                assert set(PLACEHOLDER.findall(value)) <= source, (e["msgid"], value)
                if key in ("msgstr", "msgstr[2]"):
                    assert source <= set(PLACEHOLDER.findall(value)) | {"%s"}, (e["msgid"], value)


@pytest.mark.skipif(not shutil.which("msgfmt"), reason="msgfmt not installed")
@pytest.mark.parametrize("po", PO_FILES, ids=lambda p: p.stem)
def test_compiled_catalog_is_current(po, tmp_path):
    mo = ROOT / "openexpress/locale" / po.stem / "LC_MESSAGES" / f"{i18n.DOMAIN}.mo"
    fresh = tmp_path / "fresh.mo"
    subprocess.run(["msgfmt", "--check", "-o", str(fresh), str(po)], check=True)
    assert mo.read_bytes() == fresh.read_bytes(), "run tools/update-translations.sh"
