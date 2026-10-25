"""The tiles once and for all (25.10.2026): the facts a tile says in its chips are kept with
the device, one object "tile_facts" of its entry in devices.json, written by one module after
each source (the check of updates, the count in the background, the account's list, a sync, a
detection) and read by one function (tile_facts.read). start.chips() gets only those facts and
what the device itself shows; no session, no account's list, no count held in memory. Each fact
holds for so long and while what it was told against stays the same (an update: 30 days and
the software of the device; a count: 7 days and the sync state; the account: 7 days and the
login saved); past that it is not known: no chip, never the contrary. All devices, unit IDs and
files are made up."""

import json
import sys
import time
import types
from pathlib import Path

import pytest
from conftest import DEVICE_XML, make_fit
from fakes import record_sync

from openexpress import known_devices, service, sync, tile_facts
from openexpress.known_devices import AccountFact, SyncedFact

DAY = 86400.0
NOW = 1_800_000_000.0
FENIX, EDGE, VARIA, VENU = 3000000101, 3000000102, 3000000103, 3000000104
PACKAGE = Path(tile_facts.__file__).parent


def _tile(**fields):
    from openexpress.devicelist import Tile

    values = dict(description="fēnix 8", part_number="006-B4532-00", software_version="23.39",
                  kind="mtp", connected=True, on_account=True, primary=False, last_seen=None,
                  image_part_number="006-B4532-00", image_url=None, category=None,
                  account_known=True)
    values.update(fields)
    return Tile(**values)


def _login(name="made-up"):
    """A login saved, as `openexpress login` saves it (made up)."""
    from openexpress.auth import store

    store.save(store.ConnectAuth("token", "secret", account={"username": name}))
    return store.stamp()


# --- the chips read the facts and the device, nothing else ------------------------------


class _Watched:
    """A tile that notes each of its fields chips() reads."""

    def __init__(self, tile, read):
        self._tile, self._read = tile, read

    def __getattr__(self, name):
        self._read.add(name)
        return getattr(self._tile, name)


# Every branch of chips(): an update copied (of a drive, of another device), one to install,
# the sync (to upload, with a sync and without, never synced, synced), a drive, the primary
# wearable, off the account.
EVERY_KIND = [
    dict(update_ready=NOW, kind="ums"), dict(update_ready=NOW), dict(update_found=NOW),
    dict(to_sync=3, synced_at=NOW - DAY), dict(to_sync=2), dict(never_synced=True),
    dict(to_sync=0, synced_at=NOW), dict(kind="ums"), dict(primary=True),
    dict(on_account=False), dict(on_account=False, account_known=False)]


def test_the_chips_read_only_the_facts_and_what_the_device_shows():
    from openexpress.gui import start

    read = set()
    calls = []

    def profile(frame, event, _arg):
        if event == "call":
            calls.append((frame.f_code.co_filename, frame.f_code.co_name))

    for fields in EVERY_KIND:
        tile = _Watched(_tile(**fields), read)
        sys.setprofile(profile)
        try:
            start.chips(tile, NOW)
        finally:
            sys.setprofile(None)
    assert read <= set(tile_facts.TILE_FIELDS) | set(tile_facts.DEVICE_FIELDS), read
    # Of the program's own code: the chips themselves, their words and the 30 days of an
    # update; nothing that reads a file, a session, the account or a count.
    allowed = {PACKAGE / "gui" / "start.py", PACKAGE / "gui" / "words.py", PACKAGE / "i18n.py"}
    own = [(Path(f), name) for f, name in calls if Path(f).is_relative_to(PACKAGE)]
    assert [(f, name) for f, name in own if f not in allowed] == [
        (PACKAGE / "tile_facts.py", "update_still_recent")] * sum(
        name == "update_still_recent" for _f, name in own)


