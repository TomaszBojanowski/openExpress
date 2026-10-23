"""High-level operations shared by the GUI and the background service."""

from __future__ import annotations

import collections
import contextlib
import fcntl
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from openexpress import applog, known_devices, paths, sync
from openexpress.auth import store
from openexpress.connect import (
    ApiError,
    ClockSkew,
    GarminClient,
    NotAccountDevice,
    OwnershipError,
    OwnershipUnknown,
    Unauthorized,
)
from openexpress.device.storage import DeviceError, Storage
from openexpress.device.xmldevice import GarminDevice, parse
from openexpress.i18n import N_, _, ngettext
from openexpress.progress import Waiting
from openexpress.settings import Settings, SetupError, load_consumer

log = logging.getLogger(__name__)

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
    # (free space, capacity) in bytes as the window read them, in the thread that opened the
    # device and again in the background after it wrote to it; None: not read yet, and
    # either one None: not known (it could not be asked).
    space: tuple[int | None, int | None] | None = None

    @property
    def logged_in(self) -> bool:
        return self.client.auth is not None

    @property
    def state(self) -> sync.SyncState:
        return sync.SyncState(self.device.unit_id)

    @property
    def first_sync(self) -> bool:
        return not self.state.file.exists()

    @property
    def mass_storage(self) -> bool:
        """A Garmin device in mass storage mode: never synced, nothing asked of the account."""
        return self.storage.mass_storage


class MassStorageMode(Exception):
    """What is refused for a Garmin device in mass storage mode."""


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


def open_session(device_uri: str | None = None, storage: Storage | None = None,
                 allow_ums: bool = False, found=None) -> DeviceSession:
    """A session on the connected device: MTP alone, unless `allow_ums` (the window) also
    lets a Garmin device in mass storage mode be opened. `found`: the device chosen from
    find_all_devices, of whatever kind."""
    detected = storage is None  # a device found now, not a folder handed in (a copy, a test)
    if storage is None and found is not None:
        storage = _open_found(found)
    if storage is None:
        from openexpress.device.gio_storage import open_device_storage

        storage = (open_device_or_drive() if allow_ums and device_uri is None
                   else open_device_storage(device_uri))
    device = parse(storage.read("GARMIN/GarminDevice.xml"))
    applog.hide_device(device)
    if detected:
        known_devices.seen(device.unit_id, device.description, device.part_number,
                           device.software_version_str, "ums" if storage.mass_storage else "mtp")
    settings = Settings.load()
    return DeviceSession(storage, device, settings, make_client(settings))


def _open_found(found) -> Storage:
    """The storage of the device chosen from a list: a Garmin drive as it was found; an MTP
    device by its address, as with --device-uri, mounted first when it is not (the user
    chose it; a list mounts nothing)."""
    from openexpress.device import gio_storage

    if found.kind == "ums":
        return gio_storage.open_ums_storage(found)
    gio_storage.mount(found)
    storage = gio_storage.open_device_storage(found.uri)
    if found.usb is not None:
        # Read from the USB device when it was found; an address with names (e.g. an Edge)
        # does not tell the product.
        storage.usb, storage.usb_id = found.usb, found.usb.usb_id
    return storage


def open_device_or_drive(wait: bool = False, notify=None) -> Storage:
    """An MTP device comes first, also one that is still on its way to MTP; a Garmin device
    in mass storage mode is opened only when there is none (the window and the command
    line). `wait`, `notify`: as for open_device_storage."""
    from openexpress.device import gio_storage

    try:
        storage = gio_storage.open_device_storage(wait=wait, notify=notify)
    except gio_storage.SeveralDevices:
        raise
    except DeviceError:
        if gio_storage.usb_state() not in (gio_storage.ABSENT, gio_storage.MASS_STORAGE):
            raise
        drives = gio_storage.find_ums_devices()
        if not drives:
            raise
        if len(drives) > 1:
            # Their addresses name the user's folder, and --device-uri takes no drive.
            raise gio_storage.SeveralDevices(
                _("Several Garmin devices are connected in mass storage mode. Connect only "
                  "one of them."), drives) from None
        return gio_storage.open_ums_storage(drives[0])
    skipped = len(gio_storage.find_ums_devices())
    if skipped:
        log.info("device: an MTP device is open, %d Garmin drive in mass storage mode skipped",
                 skipped)
    return storage


