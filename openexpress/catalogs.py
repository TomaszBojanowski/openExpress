"""The translations as files: the template of the texts (po/openexpress.pot, from the files
po/POTFILES lists), the compiled catalogs (.mo) of the languages of po/LINGUAS and the metainfo
translated from its template. The catalogs and the metainfo are made at the build (the Flatpak's
manifest, install-desktop from source, the tests), never kept in the repository. GNU gettext's
xgettext and msgfmt do the work.

    python3 -m openexpress.catalogs             the catalogs and the metainfo (the build)
    python3 -m openexpress.catalogs --template  po/POTFILES and po/openexpress.pot
    python3 -m openexpress.catalogs --flags FILE…  the flags of po/weblate-flags.txt into them

Weblate takes a text's flags (ignore-same and the like, which quiet a check that has nothing to
say of that text) from the "#," line of a PO file, but GNU gettext's tools drop every flag they
do not know: so the flags of po/weblate-flags.txt are written into the template when it is made
and into po/pl.po after msgmerge (tools/update-translations.sh; Weblate's own msgmerge drops
them from the other languages, where its checks may then warn).

Has: nothing of the window (a module of the build and of install-desktop).
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import sys
from pathlib import Path

from openexpress import __version__, i18n
from openexpress.i18n import _
from openexpress.paths import APP_ID

log = logging.getLogger(__name__)

PACKAGE = Path(__file__).resolve().parent
ROOT = PACKAGE.parent
PO = ROOT / "po"
TEMPLATE = PO / f"{i18n.DOMAIN}.pot"
METAINFO_TEMPLATE = PACKAGE / "data" / f"{APP_ID}.metainfo.xml.in"
METAINFO = METAINFO_TEMPLATE.with_suffix("")  # …metainfo.xml, made from the template
# The calls that mark a text to translate (i18n's docstring); the same keywords for xgettext.
GETTEXT_CALL = re.compile(r"\b(_|N_|NC_|ngettext|pgettext|npgettext)\(")
KEYWORDS = ("--keyword=_", "--keyword=N_", "--keyword=NC_:1c,2", "--keyword=ngettext:1,2",
            "--keyword=pgettext:1c,2", "--keyword=npgettext:1c,2,3")


class CatalogError(Exception):
    """A catalog or the metainfo could not be made (a tool of gettext missing or failing)."""


def languages(po_dir: Path = PO) -> list[str]:
    """The languages of po/LINGUAS, in its order (a "#" starts a comment)."""
    found = []
    for line in (po_dir / "LINGUAS").read_text(encoding="utf-8").splitlines():
        found += line.split("#", 1)[0].split()
    return found


def potfiles() -> list[str]:
    """The files with texts to translate, relative to the repository: the program's Python
    files that call one of the gettext functions, and the metainfo's template."""
    files = sorted(p.relative_to(ROOT).as_posix() for p in PACKAGE.rglob("*.py")
                   if GETTEXT_CALL.search(p.read_text(encoding="utf-8")))
    return [*files, METAINFO_TEMPLATE.relative_to(ROOT).as_posix()]


def _tool(name: str) -> str:
    found = shutil.which(name)
    if found is None:
        raise CatalogError(_("GNU gettext's {tool} is needed to build the translations (the "
                             "gettext package); without it openExpress stays in English.")
                           .format(tool=name))
    return found


def _run(args: list[str]) -> None:
    done = subprocess.run(args, capture_output=True, text=True, cwd=ROOT, check=False)
    if done.returncode != 0:
        raise CatalogError(f"{Path(args[0]).name}: {done.stderr.strip() or done.returncode}")


def write_template(out: Path = TEMPLATE) -> Path:
    """po/POTFILES written anew (with `out` the repository's template), and the template of
    every text they hold, the metainfo's by AppStream's rules (its file name says which)."""
    files = potfiles()
    if out == TEMPLATE:
        (PO / "POTFILES").write_text("\n".join(files) + "\n", encoding="utf-8")
    # The files without their lines: a change of code moves no reference of a text it leaves
    # as it was, so the template and every .po change only where a text does.
    _run([_tool("xgettext"), "--from-code=UTF-8", *KEYWORDS, "--add-comments=TRANSLATORS",
          "--add-location=file", f"--package-name={i18n.DOMAIN}",
          f"--package-version={__version__}", f"--output={out}", *files])
    add_flags(out)
    return out


# --- the flags of Weblate ---------------------------------------------------------------------

WEBLATE_FLAGS = PO / "weblate-flags.txt"
_FLAG = re.compile(r"[a-z][a-z0-9-]*(?::[^,\s]+)?")
_ESCAPES = {"n": "\n", "t": "\t", '"': '"', "\\": "\\"}


def _unescape(text: str) -> str:
    """A PO string's text: \\n, \\t, \\" and \\\\ as the characters they stand for."""
    return re.sub(r"\\(.)", lambda m: _ESCAPES.get(m.group(1), "\\" + m.group(1)), text)


