import base64
import hashlib
import http.server
import json
import threading

import pytest
import requests

from openexpress import applog, download, maps, updates
from openexpress.connect import ApiError, lower_keys
from openexpress.device.storage import DeviceError, LocalStorage

FILES = {
    "/fw/GUPDATE.GSP": b"F" * 5000,
    "/fw/aux.gcd": b"A" * 300,
    "/maps/topo.img": b"T" * 7000,
    "/maps/dem.img": b"D" * 2000,
}


@pytest.fixture
def server():
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            data = FILES.get(self.path)
            if data is None:
                self.send_response(404)
                self.end_headers()
                return
            start = 0
            rng = self.headers.get("Range")
            if rng:
                start = int(rng.split("=")[1].split("-")[0])
                self.send_response(206)
            else:
                self.send_response(200)
            self.send_header("Content-Length", str(len(data) - start))
            self.end_headers()
            self.wfile.write(data[start:])

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def md5hex(b):
    return hashlib.md5(b).hexdigest()


def test_normalize_md5():
    h = md5hex(b"x")
    assert download.normalize_md5(h.upper()) == h
    assert download.normalize_md5(base64.b64encode(bytes.fromhex(h)).decode()) == h
    assert download.normalize_md5("garbage") is None


def test_tokenize():
    url = "https://maps.example/rmu/x/D1.img"
    t = download.tokenize(url, "SALT", now=1000)
    assert t.startswith(url + "?garmindlm=87400_")
    exp = 87400
    inner = hashlib.md5(exp.to_bytes(4, "little") + b"/rmu/x/D1.imgSALT").digest()
    assert t.endswith(hashlib.md5(b"SALT" + inner).hexdigest())
    # Re-signing replaces the parameter; other parameters are kept.
    t2 = download.tokenize(t + "&a=1", "SALT", now=2000)
    assert t2.count("garmindlm=") == 1 and "a=1" in t2


def test_extract_url_salt(tmp_path):
    from openexpress.settings import extract_url_salt_from_dll

    def u(s):
        return s.encode("utf-16-le") + b"\x00\x00"

    dll = tmp_path / "Utilities.dll"
    dll.write_bytes(b"\x01" + u("File is invalid") + u("AbC123xyz") + u("{0}_{1}") + u("garmindlm"))
    assert extract_url_salt_from_dll(dll) == "AbC123xyz"


def test_fetch_resume_and_verify(server, tmp_path):
    dest = tmp_path / "f.bin"
    part = tmp_path / "f.bin.part"
    part.write_bytes(FILES["/fw/GUPDATE.GSP"][:1000])  # previously interrupted download
    download.fetch([server + "/fw/GUPDATE.GSP"], dest, 5000, md5hex(FILES["/fw/GUPDATE.GSP"]))
    assert dest.read_bytes() == FILES["/fw/GUPDATE.GSP"]
    with pytest.raises(download.DownloadError):
        download.fetch([server + "/fw/aux.gcd"], tmp_path / "x", 300, md5hex(b"other"))
    # the first URL fails, the second works
    download.fetch([server + "/missing", server + "/fw/aux.gcd"], tmp_path / "y", 300, None)


def sw_response(base):
    def upd(pn, name, path, fname, data, primary=False, reinstall=False, restart=False):
        return {
            "partnumber": pn, "productname": name, "displayversion": "23.40",
            "severity": "Recommended", "isprimaryfirmware": primary,
            "releasenotes": [{"changelog": ["Fixes"]}],
            "eulaurl": "https://example/eula",
            "deliverable": {"securedownloadurl": base + "/fw/" + fname, "md5": md5hex(data),
                            "sizeinbytes": len(data)},
            "installation": {"isreinstall": reinstall, "path": path, "filename": fname,
                             "isrestartrequired": restart, "order": 0},
        }

    return {"updates": [
        upd("006-V0000-00", "System", "Garmin", "GUPDATE.GSP", FILES["/fw/GUPDATE.GSP"],
            restart=True),
        upd("006-D0000-00", "Aux", "Garmin\\RemoteSW", "aux.gcd", FILES["/fw/aux.gcd"]),
    ]}


def test_software_update_flow(server, device_root, xdg):
    items = updates.parse_updates(sw_response(server))
    assert items[1].device_path == "Garmin/RemoteSW/aux.gcd"
    assert [u.part_number for u in updates.sort_for_install(items)] == [
        "006-D0000-00", "006-V0000-00"]
    with pytest.raises(updates.UpdateError):
        updates.select(items, ["006-X"])
    st = LocalStorage(device_root)
    rep = updates.install(updates.select(items, ["all"]), st)
    assert rep.restart_required
    assert (device_root / "GARMIN/GUPDATE.GSP").read_bytes() == FILES["/fw/GUPDATE.GSP"]
    assert (device_root / "GARMIN/RemoteSW/aux.gcd").exists()


def maps_response(base):
    def m(name, state, reinstall, files, remove, can=True):
        return {
            "displayname": name, "maptype": "TopoActive", "installationstate": state,
            "isreinstall": reinstall, "canuninstall": can,
            "release": {"majorversion": 2026, "minorversion": 11},
            "downloadhosts": {"foregroundprimaryhost": base + "/maps/",
                              "failoverhosts": []},
            "filestoremove": [{"filename": f} for f in remove],
            "installoptions": [{"ispreferred": True,
                                "identifier": {"regionpartnumber": name},
                                "files": [{"filename": fn, "datatype": "X",
                                           "deliverableoptions": [{
                                               "type": 1, "url": url,
                                               "md5": base64.b64encode(
                                                   hashlib.md5(FILES["/maps/" + url]).digest()
                                               ).decode(),
                                               "sizeinbytes": len(FILES["/maps/" + url])}]}
                                          for fn, url in files]}],
        }

    return {"maps": [
        m("TopoActive West", 1, True, [("Garmin/W.img", "topo.img"), ("Garmin/DEM.img", "dem.img")],
          ["Garmin/W.img", "Garmin/DEM.img", "Garmin/W.gma"]),
        m("TopoActive East", 1, False, [("Garmin/E2.img", "topo.img"), ("Garmin/DEM.img", "dem.img")],
          ["Garmin/E.img", "Garmin/DEM.img", "Garmin/E.gma"]),
        m("TopoActive Japan", 0, False, [("Garmin/J.img", "topo.img")], []),
    ]}


