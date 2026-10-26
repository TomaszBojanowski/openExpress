"""Device report for GitHub issues: what is useful for diagnosis, without identifiers.

The report never contains the unit ID, the serial number (also part of the MTP address),
the account e-mail or file names. It is always in English because it goes to GitHub.
"""

from __future__ import annotations

import os
import platform
import re
import sys
from pathlib import Path
from urllib.parse import quote, urlencode

from openexpress import __version__, paths
from openexpress.device.storage import Storage
from openexpress.device.xmldevice import GarminDevice
from openexpress.i18n import human_size

ISSUE_FORM = "https://github.com/TomaszBojanowski/openExpress/issues/new"
ISSUE_TEMPLATE = "device_report.yml"
ISSUE_URL = f"{ISSUE_FORM}?template={ISSUE_TEMPLATE}"
# Longer addresses are refused by web servers (GitHub answers 414 somewhere above 8 kB).
MAX_URL = 6000

# (label, test on the set of data type names)
FEATURES = [
    ("Activities", lambda d: "FIT_TYPE_4" in d),
    ("Monitoring / sleep", lambda d: bool({"FIT_TYPE_32", "FIT_TYPE_49"} & d)),
    ("TrueUp", lambda d: "FIT_TYPE_39" in d),
    ("Courses / workouts to the device", lambda d: bool({"FIT_TYPE_5", "FIT_TYPE_6"} & d)),
    ("Connect IQ", lambda d: "IQAppsConfiguration" in d),
    ("Music", lambda d: "MEDIA_TRACK" in d),
    ("Maps", lambda d: bool({"PreProgrammedNamedMaps", "BaseMaps", "NMaps"} & d)),
    ("Ephemeris (GPS data)", lambda d: any(x.startswith("Ephemeris") for x in d)),
    ("Wi-Fi configuration", lambda d: "WiFiConfiguration" in d),
    ("Device HTTP proxy (ExpressRequest)", lambda d: "ExpressRequest" in d),
]


LOG_LINES = 200


def with_log(text: str, lines: list[str], crash: list[str] | None = None) -> str:
    """The report followed by the end of the program's log and, when there is one, the end
    of crash.log (Python's trace of a crash; both redacted already)."""
    body = "\n".join(lines) if lines else "(no log file)"
    out = (f"{text}\n### Log (last {len(lines)} lines)\n```\n{body}\n```\n" if lines
           else f"{text}\n### Log\n{body}\n")
    if crash:
        out += (f"### Crash log (last {len(crash)} lines of crash.log)\n```\n"
                + "\n".join(crash) + "\n```\n")
    return out


def form_log(lines: list[str], crash: list[str] | None = None) -> str:
    """The end of the log (and of crash.log, when there is one) for the "Log" field of the
    issue forms, which shows it as code itself: the lines alone, without the headings and
    code fences of with_log. An issue opened from the window never has the log in its
    address; the window puts this in the clipboard (#18, #19)."""
    out = "\n".join(lines) + "\n" if lines else "(no log file)\n"
    if crash:
        out += (f"\n# Crash log (last {len(crash)} lines of crash.log)\n"
                + "\n".join(crash) + "\n")
    return out


def issue_url(text: str, model: str | None) -> str | None:
    """The address of a new device report on GitHub with the title and the "Device report"
    field filled in (`report` is the id of that field in the issue form). None when the
    address, encoded, would be longer than MAX_URL characters.

    Opening it sends the text to GitHub with the request, so it gets only the report, never
    the log; nothing is published until the issue is submitted."""
    query = urlencode({"template": ISSUE_TEMPLATE, "title": f"[Device] {model or 'unknown'}",
                       "report": text}, quote_via=quote)
    url = f"{ISSUE_FORM}?{query}"
    return url if len(url) <= MAX_URL else None


def _os_name() -> str:
    for path in ("/run/host/os-release", "/etc/os-release"):
        try:
            text = Path(path).read_text()
        except OSError:
            continue
        m = re.search(r'^PRETTY_NAME="?([^"\n]*)"?', text, re.MULTILINE)
        if m:
            return m.group(1)
    return platform.system()


def _install_type() -> str:
    if paths.in_flatpak():
        origin, commit = paths.flatpak_source()
        if origin is None:
            words = "Flatpak, origin unknown"
        elif origin.endswith("-origin"):
            words = "Flatpak bundle"  # a .flatpak file installs from a remote "…-origin"
        else:
            words = f'Flatpak from the "{origin}" repository'
        return f"{words}, commit {commit}" if commit else words
    here = Path(__file__).resolve()
    return "pip" if "site-packages" in here.parts or "dist-packages" in here.parts else "source"


def _size(n: int | None) -> str:
    # The same numbers as in the window, but always with a point: the report is in English.
    return "unknown" if not n else human_size(n, localize=False)


def build(device: GarminDevice, storage: Storage, logged_in: bool | None = None,
          space: tuple[int | None, int | None] | None = None) -> str:
    """The report of the device. `space`: (free space, capacity) already read (the window
    holds them with its session; None in either is "unknown"); without it they are asked of
    the storage (the command line)."""
    free, capacity = space if space is not None else (storage.free_space(), storage.capacity())
    datatypes = set(device.datatypes())
    features = "\n".join(f"- [{'x' if test(datatypes) else ' '}] {label}"
                         for label, test in FEATURES)
    desktop = os.environ.get("XDG_CURRENT_DESKTOP") or "unknown"
    session = os.environ.get("XDG_SESSION_TYPE") or "unknown"
    lines = [
        "### Device",
        f"- Model: {device.description or 'unknown'}",
        f"- Part number: {device.part_number or 'unknown'}",
        f"- Software: {device.software_version_str}",
        # Vendor and product ID of the USB device; never taken from the MTP address.
        f"- USB ID: {getattr(storage, 'usb_id', None) or 'unknown'}",
        f"- Storage: {_size(capacity)} total, {_size(free)} free",
        f"- Data types: {len(datatypes)}, components: {len(device.update_files)}",
        "",
        "### Features declared by the device",
        features,
        "",
        "### System",
        f"- openExpress: {__version__} ({_install_type()})",
        f"- OS: {_os_name()}",
        f"- Desktop: {desktop} ({session})",
        f"- Python: {sys.version.split()[0]}",
    ]
    if logged_in is not None:
        lines.append(f"- Logged in to Garmin Connect: {'yes' if logged_in else 'no'}")
    return "\n".join(lines) + "\n"
