"""Which addresses of device pictures openExpress downloads: Garmin's own hosts, over HTTPS.
The one rule for every picture of a device model, wherever its address comes from (the account,
Garmin's update server).

Amazon S3 is allowed only for a bucket named in S3_BUCKETS, by the first segment of the path
(s3.amazonaws.com/<bucket>/...) or by the host (<bucket>.s3[.<region>].amazonaws.com). A
refusal says why in words for the log, with the host and at most that first segment: never
the rest of the address, nor its query (a signed address carries a token there)."""

from __future__ import annotations

import re
from urllib.parse import urlsplit

HOSTS = frozenset({"res.garmin.com", "static.garmincdn.com"})
# Garmin's buckets of device pictures on S3, by name (S3 names a bucket in lower case only).
# garmin-connect-prod: the account's pictures of some models. Any other bucket is refused,
# and the log names it, so that a real one can be added here.
S3_BUCKETS: frozenset[str] = frozenset({"garmin-connect-prod"})

_S3_PATH_HOST = re.compile(r"s3(?:[.-][a-z0-9-]+)?\.amazonaws\.com")
_S3_BUCKET_HOST = re.compile(r"(?P<bucket>[a-z0-9][a-z0-9.-]*?)\.s3(?:[.-][a-z0-9-]+)?"
                             r"\.amazonaws\.com")


def upgraded(url):
    """The address as it is asked: plain HTTP over HTTPS instead, as every download of the
    program (download.secure_url); any other address as it is, for refusal() to judge. Used
    before the rule wherever an address is downloaded or kept."""
    if isinstance(url, str) and url[:7].lower() == "http://":
        return "https://" + url[7:]
    return url


def refusal(url) -> str | None:
    """None when this address of a device picture may be downloaded; else why not. Plain
    HTTP is refused here: it is upgraded() before."""
    if not isinstance(url, str) or not url:
        return "no address"
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return "not an address"
    if parts.scheme.lower() != "https":
        return "not HTTPS"
    host = (parts.hostname or "").lower()
    if not host:
        return "not an address"
    if parts.username is not None or parts.password is not None:
        return f"host {host}: a user name in the address"
    if port not in (None, 443):
        return f"host {host}: port {port}"
    if host in HOSTS:
        return None
    if _S3_PATH_HOST.fullmatch(host):
        bucket = parts.path.lstrip("/").split("/", 1)[0]
        if bucket and bucket in S3_BUCKETS:
            return None
        return f"host {host}, first segment {bucket or '-'}: not an allowed bucket"
    hosted = _S3_BUCKET_HOST.fullmatch(host)
    if hosted is not None:
        if hosted.group("bucket") in S3_BUCKETS:
            return None
        return f"host {host}: not an allowed bucket"
    return f"host {host} is not allowed"


def query_said(url) -> str | None:
    """For the log, next to the host: whether the address has a query ("with query" / "no
    query"), never what it is (a signed address carries its token there). None: no
    address."""
    if not isinstance(url, str) or not url:
        return None
    try:
        query = urlsplit(url).query
    except ValueError:
        return None
    return "with query" if query else "no query"


def allowed(url) -> bool:
    """Whether this address of a device picture may be downloaded (and remembered), as it
    is: upgraded() first for an address from Garmin."""
    return refusal(url) is None
