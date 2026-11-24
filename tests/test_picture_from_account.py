"""Get Picture From Account: a device off the account whose picture Garmin's update server does
not give (unit-info without an address, as for an epix) gets it from the account only when the
user clicks the button on its page, since that request names its unit ID to the account
(device-info). Every device here is made up."""
import types
from pathlib import Path

import pytest
import requests
from fakes import (
    RunNow,
    Widget,
    device_session,
    replace_in_window,
)
from fakes import window as stand_in

from openexpress import known_devices
from openexpress.connect import NotAccountDevice
from openexpress.useragent import USER_AGENT

# --- the picture of a device the account cannot give -------------------------------------------

PNG = b"\x89PNG\r\n\x1a\n" + b"picture bytes"


PICTURE_URL = "https://res.garmin.com/en/products/010-00000-00/v/cf-lg-1234.png"


SECRET_UNIT, SECRET_SERIAL = 3987654321, "7ZQ123456"


class PictureResponse:
    def __init__(self, status=200, kind="image/png", body=PNG, length=None, error=None):
        self.status_code, self.body, self.error = status, body, error
        self.headers = {"Content-Type": kind}
        if length is not None:
            self.headers["Content-Length"] = str(length)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def iter_content(self, size):
        if self.error is not None:
            raise self.error
        for i in range(0, len(self.body), size):
            yield self.body[i:i + size]


class PictureHttp:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.calls = response or PictureResponse(), error, []

    def get(self, url, **kw):
        self.calls.append((url, kw))
        if self.error is not None:
            raise self.error
        return self.response


class PictureClient:
    """Stands in for GarminClient: the account answer, the unit-info answer, the downloads."""

    def __init__(self, account_url=None, unit_info=None, http=None):
        self.account_url, self.info, self.http = account_url, unit_info, http or PictureHttp()
        self.asked = []

    def device_image_url(self, unit_id):
        self.asked.append("account")
        if isinstance(self.account_url, Exception):
            raise self.account_url
        return self.account_url

    def unit_info(self, unit_id, part_number):
        self.asked.append("unit-info")
        if isinstance(self.info, Exception):
            raise self.info
        return self.info


def _unit_info(url=PICTURE_URL):
    return {"unitid": SECRET_UNIT, "serialnumber": SECRET_SERIAL, "devicemodel": "Test watch",
            "graphicurl": url}


def _picture_window(client, logged_in=False, ownership_error=None, monkeypatch=None):
    """A window that shows a picture as a (file, size) pair instead of a GTK texture."""
    from openexpress.gui.app import MainWindow

    if monkeypatch is not None:
        replace_in_window(monkeypatch, "load_texture",
                          lambda path: ("texture", path.read_bytes())
                          if path.read_bytes().startswith(PNG[:8]) else None)
    device = types.SimpleNamespace(unit_id=SECRET_UNIT, part_number="006-B0000-00")
    win = stand_in(
        session=device_session(device=device, client=client, logged_in=logged_in,
                                      ownership_error=ownership_error),
        device_image=Widget(), device_picture=Widget(), shown=[], jobs=[])
    win._set_device_texture = win.shown.append
    win.in_background = lambda work, done, on_error=None: win.jobs.append((work, done))
    win.show = lambda: MainWindow._show_device_picture(win)
    return win


def _finish(win):
    work, done = win.jobs.pop()
    done(work())


def _cache(xdg):
    folder = xdg / "cache" / "openexpress" / "images"
    return sorted(p.name for p in folder.iterdir()) if folder.is_dir() else []


@pytest.fixture
def pictures(xdg, monkeypatch, log_file):
    from openexpress import applog

    monkeypatch.setenv("XDG_CACHE_HOME", str(xdg / "cache"))
    monkeypatch.setenv("OPENEXPRESS_LOG", "debug")
    applog.setup("gui")
    return xdg, log_file, monkeypatch


