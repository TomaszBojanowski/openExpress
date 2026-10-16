"""Connect IQ apps: watch faces, data fields, widgets and apps.

Mirrors Garmin Express:
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
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode

import requests

from openexpress import applog, netstatus, paths, proto
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
    # No visible files, but the file can be reached and deleted over a PTP session.
    direct_removal: bool = False
    # Still in OUT.BIN, but left out of the IN.BIN the device has not applied yet: it goes
    # once the device is unplugged.
    pending_removal: bool = False

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
    # An app list (IN.BIN) is on the device that it has not applied yet.
    waiting: bool = False

    @property
    def store_apps(self) -> list[InstalledApp]:
        return [a for a in self.apps if not a.preloaded]

    @property
    def listed(self) -> list[InstalledApp]:
        """The apps a new app list (IN.BIN) has to name: all but those being removed."""
        return [a for a in self.apps if not a.pending_removal]

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
        state.waiting = True
        # Keyed by ID and type: a built-in item can be listed as an app and as a widget
        # under the same ID, each with its own flags.
        pending = {(a[0], a[1]): a for a in parse_in_bin(storage.read(in_path))}
        for app in state.apps:
            if (app.app_id, app.app_type) in pending:
                app.disabled, app.favorite = pending.pop((app.app_id, app.app_type))[2:]
            elif app.removable:
                # A store app the waiting list no longer names: it was removed.
                app.pending_removal = True
        # Apps registered in IN.BIN but not yet reported by the watch must be kept,
        # otherwise the next IN.BIN rewrite would unregister them.
        for app_id, app_type, disabled, favorite in pending.values():
            state.apps.append(InstalledApp(
                app_id=app_id, app_type=app_type, name=str(app_id),
                disabled=disabled, favorite=favorite, pending=True,
            ))
    return state


def name_pending(state: InstalledState, known: list[StoreApp]) -> None:
    """IN.BIN carries no names: give the apps that wait for the device the name and version
    the account knows them by."""
    by_id = {k.app_id: k for k in known}
    for app in state.apps:
        store = by_id.get(app.app_id) if app.pending else None
        if store is not None:
            app.name, app.version = store.name, store.latest_version


def written_text(names: list[str]) -> str:
    """For apps written over a PTP session: the device installs them once unplugged."""
    return ngettext(
        "Wrote {names} to the device; it will be installed after the device is unplugged.",
        "Wrote {names} to the device; they will be installed after the device is unplugged.",
        len(names)).format(names=", ".join(names))


def update_written_text(names: list[str]) -> str:
    """For new versions written over a PTP session: the device replaces the apps itself."""
    return ngettext(
        "Wrote the update of {names} to the device; it will be installed after the device "
        "is unplugged.",
        "Wrote the updates of {names} to the device; they will be installed after the device "
        "is unplugged.",
        len(names)).format(names=", ".join(names))


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
    written = parse_in_bin(build_in_bin(state.listed + new))
    current = [(a.app_id, a.app_type, a.disabled, a.favorite) for a in state.listed]
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
        started = time.monotonic()
        try:
            resp = self.client.http.request(method, url, headers=h, json=json_body, timeout=60)
        except requests.RequestException:
            netstatus.record(netstatus.CONNECT, url, None)
            raise
        log.info("HTTP %s %s -> %s in %.1f s", method, self.base + path, resp.status_code,
                 time.monotonic() - started)
        netstatus.record(netstatus.CONNECT, url, resp.status_code, resp)
        self.client._check(resp, what)
        return lower_keys(resp.json()) if resp.content else None

    def account_apps(self, device: GarminDevice, locale: str) -> list[StoreApp]:
        data = self._request("GET", "express/appstore/rest/apps/downloads",
                             _("Connect IQ apps"),
                             params={"unitId": device.unit_id, "locale": locale,
                                     "sku": device.part_number}, unit_id=device.unit_id)
        return [_store_app(a) for a in data or []]

    def updates(self, device: GarminDevice, installed: list[InstalledApp],
                locale: str) -> list[StoreApp]:
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
    # Those of `installed` that were written over a PTP session: the device installs them
    # only once it is unplugged.
    direct: list[str] = field(default_factory=list)
    # Those of `direct` that are new versions of apps already on the device.
    updated: list[str] = field(default_factory=list)
    updated_ids: list[uuid.UUID] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    # Left in Garmin's queue: openExpress cannot write them to this device over USB yet.
    for_phone: list[str] = field(default_factory=list)
    # Those of `for_phone` that only the Flatpak sandbox keeps from the device, and those
    # of them that are new versions of apps already on the device.
    sandboxed: list[str] = field(default_factory=list)
    sandboxed_updates: list[str] = field(default_factory=list)


# --- devices that hide the app folder ------------------------------------------------------
#
# Some watches (e.g. the fēnix 8) keep the app folder declared in GarminDevice.xml out of
# the ordinary MTP listing: gvfs does not see it, is refused when it tries to create it,
# and shows no .PRG files at all. Garmin's own listing returns the folder, so where a
# direct PTP session is possible (`Storage.exclusive_ptp`) the app file and the app list
# are written through it. Where it is not (e.g. in a Flatpak without access to USB) such
# apps are left in the queue in Garmin Connect, from which the phone or Garmin Express
# installs them. There nothing is tried on the device at all (SANDBOXED): the folder is
# there, creating it can only be refused.
#
# A new version of an app is written the same way, under a new file name next to the old
# file, and the app list is written unchanged; the device replaces the app after it is
# unplugged (it keeps the old file name). Nothing of the old version is deleted by us.

READY, MISSING, REFUSED, DIRECT = "ready", "missing", "refused", "direct"
SANDBOXED = "sandboxed"
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
        paths.make_dir(f.parent)
        f.write_text(json.dumps({"refused_dirs": refused}, indent=1))
    except OSError as e:
        log.warning("Cannot remember that %s cannot be created: %s", path, e)


def _hides_app_files(device: GarminDevice, storage: Storage) -> bool:
    """Whether the device lists an app with a file of its own that the storage does not
    show: its app folder exists and is only hidden."""
    try:
        apps = parse_out_bin(storage.read(_config_path(device, False))).apps
    except (DeviceError, CiqError):
        return False
    paths_ = [app_file_path(device, a) for a in apps]
    return any(p and not storage.exists(p) for p in paths_)


def usb_dir_state(device: GarminDevice, storage: Storage, datatype: str) -> str:
    """READY (visible), DIRECT (not visible; will be looked for over PTP), SANDBOXED (hidden
    by the device and out of reach in a sandbox; nothing is tried), MISSING (not on the
    device; creating it was not tried) or REFUSED."""
    path = app_dir(device, datatype)
    if path is None or storage.resolve(path) is not None:
        return READY
    if storage.exclusive_ptp() is not None:
        return DIRECT
    if storage.usb_blocked() and _hides_app_files(device, storage):
        return SANDBOXED
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
    if usb_dir_state(device, storage, datatype) == SANDBOXED:
        return False  # the folder is there; asking the device to create it is pointless
    save_app_list_copy(device, storage)  # creating the folder is a change too
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


def queued_app_ids(messages: list[dict]) -> set[uuid.UUID]:
    """The Connect IQ apps waiting in Garmin's queue for the device."""
    return {app_id for app_id in (message_app_id(m) for m in messages if is_iq_message(m))
            if app_id is not None}


