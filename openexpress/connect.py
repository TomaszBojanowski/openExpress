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
from openexpress.auth.oauth1 import authorization_header
from openexpress.auth.store import ConnectAuth
from openexpress.errors import UserFacingError
from openexpress.i18n import _
from openexpress.settings import UPLOAD_CONFIG_CLIENT_ID, Consumer, Settings, language_tag
from openexpress.useragent import USER_AGENT
from openexpress.useragent import session as user_agent_session

log = logging.getLogger(__name__)

TIMEOUT = 60
PRIMARY_TRAINING_PATH = "userprofile-service/userprofile/user-settings/primary-training-device"


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


def lower_keys(obj: Any) -> Any:
    """Garmin servers return either camelCase or PascalCase; normalize the keys."""
    if isinstance(obj, dict):
        return {str(k).lower(): lower_keys(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [lower_keys(v) for v in obj]
    return obj


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
            raise ApiError(_("OAuth1 consumer key is missing (openexpress setup)."))
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

    def _check(self, resp: requests.Response, what: str) -> requests.Response:
        if resp.status_code in (401, 403):
            skew = netstatus.clock_skew(resp)
            if netstatus.kind_of(resp.status_code, skew) == netstatus.CLOCK:
                raise ClockSkew(
                    _("{action}: computer clock is off from the server by {minutes} min")
                    .format(action=what, minutes=int(skew // 60)),
                    resp.status_code,
                )
            raise Unauthorized(
                _("{action}: unauthorized (HTTP {status}). Please log in again.")
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

    def poll_location(
        self, url: str, delay_ms: int | None, timeout_s: float = 30, base: str | None = None
    ) -> int:
        """Wait for the activity to be processed (201 Created). Returns the last HTTP status.

        0 means the status could not be checked (network error). A relative `url` is
        resolved against `base` (the upload URL).
        """
        self.require_verified()
        if base:
            url = urljoin(base, url)
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
        """What Garmin's update server knows about the device; needs no login. The answer
        carries the unit ID and the serial number: never log or store it."""
        return self._omt(
            "GET",
            f"api/unit-info/manufacturers/Taiwan/unitids/{unit_id}",
            _("Device info"),
            quiet=True,  # the path names the device
            params={"primaryFirmwarePartNumber": part_number},
        )

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

    def account_devices(self) -> list[dict]:
        """Devices registered to the logged-in account; each entry carries `unitid`."""
        what = _("Account device list")
        r = self._connect("GET", "device-service/deviceregistration/devices", what)
        body = lower_keys(self._json(r, what))
        if not isinstance(body, list) or not all(isinstance(d, dict) for d in body):
            raise ApiError(_("{what}: unexpected response from the server").format(what=what))
        return body

    # --- consents and account settings that decide whether data may be uploaded ---------

    def consent(self, consent_type: str) -> dict | None:
        """The state of one consent of the account (e.g. DI_CONNECT_UPLOAD); None when the
        server has nothing to say about it. Read-only."""
        self.require_verified()
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
        what = _("Device sync setting")
        r = self._connect("GET", "userpreference-service/account.deviceSync", what)
        if r.status_code == 204 or not r.content.strip():
            return None
        body = lower_keys(self._json(r, what))
        if not isinstance(body, dict):
            raise ApiError(_("{what}: unexpected response from the server").format(what=what))
        return body

    def profile_country(self) -> str | None:
        """The country code of the account's location, when the profile has one. Read-only."""
        self.require_verified()
        what = _("Account location")
        r = self._connect("GET", "userprofile-service/userprofile/location", what)
        if r.status_code == 204 or not r.content.strip():
            return None
        body = lower_keys(self._json(r, what))
        code = body.get("countrycode") if isinstance(body, dict) else None
        return code.upper() if isinstance(code, str) and code.strip() else None

    def primary_training_device(self) -> dict:
        """The account's primary training device setting: {"deviceid": unit ID}; {} if unset.

        Read-only. The request names no device, so any verified device is enough.
        """
        self.require_verified()
        what = _("Primary training device")
        r = self._connect("GET", PRIMARY_TRAINING_PATH, what, params={"ignoreCache": "false"})
        if r.status_code == 204 or not r.content.strip():
            return {}
        body = lower_keys(self._json(r, what))
        if body is None:
            return {}
        if not isinstance(body, dict):
            raise ApiError(_("{what}: unexpected response from the server").format(what=what))
        return body

    def set_primary_training_device(self, unit_id: int) -> None:
        """Make this device the account's primary training device. Changes the account.

        Any 2xx answer counts as accepted; callers read the setting back to confirm.
        """
        self.require_verified(unit_id)
        self._connect("PUT", PRIMARY_TRAINING_PATH, _("Primary training device"),
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

    def device_messages(self, unit_id: int) -> list[dict]:
        self.require_verified(unit_id)
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
            _("Receipt acknowledgement"),
            params={"status": "received"},
            headers={"X-HTTP-Method-Override": "PUT"},
        )
