"""The icons of the window: the side panel's Device takes the small icon of the open device's
kind (the shape of its tile, drawn for 16 px), Activities a runner, Courses a trail of
switchbacks from its start;
the tiles of an activity's page a distance between two points, a stopwatch, a speedometer, a
heart, a flame and two peaks. Each icon of the program is drawn on the 16 px grid in
packaging/icons and made into shapes by tools/symbolic-icons.py, and the Flatpak installs
every one of them."""

import re
from fnmatch import fnmatch
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
APP_ID = "io.github.tomaszbojanowski.OpenExpress"
SMALL = ["watch-small", "cycling-computer-small", "handheld-small", "bike-accessory-small",
         "device-small"]
OWN = [*SMALL, "activities", "courses", "activity-distance", "activity-time", "activity-speed",
       "activity-heart-rate", "activity-calories", "activity-elevation"]


def own(name: str) -> str:
    return f"{APP_ID}-{name}-symbolic"


@pytest.mark.parametrize("name", OWN)
def test_each_icon_is_drawn_for_16_px_and_made_into_shapes(name):
    """Lines of 2 px with round ends on the 16 px grid; the icon made of it has no stroke (GTK
    fills every shape of a symbolic icon) and says which drawing it was made from."""
    drawing = (ROOT / "packaging" / "icons" / f"{name}.svg").read_text()
    assert 'viewBox="0 0 16 16"' in drawing and 'stroke="currentColor"' in drawing
    assert 'stroke-width="2"' in drawing and 'stroke-linecap="round"' in drawing
    icon = (ROOT / "openexpress" / "data" / f"{own(name)}.svg").read_text()
    assert "stroke" not in icon and f"packaging/icons/{name}.svg" in icon


def test_the_flatpak_installs_every_icon_of_the_program():
    manifest = (ROOT / "packaging" / "flatpak" / f"{APP_ID}.yml").read_text()
    installed = [p.replace("${FLATPAK_ID}", APP_ID)
                 for p in re.findall(r"openexpress/data/(\$\{FLATPAK_ID\}[^ \"']*\.svg)",
                                     manifest)]
    icons = sorted(p.name for p in (ROOT / "openexpress" / "data").glob("*.svg"))
    assert [i for i in icons if not any(fnmatch(i, p) for p in installed)] == []


def test_the_small_icon_of_each_kind():
    from openexpress.gui import start

    assert [start.small_kind_icon(kind) for kind in (
        start.WATCH, start.CYCLING_COMPUTER, start.HANDHELD, start.BIKE_ACCESSORY,
        start.SCALE, None)] == [own(n) for n in (*SMALL[:4], "device-small", "device-small")]


@pytest.mark.needs_display
def test_the_tiles_of_an_activity_have_their_icons():
    from openexpress.gui import activity_page

    speed, heart = own("activity-speed"), own("activity-heart-rate")
    expected = {"distance": own("activity-distance"), "time": own("activity-time"),
                "pace": speed, "swim pace": speed, "speed": speed,
                "average heart rate": heart, "maximum heart rate": heart,
                "calories": own("activity-calories"), "ascent": own("activity-elevation")}
    shown = activity_page.TILE_ICONS
    assert shown == expected
    for icon in activity_page.TILE_ICONS.values():
        assert (ROOT / "openexpress" / "data" / f"{icon}.svg").is_file()


@pytest.mark.needs_display
def test_the_pages_without_activities_take_their_side_panel_icon():
    from openexpress.gui import activities_page, activity_page

    assert activities_page.PAGE_ICON == activity_page.PAGE_ICON == own("activities")
