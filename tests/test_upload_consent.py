"""Nothing is uploaded unless the Garmin account allows it. All answers here are made up."""


import pytest
import requests
from test_fixes_ui import FakeClient, fake_client  # noqa: F401 - the fixture

from openexpress import service, sync
from openexpress.cli import main
from openexpress.connect import ApiError, ClockSkew, Unauthorized

UNIT = 1234567890
OPT_IN = {"useroption": "opt-in", "sourcetype": "CUSTOMER", "consentenforcementstate": "HARD"}
QUEUED = [{"messageid": 5, "devicexmldatatype": "FIT_TYPE_6",
           "metadata": {"messageurl": "course", "messagename": "A course"}}]


class Account(FakeClient):
    """A fake account whose consent, device sync setting and country can be set."""

    def __init__(self, consent=OPT_IN, preference="true", country="PL", **kw):
        super().__init__(**kw)
        self.consent_answer, self.preference, self.country = consent, preference, country
        self.asked = []

    def _answer(self, value):
        if isinstance(value, Exception):
            raise value
        return value

    def consent(self, consent_type):
        self.asked.append(("consent", consent_type))
        answer = self.consent_answer
        if isinstance(answer, dict) and consent_type in answer:
            answer = answer[consent_type]  # one answer per consent type
        return self._answer(answer)

    def device_sync_preference(self):
        self.asked.append(("preference", None))
        value = self._answer(self.preference)
        return value if value is None or isinstance(value, dict) else {
            "key": "account.deviceSync", "value": value}

    def profile_country(self):
        self.asked.append(("country", None))
        return self._answer(self.country)

    def download_message(self, url, absolute):
        return b"FILE"


def gate(**kw):
    return service.check_upload_allowed(Account(**kw))


# --- what the consent service's answer means ------------------------------------------------

@pytest.mark.parametrize("body, expected", [
    (OPT_IN, True),
    ({**OPT_IN, "consentenforcementstate": "SOFT"}, True),
    ({**OPT_IN, "sourcetype": "SYSTEM"}, True),
    # The server does not enforce this consent at all.
    ({"useroption": None, "sourcetype": "DEFAULT", "consentenforcementstate": "NONE"}, True),
    ({**OPT_IN, "useroption": "opt-out"}, False),
    ({"useroption": "opt-out", "sourcetype": "DEFAULT", "consentenforcementstate": "NONE"}, False),
    ({**OPT_IN, "sourcetype": "EXPIRED"}, False),
    ({**OPT_IN, "useroption": None}, False),
    ({**OPT_IN, "consentenforcementstate": None}, False),
    ({**OPT_IN, "consentenforcementstate": "SOMETHING NEW"}, False),
    ({**OPT_IN, "sourcetype": "SOMETHING NEW"}, False),
    ({}, False), (None, False), ([], False), ("true", False),
])
def test_consent_answers(body, expected):
    assert service.consent_granted(body, service.UPLOAD_CONSENT, lambda: "PL") is expected


@pytest.mark.parametrize("country, expected", [("PL", True), ("CN", False), (None, False), ("", False)])
def test_default_consent_needs_a_known_country_other_than_china(country, expected):
    body = {**OPT_IN, "sourcetype": "DEFAULT"}
    assert service.consent_granted(body, service.UPLOAD_CONSENT, lambda: country) is expected
    # The country is only asked about for a default consent.
    asked = []
    assert service.consent_granted(OPT_IN, service.UPLOAD_CONSENT, lambda: asked.append(1)) is True
    assert asked == []


# --- the gate -------------------------------------------------------------------------------

def test_uploads_are_allowed_with_consent_and_device_sync():
    client = Account()
    result = service.check_upload_allowed(client)
    assert result.allowed and result.why() == ""
    assert client.asked == [("consent", "DI_CONNECT_UPLOAD"), ("preference", None)]