def test_maps_install_update_and_shared_files(server, device_root, xdg):
    g = device_root / "GARMIN"
    for n in ("W.img", "DEM.img", "W.gma", "E.img", "E.gma"):
        (g / n).write_bytes(b"old")
    all_maps = maps.parse_maps(maps_response(server))
    west, east, japan = all_maps
    assert west.status == "up to date" and east.status == "update available"
    assert japan.status == "available"
    assert maps.find(all_maps, "japan") is japan
    st = LocalStorage(device_root)

    # The shared DEM.img is protected when removing the West map.
    assert maps.removal_list(all_maps, west, rewriting=False) == ["Garmin/W.img", "Garmin/W.gma"]

    activations = []

    def activate(ident):
        activations.append(ident)
        return {"unlocks": [{"filename": "Garmin/E2.gma",
                             "gma": base64.b64encode(b"UNLOCK").decode()}]}

    rep = maps.install(east, all_maps, st, activate)
    assert activations == [{"regionpartnumber": "TopoActive East"}]
    assert (g / "E2.img").read_bytes() == FILES["/maps/topo.img"]
    assert (g / "E2.gma").read_bytes() == b"UNLOCK"
    assert not (g / "E.img").exists() and not (g / "E.gma").exists()
    assert (g / "W.img").exists()  # other maps untouched
    assert "Garmin/DEM.img" not in rep.removed

    removed = maps.uninstall(west, all_maps, st)
    assert sorted(removed) == ["Garmin/W.gma", "Garmin/W.img"]
    assert (g / "DEM.img").exists()


def unlock_e2(ident):
    return {"unlocks": [{"filename": "Garmin/E2.gma", "gma": base64.b64encode(b"UNLOCK").decode()}]}


class RecordingStorage(LocalStorage):
    """Remembers what was written, in order; can fail writing chosen files."""

    def __init__(self, root, fail=None):
        super().__init__(root)
        self.order, self.fail = [], dict(fail or {})

    def _write_file(self, real_path, local, progress=None):
        self.order.append(real_path)
        if self.fail.get(real_path, 0) > 0:
            self.fail[real_path] -= 1
            self._p(real_path).write_bytes(b"half")
            raise DeviceError("unplugged")
        super()._write_file(real_path, local, progress)

    def _write(self, real_path, data):
        self.order.append(real_path)
        super()._write(real_path, data)


def east_with_shared_dem(server, device_root):
    g = device_root / "GARMIN"
    for n in ("W.img", "DEM.img", "W.gma", "E.img", "E.gma"):
        (g / n).write_bytes(b"old")
    all_maps = maps.parse_maps(maps_response(server))
    return all_maps, all_maps[1]


def test_map_files_are_written_smallest_first_and_the_unlock_last(server, device_root, xdg):
    all_maps, east = east_with_shared_dem(server, device_root)
    assert [f.device_path for f in east.files] == ["Garmin/E2.img", "Garmin/DEM.img"]
    st = RecordingStorage(device_root)
    lines = []
    maps.install(east, all_maps, st, unlock_e2, progress=lines.append)
    assert st.order == ["GARMIN/DEM.img", "GARMIN/E2.img", "GARMIN/E2.gma"]
    downloads = [line for line in lines if line.startswith("Downloading ")]
    assert [line.split()[1] for line in downloads] == ["Garmin/DEM.img", "Garmin/E2.img"]


def test_a_shared_file_that_failed_is_written_once_more(server, device_root, xdg):
    all_maps, east = east_with_shared_dem(server, device_root)
    st = RecordingStorage(device_root, fail={"GARMIN/DEM.img": 1})
    rep = maps.install(east, all_maps, st, unlock_e2)
    assert st.order.count("GARMIN/DEM.img") == 2
    assert (device_root / "GARMIN/DEM.img").read_bytes() == FILES["/maps/dem.img"]
    assert "GARMIN/DEM.img" in rep.written


def test_a_shared_file_that_fails_twice_is_kept_and_named(server, device_root, xdg):
    all_maps, east = east_with_shared_dem(server, device_root)
    st = RecordingStorage(device_root, fail={"GARMIN/DEM.img": 2})
    with pytest.raises(maps.MapError) as error:
        maps.install(east, all_maps, st, unlock_e2)
    assert st.order.count("GARMIN/DEM.img") == 2
    assert "Garmin/DEM.img" in str(error.value) and "TopoActive West" in str(error.value)
    # Not deleted: the West map still lists it; installing again writes it whole.
    assert (device_root / "GARMIN/DEM.img").exists()
    assert not (device_root / "GARMIN/E2.gma").exists()


def test_a_file_of_this_map_alone_is_not_tried_again(server, device_root, xdg):
    all_maps, east = east_with_shared_dem(server, device_root)
    st = RecordingStorage(device_root, fail={"GARMIN/E2.img": 1})
    with pytest.raises(DeviceError):
        maps.install(east, all_maps, st, unlock_e2)
    assert st.order.count("GARMIN/E2.img") == 1
    assert not (device_root / "GARMIN/E2.img").exists()