def test_device_outside_the_account_gets_its_picture_from_unit_info(pictures):
    xdg, log_file, monkeypatch = pictures
    client = PictureClient(unit_info=_unit_info())
    win = _picture_window(client, logged_in=True, ownership_error="not yours",
                          monkeypatch=monkeypatch)
    win.show()
    assert win.shown == [] and len(win.jobs) == 1  # only handed to the background: no waiting
    assert client.asked == [] and client.http.calls == []
    _finish(win)
    assert client.asked == ["unit-info"]  # the account is not asked about a foreign device
    url, options = client.http.calls[0]
    assert url == PICTURE_URL and options == {
        "headers": {"User-Agent": USER_AGENT}, "timeout": 15, "stream": True,
        "allow_redirects": False}
    assert _cache(xdg) == ["device-006-B0000-00.png"] and win.shown == [("texture", PNG)]

    # The answer of unit-info went nowhere: not into the log, not into any file.
    log = log_file.read_text()
    assert "device picture: from unit-info" in log
    for secret in (str(SECRET_UNIT), SECRET_SERIAL, "unitids", "res.garmin.com", "cf-lg-1234"):
        assert secret not in log
    for path in xdg.rglob("*"):
        if path.is_file() and path.suffix != ".png":
            assert str(SECRET_UNIT) not in path.read_text(errors="replace")
            assert SECRET_SERIAL not in path.read_text(errors="replace")


def test_picture_in_the_cache_needs_no_request(pictures):
    xdg, log_file, monkeypatch = pictures
    folder = xdg / "cache" / "openexpress" / "images"
    folder.mkdir(parents=True)
    (folder / "device-006-B0000-00.png").write_bytes(PNG)
    client = PictureClient(unit_info=_unit_info())
    win = _picture_window(client, monkeypatch=monkeypatch)
    win.show()
    win.show()  # the second time from memory
    assert win.jobs == [] and client.asked == [] and client.http.calls == []
    assert win.shown == [("texture", PNG)] * 2
    assert log_file.read_text().count("device picture: from the cache") == 1


def test_a_cache_file_that_is_no_picture_is_not_kept(pictures):
    """A file in the cache that is not a picture: nothing kept for that part number (no None
    in the window's store), and the sources on the network are asked."""
    xdg, _log_file, monkeypatch = pictures
    folder = xdg / "cache" / "openexpress" / "images"
    folder.mkdir(parents=True)
    (folder / "device-006-B0000-00.png").write_bytes(b"not a picture")
    win = _picture_window(PictureClient(unit_info=_unit_info()), monkeypatch=monkeypatch)
    win.show()
    assert "006-B0000-00" not in getattr(win, "_device_textures", {})
    assert win.shown == [] and len(win.jobs) == 1


@pytest.mark.needs_display
def test_the_device_views_picture_from_the_cache_is_in_a_tile_built_later(pictures, in_the_window):
    """One store: the picture the device view read from the cache is there at once for a
    tile of that part number built later, with nothing read again in the background."""
    from gi.repository import Gdk, GLib

    from openexpress.devicelist import Tile
    from openexpress.gui import pictures as store
    from openexpress.gui.start import StartPage, tile_image

    xdg, _log_file, monkeypatch = pictures
    folder = xdg / "cache" / "openexpress" / "images"
    folder.mkdir(parents=True)
    (folder / "device-006-B0000-00.png").write_bytes(PNG)
    texture = Gdk.MemoryTexture.new(4, 4, Gdk.MemoryFormat.R8G8B8, GLib.Bytes.new(bytes(48)),
                                    12)
    in_the_window("load_texture", lambda path: texture)
    win = _picture_window(PictureClient(unit_info=_unit_info()))
    win.show()
    assert win.shown == [texture] and win.jobs == []
    start = StartPage(picture_of=lambda part: store.known(win, part))
    start.show([(Tile("Test watch", "006-B0000-00", "1.00", "mtp", True, False, False, None,
                      "006-B0000-00", None, None), "x")], False)
    assert tile_image(start.tiles()[0]).get_paintable() is texture


