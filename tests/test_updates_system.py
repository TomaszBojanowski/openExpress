"""System updates: recognised by their file as well as by the flags of the server.

The answer below has the shape of a real one (an Edge 1050 offered a beta bundle, 14.10.2026):
the bundle with the device software comes as GUPDATE.GSP without "isprimaryfirmware" and
without "isrestartrequired", and its data type, "Firmware", is also that of plain files such
as fonts. All values are made up."""

import hashlib

import pytest

from openexpress import download, updates
from openexpress.cli import main
from openexpress.connect import GarminClient, lower_keys
from openexpress.device.storage import LocalStorage

BUNDLE = b"bundle bytes"
FONT = b"font"


def _entry(part, name, datatype, path, filename, data, order=0, **flags):
    """One offered item, with the keys as the server sends them (camelCase)."""
    return {
        "partNumber": part, "productName": name, "dataType": datatype,
        "displayVersion": "23.40", "severity": "Recommended",
        "isPrimaryFirmware": flags.get("primary", False),
        "releaseNotes": [],
        "deliverable": {"downloadUrl": "https://example.org/" + filename,
                        "md5": hashlib.md5(data).hexdigest().upper(),
                        "sizeInBytes": len(data)},
        "installation": {"path": path, "fileName": filename, "isReinstall": False,
                         "isRestartRequired": flags.get("restart", False), "order": order},
    }


def server_answer():
    """What the update server sends: a system bundle that says nothing about being one, and
    a plain file of the same data type that is listed first and installed first."""
    return {"updates": [
        _entry("006-D0000-00", "Test Font", "Firmware", "Garmin\\Font", "006-D0000-00.fnt", FONT,
               order=5),
        _entry("006-V0000-00", "Test Bundle", "Firmware", "Garmin", "GUPDATE.GSP", BUNDLE),
    ]}


@pytest.fixture
def offered(device_root, monkeypatch):
    answer = lower_keys(server_answer())  # as GarminClient returns it
    monkeypatch.setattr(GarminClient, "software_updates",
                        lambda self, raw, include_reinstalls=False: answer)
    return device_root


@pytest.fixture
def no_download(monkeypatch):
    def fetch(*_a, **_kw):
        raise AssertionError("nothing may be downloaded")

    monkeypatch.setattr(download, "fetch", fetch)


def test_a_system_update_is_known_by_its_file():
    bundle, font = updates.parse_updates(lower_keys(server_answer()))[::-1]
    assert (bundle.is_primary, bundle.restart_required) == (False, False)
    assert bundle.is_system
    assert not font.is_system  # the same data type ("Firmware"), a plain file


@pytest.mark.parametrize(("path", "filename", "flags", "system"), [
    ("Garmin", "GUPDATE.GCD", {}, True),
    ("GARMIN", "gupdate.gsp", {}, True),        # any letter case
    ("Garmin\\Other", "GUPDATE.GSP", {}, True),  # the name counts, not the folder
    ("Garmin", "GUPDATE.GSP.bak", {}, False),
    ("Garmin", "XGUPDATE.GCD", {}, False),
    ("Garmin", "GUPDATE.RGN", {}, False),
    ("Garmin", "settings.fit", {"primary": True}, True),  # the flags of the server still count
    ("Garmin", "settings.fit", {"restart": True}, True),
    ("Garmin", "settings.fit", {}, False),
])
def test_which_files_are_system_updates(path, filename, flags, system):
    answer = lower_keys({"updates": [_entry("006-X0000-00", "X", "CONFIG", path, filename, b"x",
                                            **flags)]})
    (u,) = updates.parse_updates(answer)
    assert u.is_system is system


def test_a_system_update_is_copied_last_and_asks_for_a_restart(device_root, tmp_path,
                                                               monkeypatch):
    items = updates.parse_updates(lower_keys(server_answer()))
    assert [u.part_number for u in updates.sort_for_install(items)] == [
        "006-D0000-00", "006-V0000-00"]  # the bundle last, although its order is lower

    def fetch(urls, dest, size, md5, session=None, progress=None):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(BUNDLE if dest.name == "GUPDATE.GSP" else FONT)
        return dest

    monkeypatch.setattr(download, "fetch", fetch)
    monkeypatch.setattr(updates, "cache_path", lambda u: tmp_path / "cache" / u.filename)
    copied = []
    storage = LocalStorage(device_root)
    write = storage.write_file
    monkeypatch.setattr(storage, "write_file",
                        lambda path, local, progress=None: copied.append(path) or write(
                            path, local, progress))
    report = updates.install(items, storage)
    assert copied == ["Garmin/Font/006-D0000-00.fnt", "Garmin/GUPDATE.GSP"]
    assert report.restart_required
    assert (device_root / "GARMIN/GUPDATE.GSP").read_bytes() == BUNDLE


def test_plain_files_alone_ask_for_no_restart(device_root, tmp_path, monkeypatch):
    (font,) = [u for u in updates.parse_updates(lower_keys(server_answer())) if not u.is_system]

    def fetch(urls, dest, size, md5, session=None, progress=None):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(FONT)
        return dest

    monkeypatch.setattr(download, "fetch", fetch)
    monkeypatch.setattr(updates, "cache_path", lambda u: tmp_path / "cache" / u.filename)
    assert not updates.install([font], LocalStorage(device_root)).restart_required


def test_cli_lists_the_system_update_first_and_marks_it(offered, no_download, capsys):
    assert main(["--path", str(offered), "updates"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[:4] == [
        "Test watch, software 23.39",
        "Available updates: 2",
        "  006-V0000-00         23.40  Recommended       12 B  Test Bundle [PRIMARY]",
        "  006-D0000-00         23.40  Recommended        4 B  Test Font"]


def test_cli_warns_before_a_system_update(offered, no_download, capsys):
    assert main(["--path", str(offered), "updates", "--install", "006-V0000-00"]) == 0
    out = capsys.readouterr().out
    assert ("Warning: this is a device system update. Charge the device and do not disconnect "
            "it while copying.") in out
    assert "Dry run: nothing was downloaded or written." in out
    assert not (offered / "GARMIN/GUPDATE.GSP").exists()


def test_cli_does_not_warn_before_a_plain_file(offered, no_download, capsys):
    assert main(["--path", str(offered), "updates", "--install", "006-D0000-00"]) == 0
    out = capsys.readouterr().out
    assert "Warning: this is a device system update" not in out
    assert "Dry run: nothing was downloaded or written." in out