def test_the_log_names_the_start_of_the_unlock_md5(server, device_root, xdg, caplog):
    all_maps, east = east_with_shared_dem(server, device_root)
    with caplog.at_level("INFO", logger="openexpress.maps"):
        maps.install(east, all_maps, LocalStorage(device_root), unlock_e2)
    h = hashlib.md5(b"UNLOCK").hexdigest()[:8]
    line = next(r.getMessage() for r in caplog.records if r.getMessage().startswith("map install:"))
    assert line.endswith(f"1 unlock files (md5 {h[:4]}_{h[4:]})")
    assert applog.redact(line) == line  # the log filter leaves it readable


# --- a device without maps ----------------------------------------------------------------

# The shape of a real answer for a device without maps: every list is there and empty.
NO_MAPS = {"maps": [], "filesToRemove": [], "details": {}, "bundledMaps": [],
           "directoriesToRemove": [], "subregions": []}
NO_MAPS_TEXT = "No maps are available for this device."


def answer_with(monkeypatch, status=200, body=b"", error=None):
    """Garmin's server gives this one answer to every request; returns the requests made."""
    asked = []

    def request(self, method, url, **kw):
        asked.append((method, url))
        if error is not None:
            raise error
        resp = requests.Response()
        resp.status_code, resp._content = status, body
        return resp

    monkeypatch.setattr(requests.Session, "request", request)
    return asked


def device_files(root):
    return sorted((str(p.relative_to(root)), p.stat().st_size) for p in root.rglob("*")
                  if p.is_file())


def test_an_empty_list_of_maps_is_an_answer():
    assert maps.read_maps(lower_keys(NO_MAPS)) == ([], 0)


@pytest.mark.parametrize("answer", [{}, {"maps": None}, {"maps": "none"}, {"maps": {}}, [],
                                    None, {"error": "try later"}])
def test_an_answer_without_a_list_of_maps_is_an_error(answer):
    with pytest.raises(ApiError) as error:
        maps.read_maps(answer)
    with pytest.raises(ApiError):
        maps.parse_maps(answer)
    assert str(error.value) == "Map check: unexpected response from the server"


def test_maps_that_cannot_be_read_are_not_no_maps(device_root, xdg, monkeypatch, capsys):
    from openexpress.cli import main

    # The server lists a map, but without anything to install: the list stays as it was.
    body = json.dumps({"maps": [{"displayName": "TopoActive Nowhere", "installOptions": []}]})
    answer_with(monkeypatch, body=body.encode())
    assert main(["--path", str(device_root), "maps"]) == 0
    out = capsys.readouterr().out
    assert NO_MAPS_TEXT not in out and "(installed):" in out
    assert maps.read_maps(json.loads(body.lower())) == ([], 1)  # listed, but not read


def test_maps_page_with_maps_that_cannot_be_read(device_root):
    from openexpress.gui.app import MainWindow

    win, _reloads = _maps_window(LocalStorage(device_root))
    MainWindow.show_maps(win, maps.read_maps({"maps": [{"displayname": "X"}]}))
    assert [g.title for g in win.sections["maps"].groups] == ["On the device"]


def test_maps_command_on_a_device_without_maps(device_root, xdg, monkeypatch, capsys):
    from openexpress.cli import main

    asked = answer_with(monkeypatch, body=json.dumps(NO_MAPS).encode())
    before = device_files(device_root)
    for argv in (["maps"], ["maps", "--all"], ["maps", "update"], ["maps", "update", "--apply"]):
        assert main(["--path", str(device_root), *argv]) == 0, argv
        captured = capsys.readouterr()
        # One message: no empty heading, no "0 more maps", no "up to date".
        assert captured.out.strip() == NO_MAPS_TEXT, argv
    for argv in (["install", "Japan"], ["install", "Japan", "--apply"],
                 ["remove", "Japan"], ["remove", "Japan", "--apply"]):
        assert main(["--path", str(device_root), "maps", *argv]) == 1, argv
        captured = capsys.readouterr()
        assert captured.err.strip() == "Error: " + NO_MAPS_TEXT and captured.out == ""
    # Only the map check was asked for, and nothing was written to the device.
    assert len(asked) == 8 and all(url.endswith("api/maps/universal/update")
                                   for _method, url in asked)
    assert device_files(device_root) == before


def test_maps_command_with_maps_keeps_the_list(server, device_root, xdg, monkeypatch, capsys):
    from openexpress.cli import main

    answer_with(monkeypatch, body=json.dumps(maps_response(server)).encode())
    assert main(["--path", str(device_root), "maps"]) == 0
    out = capsys.readouterr().out
    assert "(installed):" in out and "TopoActive West" in out and "TopoActive Japan" not in out
    assert "1 more map is available (openexpress maps --all)." in out
    assert "Free space on the device:" in out and NO_MAPS_TEXT not in out
    assert main(["--path", str(device_root), "maps", "--all"]) == 0
    out = capsys.readouterr().out
    assert "(all):" in out and "TopoActive Japan" in out and NO_MAPS_TEXT not in out


@pytest.mark.parametrize("status,body,error", [
    (500, b"oops", None),                   # the server fails
    (503, json.dumps(NO_MAPS).encode(), None),  # … even with a well-formed body
    (200, b"", None),                       # an empty answer
    (200, b"{}", None),                     # an object without a list of maps
    (200, b'{"maps": null}', None),
    (200, b"[]", None),
    (200, b"<html>", None),                 # not JSON
    (200, b"", requests.ConnectionError("no route")),  # no network
])
def test_maps_command_does_not_say_no_maps_when_the_check_fails(
        status, body, error, device_root, xdg, monkeypatch, capsys):
    from openexpress.cli import main

    answer_with(monkeypatch, status, body, error)
    for argv in (["maps"], ["maps", "update"], ["maps", "install", "Japan", "--apply"]):
        assert main(["--path", str(device_root), *argv]) == 1, argv
        captured = capsys.readouterr()
        assert NO_MAPS_TEXT not in captured.out + captured.err
        assert captured.out == "" and "up to date" not in captured.err
        assert "Server error: Map check" in captured.err and "Traceback" not in captured.err


