"""Downloading files (firmware, maps) to the local cache with size and MD5 checks."""

from __future__ import annotations

import base64
import binascii
import hashlib
import logging
import re
import struct
import time
from collections.abc import Callable
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests

from openexpress import applog, netstatus
from openexpress.errors import UserFacingError
from openexpress.i18n import _
from openexpress.useragent import USER_AGENT
from openexpress.useragent import session as user_agent_session

log = logging.getLogger(__name__)

CHUNK = 1024 * 1024


class DownloadError(UserFacingError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


TOKEN_PARAM = "garmindlm"
TOKEN_WINDOW_S = 86400


def tokenize(url: str, salt: str, now: int | None = None) -> str:
    """Sign a download URL the way Garmin Express does (garmindlm parameter).

    The map server requires a signature: <expiry>_<md5(salt + md5(expiry LE32 + path + salt))>.
    """
    expires = (int(time.time()) if now is None else now) + TOKEN_WINDOW_S
    parts = urlsplit(url)
    inner = hashlib.md5(struct.pack("<I", expires & 0xFFFFFFFF)
                        + (parts.path + salt).encode("ascii")).digest()
    token = hashlib.md5(salt.encode("ascii") + inner).hexdigest()
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if k != TOKEN_PARAM]
    query.append((TOKEN_PARAM, f"{expires}_{token}"))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))


def secure_url(url: str) -> str:
    """The address to ask: HTTPS as it is; plain HTTP over HTTPS instead (Garmin lists its
    fallback map hosts so, the same hosts it gives over HTTPS); anything else refused."""
    parts = urlsplit(url)
    if parts.scheme == "https":
        return url
    if parts.scheme == "http":
        return urlunsplit(("https", *parts[1:]))
    raise DownloadError(_("Refusing to download {url}: files are downloaded only over HTTPS")
                        .format(url=_short(url)))


def normalize_md5(value: str | None) -> str | None:
    """Garmin gives MD5 as hex (firmware) or base64 (maps). Returns hex."""
    if not value:
        return None
    v = value.strip()
    if len(v) == 32 and all(c in "0123456789abcdefABCDEF" for c in v):
        return v.lower()
    try:
        raw = base64.b64decode(v, validate=True)
    except (binascii.Error, ValueError):
        return None
    return raw.hex() if len(raw) == 16 else None


