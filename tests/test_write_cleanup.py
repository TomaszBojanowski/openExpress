"""A whole file written to the device at once (Storage.write: files from Garmin Connect,
TrueUp, the unlock file of a map, playlists, app files) never leaves half of it under its
name, as write_file never does (review of 22.10.2026, risk A). Over MTP what a copy that
touched the file left is deleted (_discard_partial); on a drive the file is written under a
temporary name and renamed when whole; a copy that never touched the file leaves the old one
as it was. The error is said by each caller as before. All data is made up."""

import posixpath
import uuid

import pytest
from fakes import RunNow, SyncClient
from test_ciq_music import FACE, STORE_ID, iq_device, out_bin  # noqa: F401 - the fixtures
from test_updates_system import disk_full_halfway

from openexpress import ciq, maps, music, sync, trueup
from openexpress.device.storage import PART_SUFFIX, CopyNotStarted, DeviceError, LocalStorage
from openexpress.device.xmldevice import parse


class Mtp(LocalStorage):
    """A device as gvfs-mtp writes it: a file is replaced in place (the old one goes first), a
    copy that stops half way leaves half the file under its name, and what a failed copy left
    is cleaned up as GioStorage does it (_discard_partial: only a file the copy touched).
    Writes of names ending in one of `breaks` stop half way (the first `times` of them);
    those ending in one of `refused` are refused before the copy starts."""

    replaces_atomically = False

    def __init__(self, root, breaks=(), refused=(), times=None):
        super().__init__(root)
        self.breaks, self.refused, self.times = breaks, refused, times
        self.touched: set[str] = set()

    def _write(self, real_path, data):
        name = posixpath.basename(real_path)
        if name.endswith(self.refused):
            raise CopyNotStarted(f"Cannot write {real_path}: the copy could not start")
        target = self._p(real_path)
        if name.endswith(self.breaks) and (self.times is None or self.times > 0):
            if self.times is not None:
                self.times -= 1
            self.touched.add(real_path)
            target.write_bytes(data[: len(data) // 2])
            raise DeviceError(f"Cannot write {real_path}: the device is gone")
        target.write_bytes(data)

    def _discard_partial(self, real_path):
        if real_path in self.touched:
            self.touched.discard(real_path)
            self._p(real_path).unlink(missing_ok=True)


# --- the storages themselves ---------------------------------------------------------------------

@pytest.mark.parametrize("old", [b"OLD", None])
def test_a_drive_write_cut_short_keeps_the_old_file(device_root, monkeypatch, old):
    """A drive: a write that fails half way (disk full) leaves the old file as it was, or no
    file when there was none, and no temporary one."""
    folder = device_root / "GARMIN" / "NewFiles"
    folder.mkdir(parents=True, exist_ok=True)
    if old is not None:
        (folder / "x.FIT").write_bytes(old)
    disk_full_halfway(monkeypatch, "x.FIT")
    with pytest.raises(DeviceError):
        LocalStorage(device_root).write("GARMIN/NewFiles/x.FIT", b"NEW DATA, LONGER")
    assert ((folder / "x.FIT").read_bytes() if (folder / "x.FIT").exists() else None) == old
    assert not [p.name for p in folder.iterdir() if p.name.endswith(PART_SUFFIX)]


def test_a_drive_write_is_whole_once_written(device_root):
    folder = device_root / "GARMIN" / "NewFiles"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "x.FIT").write_bytes(b"OLD")
    LocalStorage(device_root).write("GARMIN/NewFiles/x.FIT", b"NEW")
    assert (folder / "x.FIT").read_bytes() == b"NEW"
    assert sorted(p.name for p in folder.iterdir()) == ["x.FIT"]


def _gio(monkeypatch, files, copy):
    """GioStorage as it writes over gvfs, on a device holding `files` (real path: bytes);
    `copy(data, real, files)` is the copy of the bytes (gio copy)."""
    from openexpress.device import gio_storage

    monkeypatch.setattr(gio_storage, "in_flatpak", lambda: False)
    st = gio_storage.GioStorage.__new__(gio_storage.GioStorage)
    st._attempts = {}  # as __init__ makes it
    st._target = lambda path: path
    st._size_now = lambda real: len(files[real]) if real in files else None
    st._delete = lambda real: files.pop(real)
    st._copy_local = lambda local, real, progress, what: copy(local.read_bytes(), real, files)
    return st


def _breaks_half_way(data, real, files):
    files[real] = data[: len(data) // 2]
    raise DeviceError(f"Cannot write {real}: the device is gone")


def _refused_at_once(data, real, files):
    raise DeviceError(f"Cannot write {real}: Operation not supported")


@pytest.mark.parametrize("old", [b"OLD", None])
def test_mtp_deletes_what_a_copy_cut_short_left(monkeypatch, old):
    """Over MTP the copy touched the file (a new one, or one of another size now): what it left
    is deleted; never half a file under the name."""
    files = {} if old is None else {"GARMIN/NewFiles/x.FIT": old}
    st = _gio(monkeypatch, files, _breaks_half_way)
    with pytest.raises(DeviceError, match="the device is gone"):
        st.write("GARMIN/NewFiles/x.FIT", b"NEW DATA, LONGER")
    assert "GARMIN/NewFiles/x.FIT" not in files


def test_mtp_keeps_the_old_file_when_the_copy_did_not_touch_it(monkeypatch):
    files = {"GARMIN/NewFiles/x.FIT": b"OLD"}
    st = _gio(monkeypatch, files, _refused_at_once)
    with pytest.raises(DeviceError, match="Operation not supported"):
        st.write("GARMIN/NewFiles/x.FIT", b"NEW DATA")
    assert files == {"GARMIN/NewFiles/x.FIT": b"OLD"}


def test_mtp_write_that_goes_well(monkeypatch):
    files = {"GARMIN/NewFiles/x.FIT": b"OLD"}
    st = _gio(monkeypatch, files, lambda data, real, files: files.__setitem__(real, data))
    assert st.write("GARMIN/NewFiles/x.FIT", b"NEW") == "GARMIN/NewFiles/x.FIT"
    assert files == {"GARMIN/NewFiles/x.FIT": b"NEW"} and st._attempts == {}


# --- the callers: a file from Garmin Connect, TrueUp, a map's unlock, a playlist, an app ---------

def _files_in(st, folder: str) -> list[str]:
    """The files in a folder of the device, its name in any letter case (as the device and
    the storage take it)."""
    real = st.resolve(folder)
    return [] if real is None else sorted(e.name for e in st.listdir(real) if not e.is_dir)


def test_a_download_cut_short_is_said_and_leaves_nothing(device_root, tmp_path):
    """A course from the queue: the failure is in the report, no half file is left in the
    folder, and it is not acknowledged (the next sync writes it again)."""
    st = Mtp(device_root, breaks=(".FIT",))
    dev = parse(st.read("GARMIN/GarminDevice.xml"))
    state = sync.SyncState(dev.unit_id, base=tmp_path / "state")
    items = sync.plan_downloads(dev, [
        {"messageid": 7, "messagetype": "file", "devicexmldatatype": "FIT_TYPE_6",
         "metadata": {"messageurl": "course/7", "messagename": "Route"}}])
    client = SyncClient()
    rep = sync.run_downloads(items, st, client, state=state)
    assert len(rep.failed) == 1 and "the device is gone" in rep.failed[0][1]
    assert not rep.written and client.acks == []
    assert _files_in(st, items[0].target.path) == []


def _trueup_device(device_root):
    xml = (device_root / "GARMIN/GarminDevice.xml").read_text().replace(
        "</MassStorageMode>",
        "<DataType><Name>FIT_TYPE_39</Name><File><Location><Path>Garmin/NewFiles</Path>"
        "<FileExtension>FIT</FileExtension></Location><TransferDirection>InputToUnit"
        "</TransferDirection></File></DataType></MassStorageMode>")
    return parse(xml.encode())


def test_trueup_cut_short_is_said_and_leaves_nothing(device_root):
    dev = _trueup_device(device_root)
    true_up = {"messageid": 2, "messagetype": "true_up", "devicexmldatatype": "FIT_TYPE_39",
               "metadata": {"messageurl": "tu"}}

    class Client:
        acks = []

        def download_message(self, url, absolute):
            return b"TRUEUP DATA"

        def ack_message(self, mid):
            self.acks.append(mid)

    st = Mtp(device_root, breaks=(".FIT",))
    with pytest.raises(DeviceError, match="the device is gone"):
        trueup.run(dev, st, Client(), [true_up])
    assert _files_in(st, "Garmin/NewFiles") == []


def test_an_unlock_file_that_never_gets_written_leaves_none(device_root):
    """Six tries, each cut short: the error, and no half unlock file (the device could take
    it for the map's)."""
    st = Mtp(device_root, breaks=(".gma",))
    (device_root / "Garmin" / "Maps").mkdir(parents=True, exist_ok=True)
    with pytest.raises(maps.MapError):
        maps._write_unlock(st, "Garmin/Maps/abc.gma", b"UNLOCK DATA", lambda text: None,
                           lambda seconds: None)
    assert not (device_root / "Garmin" / "Maps" / "abc.gma").exists()


def test_an_unlock_file_cut_short_once_is_whole_after_the_next_try(device_root):
    st = Mtp(device_root, breaks=(".gma",), times=1)
    (device_root / "Garmin" / "Maps").mkdir(parents=True, exist_ok=True)
    maps._write_unlock(st, "Garmin/Maps/abc.gma", b"UNLOCK DATA", lambda text: None,
                       lambda seconds: None)
    assert (device_root / "Garmin" / "Maps" / "abc.gma").read_bytes() == b"UNLOCK DATA"


def test_an_unlock_file_refused_leaves_the_old_one(device_root):
    folder = device_root / "Garmin" / "Maps"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "abc.gma").write_bytes(b"OLD UNLOCK")
    with pytest.raises(maps.MapError):
        maps._write_unlock(Mtp(device_root, refused=(".gma",)), "Garmin/Maps/abc.gma",
                           b"NEW UNLOCK", lambda text: None, lambda seconds: None)
    assert (folder / "abc.gma").read_bytes() == b"OLD UNLOCK"


def test_a_playlist_cut_short_is_said_and_leaves_nothing(iq_device):
    st, dev = iq_device
    with pytest.raises(DeviceError, match="the device is gone"):
        music.write_playlist(dev, Mtp(st.root, breaks=(".m3u8",)), "Mix", ["Music/a.mp3"])
    assert not (st.root / "Music" / "Mix.m3u8").exists()


def test_a_playlist_refused_leaves_the_old_one(iq_device):
    st, dev = iq_device
    (st.root / "Music").mkdir(exist_ok=True)
    (st.root / "Music" / "Mix.m3u8").write_bytes(b"Music/old.mp3\n")
    with pytest.raises(DeviceError):
        music.write_playlist(dev, Mtp(st.root, refused=(".m3u8",)), "Mix", ["Music/a.mp3"])
    assert (st.root / "Music" / "Mix.m3u8").read_bytes() == b"Music/old.mp3\n"


UPDATED = uuid.UUID("a1b2c3d4-0000-4000-8000-0000000f00d5")


def _app_to_update(st):
    """An app from the store on the device (its file in MEDIA), and its update waiting in the
    queue of Garmin Connect."""
    (st.root / "GARMIN/APPS/OUT.BIN").write_bytes(out_bin([
        (STORE_ID, 1, "Connect IQ Store", False, "DADE7E4"),
        (UPDATED, 3, "Made-up Face", False, "ABCD1234")]))
    media = st.root / "GARMIN/APPS/MEDIA"
    media.mkdir(parents=True, exist_ok=True)
    (media / "ABCD1234.PRG").write_bytes(b"OLD APP")
    return media / "ABCD1234.PRG", [{
        "messageid": 9, "devicexmldatatype": "IQWatchFaces",
        "metadata": {"messageurl": "x", "messagename": "Made-up Face",
                     "appdetails": {"id": str(UPDATED)}}}]


class Downloads:
    acks: list = []

    def __init__(self):
        self.acks = []

    def download_message(self, url, absolute):
        return b"NEW APP, A LONGER FILE"

    def ack_message(self, mid):
        self.acks.append(mid)


def test_an_app_update_cut_short_leaves_no_half_app(iq_device):
    """The update of an app over the mounted storage stops half way: the failure is in the
    report, no half app file stays under its name, and the update stays in the queue."""
    st, dev = iq_device
    prg, messages = _app_to_update(st)
    client = Downloads()
    rep = ciq.install_queued(dev, Mtp(st.root, breaks=(".PRG",)), client, messages, known={})
    assert [name for name, _error in rep.failed] == ["Made-up Face"]
    assert not prg.exists() and client.acks == []


def test_an_app_update_refused_leaves_the_old_app(iq_device):
    st, dev = iq_device
    prg, messages = _app_to_update(st)
    client = Downloads()
    rep = ciq.install_queued(dev, Mtp(st.root, refused=(".PRG",)), client, messages, known={})
    assert [name for name, _error in rep.failed] == ["Made-up Face"]
    assert prg.read_bytes() == b"OLD APP" and client.acks == []
    assert FACE  # (the fixture's own face is not part of this test)


# --- the map whose unlock file could not be written: what the user is told (B a) -----------------

WINDOW_TEXT = ("The map files are on the device, but its unlock file Garmin/Maps/abc.gma could not "
               "be written (Cannot write Garmin/Maps/abc.gma: the device is gone). The device may "
               "still load the map after it is unplugged; if it does not, come back to this page.")
CLI_TEXT = ("The map files are on the device, but its unlock file Garmin/Maps/abc.gma could not "
            "be written (Cannot write Garmin/Maps/abc.gma: the device is gone). The device may "
            "still load the map after it is unplugged; if it does not, run openexpress maps "
            "again.")


def test_an_unlock_file_not_written_is_said_without_install_again(device_root):
    """Not "install the map again" (that is refused while its files are on the device): the
    files are there, the device may load the map once unplugged, else come back."""
    (device_root / "Garmin" / "Maps").mkdir(parents=True, exist_ok=True)
    with pytest.raises(maps.UnlockNotWritten) as error:
        maps._write_unlock(Mtp(device_root, breaks=(".gma",)), "Garmin/Maps/abc.gma",
                           b"UNLOCK DATA", lambda text: None, lambda seconds: None)
    assert isinstance(error.value, maps.MapError)
    assert str(error.value) == CLI_TEXT
    assert error.value.window_text == WINDOW_TEXT


@pytest.mark.parametrize("language", ["en", "pl"])
def test_neither_text_says_to_install_again(device_root, language):
    from openexpress import i18n

    i18n.setup(language)
    try:
        error = maps.UnlockNotWritten("Garmin/Maps/abc.gma", "x")
        for text in (str(error), error.window_text):
            assert "install" not in text.casefold() and "zainstaluj" not in text.casefold()
        if language == "pl":
            assert error.window_text.endswith("wróć do tej strony.")
    finally:
        i18n.setup("en")


@pytest.mark.needs_display
def test_the_window_says_the_text_of_the_window(device_root, monkeypatch):
    """The install of a map from the window fails on its unlock file: the error says to come
    back to this page (not what the command line says)."""
    import types

    from gi.repository import Adw

    from openexpress.gui import app

    questions, errors = [], []
    monkeypatch.setattr(Adw.AlertDialog, "present", lambda self, parent: questions.append(self))

    def install(*args, **kwargs):
        raise maps.UnlockNotWritten("Garmin/Maps/abc.gma",
                                    "Cannot write Garmin/Maps/abc.gma: the device is gone")

    monkeypatch.setattr(maps, "install", install)

    run_task = RunNow(error=lambda title, text: errors.append((title, text)))

    m = maps.UniversalMap(name="Test Map", map_type="TopoActive", state=0, is_reinstall=False,
                          can_uninstall=True, version="2026.11", region="Test", identifier={},
                          files=[], files_to_remove=[], hosts=[])
    win = types.SimpleNamespace(
        session=types.SimpleNamespace(storage=None, device=types.SimpleNamespace(raw=b""),
                                      client=types.SimpleNamespace(
                                          http=None,
                                          consumer=types.SimpleNamespace(url_salt="salt"))),
        maps_cache=[m], run_task=run_task, progress=None, fraction=None, toast=None,
        error=lambda title, text: errors.append((title, text)),
        load_section=lambda key, force=False: None)
    app.MainWindow._ask_install_map(win, m, 0, 0, [])
    questions[0].emit("response", "ok")
    assert errors == [("Installing Test Map…", WINDOW_TEXT)]
