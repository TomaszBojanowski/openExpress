import os
import shutil
import types
import uuid
from datetime import datetime
from pathlib import Path

import pytest
from test_ciq_music import FACE, IQ_XML_TYPES, PRELOADED, STORE_ID, FakeClient, out_bin
from test_updates_maps import maps_response, server, sw_response  # noqa: F401

from openexpress import backup, ciq, maps, music, proto, updates
from openexpress.device.storage import DeviceError, LocalStorage
from openexpress.device.xmldevice import parse


@pytest.fixture
def iq_device(device_root):
    xml_path = device_root / "GARMIN/GarminDevice.xml"
    xml_path.write_text(xml_path.read_text().replace("</MassStorageMode>",
                                                     IQ_XML_TYPES + "</MassStorageMode>"))
    (device_root / "GARMIN/APPS/OUT.BIN").write_bytes(out_bin([
        (STORE_ID, 1, "Connect IQ Store", False, "DADE7E4"),
        (PRELOADED, 8, "Softball", True, ""),
    ]))
    st = LocalStorage(device_root)
    return st, parse(st.read("GARMIN/GarminDevice.xml"))


def iq_message(mid, app_id, name):
    return {"messageid": mid, "devicexmldatatype": "IQWatchFaces",
            "metadata": {"messageurl": "x", "messagename": name, "appdetails": {"id": str(app_id)}}}


# --- 1. backup ------------------------------------------------------------------------


def test_backup_retention_ignores_foreign_dirs_and_cleans_partial(device_root, tmp_path):
    st = LocalStorage(device_root)
    dev = parse(st.read("GARMIN/GarminDevice.xml"))
    unit = tmp_path / "bk" / str(dev.unit_id)
    unit.mkdir(parents=True)
    old = [unit / f"2020-01-0{i} (10.00.00)" for i in (1, 2, 3)]
    for p in old:
        p.mkdir()
    (unit / "zzz notes").mkdir()
    (unit / ".partial-2020-01-04 (10.00.00)").mkdir()
    res = backup.run_backup(dev, st, tmp_path / "bk", keep=2)
    assert res.path.is_dir() and (res.path / "GarminDevice.xml").exists()
    assert sorted(res.removed) == old[:2]
    assert (unit / "zzz notes").is_dir() and old[2].is_dir()
    assert not list(unit.glob(".partial-*"))
    datetime.strptime(res.path.name, backup.STAMP)


def test_backup_failure_removes_partial(device_root, tmp_path):
    class Broken(LocalStorage):
        def _read(self, real_path):
            raise DeviceError("unplugged")

    st = Broken(device_root)
    dev = parse((device_root / "GARMIN/GarminDevice.xml").read_bytes())
    with pytest.raises(DeviceError):
        backup.run_backup(dev, st, tmp_path / "bk", keep=1)
    assert list((tmp_path / "bk" / str(dev.unit_id)).iterdir()) == []


# --- names from GarminDevice.xml never lead out of the backup (review of 106cd25) ------------


def _files_under(root: Path) -> set[Path]:
    return {p.resolve() for p in root.rglob("*") if p.is_file()}


def _with_type(dev, name):
    """The device with the first data type of the backup renamed (a crafted XML)."""
    import dataclasses

    first = backup.backup_files(dev)[0]
    dev.files = [dataclasses.replace(f, datatype=name) if f is first else f for f in dev.files]
    return dev


def _nothing_outside(tmp_path, before, target):
    """No file came to be anywhere but under the backup folder (resolved paths)."""
    target = target.resolve()
    new = _files_under(tmp_path) - before
    assert [p for p in new if not p.is_relative_to(target)] == []


