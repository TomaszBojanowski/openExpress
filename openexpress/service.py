"""High-level operations shared by the GUI and the background service."""

from __future__ import annotations

import collections
import contextlib
import fcntl
import json
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime

import requests

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
from openexpress.errors import OrdinaryStop, UserFacingError
from openexpress.i18n import N_, _, ngettext
from openexpress.progress import Waiting
from openexpress.settings import Settings, SetupError, language_tag, load_consumer

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
    # Its activities not uploaded yet (sync.ActivitiesToSync) as the window counted them in
    # the background, once its tile was drawn; None: not counted (yet) or not known. Whether
    # a count was started.
    to_sync: sync.ActivitiesToSync | None = None
    to_sync_asked: bool = False
    # What Garmin's update server said of the device (unit-info), asked once for the
    # session (unit_info()); it carries the unit ID and the serial number: never shown.
    unit_info_answer: dict | None = field(default=None, repr=False)
    # What the window learnt of the device's error logs when it opened it, and its copy
    # of the diagnostic files (diagnostics.at_open); None: not read. They are read in the
    # background once its page is shown: set when that is done (a task that writes to the
    # device waits for it); None: not started.
    diagnostics: object | None = None
    diagnostics_reading: threading.Event | None = field(default=None, repr=False)
    # Set to stop the wait of TrueUp (the window's Stop Waiting, the background service
    # quitting); cleared before each wait.
    stop_waiting: threading.Event = field(default_factory=threading.Event, repr=False)
    # The country of the account's profile, for maps restricted to one (account_country):
    # asked once per session, and only when such a map is there.
    account_country_code: str | None = field(default=None, repr=False)
    account_country_asked: bool = False

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


# unit-info is asked by threads of the window at the same time (the "Maps" row, the picture):
# one asks, the others wait for its answer.
_unit_info_lock = threading.Lock()


def unit_info(session) -> dict:
    """What Garmin's update server says of the open device (unit-info), asked once for the
    session, that is for each opening of the device: the "Maps" row, the Maps page and the
    picture use the one answer. A failed request is not kept, so the next one asks again.
    The answer stays in memory with the session only."""
    with _unit_info_lock:
        answer = getattr(session, "unit_info_answer", None)
        if answer is None:
            device = session.device
            answer = (session.client.unit_info(device.unit_id, device.part_number)
                      if device.unit_id else {})
            session.unit_info_answer = answer
            note_model_name(device, answer)
        return answer


def note_model_name(device, answer) -> None:
    """The name unit-info gives the model of a device whose GarminDevice.xml names it in
    capitals only ("EPIX"), kept for the window (known_devices.shown_name)."""
    model = answer.get("devicemodel") if isinstance(answer, dict) else None
    description = getattr(device, "description", "") or ""
    if device.unit_id and description.isupper() and model:
        known_devices.set_model_name(device.unit_id, model)


def forget_unit_info(session) -> None:
    """The maps checked again by hand: the next unit_info() asks the server anew."""
    with _unit_info_lock:
        session.unit_info_answer = None


class MassStorageMode(UserFacingError):
    """What is refused for a Garmin device in mass storage mode."""


def account_country(s) -> str | None:
    """The country code of the Garmin account's profile, for a map Garmin does not offer in a
    country: asked once per session (left in `s.account_country_code`). None without a login,
    for a device not checked as the account's, or when it cannot be read (said in the log),
    so that the map is only marked."""
    if (not s.logged_in or s.ownership_error is not None
            or getattr(s.client, "verified_unit_id", None) is None):
        return None
    if not s.account_country_asked:
        s.account_country_asked = True
        s.account_country_code = read_account_country(s.client.profile_country)
    return s.account_country_code


def read_account_country(ask: Callable[[], str | None]) -> str | None:
    """What `ask()` gives: the country code of the account's profile, for a map Garmin does
    not offer in a country. None when it cannot be read, and why said in the log (without
    the country), so that the map is only marked. The command line and the window check the
    device's owner first, each its own way."""
    try:
        return ask()
    except (UserFacingError, requests.RequestException, ValueError) as e:
        log.info("maps: the account's country not read (%s)", type(e).__name__)
        return None


