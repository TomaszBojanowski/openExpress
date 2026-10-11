"""Account operations must not touch a watch that the account does not list.

All IDs here are made up. UNIT is the test watch from conftest.DEVICE_XML.
"""

import json
import types

import pytest
import requests

from openexpress import ciq, cli, service, sync
from openexpress.auth import store
from openexpress.auth.store import ConnectAuth
from openexpress.cli import main
from openexpress.connect import (
    ClockSkew, DeviceNotVerified, GarminClient, NotAccountDevice, OwnershipUnknown, Unauthorized,
)
from openexpress.device.storage import LocalStorage
from openexpress.device.xmldevice import parse
from openexpress.settings import Consumer, Settings

UNIT = 1234567890
OTHER = 4222333444
DEVICES_PATH = "device-service/deviceregistration/devices"
NOT_REGISTERED = "not registered to your Garmin account"
COULD_NOT_CHECK = "Could not check whether this watch belongs"


def response(status=200, body=b"[]"):
    r = requests.Response()
    r.status_code = status
    r._content = body if isinstance(body, bytes) else json.dumps(body).encode()
    return r


class RecordingSession:
    """Stands in for requests.Session: records every request, answers from `routes`."""

    def __init__(self, devices=None, **routes):
        self.routes = {DEVICES_PATH: response(body=devices if devices is not None else [])}
        self.routes.update(routes)
        self.sent = []

    def request(self, method, url, **kw):
        self.sent.append({"method": method, "url": url, "headers": kw.get("headers") or {},
                          "body": repr((kw.get("data"), kw.get("json"), kw.get("files")))})
        for part, answer in self.routes.items():
            if part in url:
                if isinstance(answer, Exception):
                    raise answer
                return answer() if callable(answer) else answer
        return response(404, b"")

    def get(self, url, **kw):
        return self.request("GET", url, **kw)

    def signed_for(self, unit_id):
        """Token-signed requests that name this watch."""
        return [r for r in self.sent
                if "Authorization" in r["headers"] and str(unit_id) in r["url"] + r["body"]]

    def paths(self):
        return [r["url"].split("garmin.com/")[-1].split("?")[0] for r in self.sent]


def make_client(devices=None, auth=True, **routes):
    return GarminClient(Settings(), Consumer("ck", "cs"),
                        ConnectAuth("t", "ts") if auth else None,
                        RecordingSession(devices, **routes))


