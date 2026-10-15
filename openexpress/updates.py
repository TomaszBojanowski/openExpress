"""Device software updates (firmware and auxiliary files).

Flow as in Garmin Express (docs/analiza/04, sections 3-5):
  1. POST /api/device-software/v1/updates with GarminDevice.xml
  2. download `secureDownloadUrl`, check size and MD5
  3. copy to `installation.path/fileName` on the device
  4. restart the watch: it installs the file itself; the confirmation is that
     the update is no longer returned by the next server query.
"""

from __future__ import annotations

import hashlib
import logging
import posixpath
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from openexpress import applog, download, paths
from openexpress.device.storage import DeviceError, Storage
from openexpress.i18n import N_, _, human_size

log = logging.getLogger(__name__)

SEVERITY_ORDER = {"critical": 0, "recommended": 1, "optional": 2, "unspecified": 3}
RGN_MAGIC = b"KpGr"
SPACE_MARGIN = 2 * 1024 * 1024
# The files of a device system update. The server does not always say so: the system
# updates of a fēnix 8 and an Edge 1050 (GUPDATE.GSP, a bundle with the device software)
# come without "isprimaryfirmware" and without "isrestartrequired". The data type does not
# tell either: fonts and databases are "Firmware" too.
SYSTEM_FILES = frozenset({"GUPDATE.GCD", "GUPDATE.GSP"})
# Said before a system update is installed, on the command line and in the window.
SYSTEM_UPDATE_WARNING = N_("Warning: this is a device system update. Charge the device and do "
                           "not disconnect it while copying.")
# The offered updates named one by one in the log, at most.
LOG_ITEMS = 20
# Said when the server offers beta versions to the device ("accesslevel" of the answer, e.g.
# "public_beta"), on the command line and in the window.
BETA_OFFERED = N_("Garmin offers beta software for this device.")


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
    datatype: str = ""
    # The file is on the device already, with the same size and MD5 (`mark_present`).
    on_device: bool = False
    # "live", "archived", or for a beta version e.g. "public_beta_phased_rollout".
    release_state: str = ""

    @property
    def key(self) -> str:
        return f"{self.part_number}.{self.version}"

    @property
    def title(self) -> str:
        """The name to show: the product name, or a readable name of the type when the
        server only repeats the type (e.g. "Etyp") or gives no name."""
        if self.name and self.name != self.datatype:
            return self.name
        known = TYPE_NAMES.get(self.datatype.casefold())
        return _(known) if known else self.name or self.datatype

    @property
    def filename(self) -> str:
        return posixpath.basename(self.device_path)

    @property
    def severity_text(self) -> str:
        known = SEVERITY_NAMES.get(self.severity.casefold())
        return _(known) if known else self.severity

    @property
    def is_metadata(self) -> bool:
        return self.part_number.endswith("-META")

    @property
    def is_beta(self) -> bool:
        return "beta" in self.release_state.casefold()

    @property
    def is_system(self) -> bool:
        """An update of the device system: copied last, and the device must stay connected
        and charged while it is copied."""
        return (self.is_primary or self.restart_required
                or self.filename.upper() in SYSTEM_FILES)


# Types of updates as the server names them ("datatype"), seen for a fēnix 8 and in the
# answers Garmin Express logs. An unknown type is shown as the server sent it.
TYPE_NAMES = {
    # TRANSLATORS: kind of update: a file that belongs to a map (Garmin/SID/*.sid)
    "etyp": N_("Map support file"),
    # TRANSLATORS: kind of update
    "firmware": N_("Software"),
    # TRANSLATORS: kind of update
    "config": N_("Configuration"),
    # TRANSLATORS: kind of update: a file that describes other updates
    "updatemetadata": N_("Update information"),
}
SEVERITY_NAMES = {
    # TRANSLATORS: importance of an update
    "critical": N_("Critical"),
    # TRANSLATORS: importance of an update
    "recommended": N_("Recommended"),
    # TRANSLATORS: importance of an update, when the server does not say
    "unspecified": N_("Unspecified"),
}


def is_on_device(storage: Storage, u: SoftwareUpdate) -> bool:
    """Whether the file of this update is on the device already: same size and same MD5.
    Only reads. Without a size and an MD5 from the server nothing can be said."""
    md5 = download.normalize_md5(u.md5) if u.md5 else None
    if not u.device_path or not u.size or md5 is None:
        return False
    try:
        real = storage.resolve(u.device_path)
        if real is None or storage.size(real) != u.size:
            return False
        return hashlib.md5(storage.read(real)).hexdigest() == md5
    except DeviceError as e:
        log.info("updates: cannot compare %s with the device: %s", u.part_number, e)
        return False


def component_count(device) -> int | None:
    """How many components the device's GarminDevice.xml lists; None when that is not known.
    Only for the log, so it never fails."""
    files = getattr(device, "update_files", None)
    return len(files) if isinstance(files, list) else None


