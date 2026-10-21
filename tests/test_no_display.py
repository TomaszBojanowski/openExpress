"""The window started where no display can be opened (e.g. the Flatpak with GDK_BACKEND=x11 on
Wayland without an X11 socket): one line saying so, on stderr and in the log, and an exit code
that is not 0; no GTK criticals, no traceback. Run in a process of its own, without a display:
nothing is opened, no device is looked for."""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_the_window_without_a_display_says_so_in_one_line(tmp_path):
    env = {k: v for k, v in os.environ.items() if k not in ("DISPLAY", "WAYLAND_DISPLAY")}
    env.update(GDK_BACKEND="x11", OPENEXPRESS_LANG="en",
               **{f"XDG_{k}_HOME": str(tmp_path / k.lower())
                  for k in ("CONFIG", "DATA", "CACHE", "STATE")})
    done = subprocess.run([sys.executable, "-m", "openexpress", "gui"], check=False, cwd=ROOT,
                          env=env, capture_output=True, text=True, timeout=60)
    assert done.returncode == 1
    lines = [line for line in done.stderr.splitlines() if line.strip()]
    assert lines == [("No display available (GDK_BACKEND=x11, WAYLAND_DISPLAY=-, DISPLAY=-): "
                      "openExpress needs a graphical session")], done.stderr
    log = (tmp_path / "state" / "openexpress" / "openexpress.log").read_text()
    assert "no display: GDK_BACKEND=x11, WAYLAND_DISPLAY=-, DISPLAY=-" in log
