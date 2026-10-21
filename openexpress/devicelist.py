"""The tiles of the start page: one list made of the devices connected now, the devices this
installation remembers (known_devices) and the devices of the Garmin account
(GarminClient.account_devices). Data in, data out: nothing is asked of a device or a server.

One device is one tile. Sources are merged by the unit ID only, never by the part number (two
watches of one model are two devices); a connected device without a unit ID (no <Id>, or not
read) has a tile of its own. The unit ID is used only to merge: no tile holds it, and the log
never names it."""

from __future__ import annotations

import logging
import unicodedata
from dataclasses import dataclass

from openexpress import known_devices, picture_urls
from openexpress.device.gio_storage import distinct_devices
from openexpress.known_devices import BIKE_ACCESSORY, CYCLING_COMPUTER, WATCH
from openexpress.service import unit_id_of

log = logging.getLogger(__name__)

# The sections of the start page: connected now, remembered (seen here before, not connected),
# only on the Garmin account (never seen here).
CONNECTED, REMEMBERED, ON_ACCOUNT = "connected", "remembered", "on-account"
SECTIONS = (CONNECTED, REMEMBERED, ON_ACCOUNT)  # in the order of the page

# How a section of the start page shows its devices: tiles in a grid while they are few, a
# list once they are many (counted for each section on its own).
GRID, LIST = "grid", "list"
GRID_MAX = 6

# The start of a model name and the kind it tells, for the icon only: the last resort after
# what Garmin says (the account, unit-info, devices.json). Never stored, never read by the
# program's logic. Lower case, without diacritics.
NAME_KINDS = {
    WATCH: ("fenix", "epix", "enduro", "forerunner", "venu", "vivoactive", "vivomove",
            "vivosmart", "instinct", "quatix", "tactix", "descent", "marq", "lily",
            "approach s"),
    CYCLING_COMPUTER: ("edge",),
    BIKE_ACCESSORY: ("varia",),
}


def layout(count: int) -> str | None:
    """GRID up to GRID_MAX devices, LIST beyond; None: no device, no section."""
    if count <= 0:
        return None
    return GRID if count <= GRID_MAX else LIST


@dataclass(frozen=True)
class Tile:
    description: str  # "" for a connected device whose GarminDevice.xml was not read
    part_number: str
    software_version: str
    kind: str | None  # "mtp" or "ums" (connected or remembered); None: only on the account
    connected: bool
    on_account: bool
    primary: bool  # the account's primary device (its primaryActivityTrackerIndicator)
    last_seen: float | None  # from what this installation remembers only
    image_part_number: str  # the key of its picture in the cache
    image_url: str | None  # the address of its picture: the account's, else the remembered one
    category: str | None
    # Where image_url comes from, for the log: "the account" or "the remembered address".
    image_source: str | None = None


def tiles(connected, remembered, account, logged_in: bool) -> list[Tile]:
    """The tiles of entries(), without what they were made of."""
    return [tile for tile, _source in entries(connected, remembered, account, logged_in)]


def entries(connected, remembered, account, logged_in: bool) -> list[tuple[Tile, object]]:
    """(tile, its source) in order: the source is kept outside the tile, for the window to
    act on it: the connected device's DeviceSummary (to open it), else the KnownDevice
    remembered, else its entry of the account's list (only on the account).

    The tiles, in order: the devices connected now first (the primary one first among
    them), then those remembered, then those only on the account, each section by name
    (case ignored, then part number), so that the order does not change from one plug-in to
    the next; when a device was seen is only shown. The sections follow each other: the
    window counts a click over the whole list.

    `connected`: DeviceSummary list (probe); `remembered`: known_devices.load(); `account`:
    the account's device list (keys in lower case), used only when `logged_in`. Fields:
    name and version from the connected device's GarminDevice.xml, else as remembered, else
    as the account says; on_account, primary, the picture address and the category only
    from the account; when it was seen only from what is remembered."""
    on_account: dict[int, dict] = {}
    account_only: list[dict] = []  # entries of the account without a usable unit ID
    for entry in (account or []) if logged_in else []:
        unit_id = unit_id_of(entry.get("unitid"))
        if unit_id is None:
            account_only.append(entry)
        else:
            on_account.setdefault(unit_id, entry)
    done: set[int] = set()

    now = []
    for s in distinct_devices(list(connected)):
        unit_id = s.unit_id if s.ok else None
        known = remembered.get(unit_id) if unit_id is not None else None
        entry = on_account.get(unit_id) if unit_id is not None else None
        if unit_id is not None:
            done.add(unit_id)
        now.append((_tile(s.description, s.part_number, s.software_version, s.found.kind,
                          True, known, entry), s))
    now.sort(key=lambda e: not e[0].primary)  # stable: otherwise in the order found

    rest = [(_tile("", "", "", None, False, known, on_account.get(unit_id)), known)
            for unit_id, known in remembered.items() if unit_id not in done]
    done.update(remembered)
    rest += [(_tile("", "", "", None, False, None, entry), entry)
             for unit_id, entry in on_account.items() if unit_id not in done]
    rest += [(_tile("", "", "", None, False, None, entry), entry) for entry in account_only]
    rest.sort(key=lambda e: (section(e[0]) != REMEMBERED, e[0].description.casefold(),
                             e[0].part_number))

    result = now + rest
    # The start page groups them by section; kept in that order so that the list reads the
    # same as the page.
    order = [SECTIONS.index(section(tile)) for tile, _source in result]
    assert order == sorted(order), "the tiles are not in the order of the sections"
    log.debug("start page: %d tiles (%d connected, %d on the account)", len(result),
              len(now), sum(t.on_account for t, _source in result))
    return result


