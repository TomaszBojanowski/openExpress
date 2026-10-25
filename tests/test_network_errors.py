"""Network errors say neither the address nor the device (code audit of 25.10.2026): the text
of a requests error names the whole address, query and all. The Connect IQ store asked with
the unit ID and the part number in its query and passed that text on, unfiltered (the command
line printed it, the window showed it); a failed download put the signed address of a file
(its garmindlm signature) into the error the user sees. Both now go the way of the client:
ApiError (DownloadError), the address left out, filtered as the log. All made up."""

import types
from urllib.parse import urlsplit

import pytest
import requests

from openexpress import netstatus
from openexpress.connect import ApiError, GarminClient
from openexpress.settings import Settings

UNIT = 3000000101
SIGNATURE = "ab" * 16


def _refused_like_requests(url):
    """The error requests raises when nothing answers: its text names the path and query."""
    where = urlsplit(url)
    return requests.ConnectionError(
        f"HTTPSConnectionPool(host='{where.hostname}', port=443): Max retries exceeded with "
        f"url: {where.path}?{where.query} (Caused by NewConnectionError('<urllib3.connection."
        f"HTTPSConnection object at 0x7f0000000000>: Failed to establish a new connection: "
        f"[Errno 111] Connection refused'))")


def test_a_network_error_of_the_store_names_neither_the_unit_id_nor_the_address(
        monkeypatch, network_state):
    from openexpress import ciq

    client = GarminClient(Settings())
    client.require_verified = lambda unit_id: None
    client._sign = lambda method, url, *a, **kw: "OAuth x"

    def request(self, method, url, **kw):
        raise _refused_like_requests(url)

    monkeypatch.setattr(requests.Session, "request", request)
    device = types.SimpleNamespace(unit_id=UNIT, part_number="006-B4532-00")
    with pytest.raises(ApiError) as said:
        ciq.StoreApi(client).account_apps(device, "en_US")
    text = str(said.value)
    assert text.startswith("Connect IQ apps: network error (")
    assert str(UNIT) not in text and "unitId" not in text and "sku" not in text
    assert "/express/" not in text and "?" not in text and "Connection refused" in text
    assert network_state.snapshot()[0][netstatus.CONNECT].kind == netstatus.NETWORK


def test_a_failed_download_does_not_show_the_signed_address(tmp_path):
    from openexpress import download

    url = f"https://download.garmin.com/a.img?garmindlm={SIGNATURE}&id=1"

    class Broken:
        def get(self, url, **kw):
            raise _refused_like_requests(url)

    with pytest.raises(download.DownloadError) as said:
        download.fetch([url], tmp_path / "a", None, download.NO_MD5, session=Broken())
    text = str(said.value)
    assert "https://download.garmin.com/a.img: " in text  # which file, without its query
    assert SIGNATURE not in text and "garmindlm" not in text and "?" not in text


def test_the_command_line_says_a_network_error_without_its_address(monkeypatch, capsys):
    from openexpress import cli

    def broken(args):
        raise _refused_like_requests(
            f"https://apps.garmin.com/express/appstore/rest/apps/downloads?unitId={UNIT}")

    monkeypatch.setattr(cli, "cmd_info", broken)
    assert cli.main(["info"]) == 1
    err = capsys.readouterr().err
    assert err.startswith("Network error: could not reach the Garmin server (")
    assert str(UNIT) not in err and "unitId" not in err and "/express/" not in err