@pytest.mark.parametrize("name", ["../../escape", "ABSOLUTE", "", ".", "..", "a/b"])
def test_a_crafted_data_type_name_is_refused_and_writes_nothing(device_root, tmp_path, name):
    """A data type whose name is no folder name (with "..", absolute, empty, with a
    separator) refuses the whole backup, as other errors of GarminDevice.xml do: nothing is
    written outside the backup folder, and no partial backup is left."""
    if name == "ABSOLUTE":
        name = str(tmp_path / "outside")  # absolute, but inside the test's own folder
    st = LocalStorage(device_root)
    dev = _with_type(parse((device_root / "GARMIN/GarminDevice.xml").read_bytes()), name)
    before = _files_under(tmp_path)
    with pytest.raises(DeviceError, match="cannot be used as a path"):
        backup.run_backup(dev, st, tmp_path / "bk", keep=1)
    unit = tmp_path / "bk" / str(dev.unit_id)
    _nothing_outside(tmp_path, before, unit)
    assert list(unit.iterdir()) == []


def test_a_crafted_file_name_from_the_device_is_refused(device_root, tmp_path):
    """A file the device lists under a name with ".." (a device may list any name over
    MTP): refused the same way."""
    class Crafted(LocalStorage):
        def walk_files(self, path):
            files = super().walk_files(path)
            if not files:
                return files
            real, entry = files[0]
            return [(real.rsplit("/", 1)[0] + "/../../../escape.fit", entry), *files[1:]]

        def read(self, path):
            return b"x" if path.endswith("escape.fit") else super().read(path)

    st = Crafted(device_root)
    dev = parse((device_root / "GARMIN/GarminDevice.xml").read_bytes())
    before = _files_under(tmp_path)
    with pytest.raises(DeviceError, match="cannot be used as a path"):
        backup.run_backup(dev, st, tmp_path / "bk", keep=1)
    _nothing_outside(tmp_path, before, tmp_path / "bk" / str(dev.unit_id))


def test_a_plain_backup_stays_inside_its_folder(device_root, tmp_path):
    """Guards: an ordinary backup writes only below its own folder."""
    st = LocalStorage(device_root)
    dev = parse((device_root / "GARMIN/GarminDevice.xml").read_bytes())
    before = _files_under(tmp_path)
    res = backup.run_backup(dev, st, tmp_path / "bk", keep=1)
    assert res.files > 1
    _nothing_outside(tmp_path, before, res.path)


# --- 2/3/9. Connect IQ ----------------------------------------------------------------


def test_in_bin_only_apps_are_kept(iq_device):
    st, dev = iq_device
    pending = ciq.InstalledApp(FACE, 3, "", disabled=True)
    state = ciq.parse_out_bin(st.read("GARMIN/APPS/OUT.BIN"))
    st.write("GARMIN/APPS/IN.BIN", ciq.build_in_bin(state.apps + [pending]))
    state = ciq.read_installed(dev, st)
    app = state.find(FACE)
    assert app is not None and app.pending and app.disabled and app.app_type == 3
    assert app in state.store_apps
    ciq.set_enabled(dev, st, PRELOADED, True)
    ids = [p[0] for p in ciq.parse_in_bin(st.read("GARMIN/APPS/IN.BIN"))]
    assert ids == [STORE_ID, PRELOADED, FACE]


def test_pending_apps_count_in_slot_check(iq_device):
    st, dev = iq_device
    dev.iq_max_apps = 2  # Store app + one pending app
    st.write("GARMIN/APPS/IN.BIN", ciq.build_in_bin([ciq.InstalledApp(FACE, 3, "", False)]))
    other = uuid.uuid4()
    rep = ciq.install_queued(dev, st, FakeClient(), [iq_message(1, other, "X")], known={})
    assert rep.installed == [] and rep.failed[0][0] == "X"


def app_list(slots, apps, pending=()):
    """What `read_installed` gives: the device reports `slots` free slots and lists `apps`;
    `pending` wait in IN.BIN and are not in OUT.BIN yet."""
    state = ciq.InstalledState(available_slots=slots, available_space=66_000_000)
    state.apps = [ciq.InstalledApp(app_id, 1, "app", False) for app_id in apps]
    state.apps += [ciq.InstalledApp(app_id, 1, "app", False, pending=True) for app_id in pending]
    return state


def limit(max_apps):
    return types.SimpleNamespace(iq_max_apps=max_apps)