def test_account_is_the_first_source_and_unit_info_the_fallback(pictures, monkeypatch,
                                                                in_the_window):
    xdg, log_file, monkeypatch = pictures

    in_the_window("cached_download", lambda http, url, name: _save(xdg, name))
    client = PictureClient(account_url="https://connect.example/picture.png",
                           unit_info=_unit_info())
    win = _picture_window(client, logged_in=True, monkeypatch=monkeypatch)
    win.show()
    _finish(win)
    assert client.asked == ["account"] and client.http.calls == []  # unit-info not needed
    assert "device picture: from the account" in log_file.read_text()

    for account_url in (None, RuntimeError("no network")):  # no picture there, or an error
        (xdg / "cache" / "openexpress" / "images" / "device-006-B0000-00.png").unlink()
        client = PictureClient(account_url=account_url, unit_info=_unit_info())
        win = _picture_window(client, logged_in=True, monkeypatch=monkeypatch)
        win.show()
        _finish(win)
        assert client.asked == ["account", "unit-info"] and win.shown == [("texture", PNG)]


def _save(xdg, name):
    path = xdg / "cache" / "openexpress" / "images" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(PNG)
    return path


def test_without_a_login_the_fallback_is_used_too(pictures):
    xdg, _log, monkeypatch = pictures
    client = PictureClient(unit_info=_unit_info())
    win = _picture_window(client, logged_in=False, monkeypatch=monkeypatch)
    win.show()
    _finish(win)
    assert client.asked == ["unit-info"] and win.shown == [("texture", PNG)]


@pytest.mark.parametrize("url, response, error, why", [
    ("https://example.org/x.png", None, None, "host example.org is not allowed; no query"),
    ("https://res.garmin.com.example.org/x.png", None, None,
     "host res.garmin.com.example.org is not allowed; no query"),
    (PICTURE_URL, PictureResponse(status=302), None, "HTTP 302"),  # redirects are not followed
    (PICTURE_URL, PictureResponse(status=404), None, "HTTP 404"),
    (PICTURE_URL, PictureResponse(kind="text/html"), None, "not a PNG or JPEG"),
    # Said to be a JPEG, but a PNG: the type and the content must agree.
    (PICTURE_URL, PictureResponse(kind="image/jpeg"), None, "not a PNG or JPEG"),
    (PICTURE_URL, PictureResponse(body=b"<html>not a picture</html>"), None,
     "not a PNG or JPEG"),
    (PICTURE_URL, PictureResponse(length=5_000_000), None, "too large"),
    (PICTURE_URL, PictureResponse(body=PNG + b"x" * (2 * 1024 * 1024)), None, "too large"),
    (PICTURE_URL, None, requests.ConnectionError("down"), "network error"),
    (PICTURE_URL, None, requests.ReadTimeout("slow"), "timeout"),
    (PICTURE_URL, PictureResponse(error=requests.ReadTimeout("slow")), None, "timeout"),
])
def test_anything_unexpected_leaves_the_generic_icon(pictures, url, response, error, why):
    xdg, log_file, monkeypatch = pictures
    client = PictureClient(unit_info=_unit_info(url), http=PictureHttp(response, error))
    win = _picture_window(client, monkeypatch=monkeypatch)
    win.show()
    _finish(win)  # no exception: the picture is optional
    assert win.shown == [] and _cache(xdg) == []  # nothing shown, nothing left in the cache
    assert win.device_image.visible is True and win.device_picture.visible is False
    assert f"device picture: none (download refused: {why})" in log_file.read_text()
    if not url.startswith("https://res.garmin.com/"):
        assert client.http.calls == []  # another host is not even asked
        assert "x.png" not in log_file.read_text()  # its host only, never the address


JPEG = b"\xff\xd8\xff\xe0" + b"jpeg bytes"


@pytest.mark.parametrize("url, response, saved", [
    ("https://static.garmincdn.com/pumac/fenix.png", PictureResponse(), PNG),
    (PICTURE_URL, PictureResponse(kind="image/jpeg", body=JPEG), JPEG),
])
def test_a_png_or_jpeg_from_an_allowed_host_is_saved(pictures, url, response, saved):
    """The host static.garmincdn.com serves device pictures too, and a JPEG is taken as a
    PNG is. The file keeps its name in the cache: GTK reads a picture by its content."""
    xdg, _log, _monkeypatch = pictures
    from openexpress.gui.pictures import download_picture

    http = PictureHttp(response)
    path, why = download_picture(http, url, "device-x.png")
    assert (why, path.name, path.read_bytes()) == ("", "device-x.png", saved)
    assert [asked for asked, _kw in http.calls] == [url]
    assert _cache(xdg) == ["device-x.png"]


