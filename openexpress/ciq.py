"""Connect IQ apps: watch faces, data fields, widgets and apps.

Mirrors Garmin Express (docs/analiza/05, section 3):
- the list of apps on the watch comes from GARMIN/APPS/OUT.BIN (protobuf
  `Smart.connect_iq_installed_apps_service.get_installed_apps_response`);
- the apps assigned to the account come from services.garmin.com
  (`express/appstore/rest/apps/*`, OAuth1-signed with the Connect token);
- installing = asking the server to queue the app for the device, then
  downloading the queued .PRG, writing it to the watch and writing
  GARMIN/APPS/IN.BIN with the full, ordered app list (new apps appended).
"""

from __future__ import annotations

import json
import logging
import posixpath
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from urllib.parse import urlencode

from openexpress import paths, proto
from openexpress.connect import ApiError, GarminClient, lower_keys
from openexpress.device import ptp
from openexpress.device.storage import DeviceError, RemountFailed, Storage
from openexpress.device.xmldevice import GarminDevice
from openexpress.i18n import N_, _, ngettext

log = logging.getLogger(__name__)

PRELOADED_PREFIX = "c0ffee15-600d"
# The Connect IQ Store app ships with the watch firmware and must stay.
STORE_APP_ID = uuid.UUID("7cd38736-510a-4480-8801-0dfe2f5944af")

# Protobuf AppType (GDI.Proto.ConnectIQInstalledApps.AppType)
# TRANSLATORS: Connect IQ app types shown in app lists
APP_TYPES = {0: N_("unknown"), 1: N_("app"), 2: N_("widget"), 3: N_("watch face"),
             4: N_("data field"), 7: N_("music app"), 8: N_("activity")}
DATATYPE_TO_APPTYPE = {"IQWatchApps": 1, "IQWidgets": 2, "IQWatchFaces": 3,
                       "IQDataFields": 4, "IQAudioContentApps": 7}
APPTYPE_TO_DATATYPE = {v: k for k, v in DATATYPE_TO_APPTYPE.items()}
APPTYPE_TO_DATATYPE[8] = "IQWatchApps"
# Store API "type" strings
STORE_TYPES = {"watchface": 3, "watch-face": 3, "watch-app": 1, "widget": 2, "datafield": 4,
               "data-field": 4, "audio-content-provider-app": 7, "activity": 8}

CONFIG_DATATYPE = "IQAppsConfiguration"
SETTINGS_DATATYPE = "IQAppsSettingsFile"


class CiqError(Exception):
    pass


@dataclass
class InstalledApp:
    app_id: uuid.UUID
    app_type: int
    name: str
    disabled: bool
    version: int | None = None
    filename: str = ""
    size: int | None = None
    native_id: int | None = None
    favorite: bool = False
    # Only listed in IN.BIN: written to the watch but not yet confirmed in OUT.BIN.
    pending: bool = False
    # Whether its files are visible on the device; None when not checked.
    has_files: bool | None = None

    @property
    def preloaded(self) -> bool:
        return str(self.app_id).startswith(PRELOADED_PREFIX)

    @property
    def type_name(self) -> str:
        name = APP_TYPES.get(self.app_type)
        return _(name) if name else str(self.app_type)

    @property
    def removable(self) -> bool:
        return not self.preloaded and self.app_id != STORE_APP_ID


@dataclass
class InstalledState:
    apps: list[InstalledApp] = field(default_factory=list)
    available_space: int | None = None
    available_slots: int | None = None
    # Entries of OUT.BIN that could not be read. They are missing from `apps`, so a
    # rewritten app list (IN.BIN) would leave them out: nothing may be written then.
    unreadable: int = 0

    @property
    def store_apps(self) -> list[InstalledApp]:
        return [a for a in self.apps if not a.preloaded]

    def find(self, app_id: uuid.UUID, app_type: int | None = None) -> InstalledApp | None:
        """The entry with this ID; built-in items can share one ID as an app and as a widget,
        so give the type whenever one of several entries is meant."""
        return next((a for a in self.apps if a.app_id == app_id
                     and (app_type is None or a.app_type == app_type)), None)