def test_the_facts_of_a_tile_come_from_the_reader_only(monkeypatch):
    """Whatever the account's list and a count held in memory say: the fields of the chips
    are what tile_facts.read gives for the entry of the device, with what the device shows
    (connected, its USB kind, its own software, else the software remembered)."""
    from openexpress import devicelist
    from openexpress.device.gio_storage import DeviceSummary, FoundDevice

    said = tile_facts.TileFacts(update_found=1.0, update_ready=2.0, to_sync=7,
                                never_synced=True, synced_at=3.0, primary=True,
                                on_account=False)
    asked = []

    def read(entry, **kw):
        asked.append((entry.unit_id, kw))
        return said

    monkeypatch.setattr(tile_facts, "read", read)
    known_devices.remember(FENIX, "fēnix 8", "006-B4532-00", "23.38", "mtp")
    known_devices.remember(EDGE, "Edge 1050", "006-B4440-00", "33.11", "mtp")
    summary = DeviceSummary(FoundDevice("mtp://fenix/", "x", True), "fēnix 8", "006-B4532-00",
                            "23.39", FENIX)
    summary.to_sync = sync.ActivitiesToSync(FENIX, frozenset({"a.fit"}), 1)
    account = [{"unitid": FENIX, "primaryactivitytrackerindicator": False},
               {"unitid": EDGE, "primaryactivitytrackerindicator": False},
               {"unitid": VENU, "primaryactivitytrackerindicator": True,
                "productdisplayname": "Venu 2"}]
    tiles = {t.description: t for t in devicelist.tiles([summary], known_devices.load(),
                                                       account, True)}
    for name in ("fēnix 8", "Edge 1050"):
        fields = {k: getattr(tiles[name], k) for k in tile_facts.TILE_FIELDS}
        assert fields == said.tile_fields()
    assert sorted(asked, key=lambda a: a[0]) == [
        (FENIX, {"connected": True, "kind": "mtp", "software_version": "23.39"}),
        (EDGE, {"connected": False, "kind": "mtp", "software_version": "33.11"})]
    # Never seen here: no entry; its tile is the account's entry, and says whether it is the
    # primary wearable only.
    venu = {k: getattr(tiles["Venu 2"], k) for k in tile_facts.TILE_FIELDS}
    assert venu == tile_facts.of_account_entry(True).tile_fields()


# --- each fact holds for so long, and while what it was told against stays -------------


def _seen(unit=FENIX, software="23.39", kind="mtp"):
    known_devices.seen(unit, "fēnix 8", "006-B4532-00", software, kind)
    return known_devices.load()[unit]


def _read(entry, connected=True, kind="mtp", software="23.39", now=None):
    return tile_facts.read(entry, connected=connected, kind=kind, software_version=software,
                           now=now)


def test_an_update_found_holds_30_days_and_for_the_software_it_was_found_for():
    _seen()
    device = types.SimpleNamespace(unit_id=FENIX, software_version_str="23.39")
    update = types.SimpleNamespace(title="Map Region Grouping file", version="2.31",
                                   is_system=False)
    tile_facts.note_update_check(device, [update])
    entry = known_devices.load()[FENIX]
    found = entry.update_found
    assert _read(entry).update_found == found
    assert _read(entry, now=found + 29 * DAY).update_found == found
    assert _read(entry, now=found + 31 * DAY).update_found is None
    assert _read(entry, software="23.40").update_found is None  # installed since
    assert _read(entry, connected=False).update_found == found  # remembered: as last seen


def test_an_update_found_without_the_software_it_was_found_for_is_not_known():
    """An entry of an older version (no summary, no check): whether it still holds is not
    known."""
    _seen()
    known_devices.set_update_found(FENIX, True)
    assert _read(known_devices.load()[FENIX]).update_found is None


def test_the_account_holds_7_days_and_while_the_login_it_was_read_with_is_saved():
    from openexpress.auth import store

    _seen()
    _login()
    tile_facts.note_account([{"unitid": FENIX, "primaryactivitytrackerindicator": True}])
    entry = known_devices.load()[FENIX]
    at = entry.account.at
    assert (_read(entry).on_account, _read(entry).primary) == (True, True)
    old = _read(entry, now=at + 8 * DAY)
    assert (old.on_account, old.primary) == (None, False)
    _login("another")  # logged in again (another account, maybe): read anew first
    assert _read(entry).on_account is None
    store.delete()
    assert _read(entry).on_account is None


def test_a_fact_that_does_not_hold_says_nothing_never_the_contrary():
    """An old word of the account that the device is off it gives no "Not on your account";
    an old word that it was never synced gives no "Never synced"."""
    from openexpress import devicelist
    from openexpress.gui import start

    stamp = _login()
    known_devices.remember(VENU, "Venu 2", "006-B3703-00", "19.05", "mtp")
    known_devices.set_account_facts({VENU: AccountFact(False, False, time.time() - 8 * DAY,
                                                       stamp)})
    known_devices.set_synced(VENU, SyncedFact(False, None, False, time.time(), None))
    sync.SyncState(VENU).save()  # synced since
    (tile,) = devicelist.tiles([_summary(VENU, "Venu 2", "19.05")], known_devices.load(),
                               None, False)
    assert start.chips(tile) == []


