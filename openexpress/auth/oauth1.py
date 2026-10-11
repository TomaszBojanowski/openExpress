"""OAuth 1.0a signing (HMAC-SHA1, RFC 5849) for Garmin Connect requests."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from urllib.parse import parse_qsl, quote, urlsplit, urlunsplit


def _enc(s: str) -> str:
    return quote(s, safe="~-._")


def _base_url(url: str) -> str:
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    host = parts.hostname.lower() if parts.hostname else ""
    port = parts.port
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        host = f"{host}:{port}"
    return urlunsplit((scheme, host, parts.path or "/", "", ""))


def authorization_header(
    method: str,
    url: str,
    consumer_key: str,
    consumer_secret: str,
    token: str | None = None,
    token_secret: str | None = None,
    form_params: list[tuple[str, str]] | None = None,
    nonce: str | None = None,
    timestamp: int | None = None,
) -> str:
    """Return the Authorization header value.

    The signature covers query string parameters and the
    application/x-www-form-urlencoded body (`form_params`).
    """
    oauth = {
        "oauth_consumer_key": consumer_key,
        "oauth_nonce": nonce or secrets.token_hex(16),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(timestamp if timestamp is not None else int(time.time())),
        "oauth_version": "1.0",
    }
    if token:
        oauth["oauth_token"] = token

    params = list(parse_qsl(urlsplit(url).query, keep_blank_values=True))
    params += list(form_params or [])
    params += list(oauth.items())
    normalized = "&".join(
        f"{k}={v}" for k, v in sorted((_enc(k), _enc(v)) for k, v in params)
    )
    base = "&".join([method.upper(), _enc(_base_url(url)), _enc(normalized)])
    key = f"{_enc(consumer_secret)}&{_enc(token_secret or '')}"
    sig = base64.b64encode(hmac.new(key.encode(), base.encode(), hashlib.sha1).digest()).decode()
    oauth["oauth_signature"] = sig
    return "OAuth " + ", ".join(f'{_enc(k)}="{_enc(v)}"' for k, v in sorted(oauth.items()))
