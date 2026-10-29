#!/usr/bin/env python3
"""Fill translations in a .po file from a JSON dictionary {key: msgstr | [plural forms]}.

Usage: tools/po-fill.py po/pl.po translations.json

The key is the msgid; for an entry with a context (msgctxt) "context|msgid", and "|msgid"
for the entry without one. A bare msgid that also has entries with a context is ambiguous:
nothing is filled for it and it is reported (exit code 1), so that one key never overwrites
two translations. Filled entries lose the fuzzy flag and keep their other flags.
"""
import json
import re
import sys
from pathlib import Path


def unquote(lines):
    text = "".join(re.findall(r'"(.*)"', "\n".join(lines)))
    return text.encode().decode("unicode_escape").encode("latin-1").decode("utf-8")


def quote(text):
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def _entry(lines):
    """(context or None, msgid, index of msgid, of msgid_plural or None, of msgstr) of one
    block of a .po file, or None for a block without an entry."""
    try:
        a = next(k for k, line in enumerate(lines) if line.startswith("msgid "))
        p = next((k for k, line in enumerate(lines) if line.startswith("msgid_plural")), None)
        z = next(k for k, line in enumerate(lines) if line.startswith("msgstr"))
    except StopIteration:
        return None
    c = next((k for k, line in enumerate(lines[:a]) if line.startswith("msgctxt ")), None)
    context = unquote(lines[c:a]) if c is not None else None
    return context, unquote(lines[a:(p if p is not None else z)]), a, p, z


def _without_fuzzy(line):
    """A line of the entry with "fuzzy" taken out of its flags ("#, fuzzy,
    python-brace-format" -> "#, python-brace-format"); None for a line of flags that had
    nothing else. Other lines as they are."""
    if not line.startswith("#,"):
        return line
    flags = [f.strip() for f in line[2:].split(",") if f.strip() and f.strip() != "fuzzy"]
    return "#, " + ", ".join(flags) if flags else None


def _target(key):
    """(context or None, msgid, whether the key says which) of a key of the table."""
    if "|" in key:
        context, msgid = key.split("|", 1)
        return context or None, msgid, True
    return None, key, False


def main(po_path, json_path):
    table = json.loads(Path(json_path).read_text(encoding="utf-8"))
    po = Path(po_path)
    blocks = po.read_text(encoding="utf-8").split("\n\n")
    entries = [_entry(block.split("\n")) for block in blocks]
    with_context = {e[1] for e in entries if e is not None and e[0] is not None}
    wanted, ambiguous = {}, []
    for key in table:
        context, msgid, said = _target(key)
        if not said and msgid in with_context:
            ambiguous.append(key)
            continue
        wanted[(context, msgid)] = key
    done = set()
    for i, block in enumerate(blocks):
        lines = block.split("\n")
        entry = entries[i]
        if entry is None or (entry[0], entry[1]) not in wanted:
            continue
        context, msgid, a, _plural, z = entry
        key = wanted[(context, msgid)]
        head = [_without_fuzzy(line) for line in lines[:a] if not line.startswith("#| ")]
        head = [line for line in head if line is not None]
        value = table[key]
        if isinstance(value, list):
            body = [f"msgstr[{n}] " + quote(v) for n, v in enumerate(value)]
        else:
            body = ["msgstr " + quote(value)]
        blocks[i] = "\n".join(head + lines[a:z] + body)
        done.add(key)
    po.write_text("\n\n".join(blocks), encoding="utf-8")
    if ambiguous:
        print("ambiguous, the msgid has entries with a context too (use \"context|msgid\", "
              "or \"|msgid\" for the one without):", *sorted(ambiguous), sep="\n  ")
    missing = set(table) - done - set(ambiguous)
    if missing:
        print("not found:", *sorted(missing), sep="\n  ")
    return 1 if missing or ambiguous else 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:3]))
