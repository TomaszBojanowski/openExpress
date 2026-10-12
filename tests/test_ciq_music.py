import contextlib
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


@pytest.fixture
def apps_cli(monkeypatch, xdg):
    from openexpress import cli, service

    client = CountingClient()
    client.device_messages = lambda unit_id: [FACE_MESSAGE]
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
        "Dry run. Add --apply.",
    ]
    assert FakeStore.queued == [] and apps_cli.downloads == 0 and apps_files(st) == before


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
    client.device_messages = lambda unit_id: pytest.fail("the queue is not even read")
    rep = ciq.install_apps(dev, st, client, FakeStore(client), [store_app(FACE, "Face It")])
    assert FakeStore.queued == [["Face It"]]  # queued at Garmin, for the phone
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
    lines = service._install_queued_apps(s, [FACE_MESSAGE], lambda m: None, errors)
    assert errors == []  # not an error
    assert lines == ["1 Connect IQ app waits in the queue: openExpress cannot install apps on "
                     "this device over USB yet."]


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
        run_task=lambda title, fn, done=None, failed=None: done(fn()))
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
    client.device_messages = lambda unit_id: [FACE_MESSAGE]
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


def test_update_of_a_hidden_app_is_left_for_the_phone(hidden_apps):
    st, dev, fake = hidden_apps
    message = {"messageid": 9, "devicexmldatatype": "IQWatchFaces",
               "metadata": {"messageurl": "x", "messagename": "Connect IQ Store",
                            "appdetails": {"id": str(STORE_ID)}}}
    client = CountingClient()
    rep = ciq.install_queued(dev, st, client, [message], known={}, direct=True)
    assert rep.for_phone == ["Connect IQ Store"] and rep.installed == [] and rep.failed == []
    assert client.downloads == 0 and st.sessions == 0 and fake.operations == []


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
    lines = service._install_queued_apps(s, [FACE_MESSAGE], lambda m: None, errors)
    assert errors == [] and lines == [
        "1 Connect IQ app waits in the queue: openExpress cannot install apps on "
        "this device over USB yet."]
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
