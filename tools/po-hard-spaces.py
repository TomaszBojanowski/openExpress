#!/usr/bin/env python3
"""Polish typography in a .po file: a hard space (U+00A0) instead of a plain one after a
one-letter word (w, z, i, o, u, a and their capitals), so that the word never stays at the
end of a line when a text of the window wraps. tests/test_i18n.py checks po/pl.po for it.

Usage, after new translations (e.g. tools/po-fill.py):
    tools/po-hard-spaces.py po/pl.po            preview: what would change, nothing written
    tools/po-hard-spaces.py po/pl.po --apply    write it
    msgfmt --check -o openexpress/locale/pl/LC_MESSAGES/openexpress.mo po/pl.po

Only msgstr (also msgstr[N]) is changed, not in the header nor in obsolete entries (#~), and
the layout of the lines stays.
"""
import re
import sys
from pathlib import Path

NBSP = "\u00a0"
KEY = re.compile(r'^(msgctxt|msgid|msgid_plural|msgstr(?:\[\d+\])?) "(.*)"$')
CONTINUED = re.compile(r'^"(.*)"$')
# A letter that is not a word is left alone: one after a letter or a digit, a format code
# (%i), a tag (<a href…>), an option (-a), a placeholder ({a}), a path (/a) or an escape (\a).
ONE_LETTER = re.compile(r"(?<![\w%<\-/{\\])[wziouaWZIOUA] ")
ESCAPES = {"n": "\n", "t": "\t", '"': '"', "\\": "\\"}


def decoded(raw: str):
    """(character, index in `raw`) of a quoted chunk of a .po file, its escapes undone."""
    out, i = [], 0
    while i < len(raw):
        if raw[i] == "\\" and i + 1 < len(raw):
            out.append((ESCAPES.get(raw[i + 1], raw[i + 1]), i))
            i += 2
        else:
            out.append((raw[i], i))
            i += 1
    return out


def fields(lines):
    """For each msgstr of each entry but the first (the header; obsolete entries are
    comments): its chunks, as (line number, start of the quoted text in the line, raw text)."""
    entries, current, in_entry, skip = 0, None, False, False
    for n, line in enumerate(lines):
        if not line.strip():
            if current is not None:
                yield current
            current, in_entry = None, False
            continue
        if line.startswith("#"):
            continue  # comments, also obsolete entries (#~)
        m = KEY.match(line)
        if m:
            if current is not None:
                yield current
            current = None
            if not in_entry:
                in_entry, entries = True, entries + 1
                skip = entries == 1  # the header
                if skip:
                    assert m.group(1) == "msgid" and m.group(2) == "", "no header first"
            if m.group(1).startswith("msgstr") and not skip:
                current = (m.group(1), [(n, line.index('"') + 1, m.group(2))])
            continue
        c = CONTINUED.match(line)
        if c and current is not None:
            current[1].append((n, 1, c.group(1)))
    if current is not None:
        yield current


def main():
    path = Path(sys.argv[1])
    apply = "--apply" in sys.argv[2:]
    lines = path.read_text(encoding="utf-8").split("\n")
    changed_texts = replaced = 0
    for key, chunks in fields(lines):
        chars = [(ch, chunk, raw_i) for chunk, (_n, _start, raw) in enumerate(chunks)
                 for ch, raw_i in decoded(raw)]
        text = "".join(ch for ch, _c, _i in chars)
        # One after another ("i w ") as well: each match takes the letter and its space.
        spaces = [m.end() - 1 for m in ONE_LETTER.finditer(text)]
        if not spaces:
            continue
        changed_texts += 1
        replaced += len(spaces)
        shown = list(text)
        for pos in spaces:
            shown[pos] = "⍽"
            _ch, chunk, raw_i = chars[pos]
            n, start, _raw = chunks[chunk]
            line = lines[n]
            at = start + raw_i
            assert line[at] == " ", (n, line)
            lines[n] = line[:at] + NBSP + line[at + 1:]
        print(f"{key} (line {chunks[0][0] + 1}): {''.join(shown)!r}")
    done = "" if apply else " (preview, nothing written)"
    print(f"\n{changed_texts} texts, {replaced} spaces{done}")
    if apply:
        path.write_text("\n".join(lines), encoding="utf-8")


main()
