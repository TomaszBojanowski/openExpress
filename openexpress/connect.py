"""HTTP client for Garmin services (Connect API, OMT).

The calls and their meaning follow what Garmin Express sends.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit

import requests

from openexpress import answers, applog, netstatus
from openexpress.auth import store
from openexpress.auth.oauth1 import authorization_header
from openexpress.auth.store import ConnectAuth
from openexpress.errors import UserFacingError
from openexpress.i18n import _, pgettext
from openexpress.settings import UPLOAD_CONFIG_CLIENT_ID, Consumer, Settings, language_tag
from openexpress.useragent import USER_AGENT
from openexpress.useragent import session as user_agent_session

log = logging.getLogger(__name__)

TIMEOUT = 60
PRIMARY_TRAINING_PATH = "userprofile-service/userprofile/user-settings/primary-training-device"
REGISTER_PATH = "device-service/deviceregistration/device"
# A 409 of the registration with this subcode: another account has an active subscription on
# the device.
SUBSCRIPTION_SUBCODE = 4091


def _hosts(client) -> dict[str, str]:
    """The hosts of the client's environment, for the network state (shown only)."""
    settings = getattr(client, "settings", None)
    return settings.hosts if settings is not None else {}


class ApiError(UserFacingError):
    def __init__(self, message: str, status: int | None = None, body: str = ""):
        super().__init__(message)
        self.status = status
        self.body = body


class Unauthorized(ApiError):
    pass


class NoApplicationKey(ApiError):
    """A request to sign without Garmin Express's application key: the installer was not
    chosen (or its key went away meanwhile)."""


class ClockSkew(ApiError):
    pass


class OwnershipError(UserFacingError):
    """The connected watch could not be confirmed as a device of the logged-in account."""


class NotAccountDevice(OwnershipError):
    """The watch is not on the account's device list."""


class OwnershipUnknown(OwnershipError):
    """The account's device list could not be fetched or understood."""


class DeviceNotVerified(OwnershipError):
    """An account request for a watch was attempted without the ownership check."""


class RegistrationRefused(ApiError):
    """Garmin did not add the device to the account; nothing was changed."""


class OtherOwner(RegistrationRefused):
    """The device is registered to another Garmin account (HTTP 409); `subscription`: that
    account also has an active subscription on it."""

    def __init__(self, message: str, subscription: bool):
        super().__init__(message, 409)
        self.subscription = subscription


