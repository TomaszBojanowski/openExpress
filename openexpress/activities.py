"""The activities of a connected device and of its backups: one list, read a few at a time.

The device's activity files (the folders its GarminDevice.xml gives the data type FIT_TYPE_4)
come first, newest first by the time of change the device gives them; then the files that only
the backups of the same device have (backup.backups_of), the newest backup first. A file on the
device and one in a backup with the same name (letter case aside) are one activity, read from
the device, whatever their sizes; of several backups with that name, the newest gives it. What
each file says (fit.summary) is kept by its source, path, size and time of change, so a list
made again reads only what is new or has changed. Nothing is written; the log gets counts,
sizes and times, never a name or a date.
"""

from __future__ import annotations

import logging
import os
import posixpath
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path

from openexpress import applog, fit, paths
from openexpress.backup import backups_of
from openexpress.device.storage import Storage
from openexpress.device.xmldevice import GarminDevice
from openexpress.i18n import _, pgettext
from openexpress.progress import Step, say_to
from openexpress.sync import ACTIVITY_TYPE, files_of

log = logging.getLogger(__name__)

# How many activities are read at a time: the newest first, then as many more when asked.
PAGE = 30


class Source(Enum):
    DEVICE = "device"
    BACKUP = "backup"


@dataclass(frozen=True)
class Activity:
    """An activity file listed: where it is (`path`: its real path on the device, or the path
    of the copy in a backup on this computer), its name, size and time of change, and for a
    copy in a backup when that backup was made."""

    source: Source
    path: str
    name: str
    size: int
    mtime: float
    backup: datetime | None = None

    @property
    def key(self) -> tuple:
        """What its summary is kept by: another size or time of change is another file."""
        return self.source, self.path, self.size, self.mtime


@dataclass(frozen=True)
class Read:
    """An activity and what its file says of it (None: no FIT file, or a copy in a backup
    that could not be read)."""

    activity: Activity
    summary: fit.ActivitySummary | None


def keeps_activities(device: GarminDevice) -> bool:
    """Whether the device's GarminDevice.xml gives it activity files to read."""
    return bool(device.sources(ACTIVITY_TYPE))


def sport_name(sport: int | None) -> str:
    """The name of a sport of the FIT profile; "Activity" when it is not known."""
    names = {
        0: pgettext("sport", "Activity"),  # FIT profile: generic
        1: pgettext("sport", "Running"),
        2: pgettext("sport", "Cycling"),
        4: pgettext("sport", "Fitness equipment"),
        5: pgettext("sport", "Swimming"),
        10: pgettext("sport", "Training"),
        11: pgettext("sport", "Walking"),
        12: pgettext("sport", "Cross-country skiing"),
        13: pgettext("sport", "Alpine skiing"),
        14: pgettext("sport", "Snowboarding"),
        15: pgettext("sport", "Rowing"),
        16: pgettext("sport", "Mountaineering"),
        17: pgettext("sport", "Hiking"),
        18: pgettext("sport", "Multisport"),
        19: pgettext("sport", "Paddling"),
        21: pgettext("sport", "E-biking"),
        25: pgettext("sport", "Golf"),
        31: pgettext("sport", "Rock climbing"),
        35: pgettext("sport", "Snowshoeing"),
        37: pgettext("sport", "Stand-up paddleboarding"),
        41: pgettext("sport", "Kayaking"),
        62: pgettext("sport", "HIIT"),
    }
    if sport is None:
        return names[0]
    # TRANSLATORS: a sport the program has no name for, by its number in the FIT profile
    return names.get(sport) or pgettext("sport", "Sport {number}").format(number=sport)


def export_name(summary: fit.ActivitySummary, activity: Activity) -> str:
    """The name of an activity's GPX file without its extension, "YYYY-MM-DD HH.MM Sport": its
    start in local time (else when its file was made, else the name of that file)."""
    when = summary.start or summary.created
    day = (time.strftime("%Y-%m-%d %H.%M", time.localtime(when)) if when
           else posixpath.splitext(activity.name)[0])
    return f"{day} {sport_name(summary.sport)}"


def _in_backup(when: datetime, folder: Path) -> list[Activity]:
    """The activity files of one backup: regular files only, never a symbolic link (nor the
    folders one leads to), each inside the backup's folder."""
    found = []
    try:
        for top, _folders, files in os.walk(folder / ACTIVITY_TYPE):
            for name in files:
                path = Path(top) / name
                if (path.is_symlink() or not name.casefold().endswith(".fit")
                        or not paths.is_inside(path, folder)):
                    continue
                stat = path.stat()
                found.append(Activity(Source.BACKUP, str(path), name, stat.st_size,
                                      stat.st_mtime, when))
    except OSError as e:
        log.info("activities: a backup could not be listed (%s)", type(e).__name__)
    return found