def test_the_sync_holds_while_the_sync_state_is_the_same():
    _login()
    _seen()
    tile_facts.note_account([{"unitid": FENIX}])
    assert _read(known_devices.load()[FENIX]).never_synced
    sync.SyncState(FENIX).save()  # by the command line, say: not seen here
    facts = _read(known_devices.load()[FENIX])
    assert (facts.never_synced, facts.to_sync) == (False, None)


def test_a_count_holds_7_days_and_for_the_sync_state_it_was_counted_against():
    _login()
    sync.SyncState(FENIX).save()
    record_sync(FENIX, uploaded=1, downloaded=0, skipped_known=0, errors=[], lines=[])
    _seen()
    tile_facts.note_account([{"unitid": FENIX}])
    tile_facts.note_count(sync.ActivitiesToSync(FENIX, frozenset({"a.fit"}), 1))
    entry = known_devices.load()[FENIX]
    assert (_read(entry).to_sync, _read(entry).synced_at) == (1, entry.synced.last)
    assert _read(entry, now=time.time() + 8 * DAY).to_sync is None
    assert _read(entry, kind="ums").to_sync is None  # a drive: nothing of the sync
    assert _read(entry, connected=False).to_sync is None  # not connected: neither


# --- one object in devices.json, and the entries written before it -----------------------


def test_the_facts_are_one_object_of_the_entry():
    _login()
    sync.SyncState(FENIX).save()
    record_sync(FENIX, uploaded=1, downloaded=0, skipped_known=0, errors=[], lines=[])
    _seen()
    tile_facts.note_account([{"unitid": FENIX, "primaryactivitytrackerindicator": True}])
    tile_facts.note_count(sync.ActivitiesToSync(FENIX, frozenset(), 0))
    tile_facts.note_update_check(types.SimpleNamespace(unit_id=FENIX, software_version_str="23.39"),
                                 [types.SimpleNamespace(title="T", version="1", is_system=False)])
    kept = json.loads(known_devices.path().read_text())["devices"][str(FENIX)]
    assert sorted(kept) == ["description", "first_seen", "kind", "last_seen", "part_number",
                            "software_version", "tile_facts"]
    facts = kept["tile_facts"]
    assert sorted(facts) == ["account", "sync", "synced", "update"]  # its kind: kept["kind"]
    assert sorted(facts["update"]) == ["check", "found", "summary"]
    assert kept["kind"] == "mtp"
    assert (facts["account"]["on_account"], facts["account"]["primary"]) == (True, True)
    assert (facts["synced"]["once"], facts["sync"]["count"]) == (True, 0)


def test_an_entry_written_before_reads_its_facts_and_one_without_them_has_none():
    _seen()
    data = json.loads(known_devices.path().read_text())
    entry = data["devices"][str(FENIX)]
    del entry["tile_facts"]
    entry.update(update_found=NOW, update_summary={"count": 2, "software_version": "23.39"},
                 update_check={"at": NOW, "software_version": "23.39", "ready": False},
                 sync_count={"count": 3, "at": NOW, "state": "s"})
    data["devices"][str(EDGE)] = {"description": "Edge 1050", "part_number": "006-B4440-00",
                                  "software_version": "33.11", "kind": "mtp",
                                  "first_seen": NOW, "last_seen": NOW}
    known_devices.path().write_text(json.dumps(data))
    fenix, edge = known_devices.load()[FENIX], known_devices.load()[EDGE]
    assert (fenix.update_found, fenix.update_summary.count, fenix.update_check.at,
            fenix.sync_count.count) == (NOW, 2, NOW, 3)
    assert (fenix.account, fenix.synced) == (None, None)  # not known
    assert (edge.update_found, edge.sync_count, edge.account, edge.synced) == (
        None, None, None, None)
    assert _read(edge, software="33.11") == tile_facts.TileFacts()
    known_devices.set_category(EDGE, "cycling-computer")  # written again: in its new place
    kept = json.loads(known_devices.path().read_text())["devices"][str(FENIX)]
    assert "update_found" not in kept and kept["tile_facts"]["update"]["found"] == NOW
    assert kept["tile_facts"]["sync"] == {"count": 3, "at": NOW, "state": "s"}


