"""Device software updates (firmware and auxiliary files).

Flow as in Garmin Express (docs/analiza/04, sections 3-5):
  1. POST /api/device-software/v1/updates with GarminDevice.xml
  2. download `secureDownloadUrl`, check size and MD5
  3. copy to `installation.path/fileName` on the device
  4. restart the watch: it installs the file itself; the confirmation is that
     the update is no longer returned by the next server query.
"""

from __future__ import annotations

import logging
import posixpath
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from openexpress import download, paths
from openexpress.device.storage import Storage
from openexpress.i18n import _

log = logging.getLogger(__name__)

SEVERITY_ORDER = {"critical": 0, "recommended": 1, "optional": 2, "unspecified": 3}
RGN_MAGIC = b"KpGr"
SPACE_MARGIN = 2 * 1024 * 1024


class UpdateError(Exception):
    pass


@dataclass
class SoftwareUpdate:
    part_number: str
    name: str
    version: str
    severity: str
    is_primary: bool
    is_reinstall: bool
    restart_required: bool
    device_path: str
    url: str
    md5: str | None
    size: int | None
    order: int
    eula_url: str = ""
    locale: str | None = None
    changes: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{self.part_number}.{self.version}"

    @property
    def is_metadata(self) -> bool:
        return self.part_number.endswith("-META")


def parse_updates(response: dict) -> list[SoftwareUpdate]:
    out = []
    for u in response.get("updates") or []:
        inst = u.get("installation") or {}
        deliv = u.get("deliverable") or {}
        path = (inst.get("path") or "").replace("\\", "/").strip("/")
        fname = inst.get("filename") or ""
        if not fname:
            continue
        changes = [c for rn in u.get("releasenotes") or [] for c in rn.get("changelog") or []]
        out.append(
            SoftwareUpdate(
                part_number=u.get("partnumber") or "",
                name=u.get("productname") or u.get("description") or "",
                version=u.get("displayversion") or u.get("semanticversion") or u.get("version") or "",
                severity=(u.get("severity") or "Unspecified"),
                is_primary=bool(u.get("isprimaryfirmware")),
                is_reinstall=bool(inst.get("isreinstall")),
                restart_required=bool(inst.get("isrestartrequired")),
                device_path=posixpath.join(path, fname) if path else fname,
                url=deliv.get("securedownloadurl") or deliv.get("downloadurl") or "",
                md5=deliv.get("md5"),
                size=deliv.get("sizeinbytes"),
                order=int(inst.get("order") or 0),
                eula_url=u.get("eulaurl") or "",
                locale=u.get("locale"),
                changes=changes,
            )
        )
    return out


def sort_for_install(updates: list[SoftwareUpdate]) -> list[SoftwareUpdate]:
    """Files requiring a restart go last; otherwise in server order."""
    return sorted(updates, key=lambda u: (u.restart_required or u.is_primary, u.order, u.part_number))


def select(updates: list[SoftwareUpdate], wanted: list[str]) -> list[SoftwareUpdate]:
    """Select by part number (together with its -META entry) or 'all'."""
    if wanted == ["all"]:
        return list(updates)
    keys = {w.upper() for w in wanted}
    chosen = [u for u in updates if u.part_number.upper() in keys
              or u.part_number.upper().removesuffix("-META") in keys]
    missing = keys - {u.part_number.upper().removesuffix("-META") for u in chosen}
    if missing:
        raise UpdateError(_("Not in the list: {items}").format(items=", ".join(sorted(missing))))
    return chosen


def cache_path(u: SoftwareUpdate) -> Path:
    name = posixpath.basename(u.device_path)
    return paths.cache_dir() / "software" / u.part_number / u.version / name


def check_space(storage: Storage, updates: list[SoftwareUpdate]) -> tuple[int, int | None]:
    """Return (bytes needed, bytes free). Files being replaced are not counted."""
    need = 0
    for u in updates:
        existing = storage.size(u.device_path) or 0
        need += max(0, (u.size or 0) - existing)
    return need, storage.free_space()


@dataclass
class InstallReport:
    installed: list[str] = field(default_factory=list)
    restart_required: bool = False


def install(
    updates: list[SoftwareUpdate],
    storage: Storage,
    session=None,
    progress: Callable[[str], None] = lambda s: None,
) -> InstallReport:
    need, free = check_space(storage, updates)
    if free is not None and need + SPACE_MARGIN > free:
        raise UpdateError(_("Not enough space on the watch: {need} B needed, {free} B free")
                          .format(need=need, free=free))
    report = InstallReport()
    # Download everything first so the watch is never left with only some of the files.
    local_files: dict[str, Path] = {}
    for u in updates:
        if not u.url:
            raise UpdateError(_("{part}: the server did not provide a download URL")
                              .format(part=u.part_number))
        progress(_("Downloading {name} {version} ({size} B)")
                 .format(name=u.name, version=u.version, size=u.size or "?"))
        local = download.fetch([u.url], cache_path(u), u.size, u.md5, session=session)
        with open(local, "rb") as f:
            if f.read(4) == RGN_MAGIC:
                raise UpdateError(
                    _("{part}: the RGN format is not supported by openexpress")
                    .format(part=u.part_number)
                )
        local_files[u.key] = local
    for u in sort_for_install(updates):
        local = local_files[u.key]
        progress(_("Copying to the watch: {path}").format(path=u.device_path))
        try:
            real = storage.write_file(u.device_path, local)
            ok = storage.size(real) == local.stat().st_size
        except Exception:
            _delete_quietly(storage, u.device_path)
            raise
        if not ok:
            # A truncated firmware file must not stay where the watch would install it.
            _delete_quietly(storage, real)
            raise UpdateError(_("Write verification of {path} failed").format(path=real))
        report.installed.append(f"{u.name} {u.version} -> {real}")
        report.restart_required |= u.restart_required or u.is_primary
    return report


def _delete_quietly(storage: Storage, path: str) -> None:
    try:
        storage.delete(path)
    except Exception as e:  # noqa: BLE001
        log.warning("Cannot delete %s: %s", path, e)
