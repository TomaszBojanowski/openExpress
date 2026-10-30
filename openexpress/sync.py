"""Watch <-> Garmin Connect synchronization.

Flow, as in Garmin Express:
  upload:   configuration from OMT -> select new files -> check for duplicate
            activities -> POST file -> save state
  download: Connect message queue -> download -> write to device -> ack

All `plan_*` functions are read-only; changes are made by `run_*`.
The clone never deletes files from the watch.
"""

from __future__ import annotations

import hashlib
import json
import logging
import posixpath
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from openexpress import applog, paths
from openexpress.connect import (
    ApiError,
    ClockSkew,
    DeviceNotVerified,
    GarminClient,
    Unauthorized,
)
from openexpress.device.storage import Storage
from openexpress.device.xmldevice import DeviceFile, GarminDevice
from openexpress.fit import incompleteness, parse_file_id
from openexpress.i18n import _, human_size

log = logging.getLogger(__name__)

# Types handled by Garmin Express as "multi-file" (each file uploaded once,
# identified by name). The rest are single files, uploaded when their content changes.
# Mapping as in Express, including types 33, 37 and 50-55.
MULTI_FILE_TYPES = {
    "FIT_TYPE_4", "FIT_TYPE_32", "FIT_TYPE_33", "FIT_TYPE_36", "FIT_TYPE_37",
    "FIT_TYPE_38", "FIT_TYPE_49", "FIT_TYPE_50", "FIT_TYPE_51", "FIT_TYPE_52",
    "FIT_TYPE_53", "FIT_TYPE_54", "FIT_TYPE_55",
}
ACTIVITY_TYPE = "FIT_TYPE_4"
MONITORING_TYPES = {"FIT_TYPE_32", "FIT_TYPE_33"}
SLEEP_TYPES = {"FIT_TYPE_49", "FIT_TYPE_50", "FIT_TYPE_51", "FIT_TYPE_52",
               "FIT_TYPE_53", "FIT_TYPE_54", "FIT_TYPE_55"}

# Queue items handled by dedicated modules instead of a plain file download.
SPECIAL_DOWNLOAD_PREFIXES = {"IQ": "Connect IQ"}
SPECIAL_DOWNLOAD_TYPES = {"FIT_TYPE_39": "TrueUp"}
# The room kept free on the device besides a file from Garmin Connect's queue.
DOWNLOAD_MARGIN = 1024 * 1024

DOWNLOAD_NAME_PREFIX = {"FIT_TYPE_6": "course", "FIT_TYPE_5": "workout", "FIT_TYPE_11": "goal"}

# Device diagnostics and telemetry (error logs, KPIs, memory dumps, user
# behaviour log). Express uploads them without asking; here only on request.
DIAGNOSTIC_TYPES = {
    "ErrorShutdownReports", "ULFLogs", "BLELogs", "KPI", "RAMDump", "IQErrorReports",
    "FIT_TYPE_52",
}
DIAGNOSTIC_URL_MARKERS = ("era2-intake", "/kpi/", "/ulf/", "user-behavior-log", "appstore-era")
# ECG recordings: Garmin's upload settings send them to Garmin Health, which takes them only
# with a sign-in of its own that openExpress does not have (with the Garmin Connect login
# the answer is 401 or 403). Never uploaded; the files stay on the device and the summary of
# the sync says how many there are.
HEALTH_TYPES = frozenset({"FIT_TYPE_61"})

ALREADY_EXISTS_CODES = {202, 408}
REJECTED_HTTP = {400, 406, 413, 415}
# "Active tracker conflict": another device is the active activity tracker. Express
# reports it only for wellness devices; the file is not retried.
TRACKER_CONFLICT = 419
# Errors after which a file must be retried, even for consent-flagged locations.
RETRYABLE_CLIENT_HTTP = {408, 429}


