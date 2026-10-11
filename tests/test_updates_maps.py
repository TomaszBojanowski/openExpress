import base64
import hashlib
import http.server
import threading

import pytest

from openexpress import download, maps, updates
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
