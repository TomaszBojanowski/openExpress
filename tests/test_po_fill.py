"""tools/po-fill.py with contexts (msgctxt): "Storage" is both the device page's "Pamięć" and,
with the context "preferences", "Miejsce na dysku" (9dae508). A key without a context must
not overwrite either; "ctxt|msgid" fills the entry with that context, "|msgid" the one
without (review of 24.10.2026). Run on a small made-up .po."""

import json
import subprocess
import sys
from pathlib import Path

TOOL = Path(__file__).resolve().parent.parent / "tools" / "po-fill.py"
PO = """msgid ""
msgstr ""
"Content-Type: text/plain; charset=UTF-8\\n"

#: openexpress/gui/app.py:1
msgid "Storage"
msgstr "Pamięć"

#: openexpress/gui/app.py:2
msgctxt "preferences"
msgid "Storage"
msgstr "Miejsce na dysku"

#: openexpress/gui/app.py:3
msgid "Maps"
msgstr ""
"""


def fill(tmp_path, table):
    po, words = tmp_path / "pl.po", tmp_path / "words.json"
    if not po.exists():
        po.write_text(PO, encoding="utf-8")
    words.write_text(json.dumps(table, ensure_ascii=False), encoding="utf-8")
    run = subprocess.run([sys.executable, "-I", str(TOOL), str(po), str(words)],
                         check=False, capture_output=True, text=True)
    return run, po.read_text(encoding="utf-8")


def test_a_key_without_a_context_overwrites_neither_storage(tmp_path):
    run, text = fill(tmp_path, {"Storage": "Magazyn"})
    assert 'msgstr "Pamięć"' in text and 'msgstr "Miejsce na dysku"' in text
    assert "Magazyn" not in text
    assert run.returncode == 1 and "Storage" in run.stdout and "context" in run.stdout


def test_a_key_with_a_context_fills_only_that_entry(tmp_path):
    run, text = fill(tmp_path, {"preferences|Storage": "Miejsce"})
    assert run.returncode == 0
    assert 'msgctxt "preferences"\nmsgid "Storage"\nmsgstr "Miejsce"' in text
    assert 'msgid "Storage"\nmsgstr "Pamięć"' in text


def test_a_key_with_an_empty_context_fills_only_the_entry_without_one(tmp_path):
    run, text = fill(tmp_path, {"|Storage": "Pamięć urządzenia"})
    assert run.returncode == 0
    assert '#: openexpress/gui/app.py:1\nmsgid "Storage"\nmsgstr "Pamięć urządzenia"' in text
    assert 'msgstr "Miejsce na dysku"' in text


def test_a_msgid_without_contexts_is_filled_as_before(tmp_path):
    run, text = fill(tmp_path, {"Maps": "Mapy"})
    assert run.returncode == 0 and 'msgid "Maps"\nmsgstr "Mapy"' in text
