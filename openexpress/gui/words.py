"""The window's words for what it knows of a device, one function for each fact: every page
that says a fact says it in these words. Without GTK, so that a test or the command line can
use them too."""
from __future__ import annotations

import math
import re
from dataclasses import replace
from datetime import datetime
from typing import NamedTuple

from openexpress import known_devices, last_sync, service, sync
from openexpress.i18n import (
    N_,
    _,
    caption,
    decimal,
    full_time,
    human_size,
    ngettext,
    pgettext,
    relative_time,
    whole,
)


def updates_available(count: int, only=None) -> str:
    """Updates to install, `count` of them: of one (`only`), the version of a system update
    or the name and version of another, as the Updates page lists it; of several (or one not
    known), how many."""
    if count == 1 and only is not None and only.is_system:
        # TRANSLATORS: the one update to install is of the device's system software (the row
        # "Device Software" and the top of the Updates page)
        return _("Version {version} available").format(version=only.version)
    if count == 1 and only is not None:
        # TRANSLATORS: the one update to install, of another part of the device (its name and
        # version, as the server gives them)
        return _("Update available: {name} {version}").format(name=only.title,
                                                               version=only.version)
    return ngettext("{count} update available", "{count} updates available",
                    count).format(count=count)


def copied_update_text(u) -> str | None:
    """Of an update openExpress copied to the device before that the server offers again (the
    device has not installed it): when it was copied, and that its file was changed or
    refused by the device, or that the device is to be checked. None for any other update."""
    at = getattr(u, "copied_at", None)
    if at is None or u.on_device:
        return None
    when = full_time(datetime.fromtimestamp(at))
    if u.changed_on_device:
        # TRANSLATORS: below an update copied to the device before that Garmin's server offers
        # again, whose file on the device has other bytes since
        return _("The device changed or refused the file copied {when}").format(when=when)
    # TRANSLATORS: below an update copied to the device before that Garmin's server offers
    # again: the device has not reported it installed
    return _("Copied {when}, check on the device that it is installed").format(when=when)


def not_installed_text(u) -> str:
    """Below an update the device has not installed although it was unplugged since openExpress
    copied it (updates.not_installed): when it was copied, what the device did with the file,
    and the version it still reports, when that is known (versions_text)."""
    when = full_time(datetime.fromtimestamp(u.copied_at))
    old = u.installed_version
    if u.changed_on_device:
        if old:
            # TRANSLATORS: below an update the device has not installed although it was unplugged
            # since: {when} when it was copied, {old} the version the device still reports
            return _("Copied {when}. Since then the device has been unplugged, has changed or "
                     "refused the file, and still reports version {old}.").format(when=when,
                                                                                  old=old)
        return _("Copied {when}. Since then the device has been unplugged, has changed or "
                 "refused the file, and has not installed it.").format(when=when)
    if old:
        return _("Copied {when}. Since then the device has been unplugged and still reports "
                 "version {old}; the file is still on it, unchanged.").format(when=when, old=old)
    return _("Copied {when}. Since then the device has been unplugged and has not installed "
             "it; the file is still on it, unchanged.").format(when=when)


def not_installed_advice(hidden_folder: bool = False) -> str:
    """What to do about an update the device has not installed (not_installed_text).
    `hidden_folder`: it went to a folder the device hides (a car navigator, which installs it
    on its own power once unplugged), which has no menu of a watch nor a drive to eject."""
    if hidden_folder:
        # TRANSLATORS: on the Updates page, after a car navigator was unplugged and plugged in
        # again without installing the update openExpress copied
        return _("Unplug the device from the computer, connect it to its car cable or a USB "
                 "charger, turn it on and wait until it has installed the update. If the update "
                 "is still listed when you plug the device in again, a device report helps find "
                 "out why.")
    # TRANSLATORS: "Settings → System → About" is the menu of the device; use the words of the
    # Garmin devices in your language
    return _("Check the version on the device (Settings → System → About). Ejecting "
             "it again does not help; a device report helps find out why.")


def update_state_text(state: str | None, count: int = 0, only=None,
                      mass_storage: bool = False, waiting: int = 1) -> str:
    """The state of the device's updates in one text, for the row "Device Software" and the
    top of the Updates page: "available" (updates_available), "ready" (`waiting` copied,
    waiting for the device to be disconnected, or ejected: `mass_storage`; it wins over the
    `count` still to install, told after it), "not-installed" (copied, not installed by the
    device although it was unplugged since: updates.not_installed), "current", "failed", or
    None (not known yet)."""
    if state == "available":
        return updates_available(count, only)
    if state == "ready":
        ready = (ngettext("Update ready: eject the device to install it",
                          "Updates ready: eject the device to install them", waiting)
                 if mass_storage else
                 ngettext("Update ready: unplug the device to install it",
                          "Updates ready: unplug the device to install them", waiting))
        return caption(ready, more_available(count))
    if state == "current":
        # TRANSLATORS: the state of the updates, without a full stop
        return _("Software is up to date")
    if state == "not-installed":
        # TRANSLATORS: the state of the updates: an update openExpress copied is on the device,
        # which was unplugged since and has not installed it; without a full stop
        return _("Update copied, not installed by the device")
    if state == "failed":
        return _("Could not check for updates")
    return _("Checking…")


def ready_heading(waiting: int) -> str:
    """The heading of the Updates page when `waiting` updates are copied and wait for the
    device; what to do is under it (ready_line). The row "Device Software" says both in one
    sentence (update_state_text)."""
    # TRANSLATORS: heading of the Updates page when updates are copied and wait for the device
    # to be unplugged (or ejected)
    return ngettext("Update Ready", "Updates Ready", waiting)


def ready_line(waiting: int, count: int, mass_storage: bool) -> str:
    """The line under ready_heading: unplug the device, or eject it (`mass_storage`), and how
    many more there are to install (`count`)."""
    if mass_storage:
        # TRANSLATORS: the line under "Update Ready" on the Updates page, for a device connected
        # as a USB drive; without a full stop
        line = ngettext("Eject the device to install it", "Eject the device to install them",
                        waiting)
    else:
        # TRANSLATORS: the line under "Update Ready" on the Updates page; without a full stop
        line = ngettext("Unplug the device to install it", "Unplug the device to install them",
                        waiting)
    return caption(line, more_available(count))


def more_available(count: int) -> str:
    """After an update copied already: how many more there are to install ("" for none)."""
    if not count:
        return ""
    # TRANSLATORS: after "Update ready: unplug the device to install it" or "Update ready: eject
    # the device to install it" (and, on the Updates page, after "Unplug the device to install
    # it" or "Eject the device to install it"): updates still to install besides the one copied
    # already
    return ngettext("{count} more available", "{count} more available", count).format(
        count=count)


def checked_text(text: str, at: float, now: datetime | None = None) -> str:
    """What a check of updates said, when it is not of now: when that check was, told against
    now ("Software is up to date · checked 3 days ago")."""
    # TRANSLATORS: after the state of the updates of a device: when the check that said it
    # was (e.g. "checked 3 days ago")
    return caption(text, _("checked {when}").format(
        when=relative_time(datetime.fromtimestamp(at), now or datetime.now())))


def not_checked_since(at: float) -> str:
    """The last check of updates is too old to tell, or of the software the device had
    before: since when nothing is known."""
    # TRANSLATORS: the updates of a device not connected; {when} is a date and an hour
    return _("Not checked since {when}").format(when=full_time(datetime.fromtimestamp(at)))


def remembered_update_text(entry, now: datetime | None = None) -> str:
    """The row "Device Software" of a device not connected (`entry`, its KnownDevice; None:
    not remembered here): what the last check kept with it found, and when, while it is
    recent and of the software the device had when last seen; "Not checked since …" for an
    older check; "Connect the device to check" without one, or after an update copied."""
    now = now or datetime.now()
    connect = _("Connect the device to check")
    if entry is None:
        return connect
    recorded = known_devices.recorded_state(entry, entry.software_version, now.timestamp())
    if recorded is not None and recorded.state == "ready":
        # The device installs it once unplugged, which it has been since: only what else the
        # check found still holds.
        if recorded.found is None:
            return connect
        recorded = replace(recorded, state="available", at=entry.update_found)
    if recorded is not None:
        return checked_text(update_state_text(recorded.state, recorded.count, recorded.only),
                            recorded.at, now)
    last = known_devices.last_update_check(entry)
    return not_checked_since(last) if last is not None else connect


def never_synced() -> str:
    """A device never synced here (sync.synced_here: no state.json of this installation, nor,
    from source, of the Flatpak): on its tile and on its page, the same words."""
    # TRANSLATORS: a chip on the tile of a connected device that openExpress on this
    # computer has never synced, and the subtitle of "Last Sync" on its page
    return pgettext("chip", "Never synced")


