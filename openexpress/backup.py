"""Local backup of watch data.

Copies files of all data types the device exposes for reading
(OutputFromUnit), plus GarminDevice.xml. Media, maps and software are
skipped. The `keep` most recent backups are retained.
"""

from __future__ import annotations

import posixpath
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from openexpress.device.storage import Storage
from openexpress.device.xmldevice import OUTPUT, GarminDevice

SKIP_PREFIXES = ("MEDIA_", "IQ")
SKIP_TYPES = {"ErrorShutdownReports", "ULFLogs", "ExpressRequest", "WiFiConfiguration"}
STAMP = "%Y-%m-%d (%H.%M.%S)"


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


def run_backup(
    device: GarminDevice,
    storage: Storage,
    dest_root: Path,
    keep: int = 3,
    progress: Callable[[str], None] = lambda s: None,
) -> BackupResult:
    unit_dir = dest_root / str(device.unit_id)
    target = unit_dir / datetime.now().strftime(STAMP)
    target.mkdir(parents=True, exist_ok=False)
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
    removed = []
    if keep > 0:
        old = sorted((p for p in unit_dir.iterdir() if p.is_dir()), key=lambda p: p.name)
        for p in old[:-keep]:
            shutil.rmtree(p)
            removed.append(p)
    return BackupResult(target, count, total, removed)