@dataclass
class StoreApp:
    app_id: uuid.UUID
    name: str
    developer: str
    store_type: str
    size: int
    latest_version: int
    version_name: str
    has_settings: bool
    permissions_changed: bool

    @property
    def app_type(self) -> int:
        return STORE_TYPES.get(self.store_type, 1)


# --- device files --------------------------------------------------------------------


def _int(fields: list[proto.Field], number: int, default=None):
    return next((f.value for f in fields if f.number == number and f.wire_type == proto.VARINT),
                default)


def _bytes(fields: list[proto.Field], number: int, default=None):
    return next((f.value for f in fields if f.number == number and f.wire_type == proto.LEN),
                default)


def _all_bytes(fields: list[proto.Field], number: int) -> list[bytes]:
    return [f.value for f in fields if f.number == number and f.wire_type == proto.LEN]


def _app_id(fields: list[proto.Field]) -> uuid.UUID | None:
    raw = _bytes(fields, 1)
    if raw is None or len(raw) != 16:
        return None
    return uuid.UUID(bytes=raw)


def _get_service(data: bytes) -> list[proto.Field]:
    svc = _bytes(proto.decode(data), 3)
    if svc is None:
        raise CiqError(_("Not a Connect IQ configuration file"))
    return proto.decode(svc)


def parse_out_bin(data: bytes) -> InstalledState:
    try:
        resp = _bytes(_get_service(data), 2)
        if resp is None:
            raise CiqError(_("OUT.BIN does not contain an installed-apps response"))
        fields = proto.decode(resp)
    except proto.ProtoError as e:
        raise CiqError(_("The Connect IQ app list (OUT.BIN) is damaged: {error}")
                       .format(error=e)) from e
    state = InstalledState(
        available_space=_int(fields, 1),
        available_slots=_int(fields, 2),
    )
    for raw in _all_bytes(fields, 3):
        try:
            a = proto.decode(raw)
        except proto.ProtoError:
            state.unreadable += 1
            continue
        app_id = _app_id(a)
        if app_id is None:
            state.unreadable += 1
            continue
        state.apps.append(InstalledApp(
            app_id=app_id,
            app_type=_int(a, 2, 0),
            name=_bytes(a, 3, b"").decode("utf-8", "replace"),
            disabled=bool(_int(a, 4, 0)),
            version=_int(a, 5),
            filename=_bytes(a, 6, b"").decode("utf-8", "replace"),
            size=_int(a, 7),
            native_id=_int(a, 8),
            favorite=bool(_int(a, 9, 0)),
        ))
    return state


def parse_in_bin(data: bytes) -> list[tuple[uuid.UUID, int, bool, bool]]:
    """Pending app list; a damaged file is treated as empty (the watch rewrites it)."""
    try:
        req = _bytes(_get_service(data), 5)
    except (proto.ProtoError, CiqError) as e:
        log.warning("Ignoring damaged IN.BIN: %s", e)
        return []
    if req is None:
        return []
    try:
        entries = _all_bytes(proto.decode(req), 1)
    except proto.ProtoError as e:
        log.warning("Ignoring damaged IN.BIN: %s", e)
        return []
    out = []
    for raw in entries:
        try:
            a = proto.decode(raw)
        except proto.ProtoError:
            continue
        app_id = _app_id(a)
        if app_id is None:
            continue
        out.append((app_id, _int(a, 2, 0), bool(_int(a, 3, 0)), bool(_int(a, 4, 0))))
    return out


def build_in_bin(apps: list[InstalledApp]) -> bytes:
    """Smart{3: Service{5: UpdateInstalledAppsRequest{1: [InstalledApp]}}}"""
    entries = b"".join(
        proto.enc_bytes(1, proto.enc_bytes(1, a.app_id.bytes) + proto.enc_varint(2, a.app_type)
                        + proto.enc_varint(3, a.disabled) + proto.enc_varint(4, a.favorite))
        for a in apps
    )
    return proto.enc_bytes(3, proto.enc_bytes(5, entries))


