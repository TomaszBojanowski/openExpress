import uuid

import pytest

from openexpress import ciq, music, proto
from openexpress.device.storage import LocalStorage
from openexpress.device.xmldevice import parse

IQ_XML_TYPES = """
  <DataType><Name>IQWatchFaces</Name><File><Location><Path>GARMIN/APPS/MEDIA</Path>
   <FileExtension>PRG</FileExtension></Location><TransferDirection>InputOutput</TransferDirection></File></DataType>
  <DataType><Name>IQAppsConfiguration</Name>
   <File><Location><Path>GARMIN/APPS</Path><BaseName>OUT</BaseName><FileExtension>BIN</FileExtension></Location>
    <TransferDirection>OutputFromUnit</TransferDirection></File>
   <File><Location><Path>GARMIN/APPS</Path><BaseName>IN</BaseName><FileExtension>BIN</FileExtension></Location>
    <TransferDirection>InputToUnit</TransferDirection></File></DataType>
  <DataType><Name>IQAppsSettingsFile</Name><File><Location><Path>GARMIN/APPS/SETTINGS</Path>
   <FileExtension>SET</FileExtension></Location><TransferDirection>InputOutput</TransferDirection></File></DataType>
  <DataType><Name>MEDIA_TRACK</Name><File><Location><Path>Music/</Path><FileExtension>MP3</FileExtension></Location>
   <TransferDirection>InputToUnit</TransferDirection></File></DataType>
  <DataType><Name>MEDIA_PODCAST</Name><File><Location><Path>Podcasts</Path><FileExtension>MP3</FileExtension></Location>
   <TransferDirection>InputToUnit</TransferDirection></File></DataType>
  <DataType><Name>MEDIA_PLAYLIST</Name><File><Location><Path>Music/</Path><FileExtension>M3U8</FileExtension></Location>
   <TransferDirection>InputToUnit</TransferDirection></File></DataType>
"""

STORE_ID = uuid.UUID("7cd38736-510a-4480-8801-0dfe2f5944af")
PRELOADED = uuid.UUID("c0ffee15-600d-540c-0000-000000000000")
FACE = uuid.UUID("c33a05fa-bb7b-46a4-87b4-482a3e726a11")


def out_bin(apps):
    entries = b""
    for app_id, typ, name, disabled, filename in apps:
        body = (proto.enc_bytes(1, app_id.bytes) + proto.enc_varint(2, typ) + proto.enc_str(3, name)
                + proto.enc_varint(4, disabled))
        if filename:
            body += proto.enc_varint(5, 68) + proto.enc_str(6, filename)
        entries += proto.enc_bytes(3, body)
    resp = proto.enc_varint(1, 66_000_000) + proto.enc_varint(2, 31) + entries
    return proto.enc_bytes(3, proto.enc_bytes(2, resp))


@pytest.fixture
def iq_device(device_root):
    xml_path = device_root / "GARMIN/GarminDevice.xml"
    xml_path.write_text(xml_path.read_text().replace("</MassStorageMode>", IQ_XML_TYPES + "</MassStorageMode>"))
    (device_root / "GARMIN/APPS/OUT.BIN").write_bytes(out_bin([
        (STORE_ID, 1, "Connect IQ Store", False, "DADE7E4"),
        (PRELOADED, 8, "Softball", True, ""),
    ]))
    st = LocalStorage(device_root)
    return st, parse(st.read("GARMIN/GarminDevice.xml"))


def test_proto_roundtrip():
    data = proto.enc_varint(1, 300) + proto.enc_str(2, "żółw") + proto.enc_bytes(3, b"\x00\x01")
    f = proto.decode(data)
    assert proto.first(f, 1) == 300
    assert proto.first(f, 2).decode() == "żółw"
    assert proto.first(f, 3) == b"\x00\x01"


def test_read_installed_and_in_bin(iq_device):
    st, dev = iq_device
    state = ciq.read_installed(dev, st)
    assert state.available_slots == 31
    assert [a.name for a in state.store_apps] == ["Connect IQ Store"]
    assert state.find(PRELOADED).preloaded and state.find(PRELOADED).disabled
    assert ciq.app_file_path(dev, state.find(STORE_ID)) == "GARMIN/APPS/MEDIA/DADE7E4.PRG"
    ciq.set_enabled(dev, st, PRELOADED, True)
    pending = ciq.parse_in_bin(st.read("GARMIN/APPS/IN.BIN"))
    assert [p[0] for p in pending] == [STORE_ID, PRELOADED]
    assert pending[1][2] is False
    assert ciq.read_installed(dev, st).find(PRELOADED).disabled is False


