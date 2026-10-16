"""The program's own log file.

One file in the state directory (`~/.local/state/openexpress/openexpress.log`; in the
Flatpak under `~/.var/app/…/.local/state`), shared by the command-line tool, the window and
the background watcher, kept to three files of about 1 MB each.

Nothing personal may end up in it. Every line, including tracebacks and messages of other
libraries, goes through `redact()` before it is written: values the program knows to be
private (`hide()`) are replaced, and so is everything that looks like an identifier, an
address of an MTP device, an e-mail address or a credential. Code that logs must still not
pass file names, activity names, locations or file contents. What the log records print on
the terminal (`-v`, the journal of the watcher) goes through the same filter; messages the
program prints for the user (e.g. the addresses to choose from with `--device-uri`) do not.

`OPENEXPRESS_LOG=debug` writes more to the file, `OPENEXPRESS_LOG=off` writes no file.
"""

from __future__ import annotations

import fcntl
import getpass
import logging
import logging.handlers
import os
import platform
import re
import secrets
import sys
from pathlib import Path

from openexpress import __version__, i18n, paths
from openexpress.i18n import _

LOG_NAME = "openexpress.log"
MAX_BYTES = 1_000_000
OLD_FILES = 2  # besides the current one
ENV = "OPENEXPRESS_LOG"
FORMAT = "%(asctime)s %(role)s[%(run)s] %(levelname)s %(name)s: %(message)s"
# Tells the runs apart in the shared file, where a PID cannot: in the Flatpak every process
# has PID 2. Random, made anew for every process and written nowhere else.
RUN = secrets.token_hex(2)

_KEYS = (r"oauth_\w+|ticket|\w*token\w*|password|passwd|\w*secret\w*|signature|consumer_key|"
         r"session\w*")
_PATTERNS = [
    # An MTP address carries the serial number of the device.
    (re.compile(r"mtp://[^\s/'\"]+"), "mtp://<device>"),
    (re.compile(r"mtp:host=[^\s/'\"]+"), "mtp:host=<device>"),
    # Any other address but a web one: "file:///run/media/<user>/<stick>/…" (`gio copy` puts
    # it in its errors), "smb://", "sftp://<user>@<host>/…". They are percent-encoded, so one
    # ends at a space, a quote or the ": " that follows it.
    (re.compile(r"(?<![\w+.-])(?!(?:mtp|https?)://)([A-Za-z][A-Za-z0-9+.-]*)://"
                r"(?:[^\s'\":]|:(?!\s|$))*"), r"\1://<path>"),
    # Media files are named by the user: only the folder is kept. These names are the
    # fallback; the folders the connected device declares are added by `hide_device()`.
    (re.compile(r"(?i)\b(music|podcasts?|audiobooks?|playlists?)/[^\n'\":]*"), r"\1/<file>"),
    # Local paths (error messages carry them), up to a quote or the ": " that follows a path.
    # The program's own files and system folders stay readable in tracebacks.
    (re.compile(r"(?<![\w.:/<>~-])(?:~/|/(?!usr/|app/|lib(?:64)?/|sys/|dev/|proc/))"
                r"[^\n'\":]*(?::(?! )[^\n'\":]*)*"), "<path>"),
    (re.compile(r"(?i)\b(authorization|cookie|set-cookie)\b(\"?\s*[:=]\s*).*"), r"\1\2<hidden>"),
    # JSON ("key": "value") and Python's own repr ('key': 'value', key='value') alike.
    (re.compile(rf"(?i)\b({_KEYS})\b([\"']?\s*[=:]\s*[\"']?)[^\s&\"',;]+"), r"\1\2<hidden>"),
    (re.compile(r"\bST-[\w-]+"), "<ticket>"),
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+"), "<email>"),
    (re.compile(r"\b[0-9A-Fa-f]{16,}\b"), "<hex>"),
    # Long mixed strings of letters and digits: keys and tokens. Without "/" (a path on the
    # device is not a key), except in a string long enough to be base64.
    (re.compile(r"(?<![\w+-])(?=[\w+-]*\d)(?=[\w+-]*[a-z])(?=[\w+-]*[A-Z])[\w+-]{24,}={0,2}"),
     "<hidden>"),
    (re.compile(r"(?<![A-Za-z0-9+/])(?=[A-Za-z0-9+/]*\d)(?=[A-Za-z0-9+/]*[a-z])"
                r"(?=[A-Za-z0-9+/]*[A-Z])[A-Za-z0-9+/]{40,}={0,2}"), "<hidden>"),
    # Unit IDs, serial numbers, profile numbers, activity IDs…
    (re.compile(r"\d{7,}"), "<number>"),
]


