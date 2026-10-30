"""The accessories of the Garmin account (heart rate straps, scales, blood pressure monitors,
speed and cadence sensors) have a kind and an icon of their own: the account files them under
FITNESS or WELLNESS only, and their product name picks the icon (never the part number);
none of those names, the icon of another kind the name tells, else the neutral one. Our own
drawings, in the style of the others. All devices and answers are made up."""
from pathlib import Path

import pytest

from openexpress import devicelist
from openexpress.known_devices import (
    BIKE_SENSOR,
    BLOOD_PRESSURE_MONITOR,
    CYCLING_COMPUTER,
    FITNESS,
    HEART_RATE_MONITOR,
    SCALE,
    WATCH,
    WELLNESS,
)

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize(("categories", "optical", "kind"), [
    (["WELLNESS"], None, WELLNESS),
    (["FITNESS"], None, FITNESS),
    (["FITNESS", "WELLNESS"], False, FITNESS),
    (["FITNESS", "DEDICATED_BIKE_COMPUTER"], False, CYCLING_COMPUTER),
    (["FITNESS", "WELLNESS"], True, WATCH),
    (["FITNESS", "WELLNESS", "OUTDOOR"], None, None),
    ([], None, None),
])
def test_the_kind_comes_from_the_categories_of_the_account(categories, optical, kind):
    entry = {"devicecategories": categories, "productdisplayname": "Made-up"}
    if optical is not None:
        entry["hasopticalheartrate"] = optical
    assert devicelist.account_fields(entry)["category"] == kind


@pytest.mark.parametrize(("category", "name", "icon"), [
    (FITNESS, "HRM-Pro Plus", HEART_RATE_MONITOR),
    (FITNESS, "HRM-Dual", HEART_RATE_MONITOR),
    (FITNESS, "HRM 600", HEART_RATE_MONITOR),
    (WELLNESS, "Index S2", SCALE),
    (WELLNESS, "Index™ BPM", None),  # not a start the program knows: no guess
    (WELLNESS, "Index BPM", BLOOD_PRESSURE_MONITOR),
    (FITNESS, "Speed Sensor 2", BIKE_SENSOR),
    (FITNESS, "Cadence Sensor 2", BIKE_SENSOR),
    (FITNESS, "Bike Speed Sensor 2", BIKE_SENSOR),
    (FITNESS, "Forerunner 965", WATCH),  # another kind its name tells
    (WELLNESS, "Index Sleep Monitor", None),  # none: the neutral icon
    (None, "HRM-Pro Plus", None),  # an accessory only with its category
    (None, "Index S2", None),
])
def test_the_icon_of_an_accessory_comes_from_its_category_and_name(category, name, icon):
    assert devicelist.icon_kind_of(category, name) == icon


def test_garmin_tells_the_kind_of_an_accessory(caplog):
    """The line "Garmin tells no kind of …" is for what the program cannot place: an
    accessory of the account has its kind now."""
    devicelist._kind_told.clear()
    devicelist._kind_evidence.clear()
    devicelist.remember_kinds([{"unitid": 3000000099, "partnumber": "006-B0000-99",
                                "productdisplayname": "Index S2",
                                "devicecategories": ["WELLNESS"]}])
    assert "tells no kind" not in caplog.text


def test_every_kind_has_its_icon_in_the_program_and_in_the_flatpak():
    from openexpress.gui.start import KIND_ICONS

    manifest = (ROOT / "packaging/flatpak/io.github.tomaszbojanowski.OpenExpress.yml").read_text()
    for kind, icon in KIND_ICONS.items():
        assert (ROOT / "openexpress/data" / f"{icon}.svg").is_file(), kind
        assert (ROOT / "packaging/icons" / f"{kind}.svg").is_file(), kind  # its drawing
        assert f"${{FLATPAK_ID}}-{kind}-symbolic.svg ${{FLATPAK_DEST}}" in manifest, kind


def test_the_drawings_are_lines_in_the_style_of_the_others():
    """The same grid, line width and round ends as the other drawings: no fills."""
    for name in ("heart-rate-monitor", "scale", "blood-pressure-monitor", "bike-sensor"):
        text = (ROOT / "packaging/icons" / f"{name}.svg").read_text()
        assert 'viewBox="-30 -30 60 60"' in text and 'stroke-width="2.4"' in text
        assert 'fill="none"' in text and 'stroke-linecap="round"' in text


@pytest.mark.needs_display
def test_the_start_page_shows_an_accessory_with_its_icon_dimmed():
    from openexpress.gui import start

    tile = devicelist.Tile(description="HRM-Pro Plus", part_number="006-B0000-01",
                           software_version="", kind=None, connected=False, on_account=True,
                           primary=False, last_seen=None, image_part_number="006-B0000-01",
                           image_url=None, category=FITNESS)
    image = start.tile_image(start.tile_widget(tile, ""))
    assert image.get_icon_name() == start.KIND_ICONS[HEART_RATE_MONITOR]
    assert image.has_css_class("oe-placeholder")