def lower_keys(obj: Any) -> Any:
    """Garmin servers return either camelCase or PascalCase; normalize the keys."""
    if isinstance(obj, dict):
        return {str(k).lower(): lower_keys(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [lower_keys(v) for v in obj]
    return obj


@dataclass
class Registration:
    """What is kept of Garmin's answer to adding a device to the account. The answer also
    carries the device's own OAuth credentials (for its uploads over Wi-Fi); those are never
    kept, logged or written anywhere."""

    new: bool | None
    status: str  # e.g. "active" or "retired"; "" when not told


@dataclass
class UploadResult:
    status: int
    body: Any
    location: str | None
    poll_delay_ms: int | None


class GarminClient:
    def __init__(
        self,
        settings: Settings,
        consumer: Consumer | None = None,
        auth: ConnectAuth | None = None,
        session: requests.Session | None = None,
    ):
        self.settings = settings
        self.consumer = consumer
        self.auth = auth
        self.http = session or user_agent_session()
        self.session_id = str(uuid.uuid4())
        # Set by service.require_account_device(); kept in memory only.
        self.verified_unit_id: int | None = None
        # The devices this client added to the account (register_device): the only ones whose
        # status it may change before the account lists them.
        self.registered_unit_ids: set[int] = set()

    # --- helpers ---------------------------------------------------------------------

    def _garmin_headers(self) -> dict[str, str]:
        """The Garmin-Client-* headers of Garmin Express, and our own User-Agent."""
        s = self.settings
        return {
            "Garmin-Client-Name": s.client_name,
            "Garmin-Client-Version": s.client_version,
            "Garmin-Client-Platform": s.client_platform,
            "Garmin-Client-Platform-Version": s.client_platform_version,
            "Garmin-Client-SessionId": self.session_id,
            "User-Agent": USER_AGENT,
        }

    def _client_headers(self) -> dict[str, str]:
        """The Garmin Connect API: as Garmin Express, also with its client ID (and no
        language)."""
        return {**self._garmin_headers(), "X-garmin-client-id": "EXPRESS"}

    def _omt_headers(self) -> dict[str, str]:
        """Garmin's update, map and upload configuration service (OMT): as Garmin Express,
        without the client ID and with the language as a tag ("de-DE") in both headers."""
        tag = language_tag(self.settings.locale)
        return {**self._garmin_headers(), "Garmin-Client-LocaleCode": tag,
                "Accept-Language": tag, "Accept": "application/json"}

    def _store_headers(self) -> dict[str, str]:
        """The Connect IQ store: as Garmin Express only its client ID (and our User-Agent);
        the request is signed with the token besides."""
        return {"X-garmin-client-id": "EXPRESS", "User-Agent": USER_AGENT}

    def _sign(
        self,
        method: str,
        url: str,
        form: list[tuple[str, str]] | None = None,
        with_token: bool = True,
    ) -> str:
        if self.consumer is None:
            log.warning("connect: no OAuth1 consumer key (consumer.json missing or damaged): "
                        "the request is not signed")
            raise NoApplicationKey(_(
                "openExpress has no application key yet: first choose the Garmin Express "
                "installer for Windows (in the window: Preferences, Setup; on the command "
                "line: openexpress setup --from-installer GarminExpress.exe)."))
        token = secret = None
        if with_token:
            if self.auth is None:
                raise Unauthorized(_("Connect token is missing (openexpress login)."))
            token, secret = self.auth.token, self.auth.token_secret
        return authorization_header(
            method, url, self.consumer.key, self.consumer.secret, token, secret, form
        )

    def require_verified(self, unit_id: int | None = None) -> None:
        """Refuse account requests for a watch that was not checked against the account.

        Without `unit_id` (requests that do not name the watch) any verified watch is enough.
        """
        if self.verified_unit_id is None or unit_id not in (None, self.verified_unit_id):
            raise DeviceNotVerified(
                _("Internal error: this device was not checked against your Garmin account. "
                  "Nothing was transferred."))

    @staticmethod
    def _prepared_url(method: str, url: str) -> str:
        """The URL exactly as requests will send it (signatures must cover this form)."""
        return requests.Request(method, url).prepare().url

    def _request(self, method: str, url: str, what: str, quiet: bool = False,
                 service: str | None = None, **kw) -> requests.Response:
        """Send a request; network failures become ApiError, whose text names no address.
        `quiet` keeps the address out of the log, for a path that names the device.
        `service`: the service of the network state it is noted under (netstatus), for a
        host that is not of the environment (the Connect IQ store); else by its host."""
        kw.setdefault("timeout", TIMEOUT)
        started = time.monotonic()
        # Only the host and the path go to the log: no query, no headers, no body.
        where = (urlsplit(url).netloc if quiet
                 else urlsplit(url)._replace(query="", fragment="").geturl())
        try:
            resp = self.http.request(method, url, **kw)
        except requests.RequestException as e:
            log.warning("HTTP %s %s failed after %.1f s: %s", method, where,
                        time.monotonic() - started, type(e).__name__)
            if service is None:
                netstatus.record_failure(_hosts(self), url)
            else:
                netstatus.record(service, url, None)
            # Its text names the whole address, query and all (the sign-in ticket of the ticket
            # exchange, a unit ID): left out, and the rest filtered like the log, before the
            # window or the command line shows it.
            raise ApiError(_("{what}: network error ({error})").format(
                what=what, error=applog.network_error_text(e))) from e
        log.info("HTTP %s %s -> %s in %.1f s", method, where, resp.status_code,
                 time.monotonic() - started)
        if service is None:
            netstatus.record_response(_hosts(self), url, resp)
        else:
            netstatus.record(service, url, resp.status_code, resp)
        return resp

    @staticmethod
    def _json(resp: requests.Response, what: str) -> Any:
        try:
            return resp.json()
        except ValueError as e:  # includes requests' JSONDecodeError
            raise ApiError(
                _("{what}: unexpected response from the server").format(what=what),
                resp.status_code,
                resp.text[:500],
            ) from e

    @staticmethod
    def _clock_skew(resp: requests.Response, what: str) -> ClockSkew | None:
        """The error for a refusal (401, 403) the computer's wrong clock caused, if it did."""
        skew = netstatus.clock_skew(resp)
        if netstatus.kind_of(resp.status_code, skew) != netstatus.CLOCK:
            return None
        return ClockSkew(
            _("{action}: computer clock is off from the server by {minutes} min")
            .format(action=what, minutes=int(skew // 60)),
            resp.status_code,
        )

    def _check(self, resp: requests.Response, what: str) -> requests.Response:
        if resp.status_code in (401, 403):
            skew = self._clock_skew(resp, what)
            if skew is not None:
                raise skew
            raise Unauthorized(
                _("{action}: unauthorized (HTTP {status}). Please sign in again.")
                .format(action=what, status=resp.status_code),
                resp.status_code,
                resp.text[:500],
            )
        if not resp.ok:
            raise ApiError(f"{what}: HTTP {resp.status_code}", resp.status_code, resp.text[:500])
        return resp

    def connect_url(self, path: str) -> str:
        return urljoin(self.settings.hosts["connectapi"], path.lstrip("/"))

    def _connect(
        self,
        method: str,
        path: str,
        what: str,
        params: dict | list | None = None,
        form: list[tuple[str, str]] | None = None,
        headers: dict | None = None,
        **kw,
    ) -> requests.Response:
        url = self.connect_url(path)
        if params:
            url += ("&" if "?" in url else "?") + urlencode(params, doseq=True)
        url = self._prepared_url(method, url)
        h = self._client_headers()
        h.update(headers or {})
        h["Authorization"] = self._sign(method, url, form)
        data = urlencode(form) if form is not None else kw.pop("data", None)
        if form is not None:
            h["Content-Type"] = "application/x-www-form-urlencoded"
        resp = self._request(method, url, what, headers=h, data=data, **kw)
        return self._check(resp, what)

    # --- authentication -------------------------------------------------------------

    def exchange_ticket(self, ticket: str, service: str) -> tuple[str, str]:
        """Exchange an SSO ticket for a Garmin Connect OAuth1 token."""
        url = self.connect_url("oauth-service/oauth/preauthorized") + "?" + urlencode(
            {"ticket": ticket, "login-url": service}
        )
        url = self._prepared_url("GET", url)
        # TRANSLATORS: name of a server request (sign-in: a one-time ticket traded for a token),
        # shown before an error
        what = _("Ticket exchange")
        h = self._client_headers()
        h["Authorization"] = self._sign("GET", url, with_token=False)
        resp = self._check(self._request("GET", url, what, headers=h), what)
        data = dict(parse_qsl(resp.text))
        if not data.get("oauth_token") or not data.get("oauth_token_secret"):
            raise ApiError(_("Ticket exchange: no oauth_token in the response"), resp.status_code)
        return data["oauth_token"], data["oauth_token_secret"]

    def user_profile(self) -> dict:
        # TRANSLATORS: name of a server request, shown before an error
        what = _("Profile")
        r = self._connect("GET", "userprofile-service/userprofile/userProfileBase", what)
        return lower_keys(self._json(r, what))

    # --- upload ---------------------------------------------------------------------

    def upload_settings(self, unit_id: int) -> dict:
        """Upload configuration for the device (OMT, no login needed)."""
        url = urljoin(
            self.settings.hosts["omt"],
            f"UploadConfigurationService/UnitUploadSettings/{unit_id}",
        )
        h = self._omt_headers()
        h["Garmin-Client-Guid"] = self.settings.client_guid
        # TRANSLATORS: name of a server request (the device's upload settings), shown before an
        # error
        what = _("Upload configuration")
        resp = self._request(
            "GET",
            url,
            what,
            params={"clientId": UPLOAD_CONFIG_CLIENT_ID, "enforceConsent": "False"},
            headers=h,
        )
        return lower_keys(self._json(self._check(resp, what), what))

    def activity_matches(self, unit_id: int, external_ids: list[str]) -> dict[str, bool]:
        """Which activities (by time_created) Connect already has. Returns {id: in_connect}."""
        self.require_verified(unit_id)
        if not external_ids:
            return {}
        ids = sorted(external_ids)
        params = [("deviceId", str(unit_id))] + [("externalIds", i) for i in ids]
        # TRANSLATORS: name of a server request (are these activities already in Garmin Connect?),
        # shown before an error
        what = _("Duplicate check")
        # Like Garmin Express: the same parameters in the URL and as a form body, but the
        # body is NOT part of the OAuth1 signature (Garmin rejects such a signature with 401).
        r = self._connect(
            "POST",
            "activity-search-service-1.2/json/matchesByDeviceId",
            what,
            params=params,
            data=urlencode(params),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        body = lower_keys(self._json(r, what))
        matches = (body.get("matches") if isinstance(body, dict) else None) or {}
        if not isinstance(matches, dict):
            raise ApiError(_("{what}: unexpected response from the server").format(what=what))
        out = {}
        for i in ids:
            m = matches.get(i.lower()) or {}
            out[i] = bool(m.get("match") or m.get("deletedmatch"))
        return out

    def upload_file(
        self,
        url: str,
        data: bytes,
        *,
        is_zip: bool,
        last_file: bool,
        authenticated: bool,
    ) -> UploadResult:
        self.require_verified()
        headers: dict[str, str] = {"User-Agent": USER_AGENT}
        files = None
        body: bytes | None = data
        if is_zip:
            files = {"data": ("", data, "application/zip")}
            body = None
        else:
            headers["Content-Type"] = "application/octet-stream"
        if last_file:
            headers["lastFile"] = "true"
        # TRANSLATORS: name of a server request (file upload), shown before an error
        what = _("Upload")
        url = self._prepared_url("POST", url)
        if authenticated:
            headers["Authorization"] = self._sign("POST", url)
        resp = self._request("POST", url, what, data=body, files=files, headers=headers)
        if resp.status_code in (401, 403):
            self._check(resp, what)
        try:
            parsed = lower_keys(resp.json())
        except ValueError:
            parsed = resp.text[:500]
        delay = resp.headers.get("location-in-milliseconds")
        location = resp.headers.get("Location")
        return UploadResult(
            resp.status_code,
            parsed,
            urljoin(url, location) if location else None,
            int(delay) if delay and delay.isdigit() else None,
        )

    def poll_location(self, url: str, delay_ms: int | None, timeout_s: float = 30) -> int:
        """Wait for the activity to be processed (201 Created) at `url`, the whole address
        (upload_file resolves the one the server gives). Returns the last HTTP status; 0 means
        the status could not be checked (network error).
        """
        self.require_verified()
        url = self._prepared_url("GET", url)
        deadline = time.monotonic() + timeout_s
        delay = (delay_ms or 1000) / 1000
        status = 0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(max(0.0, min(delay, remaining)))
            h = {"Authorization": self._sign("GET", url), "User-Agent": USER_AGENT}
            try:
                resp = self.http.get(url, headers=h, timeout=TIMEOUT)
            except requests.RequestException as e:
                log.warning("Upload status check failed (%s)", e)
                netstatus.record_failure(_hosts(self), url)
                return 0
            netstatus.record_response(_hosts(self), url, resp)
            status = resp.status_code
            log.debug("upload status check -> %s", status)
            if status != 202:
                return status
            d = resp.headers.get("location-in-milliseconds")
            if d and d.isdigit():
                delay = int(d) / 1000
        return status

    # --- OMT: device info, software, maps (no login needed) --------------------------

    def _omt(self, method: str, path: str, what: str, quiet: bool = False, **kw) -> Any:
        url = urljoin(self.settings.hosts["omt"], path)
        resp = self._request(method, url, what, quiet=quiet, headers=self._omt_headers(), **kw)
        self._check(resp, what)
        return lower_keys(self._json(resp, what)) if resp.content else {}

    def unit_info(self, unit_id: int, part_number: str) -> dict:
        """What Garmin's update server knows about the device; needs no login. Kept for a while
        (answers.LIFETIMES), only the fields the program reads: the answer carries the unit ID
        and the serial number, never logged or kept."""
        def ask():
            return self._omt(
                "GET",
                f"api/unit-info/manufacturers/Taiwan/unitids/{unit_id}",
                # TRANSLATORS: name of a server request (information about the device), shown before
                # an error
                _("Device info"),
                quiet=True,  # the path names the device
                params={"primaryFirmwarePartNumber": part_number},
            )

        return answers.ANSWERS.ask(answers.UNIT_INFO,
                                   (self.settings.environment, unit_id, part_number), ask,
                                   about=[unit_id])

    def _omt_scope(self) -> tuple:
        """What an answer of the update server depends on besides the question: the
        environment and the language it answers in."""
        return self.settings.environment, language_tag(self.settings.locale)

    def software_updates(self, device_xml: bytes, include_reinstalls: bool = False,
                         fresh: bool = False) -> dict:
        """The updates offered to the device; kept by its GarminDevice.xml (answers), so that
        the device page and its page Updates share one answer. `fresh`: asked anew."""
        def ask():
            return self._omt(
                "POST",
                "api/device-software/v1/updates",
                # TRANSLATORS: name of a server request (looking for device software updates), shown
                # before an error
                _("Software update check"),
                params={"filter": "all"} if include_reinstalls else None,
                json={
                    "garminDeviceXml": device_xml.decode("utf-8"),
                    "isUserInteractive": True,
                    "clientInfo": {},
                },
            )

        return answers.ANSWERS.ask(
            answers.SOFTWARE_UPDATES,
            (*self._omt_scope(), answers.xml_key(device_xml), include_reinstalls), ask,
            about=answers.device_tags(device_xml), fresh=fresh)

    def universal_maps(self, device_xml: bytes, reinstall: bool = False,
                       fresh: bool = False) -> dict:
        """The maps of the device; kept by its GarminDevice.xml (answers), so that the device
        page and its page Maps share one answer. `fresh`: asked anew."""
        def ask():
            return self._omt(
                "POST",
                f"api/maps/universal/{'reinstall' if reinstall else 'update'}",
                # TRANSLATORS: name of a server request (looking for map updates), shown before an
                # error
                _("Map check"),
                json={
                    "garminDeviceXml": device_xml.decode("utf-8"),
                    "options": {"groupingMode": 2, "extractionMode": 2},
                },
            )

        return answers.ANSWERS.ask(
            answers.UNIVERSAL_MAPS,
            (*self._omt_scope(), answers.xml_key(device_xml), reinstall), ask,
            about=answers.device_tags(device_xml), fresh=fresh)

    def activate_maps(self, device_xml: bytes, identifiers: list[dict]) -> dict:
        """Never kept (the answer holds the unlock data); what was kept of the device's maps
        is forgotten, as the activation may change it."""
        try:
            return self._omt(
                "POST",
                "api/maps/universal/activate",
                # TRANSLATORS: name of a server request (activating the maps installed on the
                # device), shown before an error
                _("Map activation"),
                json={"garminDeviceXml": device_xml.decode("utf-8"),
                      "installedMaps": identifiers},
            )
        finally:
            answers.ANSWERS.forget(answers.UNIVERSAL_MAPS,
                                   about=answers.xml_key(device_xml))

    # --- the account's devices --------------------------------------------------------

    def device_image_url(self, unit_id: int) -> str | None:
        """Product picture of the watch, from the device registered in Garmin Connect."""
        self.require_verified(unit_id)
        return self._device_info_image(unit_id)

    def device_image_url_off_account(self, unit_id: int) -> str | None:
        """The product picture Garmin Connect gives for a device that is not on the account,
        asked only when the user asks for it on the device page ("Get Picture From
        Account"): the request names the device's unit ID to the account, which nothing the
        program does on its own may do."""
        return self._device_info_image(unit_id)

    def _device_info_image(self, unit_id: int) -> str | None:
        what = _("Device info")
        r = self._connect("GET", f"device-service/deviceservice/device-info/{unit_id}", what)
        body = lower_keys(self._json(r, what))
        return (body.get("imageurl") if isinstance(body, dict) else None) or None

    def account_scope(self) -> tuple:
        """What an answer about the account depends on besides the question: the environment
        and the login saved (its version, nothing of what is in it)."""
        return self.settings.environment, store.stamp()

    def account_devices(self, fresh: bool = False) -> list[dict]:
        """Devices registered to the logged-in account; each entry carries `unitid`. Kept for
        a while (answers); `fresh`: asked anew (the ownership check before a write)."""
        def ask():
            # TRANSLATORS: name of a server request (the devices on the Garmin account), shown
            # before an error
            what = _("Account device list")
            r = self._connect("GET", "device-service/deviceregistration/devices", what)
            body = lower_keys(self._json(r, what))
            if not isinstance(body, list) or not all(isinstance(d, dict) for d in body):
                raise ApiError(_("{what}: unexpected response from the server")
                               .format(what=what))
            return body

        return answers.ANSWERS.ask(answers.ACCOUNT_DEVICES, self.account_scope(), ask,
                                   fresh=fresh)

    # --- consents and account settings that decide whether data may be uploaded ---------

    def consent(self, consent_type: str) -> dict | None:
        """The state of one consent of the account (e.g. DI_CONNECT_UPLOAD); None when the
        server has nothing to say about it. Read-only."""
        self.require_verified()
        # TRANSLATORS: name of a server request (the account's consent to uploads to Garmin
        # Connect), shown before an error
        what = _("Upload consent")
        r = self._connect("GET", f"gdprconsent-service/feature/consent/{consent_type}", what)
        if r.status_code == 204 or not r.content.strip():
            return None
        body = lower_keys(self._json(r, what))
        if not isinstance(body, dict):
            raise ApiError(_("{what}: unexpected response from the server").format(what=what))
        return body

    def device_sync_preference(self) -> dict | None:
        """The account's "device sync" preference: {"key": ..., "value": "true"|"false"}.
        Read-only."""
        self.require_verified()
        # TRANSLATORS: name of a server request (the account's setting for syncing devices), shown
        # before an error
        what = _("Device sync setting")
        r = self._connect("GET", "userpreference-service/account.deviceSync", what)
        if r.status_code == 204 or not r.content.strip():
            return None
        body = lower_keys(self._json(r, what))
        if not isinstance(body, dict):
            raise ApiError(_("{what}: unexpected response from the server").format(what=what))
        return body

    def profile_country(self, fresh: bool = False) -> str | None:
        """The country code of the account's location, when the profile has one. Read-only.
        Kept for a while (answers.LIFETIMES); `fresh`: asked anew (whether data may be
        uploaded)."""
        self.require_verified()

        def ask():
            # TRANSLATORS: name of a server request (the country set on the account), shown before
            # an error
            what = _("Account location")
            r = self._connect("GET", "userprofile-service/userprofile/location", what)
            if r.status_code == 204 or not r.content.strip():
                return None
            body = lower_keys(self._json(r, what))
            code = body.get("countrycode") if isinstance(body, dict) else None
            return code.upper() if isinstance(code, str) and code.strip() else None

        return answers.ANSWERS.ask(answers.ACCOUNT_COUNTRY, self.account_scope(), ask,
                                   fresh=fresh)

    def primary_training_device(self, fresh: bool = False) -> dict:
        """The account's primary training device setting: {"deviceid": unit ID}; {} if unset.

        Read-only. The request names no device, so any verified device is enough. Kept for a
        while (answers.LIFETIMES); `fresh`: asked anew (before and after a change of it).
        """
        self.require_verified()

        def ask():
            what = pgettext("server request", "Primary training device")
            r = self._connect("GET", PRIMARY_TRAINING_PATH, what,
                              params={"ignoreCache": "false"})
            if r.status_code == 204 or not r.content.strip():
                return {}
            body = lower_keys(self._json(r, what))
            if body is None:
                return {}
            if not isinstance(body, dict):
                raise ApiError(_("{what}: unexpected response from the server")
                               .format(what=what))
            return body

        return answers.ANSWERS.ask(answers.PRIMARY_TRAINING, self.account_scope(), ask,
                                   fresh=fresh)

    def set_primary_training_device(self, unit_id: int) -> None:
        """Make this device the account's primary training device. Changes the account.

        Any 2xx answer counts as accepted; callers read the setting back to confirm.
        """
        self.require_verified(unit_id)
        self._connect("PUT", PRIMARY_TRAINING_PATH,
                      pgettext("server request", "Primary training device"),
                      data=json.dumps({"deviceId": unit_id}),
                      headers={"Content-Type": "application/json"})

    def set_primary_wearable(self, unit_id: int) -> None:
        """Make this device the account's primary wearable (Garmin Connect's "primary activity
        tracker": its steps and sleep count). Changes the account. Any 2xx answer counts as
        accepted; callers read the account's device list back to confirm."""
        self.require_verified(unit_id)
        path = f"device-service/deviceservice/device-info/{unit_id}/primary-activity-tracker"
        self._connect("PUT", path, _("Primary wearable"), data=json.dumps(True),
                      headers={"Content-Type": "application/json"})

    # --- adding a device to the account (experimental) -----------------------------------

    def register_device(self, device_xml: bytes, unit_id: int) -> Registration:
        """Add the device to the logged-in account, as an active device (`Override: 1`, as
        Garmin Express adds a device that tracks activity). Changes the account.

        The one account request for a device the account does not list yet, so it needs no
        verified unit ID; it does not verify the device either. One request, never repeated:
        409 (another account's device, with or without its subscription), 400 and 403 are
        refusals. Any 2xx answer means the device was accepted, also one that cannot be read
        (then `Registration(None, "")`); the account's device list, fetched again, tells the
        result. Nothing of the answer but `Registration` is kept: not its body, not even in an
        error."""
        what = _("Adding the device to the account")
        url = self._prepared_url("POST", self.connect_url(REGISTER_PATH))
        h = self._client_headers()
        h.update({"Content-Type": "application/xml", "Accept": "application/json",
                  "Override": "1"})
        h["Authorization"] = self._sign("POST", url)
        resp = self._request("POST", url, what, headers=h, data=device_xml)
        if resp.status_code == 409:
            raise OtherOwner(*self._other_owner(resp))
        if resp.status_code == 400:
            raise RegistrationRefused(
                _("Garmin did not accept the description of this device. Nothing was "
                  "changed."), 400)
        if resp.status_code == 403:
            raise self._clock_skew(resp, what) or RegistrationRefused(
                _("Garmin does not allow adding this device to an account. Nothing was "
                  "changed."), 403)
        if not resp.ok:
            # The usual errors, but without the body: an answer to this request may carry
            # the device's credentials.
            try:
                self._check(resp, what)
            except ApiError as e:
                e.body = ""
                raise
        self.registered_unit_ids.add(unit_id)
        try:
            body = lower_keys(resp.json())
        except ValueError:
            body = None
        if not isinstance(body, dict):
            log.info("register: accepted, the answer not understood (%s, %s)",
                     resp.headers.get("Content-Type") or "no content type",
                     applog.size(len(resp.content)))
            return Registration(None, "")
        told = body.get("unitid")
        if told is not None and str(told) != str(unit_id):
            raise ApiError(
                _("Garmin accepted the request, but its answer names another device. Check "
                  "the devices of your account in Garmin Connect."), resp.status_code)
        new = body.get("newregistration")
        status = body.get("devicestatus")
        return Registration(new if isinstance(new, bool) else None,
                            status if isinstance(status, str) else "")

    @staticmethod
    def _other_owner(resp: requests.Response) -> tuple[str, bool]:
        """The words of a 409 of the registration, and whether it is a subscription's."""
        try:
            error = lower_keys(resp.json()).get("error")
            subscription = isinstance(error, dict) and error.get("subcode") == SUBSCRIPTION_SUBCODE
        except (ValueError, AttributeError):
            subscription = False
        if subscription:
            return _("Another Garmin account has an active subscription on this device; its "
                     "owner has to end it first. Nothing was changed."), True
        return _("This device is registered to another Garmin account. If it is your other "
                 "account, sign in to it in openExpress. If it was someone else's, they have to "
                 "remove it from their account in Garmin Connect; then add it here. Nothing "
                 "was changed."), False

    def set_device_active(self, unit_id: int) -> None:
        """Make a device this client has just added to the account active, as Garmin Express
        does with one Garmin keeps as retired. Changes the account. Any 2xx answer counts as
        accepted; the account's device list, fetched again, tells the result."""
        if unit_id not in self.registered_unit_ids:
            raise DeviceNotVerified(
                _("Internal error: this device was not checked against your Garmin account. "
                  "Nothing was transferred."))
        what = _("Making the device active")
        try:
            # The path names the device: the log has only the host.
            self._connect("PUT", f"{REGISTER_PATH}/status/{unit_id}", what,
                          data=json.dumps({"deviceStatus": "active"}),
                          headers={"Content-Type": "application/json"}, quiet=True)
        except ApiError as e:
            e.body = ""
            raise

    def device_messages(self, unit_id: int) -> list[dict]:
        self.require_verified(unit_id)
        # TRANSLATORS: name of a server request (files Garmin Connect has queued for the device),
        # shown before an error
        what = _("Connect file queue")
        r = self._connect(
            "GET",
            "device-service/devicemessage/messages",
            what,
            params={"device_id": str(unit_id)},
        )
        body = lower_keys(self._json(r, what))
        if not isinstance(body, dict):
            raise ApiError(_("{what}: unexpected response from the server").format(what=what))
        return body.get("messages") or []

    def download_message(self, message_url: str, absolute: bool) -> bytes | None:
        """Download a file from the queue. None means no content (404/204): just acknowledge it."""
        self.require_verified()
        url = message_url if absolute else self.connect_url(message_url)
        url = self._prepared_url("GET", url)
        what = _("Downloading file from Connect")
        h = self._client_headers()
        h["Accept"] = "application/octet-stream, application/json, */*"
        h["Authorization"] = self._sign("GET", url)
        resp = self._request("GET", url, what, headers=h)
        if resp.status_code in (204, 404):
            return None
        self._check(resp, what)
        return resp.content

    def ack_message(self, message_id: int | str) -> None:
        self.require_verified()
        self._connect(
            "POST",
            f"device-service/devicemessage/message/{message_id}",
            # TRANSLATORS: name of a server request (telling Garmin Connect that a queued file was
            # received), shown before an error
            _("Receipt acknowledgement"),
            params={"status": "received"},
            headers={"X-HTTP-Method-Override": "PUT"},
        )


def acknowledge(client: GarminClient, message_id) -> None:
    """Tell Garmin Connect a message of the queue is received. A message it no longer has (404:
    the phone, Garmin Express or the other install of this computer took it) counts as done."""
    try:
        client.ack_message(message_id)
    except ApiError as e:
        if e.status != 404:
            raise
        log.info("queue: a message acknowledged already (HTTP 404), done")
