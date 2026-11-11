"""The translations as the build makes them (openexpress/catalogs.py): the languages are those of
po/LINGUAS, each with its po/<language>.po; the compiled catalogs (.mo) are made at the build
(the Flatpak's manifest, install-desktop, the tests) and never kept in the repository; the
template (po/openexpress.pot) holds every text of the files po/POTFILES lists, the metainfo's
too; the metainfo is translated through gettext from its template, its release notes left in
English; after tools/update-template.sh and tools/update-translations.sh Polish has the
template's date and every other language is as it was (its translators', in Weblate). GNU gettext's tools are needed; made-up catalogs only where one is written."""

import os
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from openexpress import catalogs, i18n

ROOT = Path(__file__).resolve().parent.parent
XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"
needs_gettext = pytest.mark.skipif(not shutil.which("xgettext"), reason="gettext not installed")


def test_the_languages_are_those_of_linguas_each_with_its_po():
    languages = catalogs.languages()
    assert "pl" in languages
    assert sorted(p.stem for p in (ROOT / "po").glob("*.po")) == sorted(languages)


def test_linguas_takes_comments_and_several_languages_on_a_line(tmp_path):
    (tmp_path / "LINGUAS").write_text("# the languages\npl de\n\nfr  # French\n")
    assert catalogs.languages(tmp_path) == ["pl", "de", "fr"]


def test_the_tests_run_with_a_catalog_of_every_language_built_for_them():
    """tests/conftest.py builds them into a folder of its own: none in the repository."""
    shown = i18n.available_languages()
    assert shown == [i18n.SOURCE_LANGUAGE, *sorted(catalogs.languages())]
    repository_folder = Path(i18n.__file__).parent / "locale"
    assert repository_folder != i18n.LOCALE_DIR


def test_no_compiled_catalog_nor_built_metainfo_is_kept_in_the_repository():
    ignored = (ROOT / ".gitignore").read_text().splitlines()
    assert "openexpress/locale/" in ignored
    assert "openexpress/data/io.github.tomaszbojanowski.OpenExpress.metainfo.xml" in ignored
    assert catalogs.METAINFO_TEMPLATE.is_file()


def test_building_the_catalogs_says_that_gettext_is_needed(monkeypatch, tmp_path):
    monkeypatch.setattr(catalogs.shutil, "which", lambda name: None)
    with pytest.raises(catalogs.CatalogError, match="msgfmt"):
        catalogs.build(tmp_path)


@needs_gettext
def test_every_language_is_compiled_where_it_is_asked(tmp_path):
    built = catalogs.build(tmp_path)
    assert sorted(p.relative_to(tmp_path).parts[0] for p in built) == sorted(catalogs.languages())
    assert all(p.name == "openexpress.mo" and p.stat().st_size > 0 for p in built)


def test_potfiles_lists_every_file_with_texts_and_the_metainfo_template():
    listed = (ROOT / "po" / "POTFILES").read_text().split()
    assert listed == catalogs.potfiles()
    assert "openexpress/data/io.github.tomaszbojanowski.OpenExpress.metainfo.xml.in" in listed
    assert "openexpress/i18n.py" in listed and "openexpress/__main__.py" not in listed


def _msgids(pot: Path) -> set[tuple[str, str]]:
    """(context, text) of every entry of a template."""
    found, ctx, key, fields = set(), "", None, {}
    for block in pot.read_text(encoding="utf-8").split("\n\n")[1:]:
        fields, key = {}, None
        for line in block.splitlines():
            m = re.match(r'^(msgctxt|msgid) "(.*)"$', line)
            if m:
                key = m.group(1)
                fields[key] = m.group(2)
            elif line.startswith('"') and key in ("msgctxt", "msgid"):
                fields[key] += line[1:-1]
            elif not line.startswith('"'):
                key = None
        ctx = fields.get("msgctxt", "")
        if "msgid" in fields:
            found.add((ctx, fields["msgid"]))
    return found


@needs_gettext
def test_the_template_holds_every_text_of_the_code_and_the_metainfo(tmp_path):
    fresh = tmp_path / "openexpress.pot"
    catalogs.write_template(fresh)
    assert _msgids(fresh) == _msgids(ROOT / "po" / "openexpress.pot"), \
        "run tools/update-template.sh"
    texts = {text for _ctx, text in _msgids(fresh)}
    assert "Manage your Garmin device on Linux" in texts  # the metainfo's summary
    assert "No telemetry" in texts and "Connect IQ apps" in texts
    # Not to translate: the name, the developer, the keywords, the release notes.
    assert not texts & {"openExpress", "Tomasz Bojanowski", "Garmin", "fenix"}
    assert not any(t.startswith("Clearer icons.") for t in texts)


POT_DATE = re.compile(r'^"POT-Creation-Date: (.*)\\n"$', re.MULTILINE)


def _pot_date(path: Path) -> str:
    found = POT_DATE.search(path.read_text(encoding="utf-8"))
    assert found, path.name
    return found.group(1)


