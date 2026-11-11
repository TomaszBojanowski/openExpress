"""tools/status-review.py: what it does with the folder it is given."""

import importlib.util
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _tool():
    spec = importlib.util.spec_from_file_location("status_review",
                                                  ROOT / "tools" / "status-review.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_review_into_a_folder_keeps_the_files_that_are_not_its_own(tmp_path, monkeypatch):
    """The folder may hold other work (mockups, renders of a design): a review writes its
    pictures and its index there again and leaves the rest as it was."""
    tool = _tool()
    others = {"mockup.png": b"m", "notes.txt": b"n", "renders/step-c.png": b"r"}
    for name, data in others.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_bytes(data)
    (tmp_path / "bike-pl-narrow-02-question.png").write_bytes(b"an earlier review")
    (tmp_path / "index.html").write_text("an earlier review")
    monkeypatch.setattr(tool.subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(
        a, 0, "21 passed in 1.00s\n", ""))
    monkeypatch.setattr(sys, "argv", ["status-review.py", str(tmp_path)])

    assert tool.main() == 0
    left = sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*") if p.is_file())
    assert left == ["index.html", "mockup.png", "notes.txt", "renders/step-c.png"]
    assert all((tmp_path / name).read_bytes() == data for name, data in others.items())
    assert "an earlier review" not in (tmp_path / "index.html").read_text()
