"""A map Garmin does not offer in a country (the map service's "Country" restriction). Garmin
Express takes the country from the computer's address, openExpress from the account's
profile, asked only for a restricted map on a device of the account. Such a map is not
installed or updated; with the country unknown, only marked. All data is made up."""

import html
import json
import logging
import types

import pytest
from test_country_names import countries  # noqa: F401 - a fixture of these tests
from test_updates_maps import _maps_window, maps_response

from openexpress import maps as mp
from openexpress import service
from openexpress.connect import ApiError
from openexpress.device.storage import LocalStorage
from openexpress.device.xmldevice import parse

BASE = "https://maps.example"


@pytest.fixture(autouse=True)
def made_up_countries(countries):
    """The names of the countries from a made-up list (KR: South Korea), not the system's."""

REASON = "Cannot be installed: Garmin does not offer this map in your account's country"


def answer(restricted=("TopoActive Japan",), restriction=None):
    """The made-up answer of the map service, with these maps restricted to KR."""
    data = maps_response(BASE)
    for m in data["maps"]:
        if m["displayname"] in restricted:
            m["restriction"] = restriction or {"restrictiontype": "Country", "country": "kr"}
        else:
            m["restriction"] = None
    return data


def by_name(maps):
    return {m.name: m for m in maps}


def test_the_restriction_of_a_map_is_read(caplog):
    maps = by_name(mp.parse_maps(answer()))
    assert maps["TopoActive Japan"].restricted_in == "KR"
    assert maps["TopoActive Japan"].restricted is None  # the country is not known yet
    assert maps["TopoActive East"].restricted_in is None
    assert maps["TopoActive East"].restricted is False
    # Another kind of restriction, or another shape: not applied, said in the log.
    with caplog.at_level(logging.INFO, logger="openexpress"):
        for other in ({"restrictiontype": "Region", "country": "kr"}, "KR",
                      {"restrictiontype": "Country", "country": ""}):
            (japan,) = [m for m in mp.parse_maps(answer(restriction=other))
                        if m.name == "TopoActive Japan"]
            assert japan.restricted_in is None and japan.restricted is False
    assert any("a restriction of another kind" in r.getMessage() for r in caplog.records)


def test_a_map_restricted_in_the_account_country_cannot_be_installed(device_root):
    maps = mp.parse_maps(answer())
    mp.apply_country(maps, "KR")
    japan = by_name(maps)["TopoActive Japan"]
    assert japan.restricted is True and not mp.can_install(japan)
    assert mp.cannot_install_reason(japan) == REASON
    assert mp.cannot_install_message(japan) == (
        "Map 'TopoActive Japan' cannot be installed: Garmin does not offer it in your "
        "account's country (South Korea).")
    assert japan.status == "not offered in your account's country"
    assert mp.restriction_note(japan) is None  # said by the reason, not marked besides

    def never(identifier):
        raise AssertionError("activated")

    with pytest.raises(mp.MapError, match="does not offer it in your account's country"):
        mp.install(japan, maps, LocalStorage(device_root), never)


def test_an_update_restricted_in_the_account_country_is_not_offered():
    maps = mp.parse_maps(answer(restricted=("TopoActive East",)))
    mp.apply_country(maps, "KR")
    east = by_name(maps)["TopoActive East"]  # installed, a newer version on the server
    assert east.would_update and not east.update_available and not mp.can_install(east)
    assert east.status == "update not offered in your account's country"


def test_without_the_country_a_restricted_map_is_only_marked():
    maps = mp.parse_maps(answer(restricted=("TopoActive Japan", "TopoActive East")))
    mp.apply_country(maps, None)
    japan, east = by_name(maps)["TopoActive Japan"], by_name(maps)["TopoActive East"]
    assert mp.can_install(japan) and east.update_available  # neither held back
    assert mp.restriction_note(japan) == "Garmin does not offer this map in this country: South Korea"
    assert japan.status == "available" and east.status == "update available"


def test_another_country_changes_nothing():
    maps = mp.parse_maps(answer())
    mp.apply_country(maps, "PL")
    japan = by_name(maps)["TopoActive Japan"]
    assert japan.restricted is False and mp.can_install(japan)
    assert mp.restriction_note(japan) is None