class FakeClient:
    def __init__(self):
        self.acks = []

    def download_message(self, url, absolute):
        return b"PRG-DATA"

    def ack_message(self, mid):
        self.acks.append(mid)


def test_install_queued_appends_new_app(iq_device):
    st, dev = iq_device
    messages = [
        {"messageid": 7, "devicexmldatatype": "IQWatchFaces",
         "metadata": {"messageurl": "x", "messagename": "Face It", "appdetails": {"id": str(FACE)}}},
        {"messageid": 8, "devicexmldatatype": "FIT_TYPE_6", "metadata": {"messageurl": "y"}},
    ]
    client = FakeClient()
    rep = ciq.install_queued(dev, st, client, messages, known={})
    assert rep.installed == ["Face It"] and client.acks == [7]
    prgs = list((st.root / "GARMIN/APPS/MEDIA").iterdir())
    assert len(prgs) == 1 and len(prgs[0].stem) == 8 and prgs[0].read_bytes() == b"PRG-DATA"
    pending = ciq.parse_in_bin(st.read("GARMIN/APPS/IN.BIN"))
    assert [p[0] for p in pending] == [STORE_ID, PRELOADED, FACE]
    assert pending[2][1] == 3 and pending[2][2] is True  # new watch face: installed, not active


def test_remove_store_app_only(iq_device):
    st, dev = iq_device
    (st.root / "GARMIN/APPS/MEDIA").mkdir(exist_ok=True)
    (st.root / "GARMIN/APPS/MEDIA/DADE7E4.PRG").write_bytes(b"x")
    state = ciq.read_installed(dev, st)
    with pytest.raises(ciq.CiqError):
        ciq.remove(dev, st, state.find(PRELOADED))
    with pytest.raises(ciq.CiqError):  # the built-in Connect IQ Store app
        ciq.remove(dev, st, state.find(STORE_ID))
    app = state.find(STORE_ID)
    app.app_id = FACE  # pretend it is a regular store app
    assert ciq.remove(dev, st, app) == ["GARMIN/APPS/MEDIA/DADE7E4.PRG"]


def test_music_layout_and_playlist(iq_device, tmp_path):
    st, dev = iq_device
    src = tmp_path / "lib" / "Artist A" / "Album B"
    src.mkdir(parents=True)
    (src / "01 Song.mp3").write_bytes(b"ID3" + b"\x00" * 100)
    (src / "cover.jpg").write_bytes(b"x")
    pod = tmp_path / "pods" / "Show"
    pod.mkdir(parents=True)
    (pod / "ep1.mp3").write_bytes(b"\x00" * 50)
    tracks = music.scan([tmp_path / "lib"], dev)
    assert [t.local.name for t in tracks] == ["01 Song.mp3"]
    assert tracks[0].device_path(dev) == "Music/Artist A/Album B/01 Song.mp3"
    pods = music.scan([tmp_path / "pods"], dev, forced_kind="podcast")
    assert pods[0].device_path(dev) == "Podcasts/Show/ep1.mp3"
    plan = music.plan_copy(tracks + pods, dev, st)
    assert len(plan.to_copy) == 2
    music.copy(plan, st)
    assert music.plan_copy(tracks, dev, st).to_copy == []
    items = music.list_device(dev, st)
    assert {i.kind for i in items} == {"track", "podcast"}
    pl = music.write_playlist(dev, st, "Run: mix", [plan.to_copy[0][1]])
    assert pl == "Music/Run_ mix.m3u8"
    assert (st.root / pl).read_text() == "Music/Artist A/Album B/01 Song.mp3\n"
    assert music.list_playlists(dev, st) == ["Music/Run_ mix.m3u8"]


# --- only the kinds of media that the device declares -----------------------------------------

NO_MEDIA = "This device does not play music, podcasts or audiobooks."


def test_supported_kinds(iq_device, device_root):
    _st, dev = iq_device
    assert music.supported_kinds(dev) == ["track", "podcast"]  # no audiobooks declared
    assert music.supports_playlists(dev)


