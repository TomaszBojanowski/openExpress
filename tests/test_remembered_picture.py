"""The read-only view of a remembered device shows what its tile shows: the picture of the
model when it is known (in memory from the start page, or in the cache), else the icon of
its kind; never the icon of the program. All data here is made up."""

import logging
import time
import types

import pytest
from fakes import replace_in_window, window_methods

from openexpress.known_devices import KnownDevice

PART = "006-B0000-00"
APP_ID = "io.github.tomaszbojanowski.OpenExpress"


def _remembered(category="watch", part=PART):
    now = time.time()
    return KnownDevice(unit_id=1234567890, description="Test watch", part_number=part,
                       software_version="1.00", kind="mtp", first_seen=now - 86400,
                       last_seen=now - 3600, category=category)


def _texture():
    from gi.repository import Gdk, GLib

    return Gdk.MemoryTexture.new(2, 2, Gdk.MemoryFormat.R8G8B8A8, GLib.Bytes.new(bytes(16)), 8)


# The view of a remembered device and its pictures (once picked by name: "picture",
# "remembered"), found along the whole MRO (fakes.window_methods).
REMEMBERED_METHODS = ("_show_remembered", "_show_account", "_fill_account", "_last_sync_text",
                      "_fill_remembered", "_load_tile_pictures", "_on_remembered_page",
                      "_redraw_remembered", "_refresh_remembered", "_refresh_remembered_texts",
                      "_show_device_picture", "_show_page_picture", "_tile_picture_done")


def _window(textures=None):
    """The parts of the window that open the view, with the real view in it; background work
    runs at once."""
    from openexpress.gui.start import RememberedPage

    pushed = []
    win = types.SimpleNamespace(
        remembered=RememberedPage(), navigation=types.SimpleNamespace(push=pushed.append),
        pushed=pushed, _device_textures=dict(textures or {}), _pictures_asked=set(),
        _account=None, _on_start_page=lambda: True,
        in_background=lambda fn, on_done, on_error=None: on_done(fn()))
    return window_methods(win, REMEMBERED_METHODS)


# The arrow of the row "Last sync" (an expander row): not the device's image.
EXPANDER_ARROW = "adw-expander-arrow-symbolic"


def _shown(page):
    """(icon names, paintables) of the visible images and pictures in the view's content."""
    from gi.repository import Gtk

    icons, paintables = [], []

    def walk(widget):
        if not widget.get_visible():
            return
        if isinstance(widget, Gtk.Image):
            if widget.get_storage_type() == Gtk.ImageType.PAINTABLE:
                paintables.append(widget.get_paintable())
            elif widget.get_icon_name() and widget.get_icon_name() != EXPANDER_ARROW:
                icons.append(widget.get_icon_name())
        elif isinstance(widget, Gtk.Picture) and widget.get_paintable() is not None:
            paintables.append(widget.get_paintable())
        child = widget.get_first_child()
        while child is not None:
            walk(child)
            child = child.get_next_sibling()

    walk(page.get_child().get_content())
    return icons, paintables


@pytest.mark.needs_display
def test_the_view_shows_the_picture_the_start_page_already_has(caplog):
    """The tile shows the picture from the cache: the view shows it too."""
    texture = _texture()
    win = _window({PART: texture})
    with caplog.at_level(logging.INFO, logger="openexpress"):
        win._show_remembered(_remembered())
    assert win.pushed == [win.remembered.page]
    icons, paintables = _shown(win.remembered.page)
    assert texture in paintables
    assert APP_ID not in icons
    assert [r.getMessage() for r in caplog.records if "device page" in r.getMessage()] == [
        f"device page: picture of {PART} from memory"]


@pytest.mark.needs_display
def test_the_view_shows_the_picture_from_the_cache():
    from openexpress.gui.app import device_picture_path

    path = device_picture_path(PART)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_texture().save_to_png_bytes().get_data())
    win = _window()
    win._show_remembered(_remembered())
    icons, paintables = _shown(win.remembered.page)
    assert len(paintables) == 1 and APP_ID not in icons


@pytest.mark.needs_display
@pytest.mark.parametrize("category, icon", [
    ("watch", f"{APP_ID}-watch-symbolic"),
    ("cycling-computer", f"{APP_ID}-cycling-computer-symbolic"),
    (None, f"{APP_ID}-device-symbolic"),
])
def test_without_a_picture_the_view_shows_the_icon_of_the_kind(category, icon):
    win = _window()
    win._show_remembered(_remembered(category))
    icons, paintables = _shown(win.remembered.page)
    assert icons == [icon] and paintables == []


@pytest.mark.needs_display
def test_the_next_device_does_not_keep_the_picture_of_the_one_before():
    texture = _texture()
    win = _window({PART: texture})
    win._show_remembered(_remembered())
    win._show_remembered(_remembered("cycling-computer", part="006-B0001-00"))
    icons, paintables = _shown(win.remembered.page)
    assert icons == [f"{APP_ID}-cycling-computer-symbolic"] and paintables == []