@pytest.mark.parametrize("damaged", ["x", [1], {"on_account": "yes", "primary": False,
                                                "at": 1.0, "login": "l"},
                                     {"on_account": True, "primary": False, "at": 1.0}])
def test_a_damaged_fact_is_not_known_and_the_rest_stays(damaged):
    _seen()
    data = json.loads(known_devices.path().read_text())
    data["devices"][str(FENIX)]["tile_facts"]["account"] = damaged
    known_devices.path().write_text(json.dumps(data))
    entry = known_devices.load()[FENIX]
    assert entry.account is None and entry.synced is not None


# --- the sources write -------------------------------------------------------------------


def test_the_account_s_list_is_kept_for_the_devices_remembered_only_with_a_login():
    _seen()
    tile_facts.note_account([{"unitid": FENIX}])  # no login saved: nothing
    assert known_devices.load()[FENIX].account is None
    stamp = _login()
    tile_facts.note_account([{"unitid": EDGE}])
    (fact,) = [known_devices.load()[FENIX].account]
    assert (fact.on_account, fact.primary, fact.login) == (False, False, stamp)
    assert EDGE not in known_devices.load()  # not remembered: no entry for it


def test_a_sync_and_a_detection_keep_what_is_known_of_the_sync():
    _seen()
    assert known_devices.load()[FENIX].synced.once is False
    sync.SyncState(FENIX).save()
    record_sync(FENIX, uploaded=1, downloaded=0, skipped_known=0, errors=[], lines=[])
    synced = known_devices.load()[FENIX].synced
    assert synced.once and synced.state == service.sync_stamp(FENIX) and synced.last
    record = service.load_last_sync(FENIX)
    assert synced.last == record["time"]


def test_the_window_keeps_the_account_s_list_before_it_draws_the_tiles():
    from openexpress.gui.app import MainWindow

    _login()
    _seen()
    drawn = []
    win = types.SimpleNamespace(
        _account=None, _account_why=None, _on_start_page=lambda: True,
        _on_remembered_page=lambda: False, in_background=lambda work, done, failed=None: None)
    win._render_start = lambda: drawn.append(known_devices.load()[FENIX].account)
    MainWindow._set_account(win, [{"unitid": FENIX, "primaryactivitytrackerindicator": True}])
    (fact,) = drawn
    assert (fact.on_account, fact.primary) == (True, True)


def test_forget_takes_the_facts_with_the_entry():
    from openexpress import forget

    _login()
    _seen()
    tile_facts.note_account([{"unitid": FENIX}])
    assert forget.forget_device(FENIX)
    assert FENIX not in known_devices.load()


# --- two openings on the same files ------------------------------------------------------


def _summary(unit, name, software, uri=None, kind="mtp"):
    from openexpress.device.gio_storage import DeviceSummary, FoundDevice

    return DeviceSummary(FoundDevice(uri or f"mtp://{unit}/", "x", True, kind=kind), name,
                         "006-B0000-00", software, unit)


DEVICES = {  # unit: (name, part number, software as the file has it)
    FENIX: ("fēnix 8", "006-B4532-00", "2339"),
    EDGE: ("Edge 1050", "006-B4440-00", "3313"),
    VENU: ("Venu 2", "006-B3703-00", "1905"),
    VARIA: ("Varia RTL515", "006-B3592-03", "336"),
}
ACCOUNT = [{"unitid": FENIX, "primaryactivitytrackerindicator": True},
           {"unitid": EDGE, "primaryactivitytrackerindicator": False}]
ACTIVITIES = ("2026-10-01-10-00-00.fit", "2026-10-02-10-00-00.fit")


