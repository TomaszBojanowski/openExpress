#!/usr/bin/env python3
"""Typography in a .po file, by the rules of its language, so that a text of the window that
wraps never leaves a short word or a mark alone at the end or the start of a line:
- Polish, Czech and Slovak: a hard space (U+00A0) instead of a plain one after a one-letter
  word (Polish w, z, i, o, u, a; Czech and Slovak k, s, v, z, o, u, a, i; and their capitals);
- Polish also: a hard space before an en dash (–), which would otherwise begin a line;
- French: a narrow hard space (U+202F) instead of a plain one before ; : ! ? and inside « ».
The language is the file's name (pl.po), or --lang. A language without rules is left as it
is. In the repository only Polish is changed: every other language is its translators', in
Weblate, and its file here is refused. tests/test_i18n.py checks Polish.

Usage, after new translations (e.g. tools/po-fill.py):
    tools/po-hard-spaces.py po/pl.po            preview: what would change, nothing written
    tools/po-hard-spaces.py po/pl.po --apply    write it
    tools/po-hard-spaces.py draft.po --lang cs  a file named otherwise

Only msgstr (also msgstr[N]) is changed, not in the header nor in obsolete entries (#~), and
the layout of the lines stays.
"""
import re
import sys
from pathlib import Path

NBSP, NARROW_NBSP = "\u00a0", "\u202f"
KEY = re.compile(r'^(msgctxt|msgid|msgid_plural|msgstr(?:\[\d+\])?) "(.*)"$')
CONTINUED = re.compile(r'^"(.*)"$')
# A letter that is not a word is left alone: one after a letter or a digit, a format code
# (%i), a tag (<a href…>), an option (-a), a placeholder ({a}), a path (/a) or an escape (\a).
ONE_LETTER_WORDS = {"pl": "wziouaWZIOUA", "cs": "ksvzouaiKSVZOUAI", "sk": "ksvzouaiKSVZOUAI"}


def one_letter_words(letters: str):
    pattern = re.compile(rf"(?<![\w%<\-/{{\\])[{letters}] ")

    def spaces(text: str) -> list[tuple[int, str]]:
        # One after another ("i w ") as well: each match takes the letter and its space.
        return [(m.end() - 1, NBSP) for m in pattern.finditer(text)]
    return spaces


def french(text: str) -> list[tuple[int, str]]:
    """The plain spaces before ; : ! ? and », and after «."""
    return [(i, NARROW_NBSP) for i, ch in enumerate(text) if ch == " " and (
        (i + 1 < len(text) and text[i + 1] in ";:!?»") or (i > 0 and text[i - 1] == "«"))]


def before_a_dash(text: str) -> list[tuple[int, str]]:
    """The plain spaces before an en dash (a hyphen is no dash)."""
    return [(i, NBSP) for i, ch in enumerate(text) if ch == " " and text[i + 1:i + 2] == "–"]


def both(*rules):
    return lambda text: [place for rule in rules for place in rule(text)]


RULES = {**{lang: one_letter_words(letters) for lang, letters in ONE_LETTER_WORDS.items()},
         "fr": french}
RULES["pl"] = both(RULES["pl"], before_a_dash)
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


PO = Path(__file__).resolve().parent.parent / "po"


def main():
    path = Path(sys.argv[1])
    if path.resolve().parent == PO.resolve() and path.name != "pl.po":
        print(f"{path.name}: the translators' language, changed only in Weblate: not touched")
        sys.exit(1)
    apply = "--apply" in sys.argv[2:]
    lang = sys.argv[sys.argv.index("--lang") + 1] if "--lang" in sys.argv else path.stem
    rule = RULES.get(lang)
    if rule is None:
        print(f"{lang}: no rules for hard spaces, nothing to do")
        return
    lines = path.read_text(encoding="utf-8").split("\n")
    changed_texts = replaced = 0
    for key, chunks in fields(lines):
        chars = [(ch, chunk, raw_i) for chunk, (_n, _start, raw) in enumerate(chunks)
                 for ch, raw_i in decoded(raw)]
        text = "".join(ch for ch, _c, _i in chars)
        spaces = rule(text)
        if not spaces:
            continue
        changed_texts += 1
        replaced += len(spaces)
        shown = list(text)
        for pos, hard in spaces:
            shown[pos] = "⍽"
            _ch, chunk, raw_i = chars[pos]
            n, start, _raw = chunks[chunk]
            line = lines[n]
            at = start + raw_i
            assert line[at] == " ", (n, line)
            lines[n] = line[:at] + hard + line[at + 1:]
        print(f"{key} (line {chunks[0][0] + 1}): {''.join(shown)!r}")
    done = "" if apply else " (preview, nothing written)"
    print(f"\n{changed_texts} texts, {replaced} spaces{done}")
    if apply:
        path.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