def test_music_command_lists_declared_kinds_only(iq_device, device_root, capsys):
    from openexpress.cli import main

    assert main(["--path", str(device_root), "music"]) == 0
    out = capsys.readouterr().out
    assert "Tracks: 0" in out and "Podcasts: 0" in out and "Playlists: 0" in out
    assert "Audiobooks" not in out and NO_MEDIA not in out

    # Without playlists: no playlist line either.
    xml = device_root / "GARMIN/GarminDevice.xml"
    xml.write_text(xml.read_text().replace("MEDIA_PLAYLIST", "SOMETHING_ELSE"))
    assert main(["--path", str(device_root), "music"]) == 0
    out = capsys.readouterr().out
    assert "Tracks: 0" in out and "Playlists" not in out


def test_music_command_on_a_device_without_media(device_root, tmp_path, capsys):
    from openexpress.cli import main

    st = LocalStorage(device_root)
    assert music.supported_kinds(parse(st.read("GARMIN/GarminDevice.xml"))) == []
    before = sorted(p.name for p in device_root.rglob("*"))
    assert main(["--path", str(device_root), "music"]) == 0
    captured = capsys.readouterr()
    assert captured.out.strip() == NO_MEDIA  # one message, no empty "Tracks: 0" lists
    song = tmp_path / "song.mp3"
    song.write_bytes(b"ID3" + b"\x00" * 10)
    m3u = tmp_path / "list.m3u"
    m3u.write_text("song.mp3\n")
    for argv in (["add", str(song), "--apply"], ["playlist", str(m3u), "--apply"],
                 ["remove", "song", "--apply"]):
        assert main(["--path", str(device_root), "music", *argv]) == 1, argv
        captured = capsys.readouterr()
        assert NO_MEDIA in captured.err and "Traceback" not in captured.err
    assert sorted(p.name for p in device_root.rglob("*")) == before


def test_music_page_on_a_device_without_media(device_root):
    import types

    from openexpress.gui.app import MainWindow

    st = LocalStorage(device_root)
    session = types.SimpleNamespace(device=parse(st.read("GARMIN/GarminDevice.xml")), storage=st)
    shown, started = [], []
    section = types.SimpleNamespace(
        clear=lambda: shown.append("clear"), show=shown.append,
        error=types.SimpleNamespace(set_title=lambda t: shown.append(("title", t)),
                                    set_description=lambda d: shown.append(("text", d))))
    win = types.SimpleNamespace(session=session, sections={"music": section},
                                _load=lambda *a: started.append(a))
    MainWindow.load_music(win)
    assert started == []  # nothing is read from the device
    assert shown == ["clear", ("title", "Music"), ("text", NO_MEDIA), "error"]


def test_music_page_reads_the_device_when_it_has_media(iq_device):
    import types

    from openexpress.gui.app import MainWindow

    st, dev = iq_device
    started = []
    win = types.SimpleNamespace(session=types.SimpleNamespace(device=dev, storage=st),
                                sections={}, _load=lambda *a: started.append(a[0]),
                                show_music=None)
    MainWindow.load_music(win)
    assert started == ["music"]


# --- adding a playlist (the window's "Add Playlist…") -----------------------------------------


@pytest.fixture
def library(tmp_path):
    """A made-up local library with a playlist that has good, missing and unsupported entries."""
    lib = tmp_path / "lib"
    (lib / "sub").mkdir(parents=True)
    (lib / "one.mp3").write_bytes(b"ID3" + b"\x00" * 100)
    (lib / "sub" / "two.mp3").write_bytes(b"ID3" + b"\x00" * 200)
    (lib / "lossless.flac").write_bytes(b"fLaC" + b"\x00" * 50)
    m3u = lib / "Morning run.m3u8"
    m3u.write_text("#EXTM3U\n#EXTINF:30,One\none.mp3\n\nsub/two.mp3\nlossless.flac\n"
                   "gone.mp3\n" + str(lib / "one.mp3") + "\n", encoding="utf-8")
    return m3u


def test_plan_playlist_lists_what_cannot_be_added(iq_device, library):
    st, dev = iq_device
    plan = music.plan_playlist(library, dev, st)
    assert plan.name == "Morning run" and plan.target == "Music/Morning run.m3u8"
    assert not plan.replaces
    assert plan.skipped == [("lossless.flac", "format not supported by the device"),
                            ("gone.mp3", "file not found")]
    # The same file listed twice (relative and absolute) is copied once and listed twice.
    assert [t.local.name for t, _d in plan.copy.to_copy] == ["one.mp3", "two.mp3"]
    assert plan.copy.already == [] and plan.copy.size == 103 + 203
    assert plan.device_paths == ["Music/Unknown/Unknown/one.mp3", "Music/Unknown/Unknown/two.mp3",
                                 "Music/Unknown/Unknown/one.mp3"]
    # Planning writes nothing.
    assert music.list_device(dev, st) == [] and music.list_playlists(dev, st) == []


