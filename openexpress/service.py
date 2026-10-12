"""High-level operations shared by the GUI and the background service."""

from __future__ import annotations

import collections
import json
import time
import contextlib
import fcntl
from dataclasses import dataclass, field
from typing import Callable

from openexpress import paths, sync
from openexpress.auth import store
from openexpress.connect import (
    ApiError, ClockSkew, GarminClient, NotAccountDevice, OwnershipError, OwnershipUnknown,
    Unauthorized,
)
from openexpress.device.storage import Storage
from openexpress.device.xmldevice import GarminDevice, parse
from openexpress.i18n import _, ngettext
from openexpress.settings import Settings, SetupError, load_consumer

Progress = Callable[[str], None]


@dataclass
class DeviceSession:
    storage: Storage
    device: GarminDevice
    settings: Settings
    client: GarminClient
    # Set by check_session(): why account features are unavailable for this watch.
    ownership_error: Exception | None = None
    # The account's device list from that check (model names for the window).
    account_devices: list[dict] | None = None

    @property
    def logged_in(self) -> bool:
        return self.client.auth is not None

    @property
    def state(self) -> sync.SyncState:
        return sync.SyncState(self.device.unit_id)

    @property
    def first_sync(self) -> bool:
        return not self.state.file.exists()


def make_client(settings: Settings) -> GarminClient:
    try:
        consumer = load_consumer()
    except SetupError:
        consumer = None
    try:
        auth = store.load()
        if auth.environment != settings.environment:
            auth = None
    except store.NotLoggedIn:
        auth = None
    return GarminClient(settings, consumer, auth)


def open_session(device_uri: str | None = None, storage: Storage | None = None) -> DeviceSession:
    if storage is None:
        from openexpress.device.gio_storage import open_device_storage

        storage = open_device_storage(device_uri)
    device = parse(storage.read("GARMIN/GarminDevice.xml"))
    settings = Settings.load()
    return DeviceSession(storage, device, settings, make_client(settings))


def _unit_id(value) -> int | None:
    """A unit ID from the server: a positive int or a string of digits; else ignored."""
    if isinstance(value, str) and value.isascii() and value.isdigit():
        value = int(value)
    return value if type(value) is int and value > 0 else None


def require_account_device(client: GarminClient, unit_id: int) -> list[dict]:
    """Allow account operations on this watch only if the account lists it.

    Returns the account's device list that was fetched for the check.

    Asks Garmin Connect every time (no cache, no override). Raises NotAccountDevice
    when the watch is not on the list and OwnershipUnknown when the list cannot be
    fetched: the watch is never assumed to be the user's. An expired login or a wrong
    clock is raised as such, so the user gets the usual advice.
    """
    if client.verified_unit_id != unit_id:
        client.verified_unit_id = None
    # While the same watch is checked again, tasks sharing the client (pictures, icons)
    # keep working; the result replaces the old one only when the answer is in.
    verified = None
    try:
        try:
            devices = client.account_devices()
        except (Unauthorized, ClockSkew):
            raise
        except ApiError as e:
            raise OwnershipUnknown(
                _("Could not check whether this device belongs to your Garmin account "
                  "({error}). Nothing was synced.").format(error=e)) from e
        if unit_id not in {_unit_id(d.get("unitid")) for d in devices}:
            raise NotAccountDevice(
                _("This device is not registered to your Garmin account. To use it with "
                  "Garmin Connect here, sign in to the account it belongs to or add the "
                  "device to your account in Garmin Connect."))
        verified = unit_id
        return devices
    finally:
        client.verified_unit_id = verified


@dataclass
class PrimaryTraining:
    """The account's primary training device, seen from the connected device."""

    unit_id: int | None  # None: the account has none set
    name: str  # its model name from the account's device list; "" when it is not listed
    is_this_device: bool
    capable: bool | None  # whether the connected device can be one; None when unknown


class PrimaryTrainingError(Exception):
    """The primary training device cannot be changed to the connected device."""


def primary_training_status(client: GarminClient, device: GarminDevice,
                            devices: list[dict] | None = None) -> PrimaryTraining:
    """Read which device is the primary training device. Changes nothing on the account.

    Without `devices` (the account's device list from an ownership check that was just
    made) the ownership check is run here.
    """
    if devices is None:
        devices = require_account_device(client, device.unit_id)
    raw = client.primary_training_device().get("deviceid")
    unit_id = _unit_id(raw)
    if unit_id is None and raw not in (None, 0, "0", ""):
        raise ApiError(_("{what}: unexpected response from the server").format(
            what=_("Primary training device")))
    by_id = {_unit_id(d.get("unitid")): d for d in devices}
    entry = by_id.get(unit_id) if unit_id is not None else None
    name = (entry.get("productdisplayname") or entry.get("displayname") or "") if entry else ""
    capable = by_id.get(device.unit_id, {}).get("primarytrainingcapable")
    return PrimaryTraining(unit_id, name if isinstance(name, str) else "",
                           unit_id is not None and unit_id == device.unit_id,
                           capable if isinstance(capable, bool) else None)