def mark_present(storage: Storage, updates: list[SoftwareUpdate],
                 components: int | None = None) -> list[SoftwareUpdate]:
    """Find the offered updates whose file the device already has (e.g. map support files
    it briefly does not list right after it is plugged in). They are not updates.

    `components`: how many components the device's GarminDevice.xml lists, for the log."""
    offered = [u for u in updates if not u.is_reinstall and not u.is_metadata]
    for u in offered:
        u.on_device = is_on_device(storage, u)
    # Part numbers and versions only: no paths, no addresses (a download address carries
    # a token).
    named = ", ".join(f"{u.part_number} {u.version}" + (" on the device" if u.on_device else "")
                      for u in offered[:LOG_ITEMS])
    if len(offered) > LOG_ITEMS:
        named += f", … and {len(offered) - LOG_ITEMS} more"
    log.info("updates: %d offered%s, %d of them already on the device%s", len(offered),
             f" ({named})" if named else "", sum(1 for u in offered if u.on_device),
             f"; {components} components listed" if components is not None else "")
    return updates


def staged(updates: list[SoftwareUpdate]) -> list[SoftwareUpdate]:
    """System updates that are copied already and wait for the device to install them, which
    it does when it is disconnected. Two facts, no guess: the server still offers the update
    (not as a reinstall), so the device has not installed it, and the very same file (size
    and MD5) is on the device."""
    return [u for u in updates
            if u.is_system and u.on_device and not u.is_reinstall and not u.is_metadata]


def pending(updates: list[SoftwareUpdate]) -> list[SoftwareUpdate]:
    """The real updates: not reinstalls, not metadata, not on the device already."""
    return [u for u in updates if not u.is_reinstall and not u.is_metadata and not u.on_device]


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
                version=(u.get("displayversion") or u.get("semanticversion")
                         or u.get("version") or ""),
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
                datatype=u.get("datatype") or "",
                release_state=str(u.get("releasestate") or ""),
            )
        )
    return out


def beta_offered(response: dict) -> bool:
    """Whether the server offers beta versions to this device: said for the device in the
    answer ("accesslevel"), not for each update (a reinstall of a plain file stays "live")."""
    return "beta" in str(response.get("accesslevel") or "").casefold()


def sort_for_install(updates: list[SoftwareUpdate]) -> list[SoftwareUpdate]:
    """System updates go last; otherwise in server order."""
    return sorted(updates, key=lambda u: (u.is_system, u.order, u.part_number))


def present_with_metadata(updates: list[SoftwareUpdate]) -> list[SoftwareUpdate]:
    """The offered files the device already has, each with its -META entry: what 'all' skips."""
    parts = {u.part_number.upper() for u in updates if u.on_device}
    return [u for u in updates if u.part_number.upper().removesuffix("-META") in parts]


def select(updates: list[SoftwareUpdate], wanted: list[str],
           keep_present: bool = False) -> list[SoftwareUpdate]:
    """Select by part number (together with its -META entry) or 'all'.

    'all' leaves out the files the device already has (`present_with_metadata`), unless
    `keep_present`: writing the same file again is a reinstall, and that is asked for by
    its part number or together with the reinstalls."""
    if wanted == ["all"]:
        skipped = [] if keep_present else present_with_metadata(updates)
        return [u for u in updates if not any(u is s for s in skipped)]
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
    transfer_progress=None,
) -> InstallReport:
    need, free = check_space(storage, updates)
    if free is not None and need + SPACE_MARGIN > free:
        raise UpdateError(_("Not enough space on the device: {need} needed, {free} free")
                          .format(need=human_size(need), free=human_size(free)))
    report = InstallReport()
    # Download everything first so the watch is never left with only some of the files.
    local_files: dict[str, Path] = {}
    for u in updates:
        if not u.url:
            raise UpdateError(_("{part}: the server did not provide a download URL")
                              .format(part=u.part_number))
        progress(_("Downloading {name} {version} ({size})")
                 .format(name=u.title, version=u.version, size=human_size(u.size or None)))
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
        progress(_("Copying to the device: {path}").format(path=u.device_path))
        try:
            real = storage.write_file(u.device_path, local, transfer_progress)
            ok = storage.size(real) == local.stat().st_size
        except Exception:
            _delete_quietly(storage, u.device_path)
            raise
        if not ok:
            # A truncated firmware file must not stay where the watch would install it.
            _delete_quietly(storage, real)
            raise UpdateError(_("Write verification of {path} failed").format(path=real))
        report.installed.append(f"{u.title} {u.version} -> {real}")
        report.restart_required |= u.is_system
        log.info("software update: %s %s (%s) copied to %s", u.part_number, u.version,
                 applog.size(local.stat().st_size), posixpath.dirname(real))
    return report


def _delete_quietly(storage: Storage, path: str) -> None:
    try:
        storage.delete(path)
    except Exception as e:  # noqa: BLE001
        log.warning("Cannot delete %s: %s", path, e)
