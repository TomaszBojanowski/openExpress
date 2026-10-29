"""The account's primary training device and primary wearable, both roles, in the service and
on the command line: every request sent to Garmin Connect exactly (method, the whole address,
the body byte for byte, its type, signed), what each call gives back and what the user reads.
And the rules of the window's list: when a tile has the chip "Primary" and when the device page
has the row "Primary wearable". All devices, keys and logins are made up."""

import dataclasses
import json

import pytest
import requests
from conftest import DEVICE_XML

from openexpress import connect, devicelist, service, tile_facts
from openexpress.auth import store
from openexpress.auth.store import ConnectAuth
from openexpress.cli import main
from openexpress.connect import ApiError, GarminClient, NotAccountDevice
from openexpress.device.xmldevice import parse
from openexpress.settings import Consumer, Settings, save_consumer

UNIT = parse(DEVICE_XML.encode()).unit_id  # the test watch, "Test watch"
OTHER, UNLISTED = 3100000002, 3100000009
assert UNIT not in (OTHER, UNLISTED)

BASE = "https://connectapi.garmin.com/"
DEVICES = "device-service/deviceregistration/devices"
TRAINING = "userprofile-service/userprofile/user-settings/primary-training-device"
WEARABLE = f"device-service/deviceservice/device-info/{UNIT}/primary-activity-tracker"

LIST = ("GET", BASE + DEVICES, None, None)
READ_TRAINING = ("GET", BASE + TRAINING + "?ignoreCache=false", None, None)
SET_TRAINING = ("PUT", BASE + TRAINING, b'{"deviceId": ' + str(UNIT).encode() + b"}",
                "application/json")
SET_WEARABLE = ("PUT", BASE + WEARABLE, b"true", "application/json")

NOT_ON_THE_LIST = "a device that is not on the account's list"
NOT_REGISTERED = "This device is not registered to your Garmin account."


@pytest.fixture
def device():
    return parse(DEVICE_XML.encode())


def response(status=200, body=b""):
    r = requests.Response()
    r.status_code = status
    r._content = body if isinstance(body, bytes) else json.dumps(body).encode()
    return r


class Account:
    """Garmin Connect for these tests: the account's device list, its primary training device
    (`training`) and its primary wearable (`wearable`) as they are now (None: none set); a
    PUT that answers 200 changes them, unless `applies` is False. `capable` and
    `wearable_capable`: the flags of the test watch in the list ("missing": not there).
    `sent`: every request as it goes out."""

    def __init__(self, training=OTHER, wearable=OTHER, capable=True, wearable_capable=True,
                 on_account=True, applies=True, put_status=200):
        self.training, self.wearable = training, wearable
        self.capable, self.wearable_capable = capable, wearable_capable
        self.on_account, self.applies, self.put_status = on_account, applies, put_status
        self.sent = []

    def devices(self):
        this = {"unitid": UNIT, "productdisplayname": "Test watch",
                "primaryactivitytrackerindicator": self.wearable == UNIT}
        for key, value in (("primarytrainingcapable", self.capable),
                           ("primaryactivitytrackersettingcapable", self.wearable_capable)):
            if value != "missing":
                this[key] = value
        other = {"unitid": OTHER, "productdisplayname": "Other watch",
                 "primarytrainingcapable": True, "primaryactivitytrackersettingcapable": True,
                 "primaryactivitytrackerindicator": self.wearable == OTHER}
        return [this, other] if self.on_account else [other]

    def request(self, method, url, **kw):
        headers = kw.get("headers") or {}
        data = kw.get("data")
        self.sent.append((method, url, data.encode() if isinstance(data, str) else data,
                          headers.get("Content-Type")))
        assert headers["Authorization"].startswith("OAuth ")  # every one is signed
        assert 'oauth_token="made-up-token"' in headers["Authorization"]
        path = url.removeprefix(BASE).split("?")[0]
        if method == "GET" and path == DEVICES:
            return response(body=self.devices())
        if method == "GET" and path == TRAINING:
            return response(body={"deviceId": self.training} if self.training else {})
        if method == "PUT" and path in (TRAINING, WEARABLE):
            if isinstance(self.put_status, Exception):
                raise self.put_status
            if self.put_status == 200 and self.applies:
                if path == TRAINING:
                    self.training = json.loads(data)["deviceId"]
                else:
                    self.wearable = UNIT
            return response(self.put_status)
        return response(404)

    def client(self):
        return GarminClient(Settings(), Consumer("made-up-key", "made-up-secret"),
                            ConnectAuth("made-up-token", "made-up-token-secret"), self)