def _user_rule(name: str | None) -> re.Pattern | None:
    """The name of the system user as a whole word. Not inside a longer word or a domain
    (an account "garmin" leaves "connectapi.garmin.com" and "Garmin" alone), and not when
    it is shorter than 3 characters, which would hide too much."""
    if not name or len(name) < 3:
        return None
    return re.compile(rf"(?<![\w.<-]){re.escape(name)}(?![\w>-]|\.\w)")


def _system_user() -> str | None:
    try:
        return getpass.getuser()
    except Exception:  # noqa: BLE001 - no name to hide; the other rules still apply
        return None


_PACKAGE_PARENT = str(Path(__file__).resolve().parent.parent) + os.sep
_user = _user_rule(_system_user())  # asked once
_hidden: dict[str, str] = {}
_media: dict[str, re.Pattern] = {}  # media folders declared by the devices seen
_handlers: list[logging.Handler] = []


def hide(value, label: str) -> None:
    """Never write `value` (e.g. the unit ID of the connected device) to the log."""
    text = str(value) if value is not None else ""
    if len(text) >= 4:
        _hidden[text] = f"<{label}>"


def hide_device(device) -> None:
    """Never write what is private about this device: its unit ID, and the names of the
    files in the folders it declares for music, podcasts, audiobooks and playlists."""
    hide(device.unit_id, "unit")
    for f in device.files:
        folder = (f.path or "").strip("/")
        if f.datatype.startswith("MEDIA_") and folder:
            _media[folder.casefold()] = re.compile(
                r"(?i)(?<![\w])(" + re.escape(folder) + r")/[^\n'\":]*")


def redact(text: str) -> str:
    """`text` without anything that identifies the user, the account or a device."""
    for value, label in sorted(_hidden.items(), key=lambda item: -len(item[0])):
        text = text.replace(value, label)
    for folder in sorted(_media, key=len, reverse=True):
        text = _media[folder].sub(r"\1/<file>", text)
    text = text.replace(_PACKAGE_PARENT, "")  # tracebacks: "openexpress/ciq.py"
    home = str(Path.home())
    if len(home) > 1:
        text = text.replace(home, "~")
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    if _user is not None:
        text = _user.sub("<user>", text)
    return text


class _Redacting(logging.Formatter):
    """Formats the whole record (message, traceback, stack) and redacts the result: for the
    log file and for the terminal alike (e.g. `-v`, or the journal of the watcher)."""

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


class _Formatter(_Redacting):
    """The log file: every line names the part of the program and the run."""

    def __init__(self, fmt: str, role: str):
        super().__init__(fmt)
        self.role = role

    def format(self, record: logging.LogRecord) -> str:
        record.role = self.role
        record.run = RUN
        return super().format(record)