def test_a_picture_on_s3_is_refused_and_the_log_names_its_bucket_only(pictures):
    """A bucket on S3 that is not allowed (only Garmin Connect's is): nothing is asked, and
    the log says which bucket it was, without the rest of the address or its signature."""
    xdg, log_file, monkeypatch = pictures
    url = "https://s3.amazonaws.com/garmin-bucket/devices/x.png?X-Amz-Signature=secret"
    client = PictureClient(unit_info=_unit_info(url), http=PictureHttp())
    win = _picture_window(client, monkeypatch=monkeypatch)
    win.show()
    _finish(win)
    log = log_file.read_text()
    assert ("device picture: none (download refused: host s3.amazonaws.com, first segment "
            "garmin-bucket: not an allowed bucket; with query)") in log
    assert client.http.calls == [] and _cache(xdg) == []
    assert "secret" not in log and "Signature" not in log and "/devices" not in log


def test_a_picture_from_the_bucket_of_garmin_connect_is_saved_and_its_address_kept(pictures):
    """The account gives the pictures of some models on S3, in the bucket
    garmin-connect-prod. They are downloaded as from Garmin's own hosts and their address is
    kept with the device; the log names the host, never the rest of the address."""
    from openexpress import known_devices
    from openexpress.gui.pictures import download_picture

    xdg, log_file, _monkeypatch = pictures
    url = ("https://s3.amazonaws.com/garmin-connect-prod/device_images/x.png"
           "?X-Amz-Signature=secret")
    http = PictureHttp()
    path, why = download_picture(http, url, "device-006-B0000-00.png")
    assert (why, path.read_bytes()) == ("", PNG)
    assert [asked for asked, _kw in http.calls] == [url]
    assert _cache(xdg) == ["device-006-B0000-00.png"]
    known_devices.remember(SECRET_UNIT, "Test watch", "006-B0000-00", "1.00", "mtp")
    assert known_devices.set_image_url(SECRET_UNIT, url)
    assert known_devices.load()[SECRET_UNIT].image_url == url
    log = log_file.read_text()
    assert "devices: the picture of 006-B0000-00 is at s3.amazonaws.com" in log
    for private in ("secret", "Signature", "device_images", "x.png"):
        assert private not in log, private


@pytest.mark.parametrize("logged_in, account_url, unit_info_url, kept", [
    # Off the account (or nobody logged in): the address from unit-info.
    (False, None, PICTURE_URL, PICTURE_URL),
    # On the account: the account's address.
    (True, "https://res.garmin.com/en/products/account.png", None,
     "https://res.garmin.com/en/products/account.png"),
    # An address that is not allowed is not kept: the one before stays.
    (False, None, "https://s3.amazonaws.com/garmin-bucket/x.png?X-Amz-Signature=s", "before"),
])
def test_the_address_of_a_picture_is_kept_with_the_device(pictures, logged_in, account_url,
                                                          unit_info_url, kept):
    """The device view keeps the address of its picture with the device remembered, for
    when the picture is no longer in the cache and the device is not connected."""
    from openexpress import known_devices

    xdg, _log, monkeypatch = pictures
    known_devices.remember(SECRET_UNIT, "Test watch", "006-B0000-00", "1.00", "mtp")
    before = "https://res.garmin.com/en/products/before.png"
    known_devices.set_image_url(SECRET_UNIT, before)

    class Http(PictureHttp):
        def get(self, url, **kw):  # the account's picture comes through cached_download
            self.calls.append((url, kw))
            return (types.SimpleNamespace(ok=True, content=PNG) if "account" in url
                    else self.response)

    client = PictureClient(account_url=account_url, unit_info=_unit_info(unit_info_url),
                           http=Http())
    win = _picture_window(client, logged_in=logged_in, monkeypatch=monkeypatch)
    win.show()
    _finish(win)
    assert known_devices.load()[SECRET_UNIT].image_url == (before if kept == "before"
                                                           else kept)


