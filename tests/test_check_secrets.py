"""The secret check (tools/check-secrets.sh) in a made-up clone with a made-up pattern: it finds
a secret in the commit it is given and in that commit's history, says only where (file and line,
commit), never the line or the commit's message, and looks at that commit, not at the working
tree."""

import shutil
import subprocess
from pathlib import Path

import pytest
from fakes import git

ROOT = Path(__file__).resolve().parent.parent
SECRET = "MADEUP-SECRET-4711"


@pytest.fixture
def clone(tmp_path):
    """A clone with the check and its patterns (untracked, as in the project), one clean file."""
    if shutil.which("git") is None:
        pytest.skip("no git")
    repo = tmp_path / "clone"
    (repo / "tools").mkdir(parents=True)
    shutil.copy(ROOT / "tools" / "check-secrets.sh", repo / "tools" / "check-secrets.sh")
    (repo / "tools" / "secret-patterns.txt").write_text("# made up\nMADEUP-SECRET-[0-9]+\n")
    (repo / "notes.txt").write_text("nothing here\n")
    git(repo, "init", "-q", "-b", "main")
    git(repo, "add", "tools/check-secrets.sh", "notes.txt")
    git(repo, "commit", "-q", "-m", "clean")
    return repo


def check(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([str(repo / "tools" / "check-secrets.sh"), *args], cwd=repo,
                          capture_output=True, text=True, check=False)


def test_a_clean_commit_passes(clone):
    done = check(clone, git(clone, "rev-parse", "HEAD"))
    assert done.returncode == 0, done.stderr
    assert "No secrets found" in done.stdout


def test_a_secret_is_found_and_only_where_it_is_is_said(clone):
    (clone / "notes.txt").write_text(f"first line\nkey = {SECRET}\n")
    git(clone, "commit", "-q", "-am", f"add {SECRET}")
    sha = git(clone, "rev-parse", "HEAD")
    done = check(clone, sha)
    said = done.stdout + done.stderr
    assert done.returncode == 1
    assert "notes.txt:2" in said
    assert sha[:7] in said
    assert "MADEUP" not in said


def test_a_secret_in_the_history_only_is_found(clone):
    (clone / "notes.txt").write_text(f"key = {SECRET}\n")
    git(clone, "commit", "-q", "-am", "add")
    (clone / "notes.txt").write_text("nothing here\n")
    git(clone, "commit", "-q", "-am", "take out")
    done = check(clone, git(clone, "rev-parse", "HEAD"))
    said = done.stdout + done.stderr
    assert done.returncode == 1
    assert git(clone, "rev-parse", "--short=7", "HEAD~1") in said
    assert "MADEUP" not in said


def test_the_commit_is_checked_not_the_working_tree(clone):
    """A secret only in the working tree is not in what is pushed; one only in the pushed
    commit is, though the working tree no longer has it."""
    clean = git(clone, "rev-parse", "HEAD")
    (clone / "notes.txt").write_text(f"key = {SECRET}\n")
    assert check(clone, clean).returncode == 0
    git(clone, "commit", "-q", "-am", "add")
    with_secret = git(clone, "rev-parse", "HEAD")
    (clone / "notes.txt").write_text("nothing here\n")
    done = check(clone, with_secret)
    assert done.returncode == 1
    assert "notes.txt:1" in done.stdout + done.stderr
