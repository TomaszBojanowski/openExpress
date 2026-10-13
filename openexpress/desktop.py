"""Install the application launcher and the systemd user service (home directory only)."""

from __future__ import annotations

import gettext
import os
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


# Characters that make the desktop-entry spec require quoting an Exec argument.
_DESKTOP_RESERVED = set(" \t\n\"'\\><~|&;$*?#()`")


def desktop_exec_quote(arg: str) -> str:
    """One argument of a .desktop Exec= value (quoting rules plus string escaping)."""
    if arg and not (set(arg) & _DESKTOP_RESERVED):
        quoted = arg
    else:
        quoted = '"' + "".join("\\" + c if c in '"`$\\' else c for c in arg) + '"'
    # The general string escape (backslash) applies before the Exec quoting rule, and "%"
    # starts a field code.
    return quoted.replace("\\", "\\\\").replace("%", "%%")


def systemd_quote(arg: str, exec_line: bool = False) -> str:
    """One double-quoted word for a systemd unit file (specifiers and, in Exec*, $ escaped)."""
    out = arg.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("%", "%%")
    if exec_line:
        out = out.replace("$", "$$")
    return f'"{out}"'


def _source_root() -> Path | None:
    """If openExpress runs from a source checkout, the directory to put on PYTHONPATH."""
    import site

    root = Path(__file__).resolve().parent.parent
    installed = {Path(p).resolve() for p in site.getsitepackages() + [site.getusersitepackages()]}
    return None if root in installed else root


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
    root = _source_root()
    env = f"PYTHONPATH={root}" if root else ""
    args = (["env", env] if env else []) + [sys.executable, "-m", "openexpress", "gui"]
    exec_gui = " ".join(desktop_exec_quote(a) for a in args)
    exec_watch = " ".join([systemd_quote(sys.executable, exec_line=True), "-m", "openexpress",
                           "watch"])
    desktop = desktop_entry(exec_gui)
    desktop_watch = desktop_entry(exec_gui, background=True)
    # systemd unit files have no localized keys: Description stays in English.
    service = f"""[Unit]
Description={SERVICE_DESCRIPTION}
After=graphical-session.target
PartOf=graphical-session.target

[Service]
Type=simple
{('Environment=' + systemd_quote(env)) if env else ''}
ExecStart={exec_watch}
Restart=on-failure
RestartSec=10

[Install]
WantedBy=graphical-session.target
"""
    result = {
        data / "applications" / f"{DESKTOP_ID}.desktop": desktop,
        data / "applications" / f"{DESKTOP_ID}.Watch.desktop": desktop_watch,
        config / "systemd" / "user" / SERVICE: service,
        data / "icons" / "hicolor" / "scalable" / "apps" / f"{DESKTOP_ID}.svg": ICON.read_text(),
    }
    symbolic = ICON.with_name(f"{DESKTOP_ID}-symbolic.svg")
    if symbolic.is_file():
        result[data / "icons" / "hicolor" / "symbolic" / "apps" /
               f"{DESKTOP_ID}-symbolic.svg"] = symbolic.read_text()
    return result


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


def flatpak_installed() -> bool:
    """True when the Flatpak of openExpress is installed (its launcher would be shadowed)."""
    import shutil
    import subprocess

    if not shutil.which("flatpak"):
        return False
    res = subprocess.run(["flatpak", "info", DESKTOP_ID], capture_output=True, check=False)
    return res.returncode == 0


def _service_wants_link() -> Path:
    config = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return config / "systemd" / "user" / "graphical-session.target.wants" / SERVICE


def uninstall(dry_run: bool) -> list[Path]:
    """Disables the service, then removes the launcher, icon and service written by install()
    (and legacy files)."""
    import shutil
    import subprocess

    link = _service_wants_link()
    paths_ = [p for p in files() if p.exists()]
    if link.exists() or link.is_symlink():
        paths_.append(link)
    paths_ += legacy_files()
    if not dry_run:
        if shutil.which("systemctl"):
            try:
                subprocess.run(["systemctl", "--user", "disable", "--now", SERVICE],
                               capture_output=True, timeout=30, check=False)
            except (OSError, subprocess.SubprocessError):
                pass
        for p in paths_:
            p.unlink(missing_ok=True)
    return paths_