def _run_the_scripts(tmp_path, prepare=None) -> list[Path]:
    """tools/update-template.sh, then tools/update-translations.sh, on a copy of the tree (its
    own openexpress); `prepare(po)` first changes each po/<language>.po. The languages' files."""
    (tmp_path / "tools").mkdir()
    for script in ("update-template.sh", "update-translations.sh"):
        shutil.copy2(ROOT / "tools" / script, tmp_path / "tools" / script)
    shutil.copytree(ROOT / "po", tmp_path / "po")
    shutil.copytree(ROOT / "openexpress", tmp_path / "openexpress",
                    ignore=shutil.ignore_patterns("__pycache__", "locale"))
    languages = [tmp_path / "po" / f"{lang}.po" for lang in catalogs.languages(tmp_path / "po")]
    for po in languages if prepare else ():
        prepare(po)
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}  # the copy's openexpress
    for script in ("update-template.sh", "update-translations.sh"):
        subprocess.run(["bash", str(tmp_path / "tools" / script)], cwd=tmp_path, env=env,
                       capture_output=True, text=True, timeout=120, check=True)
    return languages


@needs_gettext
def test_after_the_scripts_polish_has_the_template_s_date_and_the_others_are_untouched(tmp_path):
    """Polish, the maintainer's, is merged with the template and has its date; every other
    language is its translators', in Weblate (whose msgmerge add-on keeps it in line with the
    template): the scripts leave its file byte for byte."""
    old = "2000-01-01 00:00+0000"  # no template's date

    def an_old_date(po):
        text = po.read_text(encoding="utf-8")
        po.write_text(POT_DATE.sub(lambda _m: f'"POT-Creation-Date: {old}\\n"', text, count=1),
                      encoding="utf-8")

    others = {}

    def prepare(po):
        an_old_date(po)
        if po.name != "pl.po":
            others[po.name] = po.read_bytes()

    languages = _run_the_scripts(tmp_path, prepare)
    template = _pot_date(tmp_path / "po" / "openexpress.pot")
    assert languages and template != old
    assert _pot_date(tmp_path / "po" / "pl.po") == template
    assert {name: (tmp_path / "po" / name).read_bytes() for name in others} == others


# --- the flags of Weblate -----------------------------------------------------------------------

def _flags_in(path: Path) -> dict[tuple[str, str], list[str]]:
    """The flags of each entry of a PO file, from its "#," line."""
    lines = path.read_text(encoding="utf-8").splitlines()
    found = {}
    for start, end, context, msgid in catalogs._entries(lines):
        line = next((x for x in lines[start:end] if x.startswith("#,")), "#,")
        found[(context, msgid)] = [f.strip() for f in line[2:].split(",") if f.strip()]
    return found


def test_every_text_of_the_weblate_flags_is_one_of_the_template():
    listed = catalogs.read_flags()
    template = _flags_in(ROOT / "po" / "openexpress.pot")
    assert listed and not set(listed) - set(template), "a text of po/weblate-flags.txt is gone"
    assert all(f.startswith("ignore-") for flags in listed.values() for f in flags)


@needs_gettext
def test_after_the_scripts_the_weblate_flags_are_in_the_template_and_polish(tmp_path):
    """msgmerge and msgattrib drop the flags they do not know: the scripts write them again,
    into the files they write."""
    def no_weblate_flags(po):
        po.write_text(re.sub(r", ignore-[a-z-]+", "", po.read_text(encoding="utf-8")),
                      encoding="utf-8")

    _run_the_scripts(tmp_path, no_weblate_flags)
    listed = catalogs.read_flags(tmp_path / "po" / "weblate-flags.txt")
    for path in [tmp_path / "po" / "openexpress.pot", tmp_path / "po" / "pl.po"]:
        found = _flags_in(path)
        missing = {key: flags for key, flags in listed.items()
                   if not set(flags) <= set(found.get(key, []))}
        assert missing == {}, path.name


PO_TEXT = """\
msgid ""
msgstr ""
"Content-Type: text/plain; charset=UTF-8\\n"

#. TRANSLATORS: a comment
#: openexpress/a.py:1
msgctxt "data field"
msgid "Stamina"
msgstr "Stamina"

#. TRANSLATORS: two lines
#: openexpress/b.py:2
#, python-brace-format
msgid ""
"{count} more "
"lines\\n"
msgid_plural "{count} more lines\\n"
msgstr[0] ""

#, fuzzy
#| msgid "Old"
msgid "Garmin"
msgstr "Garmin"

#~ msgid "Gone"
#~ msgstr "Nie ma"
"""


def test_the_flags_are_written_where_a_po_file_has_them_and_never_twice(tmp_path):
    po = tmp_path / "x.po"
    po.write_text(PO_TEXT, encoding="utf-8")
    flags = {("data field", "Stamina"): ["ignore-same"],
             ("", "{count} more lines\n"): ["ignore-same-plurals", "python-brace-format"],
             ("", "Garmin"): ["ignore-same"], ("", "Gone"): ["ignore-same"]}
    assert catalogs.add_flags(po, flags) == 3
    text = po.read_text(encoding="utf-8")
    assert "#: openexpress/a.py:1\n#, ignore-same\nmsgctxt \"data field\"" in text
    assert "#, ignore-same-plurals, python-brace-format\nmsgid \"\"" in text  # sorted
    assert "#, fuzzy, ignore-same\n#| msgid \"Old\"" in text  # before the previous msgid
    assert "#~ msgid \"Gone\"\n#~ msgstr" in text and text.count("ignore-same,") == 0
    assert catalogs.add_flags(po, flags) == 0 and po.read_text(encoding="utf-8") == text
    assert _flags_in(po)[("", "")] == []  # the header untouched


