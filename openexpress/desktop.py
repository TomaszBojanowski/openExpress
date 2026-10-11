"""Install the application launcher and the systemd user service (home directory only)."""

from __future__ import annotations

import gettext
import os
import shlex
import sys
from pathlib import Path

from openexpress import i18n
from openexpress.i18n import N_

DESKTOP_ID = "io.github.tomaszbojanowski.OpenExpress"
SERVICE = "openexpress-watch.service"

NAME = "openExpress"
NAME_BACKGROUND = N_("openExpress (background)")
GENERIC_NAME = N_("Garmin Watch Sync")
COMMENT = N_("Sync, updates and maps for Garmin watches")
# TRANSLATORS: search keywords for the application menu; keep the ";" separators and the ending ";"
KEYWORDS = N_("Garmin;Fenix;Connect;sync;")
SERVICE_DESCRIPTION = "openExpress: sync when the watch is connected"
ICON = Path(__file__).parent / "data" / f"{DESKTOP_ID}.svg"
# Files written by pre-release versions (named "garmin-linux").
LEGACY_FILES = (
    "applications/io.github.garminlinux.GarminLinux.desktop",
    "applications/io.github.garminlinux.GarminLinux.Watch.desktop",
)
LEGACY_SERVICE = "garmin-linux-watch.service"


def _localized(key: str, message: str) -> str:
    """Key=message plus Key[lang]=... lines for every available translation that differs.

    The active UI language is not changed: each catalog is loaded separately.
    """
    lines = [f"{key}={message}"]
    for lang in i18n.available_languages():
        if lang == i18n.SOURCE_LANGUAGE:
            continue
        tr = gettext.translation(i18n.DOMAIN, i18n.LOCALE_DIR, languages=[lang], fallback=True)
        value = tr.gettext(message)
        if value != message:
            lines.append(f"{key}[{lang}]={value}")
    return "\n".join(lines)


def _python_cmd(module_args: str) -> str:
    return f"{shlex.quote(sys.executable)} -m openexpress {module_args}"


def _env_prefix() -> str:
    """If openExpress runs from a source checkout, point to it via PYTHONPATH."""
    import site

    root = Path(__file__).resolve().parent.parent
    installed = {Path(p).resolve() for p in site.getsitepackages() + [site.getusersitepackages()]}
    return "" if root in installed else f"PYTHONPATH={root}"


def desktop_entry(exec_gui: str, background: bool = False) -> str:
    """The .desktop launcher, with translated keys for every available language."""
    name = _localized("Name", NAME_BACKGROUND) if background else f"Name={NAME}"
    text = f"""[Desktop Entry]
Type=Application
{name}
{_localized("GenericName", GENERIC_NAME)}
{_localized("Comment", COMMENT)}
Exec={exec_gui}
Icon={DESKTOP_ID}
Terminal=false
Categories=Utility;GTK;
{_localized("Keywords", KEYWORDS)}
StartupNotify=true
"""
    return text + ("NoDisplay=true\n" if background else "")


def files() -> dict[Path, str]:
    home = Path.home()
    data = Path(os.environ.get("XDG_DATA_HOME", home / ".local/share"))
    config = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config"))
    env = _env_prefix()
    exec_gui = (f"env {env} " if env else "") + _python_cmd("gui")
    desktop = desktop_entry(exec_gui)
    desktop_watch = desktop_entry(exec_gui, background=True)
    # systemd unit files have no localized keys: Description stays in English.
    service = f"""[Unit]
Description={SERVICE_DESCRIPTION}
After=graphical-session.target
PartOf=graphical-session.target

[Service]
Type=simple
{('Environment=' + env) if env else ''}
ExecStart={_python_cmd("watch")}
Restart=on-failure
RestartSec=10

[Install]
WantedBy=graphical-session.target
"""
    return {
        data / "applications" / f"{DESKTOP_ID}.desktop": desktop,
        data / "applications" / f"{DESKTOP_ID}.Watch.desktop": desktop_watch,
        config / "systemd" / "user" / SERVICE: service,
        data / "icons" / "hicolor" / "scalable" / "apps" / f"{DESKTOP_ID}.svg": ICON.read_text(),
    }


def legacy_files() -> list[Path]:
    home = Path.home()
    data = Path(os.environ.get("XDG_DATA_HOME", home / ".local/share"))
    config = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config"))
    paths_ = [data / f for f in LEGACY_FILES] + [config / "systemd" / "user" / LEGACY_SERVICE,
                                                 config / "systemd" / "user" /
                                                 "graphical-session.target.wants" / LEGACY_SERVICE]
    return [p for p in paths_ if p.exists() or p.is_symlink()]


def install(dry_run: bool) -> tuple[list[Path], list[Path]]:
    """Returns (written files, removed legacy files)."""
    written = []
    for path, content in files().items():
        written.append(path)
        if not dry_run:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
    removed = legacy_files()
    if not dry_run:
        for path in removed:
            path.unlink()
    return written, removed
