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

from openexpress import __version__
from openexpress.device.storage import Storage
from openexpress.device.xmldevice import GarminDevice

ISSUE_URL = ("https://github.com/TomaszBojanowski/openExpress/issues/new"
             "?template=device_report.yml")

# (label, test on the set of data type names)
FEATURES = [
    ("Activities", lambda d: "FIT_TYPE_4" in d),
    ("Monitoring / sleep", lambda d: bool({"FIT_TYPE_32", "FIT_TYPE_49"} & d)),
    ("TrueUp", lambda d: "FIT_TYPE_39" in d),
    ("Courses / workouts to the watch", lambda d: bool({"FIT_TYPE_5", "FIT_TYPE_6"} & d)),
    ("Connect IQ", lambda d: "IQAppsConfiguration" in d),
    ("Music", lambda d: "MEDIA_TRACK" in d),
    ("Maps", lambda d: bool({"PreProgrammedNamedMaps", "BaseMaps", "NMaps"} & d)),
    ("Ephemeris (GPS data)", lambda d: any(x.startswith("Ephemeris") for x in d)),
    ("Wi-Fi configuration", lambda d: "WiFiConfiguration" in d),
    ("Device HTTP proxy (ExpressRequest)", lambda d: "ExpressRequest" in d),
]


def _os_name() -> str:
    for path in ("/run/host/os-release", "/etc/os-release"):
        try:
            text = Path(path).read_text()
        except OSError:
            continue
        m = re.search(r'^PRETTY_NAME="?([^"\n]*)"?', text, re.M)
        if m:
            return m.group(1)
    return platform.system()


def _install_type() -> str:
    if Path("/.flatpak-info").exists():
        return "Flatpak"
    here = Path(__file__).resolve()
    return "pip" if "site-packages" in here.parts or "dist-packages" in here.parts else "source"


def usb_product_id(storage_label: str) -> str:
    """'mtp://091e_51b4_<serial>/…' -> '091e:51b4' (model ID only, never the serial)."""
    m = re.search(r"mtp://([0-9a-fA-F]{4})_([0-9a-fA-F]{4})", storage_label or "")
    return f"{m.group(1).lower()}:{m.group(2).lower()}" if m else "unknown"


def _size(n: int | None) -> str:
    return "unknown" if not n else f"{n / 1024 ** 3:.1f} GB"


def build(device: GarminDevice, storage: Storage, logged_in: bool | None = None) -> str:
    datatypes = set(device.datatypes())
    features = "\n".join(f"- [{'x' if test(datatypes) else ' '}] {label}"
                         for label, test in FEATURES)
    desktop = os.environ.get("XDG_CURRENT_DESKTOP") or "unknown"
    session = os.environ.get("XDG_SESSION_TYPE") or "unknown"
    lines = [
        "### Watch",
        f"- Model: {device.description or 'unknown'}",
        f"- Part number: {device.part_number or 'unknown'}",
        f"- Software: {device.software_version_str}",
        f"- USB ID: {usb_product_id(getattr(storage, 'label', ''))}",
        f"- Storage: {_size(storage.capacity())} total, {_size(storage.free_space())} free",
        f"- Data types: {len(datatypes)}, components: {len(device.update_files)}",
        "",
        "### Features declared by the watch",
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