# What stands in the way of installing an app. Each check raises CiqError with the reason;
# `install_refusals` asks them before anything is requested or written, `_install_pass`
# again while it writes (a sync does not ask first, and the file may differ in size).


def _check_slot(free_slots: int | None, taken: int) -> None:
    """`taken`: the new apps already counted in this run."""
    if free_slots is not None and taken >= free_slots:
        raise CiqError(_("no free app slots on the device"))


def _check_not_leaving(existing: InstalledApp | None) -> None:
    if existing is not None and existing.pending_removal:
        raise CiqError(_("the device has not applied earlier changes yet; unplug "
                         "it, wait until it is ready and try again"))


def _check_space(state: InstalledState, used: int, size: int) -> None:
    """`used`: the bytes already counted in this run."""
    if state.available_space is not None and used + size > state.available_space:
        raise CiqError(_("not enough Connect IQ space on the device"))


def _check_accepted(device: GarminDevice, datatype: str) -> list:
    targets = device.targets(datatype)
    if not targets:
        raise CiqError(_("the device does not accept {datatype}").format(datatype=datatype))
    return targets


def install_refusals(device: GarminDevice, storage: Storage, state: InstalledState,
                     targets: list[StoreApp]) -> dict[uuid.UUID, str]:
    """Why some of these store apps cannot be installed over USB: app ID -> reason. Asked
    before anything is requested in Garmin Connect, downloaded or written; it reads nothing
    new. The apps are taken in order: one that fits uses up its slot and its space.

    The size is the one the account lists; the file that is downloaded later can differ a
    little, so the space is checked again when it is written."""
    refused: dict[uuid.UUID, str] = {}
    free_slots = _free_slots(device, state)
    new = used = 0
    for t in targets:
        existing = state.find(t.app_id)
        datatype = store_datatype(t)
        # An update over PTP is written next to the old file: it needs room of its own.
        needs_room = existing is None or usb_dir_state(device, storage, datatype) == DIRECT
        try:
            if existing is None:
                _check_slot(free_slots, new)
            _check_not_leaving(existing)
            _check_accepted(device, datatype)
            if needs_room:
                _check_space(state, used, t.size)
        except CiqError as e:
            refused[t.app_id] = str(e)
            continue
        new += existing is None
        used += t.size if needs_room else 0
    return refused


