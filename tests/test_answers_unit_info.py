"""unit-info and the account's country through the kept answers (answers): the same device
opened again does not ask unit-info again (7 days, only the fields the program reads), Check
Again of the maps asks anew; the country of the account is kept an hour for the maps, but
whether data may be uploaded asks it anew. All devices, answers and accounts are made up."""
import types

from test_detection import RTL515_XML, _UpdateServer

from openexpress import answers, service
from openexpress.connect import GarminClient
from openexpress.device.xmldevice import parse
from openexpress.settings import Settings

ANSWER = {"unitid": 3000000003, "serialnumber": "SER0000001", "mapupdatetype": "Universal",
          "graphicurl": "https://res.garmin.com/x.png", "segmenttype": "BikeAccessories",
          "devicemodel": "Made-up", "connectsupport": "Required", "productname": "Varia"}


def _session(server):
    return types.SimpleNamespace(client=GarminClient(Settings(), None, None, server),
                                 device=parse(RTL515_XML.encode()))


def test_the_device_opened_again_does_not_ask_unit_info_again():
    server = _UpdateServer(ANSWER)
    first = service.unit_info(_session(server))
    again = service.unit_info(_session(server))  # a new session: the device opened again
    assert len(server.sent) == 1 and first == again
    assert set(first) == answers.UNIT_INFO_FIELDS  # no unit ID, no serial number


def test_check_again_of_the_maps_asks_unit_info_anew():
    server = _UpdateServer(ANSWER)
    s = _session(server)
    service.unit_info(s)
    service.forget_unit_info(s)
    service.unit_info(s)
    service.unit_info(_session(server))  # kept again
    assert len(server.sent) == 2


def test_seven_days_later_unit_info_is_asked_anew(monkeypatch):
    clock = [1_800_000_000.0]
    monkeypatch.setattr(answers.ANSWERS, "clock", lambda: clock[0])
    server = _UpdateServer(ANSWER)
    service.unit_info(_session(server))
    clock[0] += 7 * 86400 - 60
    service.unit_info(_session(server))
    clock[0] += 120
    service.unit_info(_session(server))
    assert len(server.sent) == 2


class Account:
    """The consent and the location of a made-up account, counted."""

    def __init__(self):
        self.asked = []

    def consent(self, consent_type):
        return {"useroption": "opt-in", "sourcetype": "DEFAULT",
                "consentenforcementstate": "HARD"}

    def profile_country(self, fresh=False):
        self.asked.append(fresh)
        return "PL"

    def device_sync_preference(self):
        return {"value": "true"}


def test_whether_data_may_be_uploaded_asks_the_country_anew():
    account = Account()
    assert service.check_upload_allowed(account).allowed
    assert service.check_upload_allowed(account).allowed
    assert account.asked == [True, True]


def test_the_maps_take_the_country_kept_for_an_hour(monkeypatch):
    """Two openings of the device (two sessions): one request for the country."""
    import requests
    from test_ownership import make_client

    location = "userprofile-service/userprofile/location"
    resp = requests.Response()
    resp.status_code, resp._content = 200, b'{"countryCode": "kr"}'
    client = make_client([{"unitid": 1234567890}], **{location: resp})
    service.require_account_device(client, 1234567890)
    for _opening in range(2):
        s = types.SimpleNamespace(logged_in=True, ownership_error=None, client=client,
                                  account_country_asked=False, account_country_code=None)
        assert service.account_country(s) == "KR"
    assert client.http.paths().count(location) == 1
