"""Forgetting a device: what this installation keeps of it goes, so that the device, connected
again, is a new one to it.

Deleted: the entry in devices.json (last, so that a failure leaves the device listed, to be
forgotten again), the device's own folder in the data folder (what was uploaded from it, the
copies of files not uploaded yet, its last sync, the maps of your own copied to it, the apps it
refused, the copies of its diagnostic files) and the shared last sync when it is of this
device. Files not uploaded yet only with consent; its backups only when asked, and of them only
the folders openExpress made. Never the cache, the window's settings or anything on the Garmin
account."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from openexpress import known_devices, paths, service
from openexpress.backup import PARTIAL_PREFIX, is_backup_dir
from openexpress.ciq import APP_LIST_COPY_PREFIX
from openexpress.errors import OrdinaryStop
from openexpress.i18n import ngettext


class PendingFiles(OrdinaryStop):
    """Files of the device whose upload failed are kept to be sent at its next sync:
    forgetting it would lose them."""

    log_reason = "waiting for a choice about the files not uploaded yet"

    def __init__(self, count: int):
        super().__init__(ngettext(
            "{count} file of this device has not been uploaded yet and would be lost.",
            "{count} files of this device have not been uploaded yet and would be lost.",
            count).format(count=count))
        self.count = count


@dataclass(frozen=True)
class Kept:
    """What forgetting a device would lose besides what it always deletes."""
    pending: int  # files not uploaded yet
    backups: int  # backups and copies of the app list in the default backup folder


def kept(unit_id: int) -> Kept:
    return Kept(len(_pending(unit_id)), sum(1 for p in _backups(unit_id) if not _partial(p)))


def backup_folder() -> Path:
    """The folder of the backups (the one above the device's: that is named after the unit
    ID, never shown)."""
    return paths.default_backup_dir()


def forget_device(unit_id: int, discard_pending: bool = False, backups: bool = False) -> bool:
    """Forget a device remembered in devices.json; False when it is not (nothing deleted).
    Under the sync lock (a sync running: service.SyncBusy, nothing deleted) and the lock of
    devices.json. Raises PendingFiles without `discard_pending` when files of it have not
    been uploaded yet, and OSError when something cannot be deleted: the entry stays then."""
    with service.sync_lock():
        return known_devices.forget(
            unit_id, before=lambda: _delete(unit_id, discard_pending, backups))


def _delete(unit_id: int, discard_pending: bool, backups: bool) -> str:
    """Everything but the entry; words for the log line (never the unit ID)."""
    pending = len(_pending(unit_id))
    if pending and not discard_pending:
        raise PendingFiles(pending)
    deleted = 0
    if backups:
        folders = _backups(unit_id)
        for p in folders:
            shutil.rmtree(p)
        deleted = sum(1 for p in folders if not _partial(p))
        try:
            (backup_folder() / str(unit_id)).rmdir()  # only when nothing else is left in it
        except OSError:
            pass
    folder = paths.device_dir(unit_id)
    if folder.exists():
        shutil.rmtree(folder)
    shared = service.last_sync_path()
    try:
        of_it = json.loads(shared.read_text(encoding="utf-8")).get("unit_id") == unit_id
    except (OSError, ValueError, AttributeError):
        of_it = False
    if of_it:
        shared.unlink(missing_ok=True)
    return (f" and what this installation kept of it (not uploaded: {pending}, "
            f"backups deleted: {deleted})")


def _pending(unit_id: int) -> list[Path]:
    """The copies of files whose upload failed (sync.SyncState: pending/<data type>/<name>)."""
    folder = paths.device_dir(unit_id) / "sync" / "pending"
    if not folder.is_dir():
        return []
    return [p for p in folder.glob("*/*") if p.is_file()]


def _partial(p: Path) -> bool:
    return p.name.startswith(PARTIAL_PREFIX)


def _backups(unit_id: int) -> list[Path]:
    """The folders openExpress made in the device's backup folder: backups, backups cut short
    and copies of the app list. Anything else there is the user's and stays."""
    folder = backup_folder() / str(unit_id)
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.iterdir()
                  if not p.is_symlink() and p.is_dir()
                  and (is_backup_dir(p) or _partial(p)
                       or p.name.startswith(APP_LIST_COPY_PREFIX)))