def _config_path(device: GarminDevice, direction_input: bool) -> str:
    files = device.targets(CONFIG_DATATYPE) if direction_input else device.sources(CONFIG_DATATYPE)
    if not files:
        raise CiqError(_("This device does not support Connect IQ app configuration"))
    f = files[0]
    return posixpath.join(f.path, f"{f.basename}.{f.extension}")


def read_installed(device: GarminDevice, storage: Storage) -> InstalledState:
    """Current state; pending changes from IN.BIN (not yet applied by the watch) win."""
    state = parse_out_bin(storage.read(_config_path(device, False)))
    in_path = _config_path(device, True)
    if storage.exists(in_path):
        # Keyed by ID and type: a built-in item can be listed as an app and as a widget
        # under the same ID, each with its own flags.
        pending = {(a[0], a[1]): a for a in parse_in_bin(storage.read(in_path))}
        for app in state.apps:
            if (app.app_id, app.app_type) in pending:
                app.disabled, app.favorite = pending.pop((app.app_id, app.app_type))[2:]
        # Apps registered in IN.BIN but not yet reported by the watch must be kept,
        # otherwise the next IN.BIN rewrite would unregister them.
        for app_id, app_type, disabled, favorite in pending.values():
            state.apps.append(InstalledApp(
                app_id=app_id, app_type=app_type, name=str(app_id),
                disabled=disabled, favorite=favorite, pending=True,
            ))
    return state


def require_complete(state: InstalledState) -> None:
    """Refuse to change apps when the app list read from the device is incomplete."""
    if state.unreadable:
        n = state.unreadable
        raise CiqError(ngettext(
            "The Connect IQ app list on the device (OUT.BIN) has {count} entry that cannot be "
            "read. Nothing was changed, so that no app is dropped from the list.",
            "The Connect IQ app list on the device (OUT.BIN) has {count} entries that cannot be "
            "read. Nothing was changed, so that no app is dropped from the list.",
            n).format(count=n))


@dataclass
class InBinPreview:
    """The app list (IN.BIN) an install would write, checked against the device's list."""

    on_device: int  # apps in OUT.BIN
    pending: int  # apps registered by an earlier IN.BIN that the device has not applied yet
    entries: int  # entries in the new IN.BIN
    kept: bool  # every app is in the new list, in the same order, with its current flags
    new: list[tuple[str, int, bool]]  # (name, app type, disabled) of the appended apps


def preview_in_bin(state: InstalledState, targets: list[StoreApp]) -> InBinPreview:
    """What install_queued() would write for these store apps. Nothing is written."""
    new = [InstalledApp(app_id=t.app_id, app_type=t.app_type, name=t.name,
                        disabled=t.app_type == 3)
           for t in targets if state.find(t.app_id) is None]
    written = parse_in_bin(build_in_bin(state.apps + new))
    current = [(a.app_id, a.app_type, a.disabled, a.favorite) for a in state.apps]
    added = [(a.app_id, a.app_type, a.disabled, a.favorite) for a in new]
    pending = sum(1 for a in state.apps if a.pending)
    return InBinPreview(
        on_device=len(state.apps) - pending, pending=pending, entries=len(written),
        kept=written[:len(current)] == current and written[len(current):] == added,
        new=[(a.name, a.app_type, a.disabled) for a in new])


def app_file_path(device: GarminDevice, app: InstalledApp) -> str | None:
    dt = APPTYPE_TO_DATATYPE.get(app.app_type)
    targets = device.targets(dt) if dt else []
    if not targets or not app.filename:
        return None
    t = targets[0]
    return posixpath.join(t.path, f"{app.filename}.{t.extension or 'PRG'}")


def settings_file_path(device: GarminDevice, app: InstalledApp) -> str | None:
    targets = device.targets(SETTINGS_DATATYPE)
    if not targets or not app.filename:
        return None
    return posixpath.join(targets[0].path, f"{app.filename}.{targets[0].extension or 'SET'}")


# --- store API -----------------------------------------------------------------------