def unit_id_of(value) -> int | None:
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
        if unit_id not in {unit_id_of(d.get("unitid")) for d in devices}:
            refused = NotAccountDevice(
                _("This device is not registered to your Garmin account. To use it with "
                  "Garmin Connect here, sign in to the account it belongs to or add the "
                  "device to your account in Garmin Connect."))
            refused.devices = devices  # the account's list all the same (the start page)
            raise refused
        verified = unit_id
        return devices
    finally:
        client.verified_unit_id = verified


# --- may data be uploaded? ---------------------------------------------------------------
#
# Like Garmin Express, nothing is uploaded unless the account has given the upload consent
# (DI_CONNECT_UPLOAD) and has device sync turned on (the preference account.deviceSync).
# Files from Garmin Connect are still written to the device. Whatever cannot be read or
# understood counts as "no".

UPLOAD_CONSENT = "DI_CONNECT_UPLOAD"
NO_CONSENT, SYNC_OFF, NOT_CHECKED = "no-consent", "sync-off", "not-checked"
# Why nothing was uploaded: in the details of a sync, after "Nothing was uploaded: "…
NOT_UPLOADED_WHY = {
    NO_CONSENT: N_("the Garmin account has no consent to upload data from devices"),
    SYNC_OFF: N_("device sync is turned off in the Garmin account"),
    NOT_CHECKED: N_("the upload consent could not be checked. Try again later"),
}
# … and in its row, short (under "Last sync", first in the numbers of the command line).
NOT_UPLOADED_ROW = {
    # TRANSLATORS: under "Last sync": nothing was uploaded, and why
    NO_CONSENT: N_("nothing uploaded: no consent in the account"),
    # TRANSLATORS: under "Last sync": nothing was uploaded, and why
    SYNC_OFF: N_("nothing uploaded: device sync is off in the account"),
    # TRANSLATORS: under "Last sync": nothing was uploaded, and why
    NOT_CHECKED: N_("nothing uploaded: the consent could not be checked"),
}


def consent_granted(body: dict | None, consent_type: str, country=lambda: None) -> bool:
    """Whether an answer of the consent service means "granted".

    Granted when the server does not enforce the consent at all, or when the user opted
    in and the consent has not expired. An opt-out is never granted. An upload consent
    that only comes from the account's defaults counts when the account's country is
    known and is not China, where a default is not enough; `country` returns that code.
    """
    if not isinstance(body, dict):
        return False
    option = str(body.get("useroption") or "").lower()
    source = str(body.get("sourcetype") or "").upper()
    enforcement = str(body.get("consentenforcementstate") or "").upper()
    if option == "opt-out":
        return False
    if enforcement == "NONE":
        return True
    if enforcement not in ("SOFT", "HARD") or option != "opt-in" or source == "EXPIRED":
        return False
    if consent_type == UPLOAD_CONSENT and source == "DEFAULT":
        code = country()
        return bool(code) and code != "CN"
    return source in ("CUSTOMER", "SYSTEM", "DEFAULT", "CONNECT_PRIVACY")


@dataclass
class UploadGate:
    """The answer to "may this sync upload data?", asked once per sync."""

    reason: str = ""  # "", NO_CONSENT, SYNC_OFF or NOT_CHECKED
    client: object = None
    _consents: dict = field(default_factory=dict)

    @property
    def allowed(self) -> bool:
        return not self.reason

    def why(self) -> str:
        why = NOT_UPLOADED_WHY.get(self.reason)
        return _(why) if why else ""

    def message(self, done: bool = True) -> str:
        """`done`: after a sync; otherwise for a plan (dry run)."""
        frame = (_("Nothing was uploaded: {reason}") if done
                 else _("Nothing would be uploaded: {reason}"))
        return frame.format(reason=self.why())

    def granted(self, consent_type: str) -> bool:
        """Whether a consent that one upload location requires is given (asked once)."""
        if not self.allowed or self.client is None:
            return False
        if consent_type not in self._consents:
            try:
                self._consents[consent_type] = consent_granted(
                    self.client.consent(consent_type), consent_type, self.client.profile_country)
            except (Unauthorized, ClockSkew):
                raise
            except Exception as e:  # noqa: BLE001 - anything unclear means "no"
                log.warning("Cannot check the consent %s: %s", consent_type, e)
                self._consents[consent_type] = False
        return self._consents[consent_type]


