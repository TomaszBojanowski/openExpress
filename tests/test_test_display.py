"""The hidden X server of the tests (conftest.py) ends with the tests, also when they do
not end well: a segfault of pytest runs no atexit, and the server would stay."""

import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

CONFTEST = Path(__file__).resolve().parent / "conftest.py"

# A process that loads conftest.py as pytest does (which starts the hidden X server), says
# the server's PID, and dies the way a segfault ends it: at once, with no atexit.
DIES = f"""
import importlib.util, os, signal, sys
sys.path.insert(0, {str(CONFTEST.parent.parent)!r})
spec = importlib.util.spec_from_file_location("conftest_of_the_tests", {str(CONFTEST)!r})
conftest = importlib.util.module_from_spec(spec)
spec.loader.exec_module(conftest)
print(conftest._xvfb.pid if conftest._xvfb is not None else "none", flush=True)
os.kill(os.getpid(), signal.SIGKILL)
"""


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:  # a zombie is gone, only not reaped yet
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except OSError:
        return False
    return state != "Z"


@pytest.mark.skipif(shutil.which("Xvfb") is None, reason="no Xvfb")
def test_the_hidden_x_server_ends_when_pytest_dies():
    env = {k: v for k, v in os.environ.items()
           if k not in ("OPENEXPRESS_TESTS_NO_DISPLAY", "OPENEXPRESS_TESTS_ON_SCREEN")}
    run = subprocess.run([sys.executable, "-c", DIES], capture_output=True, text=True,
                         env=env, timeout=60, check=False)
    assert run.returncode == -signal.SIGKILL, run.stderr
    pid = int(run.stdout.split()[0])
    deadline = time.monotonic() + 10
    while _alive(pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    survived = _alive(pid)
    if survived:
        os.kill(pid, signal.SIGKILL)  # never left behind by this test
    assert not survived


# A test of the window whose window is left in a reference cycle: pytest's last collection of
# garbage, after every test, finalizes it.
LEFT_IN_A_CYCLE = """
import gc

import pytest

pytestmark = [pytest.mark.needs_display]


def test_a_window_left_in_a_cycle():
    from gi.repository import Gtk

    gc.disable()  # as after many tests: the cycle waits for pytest's last collection
    window = Gtk.Window()
    window.present()
    window.destroy()
    cycle = [window]
    cycle.append(cycle)
"""


@pytest.mark.skipif(shutil.which("Xvfb") is None, reason="no Xvfb")
def test_a_run_whose_last_test_leaves_a_window_in_a_cycle_ends_well(tmp_path):
    """GTK finalizing a window asks the X server; were the hidden server stopped before
    pytest's last collection of garbage, GTK would end the process at once (exit code 1, the
    summary lost in its buffer): a file of tests run alone, or last, must end with 0 and its
    summary whatever its last test leaves to the collector."""
    root = CONFTEST.parent.parent
    test = tmp_path / "test_left_in_a_cycle.py"
    test.write_text(LEFT_IN_A_CYCLE, encoding="utf-8")
    env = {k: v for k, v in os.environ.items()
           if k not in ("OPENEXPRESS_TESTS_NO_DISPLAY", "OPENEXPRESS_TESTS_ON_SCREEN")}
    env["PYTHONPATH"] = str(CONFTEST.parent)
    run = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                          "-p", "conftest", "-c", str(root / "pyproject.toml"), "--rootdir",
                          str(root), str(test)], cwd=root, capture_output=True, text=True,
                         env=env, timeout=120, check=False)
    last = run.stdout.strip().splitlines()[-1] if run.stdout.strip() else ""
    assert (run.returncode, last.split(" in ")[0]) == (0, "1 passed")