def tree(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


@pytest.fixture
def cli_client(monkeypatch):
    """The CLI talks to a recording client; `holder["client"]` can be replaced."""
    holder = {"client": make_client([{"unitid": OTHER}])}
    monkeypatch.setattr(cli, "make_client", lambda settings, need_auth: holder["client"])
    monkeypatch.setattr(cli, "GarminClient", lambda *a, **k: holder["client"])
    return holder


# --- the check ---------------------------------------------------------------------------


@pytest.mark.parametrize("value, expected", [
    (5, 5), ("5", 5), ("007", 7),
    (True, None), (False, None), (None, None), (5.0, None), ([5], None), ({"a": 1}, None),
    ("5a", None), ("", None), (" 5", None), ("-5", None), ("٥", None),
    (0, None), ("0", None), (-5, None),
])
def test_unit_id_normalization(value, expected):
    assert service._unit_id(value) == expected
    assert type(service._unit_id(value)) is type(expected)


@pytest.mark.parametrize("listed", [UNIT, str(UNIT)])
def test_own_watch_is_verified(listed):
    client = make_client([{"unitid": OTHER}, {"unitid": listed}])
    service.require_account_device(client, UNIT)
    assert client.verified_unit_id == UNIT
    assert client.http.paths() == [DEVICES_PATH]
    assert client.http.signed_for(UNIT) == []  # the list request does not name the watch


@pytest.mark.parametrize("devices", [
    [], [{"unitid": OTHER}], [{"unitid": None}], [{"serialnumber": str(UNIT)}],
    [{"unitid": float(UNIT)}], [{"unitid": [UNIT]}], [{"unitid": f"{UNIT}x"}],
])
def test_other_watch_is_refused(devices):
    client = make_client(devices)
    with pytest.raises(NotAccountDevice, match=NOT_REGISTERED):
        service.require_account_device(client, UNIT)
    assert client.verified_unit_id is None


def test_bool_never_matches_a_unit_id():
    client = make_client([{"unitid": True}])
    with pytest.raises(NotAccountDevice):
        service.require_account_device(client, 1)


def test_watch_without_an_id_is_refused():
    client = make_client([{"unitid": 0}, {"unitid": "000"}])
    with pytest.raises(NotAccountDevice):
        service.require_account_device(client, 0)


@pytest.mark.parametrize("answer", [
    requests.ConnectionError("no route"),
    requests.Timeout("slow"),
    response(500, b"oops"),
    response(200, b"<html>login</html>"),
    response(200, {"devices": [{"unitid": UNIT}]}),
    response(200, [UNIT]),
    response(200, b"null"),
], ids=["connection", "timeout", "http500", "not-json", "dict", "list-of-ints", "null"])
def test_unreadable_list_is_refused(answer):
    client = make_client(**{DEVICES_PATH: answer})
    with pytest.raises(OwnershipUnknown, match=COULD_NOT_CHECK):
        service.require_account_device(client, UNIT)
    assert client.verified_unit_id is None


def test_not_logged_in_is_refused_without_a_request():
    client = make_client([{"unitid": UNIT}], auth=False)
    with pytest.raises(Unauthorized, match="openexpress login"):
        service.require_account_device(client, UNIT)
    assert client.http.sent == [] and client.verified_unit_id is None


@pytest.mark.parametrize("status", [401, 403])
def test_rejected_login_is_reported_as_such(status):
    client = make_client(**{DEVICES_PATH: response(status, b"")})
    client.verified_unit_id = UNIT
    with pytest.raises(Unauthorized, match="Please log in again"):
        service.require_account_device(client, UNIT)
    assert client.verified_unit_id is None


def test_wrong_clock_is_reported_as_such():
    answer = response(401, b"")
    answer.headers["Date"] = "Mon, 01 Jan 2001 00:00:00 GMT"
    client = make_client(**{DEVICES_PATH: answer})
    client.verified_unit_id = UNIT
    with pytest.raises(ClockSkew, match="computer clock is off"):
        service.require_account_device(client, UNIT)
    assert client.verified_unit_id is None


@pytest.mark.parametrize("failure, error", [
    (requests.ConnectionError("gone"), OwnershipUnknown),
    (response(body=[{"unitid": OTHER}]), NotAccountDevice),
    (response(401, b""), Unauthorized),
    (response(200, b"<html>"), OwnershipUnknown),
], ids=["network", "not-listed", "http401", "not-json"])
def test_recheck_keeps_the_verification_until_the_answer(failure, error):
    client = make_client([{"unitid": UNIT}])
    service.require_account_device(client, UNIT)
    during = []

    def answer():
        during.append(client.verified_unit_id)
        if isinstance(failure, Exception):
            raise failure
        return failure

    client.http.routes[DEVICES_PATH] = answer
    with pytest.raises(error):
        service.require_account_device(client, UNIT)
    assert during == [UNIT]  # background tasks sharing the client are not interrupted
    assert client.verified_unit_id is None  # but a failed check revokes the earlier one


def test_check_of_another_watch_revokes_at_once():
    client = make_client([{"unitid": UNIT}, {"unitid": OTHER}])
    service.require_account_device(client, UNIT)
    during = []
    ok = client.http.routes[DEVICES_PATH]
    client.http.routes[DEVICES_PATH] = lambda: during.append(client.verified_unit_id) or ok
    service.require_account_device(client, OTHER)
    assert during == [None]
    assert client.verified_unit_id == OTHER


def test_unexpected_error_revokes_too():
    client = make_client([{"unitid": UNIT}])
    service.require_account_device(client, UNIT)
    client.account_devices = lambda: 1 / 0
    with pytest.raises(ZeroDivisionError):
        service.require_account_device(client, UNIT)
    assert client.verified_unit_id is None


def test_every_check_asks_the_server_again():
    client = make_client([{"unitid": UNIT}])
    service.require_account_device(client, UNIT)
    service.require_account_device(client, UNIT)
    assert client.http.paths() == [DEVICES_PATH, DEVICES_PATH]


# --- defense in depth: the client refuses by itself -----------------------------------------


def account_calls(client, device):
    api = ciq.StoreApi(client)
    return {
        "device_messages": lambda: client.device_messages(device.unit_id),
        "activity_matches": lambda: client.activity_matches(device.unit_id, ["1"]),
        "activity_matches_empty": lambda: client.activity_matches(device.unit_id, []),
        "device_image_url": lambda: client.device_image_url(device.unit_id),
        "download_message": lambda: client.download_message("x/1", absolute=False),
        "ack_message": lambda: client.ack_message(1),
        "upload_signed": lambda: client.upload_file(
            "https://up.example/a", b"d", is_zip=False, last_file=True, authenticated=True),
        "upload_unsigned": lambda: client.upload_file(
            "https://up.example/a", b"d", is_zip=False, last_file=True, authenticated=False),
        "poll_location": lambda: client.poll_location("https://up.example/st/1", 1, timeout_s=1),
        "store_account_apps": lambda: api.account_apps(device, "en-US"),
        "store_updates": lambda: api.updates(device, [], "en-US"),
        "store_queue_install": lambda: api.queue_install(device, []),
        "store_forget": lambda: api.forget(device, []),
        "store_icon_urls": lambda: api.icon_urls([ciq.uuid.uuid4()]),
    }


CALL_NAMES = sorted(account_calls(None, types.SimpleNamespace(unit_id=0)))
NEEDS_THIS_WATCH = {"device_messages", "activity_matches", "activity_matches_empty",
                    "device_image_url", "store_account_apps", "store_updates",
                    "store_queue_install", "store_forget"}


@pytest.fixture
def device(device_root):
    return parse(LocalStorage(device_root).read("GARMIN/GarminDevice.xml"))


@pytest.mark.parametrize("name", CALL_NAMES)
def test_unverified_client_sends_nothing(device, name):
    client = make_client([{"unitid": UNIT}])
    with pytest.raises(DeviceNotVerified, match="Nothing was sent"):
        account_calls(client, device)[name]()
    assert client.http.sent == []


@pytest.mark.parametrize("name", sorted(NEEDS_THIS_WATCH))
def test_client_verified_for_another_watch_sends_nothing(device, name):
    client = make_client()
    client.verified_unit_id = OTHER
    with pytest.raises(DeviceNotVerified):
        account_calls(client, device)[name]()
    assert client.http.sent == []


def test_baseline_needs_the_verified_watch(device_root, device, tmp_path):
    st = LocalStorage(device_root)
    state = sync.SyncState(device.unit_id, base=tmp_path / "state")
    for verified in (None, OTHER):
        client = make_client()
        client.verified_unit_id = verified
        with pytest.raises(DeviceNotVerified):
            sync.mark_baseline(device, st, {"datatypes": []}, state, client)
    assert not state.file.exists() and not (tmp_path / "state").exists()


# --- someone else's watch: nothing is sent, nothing is written ------------------------------


@pytest.mark.parametrize("argv", [
    ["sync"], ["sync", "--apply"], ["sync", "--apply", "--all"], ["sync", "--download-only"],
    ["sync", "--apply", "--download-only"], ["baseline"], ["baseline", "--apply"],
    ["apps"], ["apps", "update", "--apply"], ["apps", "install", "Face", "--apply"],
    ["apps", "remove", "Face", "--forget", "--apply"],
], ids=" ".join)
def test_cli_refuses_other_watch(device_root, xdg, cli_client, capsys, argv):
    before = tree(device_root)
    assert main(["--path", str(device_root), *argv]) == 1
    err = capsys.readouterr().err
    assert NOT_REGISTERED in err and "Traceback" not in err
    http = cli_client["client"].http
    assert http.paths() == [DEVICES_PATH]  # the only request; it does not name the watch
    assert http.signed_for(UNIT) == []
    assert tree(device_root) == before
    assert not sync.SyncState(UNIT).dir.exists()
    assert not service.last_sync_path().exists()


@pytest.mark.parametrize("argv", [["sync", "--apply", "--all"], ["baseline", "--apply"], ["apps"]],
                         ids=" ".join)
def test_cli_refuses_when_the_list_is_unavailable(device_root, xdg, cli_client, capsys, argv):
    cli_client["client"] = make_client(**{DEVICES_PATH: requests.ConnectionError("offline")})
    before = tree(device_root)
    assert main(["--path", str(device_root), *argv]) == 1
    err = capsys.readouterr().err
    assert COULD_NOT_CHECK in err and NOT_REGISTERED not in err
    assert cli_client["client"].http.signed_for(UNIT) == []
    assert tree(device_root) == before
    assert not sync.SyncState(UNIT).dir.exists()


@pytest.mark.parametrize("argv", [["sync", "--apply", "--all"], ["baseline", "--apply"], ["apps"]],
                         ids=" ".join)
def test_cli_asks_to_log_in_again_when_the_login_is_rejected(device_root, xdg, cli_client,
                                                             capsys, argv):
    cli_client["client"] = make_client(**{DEVICES_PATH: response(401, b"")})
    before = tree(device_root)
    assert main(["--path", str(device_root), *argv]) == 1
    err = capsys.readouterr().err
    assert "Please log in again" in err and COULD_NOT_CHECK not in err
    assert cli_client["client"].http.paths() == [DEVICES_PATH]
    assert tree(device_root) == before and not sync.SyncState(UNIT).dir.exists()


def test_cli_sync_works_for_own_watch(device_root, xdg, cli_client, capsys):
    config = {"datatypes": [{"datatypename": "FIT_TYPE_4", "uploadlocations": [
        {"url": "https://up.example/activity", "requiresauthentication": True}]}]}
    cli_client["client"] = make_client(
        [{"unitid": UNIT}],
        UnitUploadSettings=response(body=config),
        **{"devicemessage/messages": response(body={"messages": []})})
    assert main(["--path", str(device_root), "sync"]) == 0
    assert "To upload: 2 files" in capsys.readouterr().out
    http = cli_client["client"].http
    assert http.paths()[0] == DEVICES_PATH
    assert len(http.signed_for(UNIT)) == 1  # the Connect file queue


def test_run_sync_refuses_other_watch(device_root, xdg, device):
    client = make_client([{"unitid": OTHER}])
    s = service.DeviceSession(LocalStorage(device_root), device, Settings(), client)
    before = tree(device_root)
    for first_run in (None, "baseline", "all"):
        with pytest.raises(NotAccountDevice):
            service.run_sync(s, first_run)
    assert set(client.http.paths()) == {DEVICES_PATH}
    assert tree(device_root) == before
    assert not s.state.dir.exists() and not service.last_sync_path().exists()


# --- features without the account are not affected --------------------------------------------


def test_commands_without_the_account_do_not_check(device_root, xdg, monkeypatch, tmp_path, capsys):
    client = make_client(
        [{"unitid": OTHER}], auth=False,
        UnitUploadSettings=response(body={"unittypename": "fitness", "datatypes": []}),
        **{"api/": response(body={})})

    def no_login(settings, need_auth):
        if need_auth:
            raise store.NotLoggedIn("Not logged in.")
        return client

    monkeypatch.setattr(cli, "make_client", no_login)
    monkeypatch.setattr(cli, "GarminClient", lambda *a, **k: client)
    for argv in (["info"], ["files"], ["report"], ["backup", "--apply", "--dest", str(tmp_path / "b")],
                 ["upload-config"], ["sync"], ["updates"], ["maps"], ["music"]):
        assert main(["--path", str(device_root), *argv]) == 0, argv
    assert "Device type: fitness" in capsys.readouterr().out
    assert DEVICES_PATH not in client.http.paths()
    assert all("Authorization" not in r["headers"] for r in client.http.sent)
    assert client.verified_unit_id is None


# --- background watcher ---------------------------------------------------------------------


@pytest.fixture
def watcher(monkeypatch):
    from openexpress import watch

    monkeypatch.setattr(watch.GLib, "idle_add", lambda f, *a: f(*a))
    w = watch.Watcher()
    w.notes = []
    monkeypatch.setattr(w, "notify", lambda title, body: w.notes.append((title, body)))
    w.synced = []
    monkeypatch.setattr(watch.service, "run_sync",
                        lambda s, **kw: w.synced.append(s) or service._run_sync(s, None, kw["progress"], False))
    return w


def session(device_root, device, client):
    return service.DeviceSession(LocalStorage(device_root), device, Settings(), client)


def test_watcher_other_watch_gets_one_notification(device_root, xdg, device, watcher, monkeypatch):
    client = make_client([{"unitid": OTHER}])
    monkeypatch.setattr(watcher, "_open_session", lambda: session(device_root, device, client))
    before = tree(device_root)
    watcher._sync()
    assert len(watcher.notes) == 1
    title, body = watcher.notes[0]
    assert title == "Garmin: this watch was not synced" and NOT_REGISTERED in body
    assert watcher.synced == [] and client.http.paths() == [DEVICES_PATH]
    assert tree(device_root) == before and not sync.SyncState(UNIT).dir.exists()


def test_watcher_unavailable_list_gets_one_notification(device_root, xdg, device, watcher,
                                                        monkeypatch):
    client = make_client(**{DEVICES_PATH: requests.ConnectionError("offline")})
    monkeypatch.setattr(watcher, "_open_session", lambda: session(device_root, device, client))
    watcher._sync()
    assert [t for t, _b in watcher.notes] == ["Garmin: this watch was not synced"]
    assert COULD_NOT_CHECK in watcher.notes[0][1] and watcher.synced == []


def test_watcher_own_new_watch_still_asks_for_the_first_sync(device_root, xdg, device, watcher,
                                                             monkeypatch):
    client = make_client([{"unitid": UNIT}])
    monkeypatch.setattr(watcher, "_open_session", lambda: session(device_root, device, client))
    watcher._sync()
    assert [t for t, _b in watcher.notes] == ["Garmin: first sync required"]
    assert watcher.synced == []


def test_watcher_other_watch_with_old_state_is_not_synced(device_root, xdg, device, watcher,
                                                          monkeypatch):
    # E.g. a baseline made by a version without the check.
    sync.SyncState(UNIT).save()
    client = make_client([{"unitid": OTHER}])
    monkeypatch.setattr(watcher, "_open_session", lambda: session(device_root, device, client))
    before = tree(device_root)
    watcher._sync()
    assert [t for t, _b in watcher.notes] == ["Garmin: this watch was not synced"]
    assert watcher.synced == []
    assert client.http.paths() == [DEVICES_PATH] and tree(device_root) == before


def test_watcher_rejected_login_gets_one_notification(device_root, xdg, device, watcher,
                                                      monkeypatch):
    client = make_client(**{DEVICES_PATH: response(401, b"")})
    monkeypatch.setattr(watcher, "_open_session", lambda: session(device_root, device, client))
    watcher._sync()
    assert len(watcher.notes) == 1 and watcher.synced == []
    assert watcher.notes[0][0] == "Sync failed" and "Please log in again" in watcher.notes[0][1]


# --- window ---------------------------------------------------------------------------------


def test_check_session_keeps_the_refusal(device_root, device):
    s = service.check_session(session(device_root, device, make_client([{"unitid": OTHER}])))
    assert isinstance(s.ownership_error, NotAccountDevice)
    s = service.check_session(session(device_root, device, make_client([{"unitid": UNIT}])))
    assert s.ownership_error is None and s.client.verified_unit_id == UNIT
    logged_out = make_client([{"unitid": UNIT}], auth=False)
    s = service.check_session(session(device_root, device, logged_out))
    assert s.ownership_error is None and logged_out.http.sent == []


def banner_for(s):
    from openexpress.gui.app import MainWindow

    seen = {}
    win = types.SimpleNamespace(session=s, banner=types.SimpleNamespace(
        set_title=lambda t: seen.update(title=t),
        set_button_label=lambda b: seen.update(button=b),
        set_revealed=lambda r: seen.update(revealed=r)))
    MainWindow._update_banner(win)
    seen["kind"] = getattr(win, "_banner_kind", None)
    return seen


def test_window_banner_for_each_outcome(device_root, device):
    def checked(**routes):
        client = make_client([{"unitid": UNIT}], **routes)
        return service.check_session(session(device_root, device, client))

    assert banner_for(checked()) == {"revealed": False, "kind": None}

    s = checked(**{DEVICES_PATH: response(body=[{"unitid": OTHER}])})
    assert banner_for(s) == {"title": "This watch is not registered to your Garmin account",
                             "button": None, "revealed": True, "kind": "recheck"}

    s = checked(**{DEVICES_PATH: requests.ConnectionError("offline")})
    seen = banner_for(s)
    assert seen["button"] == "Try Again" and seen["kind"] == "recheck"

    # A rejected login: the usual "log in" banner, not "Try Again".
    s = checked(**{DEVICES_PATH: response(401, b"")})
    assert not s.logged_in and s.ownership_error is None and s.client.verified_unit_id is None
    assert banner_for(s) == {"title": "Not logged in to Garmin Connect", "button": "Log In",
                             "revealed": True, "kind": "login"}

    # A wrong clock: the clock message, and a way to check again.
    skewed = response(401, b"")
    skewed.headers["Date"] = "Mon, 01 Jan 2001 00:00:00 GMT"
    s = checked(**{DEVICES_PATH: skewed})
    assert s.logged_in and isinstance(s.ownership_error, ClockSkew)
    assert banner_for(s)["button"] == "Try Again"


def test_window_shows_the_refusal_instead_of_account_features(device_root, device):
    from openexpress.gui.app import MainWindow

    client = make_client([{"unitid": OTHER}])
    s = service.check_session(session(device_root, device, client))
    shown = []
    section = types.SimpleNamespace(
        clear=lambda: None, show=shown.append,
        error=types.SimpleNamespace(set_title=lambda t: None,
                                    set_description=lambda d: shown.append(d)))
    started = []
    win = types.SimpleNamespace(
        session=s, busy=False, sections={"apps": section},
        _load=lambda *a: started.append("load"), run_task=lambda *a: started.append("task"),
        in_background=lambda *a: started.append("background"),
        device_image=types.SimpleNamespace(set_visible=lambda v: None),
        device_picture=types.SimpleNamespace(set_visible=lambda v: None),
        _device_textures={})
    MainWindow.load_apps(win)
    MainWindow.start_sync(win)
    MainWindow._show_device_picture(win)
    MainWindow._load_app_icons(win, {"x": object()})
    assert started == []
    assert shown == [str(s.ownership_error), "error"] and NOT_REGISTERED in shown[0]
    assert client.http.paths() == [DEVICES_PATH]