def make_client(settings: Settings, need_auth: bool | None = None) -> GarminClient:
    """The client of Garmin Connect, with the application key and the saved login.

    `need_auth` None (the window, the watcher): what is missing is left out, without an
    error: no key, no login, a login of another environment taken for none. True or False
    (a command): the key is needed (SetupError without it, or damaged), asked for before
    the login; True: the login too (NotLoggedIn without it, or damaged; SetupError for a
    login of another environment); False: no login, none read."""
    try:
        consumer = load_consumer()
    except SetupError:
        if need_auth is not None:
            raise
        consumer = None
    auth = None
    if need_auth is not False:
        try:
            auth = store.load()
        except store.NotLoggedIn:
            if need_auth:
                raise
        if auth is not None and auth.environment != settings.environment:
            if need_auth:
                raise SetupError(
                    _("The token is for environment {token_environment}, but the configured "
                      "one is {environment}. Please log in again.").format(
                        token_environment=auth.environment, environment=settings.environment))
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
    device = read_device(storage, found_now=detected)
    settings = Settings.load()
    return DeviceSession(storage, device, settings, make_client(settings))


def read_device(storage: Storage, found_now: bool, refuse=None) -> GarminDevice:
    """The device in `storage` (its GarminDevice.xml), opened as the command line and the
    window open it: what is private about it hidden in the log at once (applog.hide_device);
    then `refuse(storage, device)`, which raises for what a command does not do with it;
    then, for a device found now (not a folder given by hand, which may be a copy), which
    keys a drive gives said in the log without them, and the device remembered with them
    (known_devices.seen)."""
    device = parse(storage.read("GARMIN/GarminDevice.xml"))
    usb = getattr(storage, "usb", None)
    applog.hide_device(device, usb)
    if refuse is not None:
        refuse(storage, device)
    if found_now:
        drive = usb if storage.mass_storage else None
        if drive is not None:
            log.info("device: %s", drive_keys_text(drive, device.unit_id))
        known_devices.seen(device.unit_id, device.description, device.part_number,
                           device.software_version_str, "ums" if storage.mass_storage else "mtp",
                           **drive_keys(drive))
    return device


def drive_keys(usb) -> dict:
    """What a drive's entry keeps of its USB device (known_devices.seen): its USB product,
    serial and the UUID of its volume, which tell it while it is not mounted;
    nothing for a device that is not a drive (None)."""
    if usb is None:
        return {}
    return {"usb_product": getattr(usb, "product", None),
            "usb_serial": getattr(usb, "serial", None),
            "volume_uuid": getattr(usb, "volume_uuid", None)}


def drive_keys_text(usb, unit_id) -> str:
    """For the log, without any of them: which of those keys a drive gives, and whether its
    USB serial is its unit ID in hexadecimal (a fēnix 8's is)."""
    serial = getattr(usb, "serial", None)
    try:
        is_unit = serial is not None and unit_id is not None and int(serial, 16) == unit_id
    except ValueError:
        is_unit = False
    said = ("this drive has no USB serial" if serial is None
            else "this drive's USB serial is its unit ID in hexadecimal" if is_unit
            else "this drive's USB serial is not its unit ID")
    has = "has a UUID" if getattr(usb, "volume_uuid", None) else "has no UUID"
    return f"{said}; its volume {has}"


def _open_found(found) -> Storage:
    """The storage of the device chosen from a list: a Garmin drive, mounted first when the
    system has not mounted it (KDE); an MTP device by its address, as with --device-uri,
    mounted first when it is not (the user chose it; a list mounts nothing)."""
    from openexpress.device import gio_storage

    if found.kind == "ums":
        if not found.mounted:  # not mounted by the system (KDE): mounted now, chosen
            found = gio_storage.mount_drive(found)
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
        if gio_storage.usb_state() not in (gio_storage.ABSENT, gio_storage.MASS_STORAGE,
                                           gio_storage.DRIVE_OFF):
            raise
        # A drive the system has not mounted is not mounted here: it is chosen (a tile).
        drives = [d for d in gio_storage.find_ums_devices() if d.mounted]
        if not drives:
            raise
        if len(drives) > 1:
            # Their addresses name the user's folder, and --device-uri takes no drive.
            raise gio_storage.SeveralDevices(
                _("Several Garmin devices are connected as USB drives. Connect only one of "
                  "them."), drives) from None
        return gio_storage.open_ums_storage(drives[0])
    skipped = sum(1 for d in gio_storage.find_ums_devices() if d.mounted)
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
    """Allow account operations on this watch only if the account lists it: returns the
    account's device list fetched for the check (asked every time, no cache, no override).
    Raises NotAccountDevice when the watch is not on it, OwnershipUnknown when it cannot be
    fetched, Unauthorized and ClockSkew as they are. Leaves `client.verified_unit_id` set on
    success, None otherwise."""
    if client.verified_unit_id != unit_id:
        client.verified_unit_id = None
    # While the same watch is checked again, tasks sharing the client (pictures, icons)
    # keep working; the result replaces the old one only when the answer is in.
    verified = None
    try:
        try:
            devices = client.account_devices()
        except (Unauthorized, ClockSkew):
            raise  # an expired login or a wrong clock as such: the user gets the usual advice
        except ApiError as e:
            # The watch is never assumed to be the user's.
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
    """Whether an answer of the consent service means "granted": when the server does not
    enforce the consent, or when the user opted in and it has not expired; an opt-out never.
    An upload consent only from the account's defaults counts when the account's country,
    returned by `country`, is known and not China, where a default is not enough."""
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


