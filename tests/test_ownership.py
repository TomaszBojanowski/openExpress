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
    ApiError,
    ClockSkew,
    DeviceNotVerified,
    GarminClient,
    NotAccountDevice,
    OwnershipUnknown,
    Unauthorized,
)
from openexpress.device.storage import LocalStorage
from openexpress.device.xmldevice import parse
from openexpress.settings import Consumer, Settings

UNIT = 1234567890
OTHER = 4222333444
DEVICES_PATH = "device-service/deviceregistration/devices"
NOT_REGISTERED = "not registered to your Garmin account"
COULD_NOT_CHECK = "Could not check whether this device belongs"


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
        self.handlers = {}  # url part -> function(method, request kwargs) -> response
        self.sent = []

    def request(self, method, url, **kw):
        self.sent.append({"method": method, "url": url, "headers": kw.get("headers") or {},
                          "body": repr((kw.get("data"), kw.get("json"), kw.get("files")))})
        for part, handler in self.handlers.items():
            if part in url:
                return handler(method, kw)
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
        "primary_training_device": lambda: client.primary_training_device(),
        "set_primary_training_device": lambda: client.set_primary_training_device(device.unit_id),
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
NEEDS_THIS_WATCH = {"set_primary_training_device", "device_messages", "activity_matches", "activity_matches_empty",
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
    assert title == "Garmin: this device was not synced" and NOT_REGISTERED in body
    assert watcher.synced == [] and client.http.paths() == [DEVICES_PATH]
    assert tree(device_root) == before and not sync.SyncState(UNIT).dir.exists()


def test_watcher_unavailable_list_gets_one_notification(device_root, xdg, device, watcher,
                                                        monkeypatch):
    client = make_client(**{DEVICES_PATH: requests.ConnectionError("offline")})
    monkeypatch.setattr(watcher, "_open_session", lambda: session(device_root, device, client))
    watcher._sync()
    assert [t for t, _b in watcher.notes] == ["Garmin: this device was not synced"]
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
    assert [t for t, _b in watcher.notes] == ["Garmin: this device was not synced"]
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
    win = types.SimpleNamespace(session=s, _ownership_title=MainWindow._ownership_title,
                                banner=types.SimpleNamespace(
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
    assert banner_for(s) == {"title": "This device is not registered to your Garmin account",
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
    MainWindow._load_app_icons(win, {"x": object()})
    assert started == []
    # The picture is the one thing still looked for, in the background and not on the account
    # (tests/test_fixes_ui.py: the device picture).
    MainWindow._show_device_picture(win)
    assert started == ["background"]
    assert shown == [str(s.ownership_error), "error"] and NOT_REGISTERED in shown[0]
    assert client.http.paths() == [DEVICES_PATH]


# --- primary training device (read-only) -----------------------------------------------------

PTD_PATH = "userprofile-service/userprofile/user-settings/primary-training-device"
ACCOUNT = [{"unitid": UNIT, "productdisplayname": "Test watch", "primarytrainingcapable": True},
           {"unitid": OTHER, "productdisplayname": "Bike computer", "primarytrainingcapable": False}]


def ptd_client(answer, devices=ACCOUNT):
    return make_client(devices, **{PTD_PATH: answer})


def test_primary_training_is_this_device(device):
    client = ptd_client(response(body={"deviceId": UNIT}))
    status = service.primary_training_status(client, device)
    assert status == service.PrimaryTraining(UNIT, "Test watch", True, True)
    http = client.http
    assert http.paths() == [DEVICES_PATH, PTD_PATH]  # ownership first, then one read
    assert [r["method"] for r in http.sent] == ["GET", "GET"]
    assert "ignoreCache=false" in http.sent[1]["url"] and "Authorization" in http.sent[1]["headers"]
    assert http.signed_for(UNIT) == []  # the request does not name the device


def test_primary_training_is_another_device(device):
    status = service.primary_training_status(ptd_client(response(body={"deviceId": str(OTHER)})),
                                             device)
    assert status == service.PrimaryTraining(OTHER, "Bike computer", False, True)


def test_primary_training_device_not_on_the_account_list(device):
    status = service.primary_training_status(ptd_client(response(body={"deviceId": 777000111})),
                                             device)
    assert (status.unit_id, status.name, status.is_this_device) == (777000111, "", False)


@pytest.mark.parametrize("answer", [
    response(body={}), response(body={"deviceId": None}), response(body={"deviceId": 0}),
    response(200, b"null"), response(200, b""), response(204, b""), response(200, b"  "),
], ids=["no-field", "null-field", "zero", "null", "empty", "http204", "blank"])
def test_primary_training_not_set(device, answer):
    status = service.primary_training_status(ptd_client(answer), device)
    assert status == service.PrimaryTraining(None, "", False, True)


@pytest.mark.parametrize("answer", [
    response(body=[UNIT]), response(body={"deviceId": "abc"}), response(body={"deviceId": True}),
    response(body={"deviceId": {"id": UNIT}}), response(200, b"<html>"), response(500, b"oops"),
    response(404, b""), requests.ConnectionError("offline"),
], ids=["list", "text-id", "bool-id", "object-id", "not-json", "http500", "http404", "network"])
def test_primary_training_unreadable_answer_is_an_error(device, answer):
    with pytest.raises(ApiError):
        service.primary_training_status(ptd_client(answer), device)


@pytest.mark.parametrize("flag, expected", [(True, True), (False, False), ("yes", None),
                                            (None, None)])
def test_primary_training_capable_flag(device, flag, expected):
    devices = [{"unitid": UNIT, "primarytrainingcapable": flag}]
    client = ptd_client(response(body={"deviceId": OTHER}), devices)
    assert service.primary_training_status(client, device).capable is expected


def test_primary_training_refuses_other_device(device):
    client = ptd_client(response(body={"deviceId": UNIT}), [{"unitid": OTHER}])
    with pytest.raises(NotAccountDevice):
        service.primary_training_status(client, device)
    assert client.http.paths() == [DEVICES_PATH]  # the setting is not even read


def test_primary_training_with_a_list_from_the_window_still_needs_the_check(device):
    client = ptd_client(response(body={"deviceId": UNIT}))
    with pytest.raises(DeviceNotVerified):
        service.primary_training_status(client, device, ACCOUNT)
    assert client.http.sent == []
    service.require_account_device(client, UNIT)
    assert service.primary_training_status(client, device, ACCOUNT).is_this_device
    assert client.http.paths() == [DEVICES_PATH, PTD_PATH]


@pytest.mark.parametrize("answer, lines", [
    ({"deviceId": UNIT}, ["Primary training device: Test watch (this device)"]),
    ({"deviceId": OTHER}, ["Primary training device: Bike computer",
                           "The connected device (Test watch) is not the primary training device."]),
    ({"deviceId": 777000111}, ["Primary training device: a device that is not on the account's list",
                               "The connected device (Test watch) is not the primary training device."]),
    ({}, ["No primary training device is set for this Garmin account."]),
], ids=["this", "other", "unlisted", "unset"])
def test_cli_primary(device_root, xdg, cli_client, capsys, answer, lines):
    cli_client["client"] = ptd_client(response(body=answer))
    before = tree(device_root)
    assert main(["--path", str(device_root), "primary"]) == 0
    assert capsys.readouterr().out.splitlines() == lines
    http = cli_client["client"].http
    assert http.paths() == [DEVICES_PATH, PTD_PATH]
    assert {r["method"] for r in http.sent} == {"GET"}  # nothing is changed on the account
    assert tree(device_root) == before


def test_cli_primary_says_when_the_device_cannot_be_one(device_root, xdg, cli_client, capsys):
    devices = [{"unitid": UNIT, "productdisplayname": "Test watch", "primarytrainingcapable": False}]
    cli_client["client"] = ptd_client(response(body={}), devices)
    assert main(["--path", str(device_root), "primary"]) == 0
    assert capsys.readouterr().out.splitlines()[-1] == \
        "The connected device cannot be a primary training device."


def test_cli_primary_refuses_other_device(device_root, xdg, cli_client, capsys):
    cli_client["client"] = ptd_client(response(body={"deviceId": OTHER}), [{"unitid": OTHER}])
    assert main(["--path", str(device_root), "primary"]) == 1
    assert NOT_REGISTERED in capsys.readouterr().err
    assert cli_client["client"].http.paths() == [DEVICES_PATH]


def test_window_primary_training_row(device_root, device):
    from openexpress.gui.app import MainWindow

    text = MainWindow._primary_training_text
    assert text(service.PrimaryTraining(None, "", False, None)) == "Not set"
    assert text(service.PrimaryTraining(UNIT, "Test watch", True, True)) == "This device"
    assert text(service.PrimaryTraining(OTHER, "Bike computer", False, True)) == "Bike computer"
    assert text(service.PrimaryTraining(OTHER, "", False, True)) == "Another device"

    def window(client):
        s = service.check_session(session(device_root, device, client))
        assert s.account_devices is not None
        win = types.SimpleNamespace(session=s, _primary_training_text=text)

        def run_now(fn, on_done, on_error=None):
            try:
                result = fn()
            except Exception as e:  # noqa: BLE001
                on_error(e)
            else:
                on_done(result)

        win.in_background = run_now
        return win

    shown, buttons = [], []
    row = types.SimpleNamespace(set_subtitle=shown.append, add_suffix=buttons.append)

    plain_window = window

    def window_with_button(client):
        win = plain_window(client)
        win._button = lambda label, cb: label
        return win

    window = window_with_button

    client = ptd_client(response(body={"deviceId": OTHER}))
    MainWindow._show_primary_training(window(client), row)
    assert shown == ["Bike computer"]
    assert buttons == ["Set as Primary"]  # another device is set and this one is capable
    # One ownership check when the device was detected, then one read: no second list request.
    assert client.http.paths() == [DEVICES_PATH, PTD_PATH]
    assert {r["method"] for r in client.http.sent} == {"GET"}

    MainWindow._show_primary_training(window(ptd_client(requests.ConnectionError("offline"))), row)
    assert shown[-1] == "Could not check"

    # No button when this device is already set, or when it cannot (or may not) be one.
    buttons.clear()
    MainWindow._show_primary_training(window(ptd_client(response(body={"deviceId": UNIT}))), row)
    for flag in (False, None):
        devices = [{"unitid": UNIT, "primarytrainingcapable": flag}, ACCOUNT[1]]
        win = window(ptd_client(response(body={"deviceId": OTHER}), devices))
        win.session.account_devices = devices
        MainWindow._show_primary_training(win, row)
    assert buttons == []


def test_check_session_forgets_the_list_on_refusal(device_root, device):
    s = service.check_session(session(device_root, device, make_client([{"unitid": OTHER}])))
    assert s.ownership_error is not None and s.account_devices is None


# --- primary training device: changing it -----------------------------------------------------


class FakeAccount:
    """Garmin's side of the setting: answers GET with the current value, applies PUT."""

    def __init__(self, current, devices=ACCOUNT, put_status=200, applies=True):
        self.current, self.put_status, self.applies = current, put_status, applies
        self.puts = []
        self.client = make_client(devices)
        self.client.http.handlers[PTD_PATH] = self.handle

    def handle(self, method, kw):
        if method == "GET":
            return response(body={"deviceId": self.current} if self.current else {})
        assert method == "PUT"
        self.puts.append((kw["data"], kw["headers"].get("Content-Type"),
                          "Authorization" in kw["headers"]))
        if isinstance(self.put_status, Exception):
            raise self.put_status
        if self.put_status == 200 and self.applies:
            self.current = json.loads(kw["data"])["deviceId"]
        return response(self.put_status, b"")

    @property
    def methods(self):
        return [r["method"] for r in self.client.http.sent]


def test_set_primary_training_dry_run_changes_nothing(device):
    account = FakeAccount(OTHER)
    before = service.set_primary_training(account.client, device)
    assert before == service.PrimaryTraining(OTHER, "Bike computer", False, True)
    assert account.puts == [] and account.current == OTHER
    assert account.methods == ["GET", "GET"]  # device list, current setting


def test_set_primary_training_apply(device):
    account = FakeAccount(OTHER)
    before = service.set_primary_training(account.client, device, apply=True)
    assert before.unit_id == OTHER and account.current == UNIT
    # One PUT: a JSON number, signed; nothing in the URL.
    assert account.puts == [(json.dumps({"deviceId": UNIT}), "application/json", True)]
    assert account.methods == ["GET", "GET", "PUT", "GET"]  # …then the setting is read back
    put = account.client.http.sent[2]
    assert put["url"].endswith(PTD_PATH) and "ignoreCache" not in put["url"]


def test_set_primary_training_when_none_was_set(device):
    account = FakeAccount(None)
    before = service.set_primary_training(account.client, device, apply=True)
    assert before.unit_id is None and account.current == UNIT


@pytest.mark.parametrize("apply", [False, True])
def test_set_primary_training_already_this_device(device, apply):
    account = FakeAccount(UNIT)
    assert service.set_primary_training(account.client, device, apply=apply).is_this_device
    assert account.puts == [] and account.methods == ["GET", "GET"]


@pytest.mark.parametrize("flag, message", [
    (False, "cannot be a primary training device"),
    (None, "Could not tell whether"), ("true", "Could not tell whether"), (1, "Could not tell"),
])
@pytest.mark.parametrize("apply", [False, True])
def test_set_primary_training_refuses_without_the_capability(device, flag, message, apply):
    devices = [{"unitid": UNIT, "productdisplayname": "Test watch", "primarytrainingcapable": flag},
               ACCOUNT[1]]
    account = FakeAccount(OTHER, devices)
    with pytest.raises(service.PrimaryTrainingError, match=message):
        service.set_primary_training(account.client, device, apply=apply)
    assert account.puts == [] and account.current == OTHER


def test_set_primary_training_refuses_a_device_without_the_flag_entry(device):
    account = FakeAccount(OTHER, [{"unitid": UNIT}, ACCOUNT[1]])
    with pytest.raises(service.PrimaryTrainingError, match="Could not tell"):
        service.set_primary_training(account.client, device, apply=True)
    assert account.puts == []


def test_set_primary_training_refuses_other_account(device):
    account = FakeAccount(OTHER, [{"unitid": OTHER, "primarytrainingcapable": True}])
    with pytest.raises(NotAccountDevice):
        service.set_primary_training(account.client, device, apply=True)
    assert account.methods == ["GET"] and account.puts == []  # only the device list


@pytest.mark.parametrize("failure", [500, 403, 404, requests.ConnectionError("offline")],
                         ids=["http500", "http403", "http404", "network"])
def test_set_primary_training_failed_request_is_an_error(device, failure):
    account = FakeAccount(OTHER, put_status=failure)
    with pytest.raises(ApiError):
        service.set_primary_training(account.client, device, apply=True)
    assert account.current == OTHER and len(account.puts) == 1


def test_set_primary_training_accepted_but_not_applied_is_an_error(device):
    account = FakeAccount(OTHER, applies=False)
    with pytest.raises(service.PrimaryTrainingError, match="did not change"):
        service.set_primary_training(account.client, device, apply=True)
    assert account.methods == ["GET", "GET", "PUT", "GET"]


def test_set_primary_training_checks_ownership_anew(device):
    account = FakeAccount(OTHER)
    service.require_account_device(account.client, UNIT)  # e.g. when the device was detected
    account.client.http.routes[DEVICES_PATH] = response(body=[ACCOUNT[1]])  # removed since
    with pytest.raises(NotAccountDevice):
        service.set_primary_training(account.client, device, apply=True)
    assert account.puts == [] and account.client.verified_unit_id is None


@pytest.mark.parametrize("argv, current, lines, puts", [
    (["primary", "set"], OTHER,
     ["Primary training device: Bike computer", "Would change it to: Test watch",
      "Dry run. Add --apply."], 0),
    (["primary", "set"], None,
     ["No primary training device is set for this Garmin account.",
      "Would change it to: Test watch", "Dry run. Add --apply."], 0),
    (["primary", "set", "--apply"], OTHER,
     ["Primary training device changed from Bike computer to Test watch.",
      ("To change it back, connect Bike computer and run `openexpress primary set --apply`, "
       "or change it in Garmin Connect.")], 1),
    (["primary", "set", "--apply"], None, ["Primary training device set to Test watch."], 1),
    (["primary", "set", "--apply"], UNIT,
     ["Test watch is already the primary training device."], 0),
    (["primary", "set"], UNIT, ["Test watch is already the primary training device."], 0),
], ids=["dry", "dry-unset", "apply", "apply-unset", "apply-already", "dry-already"])
def test_cli_primary_set(device_root, xdg, cli_client, capsys, argv, current, lines, puts):
    account = FakeAccount(current)
    cli_client["client"] = account.client
    before = tree(device_root)
    assert main(["--path", str(device_root), *argv]) == 0
    assert capsys.readouterr().out.splitlines() == lines
    assert len(account.puts) == puts
    assert tree(device_root) == before  # nothing is ever written to the device


@pytest.mark.parametrize("argv", [["primary", "set"], ["primary", "set", "--apply"]], ids=" ".join)
def test_cli_primary_set_refusals(device_root, xdg, cli_client, capsys, argv):
    devices = [{"unitid": UNIT, "primarytrainingcapable": False}, ACCOUNT[1]]
    account = FakeAccount(OTHER, devices)
    cli_client["client"] = account.client
    assert main(["--path", str(device_root), *argv]) == 1
    err = capsys.readouterr().err
    assert "Test watch cannot be a primary training device." in err and "Traceback" not in err

    account = FakeAccount(OTHER, [ACCOUNT[1]])  # a device of another account
    cli_client["client"] = account.client
    assert main(["--path", str(device_root), *argv]) == 1
    assert NOT_REGISTERED in capsys.readouterr().err and account.puts == []


def test_cli_primary_without_set_never_writes(device_root, xdg, cli_client, capsys):
    account = FakeAccount(OTHER)
    cli_client["client"] = account.client
    assert main(["--path", str(device_root), "primary"]) == 0
    assert account.puts == [] and set(account.methods) == {"GET"}


def test_window_set_primary_training(device_root, device):
    from openexpress.gui.app import MainWindow

    account = FakeAccount(OTHER)
    s = service.check_session(session(device_root, device, account.client))
    events = []

    def run_task(title, fn, on_done=None, on_error=None, device=True):
        events.append(title)
        assert device is False  # an account setting: the device may be unplugged
        on_done(fn())

    win = types.SimpleNamespace(session=s, run_task=run_task, toast=events.append,
                                show_overview=lambda: events.append("overview"))
    MainWindow.set_primary_training(win)
    assert account.current == UNIT and len(account.puts) == 1
    assert events == ["Changing the primary training device…",
                      "Primary training device changed", "overview"]
    # The window checks ownership anew for the change: list at detection, list again, …
    assert account.client.http.paths()[:2] == [DEVICES_PATH, DEVICES_PATH]

    refused = service.check_session(session(device_root, device, make_client([ACCOUNT[1]])))
    events.clear()
    win.session = refused
    MainWindow.set_primary_training(win)
    assert events == []  # a device of another account: nothing is even started