class StoreApi:
    def __init__(self, client: GarminClient, base: str = "https://services.garmin.com/"):
        self.client = client
        self.base = base

    def _request(self, method: str, path: str, what: str, json_body=None, params=None,
                 unit_id: int | None = None):
        self.client.require_verified(unit_id)
        url = self.base + path
        if params:
            url += "?" + urlencode(params)
        h = self.client._client_headers()
        h["Accept"] = "application/json"
        h["Authorization"] = self.client._sign(method, url)
        resp = self.client.http.request(method, url, headers=h, json=json_body, timeout=60)
        self.client._check(resp, what)
        return lower_keys(resp.json()) if resp.content else None

    def account_apps(self, device: GarminDevice, locale: str) -> list[StoreApp]:
        data = self._request("GET", "express/appstore/rest/apps/downloads",
                             _("Connect IQ apps"),
                             params={"unitId": device.unit_id, "locale": locale,
                                     "sku": device.part_number}, unit_id=device.unit_id)
        return [_store_app(a) for a in data or []]

    def updates(self, device: GarminDevice, installed: list[InstalledApp], locale: str) -> list[StoreApp]:
        body = {"apps": [{"appId": str(a.app_id), "internalVersionNumber": a.version or 0}
                         for a in installed],
                "deviceSKU": device.part_number, "locale": locale}
        data = self._request("POST", "express/appstore/rest/apps/updates",
                             _("Connect IQ updates"), json_body=body, unit_id=device.unit_id)
        # The server also lists apps that are already current; keep only newer versions.
        current = {a.app_id: a.version or 0 for a in installed}
        return [u for u in (_store_app(a) for a in data or [])
                if u.latest_version > current.get(u.app_id, -1)]

    def queue_install(self, device: GarminDevice, apps: list[StoreApp]) -> None:
        body = {"apps": [{"appId": str(a.app_id), "internalVersionNumber": a.latest_version}
                         for a in apps], "unitId": device.unit_id}
        data = self._request("POST", "express/appstore/rest/apps/installApps",
                             _("Connect IQ install"), json_body=body,
                             unit_id=device.unit_id) or []
        bad = [r for r in data if int(r.get("status") or 0) != 200]
        if bad:
            apps = ", ".join(f"{r.get('appid')} ({r.get('status')})" for r in bad)
            raise CiqError(_("The server refused to queue: {apps}").format(apps=apps))

    def icon_urls(self, app_ids: list[uuid.UUID]) -> dict[uuid.UUID, str]:
        if not app_ids:
            return {}
        data = self._request("POST", "express/appstore/rest/apps/appIconUrl",
                             "Connect IQ icons", json_body=[str(i) for i in app_ids]) or []
        out = {}
        for item in data:
            try:
                out[uuid.UUID(item.get("appid"))] = item.get("iconurl") or ""
            except (TypeError, ValueError):
                continue
        return {k: v for k, v in out.items() if v}

    def forget(self, device: GarminDevice, app_ids: list[uuid.UUID]) -> None:
        body = {"apps": [{"appId": str(i), "internalVersionNumber": 0} for i in app_ids],
                "unitId": device.unit_id}
        self._request("POST", "express/appstore/rest/apps/removeInstalledApps",
                      _("Connect IQ remove"), json_body=body, unit_id=device.unit_id)


def _store_app(a: dict) -> StoreApp:
    return StoreApp(
        app_id=uuid.UUID(a.get("appid")),
        name=a.get("name") or "?",
        developer=a.get("developername") or "",
        store_type=(a.get("type") or "").lower(),
        size=int(a.get("size") or 0),
        latest_version=int(a.get("latestinternalversionnumber") or 0),
        version_name=a.get("latestversionname") or "",
        has_settings=bool(a.get("hassettings")),
        permissions_changed=bool(a.get("permissionschanged")),
    )


# --- install / remove ----------------------------------------------------------------


def is_iq_message(m: dict) -> bool:
    return (m.get("devicexmldatatype") or "") in DATATYPE_TO_APPTYPE


def message_app_id(m: dict) -> uuid.UUID | None:
    details = (m.get("metadata") or {}).get("appdetails") or {}
    try:
        return uuid.UUID(details.get("id"))
    except (TypeError, ValueError):
        return None