@pytest.fixture
def account(monkeypatch):
    """The command line's own client (its key and login saved) talking to an Account."""
    save_consumer(Consumer("made-up-key", "made-up-secret"))
    store.save(ConnectAuth("made-up-token", "made-up-token-secret"))
    holder = Account()
    monkeypatch.setattr(connect, "user_agent_session", lambda: holder)
    return holder


def run(account, device_root, capsys, *argv, **state):
    """`openexpress --path <the test watch> *argv` with the account as `state` says: the exit
    code, the lines it printed, what it said on stderr."""
    for key, value in state.items():
        setattr(account, key, value)
    code = main(["--path", str(device_root), *argv])
    out = capsys.readouterr()
    return code, out.out.splitlines(), out.err


# --- the primary training device in the service ----------------------------------------------


@pytest.mark.parametrize(("training", "capable", "expected"), [
    (UNIT, True, service.PrimaryTraining(UNIT, "Test watch", True, True)),
    (OTHER, True, service.PrimaryTraining(OTHER, "Other watch", False, True)),
    (UNLISTED, False, service.PrimaryTraining(UNLISTED, "", False, False)),
    (None, "missing", service.PrimaryTraining(None, "", False, None)),
    (OTHER, "yes", service.PrimaryTraining(OTHER, "Other watch", False, None)),
], ids=["this", "other", "unlisted", "unset", "flag-text"])
def test_the_service_reads_the_primary_training_device(device, training, capable, expected):
    account = Account(training=training, capable=capable)
    assert service.primary_training_status(account.client(), device) == expected
    assert account.sent == [LIST, READ_TRAINING]


def test_the_service_reads_it_with_the_list_of_a_check_just_made(device):
    account = Account()
    client = account.client()
    devices = service.require_account_device(client, UNIT)
    assert service.primary_training_status(client, device, devices).name == "Other watch"
    assert account.sent == [LIST, READ_TRAINING]


@pytest.mark.parametrize(("training", "apply", "sent", "after"), [
    (OTHER, False, [LIST, READ_TRAINING], OTHER),
    (OTHER, True, [LIST, READ_TRAINING, SET_TRAINING, READ_TRAINING], UNIT),
    (None, True, [LIST, READ_TRAINING, SET_TRAINING, READ_TRAINING], UNIT),
    (UNIT, True, [LIST, READ_TRAINING], UNIT),
    (UNIT, False, [LIST, READ_TRAINING], UNIT),
], ids=["dry", "apply", "apply-unset", "already", "dry-already"])
def test_the_service_changes_the_primary_training_device(device, training, apply, sent, after):
    account = Account(training=training)
    before = service.set_primary_training(account.client(), device, apply=apply)
    assert before == service.primary_training_status(Account(training=training).client(),
                                                     device)
    assert account.sent == sent
    assert account.training == after and account.wearable == OTHER  # the other role untouched