def set_primary_training(client: GarminClient, device: GarminDevice,
                         apply: bool = False) -> PrimaryTraining:
    """Make the connected device the account's primary training device.

    Returns the setting as it was before. Without `apply` nothing is changed, but the same
    refusals are raised. The ownership check is always made anew, and after the change the
    setting is read back: an accepted request that changed nothing is reported as an error.
    """
    devices = require_account_device(client, device.unit_id)
    before = primary_training_status(client, device, devices)
    if before.is_this_device:
        return before
    if before.capable is False:
        raise PrimaryTrainingError(
            _("{device} cannot be a primary training device.").format(device=device.description))
    if before.capable is None:
        # Like the ownership check: never assume in favour of a change.
        raise PrimaryTrainingError(
            _("Could not tell whether {device} can be a primary training device. "
              "Nothing was changed.").format(device=device.description))
    if apply:
        client.set_primary_training_device(device.unit_id)
        after = primary_training_status(client, device, devices)
        if not after.is_this_device:
            raise PrimaryTrainingError(
                _("Garmin Connect accepted the request, but the primary training device "
                  "did not change."))
    return before


def check_session(s: DeviceSession) -> DeviceSession:
    """For the window: run the ownership check and keep the refusal instead of raising.

    A rejected login makes the session logged out, so the window asks to log in again.
    """
    s.ownership_error = None
    s.account_devices = None
    if s.logged_in:
        try:
            s.account_devices = require_account_device(s.client, s.device.unit_id)
        except Unauthorized:
            s.client.auth = None
        except (OwnershipError, ClockSkew) as e:
            s.ownership_error = e
    return s


@dataclass
class SyncSummary:
    uploaded: int = 0
    skipped_known: int = 0
    downloaded: int = 0
    errors: list[str] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


class SyncBusy(Exception):
    pass


@contextlib.contextmanager
def sync_lock():
    """Inter-process lock (the GUI and the background service never sync at the same time)."""
    path = paths.data_dir() / "sync.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SyncBusy(_("A sync is already running in another process.")) from None
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def run_sync(
    s: DeviceSession,
    first_run: str | None = None,
    progress: Progress = lambda m: None,
    include_diagnostics: bool = False,
) -> SyncSummary:
    """Full sync. `first_run`: 'baseline' or 'all' for the first time."""
    with sync_lock():
        summary = _run_sync(s, first_run, progress, include_diagnostics)
    save_last_sync(s.device.unit_id, summary)
    return summary


def last_sync_path():
    return paths.data_dir() / "last_sync.json"


def save_last_sync(unit_id: int, summary: "SyncSummary") -> None:
    data = {"time": time.time(), "unit_id": unit_id, "uploaded": summary.uploaded,
            "downloaded": summary.downloaded, "skipped_known": summary.skipped_known,
            "errors": summary.errors, "lines": summary.lines}
    path = last_sync_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False))
    tmp.replace(path)


def record_sync(unit_id: int, uploaded: int, downloaded: int, skipped_known: int,
                errors: list[str], lines: list[str]) -> None:
    """Store a sync result made outside run_sync (e.g. by the CLI) as the last sync."""
    save_last_sync(unit_id, SyncSummary(uploaded=uploaded, skipped_known=skipped_known,
                                        downloaded=downloaded, errors=list(errors),
                                        lines=list(lines)))


def load_last_sync(unit_id: int | None = None) -> dict | None:
    try:
        data = json.loads(last_sync_path().read_text())
    except (OSError, ValueError):
        return None
    if unit_id is not None and data.get("unit_id") != unit_id:
        return None
    return data


