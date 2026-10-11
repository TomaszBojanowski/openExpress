"""HTTP client for Garmin services (Connect API, OMT).

The scope and semantics of the calls follow docs/analiza/01 and 03.
"""

from __future__ import annotations

import logging
import platform
import time
import uuid
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin

import requests

from openexpress import __version__
from openexpress.auth.oauth1 import authorization_header
from openexpress.auth.store import ConnectAuth
from openexpress.i18n import _
from openexpress.settings import UPLOAD_CONFIG_CLIENT_ID, Consumer, Settings

log = logging.getLogger(__name__)

TIMEOUT = 60
MAX_CLOCK_SKEW_S = 59 * 60


class ApiError(Exception):
    def __init__(self, message: str, status: int | None = None, body: str = ""):
        super().__init__(message)
        self.status = status
        self.body = body


class Unauthorized(ApiError):
    pass


class ClockSkew(ApiError):
    pass


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
        self.http = session or requests.Session()
        self.session_id = str(uuid.uuid4())

    # --- helpers ---------------------------------------------------------------------

    def _client_headers(self) -> dict[str, str]:
        s = self.settings
        return {
            "Garmin-Client-Name": s.client_name,
            "Garmin-Client-Version": s.client_version,
            "Garmin-Client-Platform": s.client_platform,
            "Garmin-Client-Platform-Version": s.client_platform_version,
            "Garmin-Client-SessionId": self.session_id,
            "X-garmin-client-id": "EXPRESS",
            "User-Agent": f"openexpress/{__version__} ({platform.system()})",
        }

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

    def _check(self, resp: requests.Response, what: str) -> requests.Response:
        if resp.status_code in (401, 403):
            date = resp.headers.get("Date")
            if date:
                try:
                    skew = abs(parsedate_to_datetime(date).timestamp() - time.time())
                except (TypeError, ValueError):
                    skew = 0
                if skew > MAX_CLOCK_SKEW_S:
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
        h = self._client_headers()
        h.update(headers or {})
        h["Authorization"] = self._sign(method, url, form)
        data = urlencode(form) if form is not None else kw.pop("data", None)
        if form is not None:
            h["Content-Type"] = "application/x-www-form-urlencoded"
        resp = self.http.request(method, url, headers=h, data=data, timeout=TIMEOUT, **kw)
        return self._check(resp, what)

    # --- authentication -------------------------------------------------------------

    def exchange_ticket(self, ticket: str, service: str) -> tuple[str, str]:
        """Exchange an SSO ticket for a Garmin Connect OAuth1 token."""
        url = self.connect_url("oauth-service/oauth/preauthorized") + "?" + urlencode(
            {"ticket": ticket, "login-url": service}
        )
        h = self._client_headers()
        h["Authorization"] = self._sign("GET", url, with_token=False)
        resp = self._check(self.http.get(url, headers=h, timeout=TIMEOUT), _("Ticket exchange"))
        data = dict(parse_qsl(resp.text))
        if "oauth_token" not in data:
            raise ApiError(_("Ticket exchange: no oauth_token in the response"), resp.status_code)
        return data["oauth_token"], data["oauth_token_secret"]

    def user_profile(self) -> dict:
        r = self._connect("GET", "userprofile-service/userprofile/userProfileBase",
                          # TRANSLATORS: name of a server request, shown before an error
                          _("Profile"))
        return lower_keys(r.json())

    # --- upload ---------------------------------------------------------------------

    def upload_settings(self, unit_id: int) -> dict:
        """Upload configuration for the device (OMT, no login needed)."""
        url = urljoin(
            self.settings.hosts["omt"],
            f"UploadConfigurationService/UnitUploadSettings/{unit_id}",
        )
        h = self._client_headers()
        h["Garmin-Client-LocaleCode"] = self.settings.locale
        h["Accept-Language"] = self.settings.locale.replace("_", "-")
        h["Garmin-Client-Guid"] = self.settings.client_guid
        h["Accept"] = "application/json"
        resp = self.http.get(
            url,
            params={"clientId": UPLOAD_CONFIG_CLIENT_ID, "enforceConsent": "False"},
            headers=h,
            timeout=TIMEOUT,
        )
        return lower_keys(self._check(resp, _("Upload configuration")).json())

    def activity_matches(self, unit_id: int, external_ids: list[str]) -> dict[str, bool]:
        """Which activities (by time_created) Connect already has. Returns {id: in_connect}."""
        if not external_ids:
            return {}
        ids = sorted(external_ids)
        pairs = [("externalIds", i) for i in ids]
        params = [("deviceId", str(unit_id))] + pairs
        r = self._connect(
            "POST",
            "activity-search-service-1.2/json/matchesByDeviceId",
            _("Duplicate check"),
            params=params,
            form=pairs,
        )
        matches = lower_keys(r.json()).get("matches") or {}
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
        headers: dict[str, str] = {}
        files = None
        body: bytes | None = data
        if is_zip:
            files = {"data": ("", data, "application/zip")}
            body = None
        else:
            headers["Content-Type"] = "application/octet-stream"
        if last_file:
            headers["lastFile"] = "true"
        if authenticated:
            headers["Authorization"] = self._sign("POST", url)
        resp = self.http.post(url, data=body, files=files, headers=headers, timeout=TIMEOUT)
        if resp.status_code in (401, 403):
            # TRANSLATORS: name of a server request (file upload), shown before an error
            self._check(resp, _("Upload"))
        try:
            parsed = lower_keys(resp.json())
        except ValueError:
            parsed = resp.text[:500]
        delay = resp.headers.get("location-in-milliseconds")
        return UploadResult(
            resp.status_code,
            parsed,
            resp.headers.get("Location"),
            int(delay) if delay and delay.isdigit() else None,
        )

    def poll_location(self, url: str, delay_ms: int | None, timeout_s: float = 30) -> int:
        """Wait for the activity to be processed (201 Created). Returns the last HTTP status."""
        deadline = time.monotonic() + timeout_s
        delay = (delay_ms or 1000) / 1000
        status = 0
        while time.monotonic() < deadline:
            time.sleep(delay)
            h = {"Authorization": self._sign("GET", url)}
            resp = self.http.get(url, headers=h, timeout=TIMEOUT)
            status = resp.status_code
            if status != 202:
                return status
            d = resp.headers.get("location-in-milliseconds")
            if d and d.isdigit():
                delay = int(d) / 1000
        return status

    # --- OMT: device info, software, maps (no login needed) --------------------------

    def _omt(self, method: str, path: str, what: str, **kw) -> Any:
        url = urljoin(self.settings.hosts["omt"], path)
        h = self._client_headers()
        h["Garmin-Client-LocaleCode"] = self.settings.locale.replace("_", "-")
        h["Accept-Language"] = self.settings.locale.replace("_", "-")
        h["Accept"] = "application/json"
        resp = self.http.request(method, url, headers=h, timeout=TIMEOUT, **kw)
        self._check(resp, what)
        return lower_keys(resp.json()) if resp.content else {}

    def unit_info(self, unit_id: int, part_number: str) -> dict:
        return self._omt(
            "GET",
            f"api/unit-info/manufacturers/Taiwan/unitids/{unit_id}",
            _("Device info"),
            params={"primaryFirmwarePartNumber": part_number},
        )

    def software_updates(self, device_xml: bytes, include_reinstalls: bool = False) -> dict:
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

    def universal_maps(self, device_xml: bytes, reinstall: bool = False) -> dict:
        return self._omt(
            "POST",
            f"api/maps/universal/{'reinstall' if reinstall else 'update'}",
            _("Map check"),
            json={
                "garminDeviceXml": device_xml.decode("utf-8"),
                "options": {"groupingMode": 2, "extractionMode": 2},
            },
        )

    def activate_maps(self, device_xml: bytes, identifiers: list[dict]) -> dict:
        return self._omt(
            "POST",
            "api/maps/universal/activate",
            _("Map activation"),
            json={"garminDeviceXml": device_xml.decode("utf-8"), "installedMaps": identifiers},
        )

    # --- download (Connect queue -> device) -------------------------------------------

    def device_image_url(self, unit_id: int) -> str | None:
        """Product picture of the watch, from the device registered in Garmin Connect."""
        r = self._connect("GET", f"device-service/deviceservice/device-info/{unit_id}",
                          "Device info")
        return lower_keys(r.json()).get("imageurl") or None

    def device_messages(self, unit_id: int) -> list[dict]:
        r = self._connect(
            "GET",
            "device-service/devicemessage/messages",
            _("Connect file queue"),
            params={"device_id": str(unit_id)},
        )
        return lower_keys(r.json()).get("messages") or []

    def download_message(self, message_url: str, absolute: bool) -> bytes | None:
        """Download a file from the queue. None means no content (404/204): just acknowledge it."""
        url = message_url if absolute else self.connect_url(message_url)
        h = self._client_headers()
        h["Accept"] = "application/octet-stream, application/json, */*"
        h["Authorization"] = self._sign("GET", url)
        resp = self.http.get(url, headers=h, timeout=TIMEOUT)
        if resp.status_code in (204, 404):
            return None
        self._check(resp, _("Downloading file from Connect"))
        return resp.content

    def ack_message(self, message_id: int | str) -> None:
        self._connect(
            "POST",
            f"device-service/devicemessage/message/{message_id}",
            _("Receipt acknowledgement"),
            params={"status": "received"},
            headers={"X-HTTP-Method-Override": "PUT"},
        )