def install_apps(
    device: GarminDevice,
    storage: Storage,
    client: GarminClient,
    api: StoreApi,
    targets: list[StoreApp],
    progress: Callable[[str], None] = lambda s: None,
) -> IqInstallReport:
    """Queue the apps in Garmin Connect (unless they already wait there) and install over
    USB those the device accepts.

    Apps whose folder the device does not offer are only queued (`for_phone`): nothing is
    downloaded or written for them, and they are not failures.
    """
    state = read_installed(device, storage)
    require_complete(state)
    states = {t.app_id: usb_dir_state(device, storage, store_datatype(t)) for t in targets}
    # Before the copy and before Garmin is asked for anything: an app the device has no
    # room for is not requested, so that it does not stay in the account's queue.
    refused = install_refusals(device, storage, state, [
        t for t in targets if states[t.app_id] not in (REFUSED, SANDBOXED)])
    failed = [(t.name, refused[t.app_id]) for t in targets if t.app_id in refused]
    targets = [t for t in targets if t.app_id not in refused]
    if not targets:
        report = IqInstallReport(failed=failed)
        _log_install(report)
        return report
    states = {t.app_id: states[t.app_id] for t in targets}
    if any(s not in (REFUSED, SANDBOXED) for s in states.values()):
        # Something may be written: the copy comes first, before Garmin is asked for anything.
        save_app_list_copy(device, storage)
    usb = [t for t in targets
           if usb_dir_state(device, storage, store_datatype(t)) in (READY, DIRECT)
           or prepare_app_dir(device, storage, store_datatype(t))]
    for_phone = [t.name for t in targets if t not in usb]
    # An app that already waits in Garmin's queue for this device is not requested again.
    messages = client.device_messages(device.unit_id)
    waiting = queued_app_ids(messages)
    to_queue = [t for t in targets if t.app_id not in waiting]
    if to_queue:
        api.queue_install(device, to_queue)
        if usb:
            messages = client.device_messages(device.unit_id)
    report = IqInstallReport()
    if usb:
        report = install_queued(device, storage, client, messages,
                                {t.app_id: t for t in targets},
                                only={t.app_id for t in usb}, progress=progress, direct=True)
    report.failed = failed + report.failed
    report.for_phone = for_phone + [n for n in report.for_phone if n not in for_phone]
    have = {a.app_id for a in state.store_apps}
    report.sandboxed += [t.name for t in targets if states[t.app_id] == SANDBOXED
                         and t.name not in report.sandboxed]
    report.sandboxed_updates = [t.name for t in targets
                                if states[t.app_id] == SANDBOXED and t.app_id in have]
    log.info("apps install: %s; app folder: %s; requested in Garmin Connect: %d",
             ", ".join(t.name for t in targets) or "-", applog.counts(states.values()),
             len(to_queue))
    _log_install(report)
    return report


def _log_install(report: IqInstallReport) -> None:
    log.info("apps written: %d (over PTP %d, updates %d); left in the queue %d (sandbox %d); "
             "failed %d", len(report.installed), len(report.direct), len(report.updated),
             len(report.for_phone), len(report.sandboxed), len(report.failed))
    for name, error in report.failed:
        log.warning("app %s failed: %s", name, error)


def sandbox_text(update: bool = False, queued: bool = False) -> str:
    """Why an app is only queued in a Flatpak without access to USB, before (`queued`
    False) or after it was queued."""
    if update:
        return " ".join((
            _("The Flatpak has no access to USB devices, so it cannot update apps on this "
              "device."),
            _("The update waits in the queue in Garmin Connect and will be installed by the "
              "phone or Garmin Express.") if queued else
            _("The update will be queued in Garmin Connect and installed by the phone or "
              "Garmin Express.")))
    return " ".join((
        _("The Flatpak has no access to USB devices, so it cannot install apps on this "
          "device."),
        _("The app waits in the queue in Garmin Connect and will be installed by the phone "
          "or Garmin Express.") if queued else
        _("The app will be queued in Garmin Connect and installed by the phone or Garmin "
          "Express.")))


