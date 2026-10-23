"""tools/update-translations.sh drops the entries no code uses any more (msgattrib
--no-obsolete after msgmerge, which has no such option in gettext 0.26): before, they piled up
as "#~" in po/pl.po (46 by 24.10.2026). Run on a copy of the package and the catalogue in a
temporary folder; nothing in the repository changes."""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
OLD = '#~ msgid "A text no code uses any more"\n#~ msgstr "Tekst, którego kod już nie używa"\n'


@pytest.mark.skipif(not all(shutil.which(t) for t in ("xgettext", "msgmerge", "msgattrib", "msgfmt")),
                    reason="needs gettext")
def test_entries_no_code_uses_are_dropped(tmp_path):
    for folder in ("openexpress", "po", "tools"):
        shutil.copytree(ROOT / folder, tmp_path / folder,
                        ignore=shutil.ignore_patterns("__pycache__"))
    po = tmp_path / "po" / "pl.po"
    po.write_text(po.read_text(encoding="utf-8") + "\n" + OLD, encoding="utf-8")
    subprocess.run(["bash", str(tmp_path / "tools" / "update-translations.sh")], cwd=tmp_path,
                   check=True, capture_output=True, text=True)
    text = po.read_text(encoding="utf-8")
    assert "A text no code uses any more" not in text
    assert "#~" not in text
    assert 'msgid "Storage"' in text  # what the code uses stays
