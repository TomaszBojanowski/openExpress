#!/usr/bin/env python3
"""Fill translations in a .po file from a JSON dictionary {msgid: msgstr | [plural forms]}.

Used by maintainers to apply batches of translations; clears the fuzzy flag of filled
entries. Usage: tools/po-fill.py po/pl.po translations.json
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


def main(po_path, json_path):
    table = json.loads(Path(json_path).read_text(encoding="utf-8"))
    po = Path(po_path)
    blocks = po.read_text(encoding="utf-8").split("\n\n")
    done = set()
    for i, block in enumerate(blocks):
        lines = block.split("\n")
        try:
            a = next(k for k, l in enumerate(lines) if l.startswith("msgid "))
            p = next((k for k, l in enumerate(lines) if l.startswith("msgid_plural")), None)
            z = next(k for k, l in enumerate(lines) if l.startswith("msgstr"))
        except StopIteration:
            continue
        msgid = unquote(lines[a:(p if p is not None else z)])
        if msgid not in table:
            continue
        head = [l.replace("#, fuzzy, ", "#, ") for l in lines[:a]
                if not l.startswith("#, fuzzy") and not l.startswith("#| ")]
        value = table[msgid]
        if isinstance(value, list):
            body = [f"msgstr[{n}] " + quote(v) for n, v in enumerate(value)]
        else:
            body = ["msgstr " + quote(value)]
        blocks[i] = "\n".join(head + lines[a:z] + body)
        done.add(msgid)
    po.write_text("\n\n".join(blocks), encoding="utf-8")
    missing = set(table) - done
    if missing:
        print("not found:", *sorted(missing), sep="\n  ")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:3]))