def sandbox_removal_text() -> str:
    return " ".join((
        _("The Flatpak has no access to USB devices, so it cannot remove apps from this "
          "device."),
        _("Remove it on the phone, in the Connect IQ or Garmin Connect app.")))


def removal_sandboxed(device: GarminDevice, storage: Storage, app: InstalledApp) -> bool:
    """Whether only a Flatpak without access to USB keeps the app's file out of reach."""
    dt = APPTYPE_TO_DATATYPE.get(app.app_type)
    return bool(dt and app.filename and usb_dir_state(device, storage, dt) == SANDBOXED)


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
        report.direct += again.direct
        report.updated += again.updated
        report.updated_ids += again.updated_ids
        report.failed += again.failed
        report.for_phone += again.for_phone
        report.sandboxed += again.sandboxed
    if not direct:
        _log_install(report)  # a sync; an install the user asked for is logged by its caller
    return report


def _free_slots(device: GarminDevice, state: InstalledState) -> int | None:
    """How many more apps the device takes; None when nothing is known.

    The device reports its free slots in its app list (OUT.BIN), and that number counts
    what the list does not name (a Venu 2 reports 31 of 32 with no store app listed). It
    does not count the apps that wait for the device: written earlier and registered in
    the app list for the device (IN.BIN), but not yet in OUT.BIN. `read_installed` marks
    those `pending`; an app that is in OUT.BIN is already counted by the device.

    The declared limit less the store apps listed is the other bound; the smaller wins."""
    bounds = []
    if state.available_slots is not None:
        waiting = sum(1 for a in state.store_apps if a.pending)
        bounds.append(state.available_slots - waiting)
    if device.iq_max_apps:
        bounds.append(device.iq_max_apps - len(state.store_apps))
    return max(0, min(bounds)) if bounds else None


def _install_pass(device, storage, client, messages, known, only, progress,
                  direct: bool) -> tuple[IqInstallReport, list[dict]]:
    report = IqInstallReport()
    state = read_installed(device, storage)
    require_complete(state)
    free_slots = _free_slots(device, state)
    new_entries: list[InstalledApp] = []
    new_paths: list[str] = []
    hidden: list[_Hidden] = []
    leftover: list[dict] = []
    to_ack: list[tuple[dict, str]] = []
    new_size = 0
    interrupted = False
    copied = False

    def copy_first() -> None:
        """Before the first thing this pass writes to the device."""
        nonlocal copied
        if not copied:
            save_app_list_copy(device, storage)
            copied = True

    try:
        for m in messages:
            if not is_iq_message(m):
                continue
            app_id = message_app_id(m)
            if app_id is None or (only is not None and app_id not in only):
                continue
            meta = m.get("metadata") or {}
            name = meta.get("messagename") or (
                known[app_id].name if app_id in known else str(app_id))
            dt = m["devicexmldatatype"]
            existing = state.find(app_id)
            try:
                if existing is None:
                    _check_slot(free_slots, len(new_entries) + len(hidden))
                _check_not_leaving(existing)
                over_ptp = direct and usb_dir_state(device, storage, dt) == DIRECT
                if not over_ptp and not prepare_app_dir(device, storage, dt):
                    # Not an error: the app stays queued and the phone installs it.
                    report.for_phone.append(name)
                    if usb_dir_state(device, storage, dt) == SANDBOXED:
                        report.sandboxed.append(name)
                    continue
                copy_first()
                progress(_("Downloading {name}").format(name=name))
                data = client.download_message(meta.get("messageurl") or "",
                                               bool(meta.get("absolute")))
                if not data:
                    _acknowledge(client, device, m)
                    continue
                # An update over PTP is written next to the old file, which the device
                # replaces only later: it needs room of its own, like a new app.
                if existing is None or over_ptp:
                    _check_space(state, new_size, len(data))
                targets = _check_accepted(device, dt)
                # New apps get 8 random hex characters. An update through the mounted storage
                # overwrites the existing file; over PTP it gets a new name too, like Garmin
                # Express does: the old file is the device's own and is left alone, and the
                # device replaces the app itself once it is unplugged.
                filename = uuid.uuid4().hex[:8].upper()
                while existing is not None and filename == existing.filename:
                    filename = uuid.uuid4().hex[:8].upper()
                if existing is not None and existing.filename and not over_ptp:
                    filename = existing.filename
                path = posixpath.join(targets[0].path,
                                      f"{filename}.{targets[0].extension or 'PRG'}")
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
                    if existing is not None:
                        new_size += len(data)
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
            except (ApiError, AppListCopyError):
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
                storage.write(_config_path(device, True),
                              build_in_bin(state.listed + new_entries))
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
                entries = state.listed + new_entries + [h.entry for h in sent if h.entry]
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
        # The session broke: what was sent is not registered and cannot be removed any
        # more, the rest was not sent.
        for h in hidden:
            if id(h) not in settled:
                fail(h, _with_leftovers(e, [h.path] if h in sent else []))
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
        report.direct += [h.name for h in sent]
        report.updated += [h.name for h in sent if h.entry is None]
        report.updated_ids += [app_id for app_id in (
            message_app_id(h.message) for h in sent if h.entry is None) if app_id is not None]
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


