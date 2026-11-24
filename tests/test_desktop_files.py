"""The desktop files of the source version, and openexpress uninstall."""

import sys
import types
from pathlib import Path

import pytest

from openexpress import desktop
from openexpress.cli import main

# --- desktop / systemd quoting -----------------------------------------------------------


def test_desktop_exec_quote():
    q = desktop.desktop_exec_quote
    assert q("/usr/bin/python3") == "/usr/bin/python3"
    assert q("PYTHONPATH=/x/y") == "PYTHONPATH=/x/y"
    assert q("/a b/py") == '"/a b/py"'
    assert q("/a%b") == "/a%%b"
    # $ is escaped for the Exec quoting, then the backslash for the string escaping.
    assert q('/a "b" $c') == '"/a \\\\"b\\\\" \\\\$c"'


def test_files_quote_paths_with_spaces_and_percent(xdg, monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "executable", "/opt/my py%/bin/python3")
    monkeypatch.setattr(desktop, "_source_root", lambda: Path("/home/u/My Code/100%"))
    out = {p.name: text for p, text in desktop.files().items()}
    entry = out[f"{desktop.SOURCE_ID}.desktop"]
    exec_line = next(line for line in entry.splitlines() if line.startswith("Exec="))
    assert exec_line == ('Exec=env "PYTHONPATH=/home/u/My Code/100%%" '
                         '"/opt/my py%%/bin/python3" -m openexpress gui')
    unit = out[desktop.SERVICE]
    assert 'Environment="PYTHONPATH=/home/u/My Code/100%%"' in unit
    assert 'ExecStart="/opt/my py%%/bin/python3" -m openexpress watch' in unit


def test_files_without_source_root(xdg, monkeypatch):
    monkeypatch.setattr(desktop, "_source_root", lambda: None)
    unit = {p.name: t for p, t in desktop.files().items()}[desktop.SERVICE]
    assert "Environment=" not in unit


def test_an_empty_xdg_variable_counts_as_not_set(xdg, monkeypatch, tmp_path):
    """XDG_DATA_HOME="" and XDG_CONFIG_HOME="" mean the folders below the home folder, as the
    XDG rules say, not the folder the command is run in."""
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_DATA_HOME", "")
    monkeypatch.setenv("XDG_CONFIG_HOME", "")
    monkeypatch.setattr(desktop, "_source_root", lambda: None)
    written = list(desktop.files())
    assert written and all(p.is_relative_to(home) for p in written)
    assert desktop._service_wants_link().is_relative_to(home / ".config")


# --- uninstall ---------------------------------------------------------------------------


def test_uninstall_disables_service_and_removes_wants_link(xdg, monkeypatch, tmp_path):
    import shutil
    import subprocess

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    calls = []
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: calls.append(cmd)
                        or types.SimpleNamespace(returncode=0, stdout="", stderr=""))
    written = desktop.install(dry_run=False)
    unit = next(p for p in written if p.name == desktop.SERVICE)
    wants = unit.parent / "graphical-session.target.wants" / desktop.SERVICE
    wants.parent.mkdir(parents=True)
    wants.symlink_to(unit)

    planned = desktop.uninstall(dry_run=True)
    assert wants in planned and calls == []
    assert all(p.exists() for p in written)

    removed = desktop.uninstall(dry_run=False)
    assert ["systemctl", "--user", "disable", "--now", desktop.SERVICE] in calls
    assert wants in removed and not wants.is_symlink()
    assert not any(p.exists() for p in written)


def test_uninstall_without_systemctl(xdg, monkeypatch, tmp_path):
    import shutil
    import subprocess

    monkeypatch.setattr(shutil, "which", lambda name: None)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("must not run"))
    written = desktop.install(dry_run=False)
    desktop.uninstall(dry_run=False)
    assert not any(p.exists() for p in written)


def test_cli_remove_message(xdg, monkeypatch, capsys, tmp_path):
    from openexpress import background

    monkeypatch.setattr(background, "in_flatpak", lambda: False)
    monkeypatch.setattr(desktop, "uninstall",
                        lambda dry_run: [tmp_path / desktop.SERVICE])
    assert main(["install-desktop", "--remove", "--apply"]) == 0
    assert "stopped and disabled" in capsys.readouterr().out


# --- GUI logic without a display ---------------------------------------------------------


def test_removed_files_are_counted_in_the_plural():
    from openexpress.gui.app import MainWindow

    assert MainWindow._removed_files(1) == "Removed 1 file"
    assert MainWindow._removed_files(3) == "Removed 3 files"