@pytest.mark.parametrize("kw, reason", [
    ({"consent": {**OPT_IN, "useroption": "opt-out"}}, service.NO_CONSENT),
    ({"consent": {**OPT_IN, "sourcetype": "EXPIRED"}}, service.NO_CONSENT),
    ({"consent": None}, service.NO_CONSENT),                      # empty answer
    ({"consent": {"unexpected": "fields"}}, service.NO_CONSENT),  # an answer we do not understand
    ({"consent": {**OPT_IN, "sourcetype": "DEFAULT"}, "country": "CN"}, service.NO_CONSENT),
    ({"consent": {**OPT_IN, "sourcetype": "DEFAULT"}, "country": None}, service.NO_CONSENT),
    ({"preference": "false"}, service.SYNC_OFF),
    ({"preference": "no"}, service.SYNC_OFF),
    ({"preference": None}, service.NOT_CHECKED),
    ({"preference": {"key": "account.deviceSync"}}, service.NOT_CHECKED),
    ({"consent": ApiError("HTTP 500", 500)}, service.NOT_CHECKED),
    ({"consent": requests.ConnectionError("no network")}, service.NOT_CHECKED),
    ({"consent": ValueError("not JSON")}, service.NOT_CHECKED),
    ({"preference": ApiError("HTTP 503", 503)}, service.NOT_CHECKED),
    ({"consent": {**OPT_IN, "sourcetype": "DEFAULT"}, "country": ApiError("HTTP 500", 500)},
     service.NOT_CHECKED),
])
def test_uploads_are_blocked(kw, reason):
    result = gate(**kw)
    assert not result.allowed and result.reason == reason and result.why()
    assert result.granted("ANYTHING") is False


def test_enforcement_none_allows_uploads_without_an_opt_in():
    assert gate(consent={"useroption": None, "sourcetype": "DEFAULT",
                         "consentenforcementstate": "NONE"}).allowed


@pytest.mark.parametrize("error", [Unauthorized("expired", 401), ClockSkew("clock", 401)])
def test_expired_login_and_wrong_clock_are_raised_as_such(error):
    with pytest.raises(type(error)):
        gate(consent=error)
    with pytest.raises(type(error)):
        gate(preference=error)


def test_messages():
    assert gate(consent=None).message() == (
        "Nothing was uploaded: the Garmin account has no consent to upload data from devices.")
    assert gate(preference="false").message() == (
        "Nothing was uploaded: device sync is turned off in the Garmin account.")
    assert gate(consent=ApiError("x")).message() == (
        "Nothing was uploaded: the upload consent could not be checked. Try again later.")
    assert gate(preference="false").message(done=False).startswith("Nothing would be uploaded: ")


# --- consents that single upload locations require --------------------------------------------

CONFIG = {"datatypes": [
    {"datatypename": "FIT_TYPE_4", "uploadlocations": [
        {"url": "https://up.example/activity", "requiresauthentication": True}]},
    {"datatypename": "FIT_TYPE_32", "uploadlocations": [
        {"url": "https://up.example/wellness", "requiresauthentication": True,
         "requiresuserconsent": True, "requiredconsenttypes": [{"name": "SPECIAL_CONSENT"}]}]},
]}


def plan(device_root, client):
    from openexpress.device.storage import LocalStorage
    from openexpress.device.xmldevice import parse

    st = LocalStorage(device_root)
    dev = parse(st.read("GARMIN/GarminDevice.xml"))
    allowed = service.check_upload_allowed(client)
    return sync.plan_uploads(dev, st, CONFIG, sync.SyncState(UNIT),
                             consent_granted=allowed.granted), client


def test_type_with_a_required_consent_is_uploaded_only_with_it(device_root, xdg):
    with_it, client = plan(device_root, Account())
    assert {c.datatype for c in with_it.candidates} == {"FIT_TYPE_4", "FIT_TYPE_32"}
    assert ("consent", "SPECIAL_CONSENT") in client.asked

    without, client = plan(device_root, Account(consent={
        "DI_CONNECT_UPLOAD": OPT_IN, "SPECIAL_CONSENT": {**OPT_IN, "useroption": "opt-out"}}))
    assert {c.datatype for c in without.candidates} == {"FIT_TYPE_4"}
    assert without.skipped_types == {"FIT_TYPE_32": "consent not given (SPECIAL_CONSENT)"}
    assert client.asked.count(("consent", "SPECIAL_CONSENT")) == 1  # asked once

    failing, _client = plan(device_root, Account(consent={
        "DI_CONNECT_UPLOAD": OPT_IN, "SPECIAL_CONSENT": ApiError("HTTP 500", 500)}))
    assert {c.datatype for c in failing.candidates} == {"FIT_TYPE_4"}


