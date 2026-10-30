"""Local backup of watch data.

Copies files of all data types the device exposes for reading
(OutputFromUnit), plus GarminDevice.xml. Media, maps and software are
skipped. The `keep` most recent backups are retained.
"""

from __future__ import annotations

import logging
import posixpath
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from openexpress import applog, paths
from openexpress.device.storage import DeviceError, Storage
from openexpress.device.xmldevice import OUTPUT, GarminDevice
from openexpress.i18n import _
from openexpress.progress import Step

log = logging.getLogger(__name__)

SKIP_PREFIXES = ("MEDIA_", "IQ")
SKIP_TYPES = {"ErrorShutdownReports", "ULFLogs", "ExpressRequest", "WiFiConfiguration"}
STAMP = "%Y-%m-%d (%H.%M.%S)"
PARTIAL_PREFIX = ".partial-"


@dataclass
class BackupResult:
    path: Path
    files: int
    bytes: int
    removed: list[Path]


def backup_files(device: GarminDevice) -> list:
    return [
        f for f in device.files
        if f.direction == OUTPUT
        and f.datatype not in SKIP_TYPES
        and not f.datatype.startswith(SKIP_PREFIXES)
    ]


def is_backup_dir(p: Path) -> bool:
    if not p.is_dir():
        return False
    try:
        datetime.strptime(p.name, STAMP)
    except ValueError:
        return False
    return True


def keep_newest(folders: list[Path], keep: int, new: Path | None = None,
                remove: Callable[[Path], None] = shutil.rmtree) -> list[Path]:
    """Of `folders` (oldest first) all but the newest `keep` are removed, never `new` (the one
    just made); 0 keeps them all. The one rule for the full backups and the copies of the app
    list. Returns those removed."""
    removed = []
    if keep > 0:
        for p in folders[:-keep]:
            if p == new:
                continue
            remove(p)
            removed.append(p)
    return removed


def _not_a_path(name: str) -> DeviceError:
    return DeviceError(_("Nothing was backed up: the device names a folder or file '{name}' "
                         "that cannot be used as a path on this computer.").format(name=name))


def _out_path(target: Path, datatype: str, rel: str) -> Path:
    """Where a file of the device goes in the backup: target/<data type>/<path below its
    folder>. The data type's name (from GarminDevice.xml) and each part of the file's path
    (as the device lists it) must be plain names, and the result must lie within target;
    anything else refuses the backup (a crafted XML or listing must not write elsewhere)."""
    parts = rel.split("/")
    if not paths.is_safe_component(datatype):
        raise _not_a_path(datatype)
    if not all(paths.is_safe_component(p) for p in parts):
        raise _not_a_path(rel)
    out = target.joinpath(datatype, *parts)
    if not paths.is_inside(out, target):
        raise _not_a_path(rel)
    return out


def _copy_files(device: GarminDevice, storage: Storage, target: Path,
                progress: Callable[[str], None]) -> tuple[int, int]:
    """`progress`: which file of how many ("Copying to the backup · 120 of 1530"), never its
    name."""
    # Every data type checked before anything is written.
    for src in backup_files(device):
        if not paths.is_safe_component(src.datatype):
            raise _not_a_path(src.datatype)
    # All the files first, for how many there are; each path is checked before any is read.
    files = []
    seen: set[str] = set()
    for src in backup_files(device):
        for path, entry in storage.walk_files(src.path):
            if not src.matches(path) or path.casefold() in seen:
                continue
            seen.add(path.casefold())
            rel = posixpath.relpath(path, storage.resolve(src.path) or src.path)
            files.append((path, entry, _out_path(target, src.datatype, rel)))
    # TRANSLATORS: a step of a backup ("… · 120 of 1530")
    head = _("Copying to the backup")
    # Every file of the backup counted, GarminDevice.xml (read already) the first.
    count = len(files) + 1
    progress(Step(head, 1, count))
    (target / "GarminDevice.xml").write_bytes(device.raw)
    total = len(device.raw)
    for n, (path, entry, out) in enumerate(files, 2):
        progress(Step(head, n, count))
        log.debug("backup: copying %s", path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(storage.read(path))
        total += entry.size
    return count, total


def run_backup(
    device: GarminDevice,
    storage: Storage,
    dest_root: Path,
    keep: int = 3,
    progress: Callable[[str], None] = lambda s: None,
) -> BackupResult:
    unit_dir = dest_root / str(device.unit_id)
    stamp = datetime.now().strftime(STAMP)
    target = unit_dir / stamp
    if target.exists():
        raise FileExistsError(target)
    unit_dir.mkdir(parents=True, exist_ok=True)
    # Leftovers of interrupted backups.
    for p in unit_dir.glob(PARTIAL_PREFIX + "*"):
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
    partial = unit_dir / (PARTIAL_PREFIX + stamp)
    partial.mkdir()
    try:
        count, total = _copy_files(device, storage, partial, progress)
        partial.rename(target)
    except BaseException:
        shutil.rmtree(partial, ignore_errors=True)
        raise
    old = sorted((p for p in unit_dir.iterdir() if is_backup_dir(p)),
                 key=lambda p: datetime.strptime(p.name, STAMP))
    removed = keep_newest(old, keep, new=target)
    log.info("backup: %d files (%s), %d old backups removed", count, applog.size(total),
             len(removed))
    return BackupResult(target, count, total, removed)
