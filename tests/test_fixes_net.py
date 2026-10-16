import hashlib
import http.server
import json
import os
import re
import socket
import stat
import threading
import time
from pathlib import Path

import pytest
import requests

from openexpress import download, i18n, paths
from openexpress import settings as settings_mod
from openexpress.auth import store
from openexpress.auth.oauth1 import authorization_header
from openexpress.connect import ApiError, GarminClient
from openexpress.settings import Consumer, Settings, SetupError


class Server:
    def __init__(self):
        self.routes = {}
        self.requests = []
        server = self

        class H(http.server.BaseHTTPRequestHandler):
            def _handle(self):
                n = int(self.headers.get("Content-Length") or 0)
                if n:
                    self.rfile.read(n)
                server.requests.append((self.command, self.path, dict(self.headers)))
                route = server.routes.get(self.path.split("?")[0])
                if route is None:
                    status, headers, body = 404, {}, b""
                elif callable(route):
                    status, headers, body = route(self)
                else:
                    status, headers, body = route
                self.send_response(status)
                for k, v in headers.items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            do_GET = do_POST = _handle

            def log_message(self, *a):
                pass

        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()


@pytest.fixture
def server():
    s = Server()
    yield s
    s.srv.shutdown()


def unused_url():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    return f"http://127.0.0.1:{port}/"


def make_client(monkeypatch, base):
    base = base.rstrip("/") + "/"
    monkeypatch.setitem(settings_mod.ENVIRONMENTS, "test",
                        {"sso": base, "connectapi": base, "omt": base, "gcs": base})
    c = GarminClient(Settings(environment="test", client_guid="g"),
                     Consumer("ckey", "csecret"), store.ConnectAuth("tok", "tsec"))
    c.verified_unit_id = 1  # as after service.require_account_device()
    return c


# --- 1. network and JSON errors -> ApiError ---------------------------------------------

def test_connection_error_becomes_api_error(monkeypatch):
    c = make_client(monkeypatch, unused_url())
    calls = [
        c.user_profile,
        lambda: c.upload_settings(1),
        lambda: c.activity_matches(1, ["a"]),
        lambda: c.device_messages(1),
        lambda: c.download_message("x", absolute=False),
        lambda: c.ack_message(1),
        lambda: c.device_image_url(1),
        lambda: c.unit_info(1, "006"),
        lambda: c.software_updates(b"<x/>"),
        lambda: c.exchange_ticket("T", "svc"),
        lambda: c.upload_file(c.connect_url("up"), b"d", is_zip=False, last_file=True,
                              authenticated=True),
    ]
    for call in calls:
        with pytest.raises(ApiError, match="network error"):
            call()


def test_bad_json_becomes_api_error(monkeypatch, server):
    bad = (200, {"Content-Type": "application/json"}, b"<html>oops")
    for p in ("/userprofile-service/userprofile/userProfileBase",
              "/device-service/devicemessage/messages",
              "/api/unit-info/manufacturers/Taiwan/unitids/1",
              "/UploadConfigurationService/UnitUploadSettings/1"):
        server.routes[p] = bad
    c = make_client(monkeypatch, server.url)
    for call in (c.user_profile, lambda: c.device_messages(1),
                 lambda: c.unit_info(1, "006"), lambda: c.upload_settings(1)):
        with pytest.raises(ApiError, match="unexpected response from the server"):
            call()


def test_exchange_ticket_missing_secret(monkeypatch, server):
    server.routes["/oauth-service/oauth/preauthorized"] = (200, {}, b"oauth_token=abc")
    c = make_client(monkeypatch, server.url)
    with pytest.raises(ApiError):
        c.exchange_ticket("T", "svc")
    server.routes["/oauth-service/oauth/preauthorized"] = (
        200, {}, b"oauth_token=abc&oauth_token_secret=def")
    assert c.exchange_ticket("T", "svc") == ("abc", "def")