def test_a_device_off_the_account_with_its_picture_cached_gets_its_address_kept(pictures):
    """A device off the account with its picture cached: unit-info is asked once for the address
    alone, which is kept (without it, the picture would be gone once the cache is cleared); the
    cached file stays. With the address kept, or for a device on the account (its address comes
    from the account's list), nothing is asked."""
    from openexpress import known_devices

    xdg, _log, monkeypatch = pictures
    known_devices.remember(SECRET_UNIT, "Test watch", "006-B0000-00", "1.00", "mtp")
    cached = _save(xdg, "device-006-B0000-00.png")
    before = cached.stat().st_mtime_ns
    client = PictureClient(unit_info=_unit_info(), http=PictureHttp())
    win = _picture_window(client, monkeypatch=monkeypatch)
    win.show()
    assert win.shown == [("texture", PNG)]  # at once, from the cache
    _finish(win)
    assert client.asked == ["unit-info"] and client.http.calls == []  # the address only
    assert known_devices.load()[SECRET_UNIT].image_url == PICTURE_URL
    assert cached.stat().st_mtime_ns == before and _cache(xdg) == ["device-006-B0000-00.png"]
    for logged_in in (False, True):  # the address kept; or a device on the account
        again = PictureClient(unit_info=_unit_info(), http=PictureHttp())
        win = _picture_window(again, logged_in=logged_in, monkeypatch=monkeypatch)
        if logged_in:
            known_devices.forget(SECRET_UNIT)
            known_devices.remember(SECRET_UNIT, "Test watch", "006-B0000-00", "1.00", "mtp")
        win.show()
        assert win.jobs == [] and again.asked == [], logged_in


def test_an_open_drive_garmin_tells_no_kind_of_gets_unit_info_asked_once_a_run(pictures):
    """An inReach Mini 2 in mass storage mode, its picture cached and its address kept: the
    account is never asked, so unit-info is asked once a run while Garmin has told no kind of
    it, for the log, noting only its segment and connect support; the cached file stays. A
    drive whose kind is kept (a Varia) is not asked."""
    from openexpress import devicelist, known_devices

    xdg, log_file, monkeypatch = pictures
    known_devices.remember(SECRET_UNIT, "inReach Mini 2", "006-B0000-00", "5.05", "ums")
    known_devices.set_image_url(SECRET_UNIT, PICTURE_URL)
    _save(xdg, "device-006-B0000-00.png")
    info = dict(_unit_info(), segmenttype="Outdoor", connectsupport="Enabled")
    client = PictureClient(unit_info=info)
    win = _picture_window(client, logged_in=True, monkeypatch=monkeypatch)
    win.session.storage = types.SimpleNamespace(mass_storage=True)  # a drive
    win.show()
    assert win.shown == [("texture", PNG)]  # at once, from the cache
    _finish(win)
    assert client.asked == ["unit-info"] and client.http.calls == []  # never the account
    win.show()  # opened again in the same run
    assert win.jobs == [] and client.asked == ["unit-info"]
    log = log_file.read_text()
    assert ("devices: Garmin tells no kind of 006-B0000-00 (account categories: not known; "
            "unit-info segment: Outdoor, connect support: Enabled)") in log
    assert str(SECRET_UNIT) not in log and SECRET_SERIAL not in log

    known_devices.set_category(SECRET_UNIT, known_devices.BIKE_ACCESSORY)  # a Varia
    monkeypatch.setattr(devicelist, "_kind_evidence", {})  # the next run
    monkeypatch.setattr(devicelist, "_kind_told", set())
    again = PictureClient(unit_info=info)
    win = _picture_window(again, monkeypatch=monkeypatch)
    win.session.storage = types.SimpleNamespace(mass_storage=True)  # a drive
    win.show()
    assert win.jobs == [] and again.asked == []


def test_a_download_that_takes_too_long_is_given_up(pictures):
    xdg, _log, _monkeypatch = pictures
    from openexpress.gui.pictures import download_picture

    ticks = iter([0.0, 5.0, 20.0, 40.0])
    http = PictureHttp(PictureResponse(body=PNG + b"x" * 200_000))
    assert download_picture(http, PICTURE_URL, "device-x.png",
                                clock=lambda: next(ticks)) == (None, "timeout")
    assert _cache(xdg) == []


