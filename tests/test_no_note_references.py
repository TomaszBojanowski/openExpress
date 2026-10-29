"""The repository says why, not when nor where in private notes. Comments, docstrings and the
pattern files name no dates, no labels of a plan or of a decision (D7, Q1, T6b), no issues
(#19), no commits, no steps of a plan or a review, no versions of openExpress (0.6.6) and no
story of the work ("review of", "seen live", "as before"), and no function is named so; no
string names the analyses, audits, decisions or private notes, nor a "live test". A fact about
a device, a server or another program stays, with that program's own version ("libadwaita
1.9", "firmware 23.39"). Dates and the like in other strings are data (the window shows
dates as 21.10.2026), so only comments and docstrings are looked at for those. No file has an
exception."""

import ast
import io
import json
import re
import tokenize
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATE = re.compile(r"\b\d{1,2}\.\d{1,2}\.20\d\d\b")
NOTES = re.compile("|".join((
    r"\b[Aa]nalys[ie]s\s+\d+", r"\baudit\b", r"\bA[1-9]\b", r"\blive test", r"§\s?\d+",
    r"\b[Dd]ecision \d+", r"user's consent", r"\bPLAN(?:-archiwum)?\.md\b", r"\bAGENTS\.md\b",
    r"openexpress-private")))
_MONTHS = ("January|February|March|April|May|June|July|August|September|October|November|"
           "December")
# Looked for in comments, docstrings and the lines of the pattern files only. A label is a
# capital and a number, maybe with a part after a dot (T5.1); no S (Amazon S3, the Approach
# S70) and no X (X11); not a file name (E2.img), not a code of ruff (E402), not the F5 key. A
# version of openExpress has three parts, so "0.5 s" is none, nor is the version in the name
# of a pattern file (format-0.6.20-…) or a part of 127.0.0.1. A commit is 7 hexadecimal
# digits, with a letter and a digit among them.
WHEN = (
    DATE,
    re.compile(rf"\b(?:\d{{1,2}} )?(?:{_MONTHS}) (?:\d{{1,2}}, )?20\d\d\b"),
    re.compile(r"(?<![\w.-])(?!F5\b)[BCDEFKNPQTU][1-9]\d?[a-z]?(?:\.\d+)?(?!\w|\.\w)"),
    re.compile(r"(?<![\w&/])#\d{1,3}\b"),
    re.compile(r"(?<![\w.-])(?=[0-9a-f]{0,6}[a-f])(?=[0-9a-f]{0,6}\d)[0-9a-f]{7}(?![\w-])"),
    re.compile(r"\b(?:[Ss]teps?|[Ss]tages?|[Pp]oints?|[Pp]ackages?|[Pp]arts?|[Aa]reas?) "
               r"\d+[a-z]?\b"),
    re.compile(r"\b(?:review of|seen live|reporter|moved out of|split out of|the split)\b"),
    re.compile(r"\bas before\b(?!\s+(?:it|its|the|an?|this|that|these|those)\b"
               r"(?!\s+(?:change|fix|split)\b))"),
)
# "As before" points at an earlier version, unless a moment of the events follows ("as before
# the eject"; not "the change" or "the fix"). A function, and a test above all, is named after
# what it does or checks, not after what an earlier version did.
NAME = re.compile(r"(?:^|_)as_before(?:$|_(?!(?:it|its|the|an?|this|that)_))")
VERSION = re.compile(r"(?<![\w.-])v?0\.\d{1,2}\.\d{1,2}(?![\w.])")


def _docstrings(tree) -> set[int]:
    """The ids of the string constants that are docstrings."""
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                found.add(id(body[0].value))
    return found


def _said(line: int, text: str, patterns) -> list[str]:
    """The words of `text` that `patterns` find, in their order in it: "line: words"."""
    found = sorted((m.start(), m.group(0)) for pattern in patterns for m in pattern.finditer(text))
    return [f"{line}: {words}" for _start, words in found]


def references(path: Path) -> list[str]:
    """What `path` names that it should not: "line: words"."""
    text = path.read_text(encoding="utf-8")
    if path.suffix != ".py":
        # The names of the pattern files carry the version that wrote them: data.
        return [ref for n, line in enumerate(text.splitlines(), 1)
                for ref in _said(n, line, (NOTES, *WHEN))]
    said = []
    for tok in tokenize.generate_tokens(io.StringIO(text).readline):
        if tok.type == tokenize.COMMENT:
            said += _said(tok.start[0], tok.string, (NOTES, *WHEN, VERSION))
    tree = ast.parse(text)
    docs = _docstrings(tree)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and NAME.search(node.name):
            said.append(f"{node.lineno}: {node.name}")
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            patterns = (NOTES, *WHEN, VERSION) if id(node) in docs else (NOTES,)
            said += _said(node.lineno, node.value, patterns)
    return said


