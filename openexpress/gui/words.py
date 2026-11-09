"""The window's words for what it knows of a device, one function for each fact: every page
that says a fact says it in these words. Without GTK, so that a test or the command line can
use them too."""
from __future__ import annotations

import math
from datetime import datetime

from openexpress import known_devices, service
from openexpress.i18n import (
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


def not_installed_text(u, device) -> str:
    """Below an update the device has not installed although it was unplugged since openExpress
    copied it (updates.not_installed): when it was copied, what the device did with the file,
    and the version it still reports, when that is known (versions_text)."""
    from openexpress import updates as upd

    when = full_time(datetime.fromtimestamp(u.copied_at))
    old = u.installed_version or upd.the_device_version(u, device)
    if u.changed_on_device:
        if old:
            # TRANSLATORS: below an update the device has not installed although it was unplugged
            # since: {when} when it was copied, {old} the version the device still reports
            return _("Copied {when}. The device was unplugged since, changed or refused the "
                     "file and still reports version {old}.").format(when=when, old=old)
        return _("Copied {when}. The device was unplugged since, changed or refused the file "
                 "and has not installed it.").format(when=when)
    if old:
        return _("Copied {when}. The device was unplugged since and still reports version "
                 "{old}; the file is still on it, unchanged.").format(when=when, old=old)
    return _("Copied {when}. The device was unplugged since and has not installed it; the "
             "file is still on it, unchanged.").format(when=when)


def not_installed_advice() -> str:
    """What to do about an update the device has not installed (not_installed_text)."""
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
        # TRANSLATORS: after "Update ready: unplug the device to install it": updates
        # still to install besides the one copied already
        more = ngettext("{count} more available", "{count} more available", count).format(
            count=count) if count else ""
        return caption(ready, more)
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
    at = now.timestamp()
    found = known_devices.recorded_updates(entry, entry.software_version, at)
    if found is not None:
        only = found if found.count == 1 and found.title is not None else None
        return checked_text(updates_available(found.count, only), entry.update_found, now)
    check = known_devices.recorded_check(entry, entry.software_version, at)
    if check is not None:
        if check.ready:
            # The device installs it once unplugged, which it has been since.
            return connect
        if check.not_installed:
            return checked_text(update_state_text("not-installed"), check.at, now)
        if entry.update_found is None:
            return checked_text(_("Software is up to date"), check.at, now)
    last = entry.update_check.at if entry.update_check is not None else entry.update_found
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


def primary_wearable_text(wearable) -> str:
    """The subtitle of "Primary Wearable" (devicelist.PrimaryWearable), in the words of
    "Primary Training Device"."""
    if not wearable.set:
        return _("Not set")
    if wearable.is_this_device:
        return _("This device")
    return wearable.name or _("Another device")


def primary_wearable_tooltip() -> str:
    """The tooltip of the chip "Primary" of a tile: what it is, in Garmin Connect's name."""
    # TRANSLATORS: "Primary wearable" is the name Garmin Connect gives the setting (in Polish
    # "Podstawowe urządzenie typu wearable")
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
    is read, "Unknown" when the device does not say."""
    if reading:
        # TRANSLATORS: the free space of the device while it is read
        return _("Reading…")
    if free is None:
        # TRANSLATORS: the free space of the device when it does not say
        return pgettext("space", "Unknown")
    if total:
        return _("{free} free of {total}").format(free=human_size(free), total=human_size(total))
    # TRANSLATORS: free space on the device when its capacity is not known; {size}: e.g. "3.2 GB"
    return _("{size} free").format(size=human_size(free))


def free_after_text(free: int | None, needed: int, change: int, doing: str) -> str:
    """The line of a question before a map is installed, updated or copied ("install",
    "update", "copy") about the free space: what is left once it is done (`change`: how much
    less), or what is missing for the `needed`, or that the device does not say."""
    if free is None:
        # TRANSLATORS: line of the question before a map is installed or copied, when the device
        # does not say how much space is free
        return _("Free space on the device: unknown")
    if needed > free:
        # TRANSLATORS: line of the question before a map is installed or copied: {size} more
        # would have to be free
        return _("Not enough space on the device: {size} missing").format(
            size=human_size(needed - free))
    after = {
        # TRANSLATORS: line of the question before a map is installed: the space free once it
        # is installed
        "install": _("Free space after installing: {free}"),
        # TRANSLATORS: line of the question before a map is updated: the space free once the
        # new files are written and the old ones removed
        "update": _("Free space after updating: {free}"),
        # TRANSLATORS: line of the question before a map of your own is copied to the device:
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
        # TRANSLATORS: a banner of the window; {names}: the names of the devices, e.g.
        # "fēnix 8" or "fēnix 8, Edge 1050"
        return ngettext(
            "The device {names} stopped responding. Unplug it and plug it in again.",
            "The devices {names} stopped responding. Unplug them and plug them in again.",
            count).format(names=", ".join(names))
    # TRANSLATORS: the same banner, when the name of a device is not known
    return ngettext("The device stopped responding. Unplug it and plug it in again.",
                    "The devices stopped responding. Unplug them and plug them in again.",
                    count)


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