@pytest.mark.parametrize("info", [{}, {"graphicurl": ""}, {"graphicurl": None}, [], None,
                                  RuntimeError(f"HTTP 500: serial {SECRET_SERIAL}")])
def test_no_address_from_unit_info_leaves_the_icon(pictures, info):
    xdg, log_file, monkeypatch = pictures
    client = PictureClient(unit_info=info)
    win = _picture_window(client, monkeypatch=monkeypatch)
    win.show()
    _finish(win)
    assert win.shown == [] and client.http.calls == [] and _cache(xdg) == []
    log = log_file.read_text()
    assert "device picture: none (unit-info gave no address)" in log
    assert SECRET_SERIAL not in log  # an error that quotes the answer is not written down


def test_result_for_a_device_that_is_gone_is_not_shown(pictures):
    _xdg, _log, monkeypatch = pictures
    client = PictureClient(unit_info=_unit_info())
    win = _picture_window(client, monkeypatch=monkeypatch)
    win.show()
    win.session = None  # unplugged while the picture was on its way
    _finish(win)
    assert win.shown == []


def test_unit_info_request_keeps_the_device_out_of_the_log(log_file):
    from openexpress import applog
    from openexpress.connect import GarminClient
    from openexpress.settings import Settings

    applog.setup("gui")
    sent = []

    class Http:
        def request(self, method, url, **kw):
            sent.append(url)
            return types.SimpleNamespace(status_code=200, ok=True, content=b"{}",
                                         json=lambda: {"GraphicUrl": PICTURE_URL},
                                         headers={}, text="{}")

    client = GarminClient(Settings(), None, None)
    client.http = Http()
    assert client.unit_info(SECRET_UNIT, "006-B0000-00") == {"graphicurl": PICTURE_URL}
    assert sent[0].endswith(f"/unitids/{SECRET_UNIT}")  # the address itself names the unit
    line = log_file.read_text().splitlines()[-1]
    assert "HTTP GET omt.garmin.com -> 200 in " in line
    assert "unitids" not in line and str(SECRET_UNIT) not in line and "<unit>" not in line


# --- names from the server never lead out of the picture cache --------------------------------


@pytest.mark.parametrize("name", ["app-a/b.png", "app-../../x.png", "..", "", "/tmp/x.png"])
def test_a_picture_name_that_is_no_file_name_is_not_saved(xdg, name):
    """An icon is cached as app-<app ID>.png, the app ID as the Connect IQ server gives it:
    a name that is no plain file name is not saved (and not even asked for), the icon stays
    as it is; nothing is written outside the cache. The same for device pictures."""
    from openexpress.gui.pictures import cached_download, download_picture

    asked = []

    class Http:
        def get(self, url, **kw):
            asked.append(url)
            raise AssertionError("asked for a picture with a bad name")

    before = {p for p in Path(xdg).rglob("*") if p.is_file()}
    assert cached_download(Http(), "https://res.garmin.com/x.png", name) is None
    assert download_picture(Http(), "https://res.garmin.com/x.png", name) == (
        None, "not a file name")
    assert asked == []
    assert {p for p in Path(xdg).rglob("*") if p.is_file()} == before


# --- pictures over HTTPS only -------------------------------------------------------------------


def test_a_picture_over_http_is_asked_over_https(xdg):
    """A picture or an icon whose address is plain HTTP is asked over HTTPS, as every
    download; for a device picture the rule (picture_urls) then still asks for one of
    Garmin's hosts, so plain HTTP from another host is not asked at all."""
    from openexpress.gui.pictures import cached_download, download_picture

    icon = types.SimpleNamespace(ok=True, content=b"icon")

    class IconHttp:
        def __init__(self):
            self.calls = []

        def get(self, url, **kw):
            self.calls.append(url)
            return icon

    http = IconHttp()
    assert cached_download(http, "http://services.garmin.com/icon/1", "app-1.png")
    assert http.calls == ["https://services.garmin.com/icon/1"]
    pictures = PictureHttp()
    path, why = download_picture(pictures, "http://res.garmin.com/en/a.png", "device-a.png")
    assert (path is not None, why) == (True, "")
    assert [url for url, _kw in pictures.calls] == ["https://res.garmin.com/en/a.png"]
    other = PictureHttp()
    assert download_picture(other, "http://example.org/a.png", "device-b.png") == (
        None, "host example.org is not allowed; no query")
    assert other.calls == []