def test_free_slots_come_from_the_device():
    # Like a Venu 2: one slot is taken by something the app list does not name.
    assert ciq._free_slots(limit(32), app_list(31, [PRELOADED])) == 31
    assert ciq._free_slots(limit(32), app_list(0, [PRELOADED])) == 0
    # No number in the app list: the declared limit less the store apps listed.
    assert ciq._free_slots(limit(32), app_list(None, [STORE_ID, PRELOADED])) == 31
    assert ciq._free_slots(limit(None), app_list(None, [STORE_ID])) is None
    assert ciq._free_slots(limit(None), app_list(5, [STORE_ID])) == 5


def test_free_slots_count_a_waiting_app_once():
    # Written earlier, not yet in OUT.BIN: the device's number does not count it.
    assert ciq._free_slots(limit(32), app_list(31, [PRELOADED], pending=[FACE])) == 30
    # The device took it: it is in OUT.BIN and in the device's number, not counted again.
    assert ciq._free_slots(limit(32), app_list(30, [PRELOADED, FACE])) == 30
    # Never below zero.
    assert ciq._free_slots(limit(32), app_list(0, [PRELOADED], pending=[FACE])) == 0


@pytest.mark.parametrize("waiting", range(4))
def test_free_slots_on_a_device_that_lists_all_its_store_apps(waiting):
    # Like a fēnix 8: 31 free slots of 32, the one taken is the store app in the list. The
    # result is what the count of listed store apps gave before.
    pending = [uuid.uuid4() for _ in range(waiting)]
    state = app_list(31, [STORE_ID, PRELOADED], pending)
    assert ciq._free_slots(limit(32), state) == 32 - len(state.store_apps) == 31 - waiting


def install_with(monkeypatch, iq_device, state, names):
    st, dev = iq_device
    monkeypatch.setattr(ciq, "read_installed", lambda device, storage: state)
    messages = [iq_message(n, uuid.uuid4(), name) for n, name in enumerate(names, 1)]
    return ciq.install_queued(dev, st, FakeClient(), messages, known={})


def test_install_refuses_when_the_device_reports_no_free_slot(iq_device, monkeypatch):
    st, _dev = iq_device
    before = sorted(p.name for p in st.root.rglob("*"))
    # No store app in the list, and still full.
    rep = install_with(monkeypatch, iq_device, app_list(0, [PRELOADED]), ["X"])
    assert rep.installed == [] and rep.failed == [("X", "no free app slots on the device")]
    assert sorted(p.name for p in st.root.rglob("*")) == before


def test_install_stops_at_the_last_free_slot(iq_device, monkeypatch):
    rep = install_with(monkeypatch, iq_device, app_list(1, [PRELOADED]), ["A", "B"])
    assert rep.installed == ["A"]
    assert rep.failed == [("B", "no free app slots on the device")]


def test_install_counts_an_app_that_waits_for_the_device(iq_device, monkeypatch):
    rep = install_with(monkeypatch, iq_device, app_list(1, [PRELOADED], pending=[FACE]), ["A"])
    assert rep.installed == [] and rep.failed == [("A", "no free app slots on the device")]
    # The same app, taken by the device: its slot is in the device's number already.
    rep = install_with(monkeypatch, iq_device, app_list(1, [PRELOADED, FACE]), ["A"])
    assert rep.installed == ["A"] and rep.failed == []


class FailingAckClient(FakeClient):
    def ack_message(self, mid):
        if mid == 2:
            raise RuntimeError("network")
        super().ack_message(mid)


def test_install_queued_acks_after_in_bin_and_reports_ack_failure(iq_device):
    st, dev = iq_device
    a, b = uuid.uuid4(), uuid.uuid4()
    client = FailingAckClient()
    rep = ciq.install_queued(dev, st, client, [iq_message(1, a, "A"), iq_message(2, b, "B")],
                             known={})
    assert rep.installed == ["A"] and [n for n, _e in rep.failed] == ["B"]
    assert client.acks == [1]
    ids = [p[0] for p in ciq.parse_in_bin(st.read("GARMIN/APPS/IN.BIN"))]
    assert ids[-2:] == [a, b]  # B is installed even though the ack failed


