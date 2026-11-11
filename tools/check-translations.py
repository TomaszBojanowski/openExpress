#!/usr/bin/env python3
"""Before a push (tools/pre-push): no commit of ours changes a language other than Polish.

Every language but Polish is its translators', in Weblate: only Weblate's commits change its
file (po/<language>.po; their committer is Hosted Weblate), and a merge of Weblate's pull request
brings them as they are (a merge that changed such a file by hand is refused). The template and
po/pl.po are ours. The rule holds from the release RULE_FROM on: before its tag exists nothing is
checked, and only the commits after it are.

Usage: tools/check-translations.py LOCAL_SHA REMOTE_SHA   (REMOTE_SHA all zeros: a new branch)
"""
import subprocess
import sys

RULE_FROM = "v0.10.0"
WEBLATE = "@weblate.org"  # the end of the committer's address in Weblate's commits


def git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout


def known(ref: str) -> bool:
    return subprocess.run(["git", "rev-parse", "-q", "--verify", f"{ref}^{{commit}}"], check=False,
                          capture_output=True).returncode == 0


def by_hand(merge: str, parents: list[str]) -> list[str]:
    """The files a merge has otherwise than git merges its two parents by itself (a conflict
    resolved, or anything changed in it): a clean merge of Weblate's branch has none."""
    if len(parents) != 2:
        return git("diff-tree", "--cc", "--name-only", "--no-commit-id", merge).split()
    done = subprocess.run(["git", "merge-tree", "--write-tree", *parents],
                          capture_output=True, text=True, check=False)
    tree = done.stdout.split("\n", 1)[0].strip()
    if done.returncode not in (0, 1) or not tree:
        return git("diff-tree", "--cc", "--name-only", "--no-commit-id", merge).split()
    return git("diff", "--name-only", tree, merge).split()


def other_languages(files: list[str]) -> list[str]:
    return [f for f in files if f.startswith("po/") and f.endswith(".po") and f != "po/pl.po"]


def refused(local: str, remote: str) -> list[tuple[str, list[str]]]:
    """The commits to be pushed that change another language than Polish, with those files."""
    if not known(f"refs/tags/{RULE_FROM}"):
        return []
    since = [f"^{remote}"] if set(remote) != {"0"} and known(remote) else ["--not", "--remotes"]
    found = []
    for commit in git("rev-list", local, f"^{RULE_FROM}", *since).split():
        parents = git("rev-list", "--parents", "-n", "1", commit).split()[1:]
        if len(parents) > 1:  # a merge: only what was changed in it by hand
            files = by_hand(commit, parents)
        elif git("show", "-s", "--format=%ce", commit).strip().endswith(WEBLATE):
            continue
        else:
            files = git("diff-tree", "--no-commit-id", "--name-only", "-r", commit).split()
        if other_languages(files):
            found.append((commit[:7], other_languages(files)))
    return found


def main() -> int:
    found = refused(sys.argv[1], sys.argv[2])
    for commit, files in found:
        print(f"STOP: {commit} changes {', '.join(files)}: every language but Polish is changed "
              "only in Weblate.", file=sys.stderr)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
