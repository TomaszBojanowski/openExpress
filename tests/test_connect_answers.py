"""How the client reads the answers of Garmin Connect about the account: nothing to say (204,
an empty body), an object (keys in lower case), and anything else, which is an error naming
the request. All answers here are made up."""

import pytest
from fakes import DEVICE_UNIT, in_language, make_client, response

from openexpress.connect import PRIMARY_TRAINING_PATH, ApiError

NOT_UNDERSTOOD = "{what}: Garmin's server gave an answer openExpress does not understand; try again later"

CONSENT = "gdprconsent-service/feature/consent/"
PREFERENCE = "userpreference-service/account.deviceSync"
LOCATION = "userprofile-service/userprofile/location"
MESSAGES = "device-service/devicemessage/messages"


def client_answering(path, answer):
    client = make_client(**{path: answer})
    client.verified_unit_id = DEVICE_UNIT
    return client


def error_of(call) -> tuple:
    with pytest.raises(ApiError) as error:
        call()
    return str(error.value), error.value.status, error.value.body


ASKED = {
    "consent": (CONSENT, "Upload consent", lambda c: c.consent("DI_CONNECT_UPLOAD")),
    "preference": (PREFERENCE, "Device sync setting", lambda c: c.device_sync_preference()),
    "primary": (PRIMARY_TRAINING_PATH, "Primary training device",
                lambda c: c.primary_training_device()),
}
EMPTY = {"consent": None, "preference": None, "primary": {}}


@pytest.mark.parametrize("name", sorted(ASKED))
@pytest.mark.parametrize("answer", [response(204, b""), response(200, b""), response(200, b" \n")],
                         ids=["204", "empty", "blank"])
def test_nothing_to_say(name, answer):
    path, _what, call = ASKED[name]
    assert call(client_answering(path, answer)) == EMPTY[name]


@pytest.mark.parametrize("name", sorted(ASKED))
def test_an_object_with_its_keys_in_lower_case(name):
    path, _what, call = ASKED[name]
    got = call(client_answering(path, response(body={"ConsentStatus": "OK", "Inner": {"A": 1}})))
    assert got == {"consentstatus": "OK", "inner": {"a": 1}}


@pytest.mark.parametrize("name", sorted(ASKED))
@pytest.mark.parametrize("body", [[{"a": 1}], "text", 5], ids=["list", "string", "number"])
def test_anything_but_an_object_is_an_error(name, body):
    path, what, call = ASKED[name]
    assert error_of(lambda: call(client_answering(path, response(body=body)))) == (
        NOT_UNDERSTOOD.format(what=what), None, "")


@pytest.mark.parametrize("name", sorted(ASKED))
def test_an_answer_that_is_no_json_is_an_error_with_its_status(name):
    path, what, call = ASKED[name]
    assert error_of(lambda: call(client_answering(path, response(body=b"<html>")))) == (
        NOT_UNDERSTOOD.format(what=what), 200, "<html>")


def test_null_is_no_primary_training_device_but_no_consent_or_preference():
    answer = response(body=b"null")
    assert client_answering(PRIMARY_TRAINING_PATH, answer).primary_training_device() == {}
    for name in ("consent", "preference"):
        path, what, call = ASKED[name]
        assert error_of(lambda: call(client_answering(path, answer))) == (  # noqa: B023 - called at once
            NOT_UNDERSTOOD.format(what=what), None, "")


@pytest.mark.parametrize("body, country", [
    ({"countryCode": "pl"}, "PL"), ({"countryCode": " "}, None), ({"other": 1}, None),
    ([{"countryCode": "pl"}], None), (None, None), (b"", None)])
def test_the_country_of_the_account(body, country):
    """Unlike the others, an answer that is no object is no country, not an error."""
    answer = response(body=body) if body != b"" else response(204, b"")
    assert client_answering(LOCATION, answer).profile_country() == country


@pytest.mark.parametrize("body, messages", [
    ({"Messages": [{"MessageId": 5}]}, [{"messageid": 5}]), ({"messages": None}, []), ({}, [])])
def test_the_queue_of_files(body, messages):
    assert client_answering(MESSAGES, response(body=body)).device_messages(DEVICE_UNIT) == messages


@pytest.mark.parametrize("body", [[], None, "x"])
def test_a_queue_that_is_no_object_is_an_error(body):
    client = client_answering(MESSAGES, response(body=body))
    assert error_of(lambda: client.device_messages(DEVICE_UNIT)) == (
        NOT_UNDERSTOOD.format(what="Connect file queue"), None, "")


# --- a server's error (5xx) said as one; the other answers as they were ---------------------------


@pytest.mark.parametrize("status", [500, 503, 526])
def test_a_server_error_is_said_as_a_server_error(status):
    client = client_answering(CONSENT, response(status, b"<html>made up</html>"))
    said, code, _body = error_of(lambda: client.consent("DI_CONNECT_UPLOAD"))
    assert said == f"Upload consent: server error (HTTP {status})" and code == status


@pytest.mark.parametrize(("language", "words"), [
    ("en", "Connect IQ apps: server error (HTTP 526)"),
    ("pl", "Aplikacje Connect IQ: błąd serwera (HTTP 526)")])
def test_the_apps_page_says_a_server_error_in_its_language(language, words):
    """What the Apps page shows under "Something Went Wrong" (the error's words, as the
    command line says them too)."""
    import types

    from openexpress import ciq

    client = client_answering("express/appstore/rest/apps/downloads", response(526, b""))
    device = types.SimpleNamespace(unit_id=DEVICE_UNIT, part_number="006-B0000-00")
    with in_language(language):
        said, code, _body = error_of(lambda: ciq.StoreApi(client).account_apps(device, "en-US"))
    assert said == words and code == 526


@pytest.mark.parametrize(("status", "words"), [
    (401, "Upload consent: the sign-in has expired (HTTP 401). Sign in again."),
    (403, "Upload consent: the sign-in has expired (HTTP 403). Sign in again."),
    (404, "Upload consent: HTTP 404"),
    (429, "Upload consent: HTTP 429")])
def test_the_other_answers_that_fail_are_said_by_their_code(status, words):
    client = client_answering(CONSENT, response(status, b""))
    said, code, _body = error_of(lambda: client.consent("DI_CONNECT_UPLOAD"))
    assert said == words and code == status