class PrimaryTrainingError(UserFacingError):
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


@dataclass
class WearableStatus:
    """The account's primary wearable, seen from the connected device (from the account's
    device list: primaryActivityTrackerIndicator, primaryActivityTrackerSettingCapable)."""

    set: bool  # the account has one
    name: str  # its model name from the list; "" when it gives none
    is_this_device: bool
    capable: bool | None  # whether the connected device can be one; None when unknown


@dataclass
class WearableChange:
    before: WearableStatus
    devices: list[dict]  # the account's list as it is now (read back after a change)


class PrimaryWearableError(UserFacingError):
    """The primary wearable cannot be changed to the connected device."""


def primary_wearable_status(client: GarminClient, device: GarminDevice,
                            devices: list[dict] | None = None) -> WearableStatus:
    """Which device is the primary wearable, from the account's device list (the ownership
    check is run here without `devices`). Changes nothing on the account."""
    if devices is None:
        devices = require_account_device(client, device.unit_id)
    entries = [d for d in devices if isinstance(d, dict)]
    this = next((d for d in entries if unit_id_of(d.get("unitid")) == device.unit_id), None)
    primary = next((d for d in entries if d.get("primaryactivitytrackerindicator") is True),
                   None)
    name = (primary.get("productdisplayname") or primary.get("displayname") or "") if primary \
        else ""
    capable = this.get("primaryactivitytrackersettingcapable") if this is not None else None
    return WearableStatus(primary is not None, name if isinstance(name, str) else "",
                          primary is not None and primary is this,
                          capable if isinstance(capable, bool) else None)


def set_primary_wearable(client: GarminClient, device: GarminDevice,
                         apply: bool = False) -> WearableChange:
    """Make the connected device the account's primary wearable. Without `apply` nothing is
    changed, but the same refusals are raised. The ownership check is always made anew, and
    after the change the account's list is read back: an accepted request that changed
    nothing is reported as an error."""
    devices = require_account_device(client, device.unit_id)
    before = primary_wearable_status(client, device, devices)
    if before.is_this_device:
        return WearableChange(before, devices)
    if before.capable is False:
        raise PrimaryWearableError(
            _("{device} cannot be a primary wearable.").format(device=device.description))
    if before.capable is None:
        # Like the ownership check: never assume in favour of a change.
        raise PrimaryWearableError(
            _("Could not tell whether {device} can be a primary wearable. Nothing was "
              "changed.").format(device=device.description))
    if apply:
        client.set_primary_wearable(device.unit_id)
        devices = require_account_device(client, device.unit_id)
        if not primary_wearable_status(client, device, devices).is_this_device:
            raise PrimaryWearableError(
                _("Garmin Connect accepted the request, but the primary wearable did not "
                  "change."))
    return WearableChange(before, devices)


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
    # words only when it is shown (`describe_steps`): in the language of that moment.
    lines: list = field(default_factory=list)
    # The uploads Garmin Connect took without confirming that it processed them (the command
    # line warns of them). Not kept in last_sync.json.
    not_confirmed: list[str] = field(default_factory=list)

    def record(self, unit_id: int | None = None) -> dict:
        """This sync as last_sync.json keeps it (save_last_sync), to be put into words by
        last_sync.describe, as the window reads it back."""
        return {"time": time.time(), "unit_id": unit_id, "uploaded": self.uploaded,
                "downloaded": self.downloaded, "skipped_known": self.skipped_known,
                "rejected": self.rejected, "errors": self.errors, "lines": self.lines}


