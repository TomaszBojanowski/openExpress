"""Local backup of watch data.

Copies files of all data types the device exposes for reading
(OutputFromUnit), plus GarminDevice.xml. Media, maps and software are
skipped. The `keep` most recent backups are retained.
"""

from __future__ import annotations

import logging
import posixpath
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from openexpress import applog
from openexpress.device.storage import Storage
from openexpress.device.xmldevice import OUTPUT, GarminDevice

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


def _is_backup_dir(p: Path) -> bool:
    if not p.is_dir():
        return False
    try:
        datetime.strptime(p.name, STAMP)
    except ValueError:
        return False
    return True


def _copy_files(device: GarminDevice, storage: Storage, target: Path,
                progress: Callable[[str], None]) -> tuple[int, int]:
    (target / "GarminDevice.xml").write_bytes(device.raw)
    count, total = 1, len(device.raw)
    seen: set[str] = set()
    for src in backup_files(device):
        for path, entry in storage.walk_files(src.path):
            if not src.matches(path) or path.casefold() in seen:
                continue
            seen.add(path.casefold())
            rel = posixpath.relpath(path, storage.resolve(src.path) or src.path)
            out = target / src.datatype / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            progress(path)
            out.write_bytes(storage.read(path))
            count += 1
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
    removed = []
    if keep > 0:
        old = sorted((p for p in unit_dir.iterdir() if _is_backup_dir(p)),
                     key=lambda p: datetime.strptime(p.name, STAMP))
        for p in old[:-keep]:
            if p == target:
                continue
            shutil.rmtree(p)
            removed.append(p)
    log.info("backup: %d files (%s), %d old backups removed", count, applog.size(total),
             len(removed))
    return BackupResult(target, count, total, removed)