def check_upload_allowed(client) -> UploadGate:
    gate = _check_upload_allowed(client)
    log.info("uploads allowed: %s%s", gate.allowed, f" ({gate.reason})" if gate.reason else "")
    return gate


def _check_upload_allowed(client) -> UploadGate:
    """Ask Garmin Connect whether data may be uploaded. Never assumes "yes": a missing,
    empty or unreadable answer and a failed request all block uploads. An expired login
    or a wrong clock is raised as such."""
    try:
        if not consent_granted(client.consent(UPLOAD_CONSENT), UPLOAD_CONSENT,
                               client.profile_country):
            return UploadGate(NO_CONSENT)
        preference = client.device_sync_preference()
    except (Unauthorized, ClockSkew):
        raise
    except Exception as e:  # noqa: BLE001 - network, HTTP or an unexpected answer
        log.warning("Cannot check whether uploads are allowed: %s", e)
        return UploadGate(NOT_CHECKED)
    if not isinstance(preference, dict) or preference.get("value") is None:
        return UploadGate(NOT_CHECKED)
    if str(preference.get("value")).lower() != "true":
        return UploadGate(SYNC_OFF)
    gate = UploadGate("", client)
    gate._consents[UPLOAD_CONSENT] = True
    return gate


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
    unit_id = unit_id_of(raw)
    if unit_id is None and raw not in (None, 0, "0", ""):
        raise ApiError(_("{what}: unexpected response from the server").format(
            what=_("Primary training device")))
    by_id = {unit_id_of(d.get("unitid")): d for d in devices}
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
    A Garmin device in mass storage mode is not checked: nothing is asked of the account
    about it (`s.mass_storage` tells the window why there is no sync).
    """
    s.ownership_error = None
    s.account_devices = None
    if s.mass_storage:
        log.info("device: in mass storage mode, the account is not asked about it")
        return s
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
    rejected: int = 0
    errors: list[str] = field(default_factory=list)
    # The same errors as the log tells them: without the names of files, courses and
    # workouts (`errors` names them for the user). Not kept in last_sync.json.
    log_errors: list[str] = field(default_factory=list)
    # What happened, in order: a text, or the numbers of a step as a dict, which is put into
    # words only when it is shown (`describe_lines`): in the language of that moment.
    lines: list = field(default_factory=list)

    def record(self, unit_id: int | None = None) -> dict:
        """This sync as last_sync.json keeps it (save_last_sync), to be put into words by
        last_sync.describe, as the window reads it back."""
        return {"time": time.time(), "unit_id": unit_id, "uploaded": self.uploaded,
                "downloaded": self.downloaded, "skipped_known": self.skipped_known,
                "rejected": self.rejected, "errors": self.errors, "lines": self.lines}

    @property
    def counts(self) -> str:
        """The numbers of this sync, as under "Last sync" (last_sync.describe)."""
        from openexpress import last_sync

        return last_sync.describe(self.record()).counts

    @property
    def text(self) -> str:
        """The numbers and the steps, as last_sync.describe tells them."""
        from openexpress import last_sync

        return last_sync.describe(self.record()).text


# Readable names of the data types of new files. A name is given only where it is plain from
# the FIT SDK (the file type by its number), the upload address the server gives for the type
# (e.g. ".../upload/hrvstatus"), or the folder the device declares for it in GarminDevice.xml
# (e.g. "Garmin/Sleep"); where the name in the FIT profile is technical, the name says what the
# data is, after that name and the folder the device declares. Any other type is shown as
# "other data (NAME)". Types with the same name are counted together.
TYPE_NAMES = {
    "FIT_TYPE_4": N_("activities"),             # FIT SDK: activity; upload: activity
    "FIT_TYPE_9": N_("weight"),                 # FIT SDK: weight; upload: weight
    "FIT_TYPE_28": N_("daily monitoring"),      # FIT SDK: monitoring_daily
    "FIT_TYPE_32": N_("monitoring"),            # FIT SDK: monitoring_b
    "FIT_TYPE_35": N_("segment list"),          # FIT SDK: segment_list; upload: segment-list
    "FIT_TYPE_38": N_("golf scorecards"),       # folder: SCORCRDS
    "FIT_TYPE_41": N_("settings change log"),   # FIT profile: settings_change_log
    "FIT_TYPE_44": N_("metrics"),               # upload: metrics
    "FIT_TYPE_49": N_("sleep"),                 # folder: Sleep
    # FIT profile: device_monitoring ("data logged on a device level", not the wearer's
    # monitoring); upload: device-monitoring; folder: Garmin/Device
    "FIT_TYPE_58": N_("device log"),
    "FIT_TYPE_61": N_("ECG"),                   # folder: ECG
    "FIT_TYPE_68": N_("HRV status"),            # upload: hrvstatus
    # FIT profile: fbt_lha_backup; folder: Garmin/FbtBackup
    "FIT_TYPE_72": N_("training status data"),
    "FIT_TYPE_73": N_("skin temperature"),      # upload: skintemp
    # FIT profile: primary_training_backup; folder: Garmin/FbtPtdBackup
    "FIT_TYPE_74": N_("primary training device data"),
    # FIT profile: adaptive_coaching_schedule; folder: Garmin/Coach
    "FIT_TYPE_77": N_("Garmin Coach plan"),
    "FIT_TYPE_79": N_("sleep disruptions"),     # upload: sleep-disruptions
    "FIT_TYPE_88": N_("multi-day activities"),  # upload: multi-day-activity
    "BACKUP_PRIMARY": N_("device backup"),      # upload: backup
    "BACKUP_SUPPLEMENTARY": N_("device backup"),
    "ACTIVITY_GCPD": N_("raw GPS data of activities"),  # upload: rawgnssactivity
}


def type_name(datatype: str) -> str:
    """The readable name of a data type of new files, in the language of the moment."""
    known = TYPE_NAMES.get(datatype)
    return _(known) if known else _("other data ({type})").format(type=datatype)


def _describe_types(types: dict) -> str:
    """ "activities ×2, monitoring ×3, other data (FIT_TYPE_70)": the named types in the order
    of TYPE_NAMES, then the others by name; how many only when more than one."""
    order = {name: i for i, name in enumerate(TYPE_NAMES)}
    counted: dict[str, int] = {}
    for datatype in sorted(types, key=lambda t: (order.get(t, len(order)), t)):
        name = type_name(datatype)
        counted[name] = counted.get(name, 0) + types[datatype]
    return ", ".join(name if count == 1 else f"{name} ×{count}"
                     for name, count in counted.items())


def _describe_step(line) -> list[tuple[str, bool]]:
    """A line of a sync summary in words: (text, whether it is a warning) for each sentence
    it gives; none for a step this version does not know. How the files already in Connect
    were recognised (before sending, or by Garmin) is only in the log of the sync."""
    if isinstance(line, str):
        return [(line, False)]  # written in words already (also every line of an older version)
    kind = line.get("kind") if isinstance(line, dict) else None
    if kind == "new_files":
        types = line.get("types") or {}
        if not types:
            return []
        return [(_("Files from the device: {types}").format(types=_describe_types(types)),
                 False)]
    if kind == "not_uploaded":
        gate = UploadGate(str(line.get("reason") or ""))
        return [(gate.message(), False)] if gate.why() else []  # one of a newer version: none
    if kind == "upload_skipped":
        return []  # the row says it ("upload skipped")
    if kind == "only_types":
        return []  # the row says it ("activities only: …")
    if kind == "uploads":
        known, rejected = line.get("known", 0), line.get("rejected", 0)
        errors = line.get("errors", 0)
        said = []
        if known:
            said.append((ngettext("{count} file was already in Garmin Connect",
                                  "{count} files were already in Garmin Connect",
                                  known).format(count=known), False))
        if rejected:
            said.append((ngettext("{count} file was rejected by Garmin",
                                  "{count} files were rejected by Garmin",
                                  rejected).format(count=rejected), True))
        if errors:
            said.append((ngettext("{count} file could not be uploaded",
                                  "{count} files could not be uploaded",
                                  errors).format(count=errors), True))
        return said
    return []


def new_files_step(candidates) -> dict:
    """The new files of a sync, counted by data type: a step of the summary."""
    return {"kind": "new_files",
            "types": dict(collections.Counter(c.datatype for c in candidates))}


def not_uploaded_step(gate: UploadGate) -> dict:
    """Why a sync uploaded nothing (gate.reason: no consent, device sync off, not checked):
    a step of the summary, put into words when it is shown."""
    return {"kind": "not_uploaded", "reason": gate.reason}


def only_types_step(types) -> dict:
    """A sync of only these data types (`sync --type`): a step of the summary, so that the
    row says that its numbers are of them only."""
    return {"kind": "only_types", "types": sorted(set(types))}


def upload_skipped_step() -> dict:
    """A sync that did not look at the uploads at all (`sync --download-only`): a step of the
    summary, so that the row says "upload skipped", not "nothing to upload"."""
    return {"kind": "upload_skipped"}


def uploads_step(rep) -> dict:
    """What became of the uploads of a sync: a step of the summary. "known": already in
    Connect, recognised before sending or, after it was sent, by Garmin."""
    return {"kind": "uploads", "uploaded": len(rep.uploaded),
            "known": len(rep.already_in_connect) + len(rep.duplicates),
            "known_checked": len(rep.already_in_connect), "known_server": len(rep.duplicates),
            "rejected": len(rep.rejected), "errors": len(rep.failed)}


def _new_files(data: dict) -> int:
    """How many new files the device had: from the step of its new files, and never fewer
    than those uploaded, already in Connect and rejected (all a sync of an older version
    tells)."""
    listed = sum(n for line in data.get("lines") or []
                 if isinstance(line, dict) and line.get("kind") == "new_files"
                 for n in (line.get("types") or {}).values() if isinstance(n, int))
    counted = data.get("uploaded", 0) + data.get("skipped_known", 0) + data.get("rejected", 0)
    return max(listed, counted)


def describe_counts(data: dict) -> str:
    """The numbers of a sync in one line: "8 new files, 6 uploaded · 2 received" - the new
    files on the device and how many of them were uploaded ("none uploaded"; "nothing to
    upload" when it had none; "nothing uploaded: <why>" when the upload did not take
    place; "upload skipped" when it was not looked at; "activities only: …" when only some
    data types were), and the files from Garmin Connect written to the device when there are
    any.
    The window shows it under "Last sync" after when it was; the command line and the
    notification put it before the details."""
    files, uploaded = _new_files(data), data.get("uploaded", 0)
    received = data.get("downloaded", 0)
    blocked = next((line for line in data.get("lines") or [] if isinstance(line, dict)
                    and line.get("kind") == "not_uploaded"), None)
    skipped = any(isinstance(line, dict) and line.get("kind") == "upload_skipped"
                  for line in data.get("lines") or [])
    if skipped:
        # TRANSLATORS: under "Last sync": the uploads were not looked at (sync --download-only)
        parts = [_("upload skipped")]
    elif blocked is not None:
        # Nothing was uploaded, and why; a reason of a newer version: only that.
        row = NOT_UPLOADED_ROW.get(blocked.get("reason"))
        # TRANSLATORS: under "Last sync": nothing was uploaded
        parts = [_(row) if row else _("nothing uploaded")]
    elif files:
        # TRANSLATORS: files found on the device, e.g. "8 new files, 6 uploaded"
        found = ngettext("{count} new file", "{count} new files", files).format(count=files)
        sent = (ngettext("{count} uploaded", "{count} uploaded", uploaded).format(
            count=uploaded) if uploaded
            # TRANSLATORS: after "3 new files, ": not one of them was uploaded
            else _("none uploaded"))
        parts = [f"{found}, {sent}"]
    else:
        # TRANSLATORS: the device had no new file to upload to Garmin Connect
        parts = [_("nothing to upload")]
    only = next((line.get("types") for line in data.get("lines") or [] if isinstance(line, dict)
                 and line.get("kind") == "only_types"), None)
    if only and not skipped and blocked is None:
        # TRANSLATORS: under "Last sync": a sync of only these data types (sync --type), e.g.
        # "activities only: 3 new files, 3 uploaded"
        parts[0] = _("{types} only: {numbers}").format(
            types=_describe_types({datatype: 1 for datatype in only}), numbers=parts[0])
    if received:
        parts.append(ngettext("{count} received", "{count} received", received).format(
            count=received))
    return " · ".join(parts)


def describe_steps(lines) -> list[tuple[str, bool]]:
    """The lines of a sync summary in words, each with whether it is a warning (files
    rejected, or that could not be uploaded): for the window, which marks the warnings."""
    return [said for line in lines or [] for said in _describe_step(line)]


def describe_lines(lines) -> list[str]:
    """The lines of a sync summary in words, for the window, the notification and the
    command line alike."""
    return [text for text, _warning in describe_steps(lines)]


class SyncBusy(Exception):
    pass


@contextlib.contextmanager
def sync_lock():
    """Inter-process lock (the GUI and the background service never sync at the same time)."""
    path = paths.data_dir() / "sync.lock"
    paths.make_dir(path.parent)
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
    if s.mass_storage:
        log.info("sync: refused, the device is in mass storage mode")
        raise MassStorageMode(_("A device in mass storage mode is not synced."))
    started = time.monotonic()
    log.info("sync: start (first run: %s, diagnostics: %s)", first_run or "-",
             include_diagnostics)
    try:
        with sync_lock():
            summary = _run_sync(s, first_run, progress, include_diagnostics)
    except Exception as e:
        log.warning("sync: stopped after %.1f s: %s: %s", time.monotonic() - started,
                    type(e).__name__, e)
        raise
    save_last_sync(s.device.unit_id, summary)
    log.info("sync: end after %.1f s: %d uploaded, %d already known, %d downloaded, %d errors",
             time.monotonic() - started, summary.uploaded, summary.skipped_known,
             summary.downloaded, len(summary.errors))
    for error in summary.log_errors:
        log.warning("sync error: %s", error)
    return summary


def last_sync_path():
    """The newest sync of any device. The window watches this file to show a sync that the
    background service has just finished."""
    return paths.data_dir() / "last_sync.json"


def device_last_sync_path(unit_id: int):
    """The last sync of this device."""
    return paths.device_dir(unit_id) / "last_sync.json"


def _write_json(path, data: dict) -> None:
    """Written whole or not at all, readable by its owner only (it names the unit ID)."""
    paths.write_private(path, json.dumps(data, indent=1, ensure_ascii=False))


def save_last_sync(unit_id: int, summary: SyncSummary) -> None:
    data = summary.record(unit_id)
    # The device's own file first: the shared one only tells which sync was the newest,
    # and a sync of another device replaces it.
    _write_json(device_last_sync_path(unit_id), data)
    _write_json(last_sync_path(), data)


def record_sync(unit_id: int, uploaded: int, downloaded: int, skipped_known: int,
                errors: list[str], lines: list) -> None:
    """Store a sync result made outside run_sync as the last sync. `lines`: the steps as
    SyncSummary.lines has them (a text, or the numbers of a step as a dict)."""
    save_last_sync(unit_id, SyncSummary(uploaded=uploaded, skipped_known=skipped_known,
                                        downloaded=downloaded, errors=list(errors),
                                        lines=list(lines)))


def _read_json(path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))  # as written, whatever the locale
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


_LAST_SYNC_COUNTS = ("uploaded", "downloaded", "skipped_known", "rejected")


def _usable_last_sync(data: dict | None) -> dict | None:
    """The record as it is, or None when a field has a value of another type than the
    program writes (only a file edited by hand has one): shown as no sync, not as a row
    that cannot be drawn. A field left out reads as before."""
    if data is None:
        return None
    when = data.get("time", 0.0)
    if isinstance(when, bool) or not isinstance(when, int | float):
        return None
    try:
        datetime.fromtimestamp(when)
    except (ValueError, OverflowError, OSError):
        return None
    for key in _LAST_SYNC_COUNTS:
        value = data.get(key, 0)
        if isinstance(value, bool) or not isinstance(value, int):
            return None
    if not all(isinstance(data.get(key, []), list) for key in ("errors", "lines")):
        return None
    return data


def load_last_sync(unit_id: int | None = None) -> dict | None:
    """The last sync of this device (of any device without `unit_id`). Its own file comes
    first; the shared file, which earlier versions wrote alone, counts only when it is of
    this device."""
    if unit_id is not None:
        data = _usable_last_sync(_read_json(device_last_sync_path(unit_id)))
        if data is not None and data.get("unit_id") == unit_id:
            return data
    data = _usable_last_sync(_read_json(last_sync_path()))
    if data is None or (unit_id is not None and data.get("unit_id") != unit_id):
        return None
    return data


def _run_sync(s, first_run, progress, include_diagnostics) -> SyncSummary:
    if not s.logged_in:
        raise store.NotLoggedIn(_("Not logged in to Garmin Connect."))
    require_account_device(s.client, s.device.unit_id)
    out = SyncSummary()
    state = s.state
    progress(Waiting(_("Fetching configuration from the Garmin server…")))
    cfg = s.client.upload_settings(s.device.unit_id)
    if s.first_sync:
        if first_run == "baseline":
            n = sync.mark_baseline(s.device, s.storage, cfg, state, s.client)
            out.lines.append(ngettext("Marked {count} existing file as already uploaded",
                                      "Marked {count} existing files as already uploaded",
                                      n).format(count=n))
        elif first_run != "all":
            raise FirstSyncChoiceNeeded()
        state = s.state
    progress(_("Looking for new files on the device…"))
    gate = check_upload_allowed(s.client)
    plan = sync.plan_uploads(s.device, s.storage, cfg, state,
                             include_diagnostics=include_diagnostics,
                             consent_granted=gate.granted)
    if not gate.allowed:
        # Files from Connect are still written to the device below.
        out.lines.append(not_uploaded_step(gate))
    elif plan.candidates:
        out.lines.append(new_files_step(plan.candidates))
        rep = sync.run_uploads(plan, s.device, s.storage, s.client, state, progress)
        out.uploaded = len(rep.uploaded)
        out.skipped_known = len(rep.duplicates) + len(rep.already_in_connect)
        out.rejected = len(rep.rejected)
        out.errors += [f"{label}: {err}" for label, err in rep.failed]
        out.log_errors += [f"{sync.log_label(label)}: {sync.log_error(label, err)}"
                           for label, err in rep.failed]
        out.lines.append(uploads_step(rep))
        if rep.tracker_conflict:
            n = len(rep.tracker_conflict)
            out.lines.append(ngettext(
                "Skipped {count} file: another device is your active tracker in Garmin Connect",
                "Skipped {count} files: another device is your active tracker in Garmin Connect",
                n).format(count=n))
    # No new file on the device: the row says it ("nothing to upload"), no line of its own.
    progress(Waiting(_("Checking for files from Garmin Connect…")))
    messages = s.client.device_messages(s.device.unit_id)
    items = sync.plan_downloads(s.device, messages)
    todo = [i for i in items if not i.unsupported and not i.handled_by]
    if todo:
        drep = sync.run_downloads(todo, s.storage, s.client, progress, state=state)
        out.downloaded = len(drep.written)
        out.errors += [f"{label}: {err}" for label, err in drep.failed]
        out.errors += [_("{name}: written, but Garmin Connect was not notified (will retry)")
                       .format(name=label) for label in drep.ack_failed]
        out.log_errors += [f"{quiet}: {err}" for quiet, err in drep.failed_in_log]
        out.log_errors += [f"{quiet}: written, but Garmin Connect was not notified (will retry)"
                           for quiet in drep.ack_failed_in_log]
    told = len(out.errors)  # the errors of the apps and TrueUp name nothing of the user's
    if any(i.handled_by == "Connect IQ" for i in items):
        out.lines += _install_queued_apps(s, messages, progress, out.errors)
    if any(i.handled_by == "TrueUp" for i in items):
        out.lines += _trueup(s, messages, progress, out.errors)
    out.log_errors += out.errors[told:]
    skipped = [i for i in items if i.unsupported]
    if skipped:
        out.lines.append(ngettext(
            "Skipped {count} item from Garmin Connect (not accepted by the device)",
            "Skipped {count} items from Garmin Connect (not accepted by the device)",
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
        lines.append(_("Installed Connect IQ apps: {names}").format(
            names=", ".join(rep.installed)))
    if rep.for_phone:
        n = len(rep.for_phone)
        lines.append(ngettext(
            "The Flatpak has no access to USB devices; {count} Connect IQ app waits in the "
            "queue",
            "The Flatpak has no access to USB devices; {count} Connect IQ apps wait in the "
            "queue", n).format(count=n)
            if all(name in rep.sandboxed for name in rep.for_phone) else ngettext(
            "{count} Connect IQ app waits in the queue: openExpress cannot install apps on "
            "this device over USB yet",
            "{count} Connect IQ apps wait in the queue: openExpress cannot install apps on "
            "this device over USB yet", n).format(count=n))
    return lines


def _trueup(s, messages, progress, errors) -> list[str]:
    from openexpress import trueup

    try:
        res = trueup.run(s.device, s.storage, s.client, messages, progress)
    except Exception as e:  # noqa: BLE001
        log.warning("TrueUp failed: %s: %s", type(e).__name__, e)
        errors.append(f"TrueUp: {e}")
        return []
    if res.written:
        return [_("TrueUp: daily totals aligned with your other Garmin devices")]
    return []  # nothing to write, or not supported: in the log only (trueup.run)


class FirstSyncChoiceNeeded(Exception):
    """First sync: either 'baseline' or 'all' must be chosen."""
