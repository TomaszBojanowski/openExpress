"""Downloading files (firmware, maps) to the local cache with size and MD5 checks."""

from __future__ import annotations

import base64
import binascii
import hashlib
import logging
import struct
import time
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from typing import Callable

import requests

from openexpress import __version__
from openexpress.i18n import _

log = logging.getLogger(__name__)

CHUNK = 1024 * 1024


class DownloadError(Exception):
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


def fetch(
    urls: list[str],
    dest: Path,
    size: int | None,
    md5: str | None,
    session: requests.Session | None = None,
    progress: Callable[[int, int | None], None] | None = None,
) -> Path:
    """Download a file to `dest` (with resume), trying each URL in turn.

    A finished file with the correct size and MD5 is not downloaded again.
    """
    md5 = normalize_md5(md5) if md5 else None
    if _valid(dest, size, md5):
        return dest
    http = session or requests.Session()
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    errors = []
    for url in urls:
        for attempt in range(2):
            try:
                _get(http, url, part, size, progress)
            except (requests.RequestException, DownloadError, OSError) as e:
                errors.append(f"{_short(url)}: {e}")
                log.warning("Download failed (%s)", e)
                if isinstance(e, DownloadError) and e.status and 400 <= e.status < 500:
                    break  # retrying the same request will not help
                continue
            got = part.stat().st_size
            if size is not None and got != size:
                errors.append(_("{url}: size {got} instead of {size}")
                              .format(url=url, got=got, size=size))
                part.unlink()
                continue
            if md5 is not None and file_md5(part) != md5:
                errors.append(_("{url}: MD5 checksum mismatch").format(url=url))
                part.unlink()
                continue
            part.replace(dest)
            return dest
    raise DownloadError(_("Could not download the file:") + "\n  " + "\n  ".join(errors))


def _short(url: str) -> str:
    return url.split("?")[0]


def _get(http, url, part: Path, size, progress) -> None:
    have = part.stat().st_size if part.exists() else 0
    if size is not None and have >= size:
        have = 0
        part.unlink()
    headers = {"Accept": "*/*", "User-Agent": f"openexpress/{__version__}"}
    if have:
        headers["Range"] = f"bytes={have}-"
    with http.get(url, headers=headers, stream=True, timeout=(30, 300)) as resp:
        if resp.status_code == 206:
            mode = "ab"
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