@pytest.mark.parametrize(("state", "error", "text", "sent"), [
    ({"capable": False}, service.PrimaryTrainingError,
     "Test watch cannot be a primary training device.", [LIST, READ_TRAINING]),
    ({"capable": None}, service.PrimaryTrainingError,
     "Could not tell whether Test watch can be a primary training device. Nothing was changed.",
     [LIST, READ_TRAINING]),
    ({"capable": "missing"}, service.PrimaryTrainingError,
     "Could not tell whether Test watch can be a primary training device. Nothing was changed.",
     [LIST, READ_TRAINING]),
    ({"on_account": False}, NotAccountDevice, None, [LIST]),
    ({"applies": False}, service.PrimaryTrainingError,
     "Garmin Connect accepted the request, but the primary training device did not change.",
     [LIST, READ_TRAINING, SET_TRAINING, READ_TRAINING]),
    ({"put_status": 500}, ApiError, "Primary training device: HTTP 500",
     [LIST, READ_TRAINING, SET_TRAINING]),
], ids=["cannot", "unknown", "no-flag", "another-account", "not-applied", "http500"])
def test_the_service_refuses_to_change_the_primary_training_device(device, state, error, text,
                                                                   sent):
    account = Account(**state)
    with pytest.raises(error) as raised:
        service.set_primary_training(account.client(), device, apply=True)
    if text is not None:
        assert str(raised.value) == text
    assert account.sent == sent and account.wearable == OTHER


# --- the primary wearable in the service -----------------------------------------------------


@pytest.mark.parametrize(("wearable", "capable", "expected"), [
    (UNIT, True, service.WearableStatus(True, "Test watch", True, True)),
    (OTHER, True, service.WearableStatus(True, "Other watch", False, True)),
    (None, False, service.WearableStatus(False, "", False, False)),
    (OTHER, "missing", service.WearableStatus(True, "Other watch", False, None)),
    (OTHER, "yes", service.WearableStatus(True, "Other watch", False, None)),
], ids=["this", "other", "unset", "no-flag", "flag-text"])
def test_the_service_reads_the_primary_wearable(device, wearable, capable, expected):
    account = Account(wearable=wearable, wearable_capable=capable)
    assert service.primary_wearable_status(account.client(), device) == expected
    assert account.sent == [LIST]


@pytest.mark.parametrize(("wearable", "apply", "sent", "after"), [
    (OTHER, False, [LIST], OTHER),
    (OTHER, True, [LIST, SET_WEARABLE, LIST], UNIT),
    (None, True, [LIST, SET_WEARABLE, LIST], UNIT),
    (UNIT, True, [LIST], UNIT),
    (UNIT, False, [LIST], UNIT),
], ids=["dry", "apply", "apply-unset", "already", "dry-already"])
def test_the_service_changes_the_primary_wearable(device, wearable, apply, sent, after):
    account = Account(wearable=wearable)
    change = service.set_primary_wearable(account.client(), device, apply=apply)
    assert change == service.WearableChange(
        service.primary_wearable_status(Account(wearable=wearable).client(), device),
        account.devices())  # the list as it is now: read back after a change
    assert account.sent == sent
    assert account.wearable == after and account.training == OTHER  # the other role untouched


@pytest.mark.parametrize(("state", "error", "text", "sent"), [
    ({"wearable_capable": False}, service.PrimaryWearableError,
     "Test watch cannot be a primary wearable.", [LIST]),
    ({"wearable_capable": None}, service.PrimaryWearableError,
     "Could not tell whether Test watch can be a primary wearable. Nothing was changed.",
     [LIST]),
    ({"wearable_capable": "missing"}, service.PrimaryWearableError,
     "Could not tell whether Test watch can be a primary wearable. Nothing was changed.",
     [LIST]),
    ({"on_account": False}, NotAccountDevice, None, [LIST]),
    ({"applies": False}, service.PrimaryWearableError,
     "Garmin Connect accepted the request, but the primary wearable did not change.",
     [LIST, SET_WEARABLE, LIST]),
    ({"put_status": 500}, ApiError, "Primary wearable: HTTP 500", [LIST, SET_WEARABLE]),
], ids=["cannot", "unknown", "no-flag", "another-account", "not-applied", "http500"])
def test_the_service_refuses_to_change_the_primary_wearable(device, state, error, text, sent):
    account = Account(**state)
    with pytest.raises(error) as raised:
        service.set_primary_wearable(account.client(), device, apply=True)
    if text is not None:
        assert str(raised.value) == text
    assert account.sent == sent and account.training == OTHER


