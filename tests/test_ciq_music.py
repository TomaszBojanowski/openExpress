import contextlib
import datetime
import logging
import uuid

import pytest
from fakes import RunNow, step_words

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
        "2 files to copy (306\u00a0B), 0 already on the device",
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
        "1 track", "0 files to copy (0\u00a0B), 1 already on the device",
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

    run_task = RunNow(on_call=lambda call: events.append(call.title))

    win = types.SimpleNamespace(session=types.SimpleNamespace(device=dev, storage=st),
                                run_task=run_task, _playlist_summary=MainWindow._playlist_summary,
                                error=lambda title, text: events.append((title, text)))
    MainWindow.add_playlist(win, library)
    assert events[0] == "Reading the playlist…" and len(events) == 2
    title, text = events[1]
    assert title == "The playlist has no tracks that can be added."
    assert "• gone.mp3 (file not found)" in text
    assert music.list_playlists(dev, st) == [] and music.list_device(dev, st) == []


# --- an incomplete app list blocks every change -----------------------------------------------


def damaged_out_bin(device_root, kind):
    """OUT.BIN with two good apps and one entry that cannot be read."""
    good = out_bin([(STORE_ID, 1, "Connect IQ Store", False, "DADE7E4"),
                    (PRELOADED, 8, "Softball", True, "")])
    resp = proto.first(proto.decode(proto.first(proto.decode(good), 3)), 2)
    bad = {"truncated": b"\x0a\x10short",  # says 16 bytes of ID, has 5
           "no-id": proto.enc_varint(2, 1) + proto.enc_str(3, "Nameless")}[kind]
    damaged = proto.enc_bytes(3, proto.enc_bytes(2, resp + proto.enc_bytes(3, bad)))
    (device_root / "GARMIN/APPS/OUT.BIN").write_bytes(damaged)


FACE_MESSAGE = {"messageid": 7, "devicexmldatatype": "IQWatchFaces",
                "metadata": {"messageurl": "x", "messagename": "Face It",
                             "appdetails": {"id": str(FACE)}}}


class CountingClient(FakeClient):
    def __init__(self):
        super().__init__()
        self.downloads = 0

    def download_message(self, url, absolute):
        self.downloads += 1
        return super().download_message(url, absolute)


def apps_files(st):
    return sorted(str(p.relative_to(st.root)) for p in (st.root / "GARMIN/APPS").rglob("*")
                  if p.is_file())


@pytest.mark.parametrize("kind", ["truncated", "no-id"])
def test_unreadable_entry_is_counted_not_dropped_silently(iq_device, device_root, kind):
    st, dev = iq_device
    damaged_out_bin(device_root, kind)
    state = ciq.read_installed(dev, st)
    assert state.unreadable == 1 and len(state.apps) == 2  # the readable ones are still listed
    with pytest.raises(ciq.CiqError, match="1 entry that cannot be read"):
        ciq.require_complete(state)


@pytest.mark.parametrize("kind", ["truncated", "no-id"])
def test_unreadable_entry_blocks_install_remove_and_toggle(iq_device, device_root, kind):
    st, dev = iq_device
    (st.root / "GARMIN/APPS/MEDIA").mkdir(exist_ok=True)
    (st.root / "GARMIN/APPS/MEDIA/DADE7E4.PRG").write_bytes(b"x")
    damaged_out_bin(device_root, kind)
    before = apps_files(st)
    client = CountingClient()
    with pytest.raises(ciq.CiqError, match="cannot be read"):
        ciq.install_queued(dev, st, client, [FACE_MESSAGE], known={})
    assert client.downloads == 0 and client.acks == []  # nothing fetched, nothing acknowledged
    with pytest.raises(ciq.CiqError, match="cannot be read"):
        ciq.set_enabled(dev, st, PRELOADED, True)
    app = ciq.read_installed(dev, st).find(STORE_ID)
    app.app_id = FACE  # pretend it is a regular store app
    with pytest.raises(ciq.CiqError, match="cannot be read"):
        ciq.remove(dev, st, app)
    assert apps_files(st) == before  # no IN.BIN, no new .PRG, nothing deleted
    assert not (st.root / "GARMIN/APPS/IN.BIN").exists()


def test_complete_list_still_allows_changes(iq_device):
    st, dev = iq_device
    ciq.require_complete(ciq.read_installed(dev, st))
    assert ciq.read_installed(dev, st).unreadable == 0


def test_sync_reports_an_unreadable_app_list_without_failing(iq_device, device_root):
    import types

    from openexpress import service

    st, dev = iq_device
    damaged_out_bin(device_root, "no-id")

    class Client(CountingClient):
        def require_verified(self, unit_id=None):
            raise RuntimeError("no store request expected")

    s = types.SimpleNamespace(storage=st, device=dev, client=Client(),
                              settings=types.SimpleNamespace(locale="en_US"))
    errors = []
    assert service._install_queued_apps(s, [FACE_MESSAGE], lambda m: None, errors) == []
    assert len(errors) == 1 and errors[0].startswith("Connect IQ: ") and "cannot be read" in errors[0]


# --- preview of the app list an install would write ---------------------------------------------


def store_app(app_id, name, store_type="watchface"):
    return ciq.StoreApp(app_id, name, "dev", store_type, 1000, 5, "1.0", False, False)


def test_preview_in_bin_for_a_new_watch_face(iq_device):
    st, dev = iq_device
    state = ciq.read_installed(dev, st)
    p = ciq.preview_in_bin(state, [store_app(FACE, "Face It")])
    assert (p.on_device, p.pending, p.entries, p.kept) == (2, 0, 3, True)
    assert p.new == [("Face It", 3, True)]  # a watch face is added as inactive
    assert not (st.root / "GARMIN/APPS/IN.BIN").exists()  # nothing is written

    widget = ciq.preview_in_bin(state, [store_app(FACE, "Glance", "widget")])
    assert widget.new == [("Glance", 2, False)]


def test_preview_matches_what_install_writes(iq_device):
    st, dev = iq_device
    preview = ciq.preview_in_bin(ciq.read_installed(dev, st), [store_app(FACE, "Face It")])
    ciq.install_queued(dev, st, FakeClient(), [FACE_MESSAGE], known={})
    written = ciq.parse_in_bin(st.read("GARMIN/APPS/IN.BIN"))
    assert len(written) == preview.entries
    assert written[-1][1:3] == preview.new[0][1:3]


def test_preview_for_an_update_and_with_pending_apps(iq_device):
    st, dev = iq_device
    state = ciq.read_installed(dev, st)
    update = ciq.preview_in_bin(state, [store_app(STORE_ID, "Connect IQ Store", "watch-app")])
    assert (update.entries, update.kept, update.new) == (2, True, [])

    ciq.install_queued(dev, st, FakeClient(), [FACE_MESSAGE], known={})  # IN.BIN, not applied yet
    state = ciq.read_installed(dev, st)
    again = ciq.preview_in_bin(state, [store_app(FACE, "Face It")])
    assert (again.on_device, again.pending, again.entries, again.kept, again.new) == (2, 1, 3, True, [])


def test_preview_notices_a_list_that_would_lose_an_app(iq_device, monkeypatch):
    st, dev = iq_device
    state = ciq.read_installed(dev, st)
    real = ciq.build_in_bin
    monkeypatch.setattr(ciq, "build_in_bin", lambda apps: real(apps[1:]))
    assert ciq.preview_in_bin(state, [store_app(FACE, "Face It")]).kept is False
    monkeypatch.setattr(ciq, "build_in_bin",
                        lambda apps: real([ciq.InstalledApp(a.app_id, a.app_type, a.name,
                                                            not a.disabled) for a in apps]))
    assert ciq.preview_in_bin(state, []).kept is False  # flags changed


class FakeStore:
    """Stands in for ciq.StoreApi in CLI tests; records what would change the account."""

    queued = []

    def __init__(self, client):
        pass

    def account_apps(self, device, locale):
        return [store_app(FACE, "Face It")]

    def updates(self, device, installed, locale):
        return []

    def queue_install(self, device, apps):
        FakeStore.queued.append([a.name for a in apps])

    forgotten = []

    def forget(self, device, app_ids):
        FakeStore.forgotten.append(list(app_ids))


def queue_once_requested(unit_id):
    """Garmin's queue: the app is in it once the install was requested."""
    return [FACE_MESSAGE] if FakeStore.queued else []


@pytest.fixture
def apps_cli(monkeypatch, xdg):
    from openexpress import cli, service

    client = CountingClient()
    client.device_messages = queue_once_requested
    FakeStore.queued = []
    FakeStore.forgotten = []
    monkeypatch.setattr(cli, "make_client", lambda settings, need_auth: client)
    monkeypatch.setattr(service, "require_account_device", lambda c, unit_id: [])
    monkeypatch.setattr(ciq, "StoreApi", FakeStore)
    return client


