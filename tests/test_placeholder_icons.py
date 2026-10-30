"""The icon a device has until its picture comes (or when it has none) stands back from the
pictures beside it: smaller than its place, as a picture of a device leaves room around the
device, and dimmed; the place keeps the size of a picture, so that nothing moves when the
picture comes. On the tiles and rows of the start page, on the page of a remembered device
and on the device page. All devices are made up."""
import pytest
from test_narrow_window import _fill, narrow_window  # noqa: F401 - the fixture

pytestmark = pytest.mark.needs_display

TILE, ROW, REMEMBERED = 96, 48, 128


def _tile(category="watch"):
    from openexpress import devicelist

    return devicelist.Tile(description="Made-up 1", part_number="006-X", software_version="1.00",
                           kind=None, connected=False, on_account=True, primary=False,
                           last_seen=None, image_part_number="006-X", image_url=None,
                           category=category)


def _picture():
    from gi.repository import Gdk, GLib

    return Gdk.MemoryTexture.new(2, 2, Gdk.MemoryFormat.R8G8B8A8, GLib.Bytes.new(bytes(16)), 8)


def _placeholder(image, place):
    from openexpress.gui import start

    return (image.get_icon_name() is not None and image.has_css_class("oe-placeholder")
            and image.get_pixel_size() == round(place * start.PLACEHOLDER_SCALE)
            and image.get_size_request() == (place, place))


def _picture_shown(image, place):
    return (image.get_paintable() is not None and not image.has_css_class("oe-placeholder")
            and image.get_pixel_size() == place)


def test_the_icon_is_about_three_quarters_of_a_picture():
    from openexpress.gui import start

    assert 0.70 <= start.PLACEHOLDER_SCALE <= 0.75


def test_a_tile_and_a_row_without_a_picture_show_the_icon_smaller_and_dimmed():
    from openexpress.gui import start

    assert _placeholder(start.tile_image(start.tile_widget(_tile(), "")), TILE)
    assert _placeholder(start.tile_image(start.row_widget(_tile(), "")), ROW)
    assert _picture_shown(start.tile_image(start.tile_widget(_tile(), "", _picture())), TILE)
    assert _picture_shown(start.tile_image(start.row_widget(_tile(), "", _picture())), ROW)


def test_the_picture_that_comes_later_takes_the_whole_place():
    from openexpress.gui import start

    page = start.StartPage()
    page.show([(_tile(), "")], False)
    (item,) = page.tiles()
    assert _placeholder(start.tile_image(item), TILE)
    page.set_picture("006-X", _picture())
    assert _picture_shown(start.tile_image(item), TILE)


def test_the_page_of_a_remembered_device_too():
    from openexpress.gui import start

    page = start.RememberedPage()
    page.show_icon("006-X", start.kind_icon("watch"))
    assert _placeholder(page.image, REMEMBERED)
    assert page.set_picture("006-X", _picture())
    assert _picture_shown(page.image, REMEMBERED)
    page.show_icon("006-Y", start.kind_icon(None))  # another device: its icon again
    assert _placeholder(page.image, REMEMBERED)


def test_the_device_page_too(narrow_window):
    """Its picture has a place of its own (DEVICE_PICTURE_SIZE): the icon is as much smaller
    than that place."""
    from openexpress.gui.device_page import DEVICE_PICTURE_SIZE

    win = narrow_window("en")
    _fill(win, "device")
    assert _placeholder(win.device_image, DEVICE_PICTURE_SIZE)


def test_the_dimmed_style_is_the_colour_of_the_text_less_opaque():
    from openexpress.gui import start

    assert "image.oe-placeholder" in start.CSS
    rule = start.CSS.split("image.oe-placeholder", 1)[1].split("}", 1)[0]
    assert "alpha(currentColor" in rule