# --- the command line ------------------------------------------------------------------------


@pytest.mark.parametrize(("argv", "state", "lines", "sent"), [
    (["primary"], {"training": UNIT}, ["Primary training device: Test watch (this device)"],
     [LIST, READ_TRAINING]),
    (["primary"], {"training": OTHER},
     ["Primary training device: Other watch",
      "The connected device (Test watch) is not the primary training device."],
     [LIST, READ_TRAINING]),
    (["primary"], {"training": UNLISTED, "capable": False},
     [f"Primary training device: {NOT_ON_THE_LIST}",
      "The connected device (Test watch) is not the primary training device.",
      "The connected device cannot be a primary training device."], [LIST, READ_TRAINING]),
    (["primary"], {"training": None},
     ["No primary training device is set for this Garmin account."], [LIST, READ_TRAINING]),
    (["primary", "--wearable"], {"wearable": UNIT}, ["Primary wearable: Test watch (this device)"],
     [LIST]),
    (["primary", "--wearable"], {"wearable": OTHER},
     ["Primary wearable: Other watch",
      "The connected device (Test watch) is not the primary wearable."], [LIST]),
    (["primary", "--wearable"], {"wearable": None, "wearable_capable": False},
     ["No primary wearable is set for this Garmin account.",
      "The connected device cannot be a primary wearable."], [LIST]),
], ids=["training-this", "training-other", "training-unlisted", "training-unset",
        "wearable-this", "wearable-other", "wearable-unset"])
def test_the_command_line_says_which_device_is_the_primary_one(account, device_root, capsys,
                                                                argv, state, lines, sent):
    assert run(account, device_root, capsys, *argv, **state) == (0, lines, "")
    assert account.sent == sent


@pytest.mark.parametrize(("argv", "state", "lines", "sent"), [
    (["primary", "set"], {"training": OTHER},
     ["Primary training device: Other watch", "Would change it to: Test watch",
      "Dry run. Add --apply."], [LIST, READ_TRAINING]),
    (["primary", "set"], {"training": None},
     ["No primary training device is set for this Garmin account.",
      "Would change it to: Test watch", "Dry run. Add --apply."], [LIST, READ_TRAINING]),
    (["primary", "set"], {"training": UNLISTED},
     [f"Primary training device: {NOT_ON_THE_LIST}", "Would change it to: Test watch",
      "Dry run. Add --apply."], [LIST, READ_TRAINING]),
    (["primary", "set", "--apply"], {"training": OTHER},
     ["Primary training device changed from Other watch to Test watch.",
      ("To change it back, connect Other watch and run `openexpress primary set --apply`, or "
       "change it in Garmin Connect.")], [LIST, READ_TRAINING, SET_TRAINING, READ_TRAINING]),
    (["primary", "set", "--apply"], {"training": None},
     ["Primary training device set to Test watch."],
     [LIST, READ_TRAINING, SET_TRAINING, READ_TRAINING]),
    (["primary", "set", "--apply"], {"training": UNIT},
     ["Test watch is already the primary training device."], [LIST, READ_TRAINING]),
    (["primary", "set", "--wearable"], {"wearable": OTHER},
     ["Primary wearable: Other watch", "Would change it to: Test watch",
      "Dry run. Add --apply."], [LIST]),
    (["primary", "--wearable", "set"], {"wearable": None},
     ["No primary wearable is set for this Garmin account.",
      "Would change it to: Test watch", "Dry run. Add --apply."], [LIST]),
    (["primary", "set", "--wearable", "--apply"], {"wearable": OTHER},
     ["Primary wearable changed from Other watch to Test watch.",
      ("To change it back, connect Other watch and run "
       "`openexpress primary set --wearable --apply`, or change it in Garmin Connect.")],
     [LIST, SET_WEARABLE, LIST]),
    (["primary", "--wearable", "set", "--apply"], {"wearable": None},
     ["Primary wearable set to Test watch."], [LIST, SET_WEARABLE, LIST]),
    (["primary", "set", "--wearable", "--apply"], {"wearable": UNIT},
     ["Test watch is already the primary wearable."], [LIST]),
], ids=["training-dry", "training-dry-unset", "training-dry-unlisted", "training-apply",
        "training-apply-unset", "training-already", "wearable-dry", "wearable-dry-unset",
        "wearable-apply", "wearable-apply-unset", "wearable-already"])
