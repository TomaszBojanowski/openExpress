"""Maps that are not from Garmin: the name in the header of an .img file, and their list."""

import json
import types

import pytest
import requests
from gi.repository import GLib

from openexpress import ownmaps
from openexpress.cli import main
from openexpress.device import gio_storage, storage
from openexpress.device.storage import DeviceError, LocalStorage


def img_header(title=b"Somewhere made-up map", key=0, size=512):
    """The start of an .img file, put together here: no real map is kept in the tests."""
    head = bytearray(size)
    head[0x10:0x17] = b"DSKIMG\0"
    head[0x41:0x48] = b"GARMIN\0"
    first, rest = title[:20], title[20:50]
    head[0x49:0x49 + 20] = first.ljust(20, b" ") if rest else first.ljust(20, b"\0")
    head[0x65:0x65 + len(rest)] = rest
    head[0x1FE:0x200] = b"\x55\xaa"
    data = bytes(b ^ key for b in head)
    return bytes([key]) + data[1:] if key else data


# --- the name in the header ---------------------------------------------------------------------


def test_title_comes_from_both_parts_of_the_description():
    title = b"Sometown cyclemap/latin1 Example.org 13-Oct-202 "  # 48 bytes, cut by its maker
    assert ownmaps.read_title(img_header(title)) == "Sometown cyclemap/latin1 Example.org 13-Oct-202"
    assert ownmaps.read_title(img_header(b"Short")) == "Short"
    assert ownmaps.read_title(img_header(b"x" * 50)) == "x" * 50  # no zero at the end


def test_title_of_a_file_that_is_xored():
    assert ownmaps.read_title(img_header(b"Hidden a little", key=0x5A)) == "Hidden a little"


@pytest.mark.parametrize("title, expected", [
    ("Gdańsk".encode("cp1250"), "Gdañsk"),          # another code page: still text
    (b"Caf\xe9 \x81\x8d map", "Café �� map"),  # bytes cp1252 does not have
    (b"Tab\there\x01\x02", "Tab here"),                       # control characters
    (b"\xff\xfe\xfd", "ÿþý"),
])
def test_title_with_bytes_outside_ascii_never_fails(title, expected):
    assert ownmaps.read_title(img_header(title)) == expected


@pytest.mark.parametrize("data", [
    b"", b"\0" * 100, b"ID3" + b"\0" * 600,          # empty, short, another kind of file
    img_header()[:511],                              # one byte short
    img_header().replace(b"DSKIMG", b"DSKIMX"),
    img_header().replace(b"GARMIN", b"GARMIX"),
    img_header()[:0x1FE] + b"\0\0",
])
def test_a_file_that_is_not_a_map_has_no_title(data):
    assert ownmaps.read_title(data) is None


@pytest.mark.parametrize("name, garmin", [
    ("D1234567A.img", True), ("DB123456A.IMG", True), ("d123456b.img", True),
    ("gmapbmap.img", True), ("gmaptz.img", True), ("gmapsupp.img", True), ("GMAPPROM.IMG", True),
    ("osm-test.img", False), ("Poland.img", False), ("D-day.img", False), ("map.img", False),
    ("Dabc.img", False),
])
def test_names_of_garmins_own_files(name, garmin):
    assert ownmaps.is_garmin_name(name) is garmin


# --- reading only the start of a file -----------------------------------------------------------


def test_read_start_of_a_local_file(tmp_path):
    (tmp_path / "Garmin").mkdir()
    (tmp_path / "Garmin" / "big.img").write_bytes(b"0123456789" * 1000)
    (tmp_path / "Garmin" / "tiny.img").write_bytes(b"abc")
    st = LocalStorage(tmp_path)
    assert st.read_start("Garmin/big.img", 16) == b"0123456789012345"
    assert st.read_start("GARMIN/tiny.img", 512) == b"abc"  # shorter than asked for
    with pytest.raises(DeviceError):
        st.read_start("Garmin/none.img", 16)


class FakeFile:
    """What `open` gives: records how it was opened, what was read and that it was closed."""

    def __init__(self, log, fail=False):
        self.log, self.fail = log, fail

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.log.append("closed")
        return False

    def read(self, count):
        self.log.append(("read", count))
        if self.fail:
            raise OSError(5, "Input/output error")
        return b"x" * count


@pytest.mark.parametrize("fail", [False, True])
def test_local_read_start_opens_for_reading_only_and_always_closes(tmp_path, monkeypatch, fail):
    (tmp_path / "a.img").write_bytes(b"data")
    log = []

    def fake_open(path, mode="r", *args, **kw):
        log.append(("open", mode))
        return FakeFile(log, fail)

    monkeypatch.setattr(storage, "open", fake_open, raising=False)
    st = LocalStorage(tmp_path)
    if fail:
        with pytest.raises(DeviceError):
            st.read_start("a.img", 512)
    else:
        assert st.read_start("a.img", 512) == b"x" * 512
    assert log == [("open", "rb"), ("read", 512), "closed"]