@pytest.mark.parametrize("line", ["Stamina|ignore-same", "|Stamina|", "|Stamina|Ignore Same",
                                  "|Stamina|ignore-same; ignore-reused"])
def test_a_line_of_the_weblate_flags_that_is_no_entry_is_said(tmp_path, line):
    path = tmp_path / "flags.txt"
    path.write_text("# a note\n\n" + line + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="flags.txt:3"):
        catalogs.read_flags(path)
    assert catalogs.read_flags(tmp_path / "none.txt") == {}


def test_the_metainfo_template_has_no_translation_in_it():
    text = catalogs.METAINFO_TEMPLATE.read_text(encoding="utf-8")
    assert "xml:lang" not in text
    root = ET.fromstring(text)
    notes = root.findall("releases/release/description")
    assert notes and all(d.get("translate") == "no" for d in notes)


@needs_gettext
def test_the_metainfo_is_translated_at_the_build_into_each_language(tmp_path):
    """Every text to translate of the template (the summary, the description and the
    screenshots' captions) gets the words of each language of po/LINGUAS that translates it,
    once; Polish translates all of them (its summary checked word for word). No language
    beside those, and the release notes stay in English."""
    out = catalogs.compose_metainfo(tmp_path / "metainfo.xml")
    root = ET.parse(out).getroot()
    template = ET.parse(catalogs.METAINFO_TEMPLATE).getroot()
    notes = {id(e) for releases in template.iter("releases") for e in releases.iter()}
    to_translate = [e for e in template.iter()
                    if e.tag in ("summary", "p", "li", "caption") and id(e) not in notes]
    languages = catalogs.languages()
    assert to_translate and "pl" in languages
    translated: dict[str, list] = {}
    for e in root.iter():
        if e.get(XML_LANG):
            translated.setdefault(e.get(XML_LANG), []).append(e)
    assert set(translated) <= set(languages)
    for language, elements in translated.items():
        assert {e.tag for e in elements} <= {"summary", "p", "li", "caption"}, language
        assert len(elements) <= len(to_translate), language
    polish = translated["pl"]
    assert len(polish) == len(to_translate)
    assert {e.tag for e in polish} == {"summary", "p", "li", "caption"}
    assert root.find("summary[@{http://www.w3.org/XML/1998/namespace}lang='pl']").text == (
        "Zarządzaj urządzeniem Garmin na Linuksie")
    assert not root.findall(".//releases//*[@{http://www.w3.org/XML/1998/namespace}lang]")


@pytest.mark.skipif(not (shutil.which("appstreamcli") and shutil.which("msgfmt")),
                    reason="appstreamcli or msgfmt not installed")
def test_the_built_metainfo_is_valid(tmp_path):
    out = catalogs.compose_metainfo(tmp_path / "io.github.tomaszbojanowski.OpenExpress.metainfo.xml")
    done = subprocess.run(["appstreamcli", "validate", "--no-net", "--explain", str(out)],
                          capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stdout + done.stderr


def test_install_desktop_builds_the_catalogs_from_source_first(monkeypatch, capsys):
    from openexpress import background, cli, desktop

    order = []
    monkeypatch.setattr(background, "in_flatpak", lambda: False)
    monkeypatch.setattr(desktop, "flatpak_installed", lambda: False)
    monkeypatch.setattr(catalogs, "build", lambda *a, **kw: order.append("catalogs") or [])
    # Never the real one: it would write the metainfo into the source tree.
    monkeypatch.setattr(catalogs, "compose_metainfo", lambda *a, **kw: order.append("metainfo"))
    monkeypatch.setattr(desktop, "install",
                        lambda dry_run: (order.append(f"files, dry run {dry_run}"), [])[1])
    assert cli.main(["install-desktop"]) == 0
    assert cli.main(["install-desktop", "--apply"]) == 0
    assert order == ["files, dry run True", "catalogs", "metainfo", "files, dry run False"]


def test_install_desktop_without_gettext_says_so_and_goes_on_in_english(monkeypatch, capsys):
    from openexpress import background, cli, desktop

    def no_gettext(*_a, **_kw):
        raise catalogs.CatalogError("made-up: msgfmt is missing")

    monkeypatch.setattr(background, "in_flatpak", lambda: False)
    monkeypatch.setattr(desktop, "flatpak_installed", lambda: False)
    monkeypatch.setattr(catalogs, "build", no_gettext)
    monkeypatch.setattr(desktop, "install", lambda dry_run: [])
    assert cli.main(["install-desktop", "--apply"]) == 0
    assert "made-up: msgfmt is missing" in capsys.readouterr().err