@dataclass
class IqInstallReport:
    installed: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    # Left in Garmin's queue: openExpress cannot write them to this device over USB yet.
    for_phone: list[str] = field(default_factory=list)


# --- devices that hide the app folder ------------------------------------------------------
#
# Some watches (e.g. the fēnix 8) keep the app folder declared in GarminDevice.xml out of
# the ordinary MTP listing: gvfs does not see it, is refused when it tries to create it,
# and shows no .PRG files at all. Garmin's own listing returns the folder, so where a
# direct PTP session is possible (`Storage.exclusive_ptp`) the app file and the app list
# are written through it. Where it is not (e.g. in a Flatpak sandbox) such apps are left
# in the queue in Garmin Connect, from which the phone or Garmin Express installs them.

READY, MISSING, REFUSED, DIRECT = "ready", "missing", "refused", "direct"
# Room left on the storage besides the app file itself.
PTP_FREE_MARGIN = 1024 * 1024


def app_dir(device: GarminDevice, datatype: str) -> str | None:
    targets = device.targets(datatype)
    return targets[0].path.strip("/") if targets else None


def store_datatype(app: StoreApp) -> str:
    return APPTYPE_TO_DATATYPE.get(app.app_type, "IQWatchApps")


def _refusals_file(device: GarminDevice) -> Path:
    return paths.device_dir(device.unit_id) / "ciq.json"


def _refusals(device: GarminDevice) -> dict:
    try:
        data = json.loads(_refusals_file(device).read_text())
    except (OSError, ValueError):
        return {}
    refused = data.get("refused_dirs") if isinstance(data, dict) else None
    return refused if isinstance(refused, dict) else {}


def _remember_refusal(device: GarminDevice, path: str) -> None:
    """Remembered per software version, so the device is not asked again on every sync."""
    refused = _refusals(device)
    refused[path.casefold()] = device.software_version_str
    try:
        f = _refusals_file(device)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps({"refused_dirs": refused}, indent=1))
    except OSError as e:
        log.warning("Cannot remember that %s cannot be created: %s", path, e)


def usb_dir_state(device: GarminDevice, storage: Storage, datatype: str) -> str:
    """READY (visible), DIRECT (not visible; will be looked for over PTP), MISSING (not on
    the device; creating it was not tried) or REFUSED."""
    path = app_dir(device, datatype)
    if path is None or storage.resolve(path) is not None:
        return READY
    if storage.exclusive_ptp() is not None:
        return DIRECT
    if _refusals(device).get(path.casefold()) == device.software_version_str:
        return REFUSED
    return MISSING


def prepare_app_dir(device: GarminDevice, storage: Storage, datatype: str) -> bool:
    """Whether app files of this type can be written through the mounted storage; creates
    a missing folder if the device allows it."""
    path = app_dir(device, datatype)
    if path is None or storage.resolve(path) is not None:
        return True
    if _refusals(device).get(path.casefold()) == device.software_version_str:
        return False
    try:
        storage.make_dir(path)
    except DeviceError as e:
        log.info("Cannot install apps on this device over USB (%s): %s", path, e)
        _remember_refusal(device, path)
        return False
    return True


def files_on_device(device: GarminDevice, storage: Storage, app: InstalledApp) -> list[str]:
    """The app's files that are visible on the device (none on devices like the fēnix 8)."""
    return [p for p in (app_file_path(device, app), settings_file_path(device, app))
            if p and storage.exists(p)]


def install_apps(
    device: GarminDevice,
    storage: Storage,
    client: GarminClient,
    api: "StoreApi",
    targets: list[StoreApp],
    progress: Callable[[str], None] = lambda s: None,
) -> IqInstallReport:
    """Queue the apps in Garmin Connect and install over USB those the device accepts.

    Apps whose folder the device does not offer are only queued (`for_phone`): nothing is
    downloaded or written for them, and they are not failures.
    """
    require_complete(read_installed(device, storage))
    usb = [t for t in targets
           if usb_dir_state(device, storage, store_datatype(t)) in (READY, DIRECT)
           or prepare_app_dir(device, storage, store_datatype(t))]
    for_phone = [t.name for t in targets if t not in usb]
    api.queue_install(device, targets)
    report = IqInstallReport()
    if usb:
        report = install_queued(device, storage, client, client.device_messages(device.unit_id),
                                {t.app_id: t for t in targets},
                                only={t.app_id for t in usb}, progress=progress, direct=True)
    report.for_phone = for_phone + [n for n in report.for_phone if n not in for_phone]
    return report