def waiting_text(app: InstalledApp) -> str:
    """Why an app that waits for the device cannot be changed now."""
    if app.pending_removal:
        return _("{name} is already being removed; the device finishes it after it is "
                 "unplugged.").format(name=app.name)
    return _("{name} is not installed yet; the device installs it after it is unplugged. "
             "Unplug the device first.").format(name=app.name)


def removable_directly(device: GarminDevice, storage: Storage, app: InstalledApp) -> bool:
    """Whether the app's file is in a folder the device hides and a PTP session can reach."""
    dt = APPTYPE_TO_DATATYPE.get(app.app_type)
    return bool(dt and app.filename and not app.pending and not app.pending_removal
                and usb_dir_state(device, storage, dt) == DIRECT)


def _without(apps: list[InstalledApp], app: InstalledApp) -> list[InstalledApp]:
    return [a for a in apps if (a.app_id, a.app_type) != (app.app_id, app.app_type)]


def preview_removal(state: InstalledState, app: InstalledApp) -> InBinPreview:
    """The app list a removal over PTP would write. Nothing is written."""
    rest = _without(state.listed, app)
    written = parse_in_bin(build_in_bin(rest))
    pending = sum(1 for a in state.apps if a.pending)
    return InBinPreview(
        on_device=len(state.apps) - pending, pending=pending, entries=len(written),
        kept=(len(rest) == len(state.listed) - 1
              and written == [(a.app_id, a.app_type, a.disabled, a.favorite) for a in rest]),
        new=[])


def remove(device: GarminDevice, storage: Storage, app: InstalledApp) -> list[str]:
    """Remove an app from the device. Returns the deleted files."""
    if not app.removable:
        raise CiqError(_("{name} is part of the device software and cannot be removed")
                       .format(name=app.name))
    state = read_installed(device, storage)
    require_complete(state)
    current = state.find(app.app_id, app.app_type)
    if current is not None and (current.pending or current.pending_removal):
        current.name = app.name  # IN.BIN has no names; the caller may know it
        raise CiqError(waiting_text(current))
    paths_ = files_on_device(device, storage, app)
    if not paths_ and removable_directly(device, storage, app):
        return _remove_hidden(device, storage, state, app)
    removed = []
    if paths_:
        save_app_list_copy(device, storage)
    for p in paths_:
        storage.delete(p)
        removed.append(p)
    # Only the files are deleted here; the device updates its app list itself.
    log.info("apps remove: %s, %d files deleted through the mounted storage", app.name,
             len(removed))
    return removed


