"""Background sync: start at login, and start/stop the watcher now.

- Flatpak: the XDG Background portal registers autostart (the user is asked once).
- Otherwise: the systemd user service written by `openexpress install-desktop`.
The watcher is a unique GApplication, so starting it twice is harmless; it is stopped
through its "quit" action over D-Bus.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from openexpress import desktop  # noqa: E402
from openexpress.i18n import _  # noqa: E402

WATCH_ID = desktop.DESKTOP_ID + ".Watch"
WATCH_PATH = "/" + WATCH_ID.replace(".", "/")


def in_flatpak() -> bool:
    return Path("/.flatpak-info").exists()


def watcher_command() -> list[str]:
    exe = shutil.which("openexpress")
    return [exe, "watch"] if exe else [sys.executable, "-m", "openexpress", "watch"]


def start_watcher() -> None:
    env = dict(os.environ)
    root = str(Path(__file__).resolve().parent.parent)
    env["PYTHONPATH"] = root + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    subprocess.Popen(watcher_command(), start_new_session=True, env=env,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL)


def stop_watcher() -> None:
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        Gio.DBusActionGroup.get(bus, WATCH_ID, WATCH_PATH).activate_action("quit", None)
    except GLib.Error:
        pass


def _portal_request(autostart: bool, parent: str, done: Callable[[bool, str], None]) -> None:
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    token = f"openexpress{GLib.random_int_range(0, 1 << 30):d}"
    sender = bus.get_unique_name()[1:].replace(".", "_")
    handle = f"/org/freedesktop/portal/desktop/request/{sender}/{token}"
    sub: list[int] = []

    def on_response(_conn, _sender, _path, _iface, _signal, params):
        bus.signal_unsubscribe(sub[0])
        code, results = params.unpack()
        if code != 0:
            done(False, _("Background sync was not allowed."))
        elif autostart and not results.get("autostart", False):
            done(False, _("Starting at login was not allowed."))
        else:
            done(True, "")

    sub.append(bus.signal_subscribe("org.freedesktop.portal.Desktop",
                                    "org.freedesktop.portal.Request", "Response", handle,
                                    None, Gio.DBusSignalFlags.NO_MATCH_RULE, on_response))
    options = {
        "handle_token": GLib.Variant("s", token),
        "reason": GLib.Variant("s", _("Sync the device with Garmin Connect when it is connected.")),
        "autostart": GLib.Variant("b", autostart),
        "commandline": GLib.Variant("as", ["openexpress", "watch"]),
        "dbus-activatable": GLib.Variant("b", False),
    }
    try:
        bus.call_sync("org.freedesktop.portal.Desktop", "/org/freedesktop/portal/desktop",
                      "org.freedesktop.portal.Background", "RequestBackground",
                      GLib.Variant("(sa{sv})", (parent, options)), None,
                      Gio.DBusCallFlags.NONE, -1, None)
    except GLib.Error as e:
        bus.signal_unsubscribe(sub[0])
        done(False, e.message)


def _systemctl(*args: str) -> tuple[bool, str]:
    res = subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True,
                         check=False)
    return res.returncode == 0, res.stderr.strip()


def set_enabled(enabled: bool, done: Callable[[bool, str], None], parent: str = "") -> None:
    """Enables or disables background sync; `done(ok, error)` is called from the main loop."""
    if in_flatpak():
        def finished(ok: bool, error: str) -> None:
            if ok:
                start_watcher() if enabled else stop_watcher()
            done(ok, error)

        _portal_request(enabled, parent, finished)
        return
    threading.Thread(target=_set_enabled_systemd, args=(enabled, done), daemon=True).start()


def _set_enabled_systemd(enabled: bool, done: Callable[[bool, str], None]) -> None:
    try:
        if enabled:
            desktop.install(dry_run=False)
            ok, err = _systemctl("daemon-reload")
            if ok:
                ok, err = _systemctl("enable", "--now", desktop.SERVICE)
        else:
            ok, err = _systemctl("disable", "--now", desktop.SERVICE)
    except FileNotFoundError:
        ok, err = False, _("systemctl was not found; background sync needs systemd.")
    except Exception as e:  # noqa: BLE001
        ok, err = False, str(e)
    GLib.idle_add(lambda: (done(ok, err), False)[1])


def is_enabled() -> bool | None:
    """True/False outside Flatpak; None when unknown (Flatpak keeps no queryable state)."""
    if in_flatpak():
        return None
    try:
        res = subprocess.run(["systemctl", "--user", "is-enabled", desktop.SERVICE],
                             capture_output=True, text=True, check=False)
    except OSError:
        return False
    return res.stdout.strip() == "enabled"