def _maps_window(storage):
    import types

    from openexpress.gui.app import MainWindow

    class Section:
        def __init__(self):
            self.groups = []

        def group(self, title="", description=""):
            group = types.SimpleNamespace(title=title, rows=[], header=None)
            group.add = group.rows.append
            group.set_header_suffix = lambda widget: setattr(group, "header", widget)
            self.groups.append(group)
            return group

    reloads = []
    win = types.SimpleNamespace(
        session=types.SimpleNamespace(storage=storage), sections={"maps": Section()},
        busy=False, _action_buttons=[], confirm_map=lambda m: None,
        confirm_remove=lambda m: None, _search_filter=lambda search, rows: None,
        load_section=lambda key, force=False: reloads.append((key, force)),
        _set_map_state=lambda result: None)
    win._button = types.MethodType(MainWindow._button, win)
    win._trash = types.MethodType(MainWindow._trash, win)
    win._map_subtitle = MainWindow._map_subtitle
    return win, reloads


def _children(widget):
    child = widget.get_first_child()
    while child is not None:
        yield child
        child = child.get_next_sibling()


def test_maps_page_without_maps(device_root):
    from openexpress.gui.app import Gtk, MainWindow

    win, reloads = _maps_window(LocalStorage(device_root))
    MainWindow.show_maps(win, maps.read_maps(lower_keys(NO_MAPS)))
    assert win.maps_cache == []
    (group,) = win.sections["maps"].groups  # no "On the device", no storage bar
    assert group.title == ""
    (box,) = group.rows
    labels = [c.get_label() for c in _children(box) if isinstance(c, Gtk.Label)]
    assert labels == [NO_MAPS_TEXT]
    (button,) = [c for c in _children(box) if isinstance(c, Gtk.Button)]
    assert button.get_label() == "Check Again"
    button.emit("clicked")
    assert reloads == [("maps", True)]


def test_maps_page_with_maps_keeps_the_groups(server, device_root):
    from openexpress.gui.app import MainWindow

    win, _reloads = _maps_window(LocalStorage(device_root))
    MainWindow.show_maps(win, maps.read_maps(maps_response(server)))
    on_device, available = win.sections["maps"].groups
    assert on_device.title == "On the device" and available.title == "Available"
    titles = [row.get_title() for row in on_device.rows]
    assert titles[-2:] == ["TopoActive East", "TopoActive West"]


@pytest.mark.parametrize("name,map_type,subtitle", [
    ("Base maps", "Base maps", "2026.11 · 9.0 kB · up to date"),
    ("Base Maps", " base maps ", "2026.11 · 9.0 kB · up to date"),  # case and outer spaces
    ("TopoActive Western Europe", "TopoActive", "TopoActive · 2026.11 · 9.0 kB · up to date"),
    ("Something", "", "2026.11 · 9.0 kB · up to date"),  # no type from the server
])
def test_maps_page_does_not_repeat_the_name_as_the_type(name, map_type, subtitle, server,
                                                        device_root):
    from openexpress.gui.app import MainWindow

    answer = maps_response(server)
    answer["maps"] = answer["maps"][:1]  # the installed, up-to-date map
    answer["maps"][0].update(displayname=name, maptype=map_type)
    win, _reloads = _maps_window(LocalStorage(device_root))
    MainWindow.show_maps(win, maps.read_maps(answer))
    (on_device,) = win.sections["maps"].groups
    row = on_device.rows[-1]
    assert (row.get_title(), row.get_subtitle()) == (name, subtitle)


# --- a map that cannot be removed -----------------------------------------------------------


def west_files(device_root):
    g = device_root / "Garmin"
    g.mkdir()
    for n in ("W.img", "DEM.img", "W.gma"):
        (g / n).write_bytes(b"old")


@pytest.mark.parametrize("apply", [[], ["--apply"]])
def test_maps_remove_refuses_at_once_a_map_that_cannot_be_removed(
        apply, server, device_root, xdg, monkeypatch, capsys):
    from openexpress.cli import main

    answer = maps_response(server)
    answer["maps"][0]["canuninstall"] = False  # TopoActive West
    answer_with(monkeypatch, body=json.dumps(answer).encode())
    west_files(device_root)
    before = device_files(device_root)
    assert main(["--path", str(device_root), "maps", "remove", "West", *apply]) == 1
    captured = capsys.readouterr()
    # An error, and nothing before it: no "Removing map …", no list of files.
    assert captured.out == ""
    assert captured.err.strip() == "Error: Map 'TopoActive West' cannot be removed"
    assert device_files(device_root) == before


def test_maps_remove_still_lists_and_removes_a_map_that_can_be_removed(
        server, device_root, xdg, monkeypatch, capsys):
    from openexpress.cli import main

    answer_with(monkeypatch, body=json.dumps(maps_response(server)).encode())
    west_files(device_root)
    before = device_files(device_root)
    assert main(["--path", str(device_root), "maps", "remove", "West"]) == 0
    out = capsys.readouterr().out
    assert "Removing map TopoActive West: 2 files" in out and "  - Garmin/W.img" in out
    assert "Dry run. Add --apply." in out and device_files(device_root) == before
    assert main(["--path", str(device_root), "maps", "remove", "West", "--apply"]) == 0
    assert "Removed 2 files." in capsys.readouterr().out
    # The file shared with another map stays.
    assert [name for name, _size in device_files(device_root) if name.startswith("Garmin/")] == [
        "Garmin/DEM.img"]


