"""openexpress/__main__.py runs the program only as `python3 -m openexpress`: importing it
(as a tool that imports every module of the package does) runs nothing (such an import has
opened the window). The program itself is never started here: cli.main is a
stand-in while the module is imported."""

import importlib
import subprocess
import sys
from pathlib import Path

import openexpress


def test_importing_the_main_module_runs_nothing(monkeypatch):
    from openexpress import cli

    called = []
    monkeypatch.setattr(cli, "main", lambda *a, **kw: called.append(a) or 0)
    monkeypatch.delitem(sys.modules, "openexpress.__main__", raising=False)
    try:
        importlib.import_module("openexpress.__main__")
    except SystemExit:
        pass  # what the module does without its guard, after it ran main()
    finally:
        sys.modules.pop("openexpress.__main__", None)
    assert called == []


def test_python_m_openexpress_still_runs_the_program():
    """Only --version: it opens no device and asks no server."""
    root = Path(openexpress.__file__).parent.parent
    done = subprocess.run([sys.executable, "-m", "openexpress", "--version"], cwd=root,
                          capture_output=True, text=True, timeout=60, check=False)
    assert done.returncode == 0
    assert openexpress.__version__ in done.stdout + done.stderr
