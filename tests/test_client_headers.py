"""The headers of each Garmin service as Garmin Express sends them.
Garmin's update, map and upload configuration service (OMT) gets the Garmin-Client-* headers
and the language as a tag with a hyphen ("de-DE") in Garmin-Client-LocaleCode and
Accept-Language, without X-garmin-client-id; the Garmin Connect API gets the Garmin-Client-*
headers with X-garmin-client-id and no language; the Connect IQ store only X-garmin-client-id.
Every request also names openExpress (User-Agent). The requests are caught before they leave;
the key, the token and the device are made up."""

import io

import pytest
import requests
from conftest import DEVICE_XML

import openexpress
from openexpress import ciq
from openexpress.auth.store import ConnectAuth
from openexpress.connect import GarminClient
from openexpress.device.xmldevice import parse
from openexpress.settings import Consumer, Settings, language_tag

UNIT = 1234567890  # the Id of DEVICE_XML
OURS = f"openexpress/{openexpress.__version__} (Linux)"


@pytest.fixture
def sent(monkeypatch):
    """The headers of every request the HTTP library would send: (address, headers)."""
    seen = []

    def send(adapter, request, **kw):
        seen.append((request.url, {k.casefold(): v for k, v in request.headers.items()}))
        resp = requests.Response()
        resp.request, resp.url, resp.status_code = request, request.url, 200
        resp.headers["Content-Type"] = "application/json"
        resp.raw = io.BytesIO(b"[]" if "appstore" in request.url else b"{}")
        return resp

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", send)
    return seen


def _client() -> GarminClient:
    settings = Settings.load()
    settings.chosen_locale = "de_DE"
    client = GarminClient(settings, Consumer("made-up-key", "made-up-secret"),
                          ConnectAuth("made-up-token", "made-up-token-secret",
                                      settings.environment))
    client.verified_unit_id = UNIT
    return client


def test_the_language_tag_has_a_hyphen():
    assert language_tag("de_DE") == "de-DE" and language_tag("pl-PL") == "pl-PL"


def test_requests_to_the_update_and_map_service_carry_no_client_id(sent):
    client = _client()
    xml = DEVICE_XML.encode()
    client.upload_settings(UNIT)
    client.unit_info(UNIT, "006-B0000-00")
    client.software_updates(xml)
    client.universal_maps(xml)
    client.activate_maps(xml, [])
    assert len(sent) == 5
    for url, h in sent:
        assert url.startswith("https://omt.garmin.com/"), url
        assert "x-garmin-client-id" not in h, url
        assert h["garmin-client-localecode"] == h["accept-language"] == "de-DE", url
        assert h["garmin-client-name"] == "express" and h["user-agent"] == OURS


def test_requests_to_garmin_connect_and_the_store_carry_the_client_id(sent):
    client = _client()
    client.user_profile()
    ciq.StoreApi(client).account_apps(parse(DEVICE_XML.encode()), language_tag("de_DE"))
    (connect_url, connect), (store_url, store) = sent
    assert connect_url.startswith("https://connectapi.garmin.com/")
    assert connect["x-garmin-client-id"] == "EXPRESS" and connect["garmin-client-name"] == "express"
    assert "garmin-client-localecode" not in connect and "accept-language" not in connect
    assert store_url.startswith("https://services.garmin.com/")
    assert store["x-garmin-client-id"] == "EXPRESS" and store["user-agent"] == OURS
    assert store["authorization"].startswith("OAuth ")
    # The store gets none of the Garmin-Client-* headers (as Garmin Express).
    assert not [k for k in store if k.startswith("garmin-client-")]