def test_add_playlist_copies_missing_tracks_and_writes_the_list(iq_device, library):
    st, dev = iq_device
    library.write_text("one.mp3\nsub/two.mp3\nlossless.flac\n", encoding="utf-8")
    plan = music.plan_playlist(library, dev, st)
    path = music.add_playlist(plan, dev, st)
    assert path == "Music/Morning run.m3u8"
    assert sorted(i.path for i in music.list_device(dev, st)) == [
        "Music/Unknown/Unknown/one.mp3", "Music/Unknown/Unknown/two.mp3"]
    assert st.read(path).decode() == "Music/Unknown/Unknown/one.mp3\nMusic/Unknown/Unknown/two.mp3\n"

    # Again: nothing to copy, and the existing playlist is announced as replaced.
    again = music.plan_playlist(library, dev, st)
    assert again.replaces and again.copy.to_copy == [] and len(again.copy.already) == 2
    assert music.add_playlist(again, dev, st) == path


def test_playlist_without_usable_tracks_is_not_written(iq_device, library):
    st, dev = iq_device
    library.write_text("gone.mp3\nlossless.flac\n", encoding="utf-8")
    plan = music.plan_playlist(library, dev, st)
    assert plan.device_paths == [] and len(plan.skipped) == 2
    with pytest.raises(music.MusicError, match="no tracks that can be added"):
        music.add_playlist(plan, dev, st)
    assert music.list_playlists(dev, st) == []


def test_plan_playlist_needs_a_device_with_playlists(device_root, library):
    st = LocalStorage(device_root)
    dev = parse(st.read("GARMIN/GarminDevice.xml"))
    with pytest.raises(music.MusicError, match="does not support playlists"):
        music.plan_playlist(library, dev, st)


def test_window_playlist_summary(iq_device, library):
    from openexpress.gui.app import MainWindow

    st, dev = iq_device
    plan = music.plan_playlist(library, dev, st)
    text = MainWindow._playlist_summary(plan)
    assert text.splitlines() == [
        "3 tracks",
        "2 files to copy (306 B), 0 already on the device",
        "",
        "2 tracks cannot be added:",
        "• lossless.flac (format not supported by the device)",
        "• gone.mp3 (file not found)",
    ]
    assert "Flatpak" in MainWindow._playlist_summary(plan, in_flatpak=True).splitlines()[-1]

    library.write_text("one.mp3\n", encoding="utf-8")
    music.add_playlist(music.plan_playlist(library, dev, st), dev, st)
    again = MainWindow._playlist_summary(music.plan_playlist(library, dev, st), in_flatpak=True)
    assert again.splitlines() == [
        "1 track", "0 files to copy (0 B), 1 already on the device",
        "A playlist with this name is already on the device and will be replaced."]

    many = music.PlaylistPlan("x", "Music/x.m3u8", False, music.CopyPlan(),
                              [(f"t{i}.mp3", "file not found") for i in range(13)], [])
    lines = MainWindow._playlist_summary(many).splitlines()
    assert lines[3] == "13 tracks cannot be added:" and lines[-1] == "… and 3 more"
    assert len([line for line in lines if line.startswith("•")]) == 10


def test_window_add_playlist_without_usable_tracks_only_reports(iq_device, library):
    import types

    from openexpress.gui.app import MainWindow

    st, dev = iq_device
    library.write_text("gone.mp3\n", encoding="utf-8")
    events = []

    def run_task(title, fn, on_done=None, on_error=None):
        events.append(title)
        on_done(fn())

    win = types.SimpleNamespace(session=types.SimpleNamespace(device=dev, storage=st),
                                run_task=run_task, _playlist_summary=MainWindow._playlist_summary,
                                error=lambda title, text: events.append((title, text)))
    MainWindow.add_playlist(win, library)
    assert events[0] == "Reading the playlist…" and len(events) == 2
    title, text = events[1]
    assert title == "The playlist has no tracks that can be added."
    assert "• gone.mp3 (file not found)" in text
    assert music.list_playlists(dev, st) == [] and music.list_device(dev, st) == []