class _LogFile(logging.handlers.WatchedFileHandler):
    """Appends to the log file, which several processes share.

    The file is rotated by whichever process finds it too large, under a lock; the others
    notice that the file was replaced and open the new one (`WatchedFileHandler`)."""

    def _open(self):
        fd = os.open(self.baseFilename, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        return os.fdopen(fd, "a", encoding="utf-8", errors="replace")

    def emit(self, record: logging.LogRecord) -> None:
        try:
            if self.stream is not None and self.stream.tell() >= MAX_BYTES:
                rotate(Path(self.baseFilename))
        except (OSError, ValueError):
            pass
        super().emit(record)


def rotate(path: Path) -> None:
    """Start a new log file when the current one is full, keeping `OLD_FILES` old ones."""
    with open(path.with_name(path.name + ".lock"), "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            if path.stat().st_size < MAX_BYTES:
                return  # another process has just done it
        except OSError:
            return
        for n in range(OLD_FILES, 0, -1):
            older = path.with_name(f"{path.name}.{n}")
            newer = path.with_name(f"{path.name}.{n - 1}") if n > 1 else path
            if newer.exists():
                os.replace(newer, older)


def size(n: int | None) -> str:
    """A size for the log: the exact number of bytes, with the digits in groups of three
    ("6_657_384_448 B"). Written like that the filter does not take it for an identifier
    (it replaces every run of seven or more digits), and `int()` still reads it."""
    if n is None:
        return "?"
    return f"{n:_} B"


def counts(names) -> str:
    """`names` counted, e.g. "FIT_TYPE_4×2, FIT_TYPE_32×1"; "-" when there are none."""
    seen: dict[str, int] = {}
    for name in names:
        seen[str(name)] = seen.get(str(name), 0) + 1
    return ", ".join(f"{k}×{v}" for k, v in sorted(seen.items())) or "-"


def environment() -> str:
    """What the start line says about the program: its version, how it is packaged, the
    Python version, the language it runs in and the PID. Nothing about the user or the
    computer."""
    return (f"openExpress {__version__}, {'Flatpak' if paths.in_flatpak() else 'source'}, "
            f"Python {platform.python_version()}, language {i18n.current_language()}, "
            f"pid {os.getpid()}")


def log_path() -> Path:
    return paths.state_dir() / LOG_NAME


def shown_path() -> str:
    """The log file as it is shown to the user: with ~ for the home folder."""
    path, home = str(log_path()), str(Path.home())
    return "~" + path[len(home):] if len(home) > 1 and path.startswith(home + os.sep) else path


def tail(count: int = 200) -> list[str]:
    """The last `count` lines of the log, redacted once more (the rules may have become
    stricter since a line was written). Empty when there is no log."""
    lines: list[str] = []
    path = log_path()
    for file in (path, path.with_name(path.name + ".1")):  # the current file, then the one before
        if len(lines) >= count:
            break
        try:
            text = file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        lines = text.splitlines() + lines
    return [redact(line) for line in lines[-count:]]


def file_level(verbose: bool = False) -> int | None:
    """The level of the log file; None when the user turned the file off."""
    choice = os.environ.get(ENV, "").strip().lower()
    if choice in ("off", "0", "no", "none"):
        return None
    return logging.DEBUG if verbose or choice == "debug" else logging.INFO


def close() -> None:
    root = logging.getLogger()
    for handler in _handlers:
        root.removeHandler(handler)
        handler.close()
    _handlers.clear()


def setup(role: str, verbose: bool = False, stderr_level: int | None = None,
          stderr_format: str = "%(levelname)s %(name)s: %(message)s") -> Path | None:
    """Send log records to stderr (as before) and to the log file. `role` names the part of
    the program in every line: cli, gui or watch. Returns the log file, if there is one."""
    close()
    root = logging.getLogger()
    if stderr_level is None:
        stderr_level = logging.DEBUG if verbose else logging.WARNING
    err = logging.StreamHandler(sys.stderr)
    err.setLevel(stderr_level)
    err.setFormatter(_Redacting(stderr_format))  # the same filter as the log file
    handlers: list[logging.Handler] = [err]
    path = None
    level = file_level(verbose)
    if level is not None:
        try:
            path = log_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            file = _LogFile(path, encoding="utf-8")
            file.setLevel(level)
            file.setFormatter(_Formatter(FORMAT, role))
            handlers.append(file)
        except OSError as e:
            # The program works without its log file.
            print(_("No log file: {error}").format(error=e.strerror or e), file=sys.stderr)
            path = None
    for handler in handlers:
        root.addHandler(handler)
    _handlers.extend(handlers)
    root.setLevel(min(h.level for h in handlers))
    return path
