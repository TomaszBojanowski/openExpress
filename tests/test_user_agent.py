"""openExpress names itself to every server it asks with one User-Agent header,
"openexpress/<version> (Linux)" (the Garmin Connect API, uploads and their status, product
pictures, files of updates and maps), never with the HTTP library's ("python-requests/…"); only
the sign-in window, a web browser, has its own. The requests are caught before they leave; the
account, addresses and files are made up."""

import ast
import hashlib
import io
from pathlib import Path

import pytest
import requests

import openexpress
from openexpress import download
from openexpress.auth.store import ConnectAuth
from openexpress.connect import GarminClient
from openexpress.settings import Consumer, Settings

OURS = f"openexpress/{openexpress.__version__} (Linux)"
UNIT = 3_000_000_001  # made up


@pytest.fixture
def sent(monkeypatch):
    """Every request the HTTP library would send, answered here: (method, address, its
    User-Agent). The answer is made from the address."""
    seen = []

    def send(adapter, request, **kw):
        seen.append((request.method, request.url, request.headers.get("User-Agent")))
        resp = requests.Response()
        resp.request, resp.url = request, request.url
        body, resp.status_code = b"{}", 200
        resp.headers["Content-Type"] = "application/json"
        if "upload-service" in request.url and request.method == "POST":
            resp.status_code = 202
            resp.headers["Location"] = "/upload-service/upload/status/1"
        elif "upload/status" in request.url:
            resp.status_code = 201
        elif request.url.endswith(".png"):
            resp.status_code = 404
        elif request.url.endswith(".gcd"):
            body = b"made-up update"
            resp.headers["Content-Type"] = "application/octet-stream"
            resp.headers["Content-Length"] = str(len(body))
        resp.raw = io.BytesIO(body)
        return resp

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", send)
    return seen


def _client(session=None) -> GarminClient:
    settings = Settings.load()
    client = GarminClient(settings, Consumer("made-up-key", "made-up-secret"),
                          ConnectAuth("made-up-token", "made-up-token-secret",
                                      settings.environment), session=session)
    client.verified_unit_id = UNIT
    return client


def _ask_everything(client: GarminClient, tmp_path: Path, fetch_session) -> None:
    client.user_profile()
    up = client.upload_file("https://connectapi.garmin.com/upload-service/upload/.fit",
                            b"made-up activity", is_zip=False, last_file=True,
                            authenticated=True)
    client.poll_location(up.location, delay_ms=1, timeout_s=5)
    from openexpress.gui.pictures import download_picture

    download_picture(client.http, "https://res.garmin.com/p.png", "device-x.png")
    body = b"made-up update"
    download.fetch(["https://download.garmin.com/made-up/update.gcd"], tmp_path / "update.gcd",
                   len(body), hashlib.md5(body).hexdigest(), session=fetch_session)


def test_every_request_says_it_comes_from_openexpress(sent, tmp_path):
    client = _client()
    _ask_everything(client, tmp_path, fetch_session=None)  # a download with its own session
    assert [method for method, _url, _ua in sent] == ["GET", "POST", "GET", "GET", "GET"]
    assert {ua for _method, _url, ua in sent} == {OURS}, sent


def test_also_with_the_session_of_the_client(sent, tmp_path):
    """The window and the commands download updates and maps with the client's session."""
    client = _client()
    _ask_everything(client, tmp_path, fetch_session=client.http)
    assert len(sent) == 5 and {ua for _method, _url, ua in sent} == {OURS}, sent


def test_also_when_a_session_is_given(sent, tmp_path):
    """A session made elsewhere does not bring the library's header back."""
    client = _client(session=requests.Session())
    _ask_everything(client, tmp_path, fetch_session=requests.Session())
    assert len(sent) == 5 and {ua for _method, _url, ua in sent} == {OURS}, sent


def test_no_code_asks_with_a_session_of_the_library_s_own():
    """Every session of the program comes from openexpress.useragent, and no request goes
    through the library's own functions (requests.get and the like make a session without
    the header)."""
    root = Path(openexpress.__file__).parent
    found = []
    for path in sorted(root.rglob("*.py")):
        if path.name == "useragent.py":
            continue
        for node in ast.walk(ast.parse(path.read_text(), str(path))):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name) and node.func.value.id == "requests"
                    and node.func.attr in {"Session", "session", "get", "post", "put", "patch",
                                           "delete", "head", "options", "request"}):
                found.append(f"{path.relative_to(root)}:{node.lineno} requests.{node.func.attr}")
    assert found == []