def test_without_a_consent_check_such_types_are_skipped(device_root, xdg):
    from openexpress.device.storage import LocalStorage
    from openexpress.device.xmldevice import parse

    st = LocalStorage(device_root)
    dev = parse(st.read("GARMIN/GarminDevice.xml"))
    result = sync.plan_uploads(dev, st, CONFIG, sync.SyncState(UNIT))  # e.g. not logged in
    assert {c.datatype for c in result.candidates} == {"FIT_TYPE_4"}


# --- a whole sync -----------------------------------------------------------------------------

@pytest.mark.parametrize("kw, text", [
    ({"consent": {**OPT_IN, "useroption": "opt-out"}}, "no consent to upload data"),
    ({"preference": "false"}, "device sync is turned off"),
    ({"consent": ApiError("HTTP 500", 500)}, "could not be checked"),
    ({"consent": None}, "no consent to upload data"),
])
def test_blocked_sync_uploads_nothing_but_still_downloads(device_root, xdg, fake_client, capsys,
                                                         kw, text):
    client = fake_client["client"] = Account(messages=QUEUED, **kw)
    acks = []
    client.ack_message = acks.append
    assert main(["--path", str(device_root), "sync", "--apply", "--all"]) == 0
    out = capsys.readouterr().out
    assert client.uploads == []
    assert out.count("Nothing was uploaded: ") == 1 and text in out
    assert "Written: 1" in out and acks == [5]  # the queue is still served
    assert list((device_root / "GARMIN/NewFiles").iterdir())
    data = service.load_last_sync(UNIT)
    assert data["uploaded"] == 0 and any("Nothing was uploaded" in line for line in data["lines"])
    # Asked before anything could be uploaded, and once.
    assert client.asked.count(("consent", "DI_CONNECT_UPLOAD")) == 1


def test_allowed_sync_uploads_as_before(device_root, xdg, fake_client, capsys):
    client = fake_client["client"] = Account()
    assert main(["--path", str(device_root), "sync", "--apply", "--all"]) == 0
    assert len(client.uploads) == 2 and "Nothing was uploaded" not in capsys.readouterr().out
    assert client.asked[0] == ("consent", "DI_CONNECT_UPLOAD")


def test_dry_run_shows_that_nothing_would_be_uploaded(device_root, xdg, fake_client, capsys):
    client = fake_client["client"] = Account(preference="false")
    assert main(["--path", str(device_root), "sync"]) == 0
    out = capsys.readouterr().out
    assert "Nothing would be uploaded: device sync is turned off in the Garmin account." in out
    assert "Dry run" in out and client.uploads == []


def test_dry_run_shows_that_uploads_are_allowed(device_root, xdg, fake_client, capsys):
    client = fake_client["client"] = Account()
    assert main(["--path", str(device_root), "sync"]) == 0
    out = capsys.readouterr().out
    assert ("Uploads are allowed: the Garmin account has the upload consent and device sync "
            "is on.") in out
    assert client.uploads == []


def test_download_only_does_not_ask_about_consent(device_root, xdg, fake_client, capsys):
    client = fake_client["client"] = Account(consent=ApiError("HTTP 500", 500), messages=QUEUED)
    assert main(["--path", str(device_root), "sync", "--download-only"]) == 0
    assert client.asked == []


def test_include_consent_flag_is_gone(device_root, xdg, fake_client, capsys):
    with pytest.raises(SystemExit):
        main(["--path", str(device_root), "sync", "--include-consent"])
    assert "unrecognized arguments" in capsys.readouterr().err


def test_window_and_background_sync_use_the_same_gate(device_root, xdg):
    """service.run_sync is what the window and the watcher call."""
    from openexpress.device.storage import LocalStorage
    from openexpress.device.xmldevice import parse
    from openexpress.settings import Settings

    st = LocalStorage(device_root)
    dev = parse(st.read("GARMIN/GarminDevice.xml"))
    for kw, uploads in (({"preference": "false"}, 0), ({}, 2)):
        client = Account(messages=QUEUED, **kw)
        s = service.DeviceSession(st, dev, Settings(), client)
        state = sync.SyncState(UNIT)
        if state.file.exists():
            state.file.unlink()
        summary = service.run_sync(s, first_run="all")
        assert len(client.uploads) == uploads and summary.uploaded == uploads
        assert summary.downloaded == 1
        blocked = [line for line in summary.lines if line.startswith("Nothing was uploaded")]
        assert len(blocked) == (0 if uploads else 1) and summary.errors == []