def file_md5(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def _valid(path: Path, size: int | None, md5: str | None) -> bool:
    if not path.exists():
        return False
    if size is not None and path.stat().st_size != size:
        return False
    return md5 is None or file_md5(path) == md5


class Overall:
    """The progress of several downloads told as one: `report(done, total)` counts the bytes
    of the files before the current one too. `total` is the sum of the announced sizes."""

    def __init__(self, total: int | None, report: Callable[[int, int | None], None]):
        self.total, self.report, self.done = total or None, report, 0

    def file(self) -> Callable[[int, int | None], None]:
        """The progress of the next file, for `fetch`."""
        start = self.done
        return lambda done, _size: self.report(start + done, self.total)

    def finished(self, size: int | None) -> None:
        """The file is there (also when it came from the cache)."""
        self.done += size or 0
        self.report(self.done, self.total)


def fetch(
    urls: list[str],
    dest: Path,
    size: int | None,
    md5: str | None,
    session: requests.Session | None = None,
    progress: Callable[[int, int | None], None] | None = None,
) -> Path:
    """Download a file to `dest` (with resume), trying each URL in turn, over HTTPS only.

    A finished file with the correct size and MD5 is not downloaded again. Without an MD5
    nothing is downloaded.
    """
    if not md5:
        raise DownloadError(_("The server gave no MD5 checksum for this file, so it was not "
                              "downloaded"))
    given, md5 = md5, normalize_md5(md5)
    if md5 is None:
        raise DownloadError(_("Invalid MD5 checksum from the server: {value}")
                            .format(value=given))
    # Every address checked before any is asked; one asked twice (HTTP and HTTPS) once.
    urls = list(dict.fromkeys(secure_url(u) for u in urls))
    if not size:
        size = None  # 0 or missing: unknown
    if _valid(dest, size, md5):
        log.info("download: already in the cache (%s)", applog.size(size))
        return dest
    http = session or user_agent_session()
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    errors = []
    for url in urls:
        for _attempt in range(2):
            try:
                _get(http, url, part, size, progress)
            except (requests.RequestException, DownloadError, OSError) as e:
                if isinstance(e, requests.RequestException):
                    netstatus.record(netstatus.OMT, url, None)
                elif isinstance(e, DownloadError) and e.status:
                    # The address is signed with the URL salt, not the sign-in: a refusal
                    # (4xx) is an answer of a working server.
                    netstatus.record(netstatus.OMT, url, e.status if e.status >= 500 else 200)
                # The text of a requests error names the signed address, query and all.
                said = applog.network_error_text(e)
                errors.append(f"{_short(url)}: {said}")
                log.warning("Download failed (%s)", said)
                if isinstance(e, DownloadError) and e.status and 400 <= e.status < 500:
                    break  # retrying the same request will not help
                continue
            netstatus.record(netstatus.OMT, url, 200)
            got = part.stat().st_size
            # The address is signed and names the product: only the host goes to the log.
            log.info("download: %s from %s", applog.size(got), urlsplit(url).netloc)
            if size is not None and got != size:
                errors.append(_("{url}: size {got} instead of {size}")
                              .format(url=_short(url), got=got, size=size))
                part.unlink(missing_ok=True)
                continue
            if md5 is not None and file_md5(part) != md5:
                errors.append(_("{url}: MD5 checksum mismatch").format(url=_short(url)))
                part.unlink(missing_ok=True)
                continue
            part.replace(dest)
            return dest
    raise DownloadError(_("Could not download the file:") + "\n  " + "\n  ".join(errors))


def _short(url: str) -> str:
    return url.split("?")[0]


def _content_range(value: str | None) -> tuple[int | None, int | None]:
    """Parses "bytes START-END/TOTAL" or "bytes */TOTAL"; returns (start, total)."""
    if not value:
        return None, None
    m = re.fullmatch(r"\s*bytes\s+(?:(\d+)-\d+|\*)/(\d+|\*)\s*", value)
    if not m:
        return None, None
    start = int(m.group(1)) if m.group(1) is not None else None
    total = int(m.group(2)) if m.group(2) != "*" else None
    return start, total


def _get(http, url, part: Path, size, progress, retry_416: bool = True) -> None:
    have = part.stat().st_size if part.exists() else 0
    if size and have >= size:
        have = 0
        part.unlink(missing_ok=True)
    headers = {"Accept": "*/*", "User-Agent": USER_AGENT}
    if have:
        headers["Range"] = f"bytes={have}-"
    with http.get(url, headers=headers, stream=True, timeout=(30, 300)) as resp:
        if resp.status_code == 416 and have:
            total = _content_range(resp.headers.get("Content-Range"))[1]
            if total is not None and total == have:
                return  # the .part is already complete
            part.unlink(missing_ok=True)
            if retry_416:
                resp.close()
                _get(http, url, part, size, progress, retry_416=False)
                return
            raise DownloadError(f"HTTP {resp.status_code}", resp.status_code)
        if resp.status_code == 206 and have:
            start = _content_range(resp.headers.get("Content-Range"))[0]
            if start == have:
                mode = "ab"
            elif start == 0:
                mode, have = "wb", 0
            else:  # unexpected or missing range: start over without Range
                resp.close()
                part.unlink(missing_ok=True)
                _get(http, url, part, size, progress, retry_416)
                return
        elif resp.ok:
            mode, have = "wb", 0
        else:
            raise DownloadError(f"HTTP {resp.status_code}", resp.status_code)
        done = have
        with open(part, mode) as f:
            for chunk in resp.iter_content(CHUNK):
                f.write(chunk)
                done += len(chunk)
                if progress:
                    progress(done, size)