def test_install_queued_registers_written_apps_on_crash(iq_device):
    st, dev = iq_device
    a = uuid.uuid4()

    class Crash(FakeClient):
        def download_message(self, url, absolute):
            if url == "boom":
                raise ciq.ApiError("server down")
            return b"PRG-DATA"

    client = Crash()
    msgs = [iq_message(1, a, "A"), iq_message(2, uuid.uuid4(), "B")]
    msgs[1]["metadata"]["messageurl"] = "boom"
    with pytest.raises(ciq.ApiError):
        ciq.install_queued(dev, st, client, msgs, known={})
    assert a in [p[0] for p in ciq.parse_in_bin(st.read("GARMIN/APPS/IN.BIN"))]
    assert client.acks == [1]


def test_install_queued_write_failure_removes_orphan(iq_device):
    st, dev = iq_device

    class ShortWrite(LocalStorage):
        def _write(self, real_path, data):
            super()._write(real_path, data[:-1] if real_path.endswith(".PRG") else data)

    st = ShortWrite(st.root)
    client = FakeClient()
    rep = ciq.install_queued(dev, st, client, [iq_message(1, FACE, "Face")], known={})
    assert rep.installed == [] and rep.failed and client.acks == []
    assert list((st.root / "GARMIN/APPS/MEDIA").iterdir()) == []
    assert not st.exists("GARMIN/APPS/IN.BIN")


def test_install_queued_total_space(iq_device):
    st, dev = iq_device
    # available space 12 bytes: one 8-byte app fits, two do not
    resp = proto.enc_varint(1, 12) + proto.enc_bytes(3, proto.enc_bytes(1, STORE_ID.bytes))
    (st.root / "GARMIN/APPS/OUT.BIN").write_bytes(proto.enc_bytes(3, proto.enc_bytes(2, resp)))
    assert ciq.read_installed(dev, st).available_space == 12
    rep = ciq.install_queued(dev, st, FakeClient(),
                             [iq_message(1, uuid.uuid4(), "A"), iq_message(2, uuid.uuid4(), "B")],
                             known={})
    assert rep.installed == ["A"] and [n for n, _e in rep.failed] == ["B"]


def test_malformed_ciq_files():
    good = uuid.uuid4()
    bad_entries = (
        proto.enc_bytes(1, proto.enc_varint(1, 5))  # id as varint
        + proto.enc_bytes(1, proto.enc_bytes(1, b"short"))
        + proto.enc_bytes(1, b"\xff")  # undecodable entry
        + proto.enc_bytes(1, proto.enc_bytes(1, good.bytes) + proto.enc_bytes(2, b"x"))
    )
    data = proto.enc_bytes(3, proto.enc_bytes(5, bad_entries))
    assert ciq.parse_in_bin(data) == [(good, 0, False, False)]
    assert ciq.parse_in_bin(b"\xff\xff\xff") == []
    assert ciq.parse_in_bin(proto.enc_varint(3, 1)) == []
    with pytest.raises(ciq.CiqError):
        ciq.parse_out_bin(b"\xff\xff\xff")
    with pytest.raises(ciq.CiqError):
        ciq.parse_out_bin(proto.enc_varint(3, 1))
    entry = proto.enc_bytes(3, proto.enc_bytes(1, good.bytes) + proto.enc_varint(3, 7))
    state = ciq.parse_out_bin(proto.enc_bytes(3, proto.enc_bytes(2, proto.enc_bytes(1, b"x")
                                                                    + entry)))
    assert state.available_space is None and state.apps[0].name == ""


# --- 10. proto ------------------------------------------------------------------------


