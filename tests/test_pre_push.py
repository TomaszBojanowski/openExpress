"""The hook that runs before a push (tools/pre-push): it refuses a private branch and runs the
secret check on what would be pushed, from the clone's main worktree and from another one whose
tree has no tools/ (a branch of its own history, such as the website's). In a made-up clone;
the check is a stand-in that notes what it was asked to check."""

import shutil
import subprocess
from pathlib import Path

import pytest
from fakes import git

ROOT = Path(__file__).resolve().parent.parent
ZERO = "0" * 40
CHECK = """#!/bin/sh
echo "$1" >> "$(dirname "$0")/checked.txt"
"""


@pytest.fixture
def clone(tmp_path):
    """A clone with the hook linked as the project's README says, its secret check a
    stand-in, and a second worktree on a branch of its own history."""
    if shutil.which("git") is None:
        pytest.skip("no git")
    repo = tmp_path / "clone"
    (repo / "tools").mkdir(parents=True)
    shutil.copy(ROOT / "tools" / "pre-push", repo / "tools" / "pre-push")
    check = repo / "tools" / "check-secrets.sh"
    check.write_text(CHECK)
    check.chmod(0o755)
    git(repo, "init", "-q", "-b", "main")
    git(repo, "add", "tools")
    git(repo, "commit", "-q", "-m", "tools")
    (repo / ".git" / "hooks" / "pre-push").symlink_to("../../tools/pre-push")
    site = tmp_path / "site"
    try:
        git(repo, "worktree", "add", "-q", "--orphan", "-b", "site", str(site))
    except subprocess.CalledProcessError:
        pytest.skip("this git makes no worktree of a branch of its own history")
    (site / "index.html").write_text("<p>made-up</p>\n")
    git(site, "add", "index.html")
    git(site, "commit", "-q", "-m", "page")
    return repo, site


def push(cwd: Path, repo: Path, local_ref: str, sha: str) -> subprocess.CompletedProcess:
    """The hook run as git runs it for a push from `cwd`: the clone's own hook, one line on
    its input for the branch."""
    line = f"{local_ref} {sha} {local_ref} {ZERO}\n"
    return subprocess.run([str(repo / ".git" / "hooks" / "pre-push"), "origin", "https://x"],
                          cwd=cwd, input=line, capture_output=True, text=True, check=False)


@pytest.mark.parametrize("where", ["main", "other worktree"])
def test_the_secret_check_runs_on_what_is_pushed_from_any_worktree(clone, where):
    repo, site = clone
    cwd = repo if where == "main" else site
    sha = git(cwd, "rev-parse", "HEAD")
    branch = "refs/heads/main" if where == "main" else "refs/heads/site"
    done = push(cwd, repo, branch, sha)
    assert done.returncode == 0, done.stderr
    assert (repo / "tools" / "checked.txt").read_text().split() == [sha]


def test_a_private_branch_is_never_pushed(clone):
    repo, site = clone
    done = push(site, repo, "refs/heads/private-notes", git(site, "rev-parse", "HEAD"))
    assert done.returncode == 1 and "private branch" in done.stderr
    assert not (repo / "tools" / "checked.txt").exists()