def test_cli_install_dry_run_shows_the_app_list(iq_device, device_root, apps_cli, capsys):
    from openexpress.cli import main

    st, _dev = iq_device
    before = apps_files(st)
    assert main(["--path", str(device_root), "apps", "install", "Face It"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[1:] == [
        "App list to be written (IN.BIN): 3 entries; the device lists 2 (OUT.BIN)",
        "  every app on the device is kept, in the same order and with the same flags",
        "  new: Face It (watch face), added as inactive",
        "Before any change, a copy of the app list of the device is made.",
        "Dry run. Add --apply.",
    ]
    assert FakeStore.queued == [] and apps_cli.downloads == 0 and apps_files(st) == before


def test_the_store_is_asked_in_the_language_of_the_system(iq_device, device_root, apps_cli,
                                                          monkeypatch):
    """App names and descriptions come in the language of the user (issue #5)."""
    from openexpress import i18n
    from openexpress.cli import main

    monkeypatch.setattr(i18n, "system_language_names",
                        lambda: ["de_DE.UTF-8", "de_DE", "de", "C"], raising=False)
    monkeypatch.setattr(i18n, "chosen_language", lambda: None, raising=False)
    asked = []
    monkeypatch.setattr(FakeStore, "account_apps", lambda self, device, locale: asked.append(
        ("downloads", locale)) or [store_app(FACE, "Face It")])
    monkeypatch.setattr(FakeStore, "updates", lambda self, device, installed, locale: asked.append(
        ("updates", locale)) or [])
    assert main(["--path", str(device_root), "apps"]) == 0
    assert asked == [("downloads", "de-DE"), ("updates", "de-DE")]


def test_cli_install_apply_writes_what_the_preview_said(iq_device, device_root, apps_cli, capsys):
    from openexpress.cli import main

    st, _dev = iq_device
    assert main(["--path", str(device_root), "apps", "install", "Face It", "--apply"]) == 0
    assert "Installed: Face It" in capsys.readouterr().out
    assert FakeStore.queued == [["Face It"]] and apps_cli.acks == [7]
    assert len(ciq.parse_in_bin(st.read("GARMIN/APPS/IN.BIN"))) == 3


@pytest.mark.parametrize("argv", [
    ["apps", "install", "Face It"], ["apps", "install", "Face It", "--apply"],
    ["apps", "update", "--apply"], ["apps", "remove", "Connect IQ Store", "--apply"],
    ["apps", "disable", "Connect IQ Store", "--apply"], ["apps", "enable", "Softball"],
], ids=" ".join)
def test_cli_refuses_changes_with_an_unreadable_app_list(iq_device, device_root, apps_cli,
                                                         capsys, argv):
    from openexpress.cli import main

    st, _dev = iq_device
    damaged_out_bin(device_root, "truncated")
    before = apps_files(st)
    assert main(["--path", str(device_root), *argv]) == 1
    err = capsys.readouterr().err
    assert "1 entry that cannot be read" in err and "Traceback" not in err
    assert FakeStore.queued == [] and apps_cli.downloads == 0  # nothing was queued at Garmin
    assert apps_files(st) == before


def test_cli_list_warns_about_an_unreadable_app_list(iq_device, device_root, apps_cli, capsys):
    from openexpress.cli import main

    damaged_out_bin(device_root, "no-id")
    assert main(["--path", str(device_root), "apps"]) == 0
    out = capsys.readouterr().out
    assert "WARNING: 1 entry of the app list on the device cannot be read" in out
    assert "Connect IQ Store" in out  # the readable apps are still shown


# --- devices where openExpress cannot install over USB yet (e.g. the fenix 8) -------------------


@pytest.fixture
def no_usb_apps(iq_device, device_root, xdg, monkeypatch):
    """The declared app folder is missing and the device refuses to create it."""
    from openexpress.device.storage import DeviceError

    (device_root / "GARMIN/APPS/MEDIA").rmdir()
    attempts = []

    def refuse(self, real_path):
        attempts.append(real_path)
        raise DeviceError("Cannot create directory " + real_path + ": Could not send object info.")

    monkeypatch.setattr(LocalStorage, "_mkdir", refuse)
    st, dev = iq_device
    return st, dev, attempts


def test_app_folder_states(iq_device, device_root, xdg):
    st, dev = iq_device
    assert ciq.usb_dir_state(dev, st, "IQWatchFaces") == ciq.READY
    (device_root / "GARMIN/APPS/MEDIA").rmdir()
    assert ciq.usb_dir_state(dev, st, "IQWatchFaces") == ciq.MISSING
    # A device that lets the folder be created is used as before.
    assert ciq.prepare_app_dir(dev, st, "IQWatchFaces") is True
    assert (device_root / "GARMIN/APPS/MEDIA").is_dir()
    assert ciq.usb_dir_state(dev, st, "IQWatchFaces") == ciq.READY


def test_refusal_is_remembered_per_software_version(no_usb_apps, device_root):
    st, dev, attempts = no_usb_apps
    assert ciq.prepare_app_dir(dev, st, "IQWatchFaces") is False
    assert ciq.usb_dir_state(dev, st, "IQWatchFaces") == ciq.REFUSED
    assert ciq.prepare_app_dir(dev, st, "IQWatchFaces") is False
    assert len(attempts) == 1  # the device is not asked again on every sync
    dev.software_version = dev.software_version + 1  # after a firmware update: try again
    assert ciq.usb_dir_state(dev, st, "IQWatchFaces") == ciq.MISSING
    (device_root / "GARMIN/APPS/MEDIA").mkdir()  # the folder exists: the old refusal is moot
    assert ciq.usb_dir_state(dev, st, "IQWatchFaces") == ciq.READY


def test_queued_app_is_left_for_the_phone(no_usb_apps):
    st, dev, attempts = no_usb_apps
    before = apps_files(st)
    client = CountingClient()
    rep = ciq.install_queued(dev, st, client, [FACE_MESSAGE], known={})
    assert rep.for_phone == ["Face It"] and rep.installed == [] and rep.failed == []
    assert client.downloads == 0 and client.acks == []  # stays in Garmin's queue
    assert apps_files(st) == before and not (st.root / "GARMIN/APPS/IN.BIN").exists()
    ciq.install_queued(dev, st, client, [FACE_MESSAGE], known={})
    assert len(attempts) == 1


def test_install_apps_only_queues_on_such_a_device(no_usb_apps):
    st, dev, _attempts = no_usb_apps
    FakeStore.queued = []
    client = CountingClient()
    reads = []
    client.device_messages = lambda unit_id: reads.append(1) or []
    rep = ciq.install_apps(dev, st, client, FakeStore(client), [store_app(FACE, "Face It")])
    assert FakeStore.queued == [["Face It"]]  # queued at Garmin, for the phone
    assert len(reads) == 1  # read once, to see whether it already waits there
    assert rep.for_phone == ["Face It"] and rep.installed == [] and rep.failed == []
    assert client.downloads == 0


def test_sync_mentions_apps_left_for_the_phone(no_usb_apps):
    import types

    from openexpress import service

    st, dev, _attempts = no_usb_apps

    class Client(CountingClient):
        def require_verified(self, unit_id=None):
            raise RuntimeError("names are cosmetic")

    s = types.SimpleNamespace(storage=st, device=dev, client=Client(),
                              settings=types.SimpleNamespace(locale="en_US"))
    errors = []
    lines = step_words(
        service._install_queued_apps(s, [FACE_MESSAGE], lambda m: None, errors))
    assert errors == []  # not an error
    assert lines == [("1 Connect IQ app waits in the queue: openExpress cannot install apps on "
                      "this device over USB yet")]


def test_cli_install_on_a_device_without_usb_apps(no_usb_apps, device_root, apps_cli, capsys):
    from openexpress.cli import main

    st, _dev, attempts = no_usb_apps
    before = apps_files(st)
    # First dry run: the folder is missing, whether it can be created is not known yet.
    assert main(["--path", str(device_root), "apps", "install", "Face It"]) == 0
    out = capsys.readouterr().out
    assert "the app folder GARMIN/APPS/MEDIA does not exist on the device" in out
    assert "if the device refuses, the app is only queued in Garmin Connect" in out
    assert "Dry run. Add --apply." in out and FakeStore.queued == [] and attempts == []

    assert main(["--path", str(device_root), "apps", "install", "Face It", "--apply"]) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[-1] == (
        "Queued in Garmin Connect: Face It. openExpress cannot install apps on this device "
        "over USB yet; they will be installed by the phone or Garmin Express.")
    assert "ERROR" not in out and "Installed:" not in out
    assert FakeStore.queued == [["Face It"]] and apps_cli.downloads == 0 and apps_cli.acks == []
    assert apps_files(st) == before

    # From now on the dry run says it up front, before anything is queued.
    FakeStore.queued = []
    assert main(["--path", str(device_root), "apps", "install", "Face It"]) == 0
    out = capsys.readouterr().out
    assert "Face It: openExpress cannot install apps on this device over USB yet." in out
    assert "IN.BIN" not in out  # nothing would be written, so there is no list to preview
    assert FakeStore.queued == [] and len(attempts) == 1


def with_phone_installed_face(device_root):
    """OUT.BIN as after a phone install: the watch face is listed, but no file is visible."""
    (device_root / "GARMIN/APPS/OUT.BIN").write_bytes(out_bin([
        (STORE_ID, 1, "Connect IQ Store", False, "DADE7E4"),
        (FACE, 3, "FaceIt2", True, "GACF4619"),
        (PRELOADED, 8, "Softball", True, ""),
    ]))


def test_cli_remove_without_visible_files(no_usb_apps, device_root, apps_cli, capsys):
    from openexpress.cli import main

    st, _dev, _attempts = no_usb_apps
    with_phone_installed_face(device_root)
    before = apps_files(st)
    told = "FaceIt2 cannot be removed over USB: this device shows no app files."

    assert main(["--path", str(device_root), "apps", "remove", "FaceIt2", "--apply"]) == 1
    out = capsys.readouterr().out
    assert told in out and "on the device itself or in Garmin Connect on the phone" in out
    assert "Removed" not in out and FakeStore.forgotten == []

    assert main(["--path", str(device_root), "apps", "remove", "FaceIt2", "--forget"]) == 0
    assert "Would remove it from your account list." in capsys.readouterr().out
    assert FakeStore.forgotten == []

    assert main(["--path", str(device_root), "apps", "remove", "FaceIt2", "--forget", "--apply"]) == 0
    out = capsys.readouterr().out
    assert told in out and out.splitlines()[-1] == "Removed FaceIt2 from your account list."
    assert FakeStore.forgotten == [[FACE]] and apps_files(st) == before  # no file was touched


def test_cli_remove_with_files_still_deletes_them(iq_device, device_root, apps_cli, capsys):
    from openexpress.cli import main

    st, _dev = iq_device
    with_phone_installed_face(device_root)
    (device_root / "GARMIN/APPS/MEDIA/GACF4619.PRG").write_bytes(b"x")
    assert main(["--path", str(device_root), "apps", "remove", "FaceIt2", "--apply"]) == 0
    assert "Removed 1 file." in capsys.readouterr().out
    assert not (device_root / "GARMIN/APPS/MEDIA/GACF4619.PRG").exists()


def test_window_offers_only_forget_for_apps_without_files(iq_device):
    import types

    from openexpress.gui.app import MainWindow

    st, dev = iq_device
    app = ciq.InstalledApp(FACE, 3, "FaceIt2", True, filename="GACF4619", has_files=False)
    asked = []
    win = types.SimpleNamespace(session=types.SimpleNamespace(device=dev, storage=st),
                                confirm_forget_app=asked.append)
    MainWindow.confirm_remove_app(win, app)
    assert asked == [app]  # the usual "delete from the device" dialog is not shown
    text = MainWindow._for_phone_text(["Face It"])
    assert text.startswith("Face It: openExpress cannot install apps on this device over USB yet.")
    assert text.endswith("installed by the phone or Garmin Express.")


# --- built-in items listed twice under one ID (as an app and as a widget) ----------------------

WEATHER = uuid.UUID("c0ffee15-600d-540c-0000-0000000000aa")


@pytest.fixture
def twin_device(iq_device, device_root):
    """Like a real watch: "Weather" is on the list as an app (off) and as a widget (on)."""
    (device_root / "GARMIN/APPS/OUT.BIN").write_bytes(out_bin([
        (STORE_ID, 1, "Connect IQ Store", False, "DADE7E4"),
        (WEATHER, 1, "Weather", True, ""),
        (PRELOADED, 8, "Softball", True, ""),
        (WEATHER, 2, "Weather", False, ""),
    ]))
    return iq_device


def in_bin_flags(st):
    return [(str(i)[-2:], t, disabled) for i, t, disabled, _fav in
            ciq.parse_in_bin(st.read("GARMIN/APPS/IN.BIN"))]


def test_find_with_type(twin_device):
    st, dev = twin_device
    state = ciq.read_installed(dev, st)
    assert state.find(WEATHER, 1).disabled is True and state.find(WEATHER, 2).disabled is False
    assert state.find(WEATHER, 3) is None


def test_toggle_changes_only_the_entry_of_that_type(twin_device):
    st, dev = twin_device
    ciq.set_enabled(dev, st, WEATHER, False, app_type=2)  # switch the widget off
    assert in_bin_flags(st) == [("af", 1, False), ("aa", 1, True), ("00", 8, True),
                                ("aa", 2, True)]  # same order; only the widget changed
    state = ciq.read_installed(dev, st)
    assert state.find(WEATHER, 1).disabled is True and state.find(WEATHER, 2).disabled is True

    ciq.set_enabled(dev, st, WEATHER, True, app_type=1)  # now switch the app on
    assert in_bin_flags(st) == [("af", 1, False), ("aa", 1, False), ("00", 8, True),
                                ("aa", 2, True)]
    # Reading back the pending list keeps the two entries apart.
    state = ciq.read_installed(dev, st)
    assert state.find(WEATHER, 1).disabled is False and state.find(WEATHER, 2).disabled is True
    assert len(state.apps) == 4 and not any(a.pending for a in state.apps)


def test_pending_list_is_merged_by_id_and_type(twin_device):
    st, dev = twin_device
    apps = ciq.read_installed(dev, st).apps
    apps[1].favorite, apps[3].disabled = True, True  # app: favourite; widget: off
    st.write("GARMIN/APPS/IN.BIN", ciq.build_in_bin(apps))
    state = ciq.read_installed(dev, st)
    app, widget = state.find(WEATHER, 1), state.find(WEATHER, 2)
    assert (app.disabled, app.favorite) == (True, True)
    assert (widget.disabled, widget.favorite) == (True, False)
    assert ciq.preview_in_bin(state, []).kept  # and the list written next keeps both as they are


def test_toggle_without_type_refuses_a_shared_id(twin_device):
    st, dev = twin_device
    with pytest.raises(ciq.CiqError, match="share this ID"):
        ciq.set_enabled(dev, st, WEATHER, True)
    assert not (st.root / "GARMIN/APPS/IN.BIN").exists()
    ciq.set_enabled(dev, st, PRELOADED, False)  # an ID used once needs no type
    assert (st.root / "GARMIN/APPS/IN.BIN").exists()


def test_cli_toggle_asks_for_the_type(twin_device, device_root, apps_cli, capsys):
    from openexpress.cli import main

    st, _dev = twin_device
    assert main(["--path", str(device_root), "apps", "disable", "Weather", "--apply"]) == 1
    err = capsys.readouterr().err
    assert "Several entries match: Weather (app); Weather (widget). Choose one with --type." in err
    assert not (st.root / "GARMIN/APPS/IN.BIN").exists()

    assert main(["--path", str(device_root), "apps", "disable", "Weather", "--type", "widget",
                 "--apply"]) == 0
    assert in_bin_flags(st)[1] == ("aa", 1, True) and in_bin_flags(st)[3] == ("aa", 2, True)
    assert main(["--path", str(device_root), "apps", "enable", "Weather", "--type", "app",
                 "--apply"]) == 0
    assert in_bin_flags(st)[1] == ("aa", 1, False) and in_bin_flags(st)[3] == ("aa", 2, True)
    # A name used once still needs no --type.
    assert main(["--path", str(device_root), "apps", "enable", "Softball"]) == 0


def test_window_toggle_passes_the_type(twin_device):
    import types

    from openexpress.gui.app import MainWindow

    st, dev = twin_device
    widget = ciq.read_installed(dev, st).find(WEATHER, 2)
    toasts = []
    win = types.SimpleNamespace(
        session=types.SimpleNamespace(device=dev, storage=st), busy=False, toast=toasts.append,
        run_task=RunNow())
    row = types.SimpleNamespace(get_active=lambda: False)  # the switch was turned off
    MainWindow.toggle_app(win, widget, row, 0)
    assert in_bin_flags(st)[1] == ("aa", 1, True) and in_bin_flags(st)[3] == ("aa", 2, True)
    assert widget.disabled is True and toasts == ["Weather disabled"]


# --- devices that hide the app folder: installing through a PTP session --------------------


class HidingStorage(LocalStorage):
    """A storage whose app folder only a PTP session shows. While the session is open the
    storage is "unmounted": any use of it is an error."""

    def __init__(self, root, fake):
        super().__init__(root)
        self.fake = fake
        self.unmounted = False
        self.sessions = 0
        self.open_error = None
        self.remount_error = None

    def _guard(self):
        from openexpress.device.storage import DeviceError

        if self.unmounted:
            raise DeviceError("the storage was used while unmounted")

    def _list(self, real_path):
        self._guard()
        return super()._list(real_path)

    def _read(self, real_path):
        self._guard()
        return super()._read(real_path)

    def _write(self, real_path, data):
        self._guard()
        return super()._write(real_path, data)

    def exclusive_ptp(self):
        return self._block() if self.fake is not None else None

    @contextlib.contextmanager
    def _block(self):
        from openexpress.device import ptp

        self.unmounted = True
        try:
            if self.open_error is not None:
                raise self.open_error
            self.fake.reconnect()
            with ptp.Session(self.fake) as session:
                yield session
        finally:
            self.unmounted = False
            self.sessions += 1
            if self.remount_error is not None:
                raise self.remount_error


@pytest.fixture
def hidden_apps(iq_device, device_root, xdg):
    """Like the fēnix 8: GARMIN/APPS/MEDIA is not in the ordinary listing."""
    from ptp_fake import FakeDevice

    st, dev = iq_device
    (device_root / "GARMIN/APPS/MEDIA").rmdir()
    fake = FakeDevice()
    fake.add("GARMIN/Apps/OUT.BIN", (device_root / "GARMIN/APPS/OUT.BIN").read_bytes())
    fake.add("GARMIN/Apps/Media", hidden=True)
    fake.add("GARMIN/Apps/Media/DADE7E4.PRG", b"store app", hidden=True)
    return HidingStorage(device_root, fake), dev, fake


def fake_in_bin(fake):
    handle = fake.handle("GARMIN/Apps/IN.BIN")
    return None if handle is None else ciq.parse_in_bin(fake.objects[handle].data)


def test_hidden_folder_is_installed_over_ptp(hidden_apps):
    st, dev, fake = hidden_apps
    assert ciq.usb_dir_state(dev, st, "IQWatchFaces") == ciq.DIRECT
    client = CountingClient()
    lines = []
    rep = ciq.install_queued(dev, st, client, [FACE_MESSAGE], known={}, progress=lines.append,
                             direct=True)
    assert (rep.installed, rep.failed, rep.for_phone) == (["Face It"], [], [])
    new = [n for n in fake.names("GARMIN/Apps/Media", hidden=False)]
    assert len(new) == 1 and new[0].endswith(".PRG") and len(new[0]) == 12
    assert fake.objects[fake.handle("GARMIN/Apps/Media/" + new[0])].data == b"PRG-DATA"
    assert fake.names("GARMIN/Apps/Media", hidden=True) == ["DADE7E4.PRG"]
    # The app list: everything the device has, in its order, and the new face last, inactive.
    assert [(p[0], p[2]) for p in fake_in_bin(fake)] == [
        (STORE_ID, False), (PRELOADED, True), (FACE, True)]
    assert client.acks == [7] and st.sessions == 1
    assert not fake.session_open and fake.closed  # closed before the device is mounted again
    # Nothing was created or written through the mounted storage.
    assert not (st.root / "GARMIN/APPS/MEDIA").exists()
    assert not (st.root / "GARMIN/APPS/IN.BIN").exists()
    assert any(line.startswith("Writing GARMIN/APPS/MEDIA/") for line in lines)


def test_install_apps_uses_ptp_without_trying_to_create_the_folder(hidden_apps, monkeypatch):
    st, dev, fake = hidden_apps
    monkeypatch.setattr(LocalStorage, "_mkdir", lambda self, p: pytest.fail("mkdir was tried"))
    FakeStore.queued = []
    client = CountingClient()
    client.device_messages = queue_once_requested
    rep = ciq.install_apps(dev, st, client, FakeStore(client), [store_app(FACE, "Face It")])
    assert rep.installed == ["Face It"] and rep.for_phone == [] and rep.failed == []
    assert FakeStore.queued == [["Face It"]]


def test_no_ptp_session_keeps_the_old_behaviour(hidden_apps, monkeypatch):
    """E.g. in a Flatpak sandbox: the folder is tried, refused, and the app only queued."""
    from openexpress.device.storage import DeviceError

    st, dev, fake = hidden_apps
    st.fake = None

    def refuse(self, real_path):
        raise DeviceError("refused")

    monkeypatch.setattr(LocalStorage, "_mkdir", refuse)
    assert ciq.usb_dir_state(dev, st, "IQWatchFaces") == ciq.MISSING
    client = CountingClient()
    rep = ciq.install_queued(dev, st, client, [FACE_MESSAGE], known={}, direct=True)
    assert rep.for_phone == ["Face It"] and client.downloads == 0 and client.acks == []
    assert fake.operations == []


def test_usb_device_cannot_be_opened(hidden_apps):
    from openexpress.device import ptp

    st, dev, fake = hidden_apps
    st.open_error = ptp.UsbUnavailable("no access")
    client = CountingClient()
    rep = ciq.install_queued(dev, st, client, [FACE_MESSAGE], known={}, direct=True)
    assert (rep.installed, rep.failed, rep.for_phone) == ([], [], ["Face It"])
    assert client.acks == [] and st.sessions == 1 and not st.unmounted  # mounted again
    assert fake.operations == []


def test_pending_app_list_blocks_a_ptp_install(hidden_apps):
    st, dev, fake = hidden_apps
    (st.root / "GARMIN/APPS/IN.BIN").write_bytes(ciq.build_in_bin(ciq.read_installed(dev, st).apps))
    client = CountingClient()
    rep = ciq.install_queued(dev, st, client, [FACE_MESSAGE], known={}, direct=True)
    assert rep.installed == [] and "has not applied earlier changes" in rep.failed[0][1]
    assert client.acks == [] and st.sessions == 0 and fake.operations == []


def test_refused_app_file_writes_no_app_list(hidden_apps):
    from openexpress.device import ptp

    st, dev, fake = hidden_apps
    fake.refuse[ptp.SEND_OBJECT_INFO] = ptp.ACCESS_DENIED
    client = CountingClient()
    rep = ciq.install_queued(dev, st, client, [FACE_MESSAGE], known={}, direct=True)
    assert rep.installed == [] and rep.failed == [
        ("Face It", "The device refused SendObjectInfo (AccessDenied).")]
    assert fake_in_bin(fake) is None and client.acks == []
    assert fake.names("GARMIN/Apps/Media") == ["DADE7E4.PRG"]
    assert not fake.session_open and not st.unmounted


def test_unfinished_app_file_is_removed(hidden_apps):
    from openexpress.device import ptp

    st, dev, fake = hidden_apps
    fake.refuse[ptp.SEND_OBJECT] = ptp.GENERAL_ERROR
    rep = ciq.install_queued(dev, st, CountingClient(), [FACE_MESSAGE], known={}, direct=True)
    assert rep.installed == [] and "GeneralError" in rep.failed[0][1]
    assert fake.names("GARMIN/Apps/Media") == ["DADE7E4.PRG"] and fake_in_bin(fake) is None


def test_refused_app_list_removes_the_new_app_file(hidden_apps):
    st, dev, fake = hidden_apps
    fake.refuse_names["IN.BIN"] = 0x200F
    client = CountingClient()
    with pytest.raises(ciq.CiqError, match="Cannot register the apps"):
        ciq.install_queued(dev, st, client, [FACE_MESSAGE], known={}, direct=True)
    assert fake.names("GARMIN/Apps/Media") == ["DADE7E4.PRG"]
    assert client.acks == [] and not fake.session_open and not st.unmounted


def test_one_refused_app_does_not_stop_the_other(hidden_apps):
    st, dev, fake = hidden_apps
    other = uuid.UUID("11111111-2222-4333-8444-555555555555")
    second = {"messageid": 8, "devicexmldatatype": "IQWatchFaces",
              "metadata": {"messageurl": "y", "messagename": "Other",
                           "appdetails": {"id": str(other)}}}
    real_handle = fake._send_object_info
    calls = []

    def first_refused(params, data):
        calls.append(1)
        return (0x2002,) if len(calls) == 1 else real_handle(params, data)

    fake._send_object_info = first_refused
    client = CountingClient()
    rep = ciq.install_queued(dev, st, client, [FACE_MESSAGE, second], known={}, direct=True)
    assert rep.installed == ["Other"] and [n for n, _e in rep.failed] == ["Face It"]
    assert [p[0] for p in fake_in_bin(fake)] == [STORE_ID, PRELOADED, other]
    assert client.acks == [8]


def test_broken_session_reports_every_app(hidden_apps):
    st, dev, fake = hidden_apps
    fake.wrong_transaction = True
    client = CountingClient()
    rep = ciq.install_queued(dev, st, client, [FACE_MESSAGE], known={}, direct=True)
    assert rep.installed == [] and [n for n, _e in rep.failed] == ["Face It"]
    assert client.acks == [] and not st.unmounted


def test_device_without_garmins_listing_falls_back(hidden_apps, device_root):
    """No hidden folder is found: the folder is created the old way, if the device lets us."""
    st, dev, fake = hidden_apps
    fake.supports_hidden = False
    client = CountingClient()
    rep = ciq.install_queued(dev, st, client, [FACE_MESSAGE], known={}, direct=True)
    assert rep.installed == ["Face It"] and rep.failed == []
    assert len(list((device_root / "GARMIN/APPS/MEDIA").glob("*.PRG"))) == 1
    assert (device_root / "GARMIN/APPS/IN.BIN").exists() and fake_in_bin(fake) is None
    assert fake.sent(0x100C) == 0 and client.acks == [7]


def test_failed_remount_is_reported_but_the_install_counts(hidden_apps):
    from openexpress.device.storage import RemountFailed

    st, dev, fake = hidden_apps
    st.remount_error = RemountFailed("Cannot mount the device.")
    client = CountingClient()
    rep = ciq.install_queued(dev, st, client, [FACE_MESSAGE], known={}, direct=True)
    assert rep.installed == ["Face It"] and client.acks == [7]
    assert rep.failed == [("USB connection",
                           "Cannot mount the device. Unplug the device and plug it in again.")]
    assert fake_in_bin(fake) is not None


NOT_BACK = ("The device did not come back after an unmount that did not finish in time. "
            "Unplug it and plug it in again.")


def test_a_device_not_back_is_told_to_be_replugged_once(hidden_apps):
    """The unmount for the session did not finish in time and the device did not come back
    (step 4 after the audit of 0.7.9, A8, §10): its error says what to do; the app waits for
    the phone, as when no session could be had."""
    from openexpress.device import gio_storage

    st, dev, fake = hidden_apps
    st.open_error = gio_storage.DeviceNotBack(NOT_BACK)
    rep = ciq.install_queued(dev, st, CountingClient(), [FACE_MESSAGE], known={}, direct=True)
    assert rep.failed == [("USB connection", NOT_BACK)]
    assert rep.for_phone == ["Face It"]


OTHER_APP = uuid.UUID("11111111-2222-4333-8444-555555555555")
OTHER_MESSAGE = {"messageid": 8, "devicexmldatatype": "IQWatchFaces",
                 "metadata": {"messageurl": "y", "messagename": "Other",
                              "appdetails": {"id": str(OTHER_APP)}}}
REMOUNT = ("USB connection", "Cannot mount the device. Unplug the device and plug it in again.")


def break_at_app_folder(monkeypatch, fake, n):
    """The cable goes out as the session looks for the folder of the `n`-th app: every
    answer of the device after that is wrong (the session breaks)."""
    from openexpress.device import ptp

    real, seen = ptp.Session.lookup, []

    def lookup(self, storage, path):
        if path.casefold().endswith("/media"):
            seen.append(path)
            if len(seen) == n:
                fake.wrong_transaction = True
        return real(self, storage, path)

    monkeypatch.setattr(ptp.Session, "lookup", lookup)


def test_a_broken_session_then_a_failed_mount_still_tells_each_app(hidden_apps, monkeypatch):
    """The session breaks after the first app was sent, then the device is not mounted
    again: the report says what became of each app (sent and not registered, with the file
    left; not sent), and only then that the USB connection is to be renewed (decision of
    23.10.2026; before, the failed mount hid the rest)."""
    from openexpress.device.storage import RemountFailed

    st, dev, fake = hidden_apps
    st.remount_error = RemountFailed("Cannot mount the device.")
    break_at_app_folder(monkeypatch, fake, 2)
    client = CountingClient()
    rep = ciq.install_queued(dev, st, client, [FACE_MESSAGE, OTHER_MESSAGE], known={},
                             direct=True)
    assert rep.installed == [] and client.acks == []
    assert [name for name, _e in rep.failed] == ["Face It", "Other", "USB connection"]
    (_n, sent), (_o, not_sent), remount = rep.failed
    assert "An unfinished file may have been left on the device: GARMIN/APPS/MEDIA/" in sent
    assert "left on the device" not in not_sent and remount == REMOUNT


def test_cli_tells_each_app_before_the_usb_connection(hidden_apps, apps_cli, monkeypatch,
                                                      capsys):
    from openexpress import cli
    from openexpress.cli import main
    from openexpress.device.storage import RemountFailed

    st, dev, fake = hidden_apps
    monkeypatch.setattr(cli, "open_device", lambda args: (st, dev))
    st.remount_error = RemountFailed("Cannot mount the device.")
    break_at_app_folder(monkeypatch, fake, 1)
    assert main(["--path", str(st.root), "apps", "install", "Face It", "--apply"]) == 1
    errors = [line.strip() for line in capsys.readouterr().out.splitlines()
              if line.strip().startswith("ERROR")]
    assert [line.split(":")[0] for line in errors] == ["ERROR Face It", "ERROR USB connection"]
    assert errors[-1] == "ERROR USB connection: " + REMOUNT[1]


@pytest.mark.parametrize("broken", [True, False])
def test_window_tells_each_app_before_the_usb_connection(hidden_apps, monkeypatch, broken):
    """The window's error says what was written, then each app that was not, then the USB
    connection; with the session whole and only the mount failed, that the app was written
    (before, only the USB connection was said)."""
    import types

    from openexpress import service
    from openexpress.device.storage import RemountFailed
    from openexpress.gui.app import MainWindow

    st, dev, fake = hidden_apps
    st.remount_error = RemountFailed("Cannot mount the device.")
    if broken:
        break_at_app_folder(monkeypatch, fake, 1)
    monkeypatch.setattr(service, "require_account_device", lambda client, unit_id: [])
    monkeypatch.setattr(ciq, "StoreApi", FakeStore)
    FakeStore.queued = []
    client = CountingClient()
    client.device_messages = lambda unit_id: [FACE_MESSAGE]
    errors = []
    win = types.SimpleNamespace(
        session=types.SimpleNamespace(device=dev, storage=st, client=client),
        _updated_apps=set(), toast=lambda text: pytest.fail(text), progress=lambda text: None,
        error=lambda title, text: errors.append((title, text)),
        load_section=lambda key, force=False: None,
        run_task=RunNow())
    win._install_apps = lambda apps, allowed=(): MainWindow._install_apps(win, apps, allowed)
    MainWindow.install_apps(win, [store_app(FACE, "Face It")])
    ((title, text),) = errors
    lines = text.splitlines()
    assert title == "Connect IQ" and lines[-1] == ": ".join(REMOUNT)
    if broken:
        assert len(lines) == 2 and lines[0].startswith("Face It: ")
    else:
        assert lines == [("Wrote Face It to the device; it will be installed after the device "
                          "is unplugged."), ": ".join(REMOUNT)]


def test_interrupted_sync_writes_nothing_over_ptp(hidden_apps):
    from openexpress.connect import ApiError

    st, dev, fake = hidden_apps

    class Client(CountingClient):
        def download_message(self, url, absolute):
            if url == "boom":
                raise ApiError("server error")
            return super().download_message(url, absolute)

    bad = {"messageid": 8, "devicexmldatatype": "IQWatchFaces",
           "metadata": {"messageurl": "boom", "messagename": "Other",
                        "appdetails": {"id": "11111111-2222-4333-8444-555555555555"}}}
    client = Client()
    with pytest.raises(ApiError):
        ciq.install_queued(dev, st, client, [FACE_MESSAGE, bad], known={}, direct=True)
    assert st.sessions == 0 and fake.operations == [] and client.acks == []


class GoneClient(CountingClient):
    """Acknowledging fails; `queue` is what Garmin still lists afterwards."""

    def __init__(self, queue):
        super().__init__()
        self.queue = queue

    def ack_message(self, mid):
        raise RuntimeError("HTTP 404")

    def device_messages(self, unit_id):
        return self.queue


def test_message_already_gone_from_the_queue_is_not_an_error(hidden_apps):
    st, dev, _fake = hidden_apps
    rep = ciq.install_queued(dev, st, GoneClient([]), [FACE_MESSAGE], known={}, direct=True)
    assert rep.installed == ["Face It"] and rep.failed == []


def test_failed_acknowledgement_of_a_queued_message_is_reported(hidden_apps):
    st, dev, _fake = hidden_apps
    rep = ciq.install_queued(dev, st, GoneClient([FACE_MESSAGE]), [FACE_MESSAGE], known={}, direct=True)
    assert rep.installed == [] and rep.failed == [("Face It", "HTTP 404")]


def test_cli_dry_run_announces_the_direct_connection(hidden_apps, apps_cli, monkeypatch, capsys):
    from openexpress import cli
    from openexpress.cli import main

    st, _dev, fake = hidden_apps
    monkeypatch.setattr(cli, "open_device", lambda args: (st, _dev))
    assert main(["--path", str(st.root), "apps", "install", "Face It"]) == 0
    out = capsys.readouterr().out
    assert "does not show the app folder GARMIN/APPS/MEDIA" in out
    assert "direct USB connection" in out and "Dry run" in out
    assert fake.operations == [] and st.sessions == 0 and FakeStore.queued == []


def test_sync_does_not_use_ptp(hidden_apps, monkeypatch):
    """Only an install the user asked for unmounts the device; a sync leaves the app queued."""
    import types

    from openexpress import service
    from openexpress.device.storage import DeviceError

    st, dev, fake = hidden_apps

    def refuse(self, real_path):
        raise DeviceError("refused")

    monkeypatch.setattr(LocalStorage, "_mkdir", refuse)

    class Client(CountingClient):
        def require_verified(self, unit_id=None):
            raise RuntimeError("names are cosmetic")

    client = Client()
    s = types.SimpleNamespace(storage=st, device=dev, client=client,
                              settings=types.SimpleNamespace(locale="en_US"))
    errors = []
    lines = step_words(
        service._install_queued_apps(s, [FACE_MESSAGE], lambda m: None, errors))
    assert errors == [] and lines == [
        ("1 Connect IQ app waits in the queue: openExpress cannot install apps on "
         "this device over USB yet")]
    assert st.sessions == 0 and fake.operations == [] and client.acks == []
    rep = ciq.install_queued(dev, st, client, [FACE_MESSAGE], known={})  # the default
    assert rep.for_phone == ["Face It"] and st.sessions == 0


def test_user_is_told_about_an_unfinished_file_that_stays(hidden_apps):
    from openexpress.device import ptp

    st, dev, fake = hidden_apps
    fake.refuse[ptp.SEND_OBJECT] = ptp.GENERAL_ERROR
    fake.refuse[ptp.DELETE_OBJECT] = ptp.ACCESS_DENIED
    rep = ciq.install_queued(dev, st, CountingClient(), [FACE_MESSAGE], known={}, direct=True)
    name, error = rep.failed[0]
    left = fake.names("GARMIN/Apps/Media", hidden=False)
    assert name == "Face It" and len(left) == 1
    assert error.endswith("An unfinished file may have been left on the device: "
                          f"GARMIN/APPS/MEDIA/{left[0]}.")


def test_user_is_told_when_the_app_file_stays_after_a_refused_app_list(hidden_apps):
    from openexpress.device import ptp

    st, dev, fake = hidden_apps
    fake.refuse_names["IN.BIN"] = ptp.ACCESS_DENIED
    fake.refuse[ptp.DELETE_OBJECT] = ptp.ACCESS_DENIED
    with pytest.raises(ciq.CiqError) as error:
        ciq.install_queued(dev, st, CountingClient(), [FACE_MESSAGE], known={}, direct=True)
    left = fake.names("GARMIN/Apps/Media", hidden=False)
    assert f"may have been left on the device: GARMIN/APPS/MEDIA/{left[0]}." in str(error.value)


# --- an app that already waits in Garmin's queue is not requested again ---------------------


def test_install_uses_the_message_already_in_the_queue(iq_device):
    st, dev = iq_device
    FakeStore.queued = []
    client = CountingClient()
    reads = []
    client.device_messages = lambda unit_id: reads.append(1) or [FACE_MESSAGE]
    rep = ciq.install_apps(dev, st, client, FakeStore(client), [store_app(FACE, "Face It")])
    assert FakeStore.queued == []  # no second request to Garmin
    assert rep.installed == ["Face It"] and client.acks == [7] and len(reads) == 1


def test_only_the_apps_not_yet_queued_are_requested(iq_device):
    st, dev = iq_device
    other = uuid.UUID("11111111-2222-4333-8444-555555555555")
    second = {"messageid": 8, "devicexmldatatype": "IQWatchFaces",
              "metadata": {"messageurl": "y", "messagename": "Other",
                           "appdetails": {"id": str(other)}}}
    FakeStore.queued = []
    client = CountingClient()
    client.device_messages = lambda unit_id: ([FACE_MESSAGE, second] if FakeStore.queued
                                              else [FACE_MESSAGE])
    rep = ciq.install_apps(dev, st, client, FakeStore(client),
                           [store_app(FACE, "Face It"), store_app(other, "Other")])
    assert FakeStore.queued == [["Other"]]
    assert sorted(rep.installed) == ["Face It", "Other"] and sorted(client.acks) == [7, 8]


def test_queued_app_for_the_phone_is_not_requested_again(no_usb_apps):
    st, dev, _attempts = no_usb_apps
    FakeStore.queued = []
    client = CountingClient()
    client.device_messages = lambda unit_id: [FACE_MESSAGE]
    rep = ciq.install_apps(dev, st, client, FakeStore(client), [store_app(FACE, "Face It")])
    assert FakeStore.queued == [] and rep.for_phone == ["Face It"] and client.downloads == 0


def test_cli_dry_run_says_the_app_already_waits(iq_device, device_root, apps_cli, capsys):
    from openexpress.cli import main

    apps_cli.device_messages = lambda unit_id: [FACE_MESSAGE]
    assert main(["--path", str(device_root), "apps", "install", "Face It"]) == 0
    out = capsys.readouterr().out
    assert ("  Face It: already waits in the queue in Garmin Connect; no new request will be "
            "sent.") in out
    assert main(["--path", str(device_root), "apps", "install", "Face It", "--apply"]) == 0
    assert "Installed: Face It" in capsys.readouterr().out
    assert FakeStore.queued == [] and apps_cli.acks == [7]


# --- removing an app from a folder the device hides ---------------------------------------


@pytest.fixture
def hidden_spot(hidden_apps, device_root):
    """A store app the device took over: listed in OUT.BIN, its file only visible over PTP."""
    st, dev, fake = hidden_apps
    data = out_bin([
        (STORE_ID, 1, "Connect IQ Store", False, "DADE7E4"),
        (FACE, 3, "Face It", True, "0A1B2C3D"),
        (PRELOADED, 8, "Softball", True, ""),
    ])
    (device_root / "GARMIN/APPS/OUT.BIN").write_bytes(data)
    fake.objects[fake.handle("GARMIN/Apps/OUT.BIN")].data = data
    fake.add("GARMIN/Apps/Media/0A1B2C3D.PRG", b"face app", hidden=True)
    return st, dev, fake, ciq.read_installed(dev, st).find(FACE)


def test_hidden_app_is_removed_like_express_does(hidden_spot):
    from openexpress.device import ptp

    st, dev, fake, app = hidden_spot
    assert ciq.files_on_device(dev, st, app) == [] and ciq.removable_directly(dev, st, app)
    assert ciq.remove(dev, st, app) == ["GARMIN/APPS/MEDIA/0A1B2C3D.PRG"]
    assert fake.names("GARMIN/Apps/Media") == ["DADE7E4.PRG"]
    # The app list: the same entries in the same order with the same flags, minus the app.
    assert [(p[0], p[1], p[2]) for p in fake_in_bin(fake)] == [
        (STORE_ID, 1, False), (PRELOADED, 8, True)]
    # The file is deleted first, the list written after it; then the device is mounted again.
    writes = [op for op in fake.operations if op in (ptp.DELETE_OBJECT, ptp.SEND_OBJECT_INFO)]
    assert writes == [ptp.DELETE_OBJECT, ptp.SEND_OBJECT_INFO]
    assert st.sessions == 1 and not st.unmounted and not fake.session_open
    assert not (st.root / "GARMIN/APPS/IN.BIN").exists()  # nothing through the mounted storage


SETTINGS_FILE = "GARMIN/APPS/SETTINGS/0A1B2C3D.SET"


def test_a_hidden_app_is_removed_with_its_settings_file(hidden_spot):
    """As over the mounted storage and as Garmin Express: the settings file of the app goes
    too (its folder and extension from IQAppsSettingsFile, the name of the app's file). It
    is deleted once the app list is written, so that a session that breaks at it leaves no
    app listed without its file."""
    from openexpress.device import ptp

    st, dev, fake, app = hidden_spot
    fake.add("GARMIN/Apps/SETTINGS/0A1B2C3D.SET", b"app settings", hidden=True)
    assert ciq.remove(dev, st, app) == ["GARMIN/APPS/MEDIA/0A1B2C3D.PRG", SETTINGS_FILE]
    assert fake.names("GARMIN/Apps/SETTINGS") == []
    assert [(p[0], p[1]) for p in fake_in_bin(fake)] == [(STORE_ID, 1), (PRELOADED, 8)]
    writes = [op for op in fake.operations if op in (ptp.DELETE_OBJECT, ptp.SEND_OBJECT_INFO)]
    assert writes == [ptp.DELETE_OBJECT, ptp.SEND_OBJECT_INFO, ptp.DELETE_OBJECT]
    assert st.sessions == 1


def test_a_visible_settings_file_does_not_send_the_removal_through_the_mounted_storage(
        hidden_spot):
    """The app file decides the way: a settings file the ordinary listing shows used to make
    the removal delete that file alone, leave the app on the device and say it was removed."""
    st, dev, fake, app = hidden_spot
    (st.root / "GARMIN/APPS/SETTINGS").mkdir(parents=True, exist_ok=True)
    (st.root / SETTINGS_FILE).write_bytes(b"app settings")
    fake.add("GARMIN/Apps/SETTINGS/0A1B2C3D.SET", b"app settings")
    assert ciq.remove(dev, st, app) == ["GARMIN/APPS/MEDIA/0A1B2C3D.PRG", SETTINGS_FILE]
    assert st.sessions == 1
    assert fake.names("GARMIN/Apps/Media") == ["DADE7E4.PRG"]
    assert fake.names("GARMIN/Apps/SETTINGS") == []
    assert [(p[0], p[1]) for p in fake_in_bin(fake)] == [(STORE_ID, 1), (PRELOADED, 8)]


def test_a_refused_settings_file_still_leaves_the_app_removed(hidden_spot, caplog):
    from openexpress.device import ptp

    st, dev, fake, app = hidden_spot
    fake.add("GARMIN/Apps/SETTINGS/0A1B2C3D.SET", b"app settings", hidden=True)
    fake.refuse_delete_names["0A1B2C3D.SET"] = ptp.ACCESS_DENIED
    with caplog.at_level(logging.WARNING, logger="openexpress.ciq"):
        assert ciq.remove(dev, st, app) == ["GARMIN/APPS/MEDIA/0A1B2C3D.PRG"]
    assert fake.names("GARMIN/Apps/Media") == ["DADE7E4.PRG"]
    assert [(p[0], p[1]) for p in fake_in_bin(fake)] == [(STORE_ID, 1), (PRELOADED, 8)]
    assert fake.names("GARMIN/Apps/SETTINGS") == ["0A1B2C3D.SET"]
    assert any("settings file" in r.getMessage() and r.levelno == logging.WARNING
               for r in caplog.records)


def test_the_way_of_a_removal_is_told_by_the_app_file_alone(iq_device, device_root, hidden_spot):
    """The window and the command line ask the same function."""
    st, dev, _fake, app = hidden_spot
    assert ciq.removal_way(dev, st, app) == ciq.DIRECT
    (st.root / "GARMIN/APPS/SETTINGS").mkdir(parents=True, exist_ok=True)
    (st.root / SETTINGS_FILE).write_bytes(b"app settings")
    assert ciq.removal_way(dev, st, app) == ciq.DIRECT  # the settings file changes nothing


def test_removal_preview_keeps_every_other_app(hidden_spot):
    st, dev, fake, app = hidden_spot
    p = ciq.preview_removal(ciq.read_installed(dev, st), app)
    assert (p.on_device, p.pending, p.entries, p.kept) == (3, 0, 2, True)
    assert fake.operations == [] and st.sessions == 0


def test_removal_is_refused_while_an_app_list_waits(hidden_spot):
    st, dev, fake, app = hidden_spot
    (st.root / "GARMIN/APPS/IN.BIN").write_bytes(ciq.build_in_bin(ciq.read_installed(dev, st).apps))
    with pytest.raises(ciq.CiqError, match="has not applied earlier changes"):
        ciq.remove(dev, st, app)
    assert st.sessions == 0 and fake.operations == []


def test_refused_delete_changes_nothing(hidden_spot):
    from openexpress.device import ptp

    st, dev, fake, app = hidden_spot
    fake.delete_protected = ptp.OBJECT_WRITE_PROTECTED
    with pytest.raises(ciq.CiqError) as error:
        ciq.remove(dev, st, app)
    assert str(error.value) == ("The device refused to delete GARMIN/APPS/MEDIA/0A1B2C3D.PRG "
                                "(ObjectWriteProtected); nothing was changed.")
    assert fake.names("GARMIN/Apps/Media") == ["0A1B2C3D.PRG", "DADE7E4.PRG"]
    assert fake_in_bin(fake) is None and not st.unmounted


def test_file_of_another_size_is_not_deleted(hidden_spot):
    from openexpress.device import ptp

    st, dev, fake, app = hidden_spot
    app.size = 12345  # the app list describes another file
    listed = ciq.read_installed(dev, st)
    listed.find(FACE).size = 12345
    import unittest.mock

    with unittest.mock.patch.object(ciq, "read_installed", return_value=listed), \
            pytest.raises(ciq.CiqError, match="is not the file the app list describes"):
        ciq.remove(dev, st, app)
    assert fake.sent(ptp.DELETE_OBJECT) == 0 and fake_in_bin(fake) is None


def test_deleted_file_but_refused_app_list_is_explained(hidden_spot):
    from openexpress.device import ptp

    st, dev, fake, app = hidden_spot
    fake.refuse_names["IN.BIN"] = ptp.ACCESS_DENIED
    with pytest.raises(ciq.CiqError) as error:
        ciq.remove(dev, st, app)
    text = str(error.value)
    assert text.startswith("The file of Face It was deleted, but the app list on the device "
                           "could not be updated: The device refused SendObjectInfo")
    assert "remove the app again to update the list" in text
    assert fake.names("GARMIN/Apps/Media") == ["DADE7E4.PRG"] and not st.unmounted

    # The second attempt finds no file and only puts the list right.
    del fake.refuse_names["IN.BIN"]
    assert ciq.remove(dev, st, app) == []
    assert [p[0] for p in fake_in_bin(fake)] == [STORE_ID, PRELOADED]
    assert fake.sent(ptp.DELETE_OBJECT) == 1


def test_unfinished_app_list_is_removed_again(hidden_spot):
    from openexpress.device import ptp

    st, dev, fake, app = hidden_spot
    fake.refuse[ptp.SEND_OBJECT] = ptp.GENERAL_ERROR
    with pytest.raises(ciq.CiqError, match="could not be updated"):
        ciq.remove(dev, st, app)
    assert fake.handle("GARMIN/Apps/IN.BIN") is None  # no half-written list is left behind


def test_failed_remount_after_a_removal(hidden_spot):
    from openexpress.device.storage import RemountFailed

    st, dev, fake, app = hidden_spot
    st.remount_error = RemountFailed("Cannot mount the device.")
    with pytest.raises(ciq.CiqError) as error:
        ciq.remove(dev, st, app)
    assert str(error.value) == ("Face It was removed, but the device could not be mounted again: "
                                "Cannot mount the device. Unplug the device and plug it in again.")
    assert fake_in_bin(fake) is not None


def test_a_removal_whose_device_did_not_come_back_says_it_once(hidden_spot):
    from openexpress.device import gio_storage

    st, dev, fake, app = hidden_spot
    st.open_error = gio_storage.DeviceNotBack(NOT_BACK)
    with pytest.raises(ciq.CiqError) as error:
        ciq.remove(dev, st, app)
    assert str(error.value) == NOT_BACK


def break_removal(monkeypatch, fake, where):
    """The cable goes out during a removal over PTP, at `where`: every answer of the device
    after that is wrong (the session breaks)."""
    from openexpress.device import ptp

    lookup, delete, send_file, seen = (ptp.Session.lookup, ptp.Session.delete,
                                       ptp.Session.send_file, [])

    def looking(self, storage, path):
        if path.upper().endswith("0A1B2C3D.PRG"):
            seen.append(path)
            # The first look for the file comes before its delete, the second after it.
            if (where, len(seen)) in (("before the delete", 1), ("after the delete", 2)):
                fake.wrong_transaction = True
        return lookup(self, storage, path)

    def deleting(self, handle):
        if where == "during the delete":
            fake.wrong_transaction = True  # the device deletes it, its answer is lost
        return delete(self, handle)

    def sending(self, storage, parent, name, data, verify_content=False):
        if where == "at the app list":
            fake.wrong_transaction = True
        return send_file(self, storage, parent, name, data, verify_content=verify_content)

    monkeypatch.setattr(ptp.Session, "lookup", looking)
    monkeypatch.setattr(ptp.Session, "delete", deleting)
    monkeypatch.setattr(ptp.Session, "send_file", sending)


NOT_REMOVED = "Face It was not removed: "
NOT_KNOWN = "It is not known whether the file of Face It was deleted: "
LIST_LEFT = ("The file of Face It was deleted, but the app list on the device could not be "
             "updated: ")
BROKEN_REMOVALS = [("before the delete", NOT_REMOVED, True),
                   ("during the delete", NOT_KNOWN, False),
                   ("after the delete", LIST_LEFT, False),
                   ("at the app list", LIST_LEFT, False)]


@pytest.mark.parametrize(("where", "said", "file_left"), BROKEN_REMOVALS)
def test_a_broken_removal_then_a_failed_mount_tells_what_became_of_the_app(
        hidden_spot, monkeypatch, where, said, file_left):
    """The session breaks, then the device is not mounted again: what became of the app
    first (not removed; not known whether its file was deleted; its file deleted and its
    entry left), then the USB connection to renew (decision of 23.10.2026; before, the
    failed mount hid the rest, or the rest hid the failed mount)."""
    from openexpress.device.storage import RemountFailed

    st, dev, fake, app = hidden_spot
    st.remount_error = RemountFailed("Cannot mount the device.")
    break_removal(monkeypatch, fake, where)
    with pytest.raises(ciq.CiqError) as error:
        ciq.remove(dev, st, app)
    lines = str(error.value).splitlines()
    assert len(lines) == 2 and lines[0].startswith(said) and lines[1] == REMOUNT[1]
    assert ("0A1B2C3D.PRG" in fake.names("GARMIN/Apps/Media")) is file_left


@pytest.mark.parametrize(("where", "said", "file_left"), BROKEN_REMOVALS)
def test_a_broken_removal_tells_what_became_of_the_app(hidden_spot, monkeypatch, where,
                                                       said, file_left):
    """Mounted again after the break: what became of the app, and nothing of the USB."""
    st, dev, fake, app = hidden_spot
    break_removal(monkeypatch, fake, where)
    with pytest.raises(ciq.CiqError) as error:
        ciq.remove(dev, st, app)
    assert str(error.value).startswith(said) and "\n" not in str(error.value)
    assert not st.unmounted


def test_cli_tells_the_removal_before_the_usb_connection(hidden_spot, apps_cli, monkeypatch,
                                                         capsys):
    from openexpress import cli
    from openexpress.cli import main
    from openexpress.device.storage import RemountFailed

    st, dev, fake, _app = hidden_spot
    monkeypatch.setattr(cli, "open_device", lambda args: (st, dev))
    st.remount_error = RemountFailed("Cannot mount the device.")
    break_removal(monkeypatch, fake, "before the delete")
    assert main(["--path", str(st.root), "apps", "remove", "Face It", "--apply"]) == 1
    err = capsys.readouterr().err.splitlines()
    assert err[-2].startswith("Error: " + NOT_REMOVED) and err[-1] == REMOUNT[1]


def test_window_tells_the_removal_before_the_usb_connection(hidden_spot, monkeypatch):
    import types

    from openexpress.device.storage import RemountFailed
    from openexpress.gui import app as gui
    from openexpress.gui.app import MainWindow

    st, dev, fake, app = hidden_spot
    st.remount_error = RemountFailed("Cannot mount the device.")
    break_removal(monkeypatch, fake, "after the delete")

    class Answering:
        def __init__(self, **kw):
            pass

        def connect(self, signal, handler):
            handler(self, "ok")

        def get_response_enabled(self, response):
            return True

        def __getattr__(self, name):
            return lambda *a, **kw: None

    monkeypatch.setattr(gui.Adw, "AlertDialog", Answering)
    errors, reads = [], []

    run_task = RunNow(error=lambda title, text: errors.append((title, text)))

    win = types.SimpleNamespace(
        session=types.SimpleNamespace(device=dev, storage=st), toast=lambda text: None,
        error=lambda title, text: errors.append((title, text)), run_task=run_task,
        load_section=lambda key, force=False: reads.append(key))
    MainWindow.confirm_remove_app(win, ciq.read_installed(dev, st).find(FACE))
    ((title, text),) = errors
    lines = text.splitlines()
    assert title == "Removing Face It…" and len(lines) == 2
    assert lines[0].startswith(LIST_LEFT) and lines[1] == REMOUNT[1]
    assert reads == ["apps"]  # the page is read again (read_again_after_failure)


# --- an error without a full stop before the next sentence (review of 24.10.2026) ----------------

TIMED_OUT = "Cannot mount the device: the device did not answer in time"  # as _wait says it


@pytest.mark.parametrize(("error", "expected"), [
    (TIMED_OUT, TIMED_OUT + "."),
    ("It went wrong.", "It went wrong."),          # a full stop already: not a second one
    ("Refused (AccessDenied)", "Refused (AccessDenied)."),
    ("Is it there?", "Is it there?"),
    ("Wait…", "Wait…"),
])
def test_an_error_ends_its_sentence_before_the_next_one(error, expected):
    from openexpress.device.storage import DeviceError

    assert ciq._sentence(DeviceError(error)) == expected


def test_the_leftovers_come_in_a_sentence_of_their_own():
    from openexpress.device.storage import DeviceError

    text = ciq._with_leftovers(DeviceError("Cannot write it: the device did not answer in time"),
                               ["GARMIN/APPS/MEDIA/A1B2C3D4.PRG"])
    assert text == ("Cannot write it: the device did not answer in time. An unfinished file "
                    "may have been left on the device: GARMIN/APPS/MEDIA/A1B2C3D4.PRG.")


def test_a_failed_mount_after_a_removal_ends_its_sentence(hidden_spot):
    from openexpress.device.storage import RemountFailed

    st, dev, fake, app = hidden_spot
    st.remount_error = RemountFailed(TIMED_OUT)
    with pytest.raises(ciq.CiqError) as error:
        ciq.remove(dev, st, app)
    assert str(error.value) == ("Face It was removed, but the device could not be mounted again: "
                                f"{TIMED_OUT}. Unplug the device and plug it in again.")


def test_a_failed_mount_after_an_install_ends_its_sentence(hidden_apps):
    from openexpress.device.storage import RemountFailed

    st, dev, fake = hidden_apps
    st.remount_error = RemountFailed(TIMED_OUT)
    rep = ciq.install_queued(dev, st, CountingClient(), [FACE_MESSAGE], known={}, direct=True)
    assert rep.failed == [("USB connection",
                           f"{TIMED_OUT}. Unplug the device and plug it in again.")]


def test_a_broken_removal_ends_the_sentence_of_its_error(hidden_spot, monkeypatch):
    """The file deleted and the list not written, the error without a full stop."""
    from openexpress.device import ptp

    st, dev, fake, app = hidden_spot

    def broken_list(self, storage, parent, name, data, verify_content=False):
        raise ptp.PtpError("Sending the list failed: the device did not answer in time")

    monkeypatch.setattr(ptp.Session, "send_file", broken_list)
    with pytest.raises(ciq.CiqError) as error:
        ciq.remove(dev, st, app)
    assert str(error.value).startswith(
        "The file of Face It was deleted, but the app list on the device could not be updated: "
        "Sending the list failed: the device did not answer in time. The device may still list")


def test_the_full_stop_comes_in_polish_too(hidden_spot):
    from openexpress import i18n
    from openexpress.device.storage import RemountFailed

    st, dev, fake, app = hidden_spot
    st.remount_error = RemountFailed("Nie można zamontować urządzenia: urządzenie nie "
                                     "odpowiedziało na czas")
    i18n.setup("pl")
    try:
        with pytest.raises(ciq.CiqError) as error:
            ciq.remove(dev, st, app)
    finally:
        i18n.setup("en")
    assert "nie odpowiedziało na czas. Odłącz urządzenie i\u00a0podłącz je ponownie." in str(
        error.value)


def test_built_in_and_fileless_apps_are_not_removed_directly(hidden_spot):
    st, dev, fake, _app = hidden_spot
    state = ciq.read_installed(dev, st)
    assert not ciq.removable_directly(dev, st, state.find(PRELOADED))  # no file of its own
    with pytest.raises(ciq.CiqError, match="part of the device software"):
        ciq.remove(dev, st, state.find(PRELOADED))
    assert fake.operations == []


def test_cli_removes_a_hidden_app(hidden_spot, apps_cli, monkeypatch, capsys):
    from openexpress import cli
    from openexpress.cli import main

    st, dev, fake, _app = hidden_spot
    monkeypatch.setattr(cli, "open_device", lambda args: (st, dev))
    assert main(["--path", str(st.root), "apps", "remove", "Face It"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "Remove Face It from the device: GARMIN/APPS/MEDIA/0A1B2C3D.PRG"
    assert out[1] == ("  and its settings file, if the device has one: "
                      "GARMIN/APPS/SETTINGS/0A1B2C3D.SET")
    assert out[3:] == [
        "App list to be written (IN.BIN): 2 entries; the device lists 3 (OUT.BIN)",
        "  every other app on the device is kept, in the same order and with the same flags",
        "  to be removed: Face It (watch face)",
        "Before any change, a copy of the app list of the device is made.",
        "Dry run. Add --apply.",
    ]
    assert fake.operations == [] and st.sessions == 0

    assert main(["--path", str(st.root), "apps", "remove", "Face It", "--apply"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert "  to be removed: Face It (watch face)" in out  # the plan, before the write
    assert out[-3:] == [
        "Removed 1 file.",
        "  removed: Face It (watch face)",
        "The device finishes the removal after it is unplugged.",
    ]
    assert fake.names("GARMIN/Apps/Media") == ["DADE7E4.PRG"] and FakeStore.forgotten == []


def test_the_window_asks_to_remove_a_hidden_app_with_its_settings(hidden_spot, in_the_window):
    """The question before a removal over PTP names the settings, as the one through the
    mounted storage does."""
    import types

    from openexpress.gui import app as gui_app

    st, dev, _fake, app = hidden_spot
    app.has_files, app.direct_removal = False, True
    asked = []

    class AlertDialog:
        def __init__(self, heading, body):
            asked.append(body)

        def add_response(self, *args):
            pass

        def set_response_appearance(self, *args):
            pass

        def connect(self, *args):
            pass

        def present(self, parent):
            pass

        def __getattr__(self, name):  # the rest of the dialog's setters
            return lambda *a, **kw: None

    in_the_window("Adw", types.SimpleNamespace(
        AlertDialog=AlertDialog, ResponseAppearance=types.SimpleNamespace(DESTRUCTIVE=1)))
    win = types.SimpleNamespace(session=types.SimpleNamespace(device=dev, storage=st))
    gui_app.MainWindow.confirm_remove_app(win, app)
    assert asked == [
        ("The app and its settings will be deleted from the device over a direct USB "
         "connection; the device disappears from the file manager for a moment. The device "
         "finishes the removal after it is unplugged.")]


def test_cli_removes_a_hidden_app_and_its_settings(hidden_spot, apps_cli, monkeypatch, capsys):
    from openexpress import cli
    from openexpress.cli import main

    st, dev, fake, _app = hidden_spot
    fake.add("GARMIN/Apps/SETTINGS/0A1B2C3D.SET", b"app settings", hidden=True)
    monkeypatch.setattr(cli, "open_device", lambda args: (st, dev))
    assert main(["--path", str(st.root), "apps", "remove", "Face It", "--apply"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert "Removed 2 files." in out and fake.names("GARMIN/Apps/SETTINGS") == []


# --- apps that wait for the device to be unplugged ----------------------------------------


def test_app_left_out_of_the_waiting_list_is_marked_as_being_removed(hidden_spot):
    st, dev, _fake, app = hidden_spot
    ciq.remove(dev, st, app)
    # What the device shows once it is mounted again: OUT.BIN unchanged, IN.BIN without the app.
    (st.root / "GARMIN/APPS/IN.BIN").write_bytes(
        ciq.build_in_bin([a for a in ciq.read_installed(dev, st).apps if a.app_id != FACE]))
    state = ciq.read_installed(dev, st)
    assert state.find(FACE).pending_removal and not state.find(STORE_ID).pending_removal
    assert not state.find(PRELOADED).pending_removal
    assert [a.app_id for a in state.listed] == [STORE_ID, PRELOADED]
    with pytest.raises(ciq.CiqError, match="Face It is already being removed"):
        ciq.remove(dev, st, state.find(FACE))


def test_built_in_items_missing_from_a_partial_list_are_not_marked(iq_device):
    st, dev = iq_device
    (st.root / "GARMIN/APPS/IN.BIN").write_bytes(
        ciq.build_in_bin([a for a in ciq.read_installed(dev, st).apps if a.app_id == STORE_ID]))
    state = ciq.read_installed(dev, st)
    assert not any(a.pending_removal for a in state.apps) and len(state.listed) == 2


def test_new_app_list_does_not_bring_a_removed_app_back(iq_device, device_root):
    st, dev = iq_device
    (device_root / "GARMIN/APPS/OUT.BIN").write_bytes(out_bin([
        (STORE_ID, 1, "Connect IQ Store", False, "DADE7E4"),
        (FACE, 3, "Face It", True, "0A1B2C3D"),
        (PRELOADED, 8, "Softball", True, ""),
    ]))
    (st.root / "GARMIN/APPS/IN.BIN").write_bytes(
        ciq.build_in_bin([a for a in ciq.read_installed(dev, st).apps if a.app_id != FACE]))
    ciq.set_enabled(dev, st, PRELOADED, True)  # rewrites the waiting list
    assert [p[0] for p in ciq.parse_in_bin(st.read("GARMIN/APPS/IN.BIN"))] == [STORE_ID, PRELOADED]
    assert ciq.read_installed(dev, st).find(FACE).pending_removal


def test_waiting_new_app_gets_its_name_from_the_account(iq_device):
    st, dev = iq_device
    ciq.install_queued(dev, st, FakeClient(), [FACE_MESSAGE], known={})
    state = ciq.read_installed(dev, st)
    waiting = state.find(FACE)
    assert waiting.pending and waiting.name == str(FACE) and waiting.version is None
    ciq.name_pending(state, [store_app(FACE, "Face It")])
    assert (waiting.name, waiting.version) == ("Face It", 5)
    with pytest.raises(ciq.CiqError, match="Face It is not installed yet"):
        ciq.remove(dev, st, waiting)


def test_report_tells_apps_written_over_ptp_apart(hidden_apps, iq_device):
    st, dev, _fake = hidden_apps
    rep = ciq.install_queued(dev, st, CountingClient(), [FACE_MESSAGE], known={}, direct=True)
    assert rep.installed == ["Face It"] and rep.direct == ["Face It"]
    assert ciq.written_text(rep.direct) == (
        "Wrote Face It to the device; it will be installed after the device is unplugged.")
    assert ciq.written_text(["A", "B"]).startswith("Wrote A, B to the device; they will be")


def test_visible_folder_install_is_not_reported_as_written_over_ptp(iq_device):
    st, dev = iq_device
    rep = ciq.install_queued(dev, st, FakeClient(), [FACE_MESSAGE], known={})
    assert rep.installed == ["Face It"] and rep.direct == []


def test_cli_install_over_ptp_says_written_not_installed(hidden_apps, apps_cli, monkeypatch,
                                                         capsys):
    from openexpress import cli
    from openexpress.cli import main

    st, dev, _fake = hidden_apps
    monkeypatch.setattr(cli, "open_device", lambda args: (st, dev))
    assert main(["--path", str(st.root), "apps", "install", "Face It", "--apply"]) == 0
    out = capsys.readouterr().out
    assert ("Wrote Face It to the device; it will be installed after the device is "
            "unplugged.") in out
    assert "Installed:" not in out


def test_cli_list_marks_apps_that_wait_for_the_device(hidden_spot, apps_cli, monkeypatch, capsys):
    from openexpress import cli
    from openexpress.cli import main

    st, dev, _fake, _app = hidden_spot
    other = uuid.UUID("11111111-2222-4333-8444-555555555555")
    listed = [a for a in ciq.read_installed(dev, st).apps if a.app_id != FACE]
    listed.append(ciq.InstalledApp(app_id=other, app_type=1, name="x", disabled=False))
    (st.root / "GARMIN/APPS/IN.BIN").write_bytes(ciq.build_in_bin(listed))
    monkeypatch.setattr(cli, "open_device", lambda args: (st, dev))
    monkeypatch.setattr(FakeStore, "account_apps", lambda self, device, locale: [
        store_app(FACE, "Face It"), store_app(other, "Other", "watch-app")])
    assert main(["--path", str(st.root), "apps"]) == 0
    lines = capsys.readouterr().out.splitlines()
    face = next(line for line in lines if "Face It" in line)
    new = next(line for line in lines if "Other" in line)
    assert face.endswith("will be removed after the device is unplugged")
    assert new.endswith("will be installed after the device is unplugged") and "v5" in new
    assert str(other) not in "\n".join(lines)

    assert main(["--path", str(st.root), "apps", "remove", "Other", "--apply"]) == 1
    assert "Other is not installed yet" in capsys.readouterr().err


def test_window_notes_for_apps_that_wait():
    from openexpress.gui.app import MainWindow

    app = ciq.InstalledApp(app_id=FACE, app_type=3, name="Face It", disabled=False)
    assert MainWindow._waiting_note(app) == ""
    app.pending = True
    assert MainWindow._waiting_note(app) == "Will be installed after the device is unplugged"
    app.pending, app.pending_removal = False, True
    assert MainWindow._waiting_note(app) == "Will be removed after the device is unplugged"


# --- putting the apps back in the order of a saved app list -------------------------------

W1, W2, W3, W4 = (uuid.UUID(f"c0ffee15-600d-540c-0000-00000000000{n}") for n in "1234")
A1 = uuid.UUID("c0ffee15-600d-540c-0000-0000000000a1")


def order_entries(*names):
    """Made-up entries: 'a…' apps (type 1), 'w…' widgets (type 2), 's' the store app."""
    table = {"s": (STORE_ID, 1, "Store", False, "DADE7E4"), "a1": (A1, 1, "Maps", False, ""),
             "w1": (W1, 2, "Calendar", False, ""), "w2": (W2, 2, "Music", True, ""),
             "w3": (W3, 2, "Tides", False, ""), "w4": (W4, 2, "Moon", False, ""),
             "a1w": (A1, 2, "Maps", True, ""), "f": (FACE, 3, "Face It", True, "0A1B2C3D")}
    return out_bin([table[n] for n in names])


@pytest.fixture
def order_device(iq_device, device_root, tmp_path):
    st, dev = iq_device
    (device_root / "GARMIN/APPS/OUT.BIN").write_bytes(
        order_entries("s", "a1", "w2", "w1", "a1w", "w4", "w3"))
    backups = tmp_path / "Backups"
    copy = backups / str(dev.unit_id) / "GARMIN-APPS 2026-01-02 (03.04.05)" / "GARMIN/Apps/OUT.BIN"
    copy.parent.mkdir(parents=True)
    copy.write_bytes(order_entries("s", "a1", "w1", "a1w", "w2", "w3", "w4"))
    return st, dev, backups


def names(apps):
    return [f"{a.name}/{a.app_type}" for a in apps]


def test_plan_puts_each_type_in_the_order_of_the_copy(order_device):
    st, dev, backups = order_device
    copy = ciq.load_app_list_copy(dev, backups, "03.04.05")
    plan = ciq.plan_order(ciq.read_installed(dev, st), copy.state)
    assert names(plan.entries) == ["Store/1", "Maps/1", "Calendar/2", "Maps/2", "Music/2",
                                   "Tides/2", "Moon/2"]
    assert [(pos, now.name, then.name) for pos, now, then in plan.moved] == [
        (3, "Music", "Calendar"), (4, "Calendar", "Maps"), (5, "Maps", "Music"),
        (6, "Moon", "Tides"), (7, "Tides", "Moon")]
    assert plan.only_in_copy == [] and plan.only_on_device == []


def test_plan_keeps_apps_the_copy_does_not_have_and_ignores_the_rest(order_device, device_root):
    st, dev, backups = order_device
    # A watch face installed after the copy was made sits between the widgets on the device.
    (device_root / "GARMIN/APPS/OUT.BIN").write_bytes(
        order_entries("s", "a1", "w2", "f", "w1", "w4"))
    copy = ciq.load_app_list_copy(dev, backups, "03.04.05")
    plan = ciq.plan_order(ciq.read_installed(dev, st), copy.state)
    assert names(plan.entries) == ["Store/1", "Maps/1", "Calendar/2", "Face It/3", "Music/2",
                                   "Moon/2"]
    assert names(plan.only_on_device) == ["Face It/3"]
    assert names(plan.only_in_copy) == ["Maps/2", "Tides/2"]


def test_types_are_ordered_on_their_own(order_device, device_root):
    """A copy that lists widgets before apps, like the list Garmin Express writes."""
    st, dev, backups = order_device
    foreign = ciq.parse_out_bin(order_entries("w1", "a1w", "w2", "w3", "w4", "s", "a1"))
    plan = ciq.plan_order(ciq.read_installed(dev, st), foreign)
    assert [a.app_type for a in plan.entries] == [1, 1, 2, 2, 2, 2, 2]


def test_restore_order_writes_the_same_apps_and_flags(order_device):
    st, dev, backups = order_device
    before = st.read("GARMIN/APPS/OUT.BIN")
    copy = ciq.load_app_list_copy(dev, backups, "03.04.05")
    plan, saved = ciq.restore_order(dev, st, copy.state, backups)
    written = ciq.parse_in_bin(st.read("GARMIN/APPS/IN.BIN"))
    assert [(e[0], e[1]) for e in written] == [(a.app_id, a.app_type) for a in plan.entries]
    # Flags are the device's own: Music stays disabled, the Maps widget too.
    assert [e[2] for e in written] == [False, False, False, True, True, False, False]
    assert saved.startswith("GARMIN-APPS ") and saved.endswith(" Test watch")
    assert (backups / str(dev.unit_id) / saved / "OUT.BIN").read_bytes() == before
    assert st.read("GARMIN/APPS/OUT.BIN") == before  # the device's own list is never written
    # The copy just saved is listed with the others, after the older one.
    assert [c.name for c in ciq.app_list_copies(dev, backups)] == [
        "GARMIN-APPS 2026-01-02 (03.04.05)", saved]


def test_restore_order_changes_nothing_when_the_order_is_the_same(order_device):
    st, dev, backups = order_device
    same = ciq.read_installed(dev, st)
    plan, saved = ciq.restore_order(dev, st, same, backups)
    assert plan.moved == [] and saved is None
    assert not (st.root / "GARMIN/APPS/IN.BIN").exists()
    assert len(ciq.app_list_copies(dev, backups)) == 1


def test_restore_order_is_refused_while_changes_wait(order_device):
    st, dev, backups = order_device
    ciq.set_enabled(dev, st, W2, True)
    waiting = st.read("GARMIN/APPS/IN.BIN")
    copy = ciq.load_app_list_copy(dev, backups, "03.04.05")
    copies = len(ciq.app_list_copies(dev, backups))
    with pytest.raises(ciq.CiqError, match="has not applied earlier changes"):
        ciq.restore_order(dev, st, copy.state, backups)
    assert st.read("GARMIN/APPS/IN.BIN") == waiting
    assert len(ciq.app_list_copies(dev, backups)) == copies  # a refusal saves nothing


def test_restore_order_is_refused_with_unreadable_entries(order_device, device_root):
    st, dev, backups = order_device
    copy = ciq.load_app_list_copy(dev, backups, "03.04.05")
    damaged_out_bin(device_root, "truncated")
    with pytest.raises(ciq.CiqError, match="cannot be read"):
        ciq.restore_order(dev, st, copy.state, backups)
    assert not (st.root / "GARMIN/APPS/IN.BIN").exists()


def test_copy_is_found_by_name_or_path(order_device, tmp_path):
    st, dev, backups = order_device
    by_name = ciq.load_app_list_copy(dev, backups, "2026-01-02")
    assert ciq.load_app_list_copy(dev, backups, str(by_name.path)).path == by_name.path
    assert ciq.load_app_list_copy(dev, backups, str(by_name.path.parents[2])).path == by_name.path
    with pytest.raises(ciq.CiqError, match="No copy of the app list matches"):
        ciq.load_app_list_copy(dev, backups, "1999")
    second = by_name.path.parents[3] / "GARMIN-APPS 2026-01-02 (09.00.00)" / "OUT.BIN"
    second.parent.mkdir()
    second.write_bytes(by_name.path.read_bytes())
    with pytest.raises(ciq.CiqError, match="Several copies"):
        ciq.load_app_list_copy(dev, backups, "2026-01-02")
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ciq.CiqError, match="No OUT.BIN"):
        ciq.load_app_list_copy(dev, backups, str(empty))


def test_cli_apps_order(order_device, apps_cli, monkeypatch, capsys):
    from openexpress import paths
    from openexpress.cli import main

    st, dev, backups = order_device
    monkeypatch.setattr(paths, "default_backup_dir", lambda: backups)
    root = str(st.root)
    assert main(["--path", root, "apps", "order"]) == 0
    out = capsys.readouterr().out
    # The apps to move (25.10.2026): five positions change, two apps move.
    assert "GARMIN-APPS 2026-01-02 (03.04.05): 7 entries, 2 apps moved" in out

    assert main(["--path", root, "apps", "order", "--from", "03.04.05"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[1] == "2 apps moved"  # the measure of the list first (25.10.2026)
    assert out[2] == "5 positions would change (widget: 5):"
    assert out[3] == "  3: Calendar (now: Music)"
    assert out[0] == "Order of the apps from the copy GARMIN-APPS 2026-01-02 (03.04.05)"
    assert out[-4:] == [
        "App list to be written (IN.BIN): 7 entries; the device lists 7 (OUT.BIN)",
        "  the same apps with the same flags; only the order changes",
        "Before any change, a copy of the app list of the device is made.",
        "Dry run. Add --apply."]
    assert not (st.root / "GARMIN/APPS/IN.BIN").exists()
    assert len(ciq.app_list_copies(dev, backups)) == 1  # a dry run saves nothing

    assert main(["--path", root, "apps", "order", "--from", "03.04.05", "--apply"]) == 0
    out = capsys.readouterr().out
    assert "Copy of the current app list: GARMIN-APPS " in out
    assert "takes effect after the device is unplugged" in out
    assert str(dev.unit_id) not in out  # no path into the folder named after the unit ID
    assert (st.root / "GARMIN/APPS/IN.BIN").exists()

    # The new order waits on the device: a second run is refused.
    assert main(["--path", root, "apps", "order", "--from", "03.04.05"]) == 1
    assert "has not applied earlier changes" in capsys.readouterr().err


# --- a copy of the app list is saved before anything is written to the device -------------


def copy_names(dev, backups):
    return [c.name for c in ciq.app_list_copies(dev, backups)]


@pytest.fixture
def watched(monkeypatch, backups):
    """Records, for every write to a storage, whether a copy of the app list existed then."""
    seen = []

    def wrap(name):
        original = getattr(LocalStorage, name)

        def recorded(self, real_path, *args):
            seen.append((name, real_path, any(backups.rglob("OUT.BIN"))))
            return original(self, real_path, *args)

        monkeypatch.setattr(LocalStorage, name, recorded)

    for method in ("_write", "_delete", "_mkdir"):
        wrap(method)
    return seen


def test_copy_is_saved_before_an_install_through_the_mounted_storage(iq_device, backups, watched):
    st, dev = iq_device
    before = st.read("GARMIN/APPS/OUT.BIN")
    rep = ciq.install_queued(dev, st, FakeClient(), [FACE_MESSAGE], known={})
    assert rep.installed == ["Face It"]
    assert len(watched) == 2 and all(copied for _op, _path, copied in watched)
    (name,) = copy_names(dev, backups)
    assert (backups / str(dev.unit_id) / name / "OUT.BIN").read_bytes() == before


def test_copy_is_saved_before_the_ptp_session_of_an_install(hidden_apps, backups):
    st, dev, fake = hidden_apps
    at_session = []
    block = st._block

    @contextlib.contextmanager
    def recorded():
        at_session.append(copy_names(dev, backups))  # the moment the session opens
        with block() as session:
            yield session

    st._block = recorded
    rep = ciq.install_queued(dev, st, CountingClient(), [FACE_MESSAGE], known={}, direct=True)
    assert rep.installed == ["Face It"]
    assert len(at_session) == 1 and len(at_session[0]) == 1  # while still mounted


def test_copy_is_saved_before_a_removal(iq_device, device_root, backups, watched):
    st, dev = iq_device
    (device_root / "GARMIN/APPS/OUT.BIN").write_bytes(out_bin([
        (STORE_ID, 1, "Connect IQ Store", False, "DADE7E4"),
        (FACE, 3, "Face It", True, "0A1B2C3D")]))
    (device_root / "GARMIN/APPS/MEDIA/0A1B2C3D.PRG").write_bytes(b"face")
    app = ciq.read_installed(dev, st).find(FACE)
    assert ciq.remove(dev, st, app) == ["GARMIN/APPS/MEDIA/0A1B2C3D.PRG"]
    assert [(op, copied) for op, _path, copied in watched] == [("_delete", True)]


def test_copy_is_saved_before_the_ptp_session_of_a_removal(hidden_spot, backups):
    st, dev, fake, app = hidden_spot
    at_session = []
    block = st._block

    @contextlib.contextmanager
    def recorded():
        at_session.append(copy_names(dev, backups))  # the moment the session opens
        with block() as session:
            yield session

    st._block = recorded
    ciq.remove(dev, st, app)
    assert len(at_session) == 1 and len(at_session[0]) == 1


def test_copy_is_saved_before_enabling_and_before_a_new_folder(iq_device, device_root, backups,
                                                               watched, xdg):
    st, dev = iq_device
    ciq.set_enabled(dev, st, PRELOADED, True)
    assert [(op, copied) for op, _path, copied in watched] == [("_write", True)]
    del watched[:]
    (device_root / "GARMIN/APPS/IN.BIN").unlink()
    (device_root / "GARMIN/APPS/MEDIA").rmdir()
    for folder in backups.rglob("GARMIN-APPS*"):
        (folder / "OUT.BIN").unlink()
        folder.rmdir()
    assert ciq.prepare_app_dir(dev, st, "IQWatchFaces") is True
    assert [(op, copied) for op, _path, copied in watched] == [("_mkdir", True)]


@pytest.fixture
def no_copies(backups):
    """The backup folder cannot be created: a file is in its place."""
    backups.write_text("not a folder")
    return backups


def test_failed_copy_stops_an_install_before_garmin_is_asked(iq_device, no_copies, watched):
    st, dev = iq_device
    FakeStore.queued = []
    client = CountingClient()
    client.device_messages = lambda unit_id: pytest.fail("the queue is not read")
    with pytest.raises(ciq.AppListCopyError) as error:
        ciq.install_apps(dev, st, client, FakeStore(client), [store_app(FACE, "Face It")])
    assert "Nothing was changed." in str(error.value)
    assert str(dev.unit_id) not in str(error.value) and str(no_copies) not in str(error.value)
    assert FakeStore.queued == [] and client.downloads == 0 and client.acks == []
    assert watched == []


def test_failed_copy_stops_a_ptp_install_and_removal(hidden_spot, no_copies):
    st, dev, fake, app = hidden_spot
    client = CountingClient()
    with pytest.raises(ciq.AppListCopyError):
        ciq.install_queued(dev, st, client, [{**FACE_MESSAGE, "metadata": {
            "messageurl": "x", "messagename": "Other",
            "appdetails": {"id": "11111111-2222-4333-8444-555555555555"}}}], known={},
            direct=True)
    with pytest.raises(ciq.AppListCopyError):
        ciq.remove(dev, st, app)
    assert st.sessions == 0 and fake.operations == [] and client.downloads == 0
    assert fake.names("GARMIN/Apps/Media") == ["0A1B2C3D.PRG", "DADE7E4.PRG"]


def test_failed_copy_stops_every_other_change(order_device, watched, monkeypatch):
    st, dev, backups = order_device
    copy = ciq.load_app_list_copy(dev, backups, "03.04.05")

    def refuse(*args, **kwargs):
        raise ciq.AppListCopyError("no copy")

    monkeypatch.setattr(ciq, "save_app_list_copy", refuse)
    with pytest.raises(ciq.AppListCopyError):
        ciq.restore_order(dev, st, copy.state, backups)
    with pytest.raises(ciq.AppListCopyError):
        ciq.set_enabled(dev, st, W2, True)
    assert watched == [] and not (st.root / "GARMIN/APPS/IN.BIN").exists()


def test_failed_copy_in_a_sync_is_an_error_and_writes_nothing(iq_device, no_copies, watched):
    import types

    from openexpress import service

    st, dev = iq_device

    class Client(CountingClient):
        def require_verified(self, unit_id=None):
            raise RuntimeError("names are cosmetic")

    client = Client()
    s = types.SimpleNamespace(storage=st, device=dev, client=client,
                              settings=types.SimpleNamespace(locale="en_US"))
    errors = []
    assert service._install_queued_apps(s, [FACE_MESSAGE], lambda m: None, errors) == []
    assert len(errors) == 1 and "Cannot make a copy of the app list" in errors[0]
    assert watched == [] and client.downloads == 0 and client.acks == []


def test_no_second_copy_while_the_app_list_is_unchanged(iq_device, device_root, backups):
    st, dev = iq_device
    stamps = iter(datetime.datetime(2026, 1, 2, 3, 4, n) for n in range(5, 60))
    first = ciq.save_app_list_copy(dev, st, now=next(stamps))
    assert ciq.save_app_list_copy(dev, st, now=next(stamps)) == first
    ciq.set_enabled(dev, st, PRELOADED, True)  # OUT.BIN itself is not written by us
    assert copy_names(dev, backups) == [first]
    (device_root / "GARMIN/APPS/OUT.BIN").write_bytes(out_bin([
        (STORE_ID, 1, "Connect IQ Store", False, "DADE7E4")]))
    second = ciq.save_app_list_copy(dev, st, now=next(stamps))
    assert copy_names(dev, backups) == [first, second]
    # Only the newest counts: the first state coming back is copied again, and the first
    # copy, of the same list, goes (25.10.2026: a series keeps its newest).
    (device_root / "GARMIN/APPS/OUT.BIN").write_bytes(
        (backups / str(dev.unit_id) / first / "OUT.BIN").read_bytes())
    third = ciq.save_app_list_copy(dev, st, now=next(stamps))
    assert copy_names(dev, backups) == [second, third]


def test_copy_name_has_the_time_before_the_model_and_no_identifier(iq_device, backups, caplog):
    import logging

    st, dev = iq_device
    dev.description = 'fenix 8 - 47mm, Solar / "X": <test>*?'
    with caplog.at_level(logging.INFO):
        name = ciq.save_app_list_copy(dev, st, now=datetime.datetime(2026, 3, 4, 5, 6, 7))
    assert name == "GARMIN-APPS 2026-03-04 (05.06.07) fenix 8 - 47mm, Solar X test"
    assert (backups / str(dev.unit_id) / name / "OUT.BIN").is_file()
    assert str(dev.unit_id) not in name
    assert str(dev.unit_id) not in caplog.text and str(backups) not in caplog.text
    assert name in caplog.text


def test_two_copies_in_the_same_second_do_not_overwrite_each_other(iq_device, device_root,
                                                                   backups):
    st, dev = iq_device
    now = datetime.datetime(2026, 3, 4, 5, 6, 7)
    first = ciq.save_app_list_copy(dev, st, now=now)
    (device_root / "GARMIN/APPS/OUT.BIN").write_bytes(out_bin([
        (STORE_ID, 1, "Connect IQ Store", False, "DADE7E4")]))
    second = ciq.save_app_list_copy(dev, st, now=now)
    assert second == first + " (2)" and len(copy_names(dev, backups)) == 2


@pytest.fixture
def old_copies(order_device):
    """Nine copies named and laid out like the ones made by hand before copies had the
    model in their name: two with OUT.BIN at the top, the rest under GARMIN/Apps."""
    st, dev, backups = order_device
    unit = backups / str(dev.unit_id)
    for folder in unit.iterdir():
        for f in sorted(folder.rglob("*"), reverse=True):
            f.unlink() if f.is_file() else f.rmdir()
        folder.rmdir()
    stamps = ["17.37.37", "19.24.05", "20.10.54", "20.41.30", "20.45.33", "20.51.10",
              "20.56.38", "21.07.08", "21.19.41"]
    orders = [("s", "a1", "w1", "a1w", "w2", "w3", "w4"), ("s", "a1", "w2", "w1", "a1w", "w3", "w4")]
    for n, stamp in enumerate(stamps):
        folder = unit / f"GARMIN-APPS 2026-10-12 ({stamp})"
        target = folder / "OUT.BIN" if n < 2 else folder / "GARMIN/Apps/OUT.BIN"
        target.parent.mkdir(parents=True)
        target.write_bytes(order_entries(*orders[min(n, 1)]))
    return st, dev, backups, stamps


def test_copies_in_the_old_format_are_still_found(old_copies):
    st, dev, backups, stamps = old_copies
    assert copy_names(dev, backups) == [f"GARMIN-APPS 2026-10-12 ({s})" for s in stamps]
    first = ciq.load_app_list_copy(dev, backups, "17.37.37")
    assert first.name == "GARMIN-APPS 2026-10-12 (17.37.37)" and len(first.state.apps) == 7
    assert ciq.load_app_list_copy(dev, backups, "21.19.41").path.parts[-3:] == (
        "GARMIN", "Apps", "OUT.BIN")
    # A new copy sorts after them and is found the same way.
    new = ciq.save_app_list_copy(dev, st, now=datetime.datetime(2026, 10, 12, 22, 0, 0))
    assert copy_names(dev, backups)[-1] == new == "GARMIN-APPS 2026-10-12 (22.00.00) Test watch"
    assert ciq.load_app_list_copy(dev, backups, "22.00.00").name == new
    assert len(ciq.plan_order(ciq.read_installed(dev, st), first.state).moved) == 5


def test_newest_old_format_copy_counts_for_the_no_duplicate_rule(old_copies, device_root):
    st, dev, backups, stamps = old_copies
    newest = backups / str(dev.unit_id) / "GARMIN-APPS 2026-10-12 (21.19.41)/GARMIN/Apps/OUT.BIN"
    (device_root / "GARMIN/APPS/OUT.BIN").write_bytes(newest.read_bytes())
    assert ciq.save_app_list_copy(dev, st) == "GARMIN-APPS 2026-10-12 (21.19.41)"
    assert len(copy_names(dev, backups)) == 9


def test_cli_order_from_an_old_copy(old_copies, apps_cli, capsys):
    from openexpress.cli import main

    st, dev, backups, _stamps = old_copies
    assert main(["--path", str(st.root), "apps", "order", "--from", "17.37.37"]) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0] == "Order of the apps from the copy GARMIN-APPS 2026-10-12 (17.37.37)"
    assert "5 positions would change" in out and str(dev.unit_id) not in out
    assert len(copy_names(dev, backups)) == 9  # a dry run saves nothing


def test_dry_runs_save_no_copy(hidden_spot, apps_cli, monkeypatch, backups, capsys):
    from openexpress import cli
    from openexpress.cli import main

    st, dev, fake, _app = hidden_spot
    monkeypatch.setattr(cli, "open_device", lambda args: (st, dev))
    monkeypatch.setattr(FakeStore, "account_apps", lambda self, device, locale: [
        store_app(uuid.UUID("11111111-2222-4333-8444-555555555555"), "Other")])
    root = str(st.root)
    note = "Before any change, a copy of the app list of the device is made."
    for argv in (["apps", "install", "Other"], ["apps", "remove", "Face It"],
                 ["apps", "disable", "Connect IQ Store"]):
        assert main(["--path", root, *argv]) == 0
        assert note in capsys.readouterr().out
    assert not backups.exists() and fake.operations == []


def test_a_refused_folder_is_remembered_in_a_private_file(iq_device):
    """devices/<unit ID>/ciq.json is written as the other state files are (paths.write_private:
    at once, readable by its owner only; review of 24.10.2026), and read back."""
    import stat

    _st, dev = iq_device
    ciq._remember_refusal(dev, "GARMIN/APPS/MEDIA")
    ciq._remember_refusal(dev, "GARMIN/APPS")
    f = ciq._refusals_file(dev)
    assert stat.S_IMODE(f.stat().st_mode) == 0o600
    version = dev.software_version_str
    assert ciq._refusals(dev) == {"garmin/apps/media": version, "garmin/apps": version}
    assert [p.name for p in f.parent.iterdir()] == ["ciq.json"]  # no temporary file left


def test_nothing_to_write_saves_no_copy(no_usb_apps, backups):
    st, dev, _attempts = no_usb_apps
    ciq._remember_refusal(dev, "GARMIN/APPS/MEDIA")  # known: the folder cannot be created
    FakeStore.queued = []
    client = CountingClient()
    client.device_messages = lambda unit_id: []
    rep = ciq.install_apps(dev, st, client, FakeStore(client), [store_app(FACE, "Face It")])
    assert rep.for_phone == ["Face It"] and FakeStore.queued == [["Face It"]]
    assert not backups.exists()


def test_device_without_an_app_list_has_nothing_to_copy(iq_device, device_root, backups,
                                                        monkeypatch, watched):
    """E.g. after a reset: there is no OUT.BIN, so no copy is made and the change goes on."""
    st, dev = iq_device
    state = ciq.read_installed(dev, st)
    (device_root / "GARMIN/APPS/OUT.BIN").unlink()
    assert not ciq.has_app_list(dev, st)
    assert ciq.save_app_list_copy(dev, st) is None and not backups.exists()
    assert ciq.app_list_copy_note(dev, st) == "No copy will be made: the device has no app list."
    # A change that gets this far is not stopped by the missing copy.
    monkeypatch.setattr(ciq, "read_installed", lambda device, storage: state)
    ciq.set_enabled(dev, st, PRELOADED, True)
    assert [op for op, _path, _copied in watched] == ["_write"] and not backups.exists()
    assert (device_root / "GARMIN/APPS/IN.BIN").exists()


def test_app_list_that_cannot_be_read_stops_the_change(iq_device, backups, monkeypatch, watched):
    """There is an OUT.BIN, but reading it for the copy fails: nothing is written."""
    from openexpress.device.storage import DeviceError

    st, dev = iq_device
    assert ciq.app_list_copy_note(dev, st) == (
        "Before any change, a copy of the app list of the device is made.")
    reads = []
    read = LocalStorage._read

    def fails_the_second_time(self, real_path):
        if real_path.upper().endswith("OUT.BIN"):
            reads.append(real_path)
            if len(reads) > 1:
                raise DeviceError("Cannot read " + real_path + ": Input/output error")
        return read(self, real_path)

    monkeypatch.setattr(LocalStorage, "_read", fails_the_second_time)
    with pytest.raises(ciq.AppListCopyError) as error:
        ciq.set_enabled(dev, st, PRELOADED, True)
    assert "Cannot read the app list of the device to make a copy of it" in str(error.value)
    assert str(error.value).endswith("Nothing was changed.")
    assert watched == [] and not backups.exists()
    assert not (st.root / "GARMIN/APPS/IN.BIN").exists()


# --- the window: restoring the order from a copy ------------------------------------------


def test_order_choices_are_newest_first_with_their_differences(old_copies):
    from openexpress.gui.app import MainWindow

    st, dev, backups, stamps = old_copies
    state = ciq.read_installed(dev, st)
    choices = ciq.order_choices(dev, state, backups)
    assert [c.name for c, _plan in choices] == [
        f"GARMIN-APPS 2026-10-12 ({s})" for s in reversed(stamps)]
    rows = MainWindow._order_rows(choices)
    # The apps to move, not the positions that change (25.10.2026; 2 and 5 positions).
    assert rows[0] == ("2026-10-12 (21.19.41)", "1 app moved", True)
    assert rows[-1] == ("2026-10-12 (17.37.37)", "2 apps moved", True)
    assert not any(str(dev.unit_id) in text for row in rows for text in row[:2])
    # A copy made by the program carries the model after the time; one in the order the
    # device has now cannot be chosen.
    ciq.save_app_list_copy(dev, st, backups, now=datetime.datetime(2026, 10, 12, 22, 0, 0))
    rows = MainWindow._order_rows(ciq.order_choices(dev, state, backups))
    assert rows[0] == ("2026-10-12 (22.00.00) Test watch", "The same order as now", False)
    # Copies 2–8 had one list: its newest (21.19.41) stays (25.10.2026), with the first.
    assert [title for title, _sub, _usable in rows] == [
        "2026-10-12 (22.00.00) Test watch", "2026-10-12 (21.19.41)", "2026-10-12 (17.37.37)"]


def test_order_summary_shows_the_first_ten_changes(order_device):
    from openexpress.gui.app import MainWindow

    st, dev, backups = order_device
    copy = ciq.load_app_list_copy(dev, backups, "03.04.05")
    plan = ciq.plan_order(ciq.read_installed(dev, st), copy.state)
    text = MainWindow._order_summary(plan, ciq.app_list_copy_note(dev, st))
    assert text.splitlines() == [
        "2 apps moved",  # the measure of the list first (25.10.2026)
        "5 positions would change (widget: 5):",
        "  3: Calendar (now: Music)", "  4: Maps (now: Calendar)", "  5: Music (now: Maps)",
        "  6: Tides (now: Moon)", "  7: Moon (now: Tides)",
        "",
        "The same apps with the same flags; only the order changes.",
        "Before any change, a copy of the app list of the device is made."]
    many = ciq.OrderPlan(entries=plan.entries, moved=plan.moved * 5, only_in_copy=[],
                         only_on_device=[plan.entries[0]])
    lines = MainWindow._order_summary(many, "note").splitlines()
    assert lines[0] == ciq.order_difference(many)
    assert lines[1].startswith("25 positions would change")
    assert len([line for line in lines if line.startswith("  ")]) == 11
    assert "  … and 15 more" in lines and "Not in the copy, left where they are: Store" in lines


def test_order_is_blocked_while_changes_wait_or_entries_are_unreadable(order_device, device_root):
    from openexpress.gui.app import MainWindow

    st, dev, backups = order_device
    assert MainWindow._order_blocked(ciq.read_installed(dev, st)) == ""
    ciq.set_enabled(dev, st, W2, True)  # an app list now waits on the device
    waiting = ciq.read_installed(dev, st)
    assert waiting.waiting and MainWindow._order_blocked(waiting).startswith(
        "The device has not applied earlier changes yet.")
    (device_root / "GARMIN/APPS/IN.BIN").unlink()
    damaged_out_bin(device_root, "truncated")
    assert "cannot be read" in MainWindow._order_blocked(ciq.read_installed(dev, st))


def test_window_restores_the_order_through_the_shared_function(order_device):
    import types

    from openexpress.gui.app import MainWindow

    st, dev, backups = order_device
    copy = ciq.load_app_list_copy(dev, backups, "03.04.05")
    toasts, loads, tasks = [], [], []
    win = types.SimpleNamespace(
        session=types.SimpleNamespace(device=dev, storage=st), toast=toasts.append,
        load_section=lambda key, force=False: loads.append((key, force)),
        run_task=RunNow(on_call=lambda call: tasks.append(call.title)))
    MainWindow.restore_order(win, copy)
    assert tasks == ["Restoring the order…"] and loads == [("apps", True)]
    assert toasts == [("Wrote the new order to the device; it takes effect after the device is "
                       "unplugged.")]
    written = ciq.parse_in_bin(st.read("GARMIN/APPS/IN.BIN"))
    assert [(e[0], e[1]) for e in written] == [(a.app_id, a.app_type) for a in copy.state.apps]
    assert len(ciq.app_list_copies(dev, backups)) == 2  # the copy before the change
    # Now the change waits: the row is blocked until the device is unplugged.
    assert MainWindow._order_blocked(ciq.read_installed(dev, st)) != ""


def test_window_says_so_when_the_order_is_already_the_same(order_device):
    import types

    from openexpress.gui.app import MainWindow

    st, dev, backups = order_device
    same = ciq.AppListCopy(path=None, name="GARMIN-APPS x", state=ciq.read_installed(dev, st))
    toasts = []
    win = types.SimpleNamespace(
        session=types.SimpleNamespace(device=dev, storage=st), toast=toasts.append,
        load_section=lambda key, force=False: None,
        run_task=RunNow())
    MainWindow.restore_order(win, same)
    assert toasts == ["The apps on the device are already in this order."]
    assert not (st.root / "GARMIN/APPS/IN.BIN").exists()


# --- a new version of an app in a folder the device hides ---------------------------------


@pytest.fixture
def hidden_update(hidden_spot):
    """Face It is installed (its file is the device's own) and a new version is queued."""
    st, dev, fake, app = hidden_spot
    fake.add("GARMIN/Apps/DATA/0A1B2C3D.DAT", b"app data")
    fake.add("GARMIN/Apps/SETTINGS/0A1B2C3D.SET", b"app settings")
    return st, dev, fake, app


def fake_tree(fake):
    """Every object of the fake device: path, content, whether the device owns it."""
    def path(handle):
        parts = []
        while handle in fake.objects:
            parts.append(fake.objects[handle].name)
            handle = fake.objects[handle].parent
        return "/".join(reversed(parts))

    return {path(h): (o.data, o.hidden) for h, o in fake.objects.items()}


def test_update_is_written_next_to_the_old_file_like_express_does(hidden_update, backups):
    from openexpress.device import ptp

    st, dev, fake, app = hidden_update
    before = fake_tree(fake)
    out_before = st.read("GARMIN/APPS/OUT.BIN")
    client = CountingClient()
    rep = ciq.install_queued(dev, st, client, [FACE_MESSAGE], known={}, direct=True)
    assert (rep.installed, rep.direct, rep.updated, rep.failed) == (
        ["Face It"], ["Face It"], ["Face It"], [])
    after = fake_tree(fake)
    added = sorted(set(after) - set(before))
    # One new app file under a new name, and the app list; nothing else changed.
    assert len(added) == 2 and added[0] == "GARMIN/Apps/IN.BIN"
    new_file = added[1]
    assert new_file.startswith("GARMIN/Apps/Media/") and new_file.endswith(".PRG")
    assert new_file != "GARMIN/Apps/Media/0A1B2C3D.PRG"
    assert after[new_file] == (b"PRG-DATA", False)
    assert all(after[path] == before[path] for path in before)  # old file, data, settings
    assert fake.sent(ptp.DELETE_OBJECT) == 0
    # The app list is what the device has: the same entries, order and flags, nothing added.
    state = ciq.parse_out_bin(out_before)
    assert [tuple(e) for e in fake_in_bin(fake)] == [
        (a.app_id, a.app_type, a.disabled, a.favorite) for a in state.apps]
    assert client.acks == [7] and len(copy_names(dev, backups)) == 1
    assert ciq.update_written_text(rep.updated) == (
        "Wrote the update of Face It to the device; it will be installed after the device is "
        "unplugged.")


def test_device_replaces_the_app_after_it_is_unplugged(hidden_update):
    """The fake does what the fēnix 8 was seen to do: new content under the old name."""
    st, dev, fake, app = hidden_update
    ciq.install_queued(dev, st, CountingClient(), [FACE_MESSAGE], known={}, direct=True)
    new = next(n for n in fake.names("GARMIN/Apps/Media", hidden=False))
    fake.take_over("GARMIN/Apps/Media", replaces={new: "0A1B2C3D.PRG"})
    assert fake.names("GARMIN/Apps/Media") == ["0A1B2C3D.PRG", "DADE7E4.PRG"]
    assert fake.objects[fake.handle("GARMIN/Apps/Media/0A1B2C3D.PRG")].data == b"PRG-DATA"
    assert fake.names("GARMIN/Apps/DATA") == ["0A1B2C3D.DAT"]


def test_sync_leaves_an_update_of_a_hidden_app_in_the_queue(hidden_update, monkeypatch):
    from openexpress.device.storage import DeviceError

    st, dev, fake, app = hidden_update
    before = fake_tree(fake)

    def refuse(self, real_path):
        raise DeviceError("refused")

    monkeypatch.setattr(LocalStorage, "_mkdir", refuse)
    client = CountingClient()
    rep = ciq.install_queued(dev, st, client, [FACE_MESSAGE], known={})  # as a sync does
    assert rep.for_phone == ["Face It"] and rep.installed == [] and rep.updated == []
    assert client.downloads == 0 and client.acks == [] and st.sessions == 0
    assert fake_tree(fake) == before


def test_update_is_refused_while_an_app_list_waits(hidden_update):
    st, dev, fake, app = hidden_update
    before = fake_tree(fake)
    client = CountingClient()
    (st.root / "GARMIN/APPS/IN.BIN").write_bytes(ciq.build_in_bin(ciq.read_installed(dev, st).apps))
    rep = ciq.install_queued(dev, st, client, [FACE_MESSAGE], known={}, direct=True)
    assert rep.installed == [] and "has not applied earlier changes" in rep.failed[0][1]
    assert st.sessions == 0 and client.acks == [] and fake_tree(fake) == before


def test_update_needs_connect_iq_space_next_to_the_old_file(hidden_update, monkeypatch):
    st, dev, fake, app = hidden_update
    before = fake_tree(fake)
    state = ciq.read_installed(dev, st)
    state.available_space = 4  # less than the new file
    monkeypatch.setattr(ciq, "read_installed", lambda device, storage: state)
    client = CountingClient()
    rep = ciq.install_queued(dev, st, client, [FACE_MESSAGE], known={}, direct=True)
    assert rep.failed == [("Face It", "not enough Connect IQ space on the device")]
    assert st.sessions == 0 and client.acks == [] and fake_tree(fake) == before


def test_update_without_room_on_the_storage(hidden_update):
    st, dev, fake, app = hidden_update
    before = fake_tree(fake)
    fake.free = 100  # less than the file and the margin
    rep = ciq.install_queued(dev, st, CountingClient(), [FACE_MESSAGE], known={}, direct=True)
    assert rep.failed == [("Face It", "not enough free space on the device")]
    assert fake_tree(fake) == before and not st.unmounted


def test_update_interrupted_while_the_app_file_is_sent(hidden_update):
    from openexpress.device import ptp

    st, dev, fake, app = hidden_update
    before = fake_tree(fake)
    fake.refuse[ptp.SEND_OBJECT] = ptp.GENERAL_ERROR
    client = CountingClient()
    rep = ciq.install_queued(dev, st, client, [FACE_MESSAGE], known={}, direct=True)
    assert rep.installed == [] and "GeneralError" in rep.failed[0][1]
    # The unfinished file we wrote is removed; the old version and its data are as before.
    assert fake_tree(fake) == before and client.acks == []
    assert fake.sent(ptp.DELETE_OBJECT) == 1


def test_update_names_the_unfinished_file_it_could_not_remove(hidden_update):
    from openexpress.device import ptp

    st, dev, fake, app = hidden_update
    before = fake_tree(fake)
    fake.refuse[ptp.SEND_OBJECT] = ptp.GENERAL_ERROR
    fake.refuse[ptp.DELETE_OBJECT] = ptp.ACCESS_DENIED
    rep = ciq.install_queued(dev, st, CountingClient(), [FACE_MESSAGE], known={}, direct=True)
    (left,) = sorted(set(fake_tree(fake)) - set(before))
    assert rep.failed[0][1].endswith(
        "An unfinished file may have been left on the device: "
        + left.replace("GARMIN/Apps/Media", "GARMIN/APPS/MEDIA") + ".")
    assert all(fake_tree(fake)[path] == before[path] for path in before)


def test_update_interrupted_between_the_app_file_and_the_app_list(hidden_update):
    """The cable is pulled, or the session breaks: the new file stays, the list is not
    written, the old version is untouched and the user is told which file was left."""
    from openexpress.device import ptp

    st, dev, fake, app = hidden_update
    before = fake_tree(fake)
    send_object = fake._send_object

    def breaks_after_the_file(data):
        result = send_object(data)
        fake.wrong_transaction = True  # everything after this is out of step
        return result

    fake._send_object = breaks_after_the_file
    client = CountingClient()
    rep = ciq.install_queued(dev, st, client, [FACE_MESSAGE], known={}, direct=True)
    assert rep.installed == [] and client.acks == []
    (left,) = sorted(set(fake_tree(fake)) - set(before))
    assert left.endswith(".PRG") and "may have been left on the device" in rep.failed[0][1]
    assert left.rsplit("/", 1)[1] in rep.failed[0][1]
    assert all(fake_tree(fake)[path] == before[path] for path in before)
    assert fake.sent(ptp.DELETE_OBJECT) == 0 and not st.unmounted


def test_update_with_a_refused_app_list_removes_only_its_own_file(hidden_update):
    from openexpress.device import ptp

    st, dev, fake, app = hidden_update
    before = fake_tree(fake)
    fake.refuse_names["IN.BIN"] = ptp.ACCESS_DENIED
    client = CountingClient()
    with pytest.raises(ciq.CiqError, match="Cannot register the apps"):
        ciq.install_queued(dev, st, client, [FACE_MESSAGE], known={}, direct=True)
    assert fake_tree(fake) == before and client.acks == []
    assert fake.sent(ptp.DELETE_OBJECT) == 1  # the file of this session, nothing else


def test_update_and_new_app_in_one_session(hidden_update):
    st, dev, fake, app = hidden_update
    other = uuid.UUID("11111111-2222-4333-8444-555555555555")
    second = {"messageid": 8, "devicexmldatatype": "IQWatchFaces",
              "metadata": {"messageurl": "y", "messagename": "Other",
                           "appdetails": {"id": str(other)}}}
    rep = ciq.install_queued(dev, st, CountingClient(), [FACE_MESSAGE, second], known={},
                             direct=True)
    assert rep.installed == ["Face It", "Other"] and rep.updated == ["Face It"]
    assert [e[0] for e in fake_in_bin(fake)] == [STORE_ID, FACE, PRELOADED, other]
    assert len(fake.names("GARMIN/Apps/Media", hidden=False)) == 2


def test_cli_update_of_a_hidden_app(hidden_update, apps_cli, monkeypatch, capsys):
    from openexpress import cli
    from openexpress.cli import main

    st, dev, fake, _app = hidden_update
    monkeypatch.setattr(cli, "open_device", lambda args: (st, dev))
    monkeypatch.setattr(FakeStore, "updates",
                        lambda self, device, installed, locale: [store_app(FACE, "Face It")])
    apps_cli.device_messages = lambda unit_id: [FACE_MESSAGE]
    root = str(st.root)
    assert main(["--path", root, "apps", "update"]) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0].startswith("Update Face It")
    assert "direct USB connection" in out and "cannot update" not in out
    assert "App list to be written (IN.BIN): 3 entries; the device lists 3 (OUT.BIN)" in out
    assert "every app on the device is kept, in the same order and with the same flags" in out
    assert "Before any change, a copy of the app list of the device is made." in out
    assert fake.operations == [] and st.sessions == 0

    assert main(["--path", root, "apps", "update", "--apply"]) == 0
    out = capsys.readouterr().out
    assert ("Wrote the update of Face It to the device; it will be installed after the device "
            "is unplugged.") in out
    assert "Installed:" not in out and apps_cli.acks == [7]


# --- the window remembers updates written over PTP until the device is unplugged -----------


def test_report_names_the_updated_apps_by_id(hidden_update):
    st, dev, _fake, _app = hidden_update
    other = uuid.UUID("11111111-2222-4333-8444-555555555555")
    second = {"messageid": 8, "devicexmldatatype": "IQWatchFaces",
              "metadata": {"messageurl": "y", "messagename": "Other",
                           "appdetails": {"id": str(other)}}}
    rep = ciq.install_queued(dev, st, CountingClient(), [FACE_MESSAGE, second], known={},
                             direct=True)
    assert rep.updated_ids == [FACE]  # the new app is not an update


def test_window_notes_an_update_that_waits(hidden_update):
    from openexpress.gui.app import MainWindow

    app = ciq.InstalledApp(app_id=FACE, app_type=3, name="Face It", disabled=False)
    assert MainWindow._waiting_note(app, updated=True) == (
        "Will be updated after the device is unplugged")
    assert MainWindow._waiting_note(app, updated=False) == ""
    app.pending_removal = True  # a removal says more than an update
    assert MainWindow._waiting_note(app, updated=True) == (
        "Will be removed after the device is unplugged")
    offered = MainWindow._updates_to_offer(
        [store_app(FACE, "Face It"), store_app(STORE_ID, "Connect IQ Store")], {FACE})
    assert [u.name for u in offered] == ["Connect IQ Store"]  # "Update all" skips Face It
    assert MainWindow._updates_to_offer([store_app(FACE, "Face It")], {FACE}) == []


def test_window_remembers_updates_until_the_device_is_unplugged(hidden_update, monkeypatch):
    import types

    from openexpress import service
    from openexpress.gui.app import MainWindow

    st, dev, _fake, _app = hidden_update
    monkeypatch.setattr(service, "require_account_device", lambda client, unit_id: [])
    monkeypatch.setattr(ciq, "StoreApi", FakeStore)
    FakeStore.queued = []
    client = CountingClient()
    client.device_messages = lambda unit_id: [FACE_MESSAGE]
    toasts, shown = [], []
    win = types.SimpleNamespace(
        session=types.SimpleNamespace(device=dev, storage=st, client=client),
        _updated_apps=set(), toast=toasts.append, progress=lambda text: None,
        error=lambda title, text: pytest.fail(text),
        load_section=lambda key, force=False: None, show_device=lambda: shown.append(1),
        show_no_device=lambda: shown.append(1),
        run_task=RunNow())
    win._install_apps = lambda apps, allowed=(): MainWindow._install_apps(win, apps, allowed)
    MainWindow.install_apps(win, [store_app(FACE, "Face It")])
    assert win._updated_apps == {FACE}
    assert toasts == [("Wrote the update of Face It to the device; it will be installed after "
                       "the device is unplugged.")]

    # Our own unmount and mount for a PTP session is not an unplug.
    mounted_at = str(st.label).rsplit("/", 1)[0]  # the storage is a folder of the mount

    class Mount:
        def get_root(self):
            return types.SimpleNamespace(get_uri=lambda: mounted_at)

    from openexpress.device import gio_storage

    monkeypatch.setattr(gio_storage, "_quiet_until", {gio_storage._key(mounted_at): float("inf")})
    MainWindow._on_mount_removed(win, None, Mount())
    assert win._updated_apps == {FACE} and win.session is not None
    # A real unplug forgets them.
    monkeypatch.setattr(gio_storage, "_quiet_until", {})
    MainWindow._on_mount_removed(win, None, Mount())
    assert win._updated_apps == set() and win.session is None and shown == [1]


# --- the Flatpak version: no USB access, so a hidden app folder is out of reach ------------


@pytest.fixture
def flatpak_spot(hidden_spot, monkeypatch):
    """The device of `hidden_spot` seen from a Flatpak sandbox: no PTP session, and any
    attempt to create a folder on the device is recorded (and refused, as the device does)."""
    from openexpress.device.storage import DeviceError

    st, dev, fake, app = hidden_spot
    st.fake = None
    st.usb_blocked = lambda: True
    attempts = []

    def refuse(self, real_path):
        attempts.append(real_path)
        raise DeviceError("Could not send object info.")

    monkeypatch.setattr(LocalStorage, "_mkdir", refuse)
    return st, dev, fake, app, attempts


def untouched(st, dev, fake, attempts, backups, before):
    return (attempts == [] and fake.operations == [] and st.sessions == 0
            and apps_files(st) == before and not (st.root / "GARMIN/APPS/IN.BIN").exists()
            and not ciq._refusals(dev) and not list(backups.rglob("OUT.BIN")))


def test_flatpak_never_tries_to_create_a_hidden_app_folder(flatpak_spot, backups):
    st, dev, fake, _app, attempts = flatpak_spot
    before = apps_files(st)
    assert ciq.usb_dir_state(dev, st, "IQWatchFaces") == ciq.SANDBOXED
    assert ciq.prepare_app_dir(dev, st, "IQWatchFaces") is False

    new = uuid.UUID("11111111-2222-3333-4444-555555555555")
    FakeStore.queued = []
    client = CountingClient()
    client.device_messages = lambda unit_id: []
    rep = ciq.install_apps(dev, st, client, FakeStore(client), [store_app(new, "New Face")])
    assert FakeStore.queued == [["New Face"]]  # requested in Garmin Connect right away
    assert rep.for_phone == rep.sandboxed == ["New Face"] and rep.sandboxed_updates == []
    assert rep.installed == [] and rep.failed == [] and client.downloads == 0

    # A sync that finds the app in the queue leaves the device alone as well.
    rep = ciq.install_queued(dev, st, client, [FACE_MESSAGE], known={})
    assert rep.for_phone == rep.sandboxed == ["Face It"]
    assert client.downloads == 0 and client.acks == []
    assert untouched(st, dev, fake, attempts, backups, before)


def test_flatpak_still_creates_a_folder_that_is_really_missing(no_usb_apps, device_root):
    """No app file is hidden on this device, so its folder is not there: tried as before."""
    st, dev, attempts = no_usb_apps
    st.usb_blocked = lambda: True
    (device_root / "GARMIN/APPS/OUT.BIN").write_bytes(out_bin([(PRELOADED, 8, "Softball", True, "")]))
    assert ciq.usb_dir_state(dev, st, "IQWatchFaces") == ciq.MISSING
    assert ciq.prepare_app_dir(dev, st, "IQWatchFaces") is False and len(attempts) == 1


def test_flatpak_leaves_an_update_of_a_hidden_app_in_the_queue(flatpak_spot, backups):
    st, dev, fake, _app, attempts = flatpak_spot
    before, tree = apps_files(st), fake_tree(fake)
    FakeStore.queued = []
    client = CountingClient()
    client.device_messages = lambda unit_id: [FACE_MESSAGE]  # the update already waits
    rep = ciq.install_apps(dev, st, client, FakeStore(client), [store_app(FACE, "Face It")])
    assert FakeStore.queued == []  # not requested twice
    assert rep.for_phone == rep.sandboxed == rep.sandboxed_updates == ["Face It"]
    assert (rep.installed, rep.updated, rep.failed) == ([], [], [])
    assert client.downloads == 0 and client.acks == []  # stays in Garmin's queue
    assert fake_tree(fake) == tree  # the old file and the app list are as they were
    assert untouched(st, dev, fake, attempts, backups, before)


def test_flatpak_cli_install_only_queues_and_says_why(flatpak_spot, apps_cli, monkeypatch,
                                                      capsys, backups):
    from openexpress import cli
    from openexpress.cli import main

    st, dev, fake, _app, attempts = flatpak_spot
    new = uuid.UUID("11111111-2222-3333-4444-555555555555")
    monkeypatch.setattr(cli, "open_device", lambda args: (st, dev))
    monkeypatch.setattr(FakeStore, "account_apps",
                        lambda self, device, locale: [store_app(new, "New Face")])
    apps_cli.device_messages = lambda unit_id: []
    before = apps_files(st)

    assert main(["--path", str(st.root), "apps", "install", "New Face"]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "Install New Face 1.0 (1.0 kB)",
        ("  New Face: The Flatpak has no access to USB devices, so it cannot install apps on "
         "this device. The app will be queued in Garmin Connect and installed by the phone or "
         "Garmin Express."),
        "Dry run. Add --apply.",
    ]
    assert FakeStore.queued == []

    assert main(["--path", str(st.root), "apps", "install", "New Face", "--apply"]) == 0
    assert capsys.readouterr().out.splitlines()[2:] == [
        ("New Face: The Flatpak has no access to USB devices, so it cannot install apps on "
         "this device. The app waits in the queue in Garmin Connect and will be installed by "
         "the phone or Garmin Express."),
    ]
    assert FakeStore.queued == [["New Face"]] and apps_cli.downloads == 0
    assert untouched(st, dev, fake, attempts, backups, before)


def test_flatpak_cli_update_only_queues_and_says_why(flatpak_spot, apps_cli, monkeypatch,
                                                     capsys, backups):
    from openexpress import cli
    from openexpress.cli import main

    st, dev, fake, _app, attempts = flatpak_spot
    monkeypatch.setattr(cli, "open_device", lambda args: (st, dev))
    monkeypatch.setattr(FakeStore, "updates",
                        lambda self, device, installed, locale: [store_app(FACE, "Face It")])
    before = apps_files(st)

    assert main(["--path", str(st.root), "apps", "update"]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "Update Face It 1.0 (1.0 kB)",
        ("  Face It: The Flatpak has no access to USB devices, so it cannot update apps on "
         "this device. The update will be queued in Garmin Connect and installed by the "
         "phone or Garmin Express."),
        "Dry run. Add --apply.",
    ]
    assert main(["--path", str(st.root), "apps", "update", "--apply"]) == 0
    assert capsys.readouterr().out.splitlines()[2:] == [
        ("Face It: The Flatpak has no access to USB devices, so it cannot update apps on "
         "this device. The update waits in the queue in Garmin Connect and will be installed "
         "by the phone or Garmin Express."),
    ]
    assert FakeStore.queued == [["Face It"]] and apps_cli.downloads == 0
    assert untouched(st, dev, fake, attempts, backups, before)


def test_flatpak_cli_remove_changes_nothing_and_says_why(flatpak_spot, apps_cli, monkeypatch,
                                                         capsys, backups):
    from openexpress import cli
    from openexpress.cli import main

    st, dev, fake, _app, attempts = flatpak_spot
    monkeypatch.setattr(cli, "open_device", lambda args: (st, dev))
    before, tree = apps_files(st), fake_tree(fake)
    told = ("Face It: The Flatpak has no access to USB devices, so it cannot remove apps "
            "from this device. Remove it on the phone, in the Connect IQ or Garmin Connect "
            "app.")

    assert main(["--path", str(st.root), "apps", "remove", "Face It", "--apply"]) == 1
    assert capsys.readouterr().out.splitlines() == [
        told, "To remove it only from your account list, use --forget."]
    assert main(["--path", str(st.root), "apps", "remove", "Face It", "--forget"]) == 0
    assert capsys.readouterr().out.splitlines() == [
        told, "Would remove it from your account list. Add --apply."]
    assert FakeStore.forgotten == [] and fake_tree(fake) == tree
    assert untouched(st, dev, fake, attempts, backups, before)


def test_flatpak_window_texts(flatpak_spot):
    from openexpress.gui.app import MainWindow

    st, dev, _fake, app, _attempts = flatpak_spot
    assert ciq.removal_sandboxed(dev, st, app) and not ciq.removable_directly(dev, st, app)
    app.sandboxed = True
    assert MainWindow._forget_text(app) == (
        "The Flatpak has no access to USB devices, so it cannot remove apps from this "
        "device. Remove it on the phone, in the Connect IQ or Garmin Connect app.\n\n"
        "Here you can only remove it from your account list.")
    app.sandboxed = False
    assert MainWindow._forget_text(app).startswith("Face It cannot be removed over USB: ")
    text = MainWindow._for_phone_text(["Face It"], ["Face It"], ["Face It"])
    assert text == ("Face It: The Flatpak has no access to USB devices, so it cannot update "
                    "apps on this device. The update waits in the queue in Garmin Connect and "
                    "will be installed by the phone or Garmin Express.")
    assert MainWindow._for_phone_text(["Face It"], ["Face It"]).startswith(
        "Face It: The Flatpak has no access to USB devices, so it cannot install apps on this "
        "device. The app waits in the queue")
    # Outside the sandbox the wording is the old one.
    assert MainWindow._for_phone_text(["Face It"]).startswith("Face It: openExpress cannot")


def test_flatpak_sync_says_why_the_app_stays_in_the_queue(flatpak_spot, backups):
    import types

    from openexpress import service

    st, dev, fake, _app, attempts = flatpak_spot
    before = apps_files(st)

    class Client(CountingClient):
        def require_verified(self, unit_id=None):
            raise RuntimeError("names are cosmetic")

    s = types.SimpleNamespace(storage=st, device=dev, client=Client(),
                              settings=types.SimpleNamespace(locale="en_US"))
    errors = []
    lines = step_words(
        service._install_queued_apps(s, [FACE_MESSAGE], lambda m: None, errors))
    assert errors == [] and lines == [
        "The Flatpak has no access to USB devices; 1 Connect IQ app waits in the queue"]
    assert untouched(st, dev, fake, attempts, backups, before)


# --- an install is refused before anything is requested ---------------------------------------


NO_SLOT = "no free app slots on the device"
NO_SPACE = "not enough Connect IQ space on the device"
TWIN_A = uuid.UUID("aaaaaaaa-1111-4222-8333-444444444444")
TWIN_B = uuid.UUID("bbbbbbbb-1111-4222-8333-444444444444")


def device_reports(monkeypatch, slots=None, space=None):
    """The app list of the device gives these numbers in its header."""
    real = ciq.read_installed

    def read(device, storage):
        state = real(device, storage)
        if slots is not None:
            state.available_slots = slots
        if space is not None:
            state.available_space = space
        return state

    monkeypatch.setattr(ciq, "read_installed", read)


class RecordingStore:
    """Like FakeStore, for calls of ciq.install_apps: records the IDs that were requested."""

    def __init__(self):
        self.requested = []

    def queue_install(self, device, apps):
        self.requested += [a.app_id for a in apps]


def queue_client(store, apps):
    """Garmin's queue holds what was requested."""
    client = CountingClient()
    client.device_messages = lambda unit_id: [
        {"messageid": n, "devicexmldatatype": "IQWatchFaces",
         "metadata": {"messageurl": "x", "messagename": a.name,
                      "appdetails": {"id": str(a.app_id)}}}
        for n, a in enumerate(apps, 1) if a.app_id in store.requested]
    return client


def copies_made(monkeypatch):
    made = []
    real = ciq.save_app_list_copy
    monkeypatch.setattr(ciq, "save_app_list_copy",
                        lambda *a, **k: made.append(1) or real(*a, **k))
    return made


@pytest.mark.parametrize("slots,space,reason", [(0, None, NO_SLOT), (None, 999, NO_SPACE)])
def test_cli_install_dry_run_says_what_apply_would_refuse(slots, space, reason, iq_device,
                                                          device_root, apps_cli, monkeypatch,
                                                          capsys):
    from openexpress.cli import main

    st, _dev = iq_device
    device_reports(monkeypatch, slots, space)
    before = apps_files(st)
    assert main(["--path", str(device_root), "apps", "install", "Face It"]) == 1
    out = capsys.readouterr().out.splitlines()
    # The refusal, and no promise: no app list to be written, no "Dry run".
    assert out[1:] == [f"  ERROR Face It: {reason}"]
    assert FakeStore.queued == [] and apps_cli.downloads == 0 and apps_cli.acks == []
    assert apps_files(st) == before


@pytest.mark.parametrize("slots,space,reason", [(0, None, NO_SLOT), (None, 999, NO_SPACE)])
def test_cli_install_apply_refuses_before_asking_garmin(slots, space, reason, iq_device,
                                                        device_root, apps_cli, monkeypatch,
                                                        capsys):
    from openexpress.cli import main

    st, _dev = iq_device
    device_reports(monkeypatch, slots, space)
    made = copies_made(monkeypatch)
    before = apps_files(st)
    assert main(["--path", str(device_root), "apps", "install", "Face It", "--apply"]) == 1
    out = capsys.readouterr().out
    assert f"  ERROR Face It: {reason}" in out and "App list to be written" not in out
    # Not requested in Garmin Connect, so it does not stay in the account's queue.
    assert FakeStore.queued == [] and apps_cli.downloads == 0 and apps_cli.acks == []
    assert made == [] and apps_files(st) == before


def test_install_refusals_are_keyed_by_app_id(iq_device, monkeypatch):
    st, dev = iq_device
    device_reports(monkeypatch, slots=1)
    state = ciq.read_installed(dev, st)
    twins = [store_app(TWIN_A, "Twin"), store_app(TWIN_B, "Twin")]  # the same name
    assert ciq.install_refusals(dev, st, state, twins) == {TWIN_B: NO_SLOT}
    assert ciq.install_refusals(dev, st, state, twins[::-1]) == {TWIN_A: NO_SLOT}
    # An app that is on the device takes no new slot.
    listed = store_app(STORE_ID, "Connect IQ Store", "watch-app")
    assert ciq.install_refusals(dev, st, state, [listed, twins[0], twins[1]]) == {TWIN_B: NO_SLOT}
    device_reports(monkeypatch, slots=5)
    assert ciq.install_refusals(dev, st, ciq.read_installed(dev, st), twins) == {}


def test_install_refusals_add_up_the_space(iq_device, monkeypatch):
    st, dev = iq_device
    device_reports(monkeypatch, space=1500)  # each app is 1000 bytes
    state = ciq.read_installed(dev, st)
    twins = [store_app(TWIN_A, "One"), store_app(TWIN_B, "Two")]
    assert ciq.install_refusals(dev, st, state, twins) == {TWIN_B: NO_SPACE}


def test_install_with_one_free_slot_takes_the_first_of_two_apps_with_one_name(iq_device,
                                                                               monkeypatch):
    st, dev = iq_device
    device_reports(monkeypatch, slots=1)
    twins = [store_app(TWIN_A, "Twin"), store_app(TWIN_B, "Twin")]
    store = RecordingStore()
    client = queue_client(store, twins)
    rep = ciq.install_apps(dev, st, client, store, twins)
    assert store.requested == [TWIN_A]  # the second one is never requested
    assert rep.installed == ["Twin"] and rep.failed == [("Twin", NO_SLOT)]
    assert client.downloads == 1
    written = [entry[0] for entry in ciq.parse_in_bin(st.read("GARMIN/APPS/IN.BIN"))]
    assert TWIN_A in written and TWIN_B not in written


def test_install_with_no_free_slot_requests_nothing(iq_device, monkeypatch):
    st, dev = iq_device
    device_reports(monkeypatch, slots=0)
    made = copies_made(monkeypatch)
    before = apps_files(st)
    store = RecordingStore()
    asked = []
    client = CountingClient()
    client.device_messages = lambda unit_id: asked.append(1) or []
    rep = ciq.install_apps(dev, st, client, store, [store_app(FACE, "Face It")])
    assert rep.failed == [("Face It", NO_SLOT)] and rep.installed == rep.for_phone == []
    assert store.requested == [] and asked == [] and made == [] and apps_files(st) == before


def test_an_app_that_is_only_queued_is_not_refused_for_slots(flatpak_spot, monkeypatch):
    # The Flatpak cannot write it anyway: it is requested for the phone, as before.
    st, dev, _fake, _app, _attempts = flatpak_spot
    device_reports(monkeypatch, slots=0)
    new = uuid.UUID("11111111-2222-3333-4444-555555555555")
    FakeStore.queued = []
    client = CountingClient()
    client.device_messages = lambda unit_id: []
    rep = ciq.install_apps(dev, st, client, FakeStore(client), [store_app(new, "New Face")])
    assert FakeStore.queued == [["New Face"]]
    assert rep.for_phone == rep.sandboxed == ["New Face"] and rep.failed == []


def test_window_install_refuses_before_asking_garmin(iq_device, monkeypatch):
    import types

    from openexpress import service
    from openexpress.gui.app import MainWindow

    st, dev = iq_device
    device_reports(monkeypatch, slots=0)
    monkeypatch.setattr(service, "require_account_device", lambda client, unit_id: [])
    store = RecordingStore()
    monkeypatch.setattr(ciq, "StoreApi", lambda client: store)
    client = CountingClient()
    client.device_messages = lambda unit_id: []
    errors = []
    win = types.SimpleNamespace(
        session=types.SimpleNamespace(device=dev, storage=st, client=client),
        _updated_apps=set(), toast=lambda text: pytest.fail(text), progress=lambda text: None,
        error=lambda title, text: errors.append((title, text)),
        load_section=lambda key, force=False: None,
        run_task=RunNow())
    win._install_apps = lambda apps, allowed=(): MainWindow._install_apps(win, apps, allowed)
    MainWindow.install_apps(win, [store_app(FACE, "Face It")])
    assert errors == [("Connect IQ", f"Face It: {NO_SLOT}")]
    assert store.requested == [] and client.downloads == 0


# --- a file on the device that copying music would replace ------------------------------------

ONE, TWO = "Music/Unknown/Unknown/one.mp3", "Music/Unknown/Unknown/two.mp3"


@pytest.fixture
def songs(library):
    """The two playable files of the made-up library: 103 and 203 bytes."""
    return [library.parent / "one.mp3", library.parent / "sub" / "two.mp3"]


def on_device(st, path, size):
    target = st.root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"\x01" * size)


def test_copy_plan_tells_a_new_file_from_one_it_replaces(iq_device, songs):
    st, dev = iq_device
    asked = []
    real = st.size
    st.size = lambda path: asked.append(path) or real(path)
    tracks = music.scan(songs, dev)

    plan = music.plan_copy(tracks, dev, st)  # neither file is on the device
    assert [d for _t, d in plan.to_copy] == [ONE, TWO] and plan.replaced == {}
    assert music.replaced_text(plan) == ""

    on_device(st, ONE, 103)  # the same size: not copied
    on_device(st, TWO, 150)  # another size: copied over
    asked.clear()
    plan = music.plan_copy(tracks, dev, st)
    assert [d for _t, d in plan.already] == [ONE] and [d for _t, d in plan.to_copy] == [TWO]
    assert plan.replaced == {TWO: 150}
    assert asked == [ONE, TWO]  # one question per file, as before
    track = plan.to_copy[0][0]
    assert music.replaced_note(plan, track, TWO) == (
        "(replaces the file on the device: 150\u00a0B → 203\u00a0B)")
    assert music.replaced_note(plan, track, ONE) == ""
    assert music.replaced_text(plan) == "1 file on the device will be replaced."


def test_music_add_dry_run_marks_the_file_it_would_replace(iq_device, device_root, songs, capsys):
    from openexpress.cli import main

    st, _dev = iq_device
    on_device(st, TWO, 150)
    before = (st.root / TWO).read_bytes()
    argv = ["--path", str(device_root), "music", "add", *map(str, songs)]
    assert main(argv) == 0
    assert capsys.readouterr().out.splitlines() == [
        "Found 2 playable files; 2 to copy (306 B), 0 already on the device.",
        f"  + {ONE}",
        f"  ~ {TWO}  (replaces the file on the device: 150 B → 203 B)",
        "1 file on the device will be replaced.",
        "Dry run. Add --apply.",
    ]
    assert (st.root / TWO).read_bytes() == before and not (st.root / ONE).exists()

    assert main([*argv, "--apply"]) == 0
    out = capsys.readouterr().out
    assert f"  ~ {TWO}  (replaces the file on the device: 150 B → 203 B)" in out
    assert "1 file on the device will be replaced." in out
    assert (st.root / TWO).read_bytes() == songs[1].read_bytes()  # the new content


def test_music_add_without_a_replaced_file_reads_as_before(iq_device, device_root, songs, capsys):
    from openexpress.cli import main

    assert main(["--path", str(device_root), "music", "add", *map(str, songs)]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "Found 2 playable files; 2 to copy (306 B), 0 already on the device.",
        f"  + {ONE}", f"  + {TWO}", "Dry run. Add --apply."]


def test_replaced_files_are_counted_also_when_the_list_is_cut(iq_device, device_root, tmp_path,
                                                              capsys):
    from openexpress.cli import main

    st, _dev = iq_device
    many = tmp_path / "many"
    many.mkdir()
    for n in range(20):
        (many / f"{n:02}.mp3").write_bytes(b"ID3" + b"\x00" * 10)
    on_device(st, "Music/Unknown/Unknown/19.mp3", 5)  # the last one: below the cut
    assert main(["--path", str(device_root), "music", "add", str(many)]) == 0
    out = capsys.readouterr().out.splitlines()
    assert "  … and 5 more" in out and not any(line.startswith("  ~ ") for line in out)
    assert out[-2:] == ["1 file on the device will be replaced.", "Dry run. Add --apply."]


def test_music_playlist_dry_run_counts_the_replaced_files(iq_device, device_root, songs, capsys):
    from openexpress.cli import main

    st, _dev = iq_device
    on_device(st, ONE, 7)
    on_device(st, TWO, 7)
    m3u = songs[0].parent / "Both.m3u8"  # only entries that exist: this command stops at others
    m3u.write_text("one.mp3\nsub/two.mp3\n", encoding="utf-8")
    assert main(["--path", str(device_root), "music", "playlist", str(m3u)]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[-2:] == ["2 files on the device will be replaced.", "Dry run. Add --apply."]


def test_window_says_before_copying_that_files_are_replaced(iq_device, songs, library):
    from openexpress.gui.app import MainWindow

    st, dev = iq_device
    tracks = music.scan(songs, dev)
    plain = "306\u00a0B will be copied to the device (0 files already there)."
    assert MainWindow._copy_summary(music.plan_copy(tracks, dev, st)) == plain
    assert "replaced" not in MainWindow._playlist_summary(music.plan_playlist(library, dev, st))

    on_device(st, TWO, 150)
    assert MainWindow._copy_summary(music.plan_copy(tracks, dev, st)) == (
        plain + "\n\n1 file on the device will be replaced.")
    lines = MainWindow._playlist_summary(music.plan_playlist(library, dev, st)).splitlines()
    assert lines[:3] == ["3 tracks", "2 files to copy (306\u00a0B), 0 already on the device",
                         "1 file on the device will be replaced."]


def test_replaced_files_in_polish(iq_device, songs):
    from openexpress import i18n

    st, dev = iq_device
    on_device(st, TWO, 1500)
    i18n.setup("pl")
    try:
        plan = music.plan_copy(music.scan(songs, dev), dev, st)
        note = music.replaced_note(plan, plan.to_copy[1][0], TWO)
        counts = []
        for n in (1, 2, 5):
            plan.replaced = {str(k): 1 for k in range(n)}
            counts.append(music.replaced_text(plan))
    finally:
        i18n.setup("en")
    assert note == "(zastąpi plik na urządzeniu: 1,5\u00a0kB → 203\u00a0B)"
    assert counts == ["1 plik na urządzeniu zostanie zastąpiony.",
                      "2 pliki na urządzeniu zostaną zastąpione.",
                      "5 plików na urządzeniu zostanie zastąpionych."]


def test_cli_update_through_the_mounted_storage_announces_no_app_list(iq_device, device_root,
                                                                     apps_cli, monkeypatch,
                                                                     capsys):
    """An update of an app whose folder is in sight (an Edge 1050) goes next to the app's file
    under a new name and leaves the app list as it is: the dry run says nothing of an IN.BIN
    to be written, and --apply writes none (seen on the Edge, 28.10.2026). Over PTP the list
    is written again as it is: test_cli_update_of_a_hidden_app."""
    from openexpress.cli import main

    st, _dev = iq_device
    store_message = {"messageid": 8, "devicexmldatatype": "IQWatchApps",
                     "metadata": {"messageurl": "y", "messagename": "Connect IQ Store",
                                  "appdetails": {"id": str(STORE_ID)}}}
    monkeypatch.setattr(FakeStore, "updates", lambda self, device, installed, locale: [
        store_app(STORE_ID, "Connect IQ Store", "watch-app")])
    apps_cli.device_messages = lambda unit_id: [store_message]
    before = apps_files(st)
    assert main(["--path", str(device_root), "apps", "update"]) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0].startswith("Update Connect IQ Store")
    assert "IN.BIN" not in out
    assert "Before any change, a copy of the app list of the device is made." in out
    assert apps_files(st) == before

    assert main(["--path", str(device_root), "apps", "update", "--apply"]) == 0
    assert st.resolve("GARMIN/APPS/IN.BIN") is None
    assert set(before) < set(apps_files(st))  # the new file next to the old one
