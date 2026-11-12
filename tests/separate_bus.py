"""A D-Bus of its own for a program that a test or a tool runs, never the session's.

A second xdg-document-portal unmounts the session's /run/user/UID/doc when it starts; every
Flatpak then fails until the session's portal is restarted. Such a portal starts when a program
on a bus that may start services asks for a portal (plain dbus-run-session gives one). Here:
- the bus starts no service (no service folders in its configuration), so no portal, gvfsd or
  accessibility bus can start on it;
- the program asks for no portal and no accessibility bus, and uses no gvfs nor dconf;
- its runtime folder (XDG_RUNTIME_DIR) is a new one, so that even a portal started by other
  means would mount there, not on the session's.

In Python: subprocess.run(command(argv, folder), env=environment(folder)). From the command line,
in place of `dbus-run-session -- PROGRAM…`:
    python3 -I ~/garmin-linux/tests/separate_bus.py -- PROGRAM…
"""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

CONFIG = """<!DOCTYPE busconfig PUBLIC "-//freedesktop//DTD D-Bus Bus Configuration 1.0//EN"
 "http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd">
<busconfig>
  <type>session</type>
  <listen>unix:tmpdir={sockets}</listen>
  <auth>EXTERNAL</auth>
  <policy context="default">
    <allow send_destination="*" eavesdrop="true"/>
    <allow eavesdrop="true"/>
    <allow own="*"/>
  </policy>
</busconfig>
"""

NO_SERVICES = {"ADW_DISABLE_PORTAL": "1", "GDK_DEBUG": "no-portals", "GTK_A11Y": "none",
               "NO_AT_BRIDGE": "1", "GIO_USE_VFS": "local", "GSETTINGS_BACKEND": "memory"}


def available() -> bool:
    return shutil.which("dbus-run-session") is not None


def command(argv: list[str], folder: Path) -> list[str]:
    """`argv` under a bus of its own; its configuration is written in `folder`, its socket goes
    in the system's folder of temporary files (the path of a Unix socket is at most 108 bytes,
    and `folder` may be deep)."""
    folder.mkdir(parents=True, exist_ok=True)
    config = folder / "separate-bus.conf"
    config.write_text(CONFIG.format(sockets=tempfile.gettempdir()))
    return ["dbus-run-session", f"--config-file={config}", "--", *argv]


def environment(folder: Path, base: dict[str, str] | None = None) -> dict[str, str]:
    """`base` (the process's environment by default) with the services off and a runtime folder
    of its own in `folder`. The session's runtime folder stays known to the tests' guard
    (OPENEXPRESS_SESSION_RUNTIME), which watches the session's document portal."""
    env = dict(os.environ if base is None else base)
    runtime = folder / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    runtime.chmod(0o700)
    session = env.get("OPENEXPRESS_SESSION_RUNTIME") or env.get("XDG_RUNTIME_DIR", "")
    env.update(NO_SERVICES, XDG_RUNTIME_DIR=str(runtime), OPENEXPRESS_SESSION_RUNTIME=session)
    return env


def main(argv: list[str]) -> int:
    if not argv or argv[0] != "--" or len(argv) < 2:
        print(next(line.strip() for line in __doc__.splitlines() if "separate_bus.py --" in line),
              file=sys.stderr)
        return 2
    with tempfile.TemporaryDirectory(prefix="separate-bus-") as folder:
        return subprocess.run(command(argv[1:], Path(folder)), env=environment(Path(folder)),
                              check=False).returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