def md5(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


# --- local state -----------------------------------------------------------------------


class SyncState:
    """What has already been uploaded; Garmin Express keeps the same record."""

    def __init__(self, unit_id: int, base: Path | None = None):
        self.dir = (base or paths.device_dir(unit_id)) / "sync"
        self.file = self.dir / "state.json"
        self.pending_dir = self.dir / "pending"
        self.unacked_file = self.dir / "unacked.json"
        data = json.loads(self.file.read_text()) if self.file.exists() else {}
        self.completed: dict[str, set[str]] = {
            k: set(v) for k, v in data.get("completed", {}).items()
        }
        self.singleton_md5: dict[str, str] = data.get("singleton_md5", {})

    def is_completed(self, datatype: str, name: str) -> bool:
        return name.casefold() in self.completed.get(datatype, set())

    def mark_completed(self, datatype: str, name: str) -> None:
        self.completed.setdefault(datatype, set()).add(name.casefold())

    def singleton_changed(self, datatype: str, path: str, digest: str) -> bool:
        return self.singleton_md5.get(f"{datatype}:{path.casefold()}") != digest

    def mark_singleton(self, datatype: str, path: str, digest: str) -> None:
        self.singleton_md5[f"{datatype}:{path.casefold()}"] = digest

    def pending_path(self, datatype: str, name: str) -> Path:
        return self.pending_dir / datatype / name

    def pending_files(self, datatype: str) -> list[Path]:
        d = self.pending_dir / datatype
        return sorted(p for p in d.iterdir() if p.is_file()) if d.is_dir() else []

    def keep_pending(self, datatype: str, name: str, data: bytes) -> None:
        """Keep a copy of a file whose upload failed, for the next sync (an activity is
        private: readable by its owner only)."""
        paths.write_private(self.pending_path(datatype, name), data)

    def drop_pending(self, datatype: str, name: str) -> None:
        """The file is done (uploaded, found in Connect, marked as uploaded): its copy goes.
        Without it the copy would stay for ever, as the plan skips a file that is done."""
        self.pending_path(datatype, name).unlink(missing_ok=True)

    def unacked(self) -> set[str]:
        """Queue messages already written to the watch whose acknowledgement failed."""
        try:
            return {str(i) for i in json.loads(self.unacked_file.read_text())}
        except (OSError, ValueError, TypeError):
            return set()

    def save_unacked(self, ids: set[str]) -> None:
        if not ids:
            self.unacked_file.unlink(missing_ok=True)
            return
        paths.write_private(self.unacked_file, json.dumps(sorted(ids)))

    def save(self) -> None:
        """Written whole or not at all, readable by its owner only (paths.write_private)."""
        data = {
            "completed": {k: sorted(v) for k, v in sorted(self.completed.items())},
            "singleton_md5": dict(sorted(self.singleton_md5.items())),
        }
        paths.write_private(self.file, json.dumps(data, indent=1))


# --- upload ----------------------------------------------------------------------------


@dataclass
class UploadLocation:
    url: str
    requires_auth: bool
    requires_consent: bool
    consent_types: list[str]


@dataclass
class UploadCandidate:
    datatype: str
    name: str
    size: int
    device_path: str | None  # None: pending file from a previous failed attempt
    locations: list[UploadLocation]
    is_zip: bool
    data: bytes | None = None
    external_id: str | None = None

    @property
    def multi(self) -> bool:
        return self.datatype in MULTI_FILE_TYPES


@dataclass
class UploadPlan:
    candidates: list[UploadCandidate] = field(default_factory=list)
    skipped_types: dict[str, str] = field(default_factory=dict)
    already_done: int = 0
    ecg_recordings: int = 0  # on the device, never uploaded (HEALTH_TYPES)


def is_diagnostic(datatype: str, url: str) -> bool:
    return datatype in DIAGNOSTIC_TYPES or any(m in url for m in DIAGNOSTIC_URL_MARKERS)


def parse_locations(dt: dict, include_consent: bool,
                    consent_granted: Callable[[str], bool] | None = None,
                    ) -> tuple[list[UploadLocation], list[UploadLocation]]:
    """Return (used, skipped) upload locations for an OMT data type. A location that names
    required consents is used only when `consent_granted` says each is given (skipped without
    that function); `include_consent` takes them all, for marking files only."""
    used, skipped = [], []
    for loc in dt.get("uploadlocations") or []:
        ul = UploadLocation(
            url=loc.get("url") or "",
            requires_auth=bool(loc.get("requiresauthentication")),
            requires_consent=bool(loc.get("requiresuserconsent")),
            consent_types=[c.get("name") or c.get("consenttype") or "?"
                           for c in loc.get("requiredconsenttypes") or [] if isinstance(c, dict)],
        )
        if not ul.url:
            continue
        # The requiresUserConsent flag alone keeps no location back (it is set for e.g. HRV
        # and skin temperature).
        ok = (include_consent or not ul.consent_types
              or (consent_granted is not None
                  and all(consent_granted(c) for c in ul.consent_types)))
        (used if ok else skipped).append(ul)
    return used, skipped


def _sources_for(device: GarminDevice, dt: dict) -> list[DeviceFile]:
    name = dt.get("datatypename") or ""
    srcs = device.sources(name)
    if srcs:
        return srcs
    out = []
    for es in dt.get("expectedsources") or []:
        out.append(
            DeviceFile(
                datatype=name,
                direction="OutputFromUnit",
                path=es.get("path") or "",
                basename=es.get("basename") or "",
                extension=es.get("fileextension") or "",
            )
        )
    return out


def _count_files(storage: Storage, sources) -> int:
    """How many files of these sources the device has, each once; none are read."""
    seen: set[str] = set()
    for src in sources:
        for path, _entry in storage.walk_files(src.path):
            if src.matches(path):
                seen.add(path.casefold())
    return len(seen)


def plan_uploads(
    device: GarminDevice,
    storage: Storage,
    upload_config: dict,
    state: SyncState,
    include_consent_locations: bool = False,
    only_types: set[str] | None = None,
    include_diagnostics: bool = False,
    consent_granted: Callable[[str], bool] | None = None,
) -> UploadPlan:
    plan = UploadPlan()
    for dt in upload_config.get("datatypes") or []:
        name = dt.get("datatypename") or ""
        if only_types and name not in only_types:
            continue
        if name in HEALTH_TYPES:
            n = _count_files(storage, _sources_for(device, dt))
            if n:
                plan.skipped_types[name] = _("ECG (needs a Garmin Health account)")
                plan.ecg_recordings += n
            continue
        locations, skipped = parse_locations(dt, include_consent_locations, consent_granted)
        if not include_diagnostics:
            diag = [loc for loc in locations if is_diagnostic(name, loc.url)]
            locations = [loc for loc in locations if loc not in diag]
            if diag and not locations:
                if _sources_for(device, dt):
                    plan.skipped_types[name] = _("diagnostics/telemetry (--include-diagnostics)")
                continue
        if skipped:
            # As Garmin Express: a consent missing for any location keeps the whole data
            # type back, also from its locations that need no consent.
            if _sources_for(device, dt):
                consents = ", ".join(
                    sorted({c for s in skipped for c in s.consent_types}) or ["?"])
                plan.skipped_types[name] = _("consent not given ({consents})").format(
                    consents=consents)
            continue
        if not locations:
            continue
        sources = _sources_for(device, dt)
        if not sources:
            continue
        seen: set[str] = set()
        for src in sources:
            for path, entry in storage.walk_files(src.path):
                if not src.matches(path) or path.casefold() in seen:
                    continue
                seen.add(path.casefold())
                fname = posixpath.basename(path)
                is_zip = fname.lower().endswith(".zip")
                cand = UploadCandidate(name, fname, entry.size, path, locations, is_zip)
                if cand.multi:
                    if state.is_completed(name, fname):
                        plan.already_done += 1
                        continue
                else:
                    cand.data = storage.read(path)
                    if not state.singleton_changed(name, path, md5(cand.data)):
                        plan.already_done += 1
                        continue
                plan.candidates.append(cand)
        if name in MULTI_FILE_TYPES:
            on_device = {c.name.casefold() for c in plan.candidates if c.datatype == name}
            for p in state.pending_files(name):
                if p.name.casefold() not in on_device and not state.is_completed(name, p.name):
                    plan.candidates.append(
                        UploadCandidate(name, p.name, p.stat().st_size, None, locations,
                                        p.name.lower().endswith(".zip"))
                    )
    plan.candidates.sort(key=_upload_order)
    log.info("upload plan: %d new (%s), %d already uploaded, %d types skipped",
             len(plan.candidates), applog.counts(c.datatype for c in plan.candidates),
             plan.already_done, len(plan.skipped_types))
    return plan


def _upload_order(c: UploadCandidate) -> tuple[int, str]:
    group = 2 if c.datatype in SLEEP_TYPES else 1 if c.datatype in MONITORING_TYPES else 0
    return group, c.name.casefold()


@dataclass
class UploadReport:
    uploaded: list[str] = field(default_factory=list)
    duplicates: list[str] = field(default_factory=list)
    already_in_connect: list[str] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    not_confirmed: list[str] = field(default_factory=list)
    tracker_conflict: list[str] = field(default_factory=list)
    # Refused by every location, each of them one that needs a consent: neither counted nor
    # marked, tried again by the next sync (as Garmin Express does).
    not_taken: list[str] = field(default_factory=list)
    # FIT files read cut short, as the device was still writing them: not sent, not marked,
    # read again by the next sync.
    incomplete: list[str] = field(default_factory=list)


def _label(c: UploadCandidate) -> str:
    return f"{c.datatype}/{c.name}"


def log_label(label: str) -> str:
    """An upload as the log names it (`_label`): its type, never the name of the file."""
    return label.split("/")[0]


def log_error(label: str, error: str) -> str:
    """The error of an upload as the log tells it: the file's name (which a message about
    reading it may quote) as <file>."""
    name = label.split("/", 1)[1] if "/" in label else ""
    return error.replace(name, "<file>") if name else error


def still_being_written(c: UploadCandidate, data: bytes) -> str | None:
    """Why a FIT file is not whole as it was read (fit.incompleteness), or None. A device
    that writes a file while it is connected over MTP is read at the size it had when it was
    connected (gvfs keeps that size), so the file is cut short. Other files: None."""
    if c.is_zip or not c.name.lower().endswith(".fit"):
        return None
    return incompleteness(data)


def _skip_incomplete(c: UploadCandidate, why: str, report: UploadReport) -> None:
    report.incomplete.append(_label(c))
    # The type and the sizes: never the name of the file.
    log.warning("upload skipped: %s still being written by the device (%s); "
                "read again by the next sync", c.datatype, why)


def _read_candidate(c: UploadCandidate, storage: Storage, state: SyncState) -> bytes:
    if c.data is not None:
        return c.data
    if c.device_path is None:
        return state.pending_path(c.datatype, c.name).read_bytes()
    return storage.read(c.device_path)


def run_uploads(
    plan: UploadPlan,
    device: GarminDevice,
    storage: Storage,
    client: GarminClient,
    state: SyncState,
    progress: Callable[[str], None] = lambda s: None,
    wait_for_processing: bool = True,
) -> UploadReport:
    """Upload the files of the plan. A client never checked against the account (run_sync
    checks the owner first) stops it at its first refusal: nothing is sent, nothing is kept
    for a next sync, and the refusal is logged once and raised."""
    try:
        return _run_uploads(plan, device, storage, client, state, progress, wait_for_processing)
    except DeviceNotVerified:
        log.warning("uploads: nothing uploaded, the device was not checked against the account")
        raise


def _run_uploads(plan, device, storage, client, state, progress,
                 wait_for_processing) -> UploadReport:
    report = UploadReport()
    cands = plan.candidates
    unreadable: set[int] = set()

    # Activities that Connect already has (e.g. from the phone) are marked without uploading.
    # Files are read one at a time and not kept in memory.
    acts = [c for c in cands if c.datatype == ACTIVITY_TYPE]
    for c in acts:
        try:
            data = _read_candidate(c, storage, state)
        except Exception as e:  # noqa: BLE001 - one unreadable file must not stop the sync
            report.failed.append((_label(c), str(e)))
            unreadable.add(id(c))
            continue
        why = still_being_written(c, data)
        if why:
            _skip_incomplete(c, why, report)
            unreadable.add(id(c))
            continue
        try:
            fid = parse_file_id(data)
            if fid.time_created is not None:
                c.external_id = str(fid.time_created)
        except Exception:  # noqa: BLE001, S110 - unreadable FIT header: the file is uploaded anyway
            pass
        c.data = None
    ids = [c.external_id for c in acts if c.external_id]
    matched: dict[str, bool] = {}
    if ids:
        try:
            for i in range(0, len(ids), 50):
                matched.update(client.activity_matches(device.unit_id, ids[i : i + 50]))
        except (Unauthorized, ClockSkew, DeviceNotVerified):
            raise
        except Exception as e:  # noqa: BLE001
            log.warning("Duplicate check failed (%s); uploading everything", e)
            matched = {}
    remaining = []
    for c in cands:
        if id(c) in unreadable:
            continue
        if c.external_id and matched.get(c.external_id):
            state.mark_completed(c.datatype, c.name)
            state.drop_pending(c.datatype, c.name)
            report.already_in_connect.append(c.name)
        else:
            remaining.append(c)
    state.save()

    stop_types: set[str] = set()
    for idx, c in enumerate(remaining):
        label = _label(c)
        if c.datatype in stop_types:
            continue
        ordered = c.datatype in MONITORING_TYPES or c.datatype in SLEEP_TYPES
        try:
            data = _read_candidate(c, storage, state)
        except Exception as e:  # noqa: BLE001
            report.failed.append((label, str(e)))
            if ordered:
                stop_types.add(c.datatype)
            continue
        why = still_being_written(c, data)
        if why:
            _skip_incomplete(c, why, report)
            c.data = None
            if ordered:
                stop_types.add(c.datatype)  # the later ones wait: they must arrive in order
            continue
        try:
            _upload_one(c, data, label, idx == len(remaining) - 1 and ordered, client, state,
                        report, progress, wait_for_processing, stop_types)
        finally:
            c.data = None
        state.save()
    log.info("uploads: %d uploaded, %d duplicates, %d already in Connect, %d rejected, "
             "%d failed, %d not confirmed, %d for another tracker, %d not taken by a "
             "location that needs a consent",
             len(report.uploaded), len(report.duplicates), len(report.already_in_connect),
             len(report.rejected), len(report.failed), len(report.not_confirmed),
             len(report.tracker_conflict), len(report.not_taken))
    for label, error in report.failed:
        log.warning("upload of %s failed: %s", log_label(label), log_error(label, error))
    return report


def _upload_one(c, data, label, last, client, state, report, progress,
                wait_for_processing, stop_types) -> None:
    progress(_("Uploading {name} ({size})").format(name=label, size=human_size(c.size)))
    ok = True
    error = ""
    outcome = "uploaded"
    taken = False  # a location answered for good (not one that needs a consent refusing it)
    for loc in c.locations:
        try:
            res = client.upload_file(
                loc.url, data, is_zip=c.is_zip, last_file=last,
                authenticated=loc.requires_auth,
            )
        except (Unauthorized, ClockSkew, DeviceNotVerified):
            raise
        # A failure with one file (network or anything else) goes to the report and the upload
        # goes on with the other files (monitoring and sleep data: with the other types, as
        # those must arrive in order). An expired login and a wrong clock are raised above.
        except Exception as e:  # noqa: BLE001
            ok, error = False, str(e)
            break
        codes = _failure_codes(res.body) if 200 <= res.status < 300 else set()
        if res.status == 409:
            outcome = "duplicate"
        elif res.status == 412:
            raise ApiError(_("Upload consent was revoked in Garmin Connect"), 412)
        elif res.status == TRACKER_CONFLICT or TRACKER_CONFLICT in codes:
            outcome = "tracker_conflict"
        elif res.status in REJECTED_HTTP:
            outcome = "rejected"
            # Why, as far as the server says it: the type, never the name of the file.
            log.warning("upload rejected: %s (%s), HTTP %d, answer: %s", c.datatype,
                        applog.size(len(data)), res.status, applog.answer_excerpt(res.body))
        elif 200 <= res.status < 300:
            if codes and codes <= ALREADY_EXISTS_CODES:
                outcome = "duplicate"
            elif codes - ALREADY_EXISTS_CODES:
                log.warning("upload rejected: %s (%s), HTTP %d with codes %s, answer: %s",
                            c.datatype, applog.size(len(data)), res.status, sorted(codes),
                            applog.answer_excerpt(res.body))
                ok, error = False, _("server rejected the file (codes {codes})").format(
                    codes=sorted(codes))
                break
            elif wait_for_processing and res.location and "activity-service" in res.location:
                try:
                    st = client.poll_location(res.location, res.poll_delay_ms)
                except Exception as e:  # noqa: BLE001 - the file itself was accepted
                    log.warning("Could not confirm processing of %s: %s", c.datatype, e)
                    st = None
                if st != 201:
                    report.not_confirmed.append(label)
        elif (loc.requires_consent and 400 <= res.status < 500
              and res.status not in RETRYABLE_CLIENT_HTTP):
            # Optional location (e.g. HRV, skin temperature) refused by the server.
            continue
        else:
            ok, error = False, f"HTTP {res.status}"
            break
        taken = True
    if ok and not taken:
        # No location took it: tried again from the device by the next sync, not told as an
        # error. No copy is kept for it (the consent may never come); files of a type that
        # must arrive in order wait with it.
        report.not_taken.append(label)
        if c.datatype in MONITORING_TYPES or c.datatype in SLEEP_TYPES:
            stop_types.add(c.datatype)
        return
    if ok:
        if c.multi:
            state.mark_completed(c.datatype, c.name)
            state.drop_pending(c.datatype, c.name)
        elif c.device_path:
            state.mark_singleton(c.datatype, c.device_path, md5(data))
        {"uploaded": report.uploaded, "duplicate": report.duplicates,
         "rejected": report.rejected,
         "tracker_conflict": report.tracker_conflict}[outcome].append(label)
    else:
        report.failed.append((label, error))
        if c.multi:
            if c.device_path is not None:
                state.keep_pending(c.datatype, c.name, data)
            if c.datatype in MONITORING_TYPES or c.datatype in SLEEP_TYPES:
                stop_types.add(c.datatype)


def _failure_codes(body) -> set[int]:
    if not isinstance(body, dict):
        return set()
    result = body.get("detailedimportresult") or {}
    codes = set()
    for f in result.get("failures") or []:
        for m in f.get("messages") or []:
            try:
                codes.add(int(m.get("code")))
            except (TypeError, ValueError):
                pass
    return codes


def state_path(unit_id: int) -> Path:
    """The state.json of a device, as SyncState has it."""
    return paths.device_dir(unit_id) / "sync" / "state.json"


def synced_here(unit_id: int) -> bool:
    """Whether this installation has synced the device, or marked its files as uploaded
    (baseline): its state.json is there. Without it the device was never synced here, and
    its first sync asks how to begin (the one source of "never synced")."""
    return state_path(unit_id).exists()


# --- activities not uploaded yet (the chip "To sync" of a tile) ------------------------


@dataclass(frozen=True)
class ActivitiesToSync:
    """The activities on a connected device that this installation has not uploaded yet: those
    its activity folder lists that state.json does not hold as done, and the copies kept
    after a failed upload. `count` None: the device was never synced here (no state.json).
    The names listed stay in memory only, for again()."""

    unit_id: int = field(repr=False)
    listed: frozenset[str] = field(default=frozenset(), repr=False)
    count: int | None = None

    @property
    def never_synced(self) -> bool:
        return self.count is None

    def again(self) -> ActivitiesToSync | None:
        """After a sync (of the window, the background service or the command): what was
        listed against state.json as it is now; the device is not asked again (no activity
        is made while it is connected over USB)."""
        try:
            return _activities_left(self.unit_id, self.listed)
        except Exception as e:  # noqa: BLE001 - not known, never 0: the tile shows nothing
            log.info("activities: not counted again (%s)", type(e).__name__)
            return None


def activities_to_sync(device: GarminDevice, storage: Storage) -> ActivitiesToSync | None:
    """The activities of `device` not uploaded yet, from its activity folder (GarminDevice.xml)
    listed, no file read. None when not known: a drive, a device without a unit ID, or a
    folder or state.json that could not be read. A device never synced here is not listed.
    Never raises: the device opens all the same."""
    try:
        return _activities_to_sync(device, storage)
    except Exception as e:  # noqa: BLE001 - not known, never 0: the tile shows nothing
        log.info("activities: not counted (%s)", type(e).__name__)
        return None


def _activities_to_sync(device: GarminDevice, storage: Storage) -> ActivitiesToSync | None:
    if storage.mass_storage or not device.unit_id:  # a drive is never synced
        return None
    if not SyncState(device.unit_id).file.exists():
        log.info("activities: not counted, the device was never synced here")
        return ActivitiesToSync(device.unit_id)
    started = time.monotonic()
    listed = set()
    for src in device.sources(ACTIVITY_TYPE):
        for path, _entry in storage.walk_files(src.path):
            if src.matches(path):
                listed.add(posixpath.basename(path).casefold())
    took = time.monotonic() - started
    result = _activities_left(device.unit_id, frozenset(listed))
    log.info("activities: %d on the device, %d not uploaded yet (listed in %.2f s)",
             len(listed), result.count or 0, took)
    return result


def _activities_left(unit_id: int, listed: frozenset[str]) -> ActivitiesToSync:
    state = SyncState(unit_id)
    if not state.file.exists():
        return ActivitiesToSync(unit_id, listed)
    # Done as a sync marks them: also those refused by Garmin and already in Connect, not a
    # failed upload.
    done = state.completed.get(ACTIVITY_TYPE, set())
    kept = {p.name.casefold() for p in state.pending_files(ACTIVITY_TYPE)}
    return ActivitiesToSync(unit_id, listed, len((listed | kept) - done))


def mark_baseline(device: GarminDevice, storage: Storage, upload_config: dict,
                  state: SyncState, client: GarminClient) -> int:
    """Mark all files currently on the watch as already uploaded (without uploading).

    The state says "this account already has these files", so it needs the same
    ownership check as an upload.
    """
    client.require_verified(device.unit_id)
    files = plan_baseline(device, storage, upload_config, state)
    note_baseline(files, state)
    return save_baseline(device, files, state, client)


def plan_baseline(device: GarminDevice, storage: Storage, upload_config: dict,
                  state: SyncState) -> list[UploadCandidate]:
    """The files a baseline marks as uploaded already: those on the device that a sync
    could upload, whatever consent their upload needs (not the copies kept of a failed
    upload)."""
    plan = plan_uploads(device, storage, upload_config, state, include_consent_locations=True)
    return [c for c in plan.candidates if c.device_path is not None]


def note_baseline(files: list[UploadCandidate], state: SyncState) -> None:
    """Mark the files of a baseline in `state`, in memory: nothing is written (a plan of
    the uploads after it is made against it)."""
    for c in files:
        if c.multi:
            state.mark_completed(c.datatype, c.name)
        else:
            state.mark_singleton(c.datatype, c.device_path, md5(c.data or b""))
            c.data = None


def save_baseline(device: GarminDevice, files: list[UploadCandidate], state: SyncState,
                  client: GarminClient) -> int:
    """Write a baseline noted in `state` (note_baseline): the copies of its files kept of a
    failed upload go, and state.json is written. It says "this account already has these
    files", so it needs the same ownership check as an upload. How many files it marked."""
    client.require_verified(device.unit_id)
    for c in files:
        if c.multi:
            state.drop_pending(c.datatype, c.name)
    state.save()
    return len(files)


# --- download --------------------------------------------------------------------------


@dataclass
class DownloadItem:
    message_id: int | str
    datatype: str
    message_name: str
    url: str
    absolute: bool
    target: DeviceFile | None
    unsupported: str = ""
    handled_by: str = ""

    def device_name(self) -> str:
        assert self.target is not None
        ext = self.target.extension or "FIT"
        if "userprofile" in self.datatype.casefold():
            # A single profile file, replaced on every download (as in Express).
            return f"{self.target.basename or 'UserProfile'}.{ext}"
        prefix = DOWNLOAD_NAME_PREFIX.get(self.datatype)
        guid = str(uuid.uuid4())
        if prefix:
            return f"{prefix}_{guid}.{ext}"
        if self.target.basename:
            return f"{self.target.basename}_{guid}.{ext}"
        return f"{guid}.{ext}"


def plan_downloads(device: GarminDevice, messages: list[dict]) -> list[DownloadItem]:
    items = []
    for m in messages:
        meta = m.get("metadata")
        if not meta or m.get("messagetype") == "software-update":
            continue
        dt = m.get("devicexmldatatype") or ""
        targets = device.targets(dt)
        item = DownloadItem(
            message_id=m.get("messageid"),
            datatype=dt,
            message_name=meta.get("messagename") or "",
            url=meta.get("messageurl") or "",
            absolute=bool(meta.get("absolute")),
            target=targets[0] if targets else None,
        )
        special = SPECIAL_DOWNLOAD_TYPES.get(dt) or next(
            (v for k, v in SPECIAL_DOWNLOAD_PREFIXES.items() if dt.startswith(k)), "")
        if item.target is None:
            item.unsupported = _("device does not accept this type")
        elif special:
            item.handled_by = special
        items.append(item)
    log.info("queue from Garmin Connect: %d items (%s); %d not accepted by the device, "
             "handled elsewhere: %s", len(items), applog.counts(i.datatype for i in items),
             sum(1 for i in items if i.unsupported),
             applog.counts(i.handled_by for i in items if i.handled_by))
    return items


@dataclass
class DownloadReport:
    written: list[str] = field(default_factory=list)
    # The data type of each file written now (as `written`), for the details of the sync.
    written_types: list[str] = field(default_factory=list)
    empty: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    # Written to the watch, but Connect was not told; acknowledged on the next sync.
    ack_failed: list[str] = field(default_factory=list)
    # The same as the log names them: the type and the number of the queue message, never
    # its name (a course or a workout is named by the user, often after a place).
    failed_in_log: list[tuple[str, str]] = field(default_factory=list)
    ack_failed_in_log: list[str] = field(default_factory=list)


def run_downloads(
    items: list[DownloadItem],
    storage: Storage,
    client: GarminClient,
    progress: Callable[[str], None] = lambda s: None,
    state: SyncState | None = None,
) -> DownloadReport:
    """Download the queued files to the watch and return the report. With `state`, a message
    whose file was written but not acknowledged is kept in it and only acknowledged next
    time (no duplicate file on the watch). Raises DeviceNotVerified at the first refusal of
    a client never checked against the account: nothing more is fetched, written or
    acknowledged."""
    try:
        return _run_downloads(items, storage, client, progress, state)
    except DeviceNotVerified:
        # As the uploads (run_sync checks the owner first): the messages written before stay
        # not acknowledged, and the refusal is logged once.
        log.warning("downloads: nothing downloaded, the device was not checked against the "
                    "account")
        raise


def _run_downloads(items, storage, client, progress, state) -> DownloadReport:
    report = DownloadReport()
    unacked = state.unacked() if state is not None else set()
    initial = set(unacked)
    try:
        for it in items:
            if it.unsupported or it.handled_by:
                continue
            label = f"{it.datatype}: {it.message_name or it.message_id}"  # for the user
            quiet = f"{it.datatype} (message {it.message_id})"  # for the log
            mid = str(it.message_id)
            if mid in unacked:
                try:
                    client.ack_message(it.message_id)
                except (Unauthorized, DeviceNotVerified):
                    raise
                except Exception as e:  # noqa: BLE001
                    log.warning("Acknowledgement of %s failed again: %s", quiet, e)
                    report.ack_failed.append(label)
                    report.ack_failed_in_log.append(quiet)
                else:
                    unacked.discard(mid)
                continue
            try:
                progress(_("Downloading {name}").format(name=label))
                data = client.download_message(it.url, it.absolute)
                if data is None:
                    client.ack_message(it.message_id)
                    report.empty.append(label)
                    continue
                storage.require_space(len(data), DOWNLOAD_MARGIN, error=ApiError)
                target = posixpath.join(it.target.path, it.device_name())  # type: ignore[union-attr]
                # Of another size, it is deleted and not acknowledged: it comes again.
                real = storage.write_checked(target, data)
            except (Unauthorized, DeviceNotVerified):
                raise
            except Exception as e:  # noqa: BLE001
                report.failed.append((label, str(e)))
                report.failed_in_log.append((quiet, str(e)))
                continue
            progress(_("Wrote {path}").format(path=real))
            report.written.append(f"{label} -> {real}")
            report.written_types.append(it.datatype)
            # Written: not acknowledged until Garmin Connect says so, whatever stops the sync
            # meanwhile (also Ctrl+C); the next sync then acknowledges it without writing it
            # again.
            unacked.add(mid)
            try:
                client.ack_message(it.message_id)
            except (Unauthorized, DeviceNotVerified):
                raise
            except Exception as e:  # noqa: BLE001
                log.warning("Acknowledgement of %s failed: %s", quiet, e)
                report.ack_failed.append(label)
                report.ack_failed_in_log.append(quiet)
            else:
                unacked.discard(mid)
    finally:
        if state is not None and unacked != initial:
            state.save_unacked(unacked)
    log.info("downloads: %d written, %d empty, %d failed, %d not acknowledged",
             len(report.written), len(report.empty), len(report.failed),
             len(report.ack_failed))
    for _label, error in report.failed:
        log.warning("download failed: %s", error)
    return report
