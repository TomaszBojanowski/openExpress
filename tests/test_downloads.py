"""Downloads: resuming, ranges, the MD5 a download needs, HTTPS only."""

import pytest
import requests
from fakes import DOWNLOAD_DATA, md5hex

from openexpress import download

# --- downloads --------------------------------------------------------------------------

def ranges(http_server):
    """The Range header of each request the server got, in order (None: from the start)."""
    return [headers.get("Range") for _command, _path, headers in http_server.requests]


def ranged(ignore_start=False, content_range=True, wrong_start=False):
    def route(h):
        rng = h.headers.get("Range")
        if not rng:
            return 200, {}, DOWNLOAD_DATA
        start = int(rng.split("=")[1].split("-")[0])
        if ignore_start:
            start = 0
        body = DOWNLOAD_DATA[start:]
        hdr = {}
        if content_range:
            shown = start + 7 if wrong_start else start
            hdr["Content-Range"] = f"bytes {shown}-{len(DOWNLOAD_DATA) - 1}/{len(DOWNLOAD_DATA)}"
        return 206, hdr, body
    return route


def test_size_zero_without_part(http_server, tmp_path):
    http_server.routes["/f"] = (200, {}, DOWNLOAD_DATA)
    dest = tmp_path / "f.bin"
    download.fetch([http_server.url + "/f"], dest, 0, md5hex(DOWNLOAD_DATA))
    assert dest.read_bytes() == DOWNLOAD_DATA
    assert ranges(http_server) == [None]  # one request, nothing downloaded twice


# The part kept is resumed with one request from its end; a server that answers it with the
# whole file is taken at its word, one whose answer cannot be placed is asked again from
# the start.
@pytest.mark.parametrize("kw, asked", [
    ({}, ["bytes=1000-"]),
    ({"ignore_start": True}, ["bytes=1000-"]),
    ({"content_range": False}, ["bytes=1000-", None]),
    ({"wrong_start": True}, ["bytes=1000-", None]),
])
def test_resume_checks_content_range(http_server, tmp_path, kw, asked):
    http_server.routes["/f"] = ranged(**kw)
    dest = tmp_path / "f.bin"
    dest.with_name("f.bin.part").write_bytes(DOWNLOAD_DATA[:1000])
    download.fetch([http_server.url + "/f"], dest, None, md5hex(DOWNLOAD_DATA))
    assert dest.read_bytes() == DOWNLOAD_DATA
    assert ranges(http_server) == asked


def test_invalid_md5_is_an_error(http_server, tmp_path):
    http_server.routes["/f"] = (200, {}, DOWNLOAD_DATA)
    with pytest.raises(download.DownloadError):
        download.fetch([http_server.url + "/f"], tmp_path / "f.bin", None, "garbage")


def test_416_with_complete_part(http_server, tmp_path):
    def route(h):
        if h.headers.get("Range"):
            return 416, {"Content-Range": f"bytes */{len(DOWNLOAD_DATA)}"}, b""
        return 200, {}, DOWNLOAD_DATA
    http_server.routes["/f"] = route
    dest = tmp_path / "f.bin"
    part = dest.with_name("f.bin.part")
    part.write_bytes(DOWNLOAD_DATA)
    download.fetch([http_server.url + "/f"], dest, None, md5hex(DOWNLOAD_DATA))
    assert dest.read_bytes() == DOWNLOAD_DATA
    assert ranges(http_server) == [f"bytes={len(DOWNLOAD_DATA)}-"]  # the part was whole: nothing more asked
    # A .part longer than the file: deleted and downloaded again without Range.
    dest.unlink()
    part.write_bytes(DOWNLOAD_DATA + b"junk")
    download.fetch([http_server.url + "/f"], dest, None, md5hex(DOWNLOAD_DATA))
    assert dest.read_bytes() == DOWNLOAD_DATA
    assert not part.exists()
    assert ranges(http_server)[1:] == [f"bytes={len(DOWNLOAD_DATA) + 4}-", None]


# --- an MD5 is required, HTTPS only ------------------------------------------------------------


class RecordingSession:
    """Remembers each address asked; answers nothing (as if the network were down)."""

    def __init__(self):
        self.asked = []

    def get(self, url, **kw):
        self.asked.append(url)
        raise requests.ConnectionError("no network in this test")


@pytest.mark.parametrize("md5", [None, ""])
def test_a_download_without_an_md5_is_refused_before_any_request(tmp_path, md5):
    """No MD5 (missing, or unreadable where the server gives one) is never "no check": the
    file is not downloaded at all."""
    session = RecordingSession()
    with pytest.raises(download.DownloadError, match="no MD5 checksum"):
        download.fetch(["https://download.garmin.com/f"], tmp_path / "f.bin", 4, md5,
                       session=session)
    assert session.asked == []


def test_http_is_asked_over_https(tmp_path):
    """An address over plain HTTP (Garmin gives its fallback map hosts so) is asked over
    HTTPS, once even when the same address is also given over HTTPS."""
    session = RecordingSession()
    with pytest.raises(download.DownloadError):
        download.fetch(["https://omtmapupdate.garmin.com/a.img",
                        "http://omtmapupdate.garmin.com/a.img",
                        "http://worldwide.omtmapupdate.garmin.com/a.img"],
                       tmp_path / "a.img", 4, md5hex(DOWNLOAD_DATA), session=session)
    assert set(session.asked) == {"https://omtmapupdate.garmin.com/a.img",
                                  "https://worldwide.omtmapupdate.garmin.com/a.img"}
    assert not any(url.startswith("http:") for url in session.asked)


@pytest.mark.parametrize("url", ["ftp://download.garmin.com/a.img", "file:///etc/passwd",
                                 "a.img"])
def test_another_scheme_is_refused_before_any_request(tmp_path, url):
    session = RecordingSession()
    with pytest.raises(download.DownloadError, match="only over HTTPS"):
        download.fetch(["https://download.garmin.com/b.img", url], tmp_path / "a.img", 4,
                       md5hex(DOWNLOAD_DATA), session=session)
    assert session.asked == []