def test_a_network_error_in_the_ticket_exchange_does_not_show_the_ticket(monkeypatch):
    ticket = "ST-0123456-AbCdEfGhIjKlMnOp-cas"

    def refused(self, method, url, **kw):
        # What requests says when the server cannot be reached: the address, query and all.
        where = url.split("/", 3)[3]
        raise requests.ConnectionError(
            f"HTTPSConnectionPool(host='connectapi.garmin.com', port=443): Max retries exceeded "
            f"with url: /{where} (Caused by NewConnectionError(\"HTTPSConnection(host="
            f"'connectapi.garmin.com', port=443): Failed to establish a new connection: "
            f"[Errno 111] Connection refused\"))")

    monkeypatch.setattr(requests.Session, "request", refused)
    c = GarminClient(Settings(client_guid="g"), Consumer("ckey", "csecret"))
    with pytest.raises(ApiError) as error:
        c.exchange_ticket(ticket, "https://sso.garmin.com/sso/embed")
    text = str(error.value)  # what the window and the command line show
    assert "ST-" not in text and "AbCdEfGhIjKlMnOp" not in text
    # It still says what happened, and with which server.
    assert text.startswith("Ticket exchange: network error (")
    assert "connectapi.garmin.com" in text and "Connection refused" in text


# --- 2. poll_location -------------------------------------------------------------------

def test_poll_location_network_error(monkeypatch):
    c = make_client(monkeypatch, unused_url())
    assert c.poll_location(c.connect_url("st/1"), 1, timeout_s=5) == 0


def test_poll_location_relative_and_clamped(monkeypatch, server):
    server.routes["/up"] = (202, {"Location": "/activity-service/st/1"}, b"{}")
    server.routes["/activity-service/st/1"] = (201, {}, b"")
    c = make_client(monkeypatch, server.url)
    res = c.upload_file(c.connect_url("up"), b"d", is_zip=False, last_file=True,
                        authenticated=True)
    assert res.location == server.url + "/activity-service/st/1"
    assert c.poll_location(res.location, 1) == 201
    assert c.poll_location("/activity-service/st/1", 1, base=server.url + "/up") == 201

    server.routes["/activity-service/st/2"] = (202, {"location-in-milliseconds": "60000"}, b"")
    t = time.monotonic()
    assert c.poll_location(server.url + "/activity-service/st/2", 60000, timeout_s=0.3) == 202
    assert time.monotonic() - t < 3


# --- 8. signing covers the URL that is actually sent ----------------------------------

def test_signature_uses_sent_url(monkeypatch, server):
    server.routes["/a%20b/~user"] = (200, {}, b"{}")
    c = make_client(monkeypatch, server.url)
    c._connect("GET", "a b/%7Euser", "X", params={"q": "x y"})
    method, path, headers = server.requests[-1]
    assert path.startswith("/a%20b/~user?")
    auth = headers["Authorization"]
    fields = dict(re.findall(r'(\w+)="([^"]*)"', auth))
    expected = authorization_header(
        "GET", server.url + path, "ckey", "csecret", "tok", "tsec",
        nonce=fields["oauth_nonce"], timestamp=int(fields["oauth_timestamp"]))
    assert auth == expected


# --- 3/4/5. downloads -------------------------------------------------------------------

DATA = bytes(range(256)) * 20


def md5hex(b):
    return hashlib.md5(b).hexdigest()


def ranged(ignore_start=False, content_range=True, wrong_start=False):
    def route(h):
        rng = h.headers.get("Range")
        if not rng:
            return 200, {}, DATA
        start = int(rng.split("=")[1].split("-")[0])
        if ignore_start:
            start = 0
        body = DATA[start:]
        hdr = {}
        if content_range:
            shown = start + 7 if wrong_start else start
            hdr["Content-Range"] = f"bytes {shown}-{len(DATA) - 1}/{len(DATA)}"
        return 206, hdr, body
    return route


