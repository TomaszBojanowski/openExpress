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

# What the device lists for exchange with a computer: (label, test on the set of data type
# names). An empty box is no feature the device lacks: a watch may take its satellite data
# through its own requests (ExpressRequest), or its Wi-Fi networks from the phone.
FEATURES = [
    ("Activities", lambda d: "FIT_TYPE_4" in d),
    ("Monitoring / sleep", lambda d: bool({"FIT_TYPE_32", "FIT_TYPE_49"} & d)),
    ("TrueUp", lambda d: "FIT_TYPE_39" in d),
    ("Courses / workouts to the device", lambda d: bool({"FIT_TYPE_5", "FIT_TYPE_6"} & d)),
    ("Connect IQ", lambda d: "IQAppsConfiguration" in d),
    ("Music", lambda d: "MEDIA_TRACK" in d),
    ("Maps", lambda d: bool({"PreProgrammedNamedMaps", "BaseMaps", "NMaps"} & d)),
    ("Satellite data (EPO/CPE) from a computer",
     lambda d: any(x.startswith("Ephemeris") for x in d)),
    ("Wi-Fi networks set from a computer", lambda d: "WiFiConfiguration" in d),
    ("Requests to Garmin's servers through a computer (ExpressRequest)",
     lambda d: "ExpressRequest" in d),
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
    """The end of the log (and of crash.log, when there is one) to paste into the "Log" field
    of the issue forms or into a comment: the lines without the headings of with_log, between
    lines of tildes. An issue opened from the window never has the log in its address; the
    window puts this in the clipboard."""
    out = "\n".join(lines) + "\n" if lines else "(no log file)\n"
    if crash:
        out += (f"\n# Crash log (last {len(crash)} lines of crash.log)\n"
                + "\n".join(crash) + "\n")
    # Tildes make a block of code in a comment too; in the "Log" field, which GitHub shows as
    # code between backticks itself, they close nothing and are only two lines more. No line
    # of the log closes the fence: it is longer than any run of tildes a line begins with.
    longest = max((len(t) - len(t.lstrip("~")) for t in
                   (line.lstrip(" ") for line in out.splitlines())), default=0)
    fence = "~" * max(3, longest + 1)
    return f"{fence}\n{out}{fence}\n"


def issue_url(text: str, model: str | None) -> str | None:
    """The address of a new device report on GitHub with the title and the "Device report"
    field filled in; None when, encoded, it would be longer than MAX_URL characters. It holds
    only the report, never the log: opening it sends the text to GitHub, though nothing is
    published until the issue is submitted."""
    # "report" is the id of the "Device report" field in the issue form.
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


def _system(logged_in: bool | None) -> list[str]:
    """The section of the system, the same with a device or without."""
    desktop = os.environ.get("XDG_CURRENT_DESKTOP") or "unknown"
    session = os.environ.get("XDG_SESSION_TYPE") or "unknown"
    lines = [
        "### System",
        f"- openExpress: {__version__} ({_install_type()})",
        f"- OS: {_os_name()}",
        f"- Desktop: {desktop} ({session})",
        f"- Python: {sys.version.split()[0]}",
    ]
    if logged_in is not None:
        lines.append(f"- Logged in to Garmin Connect: {'yes' if logged_in else 'no'}")
    return lines


# The newest crashes the report lists.
CRASHES_SHOWN = 5


def _diagnostics(device: GarminDevice, storage: Storage, ask: bool, found) -> list[str]:
    """The section of the device's diagnostics: its crashes from every source (crashes.history)
    with the date, the software version and the error, its memory dumps and whether their copy
    is on. Never the program counter or the screen of a crash. `ask`: the command line, with
    what it read now (`found`, diagnostics.read_now; read here when not given); else what the
    page tells (status.crashes_known and status.dumps_now, from what the window read at the
    opening, `found`, diagnostics.AtOpen; None: not read yet)."""
    from openexpress import crashes, diagnostics, status

    if ask:
        read = found if found is not None else diagnostics.read_now(device, storage)
        errors_known = read.errors_known
        found_crashes = crashes.history(device.unit_id, read.entries if errors_known else None,
                                        test_drive=bool(getattr(storage, "test_drive", False)))
        dumps = ((len(read.dumps), sum(size for _p, size in read.dumps)) if read.dumps_known
                 else None)
    else:
        errors_known = found is not None and found.errors_known
        found_crashes = status.crashes_known(device, storage, found)
        dumps = status.dumps_now(device, storage, found)
    lines = ["### Diagnostics",
             f"- Crashes that restarted the device: {len(found_crashes)}"
             + ("" if errors_known else " (the error logs on the device not read)")]
    for crash in found_crashes[:CRASHES_SHOWN]:
        # Both codes where both logs hold the crash: the boot log's tells the same error as
        # the crashes only it holds.
        codes = [f"error {crash.error}"] if crash.error else []
        codes += [f"boot log error {crash.errnum}"] if crash.errnum else []
        lines.append(f"  - {crash.at:%Y-%m-%d %H:%M}: software {crash.software or 'unknown'}, "
                     + (", ".join(codes) or "error unknown"))
    if len(found_crashes) > CRASHES_SHOWN:
        lines.append(f"  - and {len(found_crashes) - CRASHES_SHOWN} earlier")
    if dumps is not None:
        count, total = dumps
        lines.append(f"- Memory dumps: {count}, {_size(total) if total else '0 B'}")
    else:
        lines.append("- Memory dumps: " + ("not known" if ask or found is not None
                                           else "not read yet"))
    lines.append(f"- Copy of the diagnostic files: {'on' if diagnostics.copy_on() else 'off'}")
    return lines


def build(device: GarminDevice, storage: Storage, logged_in: bool | None = None,
          space: tuple[int | None, int | None] | None = None, found=None) -> str:
    """The report of the device. `space`: (free space, capacity) already read (the window
    holds them with its session; None in either is "unknown"), and then nothing is asked of
    the device: its diagnostics are what the window read at the opening (`found`,
    diagnostics.AtOpen, or None) and the copies on this computer. Without `space` all of it is
    asked of the storage (the command line)."""
    ask = space is None
    free, capacity = space if space is not None else (storage.free_space(), storage.capacity())
    datatypes = set(device.datatypes())
    features = "\n".join(f"- [{'x' if test(datatypes) else ' '}] {label}"
                         for label, test in FEATURES)
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
        "### What the device exchanges with a computer",
        ("From its GarminDevice.xml. An empty box only means the device does not list this for "
         "exchange with a computer; the device itself may still have the feature."),
        features,
        "",
        *_diagnostics(device, storage, ask, found),
        "",
        *_system(logged_in),
    ]
    return "\n".join(lines) + "\n"