@dataclass
class _Hidden:
    """An app waiting to be written into a folder only PTP shows."""

    message: dict
    name: str
    datatype: str
    path: str
    data: bytes
    entry: InstalledApp
    handle: int | None = None


def install_queued(
    device: GarminDevice,
    storage: Storage,
    client: GarminClient,
    messages: list[dict],
    known: dict[uuid.UUID, StoreApp],
    only: set[uuid.UUID] | None = None,
    progress: Callable[[str], None] = lambda s: None,
    direct: bool = False,
) -> IqInstallReport:
    """Writes queued Connect IQ apps to the watch and registers them in IN.BIN.

    Messages are acknowledged only after the app is written and registered, so
    an interrupted install is retried on the next sync.

    `direct` allows a PTP session for a folder the device hides, which unmounts the
    device for a moment: only for an install the user asked for, never for a sync.
    """
    report, leftover = _install_pass(device, storage, client, messages, known, only, progress,
                                     direct=direct)
    if leftover:
        # The PTP session did not find their folder either: the old way, creating it.
        again, _none = _install_pass(device, storage, client, leftover, known, only, progress,
                                     direct=False)
        report.installed += again.installed
        report.failed += again.failed
        report.for_phone += again.for_phone
    return report


def _install_pass(device, storage, client, messages, known, only, progress,
                  direct: bool) -> tuple[IqInstallReport, list[dict]]:
    report = IqInstallReport()
    state = read_installed(device, storage)
    require_complete(state)
    max_apps = device.iq_max_apps or 0
    new_entries: list[InstalledApp] = []
    new_paths: list[str] = []
    hidden: list[_Hidden] = []
    leftover: list[dict] = []
    to_ack: list[tuple[dict, str]] = []
    new_size = 0
    interrupted = False
    try:
        for m in messages:
            if not is_iq_message(m):
                continue
            app_id = message_app_id(m)
            if app_id is None or (only is not None and app_id not in only):
                continue
            meta = m.get("metadata") or {}
            name = meta.get("messagename") or (known[app_id].name if app_id in known else str(app_id))
            dt = m["devicexmldatatype"]
            existing = state.find(app_id)
            try:
                if (existing is None and max_apps
                        and len(state.store_apps) + len(new_entries) + len(hidden) >= max_apps):
                    raise CiqError(_("no free app slots on the device"))
                over_ptp = direct and usb_dir_state(device, storage, dt) == DIRECT
                if over_ptp and existing is not None:
                    # Replacing a file the device has taken over is not known to work yet.
                    report.for_phone.append(name)
                    continue
                if not over_ptp and not prepare_app_dir(device, storage, dt):
                    # Not an error: the app stays queued and the phone installs it.
                    report.for_phone.append(name)
                    continue
                progress(_("Downloading {name}").format(name=name))
                data = client.download_message(meta.get("messageurl") or "",
                                               bool(meta.get("absolute")))
                if not data:
                    _acknowledge(client, device, m)
                    continue
                if (state.available_space is not None and existing is None
                        and new_size + len(data) > state.available_space):
                    raise CiqError(_("not enough Connect IQ space on the device"))
                targets = device.targets(dt)
                if not targets:
                    raise CiqError(_("the device does not accept {datatype}").format(datatype=dt))
                # Updates overwrite the existing file; new apps get 8 random hex characters.
                filename = (existing.filename if existing and existing.filename
                            else uuid.uuid4().hex[:8].upper())
                path = posixpath.join(targets[0].path, f"{filename}.{targets[0].extension or 'PRG'}")
                entry = None
                if existing is None:
                    store = known.get(app_id)
                    app_type = DATATYPE_TO_APPTYPE[dt]
                    entry = InstalledApp(
                        app_id=app_id, app_type=app_type, name=name,
                        # Like Express: a new watch face is installed but not activated.
                        disabled=app_type == 3, version=store.latest_version if store else None,
                        filename=filename, size=len(data),
                    )
                    new_size += len(data)
                if over_ptp:
                    hidden.append(_Hidden(m, name, dt, path, data, entry))
                    continue
                progress(_("Writing {path}").format(path=path))
                try:
                    real = storage.write(path, data)
                    if storage.size(real) != len(data):
                        raise CiqError(_("Write verification of {path} failed").format(path=real))
                except Exception:
                    if existing is None:
                        _delete_quietly(storage, path)
                        new_size -= len(data)
                    raise
                if entry is not None:
                    new_entries.append(entry)
                    new_paths.append(real)
                to_ack.append((m, name))
            except ApiError:
                raise
            except Exception as e:  # noqa: BLE001
                report.failed.append((name, str(e)))
    except BaseException:
        # Nothing was written for the apps meant for PTP; they stay in the queue.
        interrupted = True
        raise
    finally:
        registered = False
        if hidden and not interrupted:
            registered = _install_hidden(device, storage, state, hidden, new_entries, new_paths,
                                         to_ack, leftover, report, progress)
        if new_entries and not registered:
            try:
                storage.write(_config_path(device, True), build_in_bin(state.apps + new_entries))
            except Exception as e:
                for p in new_paths:
                    _delete_quietly(storage, p)
                report.failed += [(name, str(e)) for _m, name in to_ack]
                raise CiqError(_("Cannot register the apps on the device: {error}")
                               .format(error=e)) from e
        for m, name in to_ack:
            try:
                _acknowledge(client, device, m)
            except Exception as e:  # noqa: BLE001
                # The app is installed; the server will offer it again and it will be overwritten.
                report.failed.append((name, str(e)))
                continue
            report.installed.append(name)
    return report, leftover