class FakeStream:
    def __init__(self, log, chunks, fail_close=False):
        self.log, self.chunks, self.fail_close = log, list(chunks), fail_close

    def read_bytes(self, count, cancellable):
        self.log.append(("read", count))
        chunk = self.chunks.pop(0) if self.chunks else b""
        if isinstance(chunk, Exception):
            raise chunk
        return types.SimpleNamespace(get_data=lambda: chunk[:count])

    def close(self, cancellable):
        self.log.append("closed")
        if self.fail_close:
            raise GLib.Error("close failed")


def gvfs_storage(monkeypatch, log, chunks, fail_close=False):
    st = gio_storage.GioStorage("mtp://made-up/Internal")
    monkeypatch.setattr(st, "resolve", lambda path: path)

    def opened(real):
        # Gio.File.read() opens for reading; nothing else is ever called on the file.
        return types.SimpleNamespace(read=lambda c: log.append("opened for reading")
                                     or FakeStream(log, chunks, fail_close))

    monkeypatch.setattr(st, "_f", opened)
    return st


def test_gvfs_read_start_reads_only_what_is_asked_and_closes(monkeypatch):
    log = []
    st = gvfs_storage(monkeypatch, log, [b"a" * 300, b"b" * 300])  # arrives in two pieces
    assert st.read_start("Garmin/x.img", 512) == b"a" * 300 + b"b" * 212
    assert log == ["opened for reading", ("read", 512), ("read", 212), "closed"]

    log.clear()
    st = gvfs_storage(monkeypatch, log, [b"abc"])  # a file shorter than asked for
    assert st.read_start("Garmin/x.img", 512) == b"abc"
    assert log[-1] == "closed"


@pytest.mark.parametrize("fail_close", [False, True])
def test_gvfs_read_start_closes_the_stream_after_an_error(monkeypatch, fail_close):
    log = []
    st = gvfs_storage(monkeypatch, log, [b"a" * 10, GLib.Error("device gone")], fail_close)
    with pytest.raises(DeviceError) as error:
        st.read_start("Garmin/x.img", 512)
    assert "Cannot read Garmin/x.img" in str(error.value)
    assert log == ["opened for reading", ("read", 512), ("read", 502), "closed"]


def test_gvfs_read_start_when_the_file_cannot_be_opened(monkeypatch):
    st = gio_storage.GioStorage("mtp://made-up/Internal")
    monkeypatch.setattr(st, "resolve", lambda path: path)

    def refuse(cancellable):
        raise GLib.Error("no such file")

    monkeypatch.setattr(st, "_f", lambda real: types.SimpleNamespace(read=refuse))
    with pytest.raises(DeviceError):
        st.read_start("Garmin/x.img", 512)


# --- the list of own maps ------------------------------------------------------------------------


def device(folder="Garmin"):
    """A device that keeps its maps in `folder`; None: it declares no maps."""
    place = [types.SimpleNamespace(path=folder)] if folder else []
    return types.SimpleNamespace(targets=lambda datatype: place if datatype == "NMaps" else [])


def garmin_map(*paths, remove=()):
    return types.SimpleNamespace(files=[types.SimpleNamespace(device_path=p) for p in paths],
                                 files_to_remove=list(remove))


@pytest.fixture
def map_folder(tmp_path):
    g = tmp_path / "Garmin"
    (g / "CustomMaps").mkdir(parents=True)
    (g / "D1234567A.img").write_bytes(img_header(b"A map of Garmin's"))
    (g / "gmaptz.img").write_bytes(img_header(b"Time zones"))
    (g / "Listed.img").write_bytes(img_header(b"On the server's list"))
    (g / "Old.img").write_bytes(img_header(b"To be removed by Garmin"))
    (g / "osm-test.img").write_bytes(img_header(b"Sometown cyclemap") + b"\0" * 1000)
    (g / "broken.img").write_bytes(b"not a map")
    (g / "notes.txt").write_text("not a map either")
    (g / "CustomMaps" / "inside.img").write_bytes(img_header(b"In a subfolder"))
    return tmp_path


def test_own_maps_are_what_garmin_does_not_know(map_folder):
    listed = [garmin_map("Garmin/Listed.img", remove=["GARMIN/OLD.IMG"])]
    own = ownmaps.list_own(device(), LocalStorage(map_folder), listed)
    assert [(m.filename, m.size, m.title) for m in own] == [
        ("broken.img", 9, None), ("osm-test.img", 1512, "Sometown cyclemap")]