# Readable names of the data types of new files. A name is given only where it is plain from
# the FIT SDK (the file type by its number), the upload address the server gives for the type
# (e.g. ".../upload/hrvstatus"), or the folder the device declares for it in GarminDevice.xml
# (e.g. "Garmin/Sleep"); where the name in the FIT profile is technical, the name says what the
# data is, after that name and the folder the device declares. Any other type is shown as
# "other data (NAME)". Types with the same name are counted together. "queue": a type seen
# coming from Garmin Connect's queue to a device (the logs, the download of the queue); the
# names also say what came from there.
TYPE_NAMES = {
    "FIT_TYPE_4": N_("activities"),             # FIT SDK: activity; upload: activity
    "FIT_TYPE_5": N_("workouts"),               # FIT SDK: workout; queue
    "FIT_TYPE_6": N_("courses"),                # FIT SDK: course; queue
    "FIT_TYPE_7": N_("schedules"),              # FIT SDK: schedules
    "FIT_TYPE_9": N_("weight"),                 # FIT SDK: weight; upload: weight
    "FIT_TYPE_11": N_("goals"),                 # FIT SDK: goals; queue
    "FIT_TYPE_20": N_("activity summary"),      # FIT SDK: activity_summary; queue
    "FIT_TYPE_28": N_("daily monitoring"),      # FIT SDK: monitoring_daily
    "FIT_TYPE_29": N_("personal records"),      # FIT profile: personal_records; queue
    "FIT_TYPE_32": N_("monitoring"),            # FIT SDK: monitoring_b
    "FIT_TYPE_34": N_("segments"),              # FIT SDK: segment
    "FIT_TYPE_35": N_("segment list"),          # FIT SDK: segment_list; upload: segment-list
    "FIT_TYPE_38": N_("golf scorecards"),       # folder: SCORCRDS
    "FIT_TYPE_41": N_("settings change log"),   # FIT profile: settings_change_log; queue
    "FIT_TYPE_44": N_("metrics"),               # upload: metrics; queue
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
    "FIT_TYPE_87": N_("gear tracking"),         # FIT profile: gear_tracking; queue
    "FIT_TYPE_88": N_("multi-day activities"),  # upload: multi-day-activity
    "BACKUP_PRIMARY": N_("device backup"),      # upload: backup
    "BACKUP_SUPPLEMENTARY": N_("device backup"),
    "ACTIVITY_GCPD": N_("raw GPS data of activities"),  # upload: rawgnssactivity
}


def type_name(datatype: str) -> str:
    """The readable name of a data type of new files (and of the files of Garmin Connect's
    queue), in the language of the moment."""
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
    if kind == "received_files":
        types = _counted_types(line)
        if not types:
            return []
        n = sum(types.values())
        # TRANSLATORS: a line of the details of "Last sync": files of Garmin Connect's queue
        # written to the device, and their types (as "Files from the device: …" names them)
        return [(ngettext("{count} file from Garmin Connect: {types}",
                          "{count} files from Garmin Connect: {types}", n).format(
            count=n, types=_describe_types(types)), False)]
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
    if kind == "marked_known" and (n := _count(line)) is not None:
        return [(ngettext("Marked {count} existing file as already uploaded",
                          "Marked {count} existing files as already uploaded",
                          n).format(count=n), False)]
    if kind == "tracker_conflict" and (n := _count(line)) is not None:
        return [(ngettext(
            "Skipped {count} file: another device is your active tracker in Garmin Connect",
            "Skipped {count} files: another device is your active tracker in Garmin Connect",
            n).format(count=n), False)]
    if kind == "apps_installed":
        names = line.get("names")
        if not (isinstance(names, list) and names and all(isinstance(n, str) for n in names)):
            return []
        return [(_("Installed Connect IQ apps: {names}").format(names=", ".join(names)), False)]
    if kind == "apps_waiting" and (n := _count(line)) is not None:
        if line.get("flatpak") is True:
            return [(ngettext(
                "The Flatpak has no access to USB devices; {count} Connect IQ app waits in the "
                "queue",
                "The Flatpak has no access to USB devices; {count} Connect IQ apps wait in the "
                "queue", n).format(count=n), False)]
        return [(ngettext(
            "{count} Connect IQ app waits in the queue: openExpress cannot install apps on "
            "this device over USB yet",
            "{count} Connect IQ apps wait in the queue: openExpress cannot install apps on "
            "this device over USB yet", n).format(count=n), False)]
    if kind == "trueup":
        return [(_("TrueUp: daily totals aligned with your other Garmin devices"), False)]
    if kind == "trueup_stopped":
        # TRANSLATORS: a line of the details of "Last sync" and of the end of
        # `openexpress sync --apply`
        return [(_("TrueUp: waiting stopped; it comes with the next sync"), False)]
    if kind == "ecg_not_uploaded" and (n := _count(line)):
        # TRANSLATORS: a line of the details of "Last sync" and of the end of
        # `openexpress sync --apply`
        return [(ngettext(
            "{count} ECG recording was not uploaded: ECG recordings need a Garmin Health "
            "account",
            "{count} ECG recordings were not uploaded: ECG recordings need a Garmin Health "
            "account", n).format(count=n), False)]
    if kind == "not_accepted" and (n := _count(line)) is not None:
        return [(ngettext(
            "Skipped {count} item from Garmin Connect (not accepted by the device)",
            "Skipped {count} items from Garmin Connect (not accepted by the device)",
            n).format(count=n), False)]
    return []