def _client(data):
    return types.SimpleNamespace(
        unit_info=lambda unit_id, part_number: {"mapupdatetype": "Universal"},
        universal_maps=lambda raw: json.loads(json.dumps(data)))


DEVICE = types.SimpleNamespace(unit_id=3000000003, part_number="006-B0000-00", raw=b"xml",
                               files=[types.SimpleNamespace(datatype="PreProgrammedNamedMaps")],
                               datatypes=lambda: ["PreProgrammedNamedMaps"])


def test_the_account_country_is_asked_only_when_a_map_is_restricted(monkeypatch, caplog):
    monkeypatch.setattr(mp, "universal_serves", lambda value, device: True)
    asked = []

    def country():
        asked.append(1)
        return "KR"

    with caplog.at_level(logging.INFO, logger="openexpress"):
        mp.check(_client(answer(restricted=())), DEVICE, country=country)
        assert asked == []
        maps, _listed = mp.check(_client(answer()), DEVICE, country=country)
    assert asked == [1] and by_name(maps)["TopoActive Japan"].restricted is True
    lines = [r.getMessage() for r in caplog.records if "restricted by country" in r.getMessage()]
    assert lines == ["maps: 0 maps restricted by country",
                     ("maps: 1 maps restricted by country; the account's country: known; "
                      "1 apply")]
    assert not any("KR" in line for line in lines)


def _session(logged_in=True, ownership_error=None, error=False):
    asked = []

    def profile_country():
        asked.append(1)
        if error:
            raise ApiError("Account location: HTTP 500")
        return "KR"

    client = types.SimpleNamespace(auth=object() if logged_in else None,
                                   verified_unit_id=3000000003, profile_country=profile_country)
    s = service.DeviceSession(storage=None, device=None, settings=None, client=client,
                              ownership_error=ownership_error)
    return s, asked


def test_the_window_asks_the_account_once_per_session():
    s, asked = _session()
    assert service.account_country(s) == "KR" and service.account_country(s) == "KR"
    assert asked == [1]


def test_a_device_of_another_account_or_no_login_never_asks_the_account(caplog):
    for kw in ({"ownership_error": RuntimeError("not on the account")}, {"logged_in": False}):
        s, asked = _session(**kw)
        assert service.account_country(s) is None and asked == []
    s, asked = _session()
    s.client.verified_unit_id = None  # the account was not checked for the device
    assert service.account_country(s) is None and asked == []
    with caplog.at_level(logging.INFO, logger="openexpress"):
        s, asked = _session(error=True)
        assert service.account_country(s) is None and asked == [1]
    assert any("the account's country not read (ApiError)" in r.getMessage()
               for r in caplog.records)


# --- the Maps page ---------------------------------------------------------------------------


def _page_rows(device_root, country):
    from openexpress.gui.app import MainWindow

    maps = mp.parse_maps(answer(restricted=("TopoActive Japan", "TopoActive East")))
    mp.apply_country(maps, country)
    win, _reloads = _maps_window(LocalStorage(device_root))
    MainWindow.show_maps(win, (maps, len(maps)), None)
    out = {}
    for g in win.sections["maps"].groups:
        for row in g.rows:
            for r in _rows(row):
                buttons = [b.get_label() for b in _all(r) if hasattr(b, "get_label")
                           and type(b).__name__ == "Button" and b.get_label()]
                out[html.unescape(r.get_title())] = (html.unescape(r.get_subtitle()), buttons)
    return out


def _rows(widget):
    from gi.repository import Adw

    if isinstance(widget, Adw.ActionRow):
        return [widget]
    return [w for w in _all(widget) if isinstance(w, Adw.ActionRow)]


def _all(widget):
    child = widget.get_first_child() if hasattr(widget, "get_first_child") else None
    while child is not None:
        yield child
        yield from _all(child)
        child = child.get_next_sibling()


@pytest.mark.needs_display
def test_the_maps_page_has_no_button_for_a_map_not_offered_in_the_account_country(device_root):
    rows = _page_rows(device_root, "KR")
    japan_line, japan_buttons = rows["TopoActive Japan"]
    assert japan_line.endswith("\n" + REASON) and japan_buttons == []
    east_line, east_buttons = rows["TopoActive East"]
    assert east_line.endswith("\n" + REASON) and "Update" not in east_buttons