def test_size_zero_without_part(server, tmp_path):
    server.routes["/f"] = (200, {}, DATA)
    dest = tmp_path / "f.bin"
    download.fetch([server.url + "/f"], dest, 0, None)
    assert dest.read_bytes() == DATA


@pytest.mark.parametrize("kw", [{}, {"ignore_start": True}, {"content_range": False},
                                {"wrong_start": True}])
def test_resume_checks_content_range(server, tmp_path, kw):
    server.routes["/f"] = ranged(**kw)
    dest = tmp_path / "f.bin"
    dest.with_name("f.bin.part").write_bytes(DATA[:1000])
    download.fetch([server.url + "/f"], dest, None, None)
    assert dest.read_bytes() == DATA


def test_invalid_md5_is_an_error(server, tmp_path):
    server.routes["/f"] = (200, {}, DATA)
    with pytest.raises(download.DownloadError):
        download.fetch([server.url + "/f"], tmp_path / "f.bin", None, "garbage")


def test_416_with_complete_part(server, tmp_path):
    def route(h):
        if h.headers.get("Range"):
            return 416, {"Content-Range": f"bytes */{len(DATA)}"}, b""
        return 200, {}, DATA
    server.routes["/f"] = route
    dest = tmp_path / "f.bin"
    part = dest.with_name("f.bin.part")
    part.write_bytes(DATA)
    download.fetch([server.url + "/f"], dest, None, md5hex(DATA))
    assert dest.read_bytes() == DATA
    # A .part longer than the file: deleted and downloaded again without Range.
    dest.unlink()
    part.write_bytes(DATA + b"junk")
    download.fetch([server.url + "/f"], dest, None, None)
    assert dest.read_bytes() == DATA
    assert not part.exists()


# --- 6/7. paths -------------------------------------------------------------------------

def test_write_private(tmp_path, monkeypatch):
    target = tmp_path / "d" / "secret.json"
    stale = tmp_path / "d" / "secret.json.tmp"
    stale.parent.mkdir()
    stale.write_text("old")
    os.chmod(stale, 0o644)
    paths.write_private(target, "data")
    assert target.read_text() == "data"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600

    def boom(*a):
        raise OSError("fail")
    monkeypatch.setattr(paths.os, "replace", boom)
    with pytest.raises(OSError):
        paths.write_private(target, "new")
    assert target.read_text() == "data"
    assert sorted(p.name for p in target.parent.iterdir()) == ["secret.json", "secret.json.tmp"]