def test_the_command_line_changes_the_primary_one_only_with_apply(account, device_root, capsys,
                                                                  argv, state, lines, sent):
    assert run(account, device_root, capsys, *argv, **state) == (0, lines, "")
    assert account.sent == sent


@pytest.mark.parametrize(("argv", "state", "text", "sent"), [
    (["primary", "set", "--apply"], {"capable": False},
     "Test watch cannot be a primary training device.", [LIST, READ_TRAINING]),
    (["primary", "set"], {"capable": None},
     "Could not tell whether Test watch can be a primary training device. Nothing was changed.",
     [LIST, READ_TRAINING]),
    (["primary", "set", "--apply"], {"applies": False},
     "Garmin Connect accepted the request, but the primary training device did not change.",
     [LIST, READ_TRAINING, SET_TRAINING, READ_TRAINING]),
    (["primary", "set", "--apply"], {"on_account": False}, NOT_REGISTERED, [LIST]),
    (["primary"], {"on_account": False}, NOT_REGISTERED, [LIST]),
    (["primary", "set", "--wearable", "--apply"], {"wearable_capable": False},
     "Test watch cannot be a primary wearable.", [LIST]),
    (["primary", "set", "--wearable"], {"wearable_capable": "missing"},
     "Could not tell whether Test watch can be a primary wearable. Nothing was changed.",
     [LIST]),
    (["primary", "set", "--wearable", "--apply"], {"applies": False},
     "Garmin Connect accepted the request, but the primary wearable did not change.",
     [LIST, SET_WEARABLE, LIST]),
    (["primary", "--wearable"], {"on_account": False}, NOT_REGISTERED, [LIST]),
], ids=["training-cannot", "training-unknown", "training-not-applied", "training-another",
        "training-show-another", "wearable-cannot", "wearable-unknown", "wearable-not-applied",
        "wearable-show-another"])
def test_the_command_line_says_why_it_changes_nothing_and_exits_with_1(account, device_root,
                                                                       capsys, argv, state,
                                                                       text, sent):
    code, lines, err = run(account, device_root, capsys, *argv, **state)
    assert (code, lines) == (1, [])
    assert text in err and "Traceback" not in err
    assert account.sent == sent


# --- the window's list: the chip "Primary" and the row "Primary wearable" --------------------


def entry(unit, name="Watch", capable=True, primary=False, **more):
    return {"unitid": unit, "productdisplayname": name,
            "primaryactivitytrackersettingcapable": capable,
            "primaryactivitytrackerindicator": primary, **more}


W = devicelist.PrimaryWearable