@pytest.mark.needs_display
def test_with_the_country_not_known_the_maps_page_marks_the_map(device_root):
    rows = _page_rows(device_root, None)
    japan_line, japan_buttons = rows["TopoActive Japan"]
    assert japan_line.endswith("\nGarmin does not offer this map in this country: South Korea")
    assert japan_buttons == ["Install"]
    assert "Update" in rows["TopoActive East"][1]


# --- the command line ------------------------------------------------------------------------


@pytest.fixture
def cli_maps(monkeypatch, device_root):
    """`openexpress maps` on the made-up device with the made-up answer; the account's country
    as `country` says (None: not known)."""
    from openexpress import cli

    state = types.SimpleNamespace(country=None, asked=0,
                                  answer=answer(restricted=("TopoActive Japan",
                                                            "TopoActive East")))

    def load(args, client, own=False):
        st = LocalStorage(device_root)
        maps, listed = mp.read_maps(json.loads(json.dumps(state.answer)))
        dev = parse(st.read("GARMIN/GarminDevice.xml"))

        def country():
            state.asked += 1
            return state.country

        mp.apply_restrictions(maps, country)
        return st, dev, maps, listed

    monkeypatch.setattr(cli, "_load_maps", load)
    return state


def test_cli_lists_a_map_with_the_country_not_known_as_marked(cli_maps, capsys):
    from openexpress.cli import main

    assert main(["maps", "--all"]) == 0
    out = capsys.readouterr().out.splitlines()
    japan = next(i for i, line in enumerate(out) if "TopoActive Japan" in line)
    assert out[japan].endswith("available")
    assert out[japan + 1] == "    Garmin does not offer this map in this country: South Korea"


def test_cli_refuses_to_install_a_map_not_offered_in_the_account_country(cli_maps, capsys):
    from openexpress.cli import main

    cli_maps.country = "KR"
    assert main(["maps", "install", "Japan"]) == 1
    assert ("Map 'TopoActive Japan' cannot be installed: Garmin does not offer it in your "
            "account's country (South Korea).") in capsys.readouterr().err


