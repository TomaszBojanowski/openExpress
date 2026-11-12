"""The repository says why, not when nor where in private notes. Comments, docstrings and the
pattern files name no dates, no labels of a plan or of a decision (D7, Q1, T6b), no issues
(#19), no commits, no steps of a plan or a review, no versions of openExpress (0.6.6) and no
story of the work ("review of", "seen live", "as before"), and no function is named so; no
string names the analyses, audits, decisions or private notes, nor a "live test". A fact about
a device, a server or another program stays, with that program's own version ("libadwaita
1.9", "firmware 23.39"). Dates and the like in other strings are data (the window shows
dates as 21.10.2026), so only comments and docstrings are looked at for those. A comment is
read with the lines of comment that follow it, so that what a line break cuts in two is found;
a comment inside a string (of CSS, of markup) is a comment too. A section of the tests is
headed by its subject, not by the number of a point of a review. No file has an exception."""

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
# A comment inside a string: of CSS (or C) and of markup.
COMMENT_IN_STRING = re.compile(r"/\*.*?\*/|<!--.*?-->", re.DOTALL)
# A heading of a section numbered after the points of a review: "# --- 3/4/5. downloads".
NUMBERED_HEADING = re.compile(r"^#\s*-+\s*\d+(?:/\d+)*[.:](?=\s)")


# The design documents say what is decided and why, not the story of the work: no conversation
# it came from, no "a yes" that let a step begin, nothing someone read, chose or compared, no
# clock of a log, no commits and no dates. Their blocks of code are examples of what a command
# prints: data. "The user" saving a file or told something is the program's user, and stays.
DESIGN = ROOT / "docs" / "design"
# A date of the work written as 2026-11-01 (not a file's name); a quoted example is data.
ISO_DATE = re.compile(r"(?<![\w-])20\d\d-\d\d-\d\d(?![\w-])")
QUOTED = re.compile(r'"[^"]*"|`[^`]*`')
STORY = re.compile("|".join((
    r"\b(?:in )?the chat\b", r"\ba yes\b",
    (r"\bthe user(?:'s)? (?:rule|read|reads|compares|chose|chooses|changed|accepted|asked"
     r"|decided|corrects)\b"),
    r"\b\d{2}:\d{2}:\d{2}\b")))
# An implemented design says what the program does: the program before it is "Before this
# change", never "today", and the steps of the work and who took them belong to the notes.
IMPLEMENTED = re.compile(r"^Status: implemented\b", re.MULTILINE)
WORK = re.compile(r"\btoday\b|\bmaintainer\b|^#{2,4} .*\bSteps\b")


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


def _said_by_line(line: int, text: str, patterns) -> list[str]:
    """As _said, each find with the line it starts on, `text` starting on `line`."""
    found = sorted((m.start(), m.group(0)) for pattern in patterns for m in pattern.finditer(text))
    return [f"{line + text.count(chr(10), 0, start)}: {words}" for start, words in found]


def _comment_blocks(text: str) -> list[tuple[int, str]]:
    """The comments of a Python source, those on lines that follow each other as one: (the
    line of the first, their texts without their first "#", a line break between them)."""
    blocks: list[tuple[int, list[str]]] = []
    last = None
    for tok in tokenize.generate_tokens(io.StringIO(text).readline):
        if tok.type != tokenize.COMMENT:
            continue
        row = tok.start[0]
        if last is not None and row == last + 1:
            blocks[-1][1].append(tok.string[1:])
        else:
            blocks.append((row, [tok.string[1:]]))
        last = row
    return [(row, "\n".join(lines)) for row, lines in blocks]


def references(path: Path) -> list[str]:
    """What `path` names that it should not: "line: words"."""
    text = path.read_text(encoding="utf-8")
    if path.suffix != ".py":
        # The names of the pattern files carry the version that wrote them: data.
        return [ref for n, line in enumerate(text.splitlines(), 1)
                for ref in _said(n, line, (NOTES, *WHEN))]
    said = []
    for row, comment in _comment_blocks(text):
        said += _said_by_line(row, comment, (NOTES, *WHEN, VERSION))
    for tok in tokenize.generate_tokens(io.StringIO(text).readline):
        if tok.type == tokenize.COMMENT:
            said += _said(tok.start[0], tok.string, (NUMBERED_HEADING,))
    tree = ast.parse(text)
    docs = _docstrings(tree)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and NAME.search(node.name):
            said.append(f"{node.lineno}: {node.name}")
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) in docs:
                said += _said(node.lineno, node.value, (NOTES, *WHEN, VERSION))
                continue
            said += _said(node.lineno, node.value, (NOTES,))
            for m in COMMENT_IN_STRING.finditer(node.value):
                said += _said_by_line(node.lineno + node.value.count("\n", 0, m.start()),
                                      m.group(0), (*WHEN, VERSION))
    return said