def _remove_hidden(device, storage, state: InstalledState, app: InstalledApp) -> list[str]:
    """Like Garmin Express: delete the app file the device hides, then write the app list
    without the app; every other entry keeps its place and its flags."""
    in_path = _config_path(device, True)
    if storage.exists(in_path):
        raise CiqError(_("the device has not applied earlier changes yet; unplug it, wait "
                         "until it is ready and try again"))
    listed = state.find(app.app_id, app.app_type)
    if listed is None:
        raise CiqError(_("App not found on the device"))
    path = app_file_path(device, listed)
    in_bin = build_in_bin(_without(state.listed, listed))
    removed: list[str] = []
    problem: str | None = None
    finished = False
    save_app_list_copy(device, storage)  # while the device is still mounted
    try:
        with storage.exclusive_ptp() as session:
            store = _ptp_store(session)
            # Everything is looked up before anything is deleted.
            folder = session.lookup(store, posixpath.dirname(path))
            parent = session.lookup(store, posixpath.dirname(in_path))
            if folder is None or not folder.is_dir or parent is None:
                raise CiqError(_("the app folder was not found on the device"))
            if session.lookup(store, in_path) is not None:
                raise CiqError(_("the device has not applied earlier changes yet; unplug it, "
                                 "wait until it is ready and try again"))
            file = session.lookup(store, path)
            if file is not None:
                if file.is_dir or (listed.size is not None and file.size != listed.size):
                    raise CiqError(_("{path} on the device is not the file the app list "
                                     "describes; nothing was deleted").format(path=path))
                try:
                    session.delete(file.handle)
                except ptp.PtpRefused as e:
                    raise CiqError(_("The device refused to delete {path} ({reason}); nothing "
                                     "was changed.").format(
                        path=path, reason=ptp.response_name(e.code))) from e
                if session.lookup(store, path) is not None:
                    raise CiqError(_("the device did not delete {path}").format(path=path))
                removed.append(path)
            # A file that is already gone (e.g. an earlier attempt stopped half-way) leaves
            # only the app list to put right.
            try:
                session.send_file(store, parent.handle, posixpath.basename(in_path), in_bin,
                                  verify_content=True)
                finished = True
            except DeviceError as e:
                left = (_ptp_delete(session, e.handle, in_path)
                        if isinstance(e, ptp.PtpWriteError) else [])
                problem = _with_leftovers(e, left)
    except RemountFailed as e:
        if finished:
            raise CiqError(_("{name} was removed, but the device could not be mounted again: "
                             "{error} Unplug the device and plug it in again.").format(
                name=app.name, error=e)) from e
        if problem is None:
            raise CiqError(_("{error} Unplug the device and plug it in again.")
                           .format(error=e)) from e
    if problem is not None:
        if removed:
            raise CiqError(_(
                "The file of {name} was deleted, but the app list on the device could not be "
                "updated: {error} The device may still list the app. Unplug it and check; if "
                "{name} is still listed, connect the device and remove the app again to "
                "update the list.").format(name=app.name, error=problem))
        raise CiqError(_("The app list on the device could not be updated: {error}")
                       .format(error=problem))
    log.info("apps remove over PTP: %s, %d files deleted, app list written", app.name,
             len(removed))
    return removed


# --- the order of the apps -----------------------------------------------------------------
#
# The device lists its apps in blocks by type and, within a block, in the order of the last
# IN.BIN it applied (seen when Garmin Express rewrote the list in its own order). So an
# order saved earlier (a copy of OUT.BIN) can be put back by writing the same entries with
# the same flags in that order.

APP_LIST_COPY_PREFIX = "GARMIN-APPS "


@dataclass
class AppListCopy:
    path: Path  # the copied OUT.BIN
    name: str  # the folder it is in
    state: InstalledState


@dataclass
class OrderPlan:
    entries: list[InstalledApp]  # the app list to write: what the device has, reordered
    moved: list[tuple[int, InstalledApp, InstalledApp]]  # (position, now there, will be there)
    only_in_copy: list[InstalledApp]
    only_on_device: list[InstalledApp]


def _find_out_bin(folder: Path) -> Path | None:
    return next((p for p in sorted(folder.rglob("*"))
                 if p.is_file() and p.name.upper() == "OUT.BIN"), None)


def app_list_copies(device: GarminDevice, backup_dir: Path | None = None) -> list[AppListCopy]:
    """The copies of the device's app list (OUT.BIN) in its backup folder, oldest first.
    Their folders are named after the time they were made, so the names sort by age."""
    unit_dir = Path(backup_dir or paths.default_backup_dir()) / str(device.unit_id)
    found = []
    if unit_dir.is_dir():
        for folder in sorted(unit_dir.iterdir()):
            path = _find_out_bin(folder) if folder.is_dir() else None
            if path is None:
                continue
            try:
                found.append(AppListCopy(path, folder.name, parse_out_bin(path.read_bytes())))
            except (OSError, ValueError, CiqError) as e:
                # Never the path: the folder above is named after the unit ID.
                log.warning("Cannot read the copy of the app list %s: %s", folder.name,
                            getattr(e, "strerror", None) or type(e).__name__)
    return found