ACCOUNT_PART = "006-B0002-00"
ACCOUNT_URL = "https://res.garmin.com/en/products/test-bike-computer.png"


def _account_entry():
    return {"unitid": 1234567891, "productdisplayname": "Test bike computer",
            "partnumber": ACCOUNT_PART, "currentfirmwareversion": "2.00",
            "imageurl": ACCOUNT_URL, "devicecategories": ["DEDICATED_BIKE_COMPUTER"]}


def _fake_download(monkeypatch):
    """The account's picture "downloaded" here: written to the cache, no request leaves."""
    from openexpress.gui import app

    downloads = []

    def download(http, url, name):
        downloads.append(url)
        target = app.picture_target(name)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(_texture().save_to_png_bytes().get_data())
        return target, ""

    replace_in_window(monkeypatch, "download_picture", download)
    replace_in_window(monkeypatch, "account_client", lambda: types.SimpleNamespace(http=object()))
    return downloads


OTHER_BUCKET_URL = "https://s3.amazonaws.com/other-bucket/devices/x.png?X-Amz-Signature=secret"


class _Unasked:
    """Stands in for the account's HTTP session: records what would be asked."""

    def __init__(self):
        self.calls = []

    def get(self, url, **kw):
        self.calls.append(url)
        raise AssertionError("an address that is not allowed was asked")


def _tile_with_an_address_not_allowed(monkeypatch):
    from openexpress import devicelist

    http = _Unasked()
    replace_in_window(monkeypatch, "account_client", lambda: types.SimpleNamespace(http=http))
    (tile,) = devicelist.tiles([], {}, [dict(_account_entry(), imageurl=OTHER_BUCKET_URL)],
                               True)
    assert (tile.image_url, tile.image_source) == (OTHER_BUCKET_URL, "the account")
    win = _window()
    shown = []
    win.start = types.SimpleNamespace(set_picture=lambda part, texture: shown.append(part))
    return win, tile, http, shown


@pytest.mark.needs_display
def test_a_tile_whose_address_is_not_allowed_takes_the_picture_in_the_cache(monkeypatch,
                                                                            caplog):
    """The rule of the addresses is asked only when the picture is not in the cache: a tile of
    the account with an address that is not allowed (a bucket on S3 that is not Garmin
    Connect's) shows the file kept, asks nothing and logs no refusal."""
    from openexpress.gui import app

    path = app.device_picture_path(ACCOUNT_PART)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_texture().save_to_png_bytes().get_data())
    win, tile, http, shown = _tile_with_an_address_not_allowed(monkeypatch)
    with caplog.at_level(logging.INFO, logger="openexpress"):
        win._load_tile_pictures([tile])
    assert shown == [ACCOUNT_PART] and http.calls == []
    logged = [r.getMessage() for r in caplog.records]
    assert f"start page: picture of {ACCOUNT_PART} from the cache" in logged
    assert not any("refused" in m or "no picture" in m for m in logged), logged


@pytest.mark.needs_display
def test_without_the_file_the_address_of_the_tile_is_refused(monkeypatch, caplog):
    """No file in the cache: the address is refused as before, nothing is asked, the icon
    stays; the log names the host, the bucket and whether the address has a query, never
    the rest of it."""
    from openexpress.gui import app

    win, tile, http, shown = _tile_with_an_address_not_allowed(monkeypatch)
    with caplog.at_level(logging.INFO, logger="openexpress"):
        win._load_tile_pictures([tile])
    assert shown == [] and http.calls == []
    assert not app.device_picture_path(ACCOUNT_PART).exists()
    logged = [r.getMessage() for r in caplog.records]
    assert [m for m in logged if m.startswith(f"pictures: no picture of {ACCOUNT_PART} ")] == [
        (f"pictures: no picture of {ACCOUNT_PART} (download refused: host s3.amazonaws.com, "
         "first segment other-bucket: not an allowed bucket; with query)")]
    for private in ("secret", "Signature", "devices/x.png", "x.png"):
        assert private not in caplog.text, private


def _later(win):
    """Background work waits until the test runs it."""
    later = []
    win.in_background = lambda fn, on_done, on_error=None: later.append((fn, on_done))
    return later


@pytest.mark.needs_display
def test_the_account_view_shows_the_icon_then_the_picture_from_the_account(monkeypatch,
                                                                           caplog):
    downloads = _fake_download(monkeypatch)
    win = _window()
    later = _later(win)
    with caplog.at_level(logging.INFO, logger="openexpress"):
        win._show_account(_account_entry())
        assert _shown(win.remembered.page) == (
            [f"{APP_ID}-cycling-computer-symbolic"], [])  # at once: the icon of the kind
        for fn, on_done in later:
            on_done(fn())
    icons, paintables = _shown(win.remembered.page)
    assert icons == [] and len(paintables) == 1
    assert downloads == [ACCOUNT_URL]
    assert win._device_textures[ACCOUNT_PART] is paintables[0]  # the tiles have it too
    assert f"device page: picture of {ACCOUNT_PART} from the account" in caplog.text