def test_proto_rejects_truncated_fixed_and_field_zero():
    with pytest.raises(proto.ProtoError):
        proto.decode(bytes([1 << 3 | proto.I32, 1, 2]))
    with pytest.raises(proto.ProtoError):
        proto.decode(bytes([1 << 3 | proto.I64]) + b"\x00" * 7)
    with pytest.raises(proto.ProtoError):
        proto.decode(bytes([0 << 3 | proto.VARINT, 1]))
    assert proto.first(proto.decode(bytes([1 << 3 | proto.I32]) + b"abcd"), 1) == b"abcd"


# --- 4. updates / maps write failures -------------------------------------------------


class TruncatingStorage(LocalStorage):
    def _write_file(self, real_path, local, progress=None):
        self._p(real_path).write_bytes(Path(local).read_bytes()[:10])


class CrashingStorage(LocalStorage):
    # Half a file under the real name, as over MTP: not a drive's temporary copy; and, as
    # GioStorage, it removes that half itself (the caller never deletes anything).
    replaces_atomically = False

    def _write_file(self, real_path, local, progress=None):
        self._p(real_path).write_bytes(b"half")
        raise DeviceError("unplugged")

    def _discard_partial(self, real_path):
        self._p(real_path).unlink(missing_ok=True)


@pytest.mark.parametrize("cls,exc", [(TruncatingStorage, updates.UpdateError),
                                     (CrashingStorage, DeviceError)])
def test_update_write_failure_leaves_no_file(server, device_root, xdg, cls, exc):
    items = updates.parse_updates(sw_response(server))
    st = cls(device_root)
    with pytest.raises(exc):
        updates.install(items, st)
    assert not (device_root / "GARMIN/GUPDATE.GSP").exists()
    assert not (device_root / "GARMIN/RemoteSW/aux.gcd").exists()


@pytest.mark.parametrize("cls,exc", [(TruncatingStorage, maps.MapError),
                                     (CrashingStorage, DeviceError)])
def test_map_write_failure_leaves_no_file(server, device_root, xdg, cls, exc):
    all_maps = maps.parse_maps(maps_response(server))
    japan = all_maps[2]
    with pytest.raises(exc):
        maps.install(japan, all_maps, cls(device_root), lambda ident: {})
    assert not (device_root / "GARMIN/J.img").exists()


# --- 11. maps without deliverables -----------------------------------------------------


def test_map_with_missing_deliverable_not_installable(server, device_root, xdg):
    resp = maps_response(server)
    east = resp["maps"][1]
    east["installoptions"][0]["files"][1]["deliverableoptions"] = []
    resp["maps"][2]["installoptions"][0]["files"] = []
    all_maps = maps.parse_maps(resp)
    assert all_maps[0].installable
    assert not all_maps[1].installable and not all_maps[2].installable
    (device_root / "GARMIN/E.img").write_bytes(b"old")
    called = []
    with pytest.raises(maps.MapError):
        maps.install(all_maps[1], all_maps, LocalStorage(device_root), called.append)
    assert (device_root / "GARMIN/E.img").exists() and called == []


# --- 5. GIO free space -----------------------------------------------------------------


def test_gio_free_space_zero(monkeypatch):
    gio_storage = pytest.importorskip("openexpress.device.gio_storage")

    class Info:
        def __init__(self, values):
            self.values = values

        def has_attribute(self, a):
            return a in self.values

        def get_attribute_uint64(self, a):
            return self.values.get(a, 0)

    class Root:
        values = {"filesystem::free": 0, "filesystem::size": 1000}

        def query_filesystem_info(self, attr, cancellable):
            return Info({k: v for k, v in self.values.items() if k == attr})

    st = gio_storage.GioStorage.__new__(gio_storage.GioStorage)
    st.root = Root()
    assert st.free_space() == 0
    assert st.capacity() == 1000
    Root.values = {}
    assert st.free_space() is None and st.capacity() is None


# --- 6/7/13. music --------------------------------------------------------------------


def _track(local, album, disc=None, size=10):
    return music.Track(local=Path(local), title="t", artist="Art", album=album, genre="",
                       compilation=False, kind="track", size=size, disc=disc)