def test_cli_update_says_what_it_does_not_update(cli_maps, capsys):
    from openexpress.cli import main

    cli_maps.country = "KR"
    assert main(["maps", "update"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert ("TopoActive East: Garmin does not offer this map in your account's country; not "
            "updated.") in out
    assert "All installed maps are up to date." in out


def test_cli_asks_the_account_only_with_a_login_and_for_a_device_of_it(monkeypatch):
    from openexpress import cli
    from openexpress.auth import store
    from openexpress.connect import NotAccountDevice

    dev = types.SimpleNamespace(unit_id=3000000003)
    calls = []
    saved = types.SimpleNamespace(login=False)

    def make_client(settings, need_auth):
        """As cli.make_client: the saved login only when it is asked for, and NotLoggedIn
        then when none is saved."""
        if need_auth and not saved.login:
            raise store.NotLoggedIn("Not logged in. Run: openexpress login")
        return types.SimpleNamespace(auth=object() if need_auth else None,
                                     profile_country=lambda fresh=False: calls.append("country") or "KR")

    monkeypatch.setattr(cli, "make_client", make_client)
    monkeypatch.setattr(service, "require_account_device",
                        lambda c, unit_id, fresh=False: calls.append("owner") or [])
    assert cli._account_country(dev) is None and calls == []  # no login: nothing asked
    saved.login = True
    assert cli._account_country(dev) == "KR" and calls == ["owner", "country"]

    def refuse(c, unit_id, fresh=False):
        raise NotAccountDevice("This device is not registered to your Garmin account.")

    calls.clear()
    monkeypatch.setattr(service, "require_account_device", refuse)
    assert cli._account_country(dev) is None and calls == []  # another account's: not asked


# --- the command line, with the program's own client and a login saved ---------------------

LOCATION = "userprofile-service/userprofile/location"


@pytest.fixture
def saved_login(monkeypatch, device_root):
    """`openexpress maps` as it runs on the made-up device: the application key and a login
    saved as `openexpress setup` and `openexpress login` save them, and the made-up answers
    of the map service (TopoActive Japan not offered in KR) and of the account (its device
    list has the device, its profile says KR). Its `sent`: every request, in order."""
    from test_ownership import UNIT, RecordingSession, response

    from openexpress import connect
    from openexpress.auth import store
    from openexpress.settings import Consumer, save_consumer

    save_consumer(Consumer("made-up-key", "made-up-secret"))
    store.save(store.ConnectAuth("made-up-token", "made-up-token-secret"))
    http = RecordingSession([{"unitid": UNIT}], **{
        "api/unit-info/": response(body={"mapUpdateType": "Universal"}),
        "api/maps/universal/update": response(body=answer()),
        LOCATION: response(body={"countryCode": "kr"}),
    })
    monkeypatch.setattr(connect, "user_agent_session", lambda: http)
    monkeypatch.setattr(service, "open_device_or_drive",
                        lambda wait=True, notify=None: LocalStorage(device_root))
    return http


def account_asked(http):
    """The paths asked of Garmin Connect (the account), in order."""
    return [r["url"].split("garmin.com/")[-1].split("?")[0] for r in http.sent
            if r["url"].startswith("https://connectapi.")]


def japan_lines(out):
    """The line of TopoActive Japan in `openexpress maps --all` and the one after it."""
    lines = out.splitlines()
    at = next(i for i, line in enumerate(lines) if "TopoActive Japan" in line)
    return lines[at], lines[at + 1]


def test_cli_with_a_login_saved_checks_the_device_and_reads_the_account_country(
        saved_login, capsys):
    from test_ownership import DEVICES_PATH

    from openexpress.cli import main

    assert main(["maps", "--all"]) == 0
    line, _after = japan_lines(capsys.readouterr().out)
    assert line.endswith("not offered in your account's country")
    assert account_asked(saved_login) == [DEVICES_PATH, LOCATION]  # its owner first


def test_cli_with_a_login_saved_asks_only_the_owner_of_a_device_of_another_account(
        saved_login, capsys):
    from test_ownership import DEVICES_PATH, OTHER, response

    from openexpress.cli import main

    saved_login.routes[DEVICES_PATH] = response(body=[{"unitid": OTHER}])
    assert main(["maps", "--all"]) == 0
    line, after = japan_lines(capsys.readouterr().out)
    assert line.endswith("available")
    assert after == "    Garmin does not offer this map in this country: South Korea"
    assert account_asked(saved_login) == [DEVICES_PATH]


def test_cli_without_a_login_asks_the_account_nothing_and_says_no_error(
        saved_login, capsys, log_file):
    from openexpress.auth import store
    from openexpress.cli import main

    store.delete()
    assert main(["maps", "--all"]) == 0
    out = capsys.readouterr()
    line, after = japan_lines(out.out)
    assert line.endswith("available")
    assert after == "    Garmin does not offer this map in this country: South Korea"
    assert out.err == ""
    assert account_asked(saved_login) == []
    assert "the account's country not read" not in log_file.read_text()


def test_cli_checks_the_owner_with_the_list_kept_a_few_minutes(saved_login, capsys):
    """Reading only: the account's list may be the one kept (answers, 5 minutes); the check
    is made all the same."""
    from test_ownership import DEVICES_PATH

    from openexpress.cli import main

    assert main(["maps", "--all"]) == 0
    assert main(["maps", "--all"]) == 0
    # The country too is kept (answers, an hour).
    assert account_asked(saved_login) == [DEVICES_PATH, LOCATION]


def test_cli_asks_the_account_nothing_when_no_map_is_restricted(saved_login, capsys):
    from test_ownership import response

    from openexpress.cli import main

    saved_login.routes["api/maps/universal/update"] = response(body=answer(restricted=()))
    assert main(["maps", "--all"]) == 0
    assert account_asked(saved_login) == []


def test_cli_without_the_key_nor_the_login_lists_the_maps_and_asks_the_account_nothing(
        saved_login, capsys, log_file):
    from openexpress.auth import store
    from openexpress.cli import main
    from openexpress.settings import consumer_path

    store.delete()
    consumer_path().unlink()
    assert main(["maps", "--all"]) == 0
    _line, after = japan_lines(capsys.readouterr().out)
    assert after == "    Garmin does not offer this map in this country: South Korea"
    assert account_asked(saved_login) == []
    assert "maps: the account's country not read (SetupError)" in log_file.read_text()


@pytest.mark.parametrize(("devices", "location", "asked", "said"), [
    ("another account", None, ["devices"], "NotAccountDevice"),
    (None, 500, ["devices", "location"], "ApiError"),
    (401, None, ["devices"], "Unauthorized"),
    ("offline", None, ["devices"], "OwnershipUnknown"),
])
def test_cli_marks_the_map_when_the_country_cannot_be_read_and_says_why_in_the_log(
        saved_login, capsys, log_file, devices, location, asked, said):
    import requests
    from test_ownership import DEVICES_PATH, OTHER, response

    from openexpress.cli import main

    if devices == "offline":
        saved_login.routes[DEVICES_PATH] = requests.ConnectionError("offline")
    elif devices == 401:
        saved_login.routes[DEVICES_PATH] = response(401, b"")
    elif devices is not None:
        saved_login.routes[DEVICES_PATH] = response(body=[{"unitid": OTHER}])
    if location is not None:
        saved_login.routes[LOCATION] = response(location, b"")
    assert main(["maps", "--all"]) == 0
    _line, after = japan_lines(capsys.readouterr().out)
    assert after == "    Garmin does not offer this map in this country: South Korea"
    paths = {"devices": DEVICES_PATH, "location": LOCATION}
    assert account_asked(saved_login) == [paths[a] for a in asked]
    assert f"maps: the account's country not read ({said})" in log_file.read_text()


def window_session(device_root):
    from openexpress.device.storage import LocalStorage

    return service.check_session(service.open_session(storage=LocalStorage(device_root)))


def test_the_window_checks_the_owner_with_the_session_and_asks_the_country_once(
        saved_login, device_root):
    from test_ownership import DEVICES_PATH

    s = window_session(device_root)
    assert service.account_country(s) == "KR" and service.account_country(s) == "KR"
    assert account_asked(saved_login) == [DEVICES_PATH, LOCATION]
    again = window_session(device_root)  # the device opened again: a new session
    assert service.account_country(again) == "KR"
    # Its owner checked with the list kept (answers, 5 minutes), the country kept (an hour).
    assert account_asked(saved_login) == [DEVICES_PATH, LOCATION]


def test_the_window_never_asks_the_country_for_a_device_of_another_account_or_logged_out(
        saved_login, device_root):
    from test_ownership import DEVICES_PATH, OTHER, response

    from openexpress.auth import store

    saved_login.routes[DEVICES_PATH] = response(body=[{"unitid": OTHER}])
    assert service.account_country(window_session(device_root)) is None
    assert account_asked(saved_login) == [DEVICES_PATH]
    store.delete()
    assert service.account_country(window_session(device_root)) is None
    assert account_asked(saved_login) == [DEVICES_PATH]


def test_the_words_in_polish():
    from openexpress import i18n

    maps = mp.parse_maps(answer(restricted=("TopoActive Japan", "TopoActive East")))
    i18n.setup("pl")
    try:
        mp.apply_country(maps, None)
        note = mp.restriction_note(by_name(maps)["TopoActive Japan"])
        mp.apply_country(maps, "KR")
        japan, east = by_name(maps)["TopoActive Japan"], by_name(maps)["TopoActive East"]
        words = (mp.cannot_install_reason(japan), mp.cannot_install_message(japan),
                 japan.status, east.status)
    finally:
        i18n.setup("en")
    assert note == "Garmin nie oferuje tej mapy w\u00a0tym kraju: Korea Południowa"
    assert words == (
        "Nie można zainstalować: Garmin nie oferuje tej mapy w\u00a0kraju konta",
        ("Nie można zainstalować mapy „TopoActive Japan”: Garmin nie oferuje jej w\u00a0kraju "
         "konta (Korea Południowa)."),
        "niedostępna w\u00a0kraju konta", "aktualizacja niedostępna w\u00a0kraju konta")