def design_references(path: Path) -> list[str]:
    """What a design document tells of the work, outside its blocks of code: "line: words"."""
    said, in_code = [], False
    text = path.read_text(encoding="utf-8")
    story = (STORY, DATE, WHEN[1], WHEN[4]) + ((WORK,) if IMPLEMENTED.search(text) else ())
    for n, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("```"):
            in_code = not in_code
        elif not in_code:
            said += _said(n, line, story)
            said += _said(n, QUOTED.sub("", line), (ISO_DATE,))  # an example's date is data
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


def test_a_note_named_across_lines_of_comment(tmp_path):
    """Comments that follow each other are read as one, the "#" of each line left out."""
    assert _words(tmp_path, 'X = 1  # its serial (analysis\n'
                            '# 18) and its volume\n'
                            '# seen on 26.10.\n'
                            '# 2026 at night\n') == ["analysis\n 18"]
    assert _words(tmp_path, '# its serial (analysis\n'
                            '    # 18): never shown\n') == ["analysis\n 18"]


def test_a_date_in_a_comment_inside_a_string(tmp_path):
    """A comment of CSS or of markup in a string says no date; the rest of the string is
    data."""
    assert _words(tmp_path, 'CSS = """\n.row { color: red; }\n'
                            '/* fainter than the line under it (26.10.2026). */\n'
                            '.tile { color: #1e1e1e; } /* shown 21.10.2026 */\n"""\n'
                            'SVG = "<!-- made after D7, on 1 November 2026 -->"\n'
                            'SHOWN = "Last on 21.10.2026"\n') == [
        "26.10.2026", "21.10.2026", "D7", "1 November 2026"]


def test_a_section_is_headed_by_its_subject_not_by_a_number(tmp_path):
    assert _words(tmp_path, '# --- 1. network errors --------\n'
                            '# --- 3/4/5. downloads ---\n'
                            '# --- 10: inter-process lock ---\n'
                            '# --- downloads over HTTPS only ---\n'
                            '# 2 files at most; 3. the last\n') == [
        "# --- 1.", "# --- 3/4/5.", "# --- 10:"]


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


def test_the_design_documents_tell_no_story_of_the_work():
    found = {path.name: refs for path in sorted(DESIGN.glob("*.md"))
             if (refs := design_references(path))}
    assert found == {}, "a design says what and why, not how the work went: " + json.dumps(
        found, indent=1)


def test_an_implemented_design_tells_no_work_still_to_do(tmp_path):
    """Not "today", the maintainer or a section of steps once the design is implemented; a design
    still proposed may say them."""
    words = ("The page today; after the\nmaintainer's consent.\n### D15. Steps\n"
             "Each step is checked on its own.\n")
    implemented, proposed = tmp_path / "implemented.md", tmp_path / "proposed.md"
    implemented.write_text("Status: implemented.\n\n" + words, encoding="utf-8")
    proposed.write_text("Status: proposed.\n\n" + words, encoding="utf-8")
    assert [r.split(": ", 1)[1] for r in design_references(implemented)] == [
        "today", "maintainer", "### D15. Steps"]
    assert design_references(proposed) == []


def test_what_a_design_document_does_not_tell(tmp_path):
    """The story of the work, but not the program's user, its consent, nor an example."""
    made_up = tmp_path / "made-up.md"
    made_up.write_text("The mockup shown in the chat waits for a yes; as the user read them,\n"
                       "the user's rule. At 00:41:25 on 2 November 2026 (a9ce7b1), 21.10.2026.\n"
                       "Checked on a device on 2026-11-01; the file 2026-10-30-07-00-00.fit;\n"
                       'a toast "Saved as 2026-10-30 07.00 Running.gpx".\n'
                       "The user saves the GPX where the user is told; the User-Agent;\n"
                       "tiles only after the yes of D3; the colour #e5420e.\n"
                       "```\nCreated: 2026-10-30 07:00:00\n```\n", encoding="utf-8")
    assert [r.split(": ", 1)[1] for r in design_references(made_up)] == [
        "in the chat", "a yes", "the user read", "the user's rule", "00:41:25",
        "2 November 2026", "a9ce7b1", "21.10.2026", "2026-11-01"]