def _summary(data: bytes | None) -> fit.ActivitySummary | None:
    if data is None:
        return None
    try:
        return fit.summary(data)
    except fit.FitError:
        return None


class DeviceActivities:
    """The activities of one device and of its backups (`backups`: the folder of every
    device's backups; None: none read): listed by list_files, read `PAGE` at a time by
    read_next."""

    def __init__(self, device: GarminDevice, storage: Storage, backups: Path | None):
        self.device, self.storage, self.backups = device, storage, backups
        self.listing: list[Activity] = []
        self.read: list[Read] = []  # those of `listing` read so far, in its order
        self._kept: dict[tuple, fit.ActivitySummary | None] = {}

    @property
    def left(self) -> int:
        """How many of the list are not read yet."""
        return len(self.listing) - len(self.read)

    @property
    def only_in_backups(self) -> int:
        """How many of the list only a backup has."""
        return sum(1 for a in self.listing if a.source is Source.BACKUP)

    def list_files(self) -> None:
        """Lists the activity files of the device and of its backups again, no file read; the
        reading starts again from the newest, with what was kept."""
        started = time.monotonic()
        on_device = self._on_device()
        known = {a.name.casefold() for a in on_device}
        only_in_backups = []
        found = backups_of(self.backups, self.device.unit_id) if self.backups else []
        for when, folder in found:
            for a in sorted(_in_backup(when, folder), key=lambda a: a.name.casefold(),
                            reverse=True):
                if a.name.casefold() not in known:
                    known.add(a.name.casefold())
                    only_in_backups.append(a)
        self.listing = on_device + only_in_backups
        self.read = []
        log.info("activities: %d on the device, %d only in backups, backups read: %d "
                 "(listed in %.2f s)", len(on_device), len(only_in_backups), len(found),
                 time.monotonic() - started)

    def _on_device(self) -> list[Activity]:
        found = [Activity(Source.DEVICE, path, posixpath.basename(path), entry.size, entry.mtime)
                 for _src, path, entry in files_of(self.storage,
                                                   self.device.sources(ACTIVITY_TYPE))]
        return sorted(found, key=lambda a: (a.mtime, a.name.casefold()), reverse=True)

    def read_next(self, count: int = PAGE,
                  progress: Callable[[str], None] | None = None) -> list[Read]:
        """Reads the next `count` activities of the list and returns them; what is kept is
        not read again. `progress`: a step for each file read ("Reading activities · 2 of 3"),
        never its name. An error of the device stops it (DeviceError)."""
        progress = say_to(progress)
        batch = self.listing[len(self.read):len(self.read) + count]
        to_read = [a for a in batch if a.key not in self._kept]
        started, size = time.monotonic(), 0
        # TRANSLATORS: a step of a long task: the activity files read ("… · 2 of 30")
        head = _("Reading activities")
        for n, activity in enumerate(to_read, 1):
            progress(Step(head, n, len(to_read)))
            data = self.read_file(activity)
            size += len(data or b"")
            self._kept[activity.key] = _summary(data)
        found = [Read(a, self._kept[a.key]) for a in batch]
        self.read += found
        if to_read:
            states = [self._kept[a.key] for a in to_read]
            log.info("activities: %d of %d read, %d now in %.1f s (%s); %d still being "
                     "written, %d damaged, %d no FIT file", len(self.read), len(self.listing),
                     len(to_read), time.monotonic() - started, applog.size(size),
                     sum(1 for s in states if s and s.state is fit.FileState.STILL_BEING_WRITTEN),
                     sum(1 for s in states if s and s.state is fit.FileState.DAMAGED),
                     sum(1 for s in states if s is None))
        return found

    def read_file(self, activity: Activity) -> bytes | None:
        """The bytes of an activity's file; None for a copy in a backup that could not be read
        (one line of the log). An error of the device: DeviceError."""
        if activity.source is Source.DEVICE:
            return self.storage.read_at(activity.path)
        try:
            return Path(activity.path).read_bytes()
        except OSError as e:
            log.info("activities: a copy in a backup could not be read (%s)", type(e).__name__)
            return None