# --- the "Maps" row of the device page ------------------------------------------------------


def map_answer(server, installed_updates=0, installed_current=0, available=0):
    """An answer of the map check with this many maps of each kind."""
    one = maps_response(server)["maps"][0]
    out = []
    for kind, count in (("update", installed_updates), ("current", installed_current),
                        ("new", available)):
        for n in range(count):
            m = json.loads(json.dumps(one))
            m.update(displayname=f"{kind} {n}", installationstate=0 if kind == "new" else 1,
                     isreinstall=kind == "current")
            out.append(m)
    return {"maps": out}


class Row:
    subtitle = None

    def set_subtitle(self, text):
        self.subtitle = text


def _overview_window(answers, device_root=None):
    """A window with the "Maps" row; the server gives `answers` in turn (an exception is
    raised). Background work runs at once unless `later` collects it."""
    import types

    from openexpress.gui.app import MainWindow

    asked, later = [], []

    def universal_maps(raw):
        asked.append(raw)
        answer = answers[len(asked) - 1]
        if isinstance(answer, Exception):
            raise answer
        return answer

    def in_background(work, done, failed):
        def run():
            try:
                result = work()
            except Exception as e:  # noqa: BLE001 - like the window: any failure of the check
                failed(e)
            else:
                done(result)
        (later.append(run) if win.hold else run())

    session = types.SimpleNamespace(
        client=types.SimpleNamespace(universal_maps=universal_maps),
        device=types.SimpleNamespace(raw=b"xml"),
        storage=LocalStorage(device_root) if device_root else None)
    win = types.SimpleNamespace(session=session, map_state=None, map_update_count=0,
                                _map_check_session=None, map_row=Row(), hold=False,
                                in_background=in_background)
    for name in ("_render_map_state", "_set_map_state", "_check_maps_quietly"):
        setattr(win, name, types.MethodType(getattr(MainWindow, name), win))
    return win, asked, later


@pytest.mark.parametrize("counts,state,text", [
    ((1, 2, 3), "available", "1 update available"),
    ((2, 0, 0), "available", "2 updates available"),
    ((5, 1, 0), "available", "5 updates available"),
    ((0, 2, 3), "current", "All installed maps are up to date."),
    ((0, 0, 3), "empty", "No maps are installed."),
])
def test_maps_row_says_what_the_check_found(counts, state, text, server):
    win, asked, _later = _overview_window([map_answer(server, *counts)])
    assert win.map_row.subtitle is None
    win._render_map_state()
    assert win.map_row.subtitle == "Checking…"
    win._check_maps_quietly()
    assert (win.map_state, win.map_row.subtitle) == (state, text)
    assert asked == [b"xml"] and win._map_check_session is None  # one request


def test_maps_row_for_a_device_without_maps():
    win, _asked, _later = _overview_window([lower_keys(NO_MAPS)])
    win._check_maps_quietly()
    assert (win.map_state, win.map_row.subtitle) == ("none", NO_MAPS_TEXT)


@pytest.mark.parametrize("answer", [ApiError("Map check: HTTP 500", 500),
                                    requests.ConnectionError("down"), {}])
def test_maps_row_when_the_check_fails(answer):
    win, _asked, _later = _overview_window([answer])
    win._check_maps_quietly()
    assert (win.map_state, win.map_row.subtitle) == ("failed", "Could not check for updates")
    assert win._map_check_session is None


def test_maps_row_asks_once_while_a_check_runs(server):
    win, asked, later = _overview_window([map_answer(server, 1)])
    win.hold = True
    win._check_maps_quietly()
    win._check_maps_quietly()  # the device page is drawn again meanwhile
    assert len(later) == 1 and asked == []
    later.pop()()
    assert asked == [b"xml"] and win.map_state == "available"


def test_maps_row_drops_the_answer_for_another_device(server):
    import types

    win, _asked, later = _overview_window([map_answer(server, 1)])
    win.hold = True
    win._check_maps_quietly()
    win.session = types.SimpleNamespace()  # the device was replaced meanwhile
    later.pop()()
    assert win.map_state is None and win.map_row.subtitle is None


def test_maps_page_updates_the_row_of_the_device_page(server, device_root):
    from openexpress.gui.app import MainWindow

    # The quiet check found one update; the Maps page asks later and finds none.
    win, asked, _later = _overview_window([map_answer(server, 1, 1)], device_root)
    win._check_maps_quietly()
    assert win.map_row.subtitle == "1 update available"
    page, _reloads = _maps_window(LocalStorage(device_root))
    page._set_map_state = win._set_map_state
    MainWindow.show_maps(page, maps.read_maps(map_answer(server, 0, 2)))
    assert (win.map_state, win.map_row.subtitle) == (
        "current", "All installed maps are up to date.")
    assert asked == [b"xml"]  # the row itself asked only once


def test_maps_page_answer_wins_over_a_quiet_check_that_ends_later(server, device_root):
    win, _asked, later = _overview_window([map_answer(server, 3)], device_root)
    win.hold = True
    win._check_maps_quietly()  # still running …
    win._set_map_state(maps.read_maps(lower_keys(NO_MAPS)))  # … when the Maps page answers
    later.pop()()
    assert (win.map_state, win.map_row.subtitle) == ("none", NO_MAPS_TEXT)


def test_a_failed_quiet_check_does_not_hide_what_the_maps_page_found(server):
    win, _asked, later = _overview_window([ApiError("Map check: HTTP 500", 500)])
    win.hold = True
    win._check_maps_quietly()
    win._set_map_state(maps.read_maps(map_answer(server, 0, 1)))
    later.pop()()
    assert win.map_state == "current"


# --- "Check Again" on a Maps page that has maps -----------------------------------------------