def _counted_types(line: dict) -> dict[str, int]:
    """The data types of a step and how many of each, as a sync writes them (names, and
    numbers above zero); nothing when anything else is there."""
    types = line.get("types")
    if not isinstance(types, dict) or not types:
        return {}
    if not all(isinstance(t, str) and isinstance(n, int) and not isinstance(n, bool) and n > 0
               for t, n in types.items()):
        return {}
    return types


def _count(line: dict) -> int | None:
    """The number of a step as a sync writes it (an int, not below zero); None otherwise."""
    n = line.get("count")
    return n if isinstance(n, int) and not isinstance(n, bool) and n >= 0 else None


def marked_known_step(count: int) -> dict:
    """The first sync marked the files on the device as uploaded already (baseline): a step
    of the summary, put into words when it is shown, as all those below."""
    return {"kind": "marked_known", "count": count}


def tracker_conflict_step(count: int) -> dict:
    """Files not uploaded because another device is the active tracker in Garmin Connect."""
    return {"kind": "tracker_conflict", "count": count}


def apps_installed_step(names) -> dict:
    """The Connect IQ apps of the queue written to the device (their names, as the store
    gives them)."""
    return {"kind": "apps_installed", "names": list(names)}


def apps_waiting_step(count: int, flatpak: bool) -> dict:
    """Connect IQ apps left in the queue: the Flatpak without access to USB devices
    (`flatpak`), or a device this program cannot install apps on over USB yet."""
    return {"kind": "apps_waiting", "count": count, "flatpak": flatpak}


def trueup_step() -> dict:
    """TrueUp of the queue written to the device."""
    return {"kind": "trueup"}


def trueup_stopped_step() -> dict:
    """The wait for TrueUp stopped by the user: nothing of it written."""
    return {"kind": "trueup_stopped"}


def received_files_step(datatypes) -> dict:
    """The files of Garmin Connect's queue written to the device (courses, workouts,
    metrics…), counted by data type; TrueUp and the Connect IQ apps have steps of their own
    and are not in it."""
    return {"kind": "received_files", "types": dict(collections.Counter(datatypes))}


def ecg_not_uploaded_step(count: int) -> dict:
    """ECG recordings on the device, never uploaded: they need a Garmin Health account
    (sync.HEALTH_TYPES)."""
    return {"kind": "ecg_not_uploaded", "count": count}


def not_accepted_step(count: int) -> dict:
    """Items of Garmin Connect's queue the device does not take (their data type)."""
    return {"kind": "not_accepted", "count": count}


def new_files_step(candidates) -> dict:
    """The new files of a sync, counted by data type: a step of the summary."""
    return {"kind": "new_files",
            "types": dict(collections.Counter(c.datatype for c in candidates))}


def not_uploaded(data: dict) -> dict | None:
    """The step of a sync record (last_sync.json) that says it uploaded nothing because the
    account did not let it (not_uploaded_step), or None."""
    return next((line for line in data.get("lines") or [] if isinstance(line, dict)
                 and line.get("kind") == "not_uploaded"), None)


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
    """The numbers of a sync in one line: the new files on the device and how many were
    uploaded, then what came from Garmin Connect's queue, if anything ("8 new files, 6
    uploaded · 2 received"). The window shows it under "Last sync" after when it was; the
    command line and the notification put it before the details."""
    files, uploaded = _new_files(data), data.get("uploaded", 0)
    # Its files, TrueUp and the Connect IQ apps written (a record of an older version counts
    # only the files).
    received = data.get("downloaded", 0)
    blocked = not_uploaded(data)
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


