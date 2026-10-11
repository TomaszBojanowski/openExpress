"""Watch <-> Garmin Connect synchronization.

Flow (see docs/analiza/03-synchronizacja.md):
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
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from openexpress import paths
from openexpress.connect import ApiError, ClockSkew, GarminClient, Unauthorized
from openexpress.device.storage import Storage
from openexpress.device.xmldevice import DeviceFile, GarminDevice
from openexpress.fit import parse_file_id
from openexpress.i18n import _

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

# Connect queue types that this version does not install yet.
# Queue items handled by dedicated modules instead of a plain file download.
SPECIAL_DOWNLOAD_PREFIXES = {"IQ": "Connect IQ"}
SPECIAL_DOWNLOAD_TYPES = {"FIT_TYPE_39": "TrueUp"}

DOWNLOAD_NAME_PREFIX = {"FIT_TYPE_6": "course", "FIT_TYPE_5": "workout", "FIT_TYPE_11": "goal"}

# Device diagnostics and telemetry (error logs, KPIs, memory dumps, user
# behaviour log). Express uploads them without asking; here only on request.
DIAGNOSTIC_TYPES = {
    "ErrorShutdownReports", "ULFLogs", "BLELogs", "KPI", "RAMDump", "IQErrorReports",
    "FIT_TYPE_52",
}
DIAGNOSTIC_URL_MARKERS = ("era2-intake", "/kpi/", "/ulf/", "user-behavior-log", "appstore-era")

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
    """What has already been uploaded. Equivalent of CompletedUploadsV3_*.xml in Express."""

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
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.unacked_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(sorted(ids)))
        tmp.replace(self.unacked_file)

    def save(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        data = {
            "completed": {k: sorted(v) for k, v in sorted(self.completed.items())},
            "singleton_md5": dict(sorted(self.singleton_md5.items())),
        }
        tmp = self.file.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1))
        tmp.replace(self.file)


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


def is_diagnostic(datatype: str, url: str) -> bool:
    return datatype in DIAGNOSTIC_TYPES or any(m in url for m in DIAGNOSTIC_URL_MARKERS)


def parse_locations(dt: dict, include_consent: bool) -> tuple[list[UploadLocation], list[UploadLocation]]:
    """Return (used, skipped) upload locations for an OMT data type.

    As in Express, the requiresUserConsent flag alone does not exclude a location
    (it applies to e.g. HRV and skin temperature). Locations with specific
    required consents are skipped, since this version does not check their state.
    """
    used, skipped = [], []
    for loc in dt.get("uploadlocations") or []:
        ul = UploadLocation(
            url=loc.get("url") or "",
            requires_auth=bool(loc.get("requiresauthentication")),
            requires_consent=bool(loc.get("requiresuserconsent")),
            consent_types=[c.get("name", "") for c in loc.get("requiredconsenttypes") or []],
        )
        if not ul.url:
            continue
        (used if include_consent or not ul.consent_types else skipped).append(ul)
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


def plan_uploads(
    device: GarminDevice,
    storage: Storage,
    upload_config: dict,
    state: SyncState,
    include_consent_locations: bool = False,
    only_types: set[str] | None = None,
    include_diagnostics: bool = False,
) -> UploadPlan:
    plan = UploadPlan()
    for dt in upload_config.get("datatypes") or []:
        name = dt.get("datatypename") or ""
        if only_types and name not in only_types:
            continue
        locations, skipped = parse_locations(dt, include_consent_locations)
        if not include_diagnostics:
            diag = [loc for loc in locations if is_diagnostic(name, loc.url)]
            locations = [loc for loc in locations if loc not in diag]
            if diag and not locations:
                if _sources_for(device, dt):
                    plan.skipped_types[name] = _("diagnostics/telemetry (--include-diagnostics)")
                continue
        if not locations:
            if skipped and _sources_for(device, dt):
                consents = ", ".join(
                    sorted({c for s in skipped for c in s.consent_types}) or ["?"])
                plan.skipped_types[name] = _("requires consent ({consents})").format(
                    consents=consents)
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


def _label(c: UploadCandidate) -> str:
    return f"{c.datatype}/{c.name}"


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
        try:
            fid = parse_file_id(data)
            if fid.time_created is not None:
                c.external_id = str(fid.time_created)
        except Exception:  # noqa: BLE001 - not a valid FIT file: just upload it
            pass
        c.data = None
    ids = [c.external_id for c in acts if c.external_id]
    matched: dict[str, bool] = {}
    if ids:
        try:
            for i in range(0, len(ids), 50):
                matched.update(client.activity_matches(device.unit_id, ids[i : i + 50]))
        except (Unauthorized, ClockSkew):
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
        try:
            _upload_one(c, data, label, idx == len(remaining) - 1 and ordered, client, state,
                        report, progress, wait_for_processing, stop_types)
        finally:
            c.data = None
        state.save()
    return report


def _upload_one(c, data, label, last, client, state, report, progress,
                wait_for_processing, stop_types) -> None:
    progress(_("Uploading {name} ({size} B)").format(name=label, size=c.size))
    ok = True
    error = ""
    outcome = "uploaded"
    for loc in c.locations:
        try:
            res = client.upload_file(
                loc.url, data, is_zip=c.is_zip, last_file=last,
                authenticated=loc.requires_auth,
            )
        except (Unauthorized, ClockSkew):
            raise
        except Exception as e:  # network
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
        elif 200 <= res.status < 300:
            if codes and codes <= ALREADY_EXISTS_CODES:
                outcome = "duplicate"
            elif codes - ALREADY_EXISTS_CODES:
                ok, error = False, _("server rejected the file (codes {codes})").format(
                    codes=sorted(codes))
                break
            elif wait_for_processing and res.location and "activity-service" in res.location:
                try:
                    st = client.poll_location(res.location, res.poll_delay_ms)
                except Exception as e:  # noqa: BLE001 - the file itself was accepted
                    log.warning("Could not confirm processing of %s: %s", label, e)
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
    if ok:
        if c.multi:
            state.mark_completed(c.datatype, c.name)
            pending = state.pending_path(c.datatype, c.name)
            if pending.exists():
                pending.unlink()
        elif c.device_path:
            state.mark_singleton(c.datatype, c.device_path, md5(data))
        {"uploaded": report.uploaded, "duplicate": report.duplicates,
         "rejected": report.rejected,
         "tracker_conflict": report.tracker_conflict}[outcome].append(label)
    else:
        report.failed.append((label, error))
        if c.multi:
            p = state.pending_path(c.datatype, c.name)
            if c.device_path is not None:
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(data)
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


def mark_baseline(device: GarminDevice, storage: Storage, upload_config: dict,
                  state: SyncState) -> int:
    """Mark all files currently on the watch as already uploaded (without uploading)."""
    plan = plan_uploads(device, storage, upload_config, state, include_consent_locations=True)
    n = 0
    for c in plan.candidates:
        if c.device_path is None:
            continue
        if c.multi:
            state.mark_completed(c.datatype, c.name)
        else:
            state.mark_singleton(c.datatype, c.device_path, md5(c.data or b""))
        n += 1
    state.save()
    return n


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
    return items


@dataclass
class DownloadReport:
    written: list[str] = field(default_factory=list)
    empty: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    # Written to the watch, but Connect was not told; acknowledged on the next sync.
    ack_failed: list[str] = field(default_factory=list)


def run_downloads(
    items: list[DownloadItem],
    storage: Storage,
    client: GarminClient,
    progress: Callable[[str], None] = lambda s: None,
    state: SyncState | None = None,
) -> DownloadReport:
    """Download queued files to the watch.

    With `state`, messages whose acknowledgement failed after the file was written are
    remembered and only acknowledged next time (no duplicate file on the watch).
    """
    report = DownloadReport()
    unacked = state.unacked() if state is not None else set()
    initial = set(unacked)
    try:
        for it in items:
            if it.unsupported or it.handled_by:
                continue
            label = f"{it.datatype}: {it.message_name or it.message_id}"
            mid = str(it.message_id)
            if mid in unacked:
                try:
                    client.ack_message(it.message_id)
                except Unauthorized:
                    raise
                except Exception as e:  # noqa: BLE001
                    log.warning("Acknowledgement of %s failed again: %s", label, e)
                    report.ack_failed.append(label)
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
                free = storage.free_space()
                if free is not None and free < len(data) + 1024 * 1024:
                    raise ApiError(_("not enough space on the device"))
                target = posixpath.join(it.target.path, it.device_name())  # type: ignore[union-attr]
                real = storage.write(target, data)
            except Unauthorized:
                raise
            except Exception as e:  # noqa: BLE001
                report.failed.append((label, str(e)))
                continue
            progress(_("Wrote {path}").format(path=real))
            report.written.append(f"{label} -> {real}")
            try:
                client.ack_message(it.message_id)
            except Unauthorized:
                unacked.add(mid)
                raise
            except Exception as e:  # noqa: BLE001
                log.warning("Acknowledgement of %s failed: %s", label, e)
                unacked.add(mid)
                report.ack_failed.append(label)
    finally:
        if state is not None and unacked != initial:
            state.save_unacked(unacked)
    return report
