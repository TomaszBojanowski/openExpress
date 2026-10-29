"""Get Picture From Account: a device off the account whose picture Garmin's update server does
not give (unit-info without an address, as for an epix) gets it from the account only when the
user clicks the button on its page, since that request names its unit ID to the account
(device-info). Every device here is made up."""

import types

import pytest
from fakes import RunNow
from test_fixes_ui import (  # noqa: F401 - pictures: the fixture
    PICTURE_URL,
    PNG,
    SECRET_UNIT,
    PictureClient,
    _cache,
    _finish,
    _picture_window,
    pictures,
)

from openexpress import known_devices
from openexpress.connect import NotAccountDevice

NONE_TEXT = "Your Garmin Connect account has no picture of this device"


class OffAccountClient(PictureClient):
    """unit-info gives no picture address; the account, asked about the device off it, gives
    `off_account_url`."""

    def __init__(self, off_account_url=None, **kw):
        super().__init__(unit_info={"devicemodel": "Test watch"}, **kw)
        self.off_account_url = off_account_url

    def device_image_url_off_account(self, unit_id):
        self.asked.append(("account, off it", unit_id))
        if isinstance(self.off_account_url, Exception):
            raise self.off_account_url
        return self.off_account_url


def _off_account(win):
    win.session.ownership_error = NotAccountDevice("not on the account")
    return win


def test_the_client_asks_the_account_about_a_device_off_it_only_by_its_own_method():
    from openexpress.connect import DeviceNotVerified, GarminClient

    client = GarminClient.__new__(GarminClient)
    client.verified_unit_id = None  # nothing checked against the account
    sent = []
    client._connect = lambda method, path, what, **kw: sent.append((method, path)) or object()
    client._json = lambda r, what: {"imageUrl": PICTURE_URL}
    with pytest.raises(DeviceNotVerified):
        client.device_image_url(SECRET_UNIT)  # the usual method: never about a device not checked
    assert sent == []
    assert client.device_image_url_off_account(SECRET_UNIT) == PICTURE_URL
    assert sent == [("GET", f"device-service/deviceservice/device-info/{SECRET_UNIT}")]


def test_opening_never_asks_the_account_and_offers_the_button(pictures):
    _xdg, _log, monkeypatch = pictures
    client = OffAccountClient(off_account_url=PICTURE_URL)
    win = _off_account(_picture_window(client, logged_in=True, monkeypatch=monkeypatch))
    win.show()
    _finish(win)
    assert client.asked == ["unit-info"]  # never the account on its own
    assert win.shown == [] and win.account_picture_offer is win.session


@pytest.mark.parametrize("case", ["on the account", "not logged in", "a drive",
                                  "unit-info has it"])
def test_the_button_only_for_a_device_off_the_account_without_a_picture(pictures, case):
    from test_fixes_ui import _unit_info

    _xdg, _log, monkeypatch = pictures
    client = OffAccountClient(off_account_url=PICTURE_URL)
    if case == "unit-info has it":
        client.info = _unit_info()
    win = _picture_window(client, logged_in=case != "not logged in", monkeypatch=monkeypatch)
    if case != "on the account":
        _off_account(win)
    win.session.mass_storage = case == "a drive"
    win.show()
    while win.jobs:
        _finish(win)
    assert getattr(win, "account_picture_offer", None) is None
    assert not [a for a in client.asked if a != "unit-info" and a != "account"]


def _clicking_window(client, monkeypatch):
    win = _off_account(_picture_window(client, logged_in=True, monkeypatch=monkeypatch))
    win.show()
    _finish(win)
    shown_tiles, toasts, rows, errors = [], [], [], []
    win.start = types.SimpleNamespace(set_picture=lambda part, texture: shown_tiles.append(
        (part, texture)))
    win.toast = toasts.append
    win.account_picture_row = types.SimpleNamespace(set_visible=rows.append)
    win.error = lambda title, text: errors.append((title, text))
    win.run_task = RunNow()
    win.errors = errors
    return win, shown_tiles, toasts, rows


def test_the_button_gets_the_picture_into_the_cache(pictures):
    from openexpress.gui.device_page import get_picture_from_account

    xdg, log_file, monkeypatch = pictures
    known_devices.remember(SECRET_UNIT, "Test watch", "006-B0000-00", "1.00", "mtp", now=1.0)
    client = OffAccountClient(off_account_url=PICTURE_URL)
    win, shown_tiles, toasts, rows = _clicking_window(client, monkeypatch)
    get_picture_from_account(win)
    assert client.asked == ["unit-info", ("account, off it", SECRET_UNIT)]
    assert _cache(xdg) == ["device-006-B0000-00.png"]
    assert win.shown == [("texture", PNG)]
    assert shown_tiles == [("006-B0000-00", ("texture", PNG))]  # its tile too
    assert known_devices.load()[SECRET_UNIT].image_url == PICTURE_URL
    assert rows == [False] and win.account_picture_offer is None and toasts == []
    log = log_file.read_text()
    assert "device picture: from the account, asked on request" in log
    # The host of the picture only (known_devices.set_image_url), never the unit ID or the
    # rest of the address.
    assert str(SECRET_UNIT) not in log and "cf-lg-1234" not in log


def test_the_account_without_a_picture_says_so(pictures):
    from openexpress.gui.device_page import get_picture_from_account

    xdg, _log, monkeypatch = pictures
    client = OffAccountClient(off_account_url=None)
    win, shown_tiles, toasts, rows = _clicking_window(client, monkeypatch)
    get_picture_from_account(win)
    assert toasts == [NONE_TEXT] and rows == [False]  # not offered again
    assert _cache(xdg) == [] and win.shown == [] and shown_tiles == []


# The account, asked about an epix not on it, answers HTTP 404 with a JSON {"error": …}:
# the window says so in a short sentence, never the server's words, and the button stays
# (a try later may answer).
UNKNOWN_TEXT = "Garmin Connect does not know this device, so it has no picture of it."
REFUSED_TEXT = "Garmin Connect could not be asked for the picture. Try again later."


@pytest.mark.parametrize(("status", "words"), [(404, UNKNOWN_TEXT), (500, REFUSED_TEXT),
                                               (None, REFUSED_TEXT)])
def test_a_refusal_of_the_account_is_said_in_short(pictures, status, words):
    from openexpress.connect import ApiError
    from openexpress.gui.device_page import get_picture_from_account

    xdg, _log, monkeypatch = pictures
    refusal = ApiError(f"Device info: HTTP {status}", status, '{"error": "made up"}')
    client = OffAccountClient(off_account_url=refusal)
    win, shown_tiles, toasts, rows = _clicking_window(client, monkeypatch)
    get_picture_from_account(win)
    assert win.errors == [("Device picture", words)]
    assert rows == [] and win.account_picture_offer is win.session  # the button stays
    assert _cache(xdg) == [] and toasts == [] and shown_tiles == []
