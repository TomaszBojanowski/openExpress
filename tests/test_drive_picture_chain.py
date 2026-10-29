"""The picture of a device in mass storage mode (a Varia), from beginning to end: with nothing in
the cache its page asks Garmin's update server (unit-info, not the account), downloads the
picture and keeps its address with the device; once the pictures of the cache are cleared, the
device's tile on the start page downloads it again from that address. The device, its answers
and its picture are made up; no request leaves."""

import logging
import types

from fakes import replace_in_window, window_methods
from fakes import window as stand_in

UNIT, PART = 3100000515, "006-B3592-03"
URL = "https://res.garmin.com/en/products/made-up/varia.png"
PNG = b"\x89PNG\r\n\x1a\n" + b"made-up picture"


class Response:
    status_code, headers = 200, {"Content-Type": "image/png"}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def iter_content(self, size):
        yield PNG


class Http:
    def __init__(self):
        self.asked = []

    def get(self, url, **kw):
        self.asked.append(url)
        return Response()


class Client:
    """The update server's answer (unit-info) and the downloads; the account is never asked
    about a drive."""

    def __init__(self, http):
        self.http, self.asked = http, []

    def unit_info(self, unit_id, part_number):
        self.asked.append("unit-info")
        return {"graphicurl": URL, "segmenttype": "BikeAccessory"}

    def device_image_url(self, unit_id):
        raise AssertionError("the account is not asked about a drive")


def _picture_file():
    from openexpress.gui.pictures import device_picture_path

    return device_picture_path(PART)


def test_a_drive_keeps_the_address_of_its_picture_and_its_tile_gets_it_again(monkeypatch,
                                                                             caplog):
    from openexpress import cache, devicelist, known_devices
    from openexpress.gui.app import MainWindow

    replace_in_window(monkeypatch, "load_texture",
                      lambda path: ("texture", path.read_bytes())
                      if path.read_bytes().startswith(PNG[:8]) else None)
    known_devices.seen(UNIT, "Varia RTL515", PART, "3.36", "ums")  # as its opening does
    assert not _picture_file().exists()

    # Its page, the drive connected: nothing in the cache, so unit-info is asked.
    http = Http()
    client = Client(http)
    device = types.SimpleNamespace(unit_id=UNIT, part_number=PART, description="Varia RTL515")
    session = types.SimpleNamespace(device=device, client=client, logged_in=True,
                                    ownership_error=None, mass_storage=True,
                                    unit_info_answer=None)
    shown, jobs = [], []
    hidden = types.SimpleNamespace(set_visible=lambda visible: None,
                                   set_from_icon_name=lambda name: None)
    page = stand_in(session=session, device_image=hidden, device_picture=hidden,
                    _set_device_texture=shown.append,
                    in_background=lambda work, done, failed=None: jobs.append((work, done)))
    with caplog.at_level(logging.INFO, logger="openexpress"):
        MainWindow._show_device_picture(page)
        work, done = jobs.pop()
        done(work())
    assert client.asked == ["unit-info"] and http.asked == [URL]
    assert shown == [("texture", PNG)] and _picture_file().read_bytes() == PNG
    assert known_devices.load()[UNIT].image_url == URL  # its address kept with it
    assert "device picture: from unit-info" in caplog.messages

    # The pictures of the cache cleared (Preferences, Storage, Pictures).
    cache.clear(["pictures"])
    assert not _picture_file().exists()

    # The start page, the drive not connected: its tile takes the address kept with it.
    (tile,) = devicelist.tiles([], known_devices.load(), None, False)
    assert (tile.image_part_number, tile.image_url) == (PART, URL)
    again = Http()
    replace_in_window(monkeypatch, "account_client", lambda: types.SimpleNamespace(http=again))
    drawn = []
    start = stand_in(start=types.SimpleNamespace(set_picture=lambda part, texture:
                                                 drawn.append((part, texture))),
                     in_background=lambda work, done, failed=None: done(work()))
    window_methods(start, ["_load_tile_pictures", "_tile_picture_done"])
    with caplog.at_level(logging.INFO, logger="openexpress"):
        start._load_tile_pictures([tile])
    assert again.asked == [URL] and _picture_file().read_bytes() == PNG
    assert drawn == [(PART, ("texture", PNG))]
    assert f"start page: picture of {PART} from the remembered address" in caplog.messages