def never_synced_tooltip() -> str:
    """The tooltip of never_synced: each installation keeps its own record (the version from
    source reads the Flatpak's too)."""
    return _("Never synced by this installation of openExpress")


def no_sync_record() -> str:
    """"Last Sync" of a device synced here before (its state.json) without a record of its
    last sync: its files marked as uploaded alone (baseline), or a sync of a version that
    kept no record."""
    # TRANSLATORS: the subtitle of "Last Sync" after the files of the device were marked as
    # uploaded without a sync (or synced by an old version)
    return _("No record of the last sync; the next sync uploads new data only")


def last_sync_words(data: dict, now: datetime | None = None) -> str:
    """The row under "Last Sync": when, told against now ("yesterday, 23:41"), and the
    numbers of that sync (last_sync.describe). Now is the window's clock."""
    return last_sync.describe(data).row(now or datetime.now())


def last_sync_subtitle(unit_id, data: dict | None, to_sync=None,
                       now: datetime | None = None, test_drive: bool = False) -> str:
    """The subtitle of "Last Sync": "Never synced" when the device was never synced here
    (sync.synced_here, the one source of it, as on the tile; a test drive: by its own record);
    else the last sync (`data`, last_sync.json) or that none is recorded, then the new
    activities to upload (`to_sync`, the count of the chip "To sync"), unless that sync could
    not upload (no chip then). The same on the device page and in `openexpress status`."""
    if not unit_id or not sync.synced_here(unit_id, test_drive):
        return never_synced()
    text = last_sync_words(data, now) if data else no_sync_record()
    return with_activities_to_upload(text, data, to_sync)


def to_upload_text(count: int) -> str:
    """After the numbers of the last sync of a connected device: how many activities it has
    now that openExpress has not uploaded yet (the number of the chip "To sync"), said apart
    from what that sync found."""
    # TRANSLATORS: at the end of the subtitle of "Last Sync" of a connected device (and of
    # `openexpress status`), after what the last sync found ("nothing to upload")
    return ngettext("{count} activity to upload now", "{count} activities to upload now",
                    count).format(count=count)


def with_activities_to_upload(text: str, record: dict | None, to_sync) -> str:
    """`text` (the last sync, or that none is recorded), then the activities the connected
    device has to upload now (`to_sync`: sync.ActivitiesToSync, the count of the chip "To
    sync"), unless that sync could not upload (no chip then): the row "Last Sync" of the
    device page and the line of `openexpress status`."""
    count = to_sync.count if to_sync is not None else None
    if count and (not record or service.not_uploaded(record) is None):
        return caption(text, to_upload_text(count))
    return text


def primary_device_text(is_set: bool, is_this_device: bool, name: str | None) -> str:
    """The subtitle of "Primary Training Device" and of "Primary Wearable"."""
    if not is_set:
        # TRANSLATORS: subtitle of "Primary Training Device" or "Primary Wearable": the account
        # has none set
        return _("Not set")
    if is_this_device:
        # TRANSLATORS: subtitle of "Primary Training Device" or "Primary Wearable": it is the
        # device shown
        return _("This device")
    # TRANSLATORS: subtitle of "Primary Training Device" or "Primary Wearable": another device
    # of the account, its name not known
    return name or _("Another device")


def primary_wearable_text(wearable) -> str:
    """The subtitle of "Primary Wearable" (devicelist.PrimaryWearable)."""
    return primary_device_text(wearable.set, wearable.is_this_device, wearable.name)


def primary_wearable_tooltip() -> str:
    """The tooltip of the chip "Primary" of a tile: what it is, in Garmin Connect's name."""
    # TRANSLATORS: "Primary wearable" is Garmin's name of the setting (in Polish its manuals
    # say "Podstawowe urządzenie noszone na ciele")
    return _("Primary wearable: steps and sleep in Garmin Connect come from this device")


def software_text(version: str, from_account: bool = False) -> str:
    """The version of the device's software, the same words everywhere: "Software 23.39";
    "Software 23.39 (Garmin Connect)" when only the account says it (it may be older than
    what the device runs)."""
    if from_account:
        # TRANSLATORS: the version of a device that only the Garmin account knows of
        return _("Software {version} (Garmin Connect)").format(version=version)
    # TRANSLATORS: the version of a device's software, under its name ("Software 23.39")
    return _("Software {version}").format(version=version)


def space_text(free: int | None, total: int | None = None, reading: bool = False) -> str:
    """The free space of the device in one caption, the same on every page and in every
    question: "2.1 GB free of 32.0 GB", "2.1 GB free" without its capacity, "Reading…" while it
    is read, "Not known" when the device does not say."""
    if reading:
        # TRANSLATORS: the free space of the device while it is read
        return _("Reading…")
    if free is None:
        # TRANSLATORS: the free space of the device when it does not say
        return _("Not known")
    if total:
        return _("{free} free of {total}").format(free=human_size(free), total=human_size(total))
    # TRANSLATORS: free space on the device when its capacity is not known; {size}: e.g. "3.2 GB"
    return _("{size} free").format(size=human_size(free))


def free_after_text(free: int | None, needed: int, change: int, doing: str) -> str:
    """The line of a question before a map is installed, updated or copied ("install",
    "update", "copy") about the free space: what is left once it is done (`change`: how much
    less), or what is missing for the `needed`, or that the device does not say."""
    if free is None:
        # TRANSLATORS: line of the confirmation before a map is installed or copied, when the device
        # does not say how much space is free
        return _("Free space on the device: unknown")
    if needed > free:
        # TRANSLATORS: line of the confirmation before a map is installed or copied: {size} more
        # would have to be free
        return _("Not enough space on the device: {size} missing").format(
            size=human_size(needed - free))
    after = {
        # TRANSLATORS: line of the confirmation before a map is installed: the space free once it
        # is installed
        "install": _("Free space after installing: {free}"),
        # TRANSLATORS: line of the confirmation before a map is updated: the space free once the
        # new files are written and the old ones removed
        "update": _("Free space after updating: {free}"),
        # TRANSLATORS: line of the confirmation before a map of your own is copied to the device:
        # the space free once it is copied
        "copy": _("Free space after copying: {free}"),
    }[doing]
    return after.format(free=human_size(max(0, free - change)))


def last_seen_text(last_seen: float, now: datetime | None = None) -> str:
    """When this computer last saw a device, told against now, as the last sync is ("Last
    seen 2 h ago"): on its tile and on its page alike; the full date is in the tooltip
    (last_seen_tooltip)."""
    # TRANSLATORS: when this computer last saw a device: {when} is "2 h ago" on a tile, a full date
    # in its tooltip and in the CLI
    return _("Last seen {when}").format(
        when=relative_time(datetime.fromtimestamp(last_seen), now or datetime.now()))


def last_seen_tooltip(last_seen: float) -> str:
    """The tooltip of last_seen_text: when, in full."""
    return _("Last seen {when}").format(when=full_time(datetime.fromtimestamp(last_seen)))


# --- the Activities page: what an activity file says ---------------------------------------


