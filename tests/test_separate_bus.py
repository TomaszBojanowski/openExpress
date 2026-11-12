"""The D-Bus of its own (tests/separate_bus.py) under which the tests and the tools run a program:
no service can start on it, the program asks for no portal, and its runtime folder is its own, so
that nothing started under it can mount a second document portal on the session's
/run/user/UID/doc (which takes the session's portal away)."""

import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

import separate_bus


def test_the_bus_starts_no_service(tmp_path):
    command = separate_bus.command(["true"], tmp_path)
    assert command[0] == "dbus-run-session" and command[-2:] == ["--", "true"]
    config = Path(command[1].removeprefix("--config-file="))
    text = config.read_text()
    assert "<servicedir>" not in text and "standard_session_servicedirs" not in text


def test_the_socket_of_the_bus_fits_however_deep_the_folder(tmp_path):
    """The path of a Unix socket is at most 108 bytes: the bus's socket goes in the system's
    folder of temporary files, not in the (maybe deep) folder given."""
    deep = tmp_path.joinpath(*["a-rather-long-folder-name"] * 6)
    command = separate_bus.command(["true"], deep)
    text = Path(command[1].removeprefix("--config-file=")).read_text()
    assert f"<listen>unix:tmpdir={tempfile.gettempdir()}</listen>" in text


def test_the_program_has_a_runtime_folder_of_its_own_and_no_portal(tmp_path):
    env = separate_bus.environment(tmp_path)
    runtime = Path(env["XDG_RUNTIME_DIR"])
    assert runtime.is_dir() and runtime.is_relative_to(tmp_path)
    assert stat.S_IMODE(runtime.stat().st_mode) == 0o700
    assert env["ADW_DISABLE_PORTAL"] == "1" and env["GDK_DEBUG"] == "no-portals"
    # The session's runtime folder, for the guard of the tests that watches its portal.
    assert env["OPENEXPRESS_SESSION_RUNTIME"] == os.environ.get(
        "OPENEXPRESS_SESSION_RUNTIME", os.environ.get("XDG_RUNTIME_DIR", ""))


def test_a_program_under_it_sees_its_bus_and_its_folder(tmp_path):
    if not separate_bus.available():
        import pytest

        pytest.skip("no dbus-run-session")
    script = ("import os; print(os.environ['DBUS_SESSION_BUS_ADDRESS']); "
              "print(os.environ['XDG_RUNTIME_DIR'])")
    done = subprocess.run(separate_bus.command([sys.executable, "-c", script], tmp_path),
                          env=separate_bus.environment(tmp_path), capture_output=True,
                          text=True, timeout=60, check=True)
    address, runtime = done.stdout.splitlines()[-2:]
    assert address.startswith(f"unix:path={tempfile.gettempdir()}/")
    assert address != os.environ.get("DBUS_SESSION_BUS_ADDRESS")  # not the session's bus
    assert Path(runtime).is_relative_to(tmp_path)
