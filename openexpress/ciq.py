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
from openexpress.device.storage import DeviceError, Storage
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

    def find(self, app_id: uuid.UUID) -> InstalledApp | None:
        return next((a for a in self.apps if a.app_id == app_id), None)


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
        pending = {a[0]: a for a in parse_in_bin(storage.read(in_path))}
        for app in state.apps:
            if app.app_id in pending:
                app.disabled, app.favorite = pending.pop(app.app_id)[2:]
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


# --- devices where openExpress cannot install apps over USB yet ---------------------------
#
# Some watches (e.g. the fēnix 8) declare an app folder in GarminDevice.xml that does not
# exist on the device; gvfs/libmtp is refused when it tries to create it, and no .PRG files
# are visible over MTP at all. Garmin Express on Windows does install over USB there, so
# this is a limit of openExpress, not of the device. Until it is solved such apps are left
# in the queue in Garmin Connect, from which the phone or Garmin Express installs them.

READY, MISSING, REFUSED = "ready", "missing", "refused"


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
    """READY, MISSING (not on the device; creating it was not tried) or REFUSED."""
    path = app_dir(device, datatype)
    if path is None or storage.resolve(path) is not None:
        return READY
    if _refusals(device).get(path.casefold()) == device.software_version_str:
        return REFUSED
    return MISSING


def prepare_app_dir(device: GarminDevice, storage: Storage, datatype: str) -> bool:
    """Whether app files of this type can be written; creates a missing folder if allowed."""
    state = usb_dir_state(device, storage, datatype)
    if state != MISSING:
        return state == READY
    path = app_dir(device, datatype)
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
    usb = [t for t in targets if prepare_app_dir(device, storage, store_datatype(t))]
    for_phone = [t.name for t in targets if t not in usb]
    api.queue_install(device, targets)
    report = IqInstallReport()
    if usb:
        report = install_queued(device, storage, client, client.device_messages(device.unit_id),
                                {t.app_id: t for t in targets},
                                only={t.app_id for t in usb}, progress=progress)
    report.for_phone = for_phone + [n for n in report.for_phone if n not in for_phone]
    return report


def install_queued(
    device: GarminDevice,
    storage: Storage,
    client: GarminClient,
    messages: list[dict],
    known: dict[uuid.UUID, StoreApp],
    only: set[uuid.UUID] | None = None,
    progress: Callable[[str], None] = lambda s: None,
) -> IqInstallReport:
    """Writes queued Connect IQ apps to the watch and registers them in IN.BIN.

    Messages are acknowledged only after the app is written and registered, so
    an interrupted install is retried on the next sync.
    """
    report = IqInstallReport()
    state = read_installed(device, storage)
    require_complete(state)
    max_apps = device.iq_max_apps or 0
    new_entries: list[InstalledApp] = []
    new_paths: list[str] = []
    to_ack: list[tuple[dict, str]] = []
    new_size = 0
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
                        and len(state.store_apps) + len(new_entries) >= max_apps):
                    raise CiqError(_("no free app slots on the device"))
                if not prepare_app_dir(device, storage, dt):
                    # Not an error: the app stays queued and the phone installs it.
                    report.for_phone.append(name)
                    continue
                progress(_("Downloading {name}").format(name=name))
                data = client.download_message(meta.get("messageurl") or "",
                                               bool(meta.get("absolute")))
                if not data:
                    client.ack_message(m.get("messageid"))
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
                progress(_("Writing {path}").format(path=path))
                try:
                    real = storage.write(path, data)
                    if storage.size(real) != len(data):
                        raise CiqError(_("Write verification of {path} failed").format(path=real))
                except Exception:
                    if existing is None:
                        _delete_quietly(storage, path)
                    raise
                if existing is None:
                    store = known.get(app_id)
                    app_type = DATATYPE_TO_APPTYPE[dt]
                    new_entries.append(InstalledApp(
                        app_id=app_id, app_type=app_type, name=name,
                        # Like Express: a new watch face is installed but not activated.
                        disabled=app_type == 3, version=store.latest_version if store else None,
                        filename=filename, size=len(data),
                    ))
                    new_paths.append(real)
                    new_size += len(data)
                to_ack.append((m, name))
            except ApiError:
                raise
            except Exception as e:  # noqa: BLE001
                report.failed.append((name, str(e)))
    finally:
        if new_entries:
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
                client.ack_message(m.get("messageid"))
            except Exception as e:  # noqa: BLE001
                # The app is installed; the server will offer it again and it will be overwritten.
                report.failed.append((name, str(e)))
                continue
            report.installed.append(name)
    return report


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


def set_enabled(device: GarminDevice, storage: Storage, app_id: uuid.UUID, enabled: bool) -> None:
    state = read_installed(device, storage)
    require_complete(state)
    app = state.find(app_id)
    if app is None:
        raise CiqError(_("App not found on the device"))
    app.disabled = not enabled
    storage.write(_config_path(device, True), build_in_bin(state.apps))