def test_maps_page_with_maps_can_check_again(server, device_root):
    from openexpress.gui.app import MainWindow

    win, reloads = _maps_window(LocalStorage(device_root))
    MainWindow.show_maps(win, maps.read_maps(maps_response(server)))
    on_device, _available = win.sections["maps"].groups
    button = on_device.header
    assert on_device.title == "On the device" and button.get_label() == "Check Again"
    assert not button.has_css_class("suggested-action")  # "Update" of a map stays the one
    assert button in win._action_buttons and button.get_sensitive()
    button.emit("clicked")
    assert reloads == [("maps", True)]


def test_check_again_is_greyed_while_another_task_runs(server, device_root):
    from openexpress.gui.app import MainWindow

    win, _reloads = _maps_window(LocalStorage(device_root))
    win.busy = True
    MainWindow.show_maps(win, maps.read_maps(maps_response(server)))
    assert not win.sections["maps"].groups[0].header.get_sensitive()


def test_maps_page_without_maps_has_one_check_again(device_root):
    from openexpress.gui.app import MainWindow

    win, _reloads = _maps_window(LocalStorage(device_root))
    MainWindow.show_maps(win, maps.read_maps(lower_keys(NO_MAPS)))
    (group,) = win.sections["maps"].groups
    assert group.header is None  # only the button under the message


def _real_maps_page(answers, device_root):
    """The Maps page of the real kind in a window that is never shown, loaded the way
    MainWindow loads it; Garmin's server gives `answers` in turn."""
    import types

    from test_fixes_ui import page_window

    from openexpress.gui.app import MainWindow

    win, section = page_window("maps")
    asked = []

    def universal_maps(raw):
        asked.append(raw)
        return answers[min(len(asked), len(answers)) - 1]

    win.session = types.SimpleNamespace(
        client=types.SimpleNamespace(universal_maps=universal_maps),
        # A device that declares no folder for maps: the page has no group of own maps.
        device=types.SimpleNamespace(raw=b"xml", targets=lambda datatype: []),
        storage=LocalStorage(device_root))
    win.map_state, win.map_update_count, win._map_check_session = None, 0, None
    win.map_row = Row()
    win.confirm_map = win.confirm_remove = lambda m: None
    win.load_updates = win.load_apps = win.load_music = None  # other pages: not loaded here
    for name in ("load_maps", "show_maps", "show_own_maps", "load_section", "_set_map_state",
                 "_render_map_state"):
        setattr(win, name, types.MethodType(getattr(MainWindow, name), win))
    win._map_subtitle, win._search_filter = MainWindow._map_subtitle, MainWindow._search_filter
    return win, section, asked


def check_again_buttons(win):
    return [b for b in win._action_buttons if b.get_label() == "Check Again"]


def test_three_check_agains_leave_one_button_in_the_list(server, device_root):
    win, section, asked = _real_maps_page([maps_response(server)], device_root)
    win.load_section("maps")
    for clicks in range(1, 4):
        (button,) = check_again_buttons(win)  # one, not one per rebuild
        assert button.get_root() is win.toplevel
        button.emit("clicked")
        assert len(asked) == 1 + clicks  # each click asks the server once
    (button,) = check_again_buttons(win)
    assert all(b.get_root() is win.toplevel for b in win._action_buttons)
    assert len(section.groups) == 2


def test_check_again_updates_the_row_of_the_device_page(server, device_root):
    # First one update, then none: the "Maps" row of the device page follows.
    win, _section, asked = _real_maps_page(
        [map_answer(server, 1, 1), map_answer(server, 0, 2)], device_root)
    win.load_section("maps")
    assert win.map_row.subtitle == "1 update available"
    check_again_buttons(win)[0].emit("clicked")
    assert win.map_row.subtitle == "All installed maps are up to date." and len(asked) == 2


# --- progress while an update file is copied ----------------------------------------------------


