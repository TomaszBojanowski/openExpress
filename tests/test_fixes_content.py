import os
import shutil
import uuid
from datetime import datetime
from pathlib import Path

import pytest

from openexpress import backup, ciq, maps, music, proto, updates
from openexpress.device.storage import DeviceError, LocalStorage
from openexpress.device.xmldevice import parse

from test_ciq_music import FACE, IQ_XML_TYPES, PRELOADED, STORE_ID, FakeClient, out_bin
from test_updates_maps import FILES, maps_response, server, sw_response  # noqa: F401


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
    old = [unit / "2020-01-0{} (10.00.00)".format(i) for i in (1, 2, 3)]
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
    def _write_file(self, real_path, local, progress=None):
        self._p(real_path).write_bytes(b"half")
        raise DeviceError("unplugged")


@pytest.mark.parametrize("cls,exc", [(TruncatingStorage, updates.UpdateError),
                                     (CrashingStorage, DeviceError)])
def test_update_write_failure_leaves_no_file(server, device_root, xdg, cls, exc):  # noqa: F811
    items = updates.parse_updates(sw_response(server))
    st = cls(device_root)
    with pytest.raises(exc):
        updates.install(items, st)
    assert not (device_root / "GARMIN/GUPDATE.GSP").exists()
    assert not (device_root / "GARMIN/RemoteSW/aux.gcd").exists()


@pytest.mark.parametrize("cls,exc", [(TruncatingStorage, maps.MapError),
                                     (CrashingStorage, DeviceError)])
def test_map_write_failure_leaves_no_file(server, device_root, xdg, cls, exc):  # noqa: F811
    all_maps = maps.parse_maps(maps_response(server))
    japan = all_maps[2]
    with pytest.raises(exc):
        maps.install(japan, all_maps, cls(device_root), lambda ident: {})
    assert not (device_root / "GARMIN/J.img").exists()


# --- 11. maps without deliverables -----------------------------------------------------


def test_map_with_missing_deliverable_not_installable(server, device_root, xdg):  # noqa: F811
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
