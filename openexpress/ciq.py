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

import posixpath
import uuid
from dataclasses import dataclass, field
from typing import Callable

from urllib.parse import urlencode

from openexpress import proto
from openexpress.connect import ApiError, GarminClient, lower_keys
from openexpress.device.storage import Storage
from openexpress.device.xmldevice import GarminDevice
from openexpress.i18n import N_, _

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


def _get_service(data: bytes) -> list[proto.Field]:
    smart = proto.decode(data)
    svc = proto.first(smart, 3)
    if svc is None:
        raise CiqError(_("Not a Connect IQ configuration file"))
    return proto.decode(svc)


def parse_out_bin(data: bytes) -> InstalledState:
    resp = proto.first(_get_service(data), 2)
    if resp is None:
        raise CiqError(_("OUT.BIN does not contain an installed-apps response"))
    fields = proto.decode(resp)
    state = InstalledState(
        available_space=proto.first(fields, 1),
        available_slots=proto.first(fields, 2),
    )
    for raw in proto.all_of(fields, 3):
        a = proto.decode(raw)
        app_id = proto.first(a, 1, b"")
        if len(app_id) != 16:
            continue
        state.apps.append(InstalledApp(
            app_id=uuid.UUID(bytes=app_id),
            app_type=proto.first(a, 2, 0),
            name=proto.first(a, 3, b"").decode("utf-8", "replace"),
            disabled=bool(proto.first(a, 4, 0)),
            version=proto.first(a, 5),
            filename=proto.first(a, 6, b"").decode("utf-8", "replace"),
            size=proto.first(a, 7),
            native_id=proto.first(a, 8),
            favorite=bool(proto.first(a, 9, 0)),
        ))
    return state


def parse_in_bin(data: bytes) -> list[tuple[uuid.UUID, int, bool, bool]]:
    req = proto.first(_get_service(data), 5)
    if req is None:
        return []
    out = []
    for raw in proto.all_of(proto.decode(req), 1):
        a = proto.decode(raw)
        out.append((uuid.UUID(bytes=proto.first(a, 1)), proto.first(a, 2, 0),
                    bool(proto.first(a, 3, 0)), bool(proto.first(a, 4, 0))))
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
                _, _, app.disabled, app.favorite = pending[app.app_id]
    return state


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

    def _request(self, method: str, path: str, what: str, json_body=None, params=None):
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
                                     "sku": device.part_number})
        return [_store_app(a) for a in data or []]

    def updates(self, device: GarminDevice, installed: list[InstalledApp], locale: str) -> list[StoreApp]:
        body = {"apps": [{"appId": str(a.app_id), "internalVersionNumber": a.version or 0}
                         for a in installed],
                "deviceSKU": device.part_number, "locale": locale}
        data = self._request("POST", "express/appstore/rest/apps/updates",
                             _("Connect IQ updates"), json_body=body)
        # The server also lists apps that are already current; keep only newer versions.
        current = {a.app_id: a.version or 0 for a in installed}
        return [u for u in (_store_app(a) for a in data or [])
                if u.latest_version > current.get(u.app_id, -1)]

    def queue_install(self, device: GarminDevice, apps: list[StoreApp]) -> None:
        body = {"apps": [{"appId": str(a.app_id), "internalVersionNumber": a.latest_version}
                         for a in apps], "unitId": device.unit_id}
        data = self._request("POST", "express/appstore/rest/apps/installApps",
                             _("Connect IQ install"), json_body=body) or []
        bad = [r for r in data if int(r.get("status") or 0) != 200]
        if bad:
            apps = ", ".join(f"{r.get('appid')} ({r.get('status')})" for r in bad)
            raise CiqError(_("The server refused to queue: {apps}").format(apps=apps))

    def forget(self, device: GarminDevice, app_ids: list[uuid.UUID]) -> None:
        body = {"apps": [{"appId": str(i), "internalVersionNumber": 0} for i in app_ids],
                "unitId": device.unit_id}
        self._request("POST", "express/appstore/rest/apps/removeInstalledApps",
                      _("Connect IQ remove"), json_body=body)


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


def install_queued(
    device: GarminDevice,
    storage: Storage,
    client: GarminClient,
    messages: list[dict],
    known: dict[uuid.UUID, StoreApp],
    only: set[uuid.UUID] | None = None,
    progress: Callable[[str], None] = lambda s: None,
) -> IqInstallReport:
    """Writes queued Connect IQ apps to the watch and registers them in IN.BIN."""
    report = IqInstallReport()
    state = read_installed(device, storage)
    max_apps = device.iq_max_apps or 0
    new_entries: list[InstalledApp] = []
    for m in messages:
        if not is_iq_message(m):
            continue
        app_id = message_app_id(m)
        if app_id is None or (only is not None and app_id not in only):
            continue
        meta = m.get("metadata") or {}
        name = meta.get("messagename") or (known.get(app_id).name if app_id in known else str(app_id))
        dt = m["devicexmldatatype"]
        existing = state.find(app_id)
        try:
            if existing is None and max_apps and len(state.store_apps) + len(new_entries) >= max_apps:
                raise CiqError(_("no free app slots on the watch"))
            progress(_("Downloading {name}").format(name=name))
            data = client.download_message(meta.get("messageurl") or "", bool(meta.get("absolute")))
            if not data:
                client.ack_message(m.get("messageid"))
                continue
            if state.available_space is not None and existing is None and len(data) > state.available_space:
                raise CiqError(_("not enough Connect IQ space on the watch"))
            targets = device.targets(dt)
            if not targets:
                raise CiqError(_("the watch does not accept {datatype}").format(datatype=dt))
            # Updates overwrite the existing file; new apps get 8 random hex characters.
            filename = existing.filename if existing and existing.filename else uuid.uuid4().hex[:8].upper()
            path = posixpath.join(targets[0].path, f"{filename}.{targets[0].extension or 'PRG'}")
            progress(_("Writing {path}").format(path=path))
            storage.write(path, data)
            client.ack_message(m.get("messageid"))
            store = known.get(app_id)
            app_type = DATATYPE_TO_APPTYPE[dt]
            if existing is None:
                new_entries.append(InstalledApp(
                    app_id=app_id, app_type=app_type, name=name,
                    # Like Express: a new watch face is installed but not activated.
                    disabled=app_type == 3, version=store.latest_version if store else None,
                    filename=filename, size=len(data),
                ))
            report.installed.append(name)
        except ApiError:
            raise
        except Exception as e:  # noqa: BLE001
            report.failed.append((name, str(e)))
    if new_entries:
        storage.write(_config_path(device, True), build_in_bin(state.apps + new_entries))
    return report


def remove(device: GarminDevice, storage: Storage, app: InstalledApp) -> list[str]:
    if not app.removable:
        raise CiqError(_("{name} is part of the watch software and cannot be removed")
                       .format(name=app.name))
    removed = []
    for p in (app_file_path(device, app), settings_file_path(device, app)):
        if p and storage.exists(p):
            storage.delete(p)
            removed.append(p)
    # Like Express, only the files are deleted; the watch updates its app list itself.
    return removed


def set_enabled(device: GarminDevice, storage: Storage, app_id: uuid.UUID, enabled: bool) -> None:
    state = read_installed(device, storage)
    app = state.find(app_id)
    if app is None:
        raise CiqError(_("App not found on the watch"))
    app.disabled = not enabled
    storage.write(_config_path(device, True), build_in_bin(state.apps))