def activity_time_text(seconds: float) -> str:
    """The time of an activity: "1 h 02 min", "45 min", "40 s" (whole minutes from a minute)."""
    total = max(0, round(seconds))
    hours, minutes = divmod(total // 60, 60)
    if hours:
        # TRANSLATORS: the time of an activity in hours and minutes ("1 h 02 min")
        return _("{hours} h {minutes} min").format(hours=hours,
                                                            minutes=f"{minutes:02}")
    if minutes:
        return _("{minutes} min").format(minutes=minutes)
    return _("{seconds} s").format(seconds=total)


def activity_title(read) -> str:
    """The title of an activity's row (activities.Read): its sport, "Activity" when the file
    does not say, "Unreadable File" for one that is no FIT file."""
    from openexpress.activities import sport_name

    if read.summary is None:
        # TRANSLATORS: title of the row of an activity whose file is not a FIT file, in place of its
        # sport
        return _("Unreadable File")
    return sport_name(read.summary.sport)


def activity_start(read) -> str:
    """When an activity (activities.Read) started (its first session; the time its file was
    made standing in); "" when its file does not say."""
    found = read.summary
    when = (found.start or found.created) if found is not None else None
    return full_time(datetime.fromtimestamp(when)) if when else ""


def activity_caption(read) -> str:
    """The details of an activity's row: when it started, its time, distance and ascent,
    where it was read, and what is wrong with its file; of a file the device was still writing
    only when it was made and that."""
    from openexpress import fit
    from openexpress.activities import Source
    a, found = read.activity, read.summary
    if a.source is Source.DEVICE:
        # TRANSLATORS: in the details of an activity on the Activities page: where it was read
        source = pgettext("activity source", "Device")
    else:
        source = pgettext("activity source", "Backup of {date}").format(
            date=a.backup.strftime(_("%Y-%m-%d")))
    if found is None:
        return source
    parts = [activity_start(read)]
    if found.state is fit.FileState.STILL_BEING_WRITTEN:
        return caption(*parts, still_being_written_text())
    if found.timer_time is not None:
        parts.append(activity_time_text(found.timer_time))
    if found.distance:
        parts.append(row_distance_text(found.distance))
    if found.ascent is not None:
        parts.append(row_ascent_text(found.ascent))
    parts.append(source)
    if found.state is fit.FileState.DAMAGED:
        parts.append(damaged_file_text())
    return caption(*parts)


def row_distance_text(metres: float) -> str:
    """A distance in the details of a row on the Activities and Courses pages: kilometres with
    one decimal ("12.4 km", a hard space before the unit)."""
    return f"{decimal(f'{metres / 1000:.1f}')}\u00a0km"


def row_ascent_text(metres: float) -> str:
    """An ascent in the details of a row on the Activities and Courses pages: whole metres after
    an arrow up ("↑ 321 m", hard spaces)."""
    return f"\u2191\u00a0{round(metres)}\u00a0m"


def still_being_written_text() -> str:
    """An activity whose file its device was still writing when it was connected, in its row."""
    return _("Still being written: unplug the device and plug it in again")


def still_being_written_title() -> tuple[str, str]:
    """The title and the description of the page of an activity whose file its device was
    still writing when it was connected."""
    # TRANSLATORS: title of the page of an activity whose file the device was still writing when
    # it was connected
    return _("Still Being Written"), _("Unplug the device and plug it in again")


def damaged_file_text() -> str:
    """An activity whose file is damaged: in its row and under the tiles of its page."""
    return _("Damaged file")


def activity_file_error() -> str:
    """The error of an activity whose file could not be read again (a copy in a backup that
    could not be read, or no FIT file)."""
    return _("The file of this activity could not be read.")


def activity_not_read_title() -> tuple[str, str]:
    """The title and the description of the page of an activity whose file could not be read
    again."""
    # TRANSLATORS: title of the page of an activity whose file could not be read
    return _("Activity Not Read"), _("The file of this activity could not be read")


def no_summary_title() -> tuple[str, str]:
    """The title and the description of the page of an activity whose file has no summary (no
    session)."""
    # TRANSLATORS: title of the page of an activity whose file has no summary (no tiles to show)
    return _("No Summary"), _("This activity has no summary")


def reading_activity_text() -> str:
    """The page of an activity while its file is read again: its title, and the task's."""
    return _("Reading the Activity…")


def activities_count_text(count: int, only_in_backups: int) -> str:
    """Above the rows of the Activities page: how many activities, and how many of them only
    a backup has."""
    return caption(
        # TRANSLATORS: above the rows of the Activities page: how many activities are listed
        ngettext("{count} activity", "{count} activities", count).format(count=count),
        # TRANSLATORS: on the Activities page: how many activities only a backup has
        _("{count} only in backups").format(count=only_in_backups) if only_in_backups else "")


def stalled_text(names: list[str | None]) -> str:
    """The banner while devices that stopped responding are still plugged in: each of them
    (in the order they stopped), named when every name is known."""
    count = len(names)
    if names and all(names):
        return ngettext(
            # TRANSLATORS: a banner of the window; {names}: the names of the devices, e.g.
            # "fēnix 8" or "fēnix 8, Edge 1050"
            "The device {names} stopped responding. Unplug it and plug it in again.",
            "The devices {names} stopped responding. Unplug them and plug them in again.",
            count).format(names=", ".join(names))
    # TRANSLATORS: the same banner, when the name of a device is not known
    return ngettext("The device stopped responding. Unplug it and plug it in again.",
                    "The devices stopped responding. Unplug them and plug them in again.",
                    count)


# --- the Activities page since the connection before this one (since_last.py) ----------------

# The days of the week as datetime.weekday() counts them, each a whole sentence (in some
# languages the name changes with the words before it); two to six days back.
_SINCE_DAY = (
    # TRANSLATORS: under the heading of the Activities page: the activities since the device's
    # connection before this one, on a Monday two to six days back
    N_("Since Monday"),
    # TRANSLATORS: the same, on a Tuesday two to six days back
    N_("Since Tuesday"),
    # TRANSLATORS: the same, on a Wednesday two to six days back
    N_("Since Wednesday"),
    # TRANSLATORS: the same, on a Thursday two to six days back
    N_("Since Thursday"),
    # TRANSLATORS: the same, on a Friday two to six days back
    N_("Since Friday"),
    # TRANSLATORS: the same, on a Saturday two to six days back
    N_("Since Saturday"),
    # TRANSLATORS: the same, on a Sunday two to six days back
    N_("Since Sunday"),
)
SINCE_DAYS = 6  # back to here, the day's name; before, its date


def since_text(since: float, now: datetime | None = None) -> str:
    """Since when the Activities page counts new activities, by the day of the connection before
    this one on this computer's calendar: "Since today", "Since yesterday", the day of the week
    two to six days back ("Since Thursday"), its date in digits before that ("Since
    2026-11-12")."""
    day = datetime.fromtimestamp(since)
    back = ((now or datetime.now()).date() - day.date()).days
    if back == 0:
        # TRANSLATORS: under the heading of the Activities page: the activities since the device's
        # connection before this one, earlier today
        return _("Since today")
    if back == 1:
        # TRANSLATORS: the same, the connection before this one yesterday
        return _("Since yesterday")
    if 2 <= back <= SINCE_DAYS:
        return _(_SINCE_DAY[day.weekday()])
    # TRANSLATORS: under the heading of the Activities page: the activities since the device's
    # connection before this one, more than six days ago; {date} in digits, e.g. 2026-11-12
    return _("Since {date}").format(date=day.strftime(_("%Y-%m-%d")))


def new_activities_text(count: int) -> str:
    """The heading of the Activities page: how many activities are new since the connection
    before this one ("3 new activities"); "No new activities" for none."""
    if not count:
        # TRANSLATORS: heading of the Activities page when none started since the device's
        # connection before this one
        return _("No new activities")
    # TRANSLATORS: heading of the Activities page: the activities started since the device's
    # connection before this one
    return ngettext("{count} new activity", "{count} new activities", count).format(count=count)


def latest_text() -> str:
    # TRANSLATORS: above the tiles of the newest activities on the Activities page, when none is
    # new
    return _("Latest")


def journal_started_text() -> str:
    # TRANSLATORS: heading of the Activities page after the activity journal read a device for
    # the first time (no connection of it before)
    return _("Activity journal started")


def journal_read_text(count: int, oldest: float) -> str:
    """Under "Activity journal started": how many activities the first reading took, from the
    day of the oldest."""
    date = datetime.fromtimestamp(oldest).strftime(_("%Y-%m-%d"))
    # TRANSLATORS: under "Activity journal started"; {date} in digits, e.g. 2024-11-03
    return ngettext("{count} activity read from this device, from {date}",
                    "{count} activities read from this device, from {date}",
                    count).format(count=count, date=date)


def reading_journal_text() -> str:
    # TRANSLATORS: the Activities page while the activity journal reads the device's activities
    return _("Reading the activities of this device…")


def gap_text() -> str:
    # TRANSLATORS: under the heading of the Activities page, when the device deleted activities
    # the journal had not read yet (too many between two connections, or the journal was off)
    return _("Some older activities left the device before openExpress could record them")


def earlier_text(count: int) -> str:
    # TRANSLATORS: at the bottom of the Activities page, beside Show All: the activities still
    # on the device that are not new
    return ngettext("{count} earlier activity on this device",
                    "{count} earlier activities on this device", count).format(count=count)


def upload_state_text(state: str) -> str:
    """The upload state of a tile: "uploaded" (the sync's record has the file), "waiting"
    (kept to upload again)."""
    if state == "uploaded":
        # TRANSLATORS: on the tile of an activity: openExpress uploaded it to Garmin Connect
        return pgettext("upload state", "Uploaded")
    # TRANSLATORS: on the tile of an activity: its upload to Garmin Connect failed and is tried
    # again at the next sync
    return pgettext("upload state", "Not uploaded yet")


def since_distance(metres: float, miles: bool) -> tuple[str, str]:
    """A distance of the page since the last connection, as a template and its number: one
    decimal, in kilometres or in miles ("{distance} km", "53.0")."""
    if miles:
        # TRANSLATORS: a distance in miles on the Activities page ("33.0 mi")
        return _("{value} mi"), decimal(f"{metres / 1609.344:.1f}")
    return _("{distance} km"), decimal(f"{metres / 1000:.1f}")


def since_height(metres: float, feet: bool) -> tuple[str, str]:
    """An ascent of the page since the last connection, as a template and its number: whole
    metres or feet ("{metres} m", "893")."""
    if feet:
        # TRANSLATORS: an ascent in feet on the Activities page ("2930 ft")
        return _("{value} ft"), whole(round(metres / 0.3048))
    return _("{metres} m"), whole(round(metres))


def hours_and_minutes(seconds: float) -> str:
    """A time of the page since the last connection (its card, a tile), the same in every
    language: "2:51", "0:38"."""
    total = max(0, round(seconds)) // 60
    return f"{total // 60}:{total % 60:02}"


def _whole(text: str) -> str:
    """A number and its unit kept on one line (a hard space between them)."""
    return text.replace(" ", "\u00a0")


def tile_caption(activity: dict, units) -> str:
    """The numbers of a tile: its distance, time and ascent ("21.3 km · 2:51 · 640 m"), each
    left out when the activity has none (a workout without a distance, a swim in a pool
    without an ascent)."""
    parts = []
    if activity.get("distance"):
        template, number = since_distance(activity["distance"], units.miles)
        parts.append(_whole(template.format(distance=number, value=number)))
    time = activity.get("timer_time")
    if time is None:
        time = activity.get("elapsed_time")
    if time:
        parts.append(hours_and_minutes(time))
    if activity.get("ascent"):
        template, number = since_height(activity["ascent"], units.feet)
        parts.append(_whole(template.format(metres=number, value=number)))
    return caption(*parts)


def tile_day(day: datetime) -> str:
    """The day of a tile, in digits ("2026-11-21"), as the program writes dates."""
    return day.strftime(_("%Y-%m-%d"))


def trivia_lead(kind: str, metres: float, units) -> str:
    """The first sentence of the trivia: how high the new activities climbed, or how far they
    went ("893 m climbed.")."""
    if kind == "climbed":
        template, number = since_height(metres, units.feet)
        amount = _whole(template.format(metres=number, value=number))
        # TRANSLATORS: the trivia of the Activities page, first sentence; {climbed}: the ascent
        # of the new activities ("893 m climbed.")
        return _("{climbed} climbed.").format(climbed=amount)
    template, number = since_distance(metres, units.miles)
    amount = _whole(template.format(distance=number, value=number))
    # TRANSLATORS: the trivia of the Activities page, first sentence; {distance}: the distance of
    # the new activities ("53.0 km covered.")
    return _("{distance} covered.").format(distance=amount)


def trivia_object(thing: str, times: float) -> str:
    """The second sentence of the trivia: the well-known height or length and how many times
    it is; {times} always with one decimal ("That is the Eiffel Tower 2.7 times.")."""
    sentences = {
        # TRANSLATORS: the trivia of the Activities page after "893 m climbed."; {times} is always
        # a number with one decimal, e.g. 2.7 (also the sentences of the other heights and
        # lengths)
        "statue-of-liberty": N_("That is the Statue of Liberty {times} times."),
        "great-pyramid": N_("That is the Great Pyramid of Giza {times} times."),
        "eiffel-tower": N_("That is the Eiffel Tower {times} times."),
        "empire-state-building": N_("That is the Empire State Building {times} times."),
        "burj-khalifa": N_("That is the Burj Khalifa {times} times."),
        "table-mountain": N_("That is Table Mountain {times} times."),
        "mont-blanc": N_("That is Mont Blanc {times} times."),
        "kilimanjaro": N_("That is Kilimanjaro {times} times."),
        "everest": N_("That is Mount Everest {times} times."),
        # TRANSLATORS: the edge of space: the Kármán line, 100 km up
        "edge-of-space": N_("That is the edge of space {times} times."),
        "marathon": N_("That is a marathon {times} times."),
        "panama-canal": N_("That is the Panama Canal {times} times."),
        "grand-canyon": N_("That is the Grand Canyon {times} times."),
        "rhine": N_("That is the Rhine {times} times."),
        "danube": N_("That is the Danube {times} times."),
        "equator": N_("That is the equator {times} times."),
    }
    return _(sentences[thing]).format(times=decimal(f"{times:.1f}"))


# --- the page of an activity -------------------------------------------------------------------

# FIT sports read with a pace a kilometre: running, walking, mountaineering, hiking, snowshoeing;
# swimming with a pace a hundred metres; any other with a distance, with a speed.
_PACE_SPORTS = {1, 11, 16, 17, 35}
_SWIMMING = 5
PACES = ("pace", "swim pace")  # the series read as a pace: the faster, the lower


def speed_kind(sport: int | None) -> str:
    """How an activity of this FIT sport tells how fast it went: "pace" (a kilometre), "swim
    pace" (a hundred metres) or "speed"."""
    if sport in _PACE_SPORTS:
        return "pace"
    return "swim pace" if sport == _SWIMMING else "speed"


def activity_clock(seconds: float) -> str:
    """A time with its seconds, the same in every language: "0:41", "52:41", "1:02:41"."""
    total = max(0, round(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02}:{secs:02}" if hours else f"{minutes}:{secs:02}"


def activity_distance(metres: float | None, sport: int | None) -> str | None:
    """A distance: in kilometres with two decimals ("10.24 km"), a swim in whole metres ("1500
    m"); None for none or 0 m (a workout may record 0 m)."""
    if not metres or metres <= 0:
        return None
    if sport == _SWIMMING:
        return metres_text(metres)
    # TRANSLATORS: a distance in kilometres on the page of an activity ("10.24 km")
    return _("{distance} km").format(distance=decimal(f"{metres / 1000:.2f}"))


def activity_pace(speed: float | None, sport: int | None) -> str | None:
    """How fast, from a speed in metres a second, as the sport tells it (speed_kind): "5:10
    /km", "2:05 /100 m" or "24.3 km/h"; None for none or 0."""
    if not speed or speed <= 0:
        return None
    kind = speed_kind(sport)
    if kind == "pace":
        return _pace_text(kind, 1000 / speed)
    if kind == "swim pace":
        return _pace_text(kind, 100 / speed)
    return _pace_text(kind, speed * 3.6)


def _pace_text(kind: str, value: float) -> str:
    """A pace (`value`: the seconds a kilometre takes, a hundred metres of a swim) or a speed
    (kilometres an hour) in its unit."""
    if kind == "pace":
        # TRANSLATORS: a pace, the time a kilometre takes ("5:10 /km")
        return _("{pace} /km").format(pace=activity_clock(value))
    if kind == "swim pace":
        # TRANSLATORS: a pace of a swim, the time a hundred metres take ("2:05 /100 m")
        return _("{pace} /100 m").format(pace=activity_clock(value))
    # TRANSLATORS: a speed in kilometres an hour ("24.3 km/h")
    return _("{speed} km/h").format(speed=decimal(f"{value:.1f}"))


def metres_text(metres: float) -> str:
    """Whole metres: "740 m", from 10 000 with the thousands apart."""
    # TRANSLATORS: a distance or an ascent in whole metres on the page of an activity
    return _("{metres} m").format(metres=whole(round(metres)))


def heart_rate_text(bpm: int | None) -> str | None:
    """A heart rate: "148 bpm"; None when not given."""
    if bpm is None:
        return None
    # TRANSLATORS: a heart rate in beats a minute ("148 bpm"), in Garmin Connect's unit
    # (Polish keeps "bpm")
    return _("{rate} bpm").format(rate=bpm)


def calories_text(kcal: int | None) -> str | None:
    """Calories: "520 kcal"; None when not given."""
    if kcal is None:
        return None
    # TRANSLATORS: the calories of an activity ("520 kcal")
    return _("{calories} kcal").format(calories=whole(kcal))


def ascent_text(metres: int | None) -> str | None:
    """A total ascent in whole metres: "740 m"; None for none or 0, as the list leaves it out."""
    if not metres or metres <= 0:
        return None
    return metres_text(metres)


def tile_name(kind: str) -> str:
    """The name under the value of a tile of an activity's page; "swim pace" is a pace too."""
    names = {
        # TRANSLATORS: the names of the tiles on top of the page of an activity
        "distance": pgettext("activity tile", "Distance"),
        "time": pgettext("activity tile", "Time"),
        "pace": pgettext("activity tile", "Average Pace"),
        "swim pace": pgettext("activity tile", "Average Pace"),
        "speed": pgettext("activity tile", "Average Speed"),
        "average heart rate": pgettext("activity tile", "Average Heart Rate"),
        "maximum heart rate": pgettext("activity tile", "Maximum Heart Rate"),
        "calories": pgettext("activity tile", "Calories"),
        "ascent": pgettext("activity tile", "Total Ascent"),
    }
    return names[kind]


def activity_tile_kinds(data) -> list[tuple[str, str]]:
    """The tiles on top of an activity's page (fit.ActivityData), each (kind, value): with a
    distance its distance, time, pace or speed, average heart rate and ascent; without one (a
    workout, also one of 0 m) its time, average and maximum heart rate, calories and ascent. A
    value not given leaves its tile out, an ascent of 0 m too; the time is the timer's, the
    elapsed standing in; a speed its sessions do not give is worked out."""
    sport = data.sport
    time = data.timer_time if data.timer_time is not None else data.elapsed_time
    clock = activity_clock(time) if time is not None else None
    distance = activity_distance(data.distance, sport)
    if distance:
        speed = data.avg_speed
        if speed is None and time:
            speed = data.distance / time
        tiles = [("distance", distance), ("time", clock),
                 (speed_kind(sport), activity_pace(speed, sport)),
                 ("average heart rate", heart_rate_text(data.avg_heart_rate)),
                 ("ascent", ascent_text(data.ascent))]
    else:
        tiles = [("time", clock), ("average heart rate", heart_rate_text(data.avg_heart_rate)),
                 ("maximum heart rate", heart_rate_text(data.max_heart_rate)),
                 ("calories", calories_text(data.calories)), ("ascent", ascent_text(data.ascent))]
    return [(kind, value) for kind, value in tiles if value]


# --- the top of an activity's page: its numbers in the units of its device -------------------

_METRES_A_MILE = 1609.344
_METRES_A_YARD = 0.9144


def overview_title(shown) -> str:
    """The title of an activity's page (one_activity.Overview): its kind with its sub sport."""
    from openexpress.activities import kind_name

    return kind_name(shown.sport, shown.sub_sport)


def overview_when(shown, device: str) -> str:
    """Under the title: the day in digits, the time on the clock the device had where it was,
    and the device ("2026-11-21 · 09:14 · fēnix 8")."""
    from openexpress.one_activity import started

    when = started(shown)
    if when is None:
        return caption(device)
    return caption(when.strftime(_("%Y-%m-%d")), when.strftime("%H:%M"), device)


def _overview_distance(metres: float, sport: int | None, miles: bool) -> tuple[str, str]:
    """A distance of the top of an activity's page, as a template and its number: two decimals
    of a kilometre or of a mile, a swim in whole metres or yards."""
    if sport == _SWIMMING:
        if miles:
            # TRANSLATORS: the distance of a swim in yards on the page of an activity ("1500 yd")
            return _("{value} yd"), whole(round(metres / _METRES_A_YARD))
        return _("{metres} m"), whole(round(metres))
    if miles:
        return _("{value} mi"), decimal(f"{metres / _METRES_A_MILE:.2f}")
    return _("{distance} km"), decimal(f"{metres / 1000:.2f}")


def _overview_speed(speed: float, sport: int | None, miles: bool) -> tuple[str, str, str]:
    """How fast (metres a second) as the sport tells it, as its kind (speed_kind), a template
    and its number: "8:02" a kilometre or a mile, "2:00" a hundred metres or yards, "24.3" km/h
    or mph."""
    kind = speed_kind(sport)
    if kind == "pace":
        if miles:
            # TRANSLATORS: a pace, the time a mile takes ("12:56 /mi")
            return kind, _("{pace} /mi"), activity_clock(_METRES_A_MILE / speed)
        return kind, _("{pace} /km"), activity_clock(1000 / speed)
    if kind == "swim pace":
        if miles:
            # TRANSLATORS: a pace of a swim, the time a hundred yards take ("2:00 /100 yd")
            return kind, _("{pace} /100 yd"), activity_clock(100 * _METRES_A_YARD / speed)
        return kind, _("{pace} /100 m"), activity_clock(100 / speed)
    if miles:
        # TRANSLATORS: a speed in miles an hour ("24.9 mph")
        return kind, _("{speed} mph"), decimal(f"{speed * 3600 / _METRES_A_MILE:.1f}")
    return kind, _("{speed} km/h"), decimal(f"{speed * 3.6:.1f}")


def overview_numbers(shown) -> list[tuple[str, str, dict[str, str]]]:
    """The numbers on top of an activity's page (one_activity.Overview), each its name, its
    template and the values of the template (the number and its unit kept on one line): with a
    distance its distance, time, pace or speed and ascent; without one (a workout, also one of
    0 m) its time, calories, rest time and ascent; in the units of its device. One not given
    is left out, an ascent of 0 m too."""
    found: list[tuple[str, str, dict[str, str]]] = []

    def add(name: str, template: str, value: str) -> None:
        # The one field of the template, whatever its name in the translation.
        found.append((name, _whole(template),
                      dict.fromkeys(re.findall(r"\{(\w+)\}", template), value)))

    sport, units = shown.sport, shown.units
    if shown.distance and shown.distance > 0:
        template, number = _overview_distance(shown.distance, sport, units.miles)
        add(tile_name("distance"), template, number)
    if shown.time is not None:
        add(tile_name("time"), "{time}", activity_clock(shown.time))
    if shown.distance and shown.distance > 0:
        if shown.speed:
            kind, template, number = _overview_speed(shown.speed, sport, units.miles)
            add(tile_name(kind), template, number)
    else:
        if shown.calories is not None:
            add(tile_name("calories"), _("{calories} kcal"), whole(shown.calories))
        if shown.rest_time:
            add(detail_name("rest time"), "{time}", activity_clock(shown.rest_time))
    if shown.ascent and shown.ascent > 0:
        template, number = since_height(shown.ascent, units.feet)
        add(tile_name("ascent"), template, number)
    return found


def elevation_range(shown) -> list[str]:
    """The lowest and the highest point of an activity, in the units of its device ("38 m
    lowest", "212 m highest"); none when either is not known."""
    if shown.lowest is None or shown.highest is None:
        return []
    said = []
    for point, words_ in ((shown.lowest, "lowest"), (shown.highest, "highest")):
        template, number = since_height(point, shown.units.feet)
        height = _whole(template.format(metres=number, value=number))
        if words_ == "lowest":
            # TRANSLATORS: beside the title of the elevation of an activity: its lowest point
            # ("38 m lowest")
            said.append(pgettext("elevation", "{height} lowest").format(height=height))
        else:
            # TRANSLATORS: beside the title of the elevation of an activity: its highest point
            # ("212 m highest")
            said.append(pgettext("elevation", "{height} highest").format(height=height))
    return said


def heart_rate_words(shown) -> list[str]:
    """The average and the highest heart rate of an activity ("148 bpm average", "176 bpm
    highest"), each when known."""
    said = []
    if shown.avg_heart_rate is not None:
        # TRANSLATORS: beside the title of the heart rate zones: the average heart rate of the
        # activity ("148 bpm average")
        said.append(pgettext("heart rate", "{rate} average").format(
            rate=_whole(heart_rate_text(shown.avg_heart_rate))))
    if shown.max_heart_rate is not None:
        # TRANSLATORS: beside the title of the heart rate zones: the highest heart rate of the
        # activity ("176 bpm highest")
        said.append(pgettext("heart rate", "{rate} highest").format(
            rate=_whole(heart_rate_text(shown.max_heart_rate))))
    return said


def percent_text(percent: int) -> str:
    # TRANSLATORS: a share in percent ("38%"), under the bar of the heart rate zones
    return _("{percent}%").format(percent=percent)


def zone_shares(times) -> list[tuple[int, str]]:
    """Each zone (1 to 5; 0, the time below zone 1, first and only when there is some) with its
    share of the time in all ("38%"); none when no time is kept."""
    total = sum(times)
    if not total:
        return []
    return [(number, percent_text(round(seconds / total * 100)))
            for number, seconds in enumerate(times) if number or seconds]


def shape_only_text() -> str:
    # TRANSLATORS: over the shape of an activity's track drawn on a plain grid: there is no map
    # under it (before the yes to OpenStreetMap's map, or from the activity journal)
    return _("Shape only, no map underneath")


def from_journal_text() -> str:
    # TRANSLATORS: on the page of an activity whose file is gone: what the page shows comes from
    # the activity journal of this computer
    return _("The file of this activity is no longer on the device or in its backups; this is "
             "what the activity journal kept")


# FIT sports whose cadence is in steps a minute (two steps a stride): those read with a pace a
# kilometre; and those whose cadence is in revolutions a minute: cycling and e-biking. Not the
# groups of an activity's look (gui/sports.py), which put climbing on foot and snowshoeing in
# winter.
_STEP_CADENCE = _PACE_SPORTS
_PEDAL_CADENCE = {2, 21}


def details_title() -> str:
    # TRANSLATORS: the closed row of the details at the end of the page of an activity
    return pgettext("activity", "Details")


def detail_name(kind: str) -> str:
    """The name of a detail of an activity's page, as Garmin Connect says it."""
    names = {
        # TRANSLATORS: the details of the page of an activity, as Garmin Connect names them in
        # your language: the time with the pauses
        "elapsed time": pgettext("activity detail", "Elapsed Time"),
        "moving time": pgettext("activity detail", "Moving Time"),
        "work time": pgettext("activity detail", "Work Time"),
        "rest time": pgettext("activity detail", "Rest Time"),
        "descent": pgettext("activity detail", "Total Descent"),
        "minimum elevation": pgettext("activity detail", "Minimum Elevation"),
        "maximum elevation": pgettext("activity detail", "Maximum Elevation"),
        "moving pace": pgettext("activity detail", "Average Moving Pace"),
        "moving speed": pgettext("activity detail", "Average Moving Speed"),
        "best pace": pgettext("activity detail", "Best Pace"),
        "maximum speed": pgettext("activity detail", "Maximum Speed"),
        "cadence": pgettext("activity detail", "Average Cadence"),
        "steps": pgettext("activity detail", "Steps"),
        "heart rate": pgettext("activity detail", "Heart Rate"),
        "calories": pgettext("activity detail", "Calories"),
        "temperature": pgettext("activity detail", "Temperature"),
        "respiration": pgettext("activity detail", "Respiration Rate"),
    }
    return names[kind]


def _clock(seconds: float | None) -> str | None:
    return activity_clock(seconds) if seconds else None


def _cadence_text(data) -> str | None:
    """The average cadence and its maximum: steps a minute on foot (two a stride), revolutions
    a minute on a bike; None in another sport."""
    if not data.avg_cadence:
        return None
    if data.sport in _STEP_CADENCE:
        average = round(2 * data.avg_cadence)
        top = round(2 * data.max_cadence) if data.max_cadence else None
        if top is None:
            # TRANSLATORS: a cadence on foot, steps a minute ("104 spm")
            return _("{cadence} spm").format(cadence=average)
        # TRANSLATORS: the average and the maximum cadence on foot ("104 spm (max 118)")
        return _("{average} spm (max {max})").format(average=average, max=top)
    if data.sport in _PEDAL_CADENCE:
        average = round(data.avg_cadence)
        top = round(data.max_cadence) if data.max_cadence else None
        if top is None:
            # TRANSLATORS: a cadence on a bike, revolutions a minute ("85 rpm")
            return _("{cadence} rpm").format(cadence=average)
        # TRANSLATORS: the average and the maximum cadence on a bike ("85 rpm (max 110)")
        return _("{average} rpm (max {max})").format(average=average, max=top)
    return None


def _degrees(value: float) -> str:
    """A temperature with one decimal, as Garmin Connect shows it ("21.4"); a minus sign below
    zero."""
    shown = round(value, 1)
    return ("\u2212" if shown < 0 else "") + decimal(f"{abs(shown):.1f}")


def _temperature_text(data) -> str | None:
    if data.avg_temperature is None:
        return None
    average = _degrees(data.avg_temperature)
    if data.min_temperature is None or data.max_temperature is None:
        # TRANSLATORS: a temperature ("21.4 °C")
        return _("{temperature} °C").format(temperature=average)
    # TRANSLATORS: the average temperature and its range ("21.4 °C (18.0–26.0 °C)")
    return _("{average} °C ({low}–{high} °C)").format(
        average=average, low=_degrees(data.min_temperature),
        high=_degrees(data.max_temperature))


def _respiration_text(data) -> str | None:
    if data.avg_respiration is None:
        return None
    average = round(data.avg_respiration)
    if data.min_respiration is None or data.max_respiration is None:
        # TRANSLATORS: a respiration rate, breaths a minute ("24 brpm")
        return _("{rate} brpm").format(rate=average)
    # TRANSLATORS: the average respiration rate and its range ("24 brpm (15–38)")
    return _("{average} brpm ({low}–{high})").format(
        average=average, low=round(data.min_respiration), high=round(data.max_respiration))


def activity_detail_kinds(data, moving: float | None = None) -> list[tuple[str, str]]:
    """The details of an activity's page (fit.ActivityData), each (kind, value), in their
    order: elapsed time, moving time, work and rest time, descent, minimum and maximum
    elevation, average moving pace (or speed), best pace (or maximum speed), cadence, steps,
    heart rate (minimum–maximum), calories, temperature, respiration rate. Only what the file
    gives: `moving` is the moving time worked out from its records (None: none); no value of
    0; nothing a tile shows already (a time, a pace or calories the same as a tile's)."""
    sport, kind = data.sport, speed_kind(data.sport)
    tiles = dict(activity_tile_kinds(data))
    moving_speed = data.distance / moving if moving and data.distance else None
    found = [
        ("elapsed time", _clock(data.elapsed_time), "time"),
        ("moving time", _clock(moving) if "distance" in tiles else None, "time"),
        ("work time", _clock(data.active_time), "time"),
        ("rest time", _clock(data.rest_time), None),
        ("descent", metres_text(data.descent) if data.descent else None, None),
        ("minimum elevation",
         metres_text(data.min_altitude) if data.min_altitude is not None else None, None),
        ("maximum elevation",
         metres_text(data.max_altitude) if data.max_altitude is not None else None, None),
        ("moving speed" if kind == "speed" else "moving pace",
         activity_pace(moving_speed, sport) if "distance" in tiles else None, kind),
        ("maximum speed" if kind == "speed" else "best pace",
         activity_pace(data.max_speed, sport), None),
        ("cadence", _cadence_text(data), None),
        ("steps", whole(round(2 * data.cycles))
         if data.cycles and sport in _STEP_CADENCE else None, None),
        ("heart rate", _("{low}–{high} bpm").format(low=data.min_heart_rate,
                                                     high=data.max_heart_rate)
         if data.min_heart_rate is not None and data.max_heart_rate is not None else None, None),
        ("calories", calories_text(data.calories), "calories"),
        ("temperature", _temperature_text(data), None),
        ("respiration", _respiration_text(data), None),
    ]
    return [(detail, value) for detail, value, tile in found
            if value and (tile is None or tiles.get(tile) != value)]


def activity_details(data, moving: float | None = None) -> list[tuple[str, str]]:
    """The details of activity_detail_kinds, each (name, value)."""
    return [(detail_name(kind), value) for kind, value in activity_detail_kinds(data, moving)]


def lap_title(number: int) -> str:
    """The title of a lap's row: "Lap 1"."""
    # TRANSLATORS: the title of the row of a lap on the page of an activity
    return _("Lap {number}").format(number=number)


def laps_title(kilometres: bool) -> str:
    """The title of the laps of an activity's page: its device's laps, or the kilometres
    worked out from its records when the device kept none."""
    # TRANSLATORS: title of the list of kilometre splits worked out from an activity's records,
    # shown in place of "Laps" (not the unit)
    return (_("Kilometres") if kilometres
            # TRANSLATORS: title of the list of an activity's laps as its device recorded them
            else _("Laps"))


def kilometre_title(number: int) -> str:
    """The title of the row of a kilometre worked out from the records: "Kilometre 1"."""
    # TRANSLATORS: the title of the row of a kilometre on the page of an activity whose device
    # kept no laps
    return _("Kilometre {number}").format(number=number)


def _pace_is_time(distance: str | None, sport: int | None) -> bool:
    """Whether a lap shown with this distance has its time for its pace: shown as 1.00 km at
    a pace a kilometre, or as 100 m at a pace of 100 m (a swim)."""
    kind = speed_kind(sport)
    if kind == "pace":
        return distance == activity_distance(1000, sport)
    return kind == "swim pace" and distance == activity_distance(100, sport)


def lap_caption(lap, sport: int | None) -> str:
    """The line of a lap (fit.Lap): its distance, its time (the timer's, the elapsed standing
    in), how fast (its average speed, else worked out; no pace that is its time) and its
    average heart rate; only what it gives."""
    time = lap.timer_time if lap.timer_time is not None else lap.elapsed_time
    speed = lap.avg_speed
    if speed is None and lap.distance and time:
        speed = lap.distance / time
    distance = activity_distance(lap.distance, sport)
    fast = activity_pace(speed, sport) if distance and not _pace_is_time(distance, sport) else None
    parts = [distance, activity_clock(time) if time is not None else None, fast,
             heart_rate_text(lap.avg_heart_rate)]
    return caption(*(part for part in parts if part))


# --- the heart rate zones of an activity ---------------------------------------------------------


def zones_title() -> str:
    # TRANSLATORS: the title of the section of the heart rate zones on the page of an activity
    return _("Heart Rate Zones")


def zone_name(number: int) -> str:
    """A zone by its number (1-5); 0: the time below zone 1."""
    if number == 0:
        # TRANSLATORS: the row of the time below zone 1 of the heart rate
        return _("Below Zone 1")
    # TRANSLATORS: a heart rate zone ("Zone 3")
    return _("Zone {number}").format(number=number)


def zone_range(bounds, number: int) -> str:
    """The heart rates of a zone (0: below zone 1) from the bounds the device keeps
    (fit.HeartRateZones), as Garmin Connect shows them: each bound is the first beat of the
    zone above it. Of made-up bounds: "below 118 bpm", "118–140 bpm", zone 5 with no top:
    "above 182 bpm"."""
    if number == 0:
        # TRANSLATORS: the heart rates below zone 1 (made up: "below 118 bpm")
        return _("below {rate} bpm").format(rate=bounds[0])
    if number == len(bounds) - 1:
        # TRANSLATORS: the heart rates of the top zone, which has no top (made up: "above 182 bpm")
        return _("above {rate} bpm").format(rate=bounds[number - 1] - 1)
    # TRANSLATORS: the heart rates of a zone (made up: "118–140 bpm")
    return _("{low}–{high} bpm").format(low=bounds[number - 1], high=bounds[number] - 1)


def zone_share(seconds: float, total: float) -> str:
    """The time in a zone and its share of the time in all: "36:30 · 88%"."""
    percent = round(seconds / total * 100) if total else 0
    # TRANSLATORS: the time in a heart rate zone and its share ("36:30 · 88%")
    return _("{time} · {percent}%").format(time=activity_clock(seconds), percent=percent)


def zones_method_text(zones) -> str | None:
    """How the device worked out the bounds of the zones (fit.HeartRateZones), and from what;
    None when it does not say, or not the heart rate it says they come from."""
    method = zones.method
    if method == "max" and zones.max_heart_rate:
        # TRANSLATORS: under the title of the heart rate zones ("… (190 bpm)")
        return _("Bounds from the device: % of maximum heart rate ({bpm})").format(
            bpm=heart_rate_text(zones.max_heart_rate))
    if method == "threshold" and zones.threshold_heart_rate:
        # TRANSLATORS: under the title of the heart rate zones ("… (165 bpm)")
        return _("Bounds from the device: % of lactate threshold ({bpm})").format(
            bpm=heart_rate_text(zones.threshold_heart_rate))
    if method == "reserve":
        # TRANSLATORS: under the title of the heart rate zones
        return _("Bounds from the device: % of heart rate reserve")
    if method == "custom":
        # TRANSLATORS: under the title of the heart rate zones: set by the user
        return _("Bounds from the device: your own")
    return None


def effect_title() -> str:
    # TRANSLATORS: the title of the section of the training effect on the page of an activity
    return _("Training Effect")


def effect_name(kind: str) -> str:
    """The name of a training effect: "aerobic" or "anaerobic"."""
    if kind == "aerobic":
        # TRANSLATORS: the aerobic training effect of an activity
        return _("Aerobic")
    # TRANSLATORS: the anaerobic training effect of an activity
    return _("Anaerobic")


def effect_word(value: float) -> str:
    """What a training effect (0.0-5.0) means, by the public scale of Garmin's devices: below
    1 no benefit, from 1 minor benefit, 2 maintaining, 3 improving, 4 highly improving, 5
    overreaching."""
    step = min(5, max(0, round(value * 10) // 10))
    return (
        # TRANSLATORS: a training effect from 0.0 to 0.9. The six words of the scale are
        # Garmin Connect's: use those of its help on the training effect in your language (a
        # device's manual or Garmin's web pages may say them otherwise)
        _("No benefit"),
        # TRANSLATORS: a training effect from 1.0 to 1.9, in Garmin Connect's word
        _("Minor benefit"),
        # TRANSLATORS: a training effect from 2.0 to 2.9, in Garmin Connect's word
        _("Maintaining"),
        # TRANSLATORS: a training effect from 3.0 to 3.9, in Garmin Connect's word
        _("Improving"),
        # TRANSLATORS: a training effect from 4.0 to 4.9, in Garmin Connect's word
        _("Highly improving"),
        # TRANSLATORS: a training effect of 5.0, in Garmin Connect's word
        _("Overreaching"),
    )[step]


def effect_text(value: float) -> str:
    """A training effect with its word: "1.6 · Minor benefit"."""
    return caption(decimal(f"{value:.1f}"), effect_word(value))


def training_load_name() -> str:
    # TRANSLATORS: the training load of an activity (a number)
    return _("Training Load")


def training_load_text(load: float) -> str:
    """The training load as a whole number, a half up: "28"."""
    return str(math.floor(load + 0.5))


def track_colour_text(kind: str) -> str:
    """What the track's colours show, under the map: "Track colour: heart rate"."""
    names = {
        # TRANSLATORS: a series after "Track colour:", as a sentence goes on there (in Polish
        # in lower case: "tętno")
        "heart rate": pgettext("track colour", "heart rate"),
        # TRANSLATORS: a series after "Track colour:" (in Polish "wysokość")
        "elevation": pgettext("track colour", "elevation"),
        # TRANSLATORS: a series after "Track colour:" (in Polish "tempo")
        "pace": pgettext("track colour", "pace"),
        # TRANSLATORS: a series after "Track colour:" (in Polish "prędkość")
        "speed": pgettext("track colour", "speed"),
    }
    # TRANSLATORS: under the map of an activity: which series of the chart colours the track
    return _("Track colour: {series}").format(series=names["pace" if kind == "swim pace"
                                                            else kind])


def colours_text() -> str:
    # TRANSLATORS: beside the switch under the map of an activity that colours its track
    return _("Colours")


# --- the chart of an activity ------------------------------------------------------------------


def chart_series_name(kind: str) -> str:
    """The name of a series of the chart, as its switch says it; "swim pace" is a pace too."""
    names = {
        # TRANSLATORS: the switch above the chart of an activity
        "heart rate": pgettext("activity chart", "Heart rate"),
        "elevation": pgettext("activity chart", "Elevation"),
        "pace": pgettext("activity chart", "Pace"),
        "swim pace": pgettext("activity chart", "Pace"),
        "speed": pgettext("activity chart", "Speed"),
    }
    return names[kind]


def chart_unit(kind: str) -> str:
    """The unit of a series, once on top of the axis of its values."""
    units = {
        # TRANSLATORS: the unit on top of the axis of the chart of an activity
        "heart rate": pgettext("chart unit", "bpm"),
        "elevation": pgettext("chart unit", "m"),
        "pace": pgettext("chart unit", "min/km"),
        "swim pace": pgettext("chart unit", "min/100 m"),
        "speed": pgettext("chart unit", "km/h"),
    }
    return units[kind]


def chart_accessible_name(kind: str) -> str:
    """The chart as a screen reader names it: "Heart rate over time"."""
    # TRANSLATORS: the name of the chart for a screen reader; {series}: e.g. "Heart rate"
    return _("{series} over time").format(series=chart_series_name(kind))


def no_chart_text() -> str:
    """In place of the chart of an activity whose records give none of its series."""
    return _("No heart rate, elevation or speed in this activity")


def chart_tick(kind: str, value: float, step: float) -> str:
    """A value on the axis of a series: a whole number of beats or metres, a speed with the
    decimals of the axis' step, a pace as a clock."""
    if kind in PACES:
        return activity_clock(value)
    if kind == "speed":
        decimals = 0
        while decimals < 3 and abs(step * 10**decimals - round(step * 10**decimals)) > 1e-9:
            decimals += 1
        return decimal(f"{value:.{decimals}f}")
    return str(round(value))


def chart_value_text(kind: str, value: float) -> str:
    """A value of a series where the cursor is: "152 bpm", "412 m", "5:10 /km", "2:05 /100 m",
    "24.3 km/h"."""
    if kind == "heart rate":
        return heart_rate_text(round(value))
    if kind == "elevation":  # as the axis has it: no thousands apart, a hyphen below zero
        return _("{metres} m").format(metres=round(value))
    return _pace_text(kind, value)


def chart_cursor_text(seconds: float, kind: str, value: float | None) -> str:
    """The line beside the chart's switch while its cursor is placed: the time since the start
    and the value there ("31:20 · 152 bpm"), or that there is none (a gap, a pause)."""
    # TRANSLATORS: beside the chart of an activity, where its cursor is on a gap or a pause
    shown = _("no reading") if value is None else chart_value_text(kind, value)
    return caption(activity_clock(seconds), shown)


def satellite_reason(reason: str | None) -> str:
    """Why the satellite data was not updated (satellite.Result.reason), after "not updated: "."""
    from openexpress import satellite

    if reason == satellite.FORMAT:
        # TRANSLATORS: why the satellite data was not updated, after "not updated: "
        return _("Garmin's file is not in the format the device has")
    if reason == satellite.WRITE:
        # TRANSLATORS: why the satellite data was not updated, after "not updated: "
        return _("the file could not be written to the device")
    if reason == satellite.HIDDEN:
        # TRANSLATORS: why the satellite data was not updated, after "not updated: ": the watch
        # keeps them hidden from the file manager and the app cannot open its own connection to
        # the watch here (e.g. a Flatpak without access to USB)
        return _("the device's hidden files cannot be reached here")
    if reason and reason.startswith(satellite.WAIT):
        try:
            until = datetime.fromtimestamp(float(reason[len(satellite.WAIT):]))
        except (ValueError, OverflowError, OSError):
            return ""
        # TRANSLATORS: why the satellite data was not updated, after "not updated: ": Garmin's
        # server answered that it was asked too often; {time} is when it may be asked again,
        # e.g. "14:05"
        return _("Garmin's server asks to wait until {time}").format(time=f"{until:%H:%M}")
    if reason and reason.startswith(satellite.NETWORK):
        return reason[len(satellite.NETWORK):]  # the words of the network error
    return reason or ""


def satellite_stop_reason(why: str | None) -> str:
    """Why the device's satellite data turned itself off (satellite.State.why), for `openexpress
    status` and `sync`: the device did not take a file written."""
    from openexpress import satellite

    if why == satellite.NOT_TAKEN:
        # TRANSLATORS: `openexpress status` and `sync`: the watch did not take the satellite
        # data file written (left it as written after it was unplugged, or renamed it)
        return _("the device did not take the last file")
    if why == satellite.GONE_TWICE:
        # TRANSLATORS: `openexpress status` and `sync`: the satellite data file written was not
        # on the watch twice in a row
        return _("the written file disappeared twice")
    # TRANSLATORS: `openexpress status` and `sync`: the watch renamed the file written to
    # CPE_BAD.bin
    return _("the device rejected the last file (CPE_BAD.bin)")


def satellite_subtitle(state, now: datetime, waits: bool = False) -> str:
    """The subtitle of the row Satellite Data of the device page (satellite.State): off, on
    and what the last sync did, or turned off by the device's rejection of the file. When the
    last sync found the device's data current and newer than the file written last (from the
    phone), it says so rather than how old that file is; `waits`: the file written in this
    connection waits for the device to take it in once unplugged
    (satellite.waits_to_be_taken)."""
    from openexpress import satellite

    if state.stopped and not state.on:
        if state.why == satellite.NOT_TAKEN:
            # TRANSLATORS: subtitle of the row Satellite Data: the watch did not take the file
            # written (left it as written after it was unplugged, or renamed it), so the switch
            # was turned off
            return _("Turned off: the device did not take the last file")
        if state.why == satellite.GONE_TWICE:
            # TRANSLATORS: subtitle of the row Satellite Data: the file written was not on the
            # watch twice in a row, so the switch was turned off
            return _("Turned off: the written file disappeared twice")
        # TRANSLATORS: subtitle of the row Satellite Data: the watch renamed the file written to
        # CPE_BAD.bin, so the switch was turned off
        return _("Turned off: the device rejected the last file (CPE_BAD.bin)")
    # TRANSLATORS: the first part of the subtitle of the row Satellite Data: the feature is
    # experimental (in Polish plural, as "dane satelitarne")
    first = pgettext("satellite data", "Experimental")
    if not state.on:
        # TRANSLATORS: in the subtitle of the row Satellite Data while its switch is off
        return caption(first, _("when on, each sync updates it"))
    if state.error:
        # TRANSLATORS: in the subtitle of the row Satellite Data: the last sync did not update
        # them; {reason} says why
        return caption(first, pgettext("satellite data", "not updated: {reason}").format(
            reason=satellite_reason(state.error)))
    if satellite.shows_device_data(state):
        if state.current:
            # TRANSLATORS: in the subtitle of the row Satellite Data: on, nothing written yet,
            # as the watch's own satellite data (e.g. from the phone) is current
            return caption(first, _("the data on the device is current"))
        # TRANSLATORS: in the subtitle of the row Satellite Data: on, not written yet
        return caption(first, _("the next sync updates it"))
    when = relative_time(datetime.fromtimestamp(state.written_at), now)
    # TRANSLATORS: in the subtitle of the row Satellite Data: when the last sync wrote them,
    # e.g. "updated yesterday, 20:39"
    updated = pgettext("satellite data", "updated {when}").format(when=when)
    if waits:
        # TRANSLATORS: in the subtitle of the row Satellite Data, after "updated just now": the
        # file written waits for the device, which takes it in (unpacks it) once it is
        # unplugged (in Polish plural, as "dane satelitarne")
        taken_in = pgettext("satellite data", "the device takes it in once unplugged")
        return caption(first, updated, taken_in)
    return caption(first, updated)


def crash_logs_not_copied() -> str:
    """Under the summary of the page Device Status while the device's hidden crash logs may hold
    crashes not counted (status.Facts.crash_logs_behind): the row Crash Logs below copies them."""
    # TRANSLATORS: under the summary of the page Device Status of a watch that hides its crash
    # logs; "below" is the row "Crash Logs" with its button "Copy"; one sentence, no full stop
    return _("Crash logs not copied yet may hold more crashes: copy them below")


class Summary(NamedTuple):
    """The summary of the page Device Status."""
    look: str  # "good", "warning", "unknown", or "none" without diagnostic data
    title: str  # the title of the page's summary, header case
    line: str  # the line under it
    row: str  # the same as a sentence: the subtitle of the row that leads to the page


def status_summary(found, errors_known: bool, free: int | None, capacity: int | None,
                   now: datetime, has_diagnostics: bool = True) -> Summary:
    """The summary on top of the page Device Status, and the subtitle of the rows that lead to
    it (the device page, the view of a remembered device). A device without diagnostic data has
    no summary (its page says why): only the words of the row. Else crashes of the last 30 days
    first (the error colour is
    never used: a crash is a fact to know, not an alarm), then little free space
    (status.little_space), then error logs that were not read."""
    from openexpress import crashes, status

    if not has_diagnostics:
        # TRANSLATORS: subtitle of the row Device Status of a device that keeps no error logs or
        # memory dumps openExpress can read (its page says so in a sentence)
        return Summary("none", "", "", _("No diagnostic data"))

    lately = status.recent(found, now)
    if lately:
        n = len(lately)
        # TRANSLATORS: title of the summary of the page Device Status
        title = ngettext("{count} Crash in the Last 30 Days", "{count} Crashes in the Last 30 Days",
                         n).format(count=n)
        # TRANSLATORS: subtitle of the row Device Status: as the title of the page's summary
        # "{count} Crashes in the Last 30 Days", as a sentence
        row = ngettext("{count} crash in the last 30 days", "{count} crashes in the last 30 days",
                       n).format(count=n)
        # TRANSLATORS: in the summary of the page Device Status: the last crash, e.g. "Last one
        # 2 days ago"
        parts = [_("Last one {when}").format(when=relative_time(lately[0].at, now))]
        if n > 1 and all(crashes.same_error(lately[0], c) for c in lately[1:]):
            # TRANSLATORS: in the summary of the page Device Status, after "Last one …"
            parts.append(_("the same error each time"))
        if free is not None:
            parts.append(space_text(free))
        return Summary("warning", title, caption(*parts), row)
    if status.little_space(free, capacity):
        # TRANSLATORS: subtitle of the row Device Status: as the title of the page's summary
        # "Little Free Space", as a sentence
        row = _("Little free space")
        # TRANSLATORS: title of the summary of the page Device Status
        return Summary("warning", _("Little Free Space"), space_text(free, capacity), row)
    if not errors_known:
        # TRANSLATORS: subtitle of the row Device Status: as the title of the page's summary
        # "Error Logs Not Read", as a sentence
        row = _("Error logs not read")
        # TRANSLATORS: title of the summary of the page Device Status: the device's error logs
        # could not be read, so whether it crashed is not known
        return Summary("unknown", _("Error Logs Not Read"), space_text(free, capacity), row)
    # TRANSLATORS: subtitle of the row Device Status: as the title of the page's summary "No
    # Problems Found", as a sentence
    row = _("No problems found")
    # TRANSLATORS: title of the summary of the page Device Status
    return Summary("good", _("No Problems Found"), space_text(free, capacity), row)