def load_app_list_copy(device: GarminDevice, backup_dir: Path | None, source: str) -> AppListCopy:
    """A copy named by a path (file or folder) or by part of its folder name."""
    given = Path(source).expanduser()
    if given.exists():
        path = given if given.is_file() else _find_out_bin(given)
        name = given.parent.name if given.is_file() else given.name
        if path is None:
            raise CiqError(_("No OUT.BIN in {name}").format(name=name))
        try:
            return AppListCopy(path, name, parse_out_bin(path.read_bytes()))
        except (OSError, ValueError) as e:
            raise CiqError(_("Cannot read the copy of the app list {name}: {error}").format(
                name=name, error=getattr(e, "strerror", None) or type(e).__name__)) from e
    matches = [c for c in app_list_copies(device, backup_dir) if source in c.name]
    if len(matches) != 1:
        raise CiqError(
            _("No copy of the app list matches “{name}”.").format(name=source) if not matches
            else _("Several copies of the app list match “{name}”: {names}").format(
                name=source, names=", ".join(c.name for c in matches)))
    return matches[0]


class AppListCopyError(CiqError):
    """The copy of the app list could not be made: nothing may be written to the device."""


def _model_for_name(device: GarminDevice) -> str:
    """The model name as part of a folder name: no path separators or odd characters."""
    text = "".join(c if c.isalnum() or c in " -_,.()+" else " " for c in device.description or "")
    return " ".join(text.split()).strip(" .")[:60]


def has_app_list(device: GarminDevice, storage: Storage) -> bool:
    """Whether the device has an app list (OUT.BIN) at all; one without apps may not."""
    return storage.exists(_config_path(device, False))


def app_list_copy_note(device: GarminDevice, storage: Storage) -> str:
    """For dry runs of everything that changes the apps: what happens before the change."""
    if has_app_list(device, storage):
        return _("Before any change, a copy of the app list of the device is made.")
    return _("No copy will be made: the device has no app list.")


def save_app_list_copy(device: GarminDevice, storage: Storage, backup_dir: Path | None = None,
                       now: datetime | None = None) -> str | None:
    """Keep the device's current app list (OUT.BIN) before it is changed, so that the apps
    and their order can be looked up and put back. Returns the name of the copy; when the
    newest copy of this device is the same byte for byte, that one is named and no new
    copy is made. A device without an app list has nothing to copy: None, and the change
    may go on. Raises `AppListCopyError` when there is a list but the copy cannot be made.

    The copies live next to the backups, in a folder named after the unit ID. Messages
    and logs therefore name a copy by its own folder only, never by its path."""
    if not has_app_list(device, storage):
        log.info("The device has no app list: no copy made")
        return None
    try:
        data = storage.read(_config_path(device, False))
    except DeviceError as e:
        raise AppListCopyError(_("Cannot read the app list of the device to make a copy of "
                                 "it: {error}. Nothing was changed.").format(error=e)) from e
    unit_dir = Path(backup_dir or paths.default_backup_dir()) / str(device.unit_id)
    stamp = (now or datetime.now()).strftime("%Y-%m-%d (%H.%M.%S)")
    name = " ".join((APP_LIST_COPY_PREFIX + stamp, _model_for_name(device))).strip()
    try:
        newest = None
        if unit_dir.is_dir():
            for folder in sorted(unit_dir.iterdir(), reverse=True):
                newest = _find_out_bin(folder) if folder.is_dir() else None
                if newest is not None:
                    break
        if newest is not None and newest.read_bytes() == data:
            return newest.relative_to(unit_dir).parts[0]
        folder, n = unit_dir / name, 1
        while folder.exists():
            n += 1
            folder = unit_dir / f"{name} ({n})"
        folder.mkdir(parents=True)
        (folder / "OUT.BIN").write_bytes(data)
        if (folder / "OUT.BIN").read_bytes() != data:
            raise OSError(0, "the copy differs from the app list")
    except OSError as e:
        raise AppListCopyError(_("Cannot make a copy of the app list ({name}): {error}. "
                                 "Nothing was changed.").format(
            name=name, error=e.strerror or type(e).__name__)) from e
    log.info("Saved a copy of the app list: %s", folder.name)
    return folder.name


def changes_waiting(device: GarminDevice, storage: Storage) -> bool:
    """Whether an app list (IN.BIN) is on the device that it has not applied yet."""
    return storage.exists(_config_path(device, True))