def test_plan_copy_disambiguates_duplicates(iq_device):
    st, dev = iq_device
    tracks = [_track("/a/CD1/01 Intro.mp3", "Alb", 1), _track("/a/CD2/01 Intro.mp3", "Alb", 2),
              _track("/b/01 intro.MP3", "alb"), _track("/c/01 Intro.mp3", "Alb")]
    dests = [d for _t, d in music.plan_copy(tracks, dev, st).to_copy]
    assert dests == ["Music/Art/Alb/01 Intro.mp3", "Music/Art/Alb/2-01 Intro.mp3",
                     "Music/Art/alb/01 intro (2).MP3", "Music/Art/Alb/01 Intro (3).mp3"]
    assert len({d.casefold() for d in dests}) == 4


def test_match_paths(iq_device):
    st, dev = iq_device
    items = [music.DeviceMedia("Music/Artist A/Album B/01 Song.mp3", 1, "track"),
             music.DeviceMedia("Music/Other/Musical/02 Tune.mp3", 1, "track"),
             music.DeviceMedia("Podcasts/Show/ep1.mp3", 1, "podcast")]
    pls = ["Music/Run mix.m3u8"]
    for bad in ["", "  ", "ab", " a "]:
        with pytest.raises(music.MusicError):
            music.match_paths(items, pls, [bad])
    assert music.match_paths(items, pls, ["music"]) == ["Music/Other/Musical/02 Tune.mp3"]
    assert music.match_paths(items, pls, ["artist a"]) == ["Music/Artist A/Album B/01 Song.mp3"]
    assert music.match_paths(items, pls, ["SONG"]) == ["Music/Artist A/Album B/01 Song.mp3"]
    assert music.match_paths(items, pls, ["podcasts/show"], dev) == ["Podcasts/Show/ep1.mp3"]
    assert music.match_paths(items, pls, ["mix"]) == ["Music/Run mix.m3u8"]
    assert music.match_paths(items, pls, ["Podcasts/Show/ep1.mp3"]) == ["Podcasts/Show/ep1.mp3"]
    assert music.match_paths(items, pls, ["Podcasts"]) == []
    outside = [music.DeviceMedia("GARMIN/Other/song.mp3", 1, "track")]
    assert music.match_paths(outside, [], ["song"], dev) == []


def test_scan_direct_file_does_not_use_parent_folders(iq_device, tmp_path):
    st, dev = iq_device
    home = tmp_path / "home" / "user"
    home.mkdir(parents=True)
    f = home / "song.mp3"
    f.write_bytes(b"\x00" * 10)
    t = music.scan([f], dev)[0]
    assert t.artist == "" and t.album == ""
    assert t.device_path(dev) == "Music/Unknown/Unknown/song.mp3"
    lib = tmp_path / "lib"
    (lib / "Album").mkdir(parents=True)
    shutil.copy(f, lib / "Album" / "x.mp3")
    shutil.copy(f, lib / "top.mp3")
    by_name = {t.local.name: t for t in music.scan([lib], dev)}
    assert (by_name["x.mp3"].artist, by_name["x.mp3"].album) == ("", "Album")
    assert (by_name["top.mp3"].artist, by_name["top.mp3"].album) == ("", "")


# --- 12. storage ----------------------------------------------------------------------


def test_local_storage_errors(device_root):
    st = LocalStorage(device_root)
    for bad in ["", "/", "GARMIN/", "GARMIN/  "]:
        with pytest.raises(DeviceError):
            st.write(bad, b"x")
        with pytest.raises(DeviceError):
            st.write_file(bad, device_root / "GARMIN/GarminDevice.xml")
    with pytest.raises(DeviceError):
        st.write("GARMIN/Activity", b"x")
    with pytest.raises(DeviceError):
        st.delete("GARMIN/Activity")
    with pytest.raises(DeviceError):
        st.delete("")
    assert (device_root / "GARMIN/Activity").is_dir()
    if os.geteuid() != 0:
        (device_root / "GARMIN/Monitor/M1.FIT").chmod(0)
        with pytest.raises(DeviceError):
            st.read("GARMIN/Monitor/M1.FIT")