class SyncBusy(OrdinaryStop):
    log_reason = "another sync is running"


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
    options: SyncOptions | None = None,
) -> SyncSummary:
    """A whole sync: planned (plan_sync) and done (apply_sync) under the sync lock, one process
    at a time; returns its summary. Leaves its record (last_sync.json, also of a sync
    stopped after its uploads) and logs its start, end and errors. `first_run`: 'baseline' or
    'all' for the first sync; `options` (the command line's) hold it and the diagnostics
    instead (ValueError with both)."""
    # The window and the background service ask for everything.
    if options is None:
        options = SyncOptions(first_run=first_run, include_diagnostics=include_diagnostics)
    elif first_run is not None or include_diagnostics:
        raise ValueError("run_sync: first_run and include_diagnostics go in the options")
    if s.mass_storage:
        log.info("sync: refused, the device is in mass storage mode")
        raise MassStorageMode(_("A device connected as a USB drive is not synced."))
    started = time.monotonic()
    log.info("sync: start (first run: %s, diagnostics: %s)", options.first_run or "-",
             options.include_diagnostics)
    try:
        with sync_lock():
            summary = apply_sync(s, plan_sync(s, options, progress), progress)
    except OrdinaryStop as e:
        log.info("sync: %s", e.log_reason)  # the caller tells the user
        raise
    except KeyboardInterrupt as e:
        log.info("sync: interrupted after %.1f s (Ctrl+C)", time.monotonic() - started)
        # TRANSLATORS: under "Last sync", in its details: Ctrl+C on the command line
        _keep_stopped(s.device.unit_id, e, _("The sync was interrupted before the end (Ctrl+C)"))
        raise
    except Exception as e:
        log.warning("sync: stopped after %.1f s: %s: %s", time.monotonic() - started,
                    type(e).__name__, e, extra=applog.TOLD)  # the caller tells why
        # TRANSLATORS: under "Last sync", in its details: the error that stopped the sync
        _keep_stopped(s.device.unit_id, e, _("The sync stopped before the end: {error}").format(
            error=_stop_text(e)))
        raise
    save_last_sync(s.device.unit_id, summary)
    log.info("sync: end after %.1f s: %d uploaded, %d already known, %d downloaded, %d errors",
             time.monotonic() - started, summary.uploaded, summary.skipped_known,
             summary.downloaded, len(summary.errors))
    for error in summary.log_errors:
        log.warning("sync error: %s", error, extra=applog.TOLD)  # in summary.errors
    return summary


@dataclass(frozen=True)
class SyncOptions:
    """What a sync is asked to do. The window and the background service always ask for
    all of it; the command line may be asked for less (its options)."""

    first_run: str | None = None  # the first sync: "baseline" or "all"; None: not chosen
    include_diagnostics: bool = False  # also the device's diagnostics and telemetry
    only_types: frozenset[str] | None = None  # only these data types (sync --type)
    uploads: bool = True  # False: the uploads are not looked at (sync --download-only)
    downloads: bool = True  # False: Garmin Connect's queue is not (sync --upload-only)
    # Only to be shown (the dry run of the command line): no login needed (the uploads
    # alone then), the first sync planned without its choice, the queue read with the plan.
    preview: bool = False


@dataclass
class QueuePlan:
    """Garmin Connect's queue for the device, and what becomes of each item of it
    (sync.DownloadItem: written to the device, handled by Connect IQ or TrueUp, or not
    taken by the device)."""

    messages: list[dict]
    items: list[sync.DownloadItem]


@dataclass
class SyncPlan:
    """What a sync will do, worked out once (plan_sync): shown by the dry run of the command
    line, done by apply_sync. Nothing of it is written yet."""

    options: SyncOptions
    # What was uploaded before, as the plan was made against it: with the files of the
    # baseline marked, in memory, when the first sync has one.
    state: sync.SyncState
    config: dict | None = None  # the device's upload settings; None: not asked
    baseline: list | None = None  # the files the first sync marks as uploaded already
    gate: UploadGate | None = None  # whether data may be uploaded; None: not asked
    uploads: sync.UploadPlan | None = None  # None: the uploads are not looked at
    queue: QueuePlan | None = None  # read with the plan for a preview only


def plan_sync(s: DeviceSession, options: SyncOptions | None = None,
              progress: Progress = lambda m: None) -> SyncPlan:
    """What a sync of the device will do (a SyncPlan), in the order it does it: the device
    checked against the account, its upload settings, the first sync (its baseline marked in
    memory, or the choice asked for: FirstSyncChoiceNeeded), whether the account lets data be
    uploaded, and the files to upload. Writes and uploads nothing."""
    options = options or SyncOptions()
    if not s.logged_in and not options.preview:
        raise store.NotLoggedIn(_("Not logged in to Garmin Connect."))
    if s.logged_in:
        require_account_device(s.client, s.device.unit_id)
    plan = SyncPlan(options, s.state)
    if options.uploads:
        progress(Waiting(_("Fetching configuration from the Garmin server…")))
        plan.config = s.client.upload_settings(s.device.unit_id)
        if not plan.state.file.exists():  # the first sync
            if options.first_run == "baseline":
                plan.baseline = sync.plan_baseline(s.device, s.storage, plan.config,
                                                   plan.state)
                sync.note_baseline(plan.baseline, plan.state)
            elif options.first_run != "all" and not options.preview:
                raise FirstSyncChoiceNeeded()
        progress(_("Looking for new files on the device…"))
        if s.logged_in:
            plan.gate = check_upload_allowed(s.client)
        plan.uploads = sync.plan_uploads(
            s.device, s.storage, plan.config, plan.state, only_types=options.only_types,
            include_diagnostics=options.include_diagnostics,
            consent_granted=plan.gate.granted if plan.gate is not None else None)
    # Garmin Connect's queue is read by apply_sync, after the uploads, in the order Garmin
    # Express syncs; a preview reads it now (nothing is uploaded before it).
    if options.preview and options.downloads and s.logged_in:
        plan.queue = plan_queue(s, progress)
    return plan