class ReportingStorage(LocalStorage):
    """Copies like the real ones do: tells the progress function how far it is."""

    seen: list = []

    def _write_file(self, real_path, local, progress=None):
        type(self).seen.append((real_path.split("/")[-1], progress))
        super()._write_file(real_path, local)
        if progress:
            size = local.stat().st_size
            progress(size // 2, size)
            progress(size, size)


def test_update_files_report_their_progress(server, device_root, xdg):
    ReportingStorage.seen = []
    steps = []
    items = updates.select(updates.parse_updates(sw_response(server)), ["all"])
    rep = updates.install(items, ReportingStorage(device_root),
                          transfer_progress=lambda done, total: steps.append((done, total)))
    assert len(rep.installed) == 2
    # Every file of the update gets the function, and the copy calls it.
    assert [name for name, _p in ReportingStorage.seen] == ["aux.gcd", "GUPDATE.GSP"]
    assert all(p is not None for _name, p in ReportingStorage.seen)
    assert steps == [(150, 300), (300, 300), (2500, 5000), (5000, 5000)]
    assert (device_root / "GARMIN/GUPDATE.GSP").read_bytes() == FILES["/fw/GUPDATE.GSP"]


def test_update_install_without_a_progress_function_works_as_before(server, device_root, xdg):
    ReportingStorage.seen = []
    items = updates.select(updates.parse_updates(sw_response(server)), ["all"])
    updates.install(items, ReportingStorage(device_root))
    assert [p for _name, p in ReportingStorage.seen] == [None, None]


# --- maps written or removed, waiting for the device to take them in ------------------------


def put(root, **files):
    """Files on the made-up device: name in GARMIN/ -> content."""
    for name, data in files.items():
        (root / "GARMIN" / name).write_bytes(data)


TOPO, DEM = FILES["/maps/topo.img"], FILES["/maps/dem.img"]


def waiting_maps(server, device_root, storage=None):
    all_maps = maps.parse_maps(maps_response(server))
    maps.mark_waiting(storage or LocalStorage(device_root), all_maps)
    return {m.name.split()[-1]: m for m in all_maps}


def test_a_map_written_but_not_taken_in_waits_for_unplugging(server, device_root, xdg):
    put(device_root, **{"W.img": TOPO, "DEM.img": DEM, "W.gma": b"u", "E.img": b"old",
                        "E.gma": b"u", "J.img": TOPO})
    m = waiting_maps(server, device_root)
    assert m["Japan"].waiting == maps.WAIT_INSTALL and not m["Japan"].installed
    assert m["Japan"].status == "will be loaded after the device is unplugged"
    assert m["West"].waiting is None and m["East"].waiting is None
    assert m["East"].update_available  # its new file E2.img is not there


def test_an_update_written_but_not_taken_in_waits_too(server, device_root, xdg):
    put(device_root, **{"W.img": TOPO, "DEM.img": DEM, "E.img": b"old", "E2.img": TOPO})
    east = waiting_maps(server, device_root)["East"]
    assert east.waiting == maps.WAIT_INSTALL and not east.update_available


def test_an_update_with_the_names_installed_now_is_not_told_apart(server, device_root, xdg):
    put(device_root, **{"E.img": TOPO, "DEM.img": DEM})
    all_maps = maps.parse_maps(maps_response(server))
    east = all_maps[1]
    east.files[0].device_path = "Garmin/E.img"  # the new version keeps the old name
    maps.mark_waiting(LocalStorage(device_root), all_maps)
    assert east.waiting is None and east.update_available


@pytest.mark.parametrize("files", [
    {"J.img": b"half"},             # written only in part
    {},                             # not written
])
def test_a_map_not_wholly_on_the_device_does_not_wait(server, device_root, xdg, files):
    put(device_root, **{"W.img": TOPO, "DEM.img": DEM}, **files)
    assert waiting_maps(server, device_root)["Japan"].waiting is None


def test_a_shared_file_alone_does_not_make_a_map_wait(server, device_root, xdg):
    resp = maps_response(server)
    resp["maps"][2]["installoptions"][0]["files"].append(
        resp["maps"][0]["installoptions"][0]["files"][1])  # Japan uses DEM.img too
    put(device_root, **{"W.img": TOPO, "DEM.img": DEM})
    all_maps = maps.parse_maps(resp)
    maps.mark_waiting(LocalStorage(device_root), all_maps)
    assert all_maps[2].waiting is None


def test_a_map_removed_but_not_taken_in_waits_for_unplugging(server, device_root, xdg):
    put(device_root, **{"DEM.img": DEM, "E.img": b"old", "E.gma": b"u"})  # W.img, W.gma gone
    m = waiting_maps(server, device_root)
    assert m["West"].waiting == maps.WAIT_REMOVE  # the shared DEM.img does not count
    assert m["West"].status == "will be removed after the device is unplugged"
    assert m["East"].waiting is None and m["Japan"].waiting is None


def test_a_map_that_looks_removed_still_keeps_its_shared_file(server, device_root, xdg):
    put(device_root, **{"W.img": TOPO, "DEM.img": DEM, "W.gma": b"u"})  # East's own files gone
    all_maps = maps.parse_maps(maps_response(server))
    maps.mark_waiting(LocalStorage(device_root), all_maps)
    west, east = all_maps[0], all_maps[1]
    assert east.waiting == maps.WAIT_REMOVE
    # Removing West keeps DEM.img: East may still be on the device as far as anyone knows.
    assert maps.removal_list(all_maps, west, rewriting=False) == ["Garmin/W.img", "Garmin/W.gma"]


def test_a_device_that_cannot_be_read_marks_nothing(server, device_root, xdg, caplog):
    class Unreadable(LocalStorage):
        def listdir(self, path):
            raise DeviceError("unplugged")

    put(device_root, **{"J.img": TOPO})
    with caplog.at_level("WARNING", logger="openexpress.maps"):
        m = waiting_maps(server, device_root, Unreadable(device_root))
    assert all(x.waiting is None for x in m.values())
    assert m["East"].update_available and m["Japan"].status == "available"
    assert any("cannot read the map files" in r.getMessage() for r in caplog.records)


def test_a_waiting_map_is_not_installed_again(server, device_root, xdg):
    put(device_root, **{"J.img": TOPO})
    all_maps = maps.parse_maps(maps_response(server))
    maps.mark_waiting(LocalStorage(device_root), all_maps)
    with pytest.raises(maps.MapError) as error:
        maps.install(all_maps[2], all_maps, LocalStorage(device_root), unlock_e2)
    assert "will be loaded after the device is unplugged" in str(error.value)


def test_the_maps_page_shows_waiting_maps_on_the_device(server, device_root):
    from openexpress.gui.app import Gtk, MainWindow

    put(device_root, **{"DEM.img": DEM, "E.img": b"old", "E.gma": b"u", "J.img": TOPO})
    all_maps = maps.parse_maps(maps_response(server))
    maps.mark_waiting(LocalStorage(device_root), all_maps)  # Japan written, West removed
    win, _reloads = _maps_window(LocalStorage(device_root))
    MainWindow.show_maps(win, (all_maps, len(all_maps)))
    groups = win.sections["maps"].groups
    on_device = {r.get_title(): r for r in groups[0].rows if hasattr(r, "get_title")}
    assert set(on_device) >= {"TopoActive East", "TopoActive Japan", "TopoActive West"}
    for name in ("TopoActive Japan", "TopoActive West"):
        row = on_device[name]
        buttons = [c for c in _children(row) if isinstance(c, Gtk.Button)]
        assert buttons == [] or not any(b.get_mapped() for b in buttons)
    assert "will be loaded after the device is unplugged" in on_device[
        "TopoActive Japan"].get_subtitle()
    assert on_device["TopoActive Japan"].get_sensitive()
    assert not on_device["TopoActive West"].get_sensitive()
    assert len(groups) == 1  # nothing left to install: no "Available"


def test_the_maps_command_marks_and_refuses_a_waiting_map(server, device_root, xdg,
                                                           monkeypatch, capsys):
    from openexpress.cli import main

    answer_with(monkeypatch, body=json.dumps(maps_response(server)).encode())
    put(device_root, **{"W.img": TOPO, "DEM.img": DEM, "W.gma": b"u", "E.img": b"old",
                        "E.gma": b"u", "J.img": TOPO})
    assert main(["--path", str(device_root), "maps"]) == 0
    out = capsys.readouterr().out
    assert "TopoActive Japan" in out and "will be loaded after the device is unplugged" in out
    assert "0 more maps are available" in out  # Japan is counted as on the device
    before = device_files(device_root)
    assert main(["--path", str(device_root), "maps", "install", "Japan"]) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and "Unplug the device first." in captured.err
    assert device_files(device_root) == before


def test_the_maps_command_says_when_the_map_is_loaded(server, device_root, xdg, monkeypatch,
                                                      capsys):
    from openexpress import cli

    answer_with(monkeypatch, body=json.dumps(maps_response(server)).encode())
    monkeypatch.setattr(cli, "_url_salt", lambda: "salt")
    monkeypatch.setattr(maps, "install", lambda *a, **kw: maps.MapReport(
        written=["GARMIN/J.img"], unlocks=["GARMIN/J.gma"]))
    assert cli.main(["--path", str(device_root), "maps", "install", "Japan", "--apply"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[-2:] == ["Wrote 1 map file and 1 unlock files.",
                        "The map will be loaded after the device is unplugged."]


# --- removal keeps what other maps need --------------------------------------------------


def test_a_map_written_and_waiting_keeps_its_shared_file(server, device_root, xdg):
    resp = maps_response(server)
    resp["maps"][1]["installationstate"] = 0  # East: not on the server's list of the device …
    put(device_root, **{"W.img": TOPO, "W.gma": b"u", "E2.img": TOPO, "DEM.img": DEM})
    all_maps = maps.parse_maps(resp)
    maps.mark_waiting(LocalStorage(device_root), all_maps)
    west, east = all_maps[0], all_maps[1]
    assert east.waiting == maps.WAIT_INSTALL  # … but written, waiting to be taken in
    # Only East needs DEM.img now; removing West keeps it.
    assert maps.removal_list(all_maps, west, rewriting=False) == ["Garmin/W.img", "Garmin/W.gma"]


def test_a_device_that_cannot_be_read_still_keeps_shared_files(server, device_root, xdg):
    class Unreadable(LocalStorage):
        def listdir(self, path):
            raise DeviceError("unplugged")

    all_maps = maps.parse_maps(maps_response(server))
    maps.mark_waiting(Unreadable(device_root), all_maps)
    west = all_maps[0]
    assert maps.removal_list(all_maps, west, rewriting=False) == ["Garmin/W.img", "Garmin/W.gma"]


def test_own_maps_are_never_on_a_removal_list(server, device_root, xdg):
    put(device_root, **{"W.img": TOPO, "DEM.img": DEM, "W.gma": b"u", "Berlin.img": b"own"})
    all_maps = maps.parse_maps(maps_response(server))
    maps.mark_waiting(LocalStorage(device_root), all_maps)
    for m in all_maps:
        assert "Garmin/Berlin.img" not in maps.removal_list(all_maps, m, rewriting=False)


def test_a_map_the_server_does_not_list_is_unknown_to_the_removal(server, device_root, xdg):
    """A known limit, as in Garmin Express: only the maps the server lists are known. When
    the server leaves out an installed map (East), its share of DEM.img is not seen."""
    resp = maps_response(server)
    del resp["maps"][1]
    all_maps = maps.parse_maps(resp)
    west = all_maps[0]
    assert "Garmin/DEM.img" in maps.removal_list(all_maps, west, rewriting=False)


def _a_map(name, state, paths, files_to_remove, waiting=None):
    files = [maps.MapFile(device_path=p, url=f"https://example.org/{n}", md5=None, size=1)
             for n, p in enumerate(paths)]
    return maps.UniversalMap(name=name, map_type="TopoActive", state=state, is_reinstall=True,
                             can_uninstall=True, version="2026.11", region="Test",
                             identifier={}, files=files, files_to_remove=files_to_remove,
                             hosts=[], waiting=waiting)


@pytest.mark.parametrize("state,waiting", [(1, None), (0, maps.WAIT_INSTALL)],
                         ids=["installed", "written-and-waiting"])
def test_a_shared_file_is_kept_whatever_its_spelling(state, waiting):
    # Written and waiting: not on the server's list of the device yet, so nothing to remove.
    a = _a_map("Map A", state, ["Garmin/SID/DA69900.sid"],
               ["Garmin/SID/DA69900.sid"] if state else [], waiting)
    b = _a_map("Map B", 1, ["GARMIN/SID/da69900.SID", "Garmin/B.img"],
               ["GARMIN/SID/da69900.SID", "Garmin/B.img"])
    all_maps = [a, b]
    keep = maps.protected_files(all_maps, b)
    assert "Garmin/SID/DA69900.sid".casefold() in keep
    assert "GARMIN/SID/da69900.SID".casefold() in keep
    removing = maps.removal_list(all_maps, b, rewriting=False)
    assert removing == ["Garmin/B.img"]  # only B's own file
    assert all(p.casefold() != "garmin/sid/da69900.sid" for p in removing)
