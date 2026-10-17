"""The cache of openExpress: downloaded maps and software updates, pictures, and files staged
for copying to the device. Everything in it can be downloaded again.

It is paths.cache_dir(): ~/.cache/openexpress for the version from source; in the Flatpak,
where XDG_CACHE_HOME is ~/.var/app/<app ID>/cache, its own folder below that. Each
installation has its own cache."""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path

from openexpress import paths
from openexpress.i18n import N_

# The folders openExpress keeps in its cache, in the order they are shown; nothing else in
# the cache folder is counted (or, later, deleted).
FOLDERS = ("maps", "images", "software", "transfer")
# TRANSLATORS: the parts of the cache, as `openexpress cache` and the preferences list them
FOLDER_NAMES = {"maps": N_("Maps"), "images": N_("Pictures"),
                "software": N_("Software updates"), "transfer": N_("Transfer")}


@dataclass
class CacheUsage:
    root: Path
    sizes: dict[str, int]  # bytes in each of FOLDERS

    @property
    def total(self) -> int:
        return sum(self.sizes.values())


def usage() -> CacheUsage:
    """How big each folder of the cache is, in bytes. A folder that is not there is 0; nothing
    is made or changed."""
    root = paths.cache_dir()
    return CacheUsage(root, {name: _size(root / name) for name in FOLDERS})


def _size(folder: Path) -> int:
    """The bytes of the regular files below `folder`. Symbolic links are neither followed nor
    counted: a folder that is a link to elsewhere counts as nothing."""
    if folder.is_symlink() or not folder.is_dir():
        return 0
    total = 0
    for here, _dirs, files in os.walk(folder, followlinks=False):
        for name in files:
            try:
                st = os.lstat(os.path.join(here, name))
            except OSError:
                continue  # gone meanwhile (e.g. a staged file just deleted)
            if stat.S_ISREG(st.st_mode):
                total += st.st_size
    return total
