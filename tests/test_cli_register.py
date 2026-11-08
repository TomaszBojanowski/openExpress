"""The command register: a dry run that says what would be sent and where, the account's
e-mail address masked; with --apply the device added, its outcomes; nothing written to the
device. All IDs and names here are made up."""

import pytest
from fakes import DEVICE_UNIT, DEVICES_PATH, OTHER_UNIT, make_client, response

from openexpress import cli
from openexpress.cli import main
from openexpress.connect import REGISTER_PATH
from openexpress.i18n import human_size

UNIT = DEVICE_UNIT
STATUS_PATH = f"{REGISTER_PATH}/status/{UNIT}"


def tree(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*"))
            if p.is_file()}


class Garmin:
    """A Garmin Connect for the command: its lists of the account's devices, one a reading,
    and its answers to the registration and the change of status."""

    def __init__(self, lists, answer=None, status_answer=None, account=None):
        self.lists = list(lists)
        self.client = make_client()
        self.client.auth.account = account if account is not None else {"username": "tomek_b"}
        self.client.http.routes[DEVICES_PATH] = lambda: response(
            body=self.lists.pop(0) if len(self.lists) > 1 else self.lists[0])
        added = {"unitId": UNIT, "newRegistration": True, "deviceStatus": "active"}

        def handler(method, request):
            if method == "POST":
                return answer if answer is not None else response(body=added)
            if method == "PUT":
                return status_answer if status_answer is not None else response(204, b"")
            return None

        self.client.http.handlers[REGISTER_PATH] = handler

    def requests(self):
        return [(r["method"], r["url"].split("garmin.com/")[-1]) for r in self.client.http.sent]


@pytest.fixture
def garmin(monkeypatch):
    holder = {}

    def use(*a, **kw):
        holder["garmin"] = g = Garmin(*a, **kw)
        monkeypatch.setattr(cli, "make_client", lambda settings, need_auth: g.client)
        return g

    return use


def xml_size(device_root) -> str:
    """As the command line prints it: with ordinary spaces."""
    size = human_size(len((device_root / "GARMIN" / "GarminDevice.xml").read_bytes()))
    return size.replace("\u00a0", " ")


def test_the_trial_run_says_what_would_be_sent(device_root, xdg, garmin, capsys):
    g = garmin([[{"unitid": OTHER_UNIT}]])
    before = tree(device_root)
    assert main(["--path", str(device_root), "register"]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "Test watch (006-B0000-00, software 23.39) is not on the Garmin account t…b.",
        ("Would add it as an active device: POST "
         "connectapi.garmin.com/device-service/deviceregistration/device"),
        f"with its GarminDevice.xml ({xml_size(device_root)}; it holds the device's Unit ID).",
        "After that, a sync uploads its activities and health data to this account.",
        "A device can be removed from an account only in Garmin Connect.",
        "This is an experimental feature.",
        "Dry run: nothing was changed. Add --apply.",
    ]
    assert g.requests() == [("GET", DEVICES_PATH)]
    assert tree(device_root) == before


@pytest.mark.parametrize("account, shown", [
    ({"username": "tomek_b"}, "t…b"),
    ({"username": "tb"}, "t…"),
    ({"emailaddress": "tomek@example.com"}, "t…@e…"),
    ({"username": "tomek@example.com", "emailaddress": "x@y.z"}, "t…@e…"),
    ({}, "(unknown account)"),
])
def test_the_account_is_masked(device_root, xdg, garmin, capsys, account, shown):
    garmin([[]], account=account)
    assert main(["--path", str(device_root), "register"]) == 0
    out = capsys.readouterr().out
    assert f"on the Garmin account {shown}." in out
    assert "example.com" not in out and "tomek" not in out


def test_adding(device_root, xdg, garmin, capsys):
    g = garmin([[], [{"unitid": UNIT}]])
    before = tree(device_root)
    assert main(["--path", str(device_root), "register", "--apply"]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "Added Test watch to the Garmin account t…b."]
    assert g.requests() == [("GET", DEVICES_PATH), ("POST", REGISTER_PATH),
                            ("GET", DEVICES_PATH)]
    assert tree(device_root) == before  # nothing is ever written to the device


def test_adding_a_retired_device(device_root, xdg, garmin, capsys):
    retired = response(body={"unitId": UNIT, "newRegistration": False,
                             "deviceStatus": "retired"})
    g = garmin([[], [{"unitid": UNIT}]], answer=retired)
    assert main(["--path", str(device_root), "register", "--apply"]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "Added Test watch to the Garmin account t…b.",
        "Garmin kept it as retired; it is active now."]
    assert ("PUT", STATUS_PATH) in g.requests()


@pytest.mark.parametrize("argv", [["register"], ["register", "--apply"]], ids=" ".join)
def test_a_device_on_the_account_already(device_root, xdg, garmin, capsys, argv):
    g = garmin([[{"unitid": UNIT}]])
    assert main(["--path", str(device_root), *argv]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "Test watch is on the Garmin account t…b already."]
    assert g.requests() == [("GET", DEVICES_PATH)]


@pytest.mark.parametrize("answer, said", [
    (response(409, {"error": {"code": 409}}), "registered to another Garmin account"),
    (response(409, {"error": {"code": 409, "subcode": 4091}}), "active subscription"),
    (response(400, b""), "did not accept the description of this device"),
    (response(403, b""), "does not allow adding this device"),
])
def test_a_refusal(device_root, xdg, garmin, capsys, answer, said):
    g = garmin([[]], answer=answer)
    assert main(["--path", str(device_root), "register", "--apply"]) == 1
    captured = capsys.readouterr()
    assert said in captured.err and "Nothing was changed." in captured.err
    assert "Traceback" not in captured.err and captured.out == ""
    assert g.requests() == [("GET", DEVICES_PATH), ("POST", REGISTER_PATH)]


def test_accepted_but_not_on_the_list_yet(device_root, xdg, garmin, capsys):
    garmin([[], []])
    assert main(["--path", str(device_root), "register", "--apply"]) == 1
    err = capsys.readouterr().err
    assert "Garmin accepted the device, but the account's list does not show it yet." in err


def test_accepted_but_the_list_cannot_be_read(device_root, xdg, garmin, capsys):
    g = garmin([[]])
    lists = iter([response(body=[]), response(500, b"oops")])
    g.client.http.routes[DEVICES_PATH] = lambda: next(lists)
    assert main(["--path", str(device_root), "register", "--apply"]) == 1
    assert "Could not read the account's device list to check it." in capsys.readouterr().err


def test_the_help_says_it_is_experimental(capsys):
    with pytest.raises(SystemExit):
        main(["register", "--help"])
    assert "experimental" in capsys.readouterr().out
