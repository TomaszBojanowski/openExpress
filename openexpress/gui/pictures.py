"""Pictures of device models for the tiles of the start page and for the pages of devices that
are not connected, the same as the device view's: by part number (never by unit ID), from
memory, from the cache, or from the account's address of the picture."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from openexpress import paths

log = logging.getLogger(__name__)

# use(texture, source): source is "memory", "the cache" or "the account"
Use = Callable[[object, str], None]


def device_picture_path(part_number: str) -> Path:
    """Cached product picture of a watch model (the picture never changes for a model)."""
    safe = "".join(c for c in part_number if c.isalnum() or c in "-_") or "unknown"
    return paths.cache_dir() / "images" / f"device-{safe}.png"


def with_device_picture(win, part: str, url: str | None, use: Use, read) -> None:
    """The picture of a model for `use`: in memory (`win._device_textures`) at once; else the
    file in the cache, or the account's address (a device on the account), read with
    `read(part, url)` -> (texture, source) or (None, why) in `win.in_background`, kept in
    memory and given to everyone who asked for it meanwhile. Nothing else is asked (no
    unit-info for a device not open). Each part number is read once; none, or a failure:
    `use` is not called and the icon stays."""
    textures = getattr(win, "_device_textures", None)
    if textures is None:
        textures = win._device_textures = {}
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
    win._device_textures[part] = texture
    for use in users:
        use(texture, source)
