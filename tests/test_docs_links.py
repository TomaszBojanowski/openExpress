"""The links between the documents (README.md, CONTRIBUTING.md, docs/*.md) lead somewhere: every
link and picture to a file of the repository names one that is there, and every #anchor a
heading of that file, as GitHub makes the anchor of a heading."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCUMENTS = [ROOT / "README.md", ROOT / "CONTRIBUTING.md", *sorted((ROOT / "docs").glob("*.md"))]
LINK = re.compile(r"\]\(([^)\s]+)\)|(?:src|srcset)=\"([^\"]+)\"")


def anchors(path: Path) -> set[str]:
    """The anchors GitHub gives the headings of a document (outside code blocks)."""
    found, in_code = set(), False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("```"):
            in_code = not in_code
        match = re.match(r"#+ (.*)", line)
        if match and not in_code:
            text = re.sub(r"[^\w\- ]", "", match.group(1).strip().lower())
            found.add(text.replace(" ", "-"))
    return found


def links(path: Path) -> list[str]:
    text = re.sub(r"```.*?```", "", path.read_text(encoding="utf-8"), flags=re.DOTALL)
    return [a or b for a, b in LINK.findall(text)]


def test_the_documents_are_there():
    assert len(DOCUMENTS) >= 8 and all(p.exists() for p in DOCUMENTS)


def test_every_link_between_the_documents_leads_somewhere():
    broken = []
    for doc in DOCUMENTS:
        for target in links(doc):
            if re.match(r"[a-z]+:", target):
                continue  # an address on the web
            file, _, anchor = target.partition("#")
            path = (doc.parent / file).resolve() if file else doc
            if not path.exists():
                broken.append(f"{doc.relative_to(ROOT)}: {target} (no such file)")
            elif anchor and path.suffix == ".md" and anchor not in anchors(path):
                broken.append(f"{doc.relative_to(ROOT)}: {target} (no such heading)")
    assert broken == []
