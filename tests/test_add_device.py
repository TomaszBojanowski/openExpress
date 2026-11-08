"""Adding the connected device to the account (service.add_device): the account's list read
first (on it already: nothing sent), a dry run that stops there, the request, a retired device
made active, the list read again, which tells the result. All IDs here are made up."""

import logging

import pytest
from fakes import DEVICE_UNIT, DEVICES_PATH, OTHER_UNIT, make_client, response

from openexpress import service
from openexpress.connect import (
    REGISTER_PATH,
    OtherOwner,
    OwnershipUnknown,
    Registration,
    Unauthorized,
)
from openexpress.device.xmldevice import GarminDevice

UNIT = DEVICE_UNIT
PART = "006-B2604-00"
XML = b'<?xml version="1.0"?><Device><Id>1234567890</Id><Model>made up</Model></Device>'
SECRET = "OauthTokenSecret3928"
STATUS_PATH = f"{REGISTER_PATH}/status/{UNIT}"


def device() -> GarminDevice:
    return GarminDevice(unit_id=UNIT, part_number=PART, software_version=1910,
                        description="fenix 5x", raw=XML)


def garmin(lists, answer=None, status_answer=None, **kw):
    """A client whose Garmin lists, at each reading of the account's list, the next of
    `lists`, and answers the registration with `answer` (by default: added, active) and a
    change of status with `status_answer`."""
    lists = list(lists)
    client = make_client(**kw)
    client.http.routes[DEVICES_PATH] = lambda: response(body=lists.pop(0) if len(lists) > 1
                                                        else lists[0])
    added = {"unitId": UNIT, "newRegistration": True, "deviceStatus": "active",
             "oAuthTokenSecret": SECRET}

    def handler(method, request):
        if method == "POST":
            return answer if answer is not None else response(body=added)
        if method == "PUT":
            return status_answer if status_answer is not None else response(204, b"")
        return None  # the account's list: the routes answer it

    client.http.handlers[REGISTER_PATH] = handler
    return client


def requests_made(client):
    return [(r["method"], r["url"].split("garmin.com/")[-1]) for r in client.http.sent]


def test_a_device_on_the_account_already_sends_nothing():
    client = garmin([[{"unitid": UNIT}]])
    result = service.add_device(client, device(), apply=True)
    assert result == service.DeviceAddition(already=True)
    assert requests_made(client) == [("GET", DEVICES_PATH)]
    assert client.verified_unit_id == UNIT


def test_the_trial_run_reads_the_list_only():
    client = garmin([[{"unitid": OTHER_UNIT}]])
    result = service.add_device(client, device())
    assert result == service.DeviceAddition(already=False)
    assert requests_made(client) == [("GET", DEVICES_PATH)]


def test_the_list_is_read_anew_not_kept():
    client = garmin([[{"unitid": OTHER_UNIT}], [{"unitid": UNIT}]])
    service.add_device(client, device())  # the dry run reads the list
    assert service.add_device(client, device()).already is True  # read again: on it now


def test_adding_sends_one_request_and_reads_the_list_again(caplog):
    client = garmin([[{"unitid": OTHER_UNIT}], [{"unitid": OTHER_UNIT}, {"unitid": UNIT}]])
    with caplog.at_level(logging.INFO):
        result = service.add_device(client, device(), apply=True)
    assert result == service.DeviceAddition(already=False, registration=Registration(True, "active"),
                                            made_active=False, listed=True)
    assert requests_made(client) == [("GET", DEVICES_PATH), ("POST", REGISTER_PATH),
                                     ("GET", DEVICES_PATH)]
    assert client.verified_unit_id == UNIT
    said = [r.getMessage() for r in caplog.records if r.getMessage().startswith("register:")]
    assert said == [
        (f"register: {PART} sent as an active device (GarminDevice.xml {len(XML)} B, "
         "environment production)"),
        "register: Garmin added it (new registration: True, status: active)",
        "register: on the account's list after adding it: yes"]
    assert str(UNIT) not in caplog.text and SECRET not in caplog.text


def test_a_retired_device_is_made_active():
    retired = response(body={"unitId": UNIT, "newRegistration": False, "deviceStatus": "retired"})
    client = garmin([[], [{"unitid": UNIT}]], answer=retired)
    result = service.add_device(client, device(), apply=True)
    assert result.made_active and result.listed is True
    assert requests_made(client) == [("GET", DEVICES_PATH), ("POST", REGISTER_PATH),
                                     ("PUT", STATUS_PATH), ("GET", DEVICES_PATH)]


def test_accepted_but_not_on_the_list_yet():
    client = garmin([[], []])
    result = service.add_device(client, device(), apply=True)
    assert result.listed is False and result.registration == Registration(True, "active")
    assert client.verified_unit_id is None


def test_a_list_that_cannot_be_read_after_adding():
    lists = iter([response(body=[]), response(500, b"oops")])
    client = garmin([[]])
    client.http.routes[DEVICES_PATH] = lambda: next(lists)
    result = service.add_device(client, device(), apply=True)
    assert result.listed is None and result.registration is not None


def test_a_refusal_ends_it_without_reading_the_list_again():
    client = garmin([[]], answer=response(409, {"error": {"code": 409}}))
    with pytest.raises(OtherOwner):
        service.add_device(client, device(), apply=True)
    assert requests_made(client) == [("GET", DEVICES_PATH), ("POST", REGISTER_PATH)]


@pytest.mark.parametrize("first", [response(500, b"oops"), response(401, b"")])
def test_a_list_that_cannot_be_read_first_sends_nothing(first):
    client = garmin([[]])
    client.http.routes[DEVICES_PATH] = first
    with pytest.raises((OwnershipUnknown, Unauthorized)):
        service.add_device(client, device(), apply=True)
    assert requests_made(client) == [("GET", DEVICES_PATH)]


def test_what_the_account_kept_is_forgotten_after_adding(monkeypatch):
    from openexpress import answers

    client = garmin([[], [{"unitid": UNIT}]])
    forgotten = []
    real = answers.ANSWERS.forget
    monkeypatch.setattr(answers.ANSWERS, "forget",
                        lambda kinds=None, about=None: (forgotten.append(kinds),
                                                        real(kinds, about)))
    service.add_device(client, device(), apply=True)
    assert any(answers.PRIMARY_TRAINING in (k or ()) for k in forgotten)