def read_flags(path: Path = WEBLATE_FLAGS) -> dict[tuple[str, str], list[str]]:
    """The flags of each text, from lines "context|text|flag, flag" (the context empty for a
    text without one; the text written as in a PO file: \\n for a new line). Lines starting with
    "#" and empty ones are notes. ValueError for a line that is no such entry."""
    flags: dict[tuple[str, str], list[str]] = {}
    if not path.exists():
        return flags
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip() or line.startswith("#"):
            continue
        context, sep1, rest = line.partition("|")
        text, sep2, names = rest.rpartition("|")
        found = [f.strip() for f in names.split(",") if f.strip()]
        if not (sep1 and sep2 and text and found and all(_FLAG.fullmatch(f) for f in found)):
            raise ValueError(f"{path.name}:{n}: not \"context|text|flags\"")
        key = (context, _unescape(text))
        flags[key] = [*flags.get(key, []), *(f for f in found if f not in flags.get(key, []))]
    return flags


def _entries(lines: list[str]):
    """Each entry of a PO file as (its first line, the line after it, its context, its
    msgid); the header and obsolete entries too, with their msgid."""
    start, n = 0, len(lines)
    while start < n:
        while start < n and not lines[start].strip():
            start += 1
        end = start
        while end < n and lines[end].strip():
            end += 1
        if start == end:
            break
        parts: dict[str, list[str]] = {}
        key = None
        for line in lines[start:end]:
            m = re.match(r"(msgctxt|msgid|msgid_plural|msgstr(?:\[\d+\])?)\s+\"(.*)\"\s*$", line)
            if m:
                key = m.group(1)
                parts[key] = [m.group(2)]
            elif key and line.startswith('"'):
                parts[key].append(line.strip()[1:-1])
            elif line.startswith("#"):
                key = None
        context = _unescape("".join(parts["msgctxt"])) if "msgctxt" in parts else ""
        msgid = _unescape("".join(parts["msgid"])) if "msgid" in parts else None
        yield start, end, context, msgid
        start = end


def add_flags(path: Path, flags: dict[tuple[str, str], list[str]] | None = None) -> int:
    """The flags of each listed text written into its "#," line in the PO file at `path` (the
    line made where a PO file has it when the entry has none); the flags it has stay, none is
    written twice, all in alphabetical order. How many entries got a flag."""
    flags = read_flags() if flags is None else flags
    lines = path.read_text(encoding="utf-8").splitlines()
    out, last, changed = [], 0, 0
    for start, end, context, msgid in _entries(lines):
        out += lines[last:start]
        entry = lines[start:end]
        wanted = flags.get((context, msgid)) if msgid else None
        if wanted:
            at = next((i for i, line in enumerate(entry) if line.startswith("#,")), None)
            have = ([f.strip() for f in entry[at][2:].split(",") if f.strip()]
                    if at is not None else [])
            # Sorted, as translate-toolkit (Weblate) writes the line whenever it changes one
            # of its flags: the two never reorder each other's lines.
            merged = sorted({*have, *wanted})
            if set(merged) != set(have):
                changed += 1
                line = "#, " + ", ".join(merged)
                if at is not None:
                    entry[at] = line
                else:
                    # After the translators' and extracted comments and the references; before
                    # the previous msgid (#|) and the entry itself.
                    i = next((i for i, text in enumerate(entry)
                              if not text.startswith(("# ", "#.", "#:")) and text != "#"),
                             len(entry))
                    entry.insert(i, line)
        out += entry
        last = end
    out += lines[last:]
    if changed:
        path.write_text("\n".join(out) + "\n", encoding="utf-8")
    return changed


def build(locale_dir: Path | None = None, po_dir: Path = PO) -> list[Path]:
    """The compiled catalog of each language of LINGUAS, into `locale_dir` (by default where
    the program reads them, i18n.LOCALE_DIR); each checked by msgfmt."""
    locale_dir = locale_dir or i18n.LOCALE_DIR
    msgfmt = _tool("msgfmt")
    built = []
    for language in languages(po_dir):
        mo = locale_dir / language / "LC_MESSAGES" / f"{i18n.DOMAIN}.mo"
        mo.parent.mkdir(parents=True, exist_ok=True)
        _run([msgfmt, "--check", f"--output-file={mo}", str(po_dir / f"{language}.po")])
        built.append(mo)
    log.info("translations: %d catalog%s built", len(built), "" if len(built) == 1 else "s")
    return built


def compose_metainfo(out: Path = METAINFO, po_dir: Path = PO) -> Path:
    """The metainfo with every language of LINGUAS, from its template (msgfmt's bulk mode)."""
    _run([_tool("msgfmt"), "--xml", f"--template={METAINFO_TEMPLATE}", "-d", str(po_dir),
          f"--output-file={out}"])
    return out


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    try:
        if argv == ["--template"]:
            print(write_template().relative_to(ROOT))
            return 0
        if argv[:1] == ["--flags"] and argv[1:]:
            flags = read_flags()
            for name in argv[1:]:
                add_flags(Path(name), flags)
            return 0
        if argv:
            print(__doc__.split("\n\n")[1], file=sys.stderr)
            return 2
        for mo in build():
            print(mo.relative_to(ROOT) if mo.is_relative_to(ROOT) else mo)
        print(compose_metainfo().relative_to(ROOT))
    except CatalogError as e:
        print(e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