@pytest.mark.parametrize("url, why", [
    ("https://example.org/a.png?token=secret", "host example.org is not allowed; with query"),
    ("https://example.org/a.png", "host example.org is not allowed; no query"),
    ("https://s3.amazonaws.com/other-bucket/a.png?X-Amz-Signature=secret",
     "host s3.amazonaws.com, first segment other-bucket: not an allowed bucket; with query"),
    ("ftp://res.garmin.com/a.png?x=1", "not HTTPS; with query"),
    ("", "no address"),  # nothing to say of a query
])
def test_a_refusal_says_whether_the_address_has_a_query_never_what(xdg, url, why):
    from openexpress.gui.pictures import download_picture

    http = PictureHttp()
    assert download_picture(http, url, "device-a.png") == (None, why)
    assert http.calls == [] and "secret" not in why and "token" not in why


@pytest.mark.parametrize("url", ["ftp://res.garmin.com/a.png", "file:///etc/passwd", "a.png"])
def test_a_picture_over_another_scheme_is_not_asked(xdg, url, log_file):
    """Any other address: nothing is asked or saved, the icon stays; one line in the log, no
    message (pictures are optional)."""
    from openexpress.gui.pictures import cached_download, download_picture

    http = PictureHttp()
    assert cached_download(http, url, "app-1.png") is None
    assert download_picture(http, url, "device-a.png") == (None, "not HTTPS; no query")
    assert http.calls == []
    assert not any(p.is_file() for p in (Path(xdg) / "cache").rglob("*.png"))


NONE_TEXT = "Your Garmin Connect account has no picture of this device"


class OffAccountClient(PictureClient):
    """unit-info gives no picture address; the account, asked about the device off it, gives
    `off_account_url`."""

    def __init__(self, off_account_url=None, **kw):
        super().__init__(unit_info={"devicemodel": "Test watch"}, **kw)
        self.off_account_url = off_account_url

    def device_image_url_off_account(self, unit_id):
        self.asked.append(("account, off it", unit_id))
        if isinstance(self.off_account_url, Exception):
            raise self.off_account_url
        return self.off_account_url


def _off_account(win):
    win.session.ownership_error = NotAccountDevice("not on the account")
    return win


def test_the_client_asks_the_account_about_a_device_off_it_only_by_its_own_method():
    from openexpress.connect import DeviceNotVerified, GarminClient

    client = GarminClient.__new__(GarminClient)
    client.verified_unit_id = None  # nothing checked against the account
    sent = []
    client._connect = lambda method, path, what, **kw: sent.append((method, path)) or object()
    client._json = lambda r, what: {"imageUrl": PICTURE_URL}
    with pytest.raises(DeviceNotVerified):
        client.device_image_url(SECRET_UNIT)  # the usual method: never about a device not checked
    assert sent == []
    assert client.device_image_url_off_account(SECRET_UNIT) == PICTURE_URL
    assert sent == [("GET", f"device-service/deviceservice/device-info/{SECRET_UNIT}")]


def test_opening_never_asks_the_account_and_offers_the_button(pictures):
    _xdg, _log, monkeypatch = pictures
    client = OffAccountClient(off_account_url=PICTURE_URL)
    win = _off_account(_picture_window(client, logged_in=True, monkeypatch=monkeypatch))
    win.show()
    _finish(win)
    assert client.asked == ["unit-info"]  # never the account on its own
    assert win.shown == [] and win.account_picture_offer is win.session


@pytest.mark.parametrize("case", ["on the account", "not logged in", "a drive",
                                  "unit-info has it"])
