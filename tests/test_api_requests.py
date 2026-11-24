"""Requests to Garmin's servers: network and JSON errors, the status check of an upload, signing."""

import logging
import re
import socket
import time

import pytest
import requests

from openexpress import settings as settings_mod
from openexpress.auth import store
from openexpress.auth.oauth1 import authorization_header
from openexpress.connect import ApiError, GarminClient
from openexpress.settings import Consumer, Settings


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


# --- network and JSON errors -> ApiError ------------------------------------------------

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


def test_bad_json_becomes_api_error(monkeypatch, http_server):
    bad = (200, {"Content-Type": "application/json"}, b"<html>oops")
    for p in ("/userprofile-service/userprofile/userProfileBase",
              "/device-service/devicemessage/messages",
              "/api/unit-info/manufacturers/Taiwan/unitids/1",
              "/UploadConfigurationService/UnitUploadSettings/1"):
        http_server.routes[p] = bad
    c = make_client(monkeypatch, http_server.url)
    for call in (c.user_profile, lambda: c.device_messages(1),
                 lambda: c.unit_info(1, "006"), lambda: c.upload_settings(1)):
        with pytest.raises(ApiError, match="Garmin's server gave an answer openExpress does not understand; try again later"):
            call()


def test_exchange_ticket_missing_secret(monkeypatch, http_server):
    http_server.routes["/oauth-service/oauth/preauthorized"] = (200, {}, b"oauth_token=abc")
    c = make_client(monkeypatch, http_server.url)
    with pytest.raises(ApiError):
        c.exchange_ticket("T", "svc")
    http_server.routes["/oauth-service/oauth/preauthorized"] = (
        200, {}, b"oauth_token=abc&oauth_token_secret=def")
    assert c.exchange_ticket("T", "svc") == ("abc", "def")


def test_a_network_error_in_the_ticket_exchange_does_not_show_the_ticket(monkeypatch, caplog):
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
    with caplog.at_level(logging.INFO, logger="openexpress"), \
            pytest.raises(ApiError) as error:
        c.exchange_ticket(ticket, "https://sso.garmin.com/sso/embed")
    # What the window and the command line show.
    assert str(error.value) == "Ticket exchange: network error (no connection)"
    # The log file says what happened, and with which server, without the ticket.
    (logged,) = [r.getMessage() for r in caplog.records if "HTTPSConnectionPool" in r.getMessage()]
    assert "ST-" not in logged and "AbCdEfGhIjKlMnOp" not in logged
    assert "connectapi.garmin.com" in logged and "Connection refused" in logged


# --- polling where an upload is processed -----------------------------------------------

def test_poll_location_network_error(monkeypatch):
    c = make_client(monkeypatch, unused_url())
    assert c.poll_location(c.connect_url("st/1"), 1, timeout_s=5) == 0


@pytest.mark.parametrize("whole_address", [False, True])
def test_a_failed_upload_status_check_logs_no_address(monkeypatch, caplog, whole_address):
    """The text of a requests error names the address, query and all (an upload's number, a
    ticket): its path after "with url:", or the whole address. The log line says the error
    without it, as every network error does."""
    from openexpress import applog

    c = make_client(monkeypatch, unused_url())
    url = c.connect_url("upload-service/upload/424242?ticket=made-up")
    if whole_address:
        def refused(address, **kw):
            raise requests.ConnectionError(f"Connection aborted while asking {address}")

        monkeypatch.setattr(c.http, "get", refused)
    with caplog.at_level(logging.WARNING, logger="openexpress"):
        assert c.poll_location(url, 1, timeout_s=5) == 0
    said = [applog.redact(r.getMessage()) for r in caplog.records  # as the log file has it
            if r.getMessage().startswith("Upload status check failed")]
    assert len(said) == 1 and "<address>" in said[0]
    assert "upload-service" not in said[0] and "424242" not in said[0]


def test_the_command_line_does_not_repeat_a_failed_upload_status_check(monkeypatch, capsys):
    """The sync says it itself: "Garmin Connect did not confirm processing of …"."""
    from openexpress import cli

    c = make_client(monkeypatch, unused_url())
    url = c.connect_url("upload-service/upload/424242?ticket=made-up")
    monkeypatch.setattr(cli, "cmd_info", lambda args: c.poll_location(url, 1, timeout_s=5))
    assert cli.main(["info"]) == 0
    assert "Upload status check failed" not in capsys.readouterr().err


def test_a_failed_upload_status_check_stays_in_the_log_file(monkeypatch, log_file):
    from openexpress import cli

    c = make_client(monkeypatch, unused_url())
    url = c.connect_url("upload-service/upload/424242?ticket=made-up")
    monkeypatch.setattr(cli, "cmd_info", lambda args: c.poll_location(url, 1, timeout_s=5))
    assert cli.main(["info"]) == 0
    assert log_file.read_text(encoding="utf-8").count("Upload status check failed") == 1


def test_poll_location_relative_and_clamped(monkeypatch, http_server):
    http_server.routes["/up"] = (202, {"Location": "/activity-service/st/1"}, b"{}")
    http_server.routes["/activity-service/st/1"] = (201, {}, b"")
    c = make_client(monkeypatch, http_server.url)
    res = c.upload_file(c.connect_url("up"), b"d", is_zip=False, last_file=True,
                        authenticated=True)
    assert res.location == http_server.url + "/activity-service/st/1"
    assert c.poll_location(res.location, 1) == 201

    http_server.routes["/activity-service/st/2"] = (202, {"location-in-milliseconds": "60000"}, b"")
    t = time.monotonic()
    assert c.poll_location(http_server.url + "/activity-service/st/2", 60000, timeout_s=0.3) == 202
    assert time.monotonic() - t < 3


# --- signing covers the URL that is actually sent -------------------------------------

def test_signature_uses_sent_url(monkeypatch, http_server):
    http_server.routes["/a%20b/~user"] = (200, {}, b"{}")
    c = make_client(monkeypatch, http_server.url)
    c._connect("GET", "a b/%7Euser", "X", params={"q": "x y"})
    method, path, headers = http_server.requests[-1]
    assert path.startswith("/a%20b/~user?")
    auth = headers["Authorization"]
    fields = dict(re.findall(r'(\w+)="([^"]*)"', auth))
    expected = authorization_header(
        "GET", http_server.url + path, "ckey", "csecret", "tok", "tsec",
        nonce=fields["oauth_nonce"], timestamp=int(fields["oauth_timestamp"]))
    assert auth == expected


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
