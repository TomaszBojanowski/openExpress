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


# --- "maps add": copying a map of the user's to the device --------------------------------------

NOT_A_MAP = "Error: {} is not a Garmin map file (.img)."
BAD_NAME = ("Error: The name {} cannot be used: only letters, digits, spaces and . _ - ( ) are "
            "allowed, and it must end with .img.")
RESERVED = "Error: The name {} is reserved for Garmin's own maps. Choose another one with --name."
EXISTS = ("Error: A file named {} is already on the device; nothing is overwritten. Choose "
          "another name with --name.")
NO_FOLDER = "Error: This device has no folder for maps."


@pytest.fixture
def my_map(tmp_path):
    """A made-up map file of 2000 bytes on the computer."""
    path = tmp_path / "local" / "osm-test.img"
    path.parent.mkdir()
    path.write_bytes(img_header(b"Sometown cyclemap/latin1 Example.org") + b"\x07" * 1488)
    return path


def tree(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def state_of(device_root):
    from openexpress.device.xmldevice import parse

    return ownmaps.recorded(parse((device_root / "GARMIN/GarminDevice.xml").read_bytes()))


def refused(capsys, device_root, argv, message):
    """The command fails with exactly this message, in a dry run and with --apply, and
    neither the device nor the list of own maps changes."""
    before = tree(device_root)
    for extra in ([], ["--apply"]):
        assert main(["--path", str(device_root), "maps", "add", *argv, *extra]) == 1
        captured = capsys.readouterr()
        lines = [line for line in captured.err.splitlines() if not line.startswith("Do not unplug")]
        assert lines == [message] and captured.out == ""
        assert tree(device_root) == before and state_of(device_root) == []


@pytest.mark.parametrize("name", ["osm-test.img", "My map (v2).img", "a.img", "A-b_c.1.img",
                                  "x" * 60 + ".img"])
def test_names_a_map_of_the_user_may_have(name):
    assert ownmaps.valid_name(name)


@pytest.mark.parametrize("name", [
    "osm-test", "osm-test.IMG", "osm-test.img ", " osm.img", ".hidden.img", "a/b.img", "a\\b.img",
    "Gdańsk.img", "a:b.img", "a*.img", "tab\t.img", "", ".img", "x" * 61 + ".img", "a.img.txt",
])
def test_names_that_are_refused(name):
    assert not ownmaps.valid_name(name)


def test_add_refuses_a_file_that_is_not_a_map(maps_cli, tmp_path, capsys):
    song = tmp_path / "song.img"
    song.write_bytes(b"ID3" + b"\0" * 2000)
    refused(capsys, maps_cli, [str(song)], NOT_A_MAP.format("song.img"))
    refused(capsys, maps_cli, [str(tmp_path / "none.img")],
            f"Error: Not found: {tmp_path / 'none.img'}")


@pytest.mark.parametrize("name", ["Gdańsk.img", "map", "a/b.img", "x" * 61 + ".img"])
def test_add_refuses_a_name_it_would_have_to_change(maps_cli, my_map, capsys, name):
    refused(capsys, maps_cli, [str(my_map), "--name", name], BAD_NAME.format(name))


def test_add_refuses_the_local_name_too(maps_cli, my_map, capsys):
    odd = my_map.with_name("moja mapa żółw.img")
    my_map.rename(odd)
    refused(capsys, maps_cli, [str(odd)], BAD_NAME.format(odd.name))


def test_local_checks_come_before_the_device_and_the_server(maps_cli, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(requests.Session, "request",
                        lambda *a, **k: pytest.fail("the server was asked"))
    song = tmp_path / "song.img"
    song.write_bytes(b"not a map")
    assert main(["--path", str(tmp_path / "no-device"), "maps", "add", str(song)]) == 1
    assert capsys.readouterr().err.strip() == NOT_A_MAP.format("song.img")


def test_add_on_a_device_without_a_map_folder(device_root, xdg, my_map, monkeypatch, capsys):
    def request(self, method, url, **kw):  # the answer for a device without maps
        resp = requests.Response()
        resp.status_code, resp._content = 200, b'{"maps": []}'
        return resp

    monkeypatch.setattr(requests.Session, "request", request)
    refused(capsys, device_root, [str(my_map)], NO_FOLDER)


@pytest.mark.parametrize("name", [
    "D1234567A.img", "gmapsupp.img", "gmaptz.img",  # named like Garmin's files
    "Listed.img", "LISTED.img",                     # a file of a map the server lists
    "Old.img",                                      # a file a newer version removes
])
def test_add_refuses_names_of_garmins_maps(maps_cli, my_map, capsys, name):
    refused(capsys, maps_cli, [str(my_map), "--name", name], RESERVED.format(name))


def test_add_refuses_a_map_garmin_lists_but_has_not_installed(maps_cli, my_map, monkeypatch,
                                                              capsys):
    answer = json.loads(json.dumps(ANSWER))
    answer["maps"][0]["installationstate"] = 0
    resp = requests.Response()
    resp.status_code, resp._content = 200, json.dumps(answer).encode()
    monkeypatch.setattr(requests.Session, "request", lambda self, method, url, **kw: resp)
    refused(capsys, maps_cli, [str(my_map), "--name", "Listed.img"], RESERVED.format("Listed.img"))


@pytest.mark.parametrize("on_device", ["osm-test.img", "OSM-Test.IMG"])
def test_add_never_overwrites(maps_cli, my_map, capsys, on_device):
    (maps_cli / "GARMIN" / on_device).write_bytes(b"something else entirely")
    refused(capsys, maps_cli, [str(my_map)], EXISTS.format("osm-test.img"))


def test_add_refuses_when_there_is_not_enough_room(maps_cli, my_map, monkeypatch, capsys):
    from openexpress import maps as mp

    # The file fits, but not with the margin kept for maps.
    monkeypatch.setattr(LocalStorage, "free_space", lambda self: 2000 + mp.SPACE_MARGIN - 1)
    refused(capsys, maps_cli, [str(my_map)],
            "Error: Not enough space on the device: 2.0 kB needed, 52.4 MB free")


def test_add_dry_run_says_what_would_be_written(maps_cli, my_map, monkeypatch, capsys):
    monkeypatch.setattr(LocalStorage, "free_space", lambda self: 12_400_000_000)
    before = tree(maps_cli)
    assert main(["--path", str(maps_cli), "maps", "add", str(my_map)]) == 0
    captured = capsys.readouterr()
    assert captured.out.splitlines() == [
        "Map: Sometown cyclemap/latin1 Example.org",
        "  osm-test.img (2.0 kB) will be written to Garmin/osm-test.img",
        "Free space on the device: 12.4 GB now, 12.4 GB after",
        "Dry run. Add --apply.",
    ]
    assert captured.err == "" and tree(maps_cli) == before and state_of(maps_cli) == []


def test_add_apply_writes_the_map_and_puts_it_on_the_list(maps_cli, my_map, capsys):
    assert main(["--path", str(maps_cli), "maps", "add", str(my_map), "--apply"]) == 0
    out = capsys.readouterr().out
    # The path as the device spells it (this made-up device calls the folder "GARMIN").
    assert "Wrote GARMIN/osm-test.img (2.0 kB)." in out and "Dry run" not in out
    written = next(p for p in maps_cli.rglob("osm-test.img"))
    assert written.read_bytes() == my_map.read_bytes()
    (entry,) = state_of(maps_cli)
    assert sorted(entry) == ["added", "filename", "size", "title"]
    assert (entry["filename"], entry["size"], entry["title"]) == (
        "osm-test.img", 2000, "Sometown cyclemap/latin1 Example.org")
    assert entry["added"].startswith("20") and len(entry["added"]) == 19

    assert main(["--path", str(maps_cli), "maps"]) == 0  # and "maps" shows it
    assert capsys.readouterr().out.splitlines()[-2:] == [
        "Own maps (not from Garmin):",
        f"  {'osm-test.img':48} {'2.0 kB':>9}  Sometown cyclemap/latin1 Example.org"]

    # Again: the file is there now.
    assert main(["--path", str(maps_cli), "maps", "add", str(my_map), "--apply"]) == 1
    assert EXISTS.format("osm-test.img") in capsys.readouterr().err
    assert len(state_of(maps_cli)) == 1


def test_add_with_another_name(maps_cli, my_map, capsys):
    argv = ["--path", str(maps_cli), "maps", "add", str(my_map), "--name", "Gdynia (bike).img"]
    assert main([*argv, "--apply"]) == 0
    assert "Wrote GARMIN/Gdynia (bike).img" in capsys.readouterr().out
    assert [m["filename"] for m in state_of(maps_cli)] == ["Gdynia (bike).img"]
    assert not list(maps_cli.rglob("osm-test.img"))


class FailingWrite(LocalStorage):
    """The copy breaks off: half of the file is on the device, then an error."""

    keep_the_rest = False  # the device refuses to delete, too

    def _write_file(self, real_path, local, progress=None):
        self._p(real_path).write_bytes(local.read_bytes()[:1000])
        if type(self).short_only:
            return
        raise DeviceError("Cannot write: device gone")

    def _delete(self, real_path):
        if type(self).keep_the_rest:
            raise DeviceError("Cannot delete")
        super()._delete(real_path)


def failing(monkeypatch, short_only=False, keep_the_rest=False):
    from openexpress import cli

    FailingWrite.short_only, FailingWrite.keep_the_rest = short_only, keep_the_rest
    monkeypatch.setattr(cli, "LocalStorage", FailingWrite)


@pytest.mark.parametrize("short_only, reason", [
    (False, "Cannot write: device gone"),
    (True, "the file on the device has 1000 B instead of 2000 B"),  # written, but cut short
])
def test_a_failed_copy_removes_its_own_incomplete_file(maps_cli, my_map, monkeypatch, capsys,
                                                       short_only, reason):
    failing(monkeypatch, short_only)
    before = tree(maps_cli)
    assert main(["--path", str(maps_cli), "maps", "add", str(my_map), "--apply"]) == 1
    captured = capsys.readouterr()
    assert captured.err.splitlines()[-1] == (
        "Error: Writing Garmin/osm-test.img failed; the incomplete file was removed from the "
        f"device. ({reason})")
    assert "Wrote" not in captured.out
    assert tree(maps_cli) == before and state_of(maps_cli) == []


def test_a_failed_copy_that_cannot_be_cleaned_up_says_so(maps_cli, my_map, monkeypatch, capsys):
    failing(monkeypatch, keep_the_rest=True)
    assert main(["--path", str(maps_cli), "maps", "add", str(my_map), "--apply"]) == 1
    assert ("Error: Writing Garmin/osm-test.img failed and the incomplete file could not be "
            "removed; delete Garmin/osm-test.img from the device yourself."
            ) in capsys.readouterr().err
    assert state_of(maps_cli) == []


def test_a_copy_that_left_nothing_behind(maps_cli, my_map, monkeypatch, capsys):
    from openexpress import cli

    class Nothing(LocalStorage):
        def _write_file(self, real_path, local, progress=None):
            raise DeviceError("Cannot write: refused")

    monkeypatch.setattr(cli, "LocalStorage", Nothing)
    assert main(["--path", str(maps_cli), "maps", "add", str(my_map), "--apply"]) == 1
    assert capsys.readouterr().err.splitlines()[-1] == (
        "Error: Writing Garmin/osm-test.img failed. (Cannot write: refused)")


def test_a_file_that_appeared_meanwhile_is_not_touched(map_folder, my_map):
    st = LocalStorage(map_folder)
    dev = device()
    dev.unit_id = 4000000001  # made up
    plan = ownmaps.plan_add(dev, st, [], my_map, "new.img")
    (map_folder / "Garmin" / "new.img").write_bytes(b"someone else's file")  # after the plan
    with pytest.raises(ownmaps.MapError) as error:
        ownmaps.add(plan, dev, st)
    assert "already on the device" in str(error.value)
    assert (map_folder / "Garmin" / "new.img").read_bytes() == b"someone else's file"


def test_the_list_of_own_maps_stays_out_of_the_log(maps_cli, my_map, log_file, capsys):
    assert main(["--path", str(maps_cli), "maps", "add", str(my_map), "--apply"]) == 0
    capsys.readouterr()
    log = log_file.read_text(encoding="utf-8")
    assert "own map added: 2_000 B" in log
    for private in ("osm-test", "Sometown", "ownmaps.json", str(my_map)):
        assert private not in log


def test_refusals_keep_the_names_out_of_the_log_too(maps_cli, my_map, tmp_path, log_file, capsys):
    (maps_cli / "GARMIN" / "osm-test.img").write_bytes(b"taken")
    song = tmp_path / "holiday-song.img"
    song.write_bytes(b"not a map")
    for argv in ([str(my_map)], [str(my_map), "--name", "D1234567A.img"],
                 [str(my_map), "--name", "private name.txt"], [str(song)]):
        assert main(["--path", str(maps_cli), "maps", "add", *argv]) == 1
    shown = capsys.readouterr().err
    assert "osm-test.img" in shown and "holiday-song.img" in shown  # the user sees them
    log = log_file.read_text(encoding="utf-8")
    assert log.count("error: MapError:") == 4
    for private in ("osm-test", "holiday-song", "private name", "Sometown", str(tmp_path)):
        assert private not in log


def test_add_in_polish(maps_cli, my_map, capsys):
    from openexpress import i18n

    (maps_cli / "GARMIN" / "taken.img").write_bytes(b"x")
    i18n.setup("pl")
    try:
        assert main(["--path", str(maps_cli), "maps", "add", str(my_map)]) == 0
        out = capsys.readouterr().out.splitlines()
        assert main(["--path", str(maps_cli), "maps", "add", str(my_map), "--name", "taken.img"]) == 1
        err = capsys.readouterr().err
    finally:
        i18n.setup("en")
    assert out[0] == "Mapa: Sometown cyclemap/latin1 Example.org"
    assert out[1] == "  osm-test.img (2,0 kB) zostanie zapisany jako Garmin/osm-test.img"
    assert out[2].startswith("Wolne miejsce na urządzeniu: teraz ") and ", potem " in out[2]
    assert "Plik o nazwie taken.img jest już na urządzeniu; nic nie zostanie nadpisane." in err


# --- "maps remove --own": removing a map this installation copied -------------------------------

NOT_OURS = ("Error: {} was not copied to the device by this installation of openExpress (it may "
            "have been copied by hand or by another installation), so it is not removed. Delete "
            "it in the file manager if you want it gone.")
OTHER_SIZE = ("Error: {} on the device has {} B, but the map that was copied had {} B: it is not "
              "the same file, so it is not removed.")
LIKE_GARMINS = "Error: {} is named like one of Garmin's own maps, so it is not removed."
GONE = ("Error: {} is no longer on the device. Run the command with --apply to take it off the "
        "list of own maps.")


@pytest.fixture
def added(maps_cli, my_map, capsys):
    """The made-up device after `maps add --apply`: the map is on it and on the list."""
    assert main(["--path", str(maps_cli), "maps", "add", str(my_map), "--apply"]) == 0
    capsys.readouterr()
    return maps_cli


def state_file(device_root):
    from openexpress.device.xmldevice import parse

    return ownmaps._state_file(parse((device_root / "GARMIN/GarminDevice.xml").read_bytes()))


def remove_refused(capsys, device_root, name, message, modes=([], ["--apply"]), quiet=True):
    """The command fails with this message and neither the device nor the list changes.
    `quiet`: it is refused before anything is printed."""
    before, listed = tree(device_root), state_of(device_root)
    for extra in modes:
        assert main(["--path", str(device_root), "maps", "remove", "--own", name, *extra]) == 1
        captured = capsys.readouterr()
        lines = [line for line in captured.err.splitlines() if not line.startswith("Do not unplug")]
        assert lines == [message] and (captured.out == "" or not quiet)
        assert "Removed" not in captured.out
        assert tree(device_root) == before and state_of(device_root) == listed


def test_remove_own_refuses_a_map_this_installation_did_not_copy(added, capsys):
    (added / "GARMIN" / "by-hand.img").write_bytes(img_header(b"Copied in the file manager"))
    remove_refused(capsys, added, "by-hand.img", NOT_OURS.format("by-hand.img"))
    remove_refused(capsys, added, "nothing.img", NOT_OURS.format("nothing.img"))
    # A map of Garmin's is not on the list either.
    (added / "GARMIN" / "Listed.img").write_bytes(img_header(b"Of Garmin"))
    remove_refused(capsys, added, "Listed.img", NOT_OURS.format("Listed.img"))


def test_remove_own_refuses_a_file_of_another_size(added, capsys):
    (added / "GARMIN" / "osm-test.img").write_bytes(b"someone put another file here")
    remove_refused(capsys, added, "osm-test.img", OTHER_SIZE.format("osm-test.img", 29, 2000))


@pytest.mark.parametrize("name", ["D1234567A.img", "gmaptz.img", "Listed.img", "Old.img"])
def test_remove_own_never_touches_a_name_of_garmins(added, capsys, name):
    # Such a name cannot get onto the list through `maps add`; here it is put there by hand.
    f = state_file(added)
    f.write_text(json.dumps({"maps": [{"filename": name, "size": 512, "title": "x",
                                      "added": "2026-10-13T00:00:00"}]}))
    (added / "GARMIN" / name).write_bytes(img_header(b"Of Garmin"))
    remove_refused(capsys, added, name, LIKE_GARMINS.format(name))


def test_remove_own_dry_run_says_what_would_go(added, monkeypatch, capsys):
    monkeypatch.setattr(LocalStorage, "free_space", lambda self: 12_400_000_000)
    before, listed = tree(added), state_of(added)
    assert main(["--path", str(added), "maps", "remove", "--own", "OSM-TEST.IMG"]) == 0  # any case
    captured = capsys.readouterr()
    assert captured.out.splitlines() == [
        "Removing own map: Sometown cyclemap/latin1 Example.org",
        "  - GARMIN/osm-test.img  (2.0 kB)",
        "Free space on the device: 12.4 GB now, 12.4 GB after",
        "Dry run. Add --apply.",
    ]
    assert captured.err == "" and tree(added) == before and state_of(added) == listed


def test_remove_own_apply_deletes_the_file_and_then_the_entry(added, my_map, capsys):
    # A second map of ours stays, on the device and on the list.
    assert main(["--path", str(added), "maps", "add", str(my_map), "--name", "second.img",
                 "--apply"]) == 0
    capsys.readouterr()
    assert main(["--path", str(added), "maps", "remove", "--own", "osm-test.img", "--apply"]) == 0
    assert "Removed GARMIN/osm-test.img (2.0 kB)." in capsys.readouterr().out
    assert not list(added.rglob("osm-test.img")) and list(added.rglob("second.img"))
    assert [m["filename"] for m in state_of(added)] == ["second.img"]

    assert main(["--path", str(added), "maps", "remove", "--own", "second.img", "--apply"]) == 0
    capsys.readouterr()
    assert state_of(added) == []
    assert main(["--path", str(added), "maps"]) == 0
    assert "Own maps" not in capsys.readouterr().out  # the section is gone


class KeepsFiles(LocalStorage):
    """A device that answers a delete without deleting, or refuses it."""

    refuse = False

    def _delete(self, real_path):
        if type(self).refuse:
            raise DeviceError("Cannot delete: refused")


@pytest.mark.parametrize("refuse, message", [
    (False, "Error: GARMIN/osm-test.img is still on the device; nothing was taken off the list."),
    (True, "Error: Cannot delete: refused"),
])
def test_the_entry_stays_while_the_file_is_on_the_device(added, monkeypatch, capsys, refuse,
                                                         message):
    from openexpress import cli

    KeepsFiles.refuse = refuse
    monkeypatch.setattr(cli, "LocalStorage", KeepsFiles)
    # The plan was printed; then the removal failed.
    remove_refused(capsys, added, "osm-test.img", message, modes=(["--apply"],), quiet=False)


def test_a_map_that_is_gone_only_leaves_the_list(added, capsys):
    next(added.rglob("osm-test.img")).unlink()  # deleted in the file manager
    remove_refused(capsys, added, "osm-test.img", GONE.format("osm-test.img"), modes=([],))
    assert main(["--path", str(added), "maps", "remove", "--own", "osm-test.img", "--apply"]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "Took osm-test.img off the list of own maps; the file was not on the device."]
    assert state_of(added) == []


def test_a_file_that_changed_after_the_plan_is_not_deleted(map_folder, my_map):
    st = LocalStorage(map_folder)
    dev = device()
    dev.unit_id = 4000000002  # made up
    ownmaps.add(ownmaps.plan_add(dev, st, [], my_map, "mine.img"), dev, st)
    plan = ownmaps.plan_remove(dev, st, [], "mine.img")
    (map_folder / "Garmin" / "mine.img").write_bytes(b"replaced meanwhile")
    with pytest.raises(ownmaps.MapError) as error:
        ownmaps.remove(plan, dev, st)
    assert "not the same file" in str(error.value)
    assert (map_folder / "Garmin" / "mine.img").read_bytes() == b"replaced meanwhile"
    assert [m["filename"] for m in ownmaps.recorded(dev)] == ["mine.img"]


def test_remove_without_own_works_on_garmins_maps_as_before(added, capsys):
    (added / "GARMIN" / "Listed.img").write_bytes(img_header(b"Of Garmin"))
    assert main(["--path", str(added), "maps", "remove", "Somewhere"]) == 0
    out = capsys.readouterr().out
    assert "Removing map TopoActive Somewhere" in out and "Dry run. Add --apply." in out
    # And a map of the user's is not found among Garmin's.
    assert main(["--path", str(added), "maps", "remove", "osm-test.img"]) == 1
    assert "No map named 'osm-test.img'" in capsys.readouterr().err
    assert state_of(added) and list(added.rglob("osm-test.img"))


def test_removal_leaves_only_the_size_in_the_log(added, log_file, capsys):
    (added / "GARMIN" / "by-hand.img").write_bytes(img_header(b"Copied by hand"))
    assert main(["--path", str(added), "maps", "remove", "--own", "by-hand.img"]) == 1
    assert main(["--path", str(added), "maps", "remove", "--own", "osm-test.img", "--apply"]) == 0
    capsys.readouterr()
    log = log_file.read_text(encoding="utf-8")
    assert "own map removed: 2_000 B" in log and log.count("error: MapError:") == 1
    for private in ("osm-test", "by-hand", "Sometown", "ownmaps.json"):
        assert private not in log


def test_remove_own_in_polish(added, capsys):
    from openexpress import i18n

    (added / "GARMIN" / "by-hand.img").write_bytes(b"x")
    i18n.setup("pl")
    try:
        assert main(["--path", str(added), "maps", "remove", "--own", "osm-test.img"]) == 0
        out = capsys.readouterr().out.splitlines()
        assert main(["--path", str(added), "maps", "remove", "--own", "by-hand.img"]) == 1
        err = capsys.readouterr().err.strip()
    finally:
        i18n.setup("en")
    assert out[0] == "Usuwanie własnej mapy: Sometown cyclemap/latin1 Example.org"
    assert out[1] == "  - GARMIN/osm-test.img  (2,0 kB)"
    assert err == ("Błąd: Plik by-hand.img nie został skopiowany przez tę instalację openExpress "
                   "(mógł zostać skopiowany ręcznie albo przez inną instalację), więc nie "
                   "zostanie usunięty. Jeśli chcesz go usunąć, zrób to w menedżerze plików.")


# --- the Maps page of the window ------------------------------------------------------------------

WINDOW_HINT = "Rename the file on your computer and add it again."
NOT_OURS_NOTE = "not copied by this installation; remove it in the file manager"


def numbered(dev, unit_id):
    dev.unit_id = unit_id  # made up; names the folder of the device's state
    return dev


def test_overview_says_which_maps_can_be_removed(map_folder, my_map, xdg):
    st, dev = LocalStorage(map_folder), numbered(device(), 4000000003)
    ownmaps.add(ownmaps.plan_add(dev, st, [], my_map, "ours.img"), dev, st)
    listed = [garmin_map("Garmin/Listed.img", remove=["Garmin/Old.img"])]
    view = ownmaps.overview(dev, st, listed)
    assert view.folder == "Garmin" and not view.unreadable
    assert [(m.filename, m.path) for m in view.maps] == [
        ("broken.img", "Garmin/broken.img"), ("osm-test.img", "Garmin/osm-test.img"),
        ("ours.img", "Garmin/ours.img")]
    assert view.removable == {"ours.img"}  # the others were copied by hand


def test_overview_of_a_device_without_maps_and_of_a_folder_that_cannot_be_read(map_folder, xdg,
                                                                               monkeypatch):
    st = LocalStorage(map_folder)
    nothing = ownmaps.overview(numbered(device(None), 4000000004), st, [])
    assert (nothing.folder, nothing.maps, nothing.removable) == (None, [], set())

    def fail(path):
        raise DeviceError("Cannot list")

    monkeypatch.setattr(st, "listdir", fail)
    broken = ownmaps.overview(numbered(device(), 4000000004), st, [])
    assert broken.folder == "Garmin" and broken.unreadable and broken.maps == []


def test_refusals_carry_a_hint_for_the_place_they_are_shown_in(map_folder, my_map, xdg):
    st, dev = LocalStorage(map_folder), numbered(device(), 4000000005)
    cases = [("osm-test.img", "taken", "Choose another name with --name."),
             ("gmapsupp.img", "reserved", "Choose another one with --name."),
             ("bad nameł.img", "name", "")]
    for name, kind, cli_hint in cases:
        with pytest.raises(ownmaps.OwnMapError) as error:
            ownmaps.plan_add(dev, st, [], my_map, name)
        e = error.value
        assert e.kind == kind and "--name" not in str(e)
        assert e.text(window=False) == f"{e} {cli_hint}".strip()
        assert e.text(window=True) == f"{e} {WINDOW_HINT}" and "--" not in e.text(window=True)


def own_maps_window(key="maps"):
    """The Maps page of the real kind in a window that is never shown, with the window's own
    code for own maps; what it would show the user is recorded instead."""
    from test_fixes_ui import page_window

    from openexpress.gui.app import MainWindow

    win, section = page_window(key)
    win.shown = []  # ("error" | "toast" | "ask" | "task", …)
    win.error = lambda title, text: win.shown.append(("error", title, text))
    win.toast = lambda text: win.shown.append(("toast", text))
    win.fraction = lambda done, total: None
    win.maps_cache = []
    win.answer = "ok"  # what the user picks in a confirmation

    def ask(heading, body, label, on_ok, destructive=False):
        win.shown.append(("ask", heading, body, label, destructive))
        if win.answer == "ok":
            on_ok()

    def run_task(title, work, done=None, failed=None, device=True):
        win.shown.append(("task", title))
        try:
            result = work()
        except Exception as e:
            if not (failed and failed(e)):
                raise
            return
        if done:
            done(result)

    win._ask, win.run_task = ask, run_task
    win.reloads = []
    win.load_section = lambda key, force=False: win.reloads.append((key, force))
    for name in ("show_own_maps", "add_own_map", "confirm_remove_own", "_own_map_refused",
                 "choose_map"):
        setattr(win, name, types.MethodType(getattr(MainWindow, name), win))
    win._own_map_subtitle, win._add_map_text = MainWindow._own_map_subtitle, MainWindow._add_map_text
    return win, section


def rows_of(group):
    """(title, subtitle, has a button) of the rows of a preferences group, as it was built."""
    from openexpress.gui.app import Adw, Gtk

    found = []

    def walk(widget):
        child = widget.get_first_child()
        while child is not None:
            if isinstance(child, Adw.ActionRow):
                buttons = []
                collect(child, Gtk.Button, buttons)
                found.append((child.get_title(), child.get_subtitle(), bool(buttons)))
            else:
                walk(child)
            child = child.get_next_sibling()

    def collect(widget, kind, into):
        child = widget.get_first_child()
        while child is not None:
            if isinstance(child, kind):
                into.append(child)
            collect(child, kind, into)
            child = child.get_next_sibling()

    walk(group)
    return found


def view_of(*own, removable=(), folder="Garmin", unreadable=False):
    maps = [ownmaps.OwnMap(name, size, title, f"Garmin/{name}") for name, size, title in own]
    return ownmaps.Overview(folder, maps, set(removable), unreadable)


def test_own_maps_group_shows_names_sizes_and_who_copied_them():
    win, section = own_maps_window()
    win.session = types.SimpleNamespace()
    win.show_own_maps(view_of(("ours.img", 51_001_344, "Sometown <cyclemap>"),
                              ("by-hand.img", 2000, None), removable=["ours.img"]))
    (group,) = section.groups
    assert group.get_title() == "Own maps"
    assert group.get_description().startswith("Maps that are not from Garmin")
    assert rows_of(group) == [
        ("Sometown &lt;cyclemap&gt;", "ours.img · 51.0 MB", True),      # ours: a remove button
        ("by-hand.img", f"by-hand.img · 2.0 kB · {NOT_OURS_NOTE}", False),  # not ours: none
    ]
    labels = sorted(b.get_label() or b.get_tooltip_text() for b in win._action_buttons)
    assert labels == ["Add Map…", "Remove ours.img"]


@pytest.mark.parametrize("view, rows", [
    (view_of(), [("No maps of your own on the device", "", False)]),
    (view_of(unreadable=True), [("The map folder could not be read", "", False)]),
])
def test_own_maps_group_without_maps_still_offers_to_add_one(view, rows):
    win, section = own_maps_window()
    win.session = types.SimpleNamespace()
    win.show_own_maps(view)
    (group,) = section.groups
    assert rows_of(group) == rows
    assert [b.get_label() for b in win._action_buttons] == ["Add Map…"]


def test_no_own_maps_group_for_a_device_without_a_map_folder():
    win, section = own_maps_window()
    win.session = types.SimpleNamespace()
    win.show_own_maps(view_of(folder=None))
    assert section.groups == [] and win._action_buttons == []


def test_reloading_the_page_keeps_one_add_button_and_greys_it_while_busy():
    win, section = own_maps_window()
    view = view_of(("ours.img", 2000, "Mine"), removable=["ours.img"])
    for _n in range(3):
        win._load("maps", "Checking maps…", lambda: view, win.show_own_maps)
        assert sorted(b.get_label() or "trash" for b in win._action_buttons) == ["Add Map…", "trash"]
        assert all(b.get_root() is win.toplevel and b.get_sensitive() for b in win._action_buttons)
    win.busy = True  # another task runs while the page is drawn again
    section.clear()
    win.show_own_maps(view)
    assert not any(b.get_sensitive() for b in win._action_buttons if b.get_root() is not None)


def test_own_map_rows_do_not_keep_themselves_alive():
    """The rule since 0.4.2: a handler of a widget must not hold the widget or its siblings."""
    import gc
    import weakref

    from openexpress.gui.app import still_in_window

    win, section = own_maps_window()
    win.session = types.SimpleNamespace()
    gc.collect()
    gc.disable()
    try:
        win.show_own_maps(view_of(("ours.img", 2000, "Mine"), removable=["ours.img"]))
        (group,) = section.groups
        alive = [weakref.ref(group)] + [weakref.ref(b) for b in win._action_buttons]
        assert len(alive) == 3
        section.clear()
        win._action_buttons = still_in_window(win._action_buttons)
        del group
        # Freed by reference counting alone: nothing waits for a later collection.
        assert [ref() for ref in alive] == [None, None, None]
    finally:
        gc.enable()


@pytest.fixture
def window_device(map_folder, xdg):
    """The window's session on the made-up map folder."""
    win, section = own_maps_window()
    dev = numbered(device(), 4000000006)
    win.session = types.SimpleNamespace(device=dev, storage=LocalStorage(map_folder))
    win.maps_cache = [garmin_map("Garmin/Listed.img", remove=["Garmin/Old.img"])]
    return win, dev, map_folder


def test_window_adds_a_map_after_a_confirmation(window_device, my_map, monkeypatch):
    win, dev, root = window_device
    monkeypatch.setattr(LocalStorage, "free_space", lambda self: 12_400_000_000)
    target = my_map.with_name("new-map.img")
    my_map.rename(target)
    win.add_own_map(target)
    assert win.shown == [
        ("task", "Checking maps…"),
        ("ask", "Copy this map to the device?",
         ("Sometown cyclemap/latin1 Example.org\n"
          "new-map.img (2.0 kB) will be written to Garmin/new-map.img\n"
          "Free space on the device: 12.4 GB now, 12.4 GB after"), "Copy", False),
        ("task", "Copying the map…"),
        ("toast", "Wrote Garmin/new-map.img (2.0 kB)."),
    ]
    assert win.reloads == [("maps", True)]
    assert (root / "Garmin" / "new-map.img").read_bytes() == target.read_bytes()
    assert [m["filename"] for m in ownmaps.recorded(dev)] == ["new-map.img"]


def test_window_copies_nothing_when_the_user_cancels(window_device, my_map):
    win, dev, root = window_device
    win.answer = "cancel"
    before = tree(root / "Garmin")
    win.add_own_map(my_map.rename(my_map.with_name("new-map.img")))
    assert [kind for kind, *_rest in win.shown] == ["task", "ask"]
    assert tree(root / "Garmin") == before and ownmaps.recorded(dev) == [] and win.reloads == []


@pytest.mark.parametrize("name, message", [
    ("osm-test.img", "A file named osm-test.img is already on the device; nothing is overwritten. "
                     + WINDOW_HINT),
    ("Listed.img", "The name Listed.img is reserved for Garmin's own maps. " + WINDOW_HINT),
    ("gmaptz.img", "The name gmaptz.img is reserved for Garmin's own maps. " + WINDOW_HINT),
    ("moja mapa ż.img", "The name moja mapa ż.img cannot be used: only letters, digits, "
                             "spaces and . _ - ( ) are allowed, and it must end with .img. "
                             + WINDOW_HINT),
])
def test_window_shows_a_refusal_with_its_own_hint(window_device, my_map, name, message):
    win, dev, root = window_device
    before = tree(root / "Garmin")
    win.add_own_map(my_map.rename(my_map.with_name(name)))
    assert win.shown == [("task", "Checking maps…"), ("error", "Own maps", message)]
    assert "--" not in message
    assert tree(root / "Garmin") == before and ownmaps.recorded(dev) == [] and win.reloads == []


def test_window_says_when_the_file_is_not_a_map(window_device, tmp_path):
    win, _dev, _root = window_device
    photo = tmp_path / "photo.img"
    photo.write_bytes(b"\xff\xd8\xff" + b"\0" * 2000)
    win.add_own_map(photo)
    assert win.shown[-1] == ("error", "Own maps", "photo.img is not a Garmin map file (.img).")


def test_window_removes_a_map_it_copied(window_device, my_map, monkeypatch):
    win, dev, root = window_device
    st = win.session.storage
    ownmaps.add(ownmaps.plan_add(dev, st, win.maps_cache, my_map, "ours.img"), dev, st)
    (ours,) = [m for m in ownmaps.overview(dev, st, win.maps_cache).maps if m.filename == "ours.img"]
    win.confirm_remove_own(ours)
    assert win.shown == [
        ("ask", "Remove Sometown cyclemap/latin1 Example.org?",
         "Garmin/ours.img (2.0 kB) will be removed from the device.", "Remove", True),
        ("task", "Removing Sometown cyclemap/latin1 Example.org…"),
        ("toast", "Removed Garmin/ours.img (2.0 kB)."),
    ]
    assert not (root / "Garmin" / "ours.img").exists() and ownmaps.recorded(dev) == []
    assert win.reloads == [("maps", True)]


def test_window_removal_can_be_cancelled_and_refused(window_device, my_map):
    win, dev, root = window_device
    st = win.session.storage
    ownmaps.add(ownmaps.plan_add(dev, st, win.maps_cache, my_map, "ours.img"), dev, st)
    ours = ownmaps.OwnMap("ours.img", 2000, "Mine", "Garmin/ours.img")
    win.answer = "cancel"
    win.confirm_remove_own(ours)
    assert (root / "Garmin" / "ours.img").exists() and len(ownmaps.recorded(dev)) == 1

    # The file was replaced meanwhile: refused, shown, nothing removed.
    win.answer, win.shown = "ok", []
    (root / "Garmin" / "ours.img").write_bytes(b"another file")
    win.confirm_remove_own(ours)
    kind, title, text = win.shown[-1]
    assert (kind, title) == ("error", "Own maps") and "not the same file" in text
    assert (root / "Garmin" / "ours.img").exists() and len(ownmaps.recorded(dev)) == 1
    assert win.reloads == []


def test_window_takes_a_map_that_is_gone_off_the_list(window_device, my_map):
    win, dev, root = window_device
    st = win.session.storage
    ownmaps.add(ownmaps.plan_add(dev, st, win.maps_cache, my_map, "ours.img"), dev, st)
    (root / "Garmin" / "ours.img").unlink()  # deleted in the file manager meanwhile
    win.confirm_remove_own(ownmaps.OwnMap("ours.img", 2000, "Mine", "Garmin/ours.img"))
    assert win.shown[-1] == (
        "toast", "Took ours.img off the list of own maps; the file was not on the device.")
    assert ownmaps.recorded(dev) == [] and win.reloads == [("maps", True)]


def test_maps_page_loads_garmins_maps_and_own_maps_in_one_task(map_folder, xdg):
    from openexpress.gui.app import MainWindow

    win, section = own_maps_window()
    asked, drawn = [], []
    win.session = types.SimpleNamespace(
        client=types.SimpleNamespace(universal_maps=lambda raw: asked.append(raw) or {"maps": []}),
        device=numbered(device(), 4000000007), storage=LocalStorage(map_folder))
    win.session.device.raw = b"xml"
    win.show_maps = lambda result: drawn.append(("maps", result))
    win.load_maps = types.MethodType(MainWindow.load_maps, win)
    real = win.show_own_maps
    win.show_own_maps = lambda view: (drawn.append(("own", [m.filename for m in view.maps])),
                                      real(view))
    win.load_maps()
    assert asked == [b"xml"]  # one request to Garmin's server
    # The server lists no map here, so every file not named like Garmin's counts as own.
    assert drawn == [("maps", ([], 0)),
                     ("own", ["broken.img", "Listed.img", "Old.img", "osm-test.img"])]
    assert win.shown == [("task", "Checking maps…")]  # one task for both, nothing asked
    (group,) = section.groups
    assert group.get_title() == "Own maps" and len(rows_of(group)) == 4


def test_own_maps_in_the_window_in_polish(window_device, my_map):
    from openexpress import i18n

    win, _dev, _root = window_device
    i18n.setup("pl")
    try:
        win.show_own_maps(view_of(("by-hand.img", 2000, None)))
        win.add_own_map(my_map)  # osm-test.img is on the made-up device already
    finally:
        i18n.setup("en")
    (group,) = win.sections["maps"].groups
    assert group.get_title() == "Własne mapy"
    note = "nieskopiowana przez tę instalację; usuń ją w menedżerze plików"
    assert rows_of(group) == [("by-hand.img", f"by-hand.img · 2,0 kB · {note}", False)]
    refusal = ("Plik o nazwie osm-test.img jest już na urządzeniu; nic nie zostanie nadpisane. "
               "Zmień nazwę pliku na komputerze i dodaj go ponownie.")
    assert win.shown[-1] == ("error", "Własne mapy", refusal)
