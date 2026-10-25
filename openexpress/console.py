"""What the command line and the window say on the terminal: no GTK, no argparse, so that
both use it without one importing the other."""

from __future__ import annotations

import sys


def error(text: str) -> None:
    """An error message for the user, on stderr. What was printed so far comes first, also
    when the output goes to a pipe or a file (stdout is buffered then, stderr is not)."""
    sys.stdout.flush()
    print(text, file=sys.stderr)
