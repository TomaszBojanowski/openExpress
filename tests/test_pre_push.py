"""The hook that runs before a push (tools/pre-push): it refuses a private branch, runs the
secret check on what would be pushed and refuses a commit of ours that changes another language
than Polish (from the release tools/check-translations.py names on), from the clone's main worktree and from another one whose
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
    shutil.copy(ROOT / "tools" / "check-translations.py", repo / "tools" / "check-translations.py")
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


# --- every language but Polish is changed only in Weblate ----------------------------------------


def commit_file(repo: Path, path: str, text: str, weblate: bool = False) -> str:
    """A commit that writes `path` (as fakes.git: no configuration of the user's, no signing);
    its committer Hosted Weblate when `weblate`."""
    import os

    (repo / path).parent.mkdir(parents=True, exist_ok=True)
    (repo / path).write_text(text)
    git(repo, "add", path)
    committer = ("Hosted Weblate", "hosted@weblate.org") if weblate else ("A", "a@example.com")
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
           "GIT_AUTHOR_NAME": "A", "GIT_AUTHOR_EMAIL": "a@example.com",
           "GIT_COMMITTER_NAME": committer[0], "GIT_COMMITTER_EMAIL": committer[1]}
    subprocess.run(["git", "-c", "commit.gpgsign=false", "commit", "-q", "-m", path], cwd=repo,
                   env=env, check=True)
    return git(repo, "rev-parse", "HEAD")


def test_before_the_release_of_the_rule_another_language_may_still_change(clone):
    repo, _site = clone
    sha = commit_file(repo, "po/de.po", "made-up German\n")
    done = push(repo, repo, "refs/heads/main", sha)
    assert done.returncode == 0, done.stderr


def test_after_it_only_weblate_changes_another_language(clone):
    repo, _site = clone
    git(repo, "tag", "v0.10.0")
    ours = commit_file(repo, "po/pl.po", "made-up Polish\n")
    assert push(repo, repo, "refs/heads/main", ours).returncode == 0
    weblate = commit_file(repo, "po/cs.po", "made-up Czech\n", weblate=True)
    assert push(repo, repo, "refs/heads/main", weblate).returncode == 0
    german = commit_file(repo, "po/de.po", "made-up German\n")
    done = push(repo, repo, "refs/heads/main", german)
    assert done.returncode == 1
    assert f"STOP: {german[:7]} changes po/de.po" in done.stderr


def test_a_merge_of_weblate_s_branch_passes_unless_a_language_was_changed_in_it(clone):
    repo, _site = clone
    git(repo, "tag", "v0.10.0")
    git(repo, "checkout", "-q", "-b", "weblate")
    commit_file(repo, "po/cs.po", "made-up Czech\n", weblate=True)
    git(repo, "checkout", "-q", "main")
    commit_file(repo, "po/pl.po", "made-up Polish\n")
    git(repo, "merge", "-q", "--no-ff", "--no-edit", "weblate")
    clean = git(repo, "rev-parse", "HEAD")
    assert push(repo, repo, "refs/heads/main", clean).returncode == 0
    git(repo, "reset", "-q", "--hard", "HEAD~1")
    git(repo, "merge", "-q", "--no-ff", "--no-commit", "weblate")
    (repo / "po" / "cs.po").write_text("made-up Czech, changed by hand\n")
    git(repo, "add", "po/cs.po")
    git(repo, "commit", "-q", "--no-edit")
    by_hand = git(repo, "rev-parse", "HEAD")
    done = push(repo, repo, "refs/heads/main", by_hand)
    assert done.returncode == 1 and "po/cs.po" in done.stderr