def test_only_the_start_of_own_maps_is_read(map_folder, monkeypatch):
    st = LocalStorage(map_folder)
    asked = []
    real = st.read_start
    monkeypatch.setattr(st, "read_start", lambda path, count: asked.append((path, count))
                        or real(path, count))
    monkeypatch.setattr(st, "read", lambda path: pytest.fail("a whole file was read"))
    ownmaps.list_own(device(), st, [garmin_map("Garmin/Listed.img", remove=["Garmin/Old.img"])])
    assert asked == [("Garmin/broken.img", 512), ("Garmin/osm-test.img", 512)]


def test_a_map_whose_start_cannot_be_read_is_still_listed(map_folder, monkeypatch):
    st = LocalStorage(map_folder)

    def fail(path, count):
        raise DeviceError("Cannot read")

    monkeypatch.setattr(st, "read_start", fail)
    own = ownmaps.list_own(device(), st, [garmin_map("Garmin/Listed.img", "Garmin/Old.img")])
    assert [(m.filename, m.title) for m in own] == [("broken.img", None), ("osm-test.img", None)]


def test_a_device_without_a_map_folder_has_no_own_maps(map_folder):
    assert ownmaps.map_dir(device(None)) is None
    assert ownmaps.list_own(device(None), LocalStorage(map_folder), []) == []
    assert ownmaps.list_own(device("Garmin/Maps"), LocalStorage(map_folder), []) == []  # missing


# --- the "maps" command --------------------------------------------------------------------------

MAPS_TYPE = """<DataType><Name>NMaps</Name>
   <File><Location><Path>Garmin</Path><FileExtension>img</FileExtension></Location>
    <TransferDirection>InputToUnit</TransferDirection></File></DataType>
  <UpdateFile>"""
ANSWER = {"maps": [{
    "displayname": "TopoActive Somewhere", "maptype": "TopoActive", "installationstate": 1,
    "isreinstall": True, "canuninstall": True, "release": {"majorversion": 2026, "minorversion": 11},
    "filestoremove": [{"filename": "Garmin/Old.img"}],
    "installoptions": [{"ispreferred": True, "identifier": {}, "files": [{
        "filename": "Garmin/Listed.img", "datatype": "X",
        "deliverableoptions": [{"type": 1, "url": "a.img", "md5": None, "sizeinbytes": 512}]}]}],
}]}


@pytest.fixture
def maps_cli(device_root, xdg, monkeypatch):
    xml = device_root / "GARMIN" / "GarminDevice.xml"
    xml.write_text(xml.read_text().replace("<UpdateFile>", MAPS_TYPE, 1))

    def request(self, method, url, **kw):
        resp = requests.Response()
        resp.status_code, resp._content = 200, json.dumps(ANSWER).encode()
        return resp

    monkeypatch.setattr(requests.Session, "request", request)
    return device_root


def test_maps_command_without_own_maps_reads_as_before(maps_cli, capsys):
    (maps_cli / "GARMIN" / "Listed.img").write_bytes(img_header(b"Of Garmin"))
    (maps_cli / "GARMIN" / "gmaptz.img").write_bytes(img_header(b"Time zones"))
    assert main(["--path", str(maps_cli), "maps"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[-1].startswith("Free space on the device:") and "Own maps" not in "\n".join(out)


def test_maps_command_lists_own_maps_with_their_names(maps_cli, capsys):
    g = maps_cli / "GARMIN"
    (g / "Listed.img").write_bytes(img_header(b"Of Garmin"))
    (g / "osm-test.img").write_bytes(img_header(b"Sometown cyclemap/latin1 Example.org") + b"\0" * 1488)
    (g / "copy.img").write_bytes(b"not a map")
    before = sorted(p.name for p in maps_cli.rglob("*"))
    for argv in (["maps"], ["maps", "--all"]):
        assert main(["--path", str(maps_cli), *argv]) == 0
        out = capsys.readouterr().out.splitlines()
        at = out.index("Own maps (not from Garmin):")
        assert out[at - 1] == "" and out[at - 2].startswith("Free space on the device:")
        assert out[at + 1:] == [
            f"  {'copy.img':48} {'9 B':>9}  (name not read)",
            f"  {'osm-test.img':48} {'2.0 kB':>9}  Sometown cyclemap/latin1 Example.org",
        ]
    assert sorted(p.name for p in maps_cli.rglob("*")) == before  # nothing was written


def test_own_maps_in_polish(maps_cli, capsys):
    from openexpress import i18n

    (maps_cli / "GARMIN" / "copy.img").write_bytes(b"not a map")
    i18n.setup("pl")
    try:
        assert main(["--path", str(maps_cli), "maps"]) == 0
    finally:
        i18n.setup("en")
    out = capsys.readouterr().out
    assert "Własne mapy (nie od Garmina):" in out and "(nazwa nieodczytana)" in out