@pytest.mark.needs_display
def test_a_late_picture_of_the_device_before_does_not_replace_the_image_of_the_next(
        monkeypatch):
    _fake_download(monkeypatch)
    win = _window()
    later = _later(win)
    win._show_account(_account_entry())  # its picture is still being read
    win._show_remembered(_remembered())  # another device, with no picture
    for fn, on_done in later:
        on_done(fn())
    assert _shown(win.remembered.page) == ([f"{APP_ID}-watch-symbolic"], [])
    assert win._device_textures[ACCOUNT_PART] is not None  # kept for its tile


@pytest.mark.needs_display
def test_the_view_guesses_the_icon_from_the_name_as_the_tile_does():
    """Venu 2 remembered without a word from Garmin on its kind: a watch's icon, as on its
    tile (the name only for the icon, never stored)."""
    win = _window()
    known = _remembered(category=None)
    known.description = "Venu 2"
    win._show_remembered(known)
    assert _shown(win.remembered.page) == ([f"{APP_ID}-watch-symbolic"], [])


def test_a_picture_is_loaded_without_a_deprecated_call(tmp_path):
    """A real small PNG becomes a texture with no DeprecationWarning (Gdk.Texture.new_for_pixbuf
    is deprecated); a file that is no picture gives None, as before."""
    import warnings

    from gi.repository import Gdk

    from openexpress.gui import app

    path = tmp_path / "device-006-B0000-00.png"
    path.write_bytes(_texture().save_to_png_bytes().get_data())
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"not a picture")
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        texture = app.load_texture(path)
        assert isinstance(texture, Gdk.Texture)
        assert (texture.get_width(), texture.get_height()) == (2, 2)
        assert app.load_texture(bad) is None


# --- the picture of a device off the account, from the address remembered with it ------------

REMEMBERED_UNIT = 1234567892
REMEMBERED_URL = "https://res.garmin.com/en/products/test-venu.png"


def _remembered_with_address():
    from openexpress import known_devices

    known_devices.remember(REMEMBERED_UNIT, "Test watch", PART, "1.00", "mtp")
    assert known_devices.set_image_url(REMEMBERED_UNIT, REMEMBERED_URL)
    return known_devices.load()[REMEMBERED_UNIT]


@pytest.mark.needs_display
def test_a_tile_off_the_account_gets_its_picture_from_the_remembered_address(monkeypatch,
                                                                              caplog):
    """A Venu 2: off the account, its picture gone with the cache
    (Preferences, Clear), and unit-info is not asked for a device that is not connected. Its
    address was remembered when it was last connected: the start page downloads it again."""
    from openexpress import devicelist, known_devices

    downloads = _fake_download(monkeypatch)
    _remembered_with_address()
    (tile,) = devicelist.tiles([], known_devices.load(), None, False)
    win = _window()
    shown = []
    win.start = types.SimpleNamespace(set_picture=lambda part, texture: shown.append(part))
    with caplog.at_level(logging.INFO, logger="openexpress"):
        win._load_tile_pictures([tile])
    assert downloads == [REMEMBERED_URL] and shown == [PART]
    assert (f"start page: picture of {PART} from the remembered address"
            in [r.getMessage() for r in caplog.records])


@pytest.mark.needs_display
def test_the_page_of_a_device_off_the_account_gets_it_from_there_too(monkeypatch, caplog):
    """Its page opened without a tile (as from the list of devices): the same address."""
    downloads = _fake_download(monkeypatch)
    known = _remembered_with_address()
    win = _window()
    with caplog.at_level(logging.INFO, logger="openexpress"):
        win._show_remembered(known)
    icons, paintables = _shown(win.remembered.page)
    assert downloads == [REMEMBERED_URL] and len(paintables) == 1 and icons == []
    assert (f"device page: picture of {PART} from the remembered address"
            in [r.getMessage() for r in caplog.records])


def test_the_account_list_read_by_the_window_keeps_the_picture_addresses():
    """However the window reads the account's list (_set_account), the addresses of the
    pictures of the devices remembered here are kept with them, as their kinds are."""
    from openexpress import known_devices
    from openexpress.gui.app import MainWindow

    known_devices.remember(1234567891, "Test bike computer", ACCOUNT_PART, "2.00", "mtp")
    win = types.SimpleNamespace(_account=None,
                                in_background=lambda fn, on_done, on_error=None: on_done(fn()))
    MainWindow._set_account(win, [_account_entry()], draw=False)
    known = known_devices.load()[1234567891]
    assert (known.image_url, known.category) == (ACCOUNT_URL, "cycling-computer")