def plan_queue(s: DeviceSession, progress: Progress = lambda m: None) -> QueuePlan:
    """Garmin Connect's queue for the device, and what becomes of each item of it."""
    progress(Waiting(_("Checking for files from Garmin Connect…")))
    messages = s.client.device_messages(s.device.unit_id)
    return QueuePlan(messages, sync.plan_downloads(s.device, messages))


def apply_sync(s: DeviceSession, plan: SyncPlan,
               progress: Progress = lambda m: None) -> SyncSummary:
    """The sync of a plan (plan_sync): its baseline written, its files uploaded and
    state.json written, then Garmin Connect's queue read and its files written to the
    device, with its Connect IQ apps and TrueUp. What was done, in steps
    (SyncSummary.lines). The record is kept by run_sync."""
    options = plan.options
    if options.preview:
        raise ValueError("a preview of a sync is only to be shown")
    out = SyncSummary()
    state = plan.state
    if not options.uploads:
        # The uploads were not looked at: the row says so, not "nothing to upload".
        out.lines.append(upload_skipped_step())
    elif options.only_types:
        # Only these types were looked at: the row says its numbers are of them only.
        out.lines.append(only_types_step(options.only_types))
    if plan.baseline is not None:
        out.lines.append(marked_known_step(
            sync.save_baseline(s.device, plan.baseline, state, s.client)))
    if plan.uploads is not None:
        gate = plan.gate if plan.gate is not None else UploadGate(NOT_CHECKED)
        if not gate.allowed:
            # Files from Connect are still written to the device below.
            out.lines.append(not_uploaded_step(gate))
        elif plan.uploads.candidates:
            out.lines.append(new_files_step(plan.uploads.candidates))
            rep = sync.run_uploads(plan.uploads, s.device, s.storage, s.client, state,
                                   progress)
            out.uploaded = len(rep.uploaded)
            out.skipped_known = len(rep.duplicates) + len(rep.already_in_connect)
            out.rejected = len(rep.rejected)
            out.errors += [f"{label}: {err}" for label, err in rep.failed]
            out.log_errors += [f"{sync.log_label(label)}: {sync.log_error(label, err)}"
                               for label, err in rep.failed]
            out.not_confirmed += rep.not_confirmed
            out.lines.append(uploads_step(rep))
            if rep.tracker_conflict:
                out.lines.append(tracker_conflict_step(len(rep.tracker_conflict)))
        if gate.allowed and plan.uploads.ecg_recordings:
            out.lines.append(ecg_not_uploaded_step(plan.uploads.ecg_recordings))
        # Written before the queue is read: a first sync stopped later (the queue, a
        # download) has made its choice all the same. Not without the uploads: then the
        # choice of the first sync stays to be made.
        state.save()
    # No new file on the device: the row says it ("nothing to upload"), no line of its own.
    if options.downloads:
        try:
            _apply_queue(s, plan_queue(s, progress), state, progress, out)
        except (Exception, KeyboardInterrupt) as e:  # Ctrl+C too: the uploads are done
            e.sync_done_before = out  # what was done before it stopped (done_before)
            raise
    return out


def _stop_text(error: Exception) -> str:
    """The error that stopped a sync, as the record keeps it: its words, those of a network
    error without the address it names."""
    if isinstance(error, requests.RequestException):
        return applog.network_error_text(error)
    return str(error) or type(error).__name__


def _keep_stopped(unit_id: int, error: BaseException, why: str) -> None:
    """Keeps the record of a sync stopped after its uploads: what it did (done_before), with
    `why` it stopped as one of its errors, so that "Last sync" and the tiles tell this sync,
    whose files are in Garmin Connect. Stopped before its uploads, nothing was done and the
    record of the sync before stays."""
    done = done_before(error)
    if done is None:
        return
    # A copy: the summary on the error stays as it is (the command line prints it).
    kept = replace(done, errors=[*done.errors, why])
    try:
        save_last_sync(unit_id, kept)
    except Exception as e:  # noqa: BLE001 - the error that stopped the sync is the one told
        log.warning("sync: the record of the stopped sync could not be kept: %s: %s",
                    type(e).__name__, e)
        return
    log.info("sync: the record of the stopped sync kept: %d uploaded, %d already known, "
             "%d downloaded, %d errors", kept.uploaded, kept.skipped_known, kept.downloaded,
             len(kept.errors))


