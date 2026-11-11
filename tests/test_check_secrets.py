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


def test_a_folder_given_with_path_is_checked_also_in_binary_files(clone, tmp_path):
    """--path: the files of a build or a bundle, binary ones too; only their names are said."""
    build = tmp_path / "build" / "files"
    (build / "lib").mkdir(parents=True)
    (build / "lib" / "clean.py").write_text("nothing here\n")
    (build / "lib" / "blob.so").write_bytes(b"\x00\x01ELF" + SECRET.encode() + b"\x00\xff")
    done = check(clone, "--path", str(build))
    said = done.stdout + done.stderr
    assert done.returncode == 1
    assert "blob.so" in said and "clean.py" not in said
    assert "MADEUP" not in said


def test_a_clean_file_given_with_path_passes(clone, tmp_path):
    bundle = tmp_path / "openexpress-0.0.0.flatpak"
    bundle.write_bytes(b"\x00made-up bundle\x00")
    done = check(clone, "--path", str(bundle))
    assert done.returncode == 0, done.stderr
    assert "No secrets found" in done.stdout
