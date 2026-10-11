"""High-level operations shared by the GUI and the background service."""

from __future__ import annotations

import collections
import contextlib
import fcntl
from dataclasses import dataclass, field
from typing import Callable

from openexpress import paths, sync
from openexpress.auth import store
from openexpress.connect import GarminClient
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
        return _run_sync(s, first_run, progress, include_diagnostics)


def _run_sync(s, first_run, progress, include_diagnostics) -> SyncSummary:
    if not s.logged_in:
        raise store.NotLoggedIn(_("Not logged in to Garmin Connect."))
    out = SyncSummary()
    state = s.state
    progress(_("Fetching configuration from the Garmin server…"))
    cfg = s.client.upload_settings(s.device.unit_id)
    if s.first_sync:
        if first_run == "baseline":
            n = sync.mark_baseline(s.device, s.storage, cfg, state)
            out.lines.append(ngettext("Marked {count} existing file as already uploaded.",
                                      "Marked {count} existing files as already uploaded.",
                                      n).format(count=n))
        elif first_run != "all":
            raise FirstSyncChoiceNeeded()
        state = s.state
    progress(_("Looking for new files on the watch…"))
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
    else:
        out.lines.append(_("No new data to upload."))
    progress(_("Checking for files from Garmin Connect…"))
    messages = s.client.device_messages(s.device.unit_id)
    items = sync.plan_downloads(s.device, messages)
    todo = [i for i in items if not i.unsupported and not i.handled_by]
    if todo:
        drep = sync.run_downloads(todo, s.storage, s.client, progress)
        out.downloaded = len(drep.written)
        out.errors += [f"{label}: {err}" for label, err in drep.failed]
        out.lines.append(ngettext("Wrote {count} file from Connect to the watch.",
                                  "Wrote {count} files from Connect to the watch.",
                                  out.downloaded).format(count=out.downloaded))
    if any(i.handled_by == "Connect IQ" for i in items):
        out.lines += _install_queued_apps(s, messages, progress, out.errors)
    if any(i.handled_by == "TrueUp" for i in items):
        out.lines += _trueup(s, messages, progress, out.errors)
    skipped = [i for i in items if i.unsupported]
    if skipped:
        out.lines.append(ngettext(
            "Skipped {count} item from Connect (not accepted by the watch).",
            "Skipped {count} items from Connect (not accepted by the watch).",
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
    rep = ciq.install_queued(s.device, s.storage, s.client, messages, known, progress=progress)
    errors += [f"Connect IQ {name}: {err}" for name, err in rep.failed]
    if not rep.installed:
        return []
    return [_("Installed Connect IQ apps: {names}.").format(names=", ".join(rep.installed))]


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
