"""The software and map checks of a device go through the kept answers (answers): the device
page and its page share one request, a GarminDevice.xml that changed (the device plugged in
again after an update) is asked anew, and what may have changed is forgotten: Refresh, Check
Again, an update installed, a map activated. Counted on a made-up update server; all devices
are made up."""
import types
from datetime import timedelta

import pytest
from fakes import RTL515_XML, UpdateServer
from fakes import window as stand_in
from test_fixes_ui import offered  # noqa: F401 - the fixture

from openexpress import answers
from openexpress.connect import GarminClient
from openexpress.settings import Settings

UNIT = 3000000003
XML = RTL515_XML.encode()
NEWER = RTL515_XML.replace("<SoftwareVersion>336", "<SoftwareVersion>340").encode()


def _client(server):
    return GarminClient(Settings(), None, None, server)


def _paths(server):
    return [url.rsplit("/", 2)[-2] + "/" + url.rsplit("/", 1)[-1] for _m, url, _p, _b in
            server.sent]


@pytest.fixture
def server():
    return UpdateServer({"updates": [], "maps": []})


def test_the_device_page_and_its_page_share_one_software_check(server):
    client = _client(server)
    client.software_updates(XML, include_reinstalls=True)  # the row of the device page
    client.software_updates(XML, include_reinstalls=True)  # the page Updates
    assert _paths(server) == ["v1/updates"]


def test_the_device_page_and_its_page_share_one_map_check(server):
    client = _client(server)
    client.universal_maps(XML)
    client.universal_maps(XML)
    assert _paths(server) == ["universal/update"]


def test_another_device_or_its_xml_after_an_update_is_asked_anew(server):
    """Plugged in again after an update, the device's GarminDevice.xml says another version:
    another key, a new request. The same XML (plugged in again, nothing changed): the answer
    kept."""
    client = _client(server)
    client.software_updates(XML, include_reinstalls=True)
    client.software_updates(XML, include_reinstalls=True)  # plugged in again, the same XML
    client.software_updates(NEWER, include_reinstalls=True)
    assert len(server.sent) == 2


def test_another_language_or_environment_is_asked_anew(server):
    client = _client(server)
    client.software_updates(XML)
    client.settings.chosen_locale = "de_DE"
    client.software_updates(XML)
    client.settings.environment = "china"
    client.software_updates(XML)
    assert len(server.sent) == 3


def test_fresh_asks_anew(server):
    client = _client(server)
    client.universal_maps(XML)
    client.universal_maps(XML, fresh=True)
    assert len(server.sent) == 2


def test_an_hour_later_it_is_asked_anew(server, monkeypatch, clock):
    client = _client(server)
    client.software_updates(XML)
    clock.current += timedelta(seconds=59 * 60)
    client.software_updates(XML)
    clock.current += timedelta(seconds=2 * 60)
    client.software_updates(XML)
    assert len(server.sent) == 2


# --- what forgets them ------------------------------------------------------------------------


def _window(unit=UNIT):
    return stand_in(session=types.SimpleNamespace(device=types.SimpleNamespace(unit_id=unit)),
                    loaded=[], load_section=None)


def test_refresh_forgets_the_checks_of_the_device_open(server):
    from openexpress.gui.tasks import forget_on_refresh

    client = _client(server)
    client.software_updates(XML)
    client.universal_maps(XML)
    other = RTL515_XML.replace("3000000003", "3000000004").encode()
    client.universal_maps(other)  # another device: kept
    forget_on_refresh(_window().session)
    client.software_updates(XML)
    client.universal_maps(XML)
    client.universal_maps(other)
    assert _paths(server) == ["v1/updates", "universal/update", "universal/update",
                              "v1/updates", "universal/update"]


def test_check_again_on_the_updates_page_asks_anew(server):
    from openexpress.gui.updates_page import check_updates_again

    client = _client(server)
    client.software_updates(XML, include_reinstalls=True)
    win = _window()
    win.load_section = lambda key, force=False: win.loaded.append((key, force))
    check_updates_again(win)
    client.software_updates(XML, include_reinstalls=True)
    assert len(server.sent) == 2 and win.loaded == [("updates", True)]