@pytest.mark.parametrize(("devices", "expected"), [
    (None, None),
    ([], None),
    ([entry(OTHER, primary=True)], None),  # the device is not on the list
    ([entry(UNIT, "Test watch", primary=True)], W(True, "Test watch")),
    ([entry(UNIT, primary=False), entry(OTHER, "Other watch", primary=True)],
     W(False, "Other watch")),
    ([entry(UNIT), entry(OTHER)], W(False, "", False)),
    ([entry(UNIT, "Test watch", capable=False, primary=True)], W(True, "Test watch")),
    ([entry(UNIT, capable=False), entry(OTHER, "Other watch", primary=True)], None),
    ([entry(UNIT, capable=None), entry(OTHER, primary=True)], None),
    ([entry(UNIT, capable="true"), entry(OTHER, primary=True)], None),
    ([entry(UNIT, primary="true"), entry(OTHER, "Other watch", primary=True)],
     W(False, "Other watch")),
    ([{"unitid": str(UNIT), "primaryactivitytrackersettingcapable": True},
      entry(OTHER, None, primary=True, displayname="Other")], W(False, "Other")),
    ([entry(UNIT), entry(OTHER, 5, primary=True, displayname="Other")], W(False, "")),
    ([entry(UNIT), entry(OTHER, "", primary=True, displayname=7)], W(False, "")),
    ([entry(UNIT), entry(OTHER, "First", primary=True), entry(UNLISTED, "Second", primary=True)],
     W(False, "First")),
    (["not an entry", entry(UNIT, "Test watch", primary=True)], W(True, "Test watch")),
], ids=["no-list", "empty", "not-listed", "this", "other", "none-set", "this-not-capable",
        "not-capable", "capable-null", "capable-text", "primary-text", "displayname",
        "name-not-text", "displayname-not-text", "first-of-two", "not-a-dict"])
def test_the_row_primary_wearable_of_the_device_page(devices, expected):
    assert devicelist.primary_wearable(devices, UNIT) == expected


def test_the_service_reads_the_same_fields_of_the_list(device):
    """The service's status, from the list a check just gave: the same name and the same
    "this device" as the row, and its own rule for whether the device can be one."""
    client = Account().client()
    service.require_account_device(client, UNIT)
    for devices, expected in [
        ([entry(UNIT), entry(OTHER, 5, primary=True, displayname="Other")],
         service.WearableStatus(True, "", False, True)),
        ([entry(UNIT, capable="yes"), entry(OTHER, None, primary=True, displayname="Other")],
         service.WearableStatus(True, "Other", False, None)),
        ([entry(UNIT, "Test watch", capable=False, primary=True)],
         service.WearableStatus(True, "Test watch", True, False)),
        ([{"unitid": str(UNIT)}, entry(OTHER)], service.WearableStatus(False, "", False, None)),
        ([entry(OTHER, primary=True)], service.WearableStatus(True, "Watch", False, None)),
    ]:
        assert service.primary_wearable_status(client, device, devices) == expected


@pytest.mark.parametrize(("value", "chip"), [(True, True), (False, False), ("true", False),
                                             (1, False), (None, False), ("missing", False)])
def test_the_chip_primary_of_a_tile_is_the_device_s_own_indicator(value, chip):
    """A device only on the account (its entry), and a device remembered here (what the
    account's list said of it when read): the chip only for its own indicator, exactly true."""
    this = entry(UNIT, "Test watch")
    if value == "missing":
        del this["primaryactivitytrackerindicator"]
    else:
        this["primaryactivitytrackerindicator"] = value
    devices = [this, entry(OTHER, "Other watch", primary=True)]
    (other_tile, this_tile) = devicelist.tiles([], {}, devices, logged_in=True)
    assert (this_tile.description, this_tile.primary) == ("Test watch", chip)
    assert (other_tile.description, other_tile.primary) == ("Other watch", True)
    facts = tile_facts.account_facts(devices, [UNIT, OTHER, UNLISTED], 1000.0, "login")
    assert [(f.on_account, f.primary) for f in (facts[UNIT], facts[OTHER], facts[UNLISTED])] == [
        (True, chip), (True, True), (False, False)]


def test_the_chip_is_drawn_only_for_a_primary_tile():
    from openexpress.gui import start

    (tile,) = devicelist.tiles([], {}, [entry(UNIT, "Test watch", primary=True)], True)
    assert [words for words, _style, _tip in start.chips(tile)] == ["Primary"]
    plain = dataclasses.replace(tile, primary=False)
    assert "Primary" not in [words for words, _style, _tip in start.chips(plain)]