def _acknowledge(client: GarminClient, device: GarminDevice, message: dict) -> None:
    """Tell Garmin the message was delivered. Garmin may have dropped it from the queue
    already (it does once the app is on the device): that is not an error."""
    try:
        client.ack_message(message.get("messageid"))
    except Exception:
        try:
            queued = {m.get("messageid") for m in client.device_messages(device.unit_id)}
        except Exception:  # noqa: BLE001 - report the first error, not this one
            queued = {message.get("messageid")}
        if message.get("messageid") in queued:
            raise
        log.info("Message %s was no longer in the queue", message.get("messageid"))


def _install_hidden(device, storage, state, hidden: list[_Hidden], new_entries, new_paths,
                    to_ack, leftover, report, progress) -> bool:
    """Write the apps of `hidden` and the app list through a PTP session. Returns whether
    the app list (IN.BIN) was written there, with `new_entries` in it."""
    in_path = _config_path(device, True)
    if storage.exists(in_path):
        # Not replaced over PTP; the device consumes it once it is unplugged.
        report.failed += [(h.name, _("the device has not applied earlier changes yet; unplug "
                                     "it, wait until it is ready and try again"))
                          for h in hidden]
        return False
    opener = storage.exclusive_ptp()
    sent: list[_Hidden] = []
    settled: set[int] = set()  # id() of the entries of `hidden` already reported
    opened = registered = False
    register_error: Exception | None = None

    def fail(h: _Hidden, error) -> None:
        settled.add(id(h))
        report.failed.append((h.name, str(error)))

    try:
        with opener as session:
            opened = True
            store = _ptp_store(session)
            for h in hidden:
                folder = session.lookup(store, posixpath.dirname(h.path))
                if folder is None or not folder.is_dir:
                    settled.add(id(h))
                    leftover.append(h.message)
                    continue
                progress(_("Writing {path}").format(path=h.path))
                try:
                    free = session.free_space(store)
                    if free is not None and free < len(h.data) + PTP_FREE_MARGIN:
                        raise CiqError(_("not enough free space on the device"))
                    h.handle = session.send_file(store, folder.handle,
                                                 posixpath.basename(h.path), h.data).handle
                    sent.append(h)
                except ptp.PtpWriteError as e:
                    fail(h, _with_leftovers(e, _ptp_delete(session, e.handle, h.path)))
                except (DeviceError, CiqError) as e:
                    fail(h, e)
            if sent:
                entries = state.apps + new_entries + [h.entry for h in sent if h.entry]
                try:
                    parent = session.lookup(store, posixpath.dirname(in_path))
                    if parent is None:
                        raise CiqError(_("the app list folder was not found"))
                    session.send_file(store, parent.handle, posixpath.basename(in_path),
                                      build_in_bin(entries), verify_content=True)
                    registered = True
                except (DeviceError, CiqError) as e:
                    left = (_ptp_delete(session, e.handle, in_path)
                            if isinstance(e, ptp.PtpWriteError) else [])
                    for h in sent:
                        left += _ptp_delete(session, h.handle, h.path)
                    register_error = CiqError(_with_leftovers(e, left))
    except ptp.UsbUnavailable as e:
        # No access to the USB device: as on a device without a PTP session.
        log.info("Cannot open the device over USB: %s", e)
        report.for_phone += [h.name for h in hidden]
        return False
    except RemountFailed as e:
        report.failed.append((_("USB connection"), _(
            "{error} Unplug the device and plug it in again.").format(error=e)))
        if not opened:
            report.for_phone += [h.name for h in hidden]
    except DeviceError as e:
        # The session broke: what was sent is not registered, the rest was not sent.
        for h in hidden:
            if id(h) not in settled:
                fail(h, e)
        return False
    if register_error is not None:
        for p in new_paths:
            _delete_quietly(storage, p)
        report.failed += [(name, str(register_error)) for _m, name in to_ack]
        report.failed += [(h.name, str(register_error)) for h in sent]
        to_ack.clear()
        raise CiqError(_("Cannot register the apps on the device: {error}")
                       .format(error=register_error)) from register_error
    if registered:
        to_ack += [(h.message, h.name) for h in sent]
    return registered


