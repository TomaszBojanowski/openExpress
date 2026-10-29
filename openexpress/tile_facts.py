"""The facts the tile of a device says (its chips), kept with the device: one object for each
device in devices.json, "tile_facts" of its entry (known_devices), and the one way they are
written and read.

The facts: what the last check of updates found (an update to install, one copied already,
when it was checked, the software of the device then); the last count of its activities to
upload (how many, the version of its sync state then); whether this installation synced it
and when it last did (and whether that sync could not upload); what the account's list said of
it (on the account, its primary wearable); how it is connected over USB (the entry's kind).

Written by this module only, after each source: the window's check of updates
(note_update_check), the count in the background (note_count), the account's list
(note_account), a sync (note_synced, from service.save_last_sync) and a detection
(known_devices.seen, with synced_fact); forgetting the device removes its entry, and its
facts with it (known_devices.forget).

Read by read() only: the tile is drawn from what it gives and from what the device itself
shows (connected or not, its name, its software, its USB kind). No session, no account's list,
no count held in memory. A device only on the account (never seen here) has no entry: its
tile is the account's entry itself, and says only whether it is the primary wearable
(of_account_entry).

Each fact holds for so long, and only while what it was told against stays the same; past
that it is not known: no chip, never the contrary of the fact.
- an update found or copied: UPDATE_KEPT_S (30 days), and the same software of the device;
- a count: known_devices.SYNC_COUNT_KEPT_S (7 days), and the same sync state (service.sync_stamp);
- the sync: the same sync state;
- the account: ACCOUNT_KEPT_S (7 days), and the same login saved (store.stamp)."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from openexpress import known_devices, service, sync
from openexpress.auth import store
from openexpress.known_devices import AccountFact, KnownDevice, SyncCount, SyncedFact

log = logging.getLogger(__name__)

UPDATE_KEPT_S = known_devices.UPDATE_FOUND_KEPT_S
ACCOUNT_KEPT_S = 7 * 86400

# The fields of a tile (devicelist.Tile) that come from read(), and what the chips of a tile
# may read of the device itself besides: nothing else (tests/test_tile_facts.py).
TILE_FIELDS = ("update_found", "update_ready", "to_sync", "never_synced", "synced_at",
               "primary", "on_account", "account_known")
DEVICE_FIELDS = ("connected", "kind")


@dataclass(frozen=True)
class TileFacts:
    """The facts of one tile that hold now. on_account None: not known (nor whether it is
    the primary wearable)."""

    update_found: float | None = None  # when the check found an update to install
    update_ready: float | None = None  # when it found one copied already (connected only)
    to_sync: int | None = None  # activities to upload (connected, not a drive)
    never_synced: bool = False
    synced_at: float | None = None  # its last sync, with to_sync
    primary: bool = False
    on_account: bool | None = None

    def tile_fields(self) -> dict:
        """As the fields of devicelist.Tile (TILE_FIELDS)."""
        return {"update_found": self.update_found, "update_ready": self.update_ready,
                "to_sync": self.to_sync, "never_synced": self.never_synced,
                "synced_at": self.synced_at, "primary": self.primary,
                "on_account": self.on_account is True,
                "account_known": self.on_account is not None}


# --- reading ------------------------------------------------------------------------------


def read(entry: KnownDevice | None, *, connected: bool, kind: str | None,
         software_version: str, now: float | None = None) -> TileFacts:
    """The facts of the device of `entry` that hold now, for its tile: `connected` (an
    update copied already and the sync are said of a connected device only), its USB `kind`
    ("ums": nothing of the sync), and the software it has now (`software_version`: the
    device's own when connected, else as remembered). No entry: nothing known."""
    if entry is None:
        return TileFacts()
    now = time.time() if now is None else now
    login = store.stamp()
    account = _account(entry, login, now)
    facts = TileFacts(
        update_found=_update_found(entry, software_version, now),
        update_ready=_update_ready(entry, software_version, now) if connected else None,
        primary=bool(account is not None and account.on_account and account.primary),
        on_account=account.on_account if account is not None else None,
        **(_sync(entry, account, login, now) if connected and kind != "ums" else {}))
    if log.isEnabledFor(logging.DEBUG):
        log.debug("start page: facts of %s: %s", entry.part_number, ", ".join(
            f"{k} {v}" for k, v in vars(facts).items() if v not in (None, False)) or "none")
    return facts


def of_account_entry(primary: bool) -> TileFacts:
    """The facts of a device only on the account (no entry here): what the account's entry
    says now, whether it is its primary wearable."""
    return TileFacts(primary=primary, on_account=True)


def update_still_recent(found: float | None, now: float | None = None) -> float | None:
    """When an update was found, if that was less than UPDATE_KEPT_S ago; else None (for the
    chips, which are drawn again later than read)."""
    if found is None:
        return None
    return found if (time.time() if now is None else now) - found < UPDATE_KEPT_S else None


def _update_found(entry: KnownDevice, software_version: str, now: float) -> float | None:
    found = update_still_recent(entry.update_found, now)
    if found is None:
        return None
    told = (entry.update_summary.software_version if entry.update_summary is not None
            else entry.update_check.software_version if entry.update_check is not None
            else None)
    return found if told is not None and told == software_version else None


def _update_ready(entry: KnownDevice, software_version: str, now: float) -> float | None:
    check = (known_devices.recorded_check(entry, software_version, now)
             if software_version else None)
    return check.at if check is not None and check.ready else None


def _account(entry: KnownDevice, login: str | None, now: float) -> AccountFact | None:
    fact = entry.account
    if fact is None or login is None or fact.login != login:
        return None
    return fact if now - fact.at < ACCOUNT_KEPT_S else None


def _sync(entry: KnownDevice, account: AccountFact | None, login: str | None,
          now: float) -> dict:
    """What a connected device's tile says of its sync: for a device of the account (or
    while whose it is is not known, with a login saved); never synced here only when the
    account has it; nothing while its last sync could not upload (no consent, device sync
    off in Garmin Connect): a sync would not help, the device page says why."""
    on = account.on_account if account is not None else None
    if on is False or (on is None and login is None):
        return {}
    state = service.sync_stamp(entry.unit_id)
    synced = entry.synced
    if synced is None or synced.state != state:
        return {}
    if not synced.once:
        return {"never_synced": True} if on else {}
    if synced.not_uploaded:
        return {}
    count = known_devices.recorded_sync_count(entry, state, now)
    if count is None:
        return {}
    return {"to_sync": count, "synced_at": synced.last}


# --- writing ------------------------------------------------------------------------------


def note_update_check(device, new_updates: list, staged: list = ()) -> None:
    """The window's check of updates answered for the open device: whether it found any to
    install, and what (update_summary; none clears both); the check itself (its time, the
    device's software, whether an update was copied already: `staged`). Optional: a failure
    is only written to the log."""
    unit_id = getattr(device, "unit_id", None)
    if not unit_id:
        return
    software = getattr(device, "software_version_str", None)
    try:
        known_devices.set_update_found(unit_id, bool(new_updates),
                                       summary=update_summary(device, new_updates),
                                       software_version=software if isinstance(software, str)
                                       else None, ready=bool(staged))
    except Exception as e:  # noqa: BLE001 - optional; the error may name the folder
        log.warning("devices: what the check of updates found not kept (%s)", type(e).__name__)


def update_summary(device, new_updates: list) -> known_devices.UpdateSummary | None:
    """What is kept of the updates found: how many; of one, its name, version and whether
    it is of the system; and the device's software now. Nothing else (no path,
    no sum, no address). None without updates, or when the device's software is not known."""
    software = getattr(device, "software_version_str", None)
    if not new_updates or not isinstance(software, str):
        return None
    if len(new_updates) == 1:
        u = new_updates[0]
        title, version = getattr(u, "title", None), getattr(u, "version", None)
        is_system = getattr(u, "is_system", None)
        if (isinstance(title, str) and isinstance(version, str)
                and isinstance(is_system, bool)):
            return known_devices.UpdateSummary(1, software, title, version, is_system)
    return known_devices.UpdateSummary(len(new_updates), software)


def note_count(counted) -> None:
    """What a count of the activities to upload found (sync.ActivitiesToSync), with the
    version of the device's sync state now; a count not known (None, never synced) keeps
    what was there. Optional: a failure is only written to the log."""
    if counted is None or counted.count is None:
        return
    stamp = service.sync_stamp(counted.unit_id)
    if stamp is None:
        return
    try:
        known_devices.set_sync_count(counted.unit_id, SyncCount(counted.count, time.time(),
                                                                stamp))
    except Exception as e:  # noqa: BLE001 - optional; the error may name the folder
        log.warning("devices: the count of activities not kept (%s)", type(e).__name__)


def account_facts(devices, units, at: float, login: str) -> dict[int, AccountFact]:
    """What the account's list (keys in lower case) says of each of the devices `units`
    (unit IDs): on it or not, and whether it is its primary wearable
    (primaryActivityTrackerIndicator)."""
    listed: dict[int, dict] = {}
    for item in devices or []:
        if isinstance(item, dict):
            unit_id = service.unit_id_of(item.get("unitid"))
            if unit_id is not None:
                listed.setdefault(unit_id, item)
    return {unit_id: AccountFact(unit_id in listed,
                                 listed.get(unit_id, {}).get(
                                     "primaryactivitytrackerindicator") is True, at, login)
            for unit_id in units}


def note_account(devices) -> None:
    """The account's list was read (`devices`): what it says of each device remembered here,
    with the login saved now. Nothing without a login saved. Optional: a failure is only
    written to the log."""
    login = store.stamp()
    if devices is None or login is None:
        return
    try:
        facts = account_facts(devices, known_devices.load(), time.time(), login)
        changed = known_devices.set_account_facts(facts)
    except Exception as e:  # noqa: BLE001 - optional; the error may name the folder
        log.warning("devices: what the account says of them not kept (%s)", type(e).__name__)
        return
    log.info("devices: the account's list kept for %d of %d remembered", changed, len(facts))


def synced_fact(unit_id: int, now: float | None = None) -> SyncedFact:
    """What this installation knows of the sync of a device now: its state.json, its last
    sync recorded (when; whether it could not upload) and the version of its sync state."""
    record = service.load_last_sync(unit_id)
    when = record.get("time") if record is not None else None
    return SyncedFact(sync.synced_here(unit_id), when,
                      record is not None and service.not_uploaded(record) is not None,
                      time.time() if now is None else now, service.sync_stamp(unit_id))


def note_synced(unit_id: int | None) -> None:
    """A device was synced (or its files marked as uploaded): what is known of its sync
    now. Optional: a failure is only written to the log."""
    if not unit_id:
        return
    try:
        known_devices.set_synced(unit_id, synced_fact(unit_id))
    except Exception as e:  # noqa: BLE001 - optional; the error may name the folder
        log.warning("devices: what is known of its sync not kept (%s)", type(e).__name__)