def _tile(description: str, part_number: str, version: str, kind: str | None,
          connected: bool, known, entry: dict | None) -> Tile:
    """One tile: what the connected device says (if connected), else what is remembered of
    it (`known`), else what the account says (`entry`)."""
    account = account_fields(entry) if entry is not None else None
    part = part_number or (known.part_number if known else "") or (
        account["part_number"] if account else "")
    image_url, image_source = picture_address(account["image_url"] if account else None,
                                              known.image_url if known else None)
    return Tile(
        description=description or (known.description if known else "") or (
            account["description"] if account else ""),
        part_number=part,
        software_version=version or (known.software_version if known else "") or (
            account["software_version"] if account else ""),
        kind=kind or (known.kind if known else None),
        connected=connected,
        on_account=account is not None,
        primary=bool(account and account["primary"]),
        last_seen=known.last_seen if known else None,
        image_part_number=part,
        image_url=image_url,
        # The account's word first, else what is remembered (also offline).
        category=(account["category"] if account else None) or (
            known.category if known else None),
        image_source=image_source,
    )


PICTURE_FROM_ACCOUNT, PICTURE_REMEMBERED = "the account", "the remembered address"


def picture_address(account_url: str | None, remembered_url: str | None):
    """(address, where it comes from) of a tile's picture: the account's, which is the newer
    word; the one remembered with the device when the account gives none it may download
    (none for a device off the account, or offline; one on S3, #8). With neither allowed,
    the account's, so that the log says why there is no picture."""
    if (remembered_url and not picture_urls.allowed(picture_urls.upgraded(account_url))
            and picture_urls.allowed(remembered_url)):
        return remembered_url, PICTURE_REMEMBERED
    if account_url:
        return account_url, PICTURE_FROM_ACCOUNT
    return None, None


def kind_from_name(name: str | None) -> str | None:
    """The kind a model name tells by its start ("fēnix 8 - 47mm, Solar": a watch), case and
    diacritics ignored; a known start must end there or before a character that is not a
    letter ("Approach S70", not "Venusaur"). None: not known (the neutral icon)."""
    text = "".join(c for c in unicodedata.normalize("NFKD", name or "")
                   if not unicodedata.combining(c)).casefold().strip()
    for kind, starts in NAME_KINDS.items():
        for start in starts:
            if text.startswith(start) and not text[len(start):len(start) + 1].isalpha():
                return kind
    return None


def icon_kind_of(category: str | None, name: str | None) -> str | None:
    """The kind for the icon: what Garmin says, else what the model name tells."""
    return category or kind_from_name(name)


def icon_kind(tile: Tile) -> str | None:
    """The kind for the icon of a tile (its `category` stays what Garmin says)."""
    return icon_kind_of(tile.category, tile.description)


def section(tile: Tile) -> str:
    """The section of the start page of a tile: CONNECTED, REMEMBERED (seen here before;
    only those have when they were seen) or ON_ACCOUNT."""
    if tile.connected:
        return CONNECTED
    return REMEMBERED if tile.last_seen is not None else ON_ACCOUNT


def account_fields(entry: dict) -> dict:
    """What a tile takes from one entry of the account's device list (keys in lower case);
    anything of another type than expected counts as missing."""
    def text(key: str) -> str:
        value = entry.get(key)
        return value if isinstance(value, str) else ""

    categories = entry.get("devicecategories")
    categories = categories if isinstance(categories, list) else []
    if "DEDICATED_BIKE_COMPUTER" in categories:
        category = CYCLING_COMPUTER
    elif entry.get("hasopticalheartrate") is True:  # worn on the wrist
        category = WATCH
    else:
        category = None
    return {
        "description": text("productdisplayname") or text("displayname"),
        "part_number": text("partnumber"),
        "software_version": text("currentfirmwareversion"),
        "primary": entry.get("primaryactivitytrackerindicator") is True,
        "image_url": text("imageurl") or None,
        "category": category,
    }


def remember_picture_addresses(account) -> None:
    """Keep the addresses of the pictures the account gives for the devices remembered here
    (devices.json), for when a picture is no longer in the cache and the account is not
    there; only an address that may be downloaded (known_devices.set_image_url)."""
    remembered = known_devices.load()
    for entry in account or []:
        unit_id = unit_id_of(entry.get("unitid"))
        url = account_fields(entry)["image_url"]
        if unit_id in remembered and url:
            known_devices.set_image_url(unit_id, url)


def remember_kinds(account) -> None:
    """Keep the kinds the account tells of the devices remembered here (devices.json), so
    that they are known offline; a device that is not remembered gets no entry."""
    remembered = known_devices.load()
    for entry in account or []:
        unit_id = unit_id_of(entry.get("unitid"))
        category = account_fields(entry)["category"]
        if unit_id in remembered and category is not None:
            known_devices.set_category(unit_id, category)
