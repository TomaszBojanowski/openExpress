import re
import shutil
import subprocess
import sys
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
    assert i18n.pgettext("tab", "Device") == "Urządzenie"
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


# A one-letter word: a letter that follows no letter or digit, and is no format code (%i),
# tag (<a …>), option (-a), placeholder ({a}), path (/a) or escape (\\a).
ONE_LETTER_WORD = re.compile(r"(?<![\w%<\-/{\\])[wziouaWZIOUA] ")


def _unescaped(raw: str) -> str:
    """A text of a .po file as the program gets it (its escapes undone)."""
    return re.sub(r"\\(.)", lambda m: {"n": "\n", "t": "\t"}.get(m.group(1), m.group(1)), raw)


def test_no_polish_one_letter_word_is_left_at_the_end_of_a_line():
    """Polish typography: after w, z, i, o, u and a (also at the start of a sentence) a hard
    space (U+00A0), so that a text that wraps never leaves one at the end of a line (the
    window at 360 px, 21.10.2026: "…oprogramowanie w / wersji beta")."""
    left = [(e["msgid"], value) for e in _entries(ROOT / "po" / "pl.po")
            for key, value in e.items()
            if key.startswith("msgstr") and ONE_LETTER_WORD.search(_unescaped(value))]
    assert left == []


SAMPLE_PO = """msgid ""
msgstr ""
"Language: pl\\n"

#: x.py:1
msgid "a b"
msgstr "Plik w katalogu i z kopii"

msgid "{a} sizes"
msgstr ""
"Rozmiar {a} w "
"oknie %i <a href=\\"x\\">a</a> -a opcja"

msgid "one"
msgid_plural "many"
msgstr[0] "a {count} z"
msgstr[1] "W tle"
msgstr[2] "u nas"

#~ msgid "old"
#~ msgstr "w starym"
"""


def test_the_tool_puts_hard_spaces_into_msgstr_only(tmp_path):
    """tools/po-hard-spaces.py: a preview writes nothing; --apply changes the spaces after
    one-letter words in msgstr only (also at the end of a line of the file), never in the
    header, a msgid, an obsolete entry, a format code, a tag or an option."""
    tool = ROOT / "tools" / "po-hard-spaces.py"
    po = tmp_path / "pl.po"
    po.write_text(SAMPLE_PO, encoding="utf-8")
    preview = subprocess.run([sys.executable, "-I", str(tool), str(po)], capture_output=True,
                             text=True, check=True)
    assert po.read_text(encoding="utf-8") == SAMPLE_PO
    assert preview.stdout.endswith("\n5 texts, 7 spaces (preview, nothing written)\n")
    subprocess.run([sys.executable, "-I", str(tool), str(po), "--apply"], capture_output=True,
                   check=True)
    nb = "\u00a0"
    assert po.read_text(encoding="utf-8") == (
        SAMPLE_PO.replace('"Plik w katalogu i z kopii"', f'"Plik w{nb}katalogu i{nb}z{nb}kopii"')
        .replace('"Rozmiar {a} w "', f'"Rozmiar {{a}} w{nb}"')
        .replace('"a {count} z"', f'"a{nb}{{count}} z"')
        .replace('"W tle"', f'"W{nb}tle"').replace('"u nas"', f'"u{nb}nas"'))


@pytest.mark.skipif(not shutil.which("msgfmt"), reason="msgfmt not installed")
@pytest.mark.parametrize("po", PO_FILES, ids=lambda p: p.stem)
def test_compiled_catalog_is_current(po, tmp_path):
    mo = ROOT / "openexpress/locale" / po.stem / "LC_MESSAGES" / f"{i18n.DOMAIN}.mo"
    fresh = tmp_path / "fresh.mo"
    subprocess.run(["msgfmt", "--check", "-o", str(fresh), str(po)], check=True)
    assert mo.read_bytes() == fresh.read_bytes(), "run tools/update-translations.sh"


# --- the decimal separator follows the language of the program --------------------------------


@pytest.fixture
def polish():
    from openexpress import i18n

    i18n.setup("pl")
    yield
    i18n.setup("en")


SIZES = (None, 0, 512, 699392, 6657199308, 7623566950)


def test_sizes_in_english_have_a_decimal_point():
    from openexpress.cli import _human

    assert [_human(n) for n in SIZES] == [
        "?", "0\u00a0B", "512\u00a0B", "699.4\u00a0kB", "6.7\u00a0GB", "7.6\u00a0GB"]


def test_sizes_in_polish_have_a_decimal_comma(polish):
    from openexpress.cli import _human

    assert [_human(n) for n in SIZES] == [
        "?", "0\u00a0B", "512\u00a0B", "699,4\u00a0kB", "6,7\u00a0GB", "7,6\u00a0GB"]


def test_separator_comes_from_the_catalog_and_is_one_character(polish):
    from openexpress import i18n

    assert i18n.pgettext("decimal separator", ".") == ","
    assert i18n.decimal("1.5 MB") == "1,5 MB" and i18n.decimal("12 B") == "12 B"


@pytest.mark.parametrize("bad", ["", ".", ",,", "x", " ", "1"])
def test_a_wrong_separator_in_a_catalog_is_ignored(monkeypatch, bad):
    """A translation that is not a single punctuation mark must not garble the numbers."""
    from openexpress import i18n

    monkeypatch.setattr(i18n, "pgettext", lambda context, message: bad)
    assert i18n.decimal("6.2 GB") == "6.2 GB"


def test_language_without_a_translation_keeps_the_point(monkeypatch):
    import gettext

    from openexpress import i18n

    monkeypatch.setattr(i18n, "_translation", gettext.NullTranslations())
    assert i18n.decimal("6.2 GB") == "6.2 GB"


def test_what_programs_read_keeps_the_point_in_polish(polish, device_root, capsys):
    """The log, the device report, JSON output and version numbers are not localized."""
    import json

    from openexpress import applog, report
    from openexpress.cli import main

    assert applog.size(699392) == "699_392 B"  # exact bytes, no decimal separator at all
    assert report._size(6657199308) == "6.7 GB"  # the window says "6,7 GB" in Polish

    assert main(["--path", str(device_root), "info", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)  # still valid JSON with plain numbers
    assert isinstance(data["free_bytes"], int) and isinstance(data["unit_id"], int)


def test_cli_in_polish_shows_sizes_with_a_comma_and_versions_with_a_point(polish, device_root,
                                                                          capsys, monkeypatch):
    from openexpress.cli import main
    from openexpress.device.storage import LocalStorage

    monkeypatch.setattr(LocalStorage, "free_space", lambda self: 6657199308)
    assert main(["--path", str(device_root), "info"]) == 0
    out = capsys.readouterr().out
    assert "6,7 GB" in out and "6.7 GB" not in out
    assert "23.39" in out and "23,39" not in out  # a version number is not a decimal fraction


def test_window_row_in_polish_uses_the_same_sizes(polish):
    from openexpress.gui import app
    from openexpress.i18n import _

    assert app._human is __import__("openexpress.cli", fromlist=["_human"])._human
    text = _("{free} free of {total}").format(free=app._human(6657199308),
                                              total=app._human(7623566950))
    assert text == "6,7\u00a0GB wolne z\u00a07,6\u00a0GB"