def done_before(error: BaseException) -> SyncSummary | None:
    """What a sync did before `error` stopped it after its uploads (Garmin Connect's queue,
    a download): the command line tells it with the error. None when it stopped before."""
    return getattr(error, "sync_done_before", None)


def _apply_queue(s, queue: QueuePlan, state, progress, out: SyncSummary) -> None:
    items = queue.items
    todo = [i for i in items if not i.unsupported and not i.handled_by]
    if todo:
        drep = sync.run_downloads(todo, s.storage, s.client, progress, state=state)
        out.downloaded = len(drep.written)
        if drep.written_types:
            out.lines.append(received_files_step(drep.written_types))
        out.errors += [f"{label}: {err}" for label, err in drep.failed]
        out.errors += [_("{name}: written, but Garmin Connect was not notified (will retry)")
                       .format(name=label) for label in drep.ack_failed]
        out.log_errors += [f"{quiet}: {err}" for quiet, err in drep.failed_in_log]
        out.log_errors += [f"{quiet}: written, but Garmin Connect was not notified (will retry)"
                           for quiet in drep.ack_failed_in_log]
    told = len(out.errors)  # the errors of the apps and TrueUp name nothing of the user's
    received: list[str] = []  # what of them came to the device: received as the files
    if any(i.handled_by == "Connect IQ" for i in items):
        out.lines += _install_queued_apps(s, queue.messages, progress, out.errors, received)
    if any(i.handled_by == "TrueUp" for i in items):
        out.lines += _trueup(s, queue.messages, progress, out.errors, received)
    out.downloaded += len(received)
    out.log_errors += out.errors[told:]
    skipped = [i for i in items if i.unsupported]
    if skipped:
        out.lines.append(not_accepted_step(len(skipped)))


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
    from openexpress import tile_facts  # it uses this module

    tile_facts.note_synced(unit_id)  # for its tile


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
    that cannot be drawn. A field left out reads as its default (0, an empty list)."""
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


def sync_stamp(unit_id: int | None) -> str | None:
    """The version of what this installation knows of the sync of a device: when its
    state.json was written and its size, and the time of its last sync recorded. A count of
    its activities is shown from a record only while this is the same (known_devices.
    SyncCount). None: never synced here (no state.json)."""
    if not unit_id:
        return None
    try:
        st = sync.state_path(unit_id).stat()
    except OSError:
        return None
    record = load_last_sync(unit_id)
    when = record.get("time") if record else None
    return f"{st.st_mtime_ns}-{st.st_size}-{when}"


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


def _install_queued_apps(s, messages, progress, errors,
                         received: list | None = None) -> list[dict]:
    """The Connect IQ apps of the queue written to the device: their lines for the details;
    their names go to `received` (counted as received, as the files of the queue). An app
    left in the queue is not received."""
    from openexpress import ciq

    # A sync installs only what already waits in the queue (asked for by the phone, the store
    # or `apps install`, each with its own consent) and never asks the store for updates:
    # nothing here needs the consent to new permissions. Should it ever update apps by itself,
    # it must leave those with permissions_changed, as Garmin Express does.
    try:
        known = {a.app_id: a for a in ciq.StoreApi(s.client).account_apps(
            s.device, language_tag(s.settings.locale))}
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
    if received is not None:
        received += rep.installed
    lines = []
    if rep.installed:
        lines.append(apps_installed_step(rep.installed))
    if rep.for_phone:
        lines.append(apps_waiting_step(
            len(rep.for_phone), flatpak=all(name in rep.sandboxed for name in rep.for_phone)))
    return lines


def _trueup(s, messages, progress, errors, received: list | None = None) -> list[dict]:
    """TrueUp of the queue written to the device: its line for the details; written, it goes
    to `received` (counted as received, as the files of the queue)."""
    from openexpress import trueup

    # Passed on as it is: a stop asked during this sync (the background service quitting
    # during the uploads) ends the wait at once. The window clears one left from its
    # previous sync when a sync starts (start_sync).
    stop = getattr(s, "stop_waiting", None)
    try:
        res = trueup.run(s.device, s.storage, s.client, messages, progress, stop=stop)
    except Exception as e:  # noqa: BLE001
        log.warning("TrueUp failed: %s: %s", type(e).__name__, e)
        errors.append(f"TrueUp: {e}")
        return []
    if res.stopped:
        return [trueup_stopped_step()]
    if res.written:
        if received is not None:
            received.append("TrueUp")
        return [trueup_step()]
    return []  # nothing to write, or not supported: in the log only (trueup.run)


class FirstSyncChoiceNeeded(OrdinaryStop):
    """First sync: either 'baseline' or 'all' must be chosen."""

    log_reason = "waiting for the first-sync choice"
