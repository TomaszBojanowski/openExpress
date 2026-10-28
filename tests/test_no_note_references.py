"""The repository says why, not when nor where in private notes: no dates, no numbers of
analyses, audits, steps or decisions, no "live test", no "§N" and no names of the private notes
in the comments, docstrings and strings of the code, the tests and the tools, nor in the pattern
files. Dates in other strings are data (the window shows dates as 21.10.2026), so only
comments and docstrings are looked at for those.

Older comments still have such words: their number per file is in
tests/data/older_note_references.json, and it may only go down (when a file is cleaned, its
number there is lowered or the file taken out)."""

import ast
import io
import json
import re
import tokenize
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OLDER = ROOT / "tests" / "data" / "older_note_references.json"
DATE = re.compile(r"\b\d{1,2}\.\d{1,2}\.20\d\d\b")
NOTES = re.compile("|".join((
    r"\b[Aa]nalys[ie]s \d+", r"\baudit\b", r"\bA[1-9]\b", r"\blive test", r"§\s?\d+",
    r"\b[Dd]ecision \d+", r"user's consent", r"\bPLAN(?:-archiwum)?\.md\b", r"\bAGENTS\.md\b",
    r"openexpress-private")))


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


def references(path: Path) -> list[str]:
    """What `path` names that it should not: "line: words"."""
    text = path.read_text(encoding="utf-8")
    if path.suffix != ".py":
        return [f"{n}: {m.group(0)}" for n, line in enumerate(text.splitlines(), 1)
                for pattern in (DATE, NOTES) for m in pattern.finditer(line)]
    said = []
    for tok in tokenize.generate_tokens(io.StringIO(text).readline):
        if tok.type == tokenize.COMMENT:
            said += [f"{tok.start[0]}: {m.group(0)}" for pattern in (DATE, NOTES)
                     for m in pattern.finditer(tok.string)]
    tree = ast.parse(text)
    docs = _docstrings(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            patterns = (DATE, NOTES) if id(node) in docs else (NOTES,)
            said += [f"{node.lineno}: {m.group(0)}" for pattern in patterns
                     for m in pattern.finditer(node.value)]
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
    older = json.loads(OLDER.read_text(encoding="utf-8"))
    new = {name: refs for name, refs in _found().items() if len(refs) > older.get(name, 0)}
    assert new == {}, "say why, not when nor where in the notes: " + json.dumps(new, indent=1)


def test_the_older_ones_only_go_down():
    """A file cleaned of some of them: its number in the list is lowered with it."""
    found = _found()
    older = json.loads(OLDER.read_text(encoding="utf-8"))
    lower = {name: (count, len(found.get(name, ()))) for name, count in older.items()
             if len(found.get(name, ())) < count}
    assert lower == {}, f"lower these numbers in {OLDER.name} (listed, found): {lower}"


def test_dates_are_looked_for_in_comments_and_docstrings_only(tmp_path):
    made_up = tmp_path / "made_up.py"
    made_up.write_text('"""Seen on 21.10.2026."""\n'
                       '# analysis 20, step 4 after the audit (A8, §10)\n'
                       'SHOWN = "Last on 21.10.2026"\n'
                       'WORDS = "the live test"\n', encoding="utf-8")
    assert [r.split(": ", 1)[1] for r in references(made_up)] == [
        "analysis 20", "audit", "A8", "§10", "21.10.2026", "live test"]