def checked_files() -> list[Path]:
    files = [p for folder in ("openexpress", "tests", "tools")
             for p in (ROOT / folder).rglob("*.py") if "__pycache__" not in p.parts]
    files += sorted((ROOT / "tests" / "data" / "last_sync").glob("*"))
    return sorted(files)


def _found() -> dict[str, list[str]]:
    said = {}
    for path in checked_files():
        if path == Path(__file__).resolve():
            continue  # it names the words it looks for
        refs = references(path)
        if refs:
            said[str(path.relative_to(ROOT))] = refs
    return said


def test_no_dates_or_notes_in_what_the_repository_says():
    found = _found()
    assert found == {}, "say why, not when nor where in the notes: " + json.dumps(found, indent=1)


def _words(tmp_path, text: str, name: str = "made_up.py") -> list[str]:
    made_up = tmp_path / name
    made_up.write_text(text, encoding="utf-8")
    return [r.split(": ", 1)[1] for r in references(made_up)]


def test_dates_are_looked_for_in_comments_and_docstrings_only(tmp_path):
    assert _words(tmp_path, '"""Seen on 21.10.2026."""\n'
                            '# analysis 20 after the audit (A8, §10)\n'
                            'SHOWN = "Last on 21.10.2026"\n'
                            'WORDS = "the live test"\n') == [
        "analysis 20", "audit", "A8", "§10", "21.10.2026", "live test"]


def test_what_tells_when_in_a_comment_or_a_docstring(tmp_path):
    assert _words(tmp_path, '"""As on 18 October 2026 (#19, Q1): T6b of D7, T5.1."""\n'
                            '# since 0.6.6, after 1c55104 (step 0, point 8 of package 3)\n'
                            '# the review of the window; seen live; the reporter\'s computer\n'
                            '# code moved out of app.py in the split; v0.7.9\n'
                            'SHOWN = "#19 Q1 0.6.6 1c55104 step 0"\n') == [
        "0.6.6", "1c55104", "step 0", "point 8", "package 3",
        "review of", "seen live", "reporter",
        "moved out of", "the split", "v0.7.9",
        "18 October 2026", "#19", "Q1", "T6b", "D7", "T5.1"]


def test_an_analysis_named_across_a_line_break(tmp_path):
    assert _words(tmp_path, '"""The rule of the analysis\n    14: one source."""\n') == [
        "analysis\n    14"]


def test_as_before_without_a_moment_after_it(tmp_path):
    """"As before" is history; before a moment it is the order of the events. A function
    named with it is history too."""
    assert _words(tmp_path, '# the tile keeps its chip, as before.\n'
                            '# no error, no progress: as before the change\n'
                            '# the list is read again, just as before the eject\n'
                            '# a detection that ends with no device, as before it\n'
                            '# written before the cable goes out\n'
                            'def test_stderr_is_as_before(): pass\n'
                            'def test_cli_installs_as_before_when_unit_info_fails(): pass\n'
                            'def _space_as_before(): pass\n'
                            'def test_the_page_is_read_as_before_the_reload(): pass\n') == [
        "as before", "as before", "test_stderr_is_as_before",
        "test_cli_installs_as_before_when_unit_info_fails", "_space_as_before"]


def test_what_is_not_history(tmp_path):
    """Facts and data that look like such words: a bucket on S3, the Approach S70, the F5 key,
    X11, a map file E2.img, a code of ruff, 127.0.0.1, a waiting time, a made-up serial in
    hexadecimal, a format of a date, a colour, the versions of other programs, a pattern
    file's name, steps in words."""
    assert _words(tmp_path, '# pictures in a bucket on S3; the Approach S70; Refresh (F5)\n'
                            '# without an X11 socket; its new file E2.img  # noqa: E402\n'
                            '# the server at 127.0.0.1; 0.5 s; b303e6a8 in hexadecimal\n'
                            '# e.g. 2026-10-14 23:41; the colour #1e1e1e\n'
                            '# libadwaita 1.9, Flatpak 1.16, firmware 23.39, Python 3.14,\n'
                            '# Garmin Express 7.29.1.0, gettext 0.26\n'
                            '# format-0.6.20-details-as-data.json; step by step, the next '
                            'step\n') == []
    assert _words(tmp_path, '"synthetic": "made up: no real sync has made it yet",\n'
                            '"file": "format-0.6.21-received-files.json"\n',
                  name="format-0.7.5-made-up.json") == []
