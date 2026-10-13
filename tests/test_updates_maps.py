import base64
import hashlib
import http.server
import json
import threading

import pytest
import requests

from openexpress import download, maps, updates
from openexpress.connect import ApiError, lower_keys
from openexpress.device.storage import LocalStorage

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
            self.groups.append(types.SimpleNamespace(title=title, rows=[]))
            self.groups[-1].add = self.groups[-1].rows.append
            return self.groups[-1]

    reloads = []
    win = types.SimpleNamespace(
        session=types.SimpleNamespace(storage=storage), sections={"maps": Section()},
        busy=False, _action_buttons=[], confirm_map=lambda m: None,
        confirm_remove=lambda m: None, _search_filter=lambda search, rows: None,
        load_section=lambda key, force=False: reloads.append((key, force)))
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