def _ptp_store(session) -> int:
    """The storage that holds the GARMIN folder."""
    stores = session.storages()
    for store in stores:
        if session.lookup(store, "GARMIN") is not None:
            return store
    raise CiqError(_("the GARMIN folder was not found on the device"))


def _ptp_delete(session, handle: int | None, path: str) -> list[str]:
    """Delete a file that was not finished. Returns the path when it could not be
    deleted, for the user to be told."""
    if handle is None:
        return []
    try:
        session.delete(handle)
    except DeviceError as e:
        log.warning("Cannot delete the unfinished file %s on the device: %s", path, e)
        return [path]
    return []


def _with_leftovers(error, paths: list[str]) -> str:
    if not paths:
        return str(error)
    return str(error) + " " + ngettext(
        "An unfinished file may have been left on the device: {paths}.",
        "Unfinished files may have been left on the device: {paths}.",
        len(paths)).format(paths=", ".join(paths))


def _delete_quietly(storage: Storage, path: str) -> None:
    try:
        storage.delete(path)
    except Exception as e:  # noqa: BLE001
        log.warning("Cannot delete %s: %s", path, e)


def remove(device: GarminDevice, storage: Storage, app: InstalledApp) -> list[str]:
    if not app.removable:
        raise CiqError(_("{name} is part of the device software and cannot be removed")
                       .format(name=app.name))
    require_complete(read_installed(device, storage))
    removed = []
    for p in files_on_device(device, storage, app):
        storage.delete(p)
        removed.append(p)
    # Like Express, only the files are deleted; the watch updates its app list itself.
    return removed


def set_enabled(device: GarminDevice, storage: Storage, app_id: uuid.UUID, enabled: bool,
                app_type: int | None = None) -> None:
    state = read_installed(device, storage)
    require_complete(state)
    if app_type is None and sum(1 for a in state.apps if a.app_id == app_id) > 1:
        raise CiqError(_("Several entries on the device share this ID; the type is needed"))
    app = state.find(app_id, app_type)
    if app is None:
        raise CiqError(_("App not found on the device"))
    app.disabled = not enabled
    storage.write(_config_path(device, True), build_in_bin(state.apps))