# Why no device was opened, by the state of the Garmin devices on USB (gio_storage.usb_state).
NOT_OPENED = {
    "absent": "no Garmin device on USB",
    "starting": "starting (in its transitional mode)",
    "present": "not ready (on USB, not offered over MTP)",
    "mass-storage": "a USB drive that is not mounted",
    "drive-off": "in its Garmin USB mode after its drive was ejected",
    "unsupported": "not supported (HID without MTP)",
}
# The classes of USB interfaces a Garmin device may have, by name.
INTERFACES = {"03": "HID", "06": "PTP/MTP", "08": "mass storage", "ff": "vendor-specific"}


def _interface(cls: str | None) -> str:
    if cls is None:
        return "unknown"
    return f"{INTERFACES[cls]} ({cls})" if cls in INTERFACES else f"other ({cls})"


def model_on_usb() -> str | None:
    """The name the first Garmin device on USB gives itself, for the title of the form when
    no device could be opened; None without one."""
    from openexpress.device import gio_storage

    return next((name for _usb_id, name, _classes in gio_storage.garmin_usb_summary() if name),
                None)


def build_without_device(error: BaseException | None = None,
                         logged_in: bool | None = None) -> str:
    """The report when no device could be opened (`error`: why, when one was tried): instead
    of the device, why none is open, the Garmin devices on USB (their USB ID, the name each
    gives itself and the classes of its interfaces; never a serial number) and whether gvfs
    has MTP; then the system, as with a device. Only /sys is read."""
    from openexpress.device import gio_storage

    state = gio_storage.usb_state()
    reason = ("several Garmin devices are connected"
              if isinstance(error, gio_storage.SeveralDevices) else NOT_OPENED.get(state, state))
    def described(usb_id: str, name: str | None, classes: list[str | None]) -> str:
        named = f' "{name}"' if name else ""
        interfaces = ", ".join(_interface(c) for c in classes) or "none"
        return f"{usb_id}{named}, interfaces: {interfaces}"

    devices = "; ".join(described(*d) for d in gio_storage.garmin_usb_summary()) or "none"
    lines = [
        "### Device",
        f"- Not opened: {reason}",
        f"- Garmin devices on USB: {devices}",
        f"- MTP support in gvfs: {'no' if gio_storage.mtp_missing() else 'yes'}",
        "",
        *_system(logged_in),
    ]
    return "\n".join(lines) + "\n"