def plan_order(state: InstalledState, copy: InstalledState) -> OrderPlan:
    """The device's apps in the order of `copy`. Apps the copy does not have keep their
    place, apps only the copy has are left out, and every flag stays as it is now. Each
    type is ordered on its own, as the device keeps the types in blocks."""
    def key(a):
        return a.app_id, a.app_type

    current = state.listed
    rank = {key(a): i for i, a in enumerate(copy.apps)}
    entries = list(current)
    for app_type in {a.app_type for a in current}:
        slots = [i for i, a in enumerate(current) if a.app_type == app_type and key(a) in rank]
        ordered = sorted((current[i] for i in slots), key=lambda a: rank[key(a)])
        for i, app in zip(slots, ordered):
            entries[i] = app
    on_device = {key(a) for a in current}
    return OrderPlan(
        entries=entries,
        moved=[(i + 1, now, then) for i, (now, then) in enumerate(zip(current, entries))
               if key(now) != key(then)],
        only_in_copy=[a for a in copy.apps if key(a) not in on_device],
        only_on_device=[a for a in current if key(a) not in rank])


def order_choices(device: GarminDevice, state: InstalledState,
                  backup_dir: Path | None = None) -> list[tuple[AppListCopy, OrderPlan]]:
    """The copies of the device's app list, newest first, each with what its order would
    change."""
    return [(c, plan_order(state, c.state))
            for c in reversed(app_list_copies(device, backup_dir))]


def copy_title(copy: AppListCopy) -> str:
    """A copy as shown to the user: when it was made and for which model."""
    name = copy.name
    return name.removeprefix(APP_LIST_COPY_PREFIX)


def describe_order(plan: OrderPlan, limit: int | None = 10) -> list[tuple[str, bool]]:
    """What a change of order does, as lines for a dry run or a confirmation; the flag
    marks the lines that list single positions."""
    kinds: dict[str, int] = {}
    for _position, _now, then in plan.moved:
        kinds[then.type_name] = kinds.get(then.type_name, 0) + 1
    lines = [(ngettext("{count} position would change ({kinds}):",
                       "{count} positions would change ({kinds}):", len(plan.moved)).format(
        count=len(plan.moved),
        kinds=", ".join(f"{kind}: {n}" for kind, n in sorted(kinds.items()))), False)]
    shown = plan.moved if limit is None else plan.moved[:limit]
    lines += [(_("{position}: {new} (now: {old})").format(
        position=position, new=then.name, old=now.name), True)
        for position, now, then in shown]
    more = len(plan.moved) - len(shown)
    if more:
        lines.append((ngettext("… and {count} more", "… and {count} more", more).format(
            count=more), True))
    for label, apps in ((_("Not in the copy, left where they are: {names}"), plan.only_on_device),
                        (_("Only in the copy, ignored: {names}"), plan.only_in_copy)):
        if apps:
            lines.append((label.format(names=", ".join(a.name for a in apps)), False))
    return lines


def restore_order(device: GarminDevice, storage: Storage, copy: InstalledState,
                  backup_dir: Path | None = None) -> tuple[OrderPlan, str | None]:
    """Write the app list in the order of `copy`. A copy of the current OUT.BIN is made
    in the backup folder first. Returns the plan and the name of that copy (None when
    nothing had to change)."""
    state = read_installed(device, storage)
    require_complete(state)
    in_path = _config_path(device, True)
    if changes_waiting(device, storage):
        raise CiqError(_("the device has not applied earlier changes yet; unplug it, wait "
                         "until it is ready and try again"))
    plan = plan_order(state, copy)
    if not plan.moved:
        return plan, None

    def flags(apps):
        return sorted((str(a.app_id), a.app_type, a.disabled, a.favorite) for a in apps)

    data = build_in_bin(plan.entries)
    written = [(str(e[0]), e[1], e[2], e[3]) for e in parse_in_bin(data)]
    if flags(plan.entries) != flags(state.listed) or sorted(written) != flags(state.listed):
        raise CiqError(_("The app list that would be written does not match the device. "
                         "Nothing was changed."))
    saved = save_app_list_copy(device, storage, backup_dir)
    real = storage.write(in_path, data)
    if storage.read(real) != data:
        _delete_quietly(storage, real)
        raise CiqError(_("Write verification of {path} failed").format(path=real))
    log.info("apps order: wrote %d entries, %d moved", len(plan.entries), len(plan.moved))
    return plan, saved


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
    log.info("apps %s: %s (type %s)", "enable" if enabled else "disable", app.name,
             app.app_type)
    save_app_list_copy(device, storage)
    storage.write(_config_path(device, True), build_in_bin(state.listed))
