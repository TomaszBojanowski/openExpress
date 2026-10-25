"""The window's words for what it knows of a device, one function for each fact (analysis of
25.10.2026): every page that says a fact says it in these words. Without GTK, so that a test
or the command line can use them too."""
from __future__ import annotations

from datetime import datetime

from openexpress import known_devices
from openexpress.i18n import _, caption, full_time, human_size, ngettext, pgettext, relative_time


def updates_available(count: int, only=None) -> str:
    """Updates to install, `count` of them: of one (`only`), the version of a system update
    or the name and version of another, as the Updates page lists it; of several (or one not
    known), how many."""
    if count == 1 and only is not None and only.is_system:
        # TRANSLATORS: the one update to install is of the device's system software (the row
        # "Device software" and the top of the Updates page)
        return _("Version {version} available").format(version=only.version)
    if count == 1 and only is not None:
        # TRANSLATORS: the one update to install, of another part of the device (its name and
        # version, as the server gives them)
        return _("Update available: {name} {version}").format(name=only.title,
                                                               version=only.version)
    return ngettext("{count} update available", "{count} updates available",
                    count).format(count=count)


def update_state_text(state: str | None, count: int = 0, only=None,
                      mass_storage: bool = False) -> str:
    """The state of the updates of the device in one text, for the row "Device software" and
    the top of the Updates page alike: "available" (updates_available), "ready" (copied, it
    waits for the device to be disconnected, or ejected: `mass_storage`; it wins over the
    `count` updates still to install, told after it), "current", "failed", or not known yet
    (None). The chips say it short ("Update available", "Update ready")."""
    if state == "available":
        return updates_available(count, only)
    if state == "ready":
        ready = (_("Update ready: eject the device to install it") if mass_storage
                 else _("Update ready: disconnect the device to install it"))
        # TRANSLATORS: after "Update ready: disconnect the device to install it": updates
        # still to install besides the one copied already
        more = ngettext("{count} more available", "{count} more available", count).format(
            count=count) if count else ""
        return caption(ready, more)
    if state == "current":
        # TRANSLATORS: the state of the updates, without a full stop
        return _("Software is up to date")
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
    """The row "Device software" of a device not connected (its KnownDevice; None: not
    remembered here), from the last check of updates kept with it: what it found and when,
    while it is recent and of the software the device had when it was last seen
    (known_devices.recorded_updates, recorded_check); "Not checked since …" for an older
    check; "Connect the device to check" without one, or when the last check found an update
    copied already (the device installs it once unplugged, which it has been since)."""
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
            return connect
        if entry.update_found is None:
            return checked_text(_("Software is up to date"), check.at, now)
    last = entry.update_check.at if entry.update_check is not None else entry.update_found
    return not_checked_since(last) if last is not None else connect


def software_text(version: str, from_account: bool = False) -> str:
    """The version of the device's software, the same words everywhere: "Software 23.39";
    "Software 23.39 (Garmin Connect)" when only the account says it (it may be older than
    what the device runs)."""
    if from_account:
        # TRANSLATORS: the version of a device that only the Garmin account knows of
        return _("Software {version} (Garmin Connect)").format(version=version)
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
    return _("{size} free").format(size=human_size(free))


def last_seen_text(last_seen: float, now: datetime | None = None) -> str:
    """When this computer last saw a device, told against now, as the last sync is ("Last
    seen 2 h ago"): on its tile and on its page alike; the full date is in the tooltip
    (last_seen_tooltip)."""
    return _("Last seen {when}").format(
        when=relative_time(datetime.fromtimestamp(last_seen), now or datetime.now()))


def last_seen_tooltip(last_seen: float) -> str:
    """The tooltip of last_seen_text: when, in full."""
    return _("Last seen {when}").format(when=full_time(datetime.fromtimestamp(last_seen)))