def test_check_again_on_the_maps_page_asks_anew(server, monkeypatch):
    from openexpress import service
    from openexpress.gui import maps_page

    monkeypatch.setattr(service, "forget_unit_info", lambda session: None)
    client = _client(server)
    client.universal_maps(XML)
    win = _window()
    win.load_section = lambda key, force=False: win.loaded.append((key, force))
    maps_page._check_again(win)
    client.universal_maps(XML)
    assert len(server.sent) == 2 and win.loaded == [("maps", True)]


def test_an_activated_map_forgets_the_maps_of_the_device(server):
    client = _client(server)
    client.universal_maps(XML)
    client.activate_maps(XML, [{"id": 1}])
    client.universal_maps(XML)
    assert _paths(server) == ["universal/update", "universal/activate", "universal/update"]


def test_an_update_installed_forgets_the_software_check(server, monkeypatch):
    """The window's install (and the command line's): the next check asks anew, also when
    the copy failed (a part may be there)."""
    from openexpress import updates
    from openexpress.gui.updates_page import install_updates

    client = _client(server)
    client.software_updates(XML, include_reinstalls=True)

    def cable_out(*a, **kw):
        raise OSError("the cable came out")

    monkeypatch.setattr(updates, "_install", cable_out)
    session = types.SimpleNamespace(storage=None, client=client,
                                    device=types.SimpleNamespace(unit_id=UNIT, raw=XML))
    with pytest.raises(OSError):
        install_updates(session, [], None)
    client.software_updates(XML, include_reinstalls=True)
    assert len(server.sent) == 2


@pytest.mark.parametrize("copied", [True, False])
def test_the_command_line_install_forgets_it_too(offered, monkeypatch, capsys, copied):
    """`updates --install … --apply`: the next check of the device asks anew, also when the
    copy failed (a part may be there)."""
    from conftest import DEVICE_XML

    from openexpress import updates
    from openexpress.cli import main
    from openexpress.device.xmldevice import parse

    root, _answer = offered
    unit = parse(DEVICE_XML.encode()).unit_id
    asked = []

    def check():
        asked.append(1)
        return {"updates": []}

    answers.ANSWERS.ask(answers.SOFTWARE_UPDATES, ("made-up",), check, about=[unit])

    def install(chosen, storage, session=None, progress=None):
        if not copied:
            raise OSError("the cable came out")
        return types.SimpleNamespace(installed=[])

    monkeypatch.setattr(updates, "_install", install)
    main(["--path", str(root), "updates", "--install", "006-DA003-00", "--apply"])
    answers.ANSWERS.ask(answers.SOFTWARE_UPDATES, ("made-up",), check, about=[unit])
    assert asked == [1, 1]


# A GarminDevice.xml written otherwise, as XML allows: the unit ID read as the program reads
# it (xmldevice), whatever the prefix or the spaces in a tag.
OTHERWISE = {
    "a prefix": RTL515_XML.replace(
        'xmlns="http://www.garmin.com/xmlschemas/GarminDevice/v2"',
        'xmlns:g="http://www.garmin.com/xmlschemas/GarminDevice/v2"')
    .replace("<", "<g:").replace("<g:/", "</g:").replace("<g:?", "<?"),
    "a space in the tag": RTL515_XML.replace("<Id>", "<Id >"),
}


@pytest.mark.parametrize("written", sorted(OTHERWISE))
def test_refresh_and_an_install_forget_also_an_xml_written_otherwise(server, monkeypatch,
                                                                     written):
    from openexpress import updates
    from openexpress.device.xmldevice import parse
    from openexpress.gui.tasks import forget_on_refresh
    from openexpress.gui.updates_page import install_updates

    xml = OTHERWISE[written].encode()
    device = parse(xml)
    assert device.unit_id == UNIT  # the program reads it
    client = _client(server)
    client.software_updates(xml, include_reinstalls=True)
    client.universal_maps(xml)
    forget_on_refresh(types.SimpleNamespace(device=device))  # Refresh
    client.software_updates(xml, include_reinstalls=True)
    client.universal_maps(xml)
    monkeypatch.setattr(updates, "_install", lambda *a, **kw: [])
    install_updates(types.SimpleNamespace(storage=None, client=client, device=device), [],
                    None)  # an install
    client.software_updates(xml, include_reinstalls=True)
    assert _paths(server) == ["v1/updates", "universal/update", "v1/updates",
                              "universal/update", "v1/updates"]