def test_app_dir_migration_race(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    old, new = tmp_path / paths.LEGACY_NAME, tmp_path / paths.APP_NAME

    def raced(self, target):
        Path(target).mkdir()  # another process won
        raise OSError("exists")
    old.mkdir()
    monkeypatch.setattr(Path, "rename", raced)
    assert paths.config_dir() == new

    def gone(self, target):
        self.rmdir()
        raise OSError("gone")
    new.rmdir()
    monkeypatch.setattr(Path, "rename", gone)
    assert paths.config_dir() == new

    def denied(self, target):
        raise OSError("denied")
    old.mkdir()
    monkeypatch.setattr(Path, "rename", denied)
    assert paths.config_dir() == old


# --- 10. system language preference list ----------------------------------------------

@pytest.fixture
def sys_lang(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("OPENEXPRESS_LANG", raising=False)
    for v in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        monkeypatch.delenv(v, raising=False)
    yield monkeypatch
    i18n.setup("en")


@pytest.mark.parametrize("env,expected", [
    ({"LANGUAGE": "en_US:pl"}, "en"),
    ({"LANGUAGE": "pl_PL:en"}, "pl"),
    ({"LANGUAGE": "de:pl"}, "pl"),
    ({"LANG": "pl_PL.UTF-8"}, "pl"),
    ({"LC_ALL": "C", "LANG": "pl_PL.UTF-8"}, "en"),
    ({"LANG": "POSIX"}, "en"),
    ({"LANG": "de_DE.UTF-8"}, "en"),
    ({}, "en"),
])
def test_system_language(sys_lang, env, expected):
    for k, v in env.items():
        sys_lang.setenv(k, v)
    assert i18n.setup() == expected
    assert (i18n._("Install") == "Install") == (expected == "en")


def test_explicit_language_wins(sys_lang):
    sys_lang.setenv("LANGUAGE", "en_US:pl")
    assert i18n.setup("pl") == "pl"
    sys_lang.setenv("OPENEXPRESS_LANG", "pl")
    assert i18n.setup() == "pl"


# --- 11. damaged config files ---------------------------------------------------------

def test_damaged_settings(xdg):
    path = paths.config_dir() / "settings.json"
    path.parent.mkdir(parents=True)
    for content in ("{bad", "[1, 2]"):
        path.write_text(content)
        with pytest.raises(SetupError, match="settings.json"):
            Settings.load()
        assert i18n._configured_language() in (None, "en")  # OPENEXPRESS_LANG may be set


def test_configured_language_ignores_non_dict(xdg, monkeypatch):
    monkeypatch.delenv("OPENEXPRESS_LANG", raising=False)
    path = paths.config_dir() / "settings.json"
    path.parent.mkdir(parents=True)
    path.write_text("[1]")
    assert i18n._configured_language() is None
    path.write_text(json.dumps({"ui_language": "pl"}))
    assert i18n._configured_language() == "pl"


def test_damaged_login(xdg):
    p = paths.config_dir() / "connect_auth.json"
    p.parent.mkdir(parents=True)
    for content in ("{bad", "[]", json.dumps({"token": "x"})):
        p.write_text(content)
        with pytest.raises(store.NotLoggedIn, match="damaged"):
            store.load()


def test_damaged_consumer(xdg):
    p = settings_mod.consumer_path()
    p.parent.mkdir(parents=True)
    for content in ("{bad", "[]", json.dumps({"key": "x"})):
        p.write_text(content)
        with pytest.raises(SetupError):
            settings_mod.load_consumer()


def test_activity_matches_body_not_signed():
    """Garmin rejects (401) a signature that includes the form body; Express doesn't sign it."""
    from urllib.parse import parse_qsl, urlsplit

    from openexpress.auth.store import ConnectAuth
    from openexpress.connect import GarminClient
    from openexpress.settings import Consumer, Settings

    sent = {}

    class Resp:
        status_code = 200
        ok = True
        headers = {}
        content = b'{"matches": {"1": {"match": {"activityId": 5}}}}'
        text = content.decode()

        def json(self):
            import json
            return json.loads(self.content)

    class Session:
        def request(self, method, url, headers=None, data=None, **kw):
            sent.update(method=method, url=url, headers=headers, data=data)
            return Resp()

    client = GarminClient(Settings(), Consumer("ck", "cs"), ConnectAuth("t", "ts"), Session())
    client.verified_unit_id = 42
    assert client.activity_matches(42, ["2", "1"]) == {"1": True, "2": False}
    query = urlsplit(sent["url"]).query
    assert sent["data"] == query == "deviceId=42&externalIds=1&externalIds=2"
    assert sent["headers"]["Content-Type"] == "application/x-www-form-urlencoded"
    # The signature must cover only the URL: recompute it without the body and compare.
    import re

    from openexpress.auth.oauth1 import authorization_header

    auth = sent["headers"]["Authorization"]
    nonce = re.search(r'oauth_nonce="([^"]+)"', auth).group(1)
    ts = int(re.search(r'oauth_timestamp="(\d+)"', auth).group(1))
    expected = authorization_header("POST", sent["url"], "ck", "cs", "t", "ts",
                                    nonce=nonce, timestamp=ts)
    assert auth == expected
    assert dict(parse_qsl(query))  # sanity
