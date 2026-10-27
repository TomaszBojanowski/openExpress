"""Pictures of device models, by part number (never by unit ID), for every view: the tiles of
the start page, the pages of devices that are not connected and the device view. One store
in the window (`win._device_textures`, never None in it); the tiles and those pages read the
cache or the account's address of the picture in the background (with_device_picture), the
device view reads the cache at once and asks its own sources (the account by unit ID,
unit-info) itself - all of them keep what they found here. Below: a picture downloaded into
the cache (download_picture, cached_download) and read (load_texture, cached_texture), the
address of the picture and the kind of the device from Garmin's update server
(picture_from_unit_info, note_segment, is_bike_accessory), moved here from gui/app.py; their
lines of the log are the window's, as before.

Has: win._device_textures, win._pictures_asked, win._pictures_waiting.
Uses: win.in_background."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from pathlib import Path

from gi.repository import GLib

from openexpress import cache, devicelist, download, known_devices, paths, picture_urls
from openexpress.i18n import _
from openexpress.useragent import USER_AGENT

log = logging.getLogger(__name__)
# The window's logger: the lines of the functions moved from gui/app.py stay where they were.
window_log = logging.getLogger("openexpress.gui.app")

# use(texture, source): source is "memory", "the cache" or "the account"
Use = Callable[[object, str], None]


def device_picture_path(part_number: str) -> Path:
    """Cached product picture of a watch model (the picture never changes for a model)."""
    return paths.cache_dir() / "images" / cache.device_picture_name(part_number)


def store(win) -> dict:
    """The window's pictures, by part number."""
    textures = getattr(win, "_device_textures", None)
    if textures is None:
        textures = win._device_textures = {}
    return textures


def known(win, part: str):
    """The picture of this part number in memory, or None."""
    return store(win).get(part)


def keep(win, part: str, texture) -> None:
    """Keep a picture found (never None: a part number without one is simply not there)."""
    if texture is not None:
        store(win)[part] = texture


def with_device_picture(win, part: str, url: str | None, use: Use, read) -> None:
    """The picture of a model for `use`: in memory (`win._device_textures`) at once; else the
    file in the cache, or the account's address (a device on the account), read with
    `read(part, url)` -> (texture, source) or (None, why) in `win.in_background`, kept in
    memory and given to everyone who asked for it meanwhile. Nothing else is asked (no
    unit-info for a device not open). Each part number is read once; none, or a failure:
    `use` is not called and the icon stays."""
    textures = store(win)
    asked = getattr(win, "_pictures_asked", None)
    if asked is None:
        asked = win._pictures_asked = set()
    waiting = getattr(win, "_pictures_waiting", None)
    if waiting is None:
        waiting = win._pictures_waiting = {}
    if textures.get(part) is not None:
        use(textures[part], "memory")
    elif part in waiting:
        waiting[part].append(use)  # being read: given to this one too
    elif part not in asked and (url or device_picture_path(part).exists()):
        asked.add(part)
        waiting[part] = [use]
        win.in_background(lambda: read(part, url),
                          lambda result: _read(win, part, result),
                          lambda _e: waiting.pop(part, None))


def _read(win, part: str, result) -> None:
    texture, source = result
    users = win._pictures_waiting.pop(part, [])
    if texture is None:
        log.info("pictures: no picture of %s (%s)", part, source)
        return
    keep(win, part, texture)
    for use in users:
        use(texture, source)


def picture_target(name: str) -> Path | None:
    """cache/images/<name>, the name from a server (an app ID, a part number): None when it
    is no plain file name or would leave the cache (nothing is saved then)."""
    images = paths.cache_dir() / "images"
    if not paths.is_safe_component(name) or not paths.is_inside(images / name, images):
        window_log.info("picture: a name from the server is no file name here, not saved")
        return None
    return images / name


def cached_download(http, url: str, name: str) -> Path | None:
    """Downloads a small file (picture, icon) once into the cache."""
    target = picture_target(name)
    if target is None:
        return None
    if target.exists() and target.stat().st_size:
        return target
    try:
        url = download.secure_url(url)  # HTTPS only, as every download
    except download.DownloadError:
        window_log.info("picture: not an HTTPS address, not asked")
        return None
    try:
        resp = http.get(url, timeout=20)
        if not resp.ok or not resp.content:
            return None
    except Exception:  # noqa: BLE001 - pictures are optional
        return None
    with cache.using():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(resp.content)
    return target


# A picture of a device model is taken only from an address picture_urls allows, as a PNG or
# a JPEG (its type and its first bytes agree), small and quickly, or the generic icon stays.
PICTURE_MAX_BYTES = 2 * 1024 * 1024
PICTURE_TIMEOUT_S = 15
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
PICTURE_SIGNATURES = {"image/png": PNG_SIGNATURE, "image/jpeg": b"\xff\xd8\xff"}


def picture_from_unit_info(client, device, ask=None) -> str | None:
    """The address of the device's picture according to Garmin's update server. The rest of
    the answer (unit ID, serial number…) is dropped here and goes nowhere. `ask` gives the
    answer (the one of the session, service.unit_info); none: it is asked here."""
    try:
        info = ask() if ask is not None else client.unit_info(device.unit_id,
                                                              device.part_number)
    except Exception as e:  # noqa: BLE001 - optional; the error may quote the answer
        window_log.info("device picture: unit-info failed (%s)", type(e).__name__)
        return None
    note_segment(device, info)
    url = info.get("graphicurl") if isinstance(info, dict) else None
    return url if isinstance(url, str) and url else None