def test_the_button_only_for_a_device_off_the_account_without_a_picture(pictures, case):
    _xdg, _log, monkeypatch = pictures
    client = OffAccountClient(off_account_url=PICTURE_URL)
    if case == "unit-info has it":
        client.info = _unit_info()
    win = _picture_window(client, logged_in=case != "not logged in", monkeypatch=monkeypatch)
    if case != "on the account":
        _off_account(win)
    win.session.storage = types.SimpleNamespace(mass_storage=case == "a drive")
    win.show()
    while win.jobs:
        _finish(win)
    assert getattr(win, "account_picture_offer", None) is None
    assert not [a for a in client.asked if a != "unit-info" and a != "account"]


def _clicking_window(client, monkeypatch):
    win = _off_account(_picture_window(client, logged_in=True, monkeypatch=monkeypatch))
    win.show()
    _finish(win)
    shown_tiles, toasts, rows, errors = [], [], [], []
    win.start = types.SimpleNamespace(set_picture=lambda part, texture: shown_tiles.append(
        (part, texture)))
    win.toast = toasts.append
    win.account_picture_row = types.SimpleNamespace(set_visible=rows.append)
    win.error = lambda title, text: errors.append((title, text))
    win.run_task = RunNow()
    win.errors = errors
    return win, shown_tiles, toasts, rows


def test_the_button_gets_the_picture_into_the_cache(pictures):
    from openexpress.gui.device_page import get_picture_from_account

    xdg, log_file, monkeypatch = pictures
    known_devices.remember(SECRET_UNIT, "Test watch", "006-B0000-00", "1.00", "mtp", now=1.0)
    client = OffAccountClient(off_account_url=PICTURE_URL)
    win, shown_tiles, toasts, rows = _clicking_window(client, monkeypatch)
    get_picture_from_account(win)
    assert client.asked == ["unit-info", ("account, off it", SECRET_UNIT)]
    assert _cache(xdg) == ["device-006-B0000-00.png"]
    assert win.shown == [("texture", PNG)]
    assert shown_tiles == [("006-B0000-00", ("texture", PNG))]  # its tile too
    assert known_devices.load()[SECRET_UNIT].image_url == PICTURE_URL
    assert rows == [False] and win.account_picture_offer is None and toasts == []
    log = log_file.read_text()
    assert "device picture: from the account, asked on request" in log
    # The host of the picture only (known_devices.set_image_url), never the unit ID or the
    # rest of the address.
    assert str(SECRET_UNIT) not in log and "cf-lg-1234" not in log


def test_the_account_without_a_picture_says_so(pictures):
    from openexpress.gui.device_page import get_picture_from_account

    xdg, _log, monkeypatch = pictures
    client = OffAccountClient(off_account_url=None)
    win, shown_tiles, toasts, rows = _clicking_window(client, monkeypatch)
    get_picture_from_account(win)
    assert toasts == [NONE_TEXT] and rows == [False]  # not offered again
    assert _cache(xdg) == [] and win.shown == [] and shown_tiles == []


# The account, asked about an epix not on it, answers HTTP 404 with a JSON {"error": …}:
# the window says so in a short sentence, never the server's words, and the button stays
# (a try later may answer).
UNKNOWN_TEXT = "Garmin Connect does not know this device, so it has no picture of it."
REFUSED_TEXT = "Garmin Connect could not be asked for the picture. Try again later."


@pytest.mark.parametrize(("status", "words"), [(404, UNKNOWN_TEXT), (500, REFUSED_TEXT),
                                               (None, REFUSED_TEXT)])
def test_a_refusal_of_the_account_is_said_in_short(pictures, status, words):
    from openexpress.connect import ApiError
    from openexpress.gui.device_page import get_picture_from_account

    xdg, _log, monkeypatch = pictures
    refusal = ApiError(f"Device info: HTTP {status}", status, '{"error": "made up"}')
    client = OffAccountClient(off_account_url=refusal)
    win, shown_tiles, toasts, rows = _clicking_window(client, monkeypatch)
    get_picture_from_account(win)
    assert win.errors == [("Picture Not Downloaded", words)]
    assert rows == [] and win.account_picture_offer is win.session  # the button stays
    assert _cache(xdg) == [] and toasts == [] and shown_tiles == []