def _run_sync(s, first_run, progress, include_diagnostics) -> SyncSummary:
    if not s.logged_in:
        raise store.NotLoggedIn(_("Not logged in to Garmin Connect."))
    require_account_device(s.client, s.device.unit_id)
    out = SyncSummary()
    state = s.state
    progress(_("Fetching configuration from the Garmin server…"))
    cfg = s.client.upload_settings(s.device.unit_id)
    if s.first_sync:
        if first_run == "baseline":
            n = sync.mark_baseline(s.device, s.storage, cfg, state, s.client)
            out.lines.append(ngettext("Marked {count} existing file as already uploaded.",
                                      "Marked {count} existing files as already uploaded.",
                                      n).format(count=n))
        elif first_run != "all":
            raise FirstSyncChoiceNeeded()
        state = s.state
    progress(_("Looking for new files on the device…"))
    plan = sync.plan_uploads(s.device, s.storage, cfg, state,
                             include_diagnostics=include_diagnostics)
    counts = collections.Counter(c.datatype for c in plan.candidates)
    if plan.candidates:
        types = ", ".join(f"{k}×{v}" for k, v in sorted(counts.items()))
        out.lines.append(_("New files: {types}").format(types=types))
        rep = sync.run_uploads(plan, s.device, s.storage, s.client, state, progress)
        out.uploaded = len(rep.uploaded)
        out.skipped_known = len(rep.duplicates) + len(rep.already_in_connect)
        out.errors += [f"{label}: {err}" for label, err in rep.failed]
        out.lines.append(_("Uploaded {uploaded}, already in Connect {known}, "
                           "rejected {rejected}, errors {errors}.").format(
            uploaded=out.uploaded, known=out.skipped_known,
            rejected=len(rep.rejected), errors=len(rep.failed)))
        if rep.tracker_conflict:
            n = len(rep.tracker_conflict)
            out.lines.append(ngettext(
                "Skipped {count} file: another device is your active tracker in Garmin Connect.",
                "Skipped {count} files: another device is your active tracker in Garmin Connect.",
                n).format(count=n))
    else:
        out.lines.append(_("No new data to upload."))
    progress(_("Checking for files from Garmin Connect…"))
    messages = s.client.device_messages(s.device.unit_id)
    items = sync.plan_downloads(s.device, messages)
    todo = [i for i in items if not i.unsupported and not i.handled_by]
    if todo:
        drep = sync.run_downloads(todo, s.storage, s.client, progress, state=state)
        out.downloaded = len(drep.written)
        out.errors += [f"{label}: {err}" for label, err in drep.failed]
        out.errors += [_("{name}: written, but Garmin Connect was not notified (will retry)")
                       .format(name=label) for label in drep.ack_failed]
        out.lines.append(ngettext("Wrote {count} file from Connect to the device.",
                                  "Wrote {count} files from Connect to the device.",
                                  out.downloaded).format(count=out.downloaded))
    if any(i.handled_by == "Connect IQ" for i in items):
        out.lines += _install_queued_apps(s, messages, progress, out.errors)
    if any(i.handled_by == "TrueUp" for i in items):
        out.lines += _trueup(s, messages, progress, out.errors)
    skipped = [i for i in items if i.unsupported]
    if skipped:
        out.lines.append(ngettext(
            "Skipped {count} item from Connect (not accepted by the device).",
            "Skipped {count} items from Connect (not accepted by the device).",
            len(skipped)).format(count=len(skipped)))
    state.save()
    return out


def _install_queued_apps(s, messages, progress, errors) -> list[str]:
    from openexpress import ciq

    try:
        known = {a.app_id: a for a in ciq.StoreApi(s.client).account_apps(
            s.device, s.settings.locale.replace("_", "-"))}
    except Exception:  # noqa: BLE001 - names are only cosmetic
        known = {}
    try:
        rep = ciq.install_queued(s.device, s.storage, s.client, messages, known,
                                 progress=progress)
    except ciq.CiqError as e:
        # E.g. an unreadable app list: the rest of the sync is not affected.
        errors.append(f"Connect IQ: {e}")
        return []
    errors += [f"Connect IQ {name}: {err}" for name, err in rep.failed]
    lines = []
    if rep.installed:
        lines.append(_("Installed Connect IQ apps: {names}.").format(
            names=", ".join(rep.installed)))
    if rep.for_phone:
        n = len(rep.for_phone)
        lines.append(ngettext(
            "{count} Connect IQ app waits in the queue: openExpress cannot install apps on "
            "this device over USB yet.",
            "{count} Connect IQ apps wait in the queue: openExpress cannot install apps on "
            "this device over USB yet.", n).format(count=n))
    return lines


def _trueup(s, messages, progress, errors) -> list[str]:
    from openexpress import trueup

    try:
        res = trueup.run(s.device, s.storage, s.client, messages, progress)
    except Exception as e:  # noqa: BLE001
        errors.append(f"TrueUp: {e}")
        return []
    if res.written:
        return [_("TrueUp: daily totals aligned with your other Garmin devices.")]
    return [_("TrueUp: {note}.").format(note=res.note)] if res.note else []


class FirstSyncChoiceNeeded(Exception):
    """First sync: either 'baseline' or 'all' must be chosen."""