def note_segment(device, info) -> None:
    """Keep the kind Garmin's update server tells in an answer the window asked for (for a
    picture, after an update, for a drive whose kind is not told): a bike accessory (e.g. a
    Varia). Only this field, its connect support and the map update type are read. A segment
    that tells no kind the program knows goes to the log with the part number and the connect
    support (devicelist.note_kind_evidence); the map update type always, once a model
    (devicelist.note_map_update_type)."""
    segment = info.get("segmenttype") if isinstance(info, dict) else None
    bike_accessory = isinstance(segment, str) and segment.casefold() == "bikeaccessory"
    if bike_accessory:
        known_devices.set_category(device.unit_id, known_devices.BIKE_ACCESSORY)
    if isinstance(info, dict):
        support = info.get("connectsupport")
        devicelist.note_kind_evidence(
            getattr(device, "part_number", "") or "",
            segment=segment if isinstance(segment, str) and segment else "none given",
            kind=known_devices.BIKE_ACCESSORY if bike_accessory else None,
            connect_support=support if isinstance(support, str) and support else "none given")
        devicelist.note_map_update_type(getattr(device, "part_number", "") or "",
                                        info.get("mapupdatetype"))


def is_bike_accessory(client, device, ask=None) -> bool:
    """Whether Garmin's update server files the device as a bike accessory (e.g. a Varia),
    which finishes an update once it is unplugged and switched on. Only this field of the
    answer is read; the rest (unit ID, serial number…) goes nowhere. `ask` gives the answer
    (the one of the session, service.unit_info); none: it is asked here."""
    try:
        info = ask() if ask is not None else client.unit_info(device.unit_id,
                                                              device.part_number)
    except Exception as e:  # noqa: BLE001 - only for a note after the update; may quote the answer
        window_log.info("software update: unit-info failed (%s)", type(e).__name__)
        return False
    note_segment(device, info)
    segment = info.get("segmenttype") if isinstance(info, dict) else None
    return isinstance(segment, str) and segment.casefold() == "bikeaccessory"


def bike_accessory_text() -> str:
    """After an update was saved on a bike accessory: how the device finishes it."""
    return _("Unplug the device and turn it on — it installs the update on its own and won't "
             "work for a short while. The lights flash and may briefly go dark during the "
             "install; when it's done, a light flashes green and the main light comes on.")


def download_picture(http, url: str, name: str,
                     clock=time.monotonic) -> tuple[Path | None, str]:
    """Download a device picture into the cache: (file, "") or (None, why not). The file
    keeps its name whether it is a PNG or a JPEG: GTK reads a picture by its content."""
    target = picture_target(name)
    if target is None:
        return None, "not a file name"
    url = picture_urls.upgraded(url)  # plain HTTP over HTTPS, as every download
    why = picture_urls.refusal(url)  # the host at most, never the whole address
    if why is not None:
        said = picture_urls.query_said(url)  # whether it has a query, never what
        return None, f"{why}; {said}" if said else why
    deadline = clock() + PICTURE_TIMEOUT_S
    data = bytearray()
    try:
        with http.get(url, headers={"User-Agent": USER_AGENT}, timeout=PICTURE_TIMEOUT_S,
                      stream=True, allow_redirects=False) as resp:
            if resp.status_code != 200:
                return None, f"HTTP {resp.status_code}"
            kind = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            signature = PICTURE_SIGNATURES.get(kind)
            if signature is None:
                return None, "not a PNG or JPEG"
            length = resp.headers.get("Content-Length") or ""
            if length.isdigit() and int(length) > PICTURE_MAX_BYTES:
                return None, "too large"
            for chunk in resp.iter_content(64 * 1024):
                data += chunk
                if len(data) > PICTURE_MAX_BYTES:
                    return None, "too large"
                if clock() > deadline:
                    return None, "timeout"
    except Exception as e:  # noqa: BLE001 - pictures are optional
        return None, "timeout" if "Timeout" in type(e).__name__ else "network error"
    if not bytes(data).startswith(signature):
        return None, "not a PNG or JPEG"
    try:
        with cache.using():
            target.parent.mkdir(parents=True, exist_ok=True)
            partial = target.with_name(target.name + ".part")
            partial.write_bytes(data)
            partial.replace(target)  # never a half-written picture in the cache
    except OSError:
        return None, "cannot write the cache"
    return target, ""


def load_texture(path: Path):
    """Picture as a texture, None when the file is no picture GTK can read (GTK reads PNG,
    JPEG and TIFF itself and other formats, e.g. GIF, through its own loaders). The size is
    left to the image that shows it: Gtk.Image fits a paintable to its pixel size."""
    from gi.repository import Gdk

    try:
        return Gdk.Texture.new_from_filename(str(path))
    except GLib.Error:
        return None


def cached_texture(part: str):
    """The picture of a part number in the cache, read now; None: no file, or not a
    picture. The one reader of the cache for every view."""
    path = device_picture_path(part)
    return load_texture(path) if path.exists() else None