@pytest.fixture
def four(tmp_path, monkeypatch):
    """The four made-up devices on their files: the fēnix and the Edge synced here before
    with nothing to upload, the Venu 2 off the account, the Varia a drive; a login saved.
    Listed as each opening of the window lists them (probe): a new list each time."""
    from openexpress.device import gio_storage
    from openexpress.device.storage import LocalStorage

    roots = {}
    for unit, (name, part, software) in DEVICES.items():
        root = tmp_path / f"device-{unit}"
        (root / "GARMIN" / "Activity").mkdir(parents=True)
        xml = (DEVICE_XML.replace("1234567890", str(unit)).replace("006-B0000-00", part)
               .replace("Test watch", name).replace("<SoftwareVersion>2339",
                                                    f"<SoftwareVersion>{software}"))
        (root / "GARMIN" / "GarminDevice.xml").write_text(xml)
        for n, activity in enumerate(ACTIVITIES):
            (root / "GARMIN" / "Activity" / activity).write_bytes(make_fit(1100000000 + n))
        roots[unit] = root
    monkeypatch.setattr(gio_storage, "_mtp_storage",
                        lambda uri, usb: LocalStorage(roots[int(uri.split("/")[2])]))
    for unit in (FENIX, EDGE):
        state = sync.SyncState(unit)
        for activity in ACTIVITIES:
            state.mark_completed(sync.ACTIVITY_TYPE, activity)
        state.save()
        record_sync(unit, uploaded=2, downloaded=0, skipped_known=0, errors=[],
                    lines=[])
    _login()

    def listed():
        found = [gio_storage.FoundDevice(f"mtp://{unit}/", "x", True,
                                         usb=gio_storage.GarminUsb("51b4", "usb", 1, n))
                 for n, unit in enumerate((FENIX, EDGE, VENU))]
        found.append(gio_storage.FoundDevice(roots[VARIA].as_uri() + "/", "GARMIN", True,
                                             kind="ums"))
        summaries = [gio_storage.probe(f) for f in found]
        for s in summaries:  # remembered as the window's detection does (_choose)
            known_devices.seen(s.unit_id, s.description, s.part_number, s.software_version,
                               s.found.kind)
        return summaries

    return listed


def _window(summaries):
    """The window's start page (the real one) and its way of drawing it, on these devices;
    work in the background waits in `later`."""
    from openexpress.gui import app, start
    from openexpress.gui.connected import Connected

    later = []
    win = types.SimpleNamespace(
        session=None, start=start.StartPage(None), _account=None, _account_why=None,
        connected=Connected(devices=list(summaries), choosing=True), _start_entries=[],
        _start_texts=[], later=later, _load_tile_pictures=lambda t: None,
        _on_start_page=lambda: True, _on_remembered_page=lambda: False,
        in_background=lambda work, done, failed=None: later.append((work, done)))
    for name in ("_show_tiles", "_start_summaries", "_is_open", "_count_activities",
                 "_activities_counted", "_update_tile_chips", "_set_account", "_render_start"):
        setattr(win, name, types.MethodType(getattr(app.MainWindow, name), win))
    return win


def _run(win):
    while win.later:
        work, done = win.later.pop(0)
        done(work())


def _labels(widget):
    from gi.repository import Gtk

    found = [widget] if isinstance(widget, Gtk.Label) else []
    child = widget.get_first_child()
    while child is not None:
        found += _labels(child)
        child = child.get_next_sibling()
    return found


def _said(win):
    """Each tile drawn: its name and its chips."""
    said = []
    for item in win.start.tiles():
        labels = _labels(item)
        chips = [w.get_label() for w in labels if w.has_css_class("oe-chip") and w.get_label()]
        said.append((labels[0].get_label(), chips))
    return said


@pytest.mark.needs_display
def test_opened_again_the_first_frame_says_what_the_last_one_said(four):
    from openexpress.gui import app

    # The first opening: nothing kept yet.
    win = _window(four())
    win._show_tiles(*win._start_summaries())
    first = _said(win)
    win._set_account(ACCOUNT)  # the account's list comes
    _run(win)  # the counts of the devices of the account
    fenix = next(s for s in win.connected.devices if s.unit_id == FENIX)
    app.remember_update_found(fenix.device, [types.SimpleNamespace(
        title="Map Region Grouping file", version="2.31", is_system=False)])
    win._render_start()  # back from the fēnix's page, which checked its updates
    last = _said(win)
    assert dict(last) == {"fēnix 8": ["Update available", "Primary"],
                          "Edge 1050": ["Synced"], "Venu 2": ["Not on your account"],
                          "Varia RTL515": ["USB drive"]}
    assert first != last

    # The second: on the same files, listed anew.
    win = _window(four())
    set_chips = []
    real = win.start.set_chips
    win.start.set_chips = lambda key, tile: (set_chips.append(key), real(key, tile))[1]
    win._show_tiles(*win._start_summaries())
    assert _said(win) == last  # the first frame
    items = win.start.tiles()
    win._set_account(ACCOUNT)
    _run(win)
    assert _said(win) == last and set_chips == []  # nothing changed: nothing drawn
    assert win.start.tiles() == items  # the same tiles, none built again
