"""Install All: a file refused does not stop the others, and an update whose folder the device
hides inside a folder that is seen is refused before anything is downloaded. A made-up watch
whose GarminDevice.xml names a folder for one of its components (Garmin/Prompts) that its
listing does not show; the server offers its software, a file of that folder and a map
support file. All devices, files and answers here are made up."""

import types

import pytest
from fakes import Section, offered_update, window_methods, xml_with
from fakes import window as stand_in

from openexpress import download, updates
from openexpress.cli import main
from openexpress.connect import GarminClient, lower_keys
from openexpress.device.storage import DeviceError, DeviceNotResponding, LocalStorage

SOFTWARE = b"made-up watch software"
PROMPTS = b"made-up voice prompts"
SID = b"made-up map support file"

# The folder the watch names for a component of its own, which its listing does not show.
PROMPTS_COMPONENT = """  <UpdateFile><PartNumber>006-D0001-00</PartNumber><Version><Major>2</Major>
   <Minor>12</Minor></Version><Path>Garmin/Prompts</Path><FileName>made_up.bundle.sar</FileName>
  </UpdateFile>
"""


def server_answer():
    return {"updates": [
        offered_update("006-B0000-00", "Made-up Watch", "Garmin", "GUPDATE.GSP", SOFTWARE,
                       primary=True),
        offered_update("006-D0002-00", "Made-up Prompt Bundle", "GARMIN\\PROMPTS",
                       "prompt_bundle.hvm", PROMPTS),
        offered_update("006-D0003-00", "Map support file", "Garmin\\SID", "D0000300.sid", SID),
    ]}


@pytest.fixture
def watch(device_root):
    """The made-up watch: its GarminDevice.xml names Garmin/Prompts, which it does not list."""
    xml_with(device_root, PROMPTS_COMPONENT)
    (device_root / "GARMIN" / "SID").mkdir()
    return device_root


def _offered(root):
    from openexpress import service

    storage = LocalStorage(root)
    device = service.read_device(storage, found_now=False)
    items = updates.offered(storage, lower_keys(server_answer()), device, forget_installed=False)
    return storage, {u.part_number: u for u in items}


@pytest.fixture
def downloads(monkeypatch, tmp_path):
    """download.fetch replaced: the files fetched, in their order."""
    fetched = []
    content = {"GUPDATE.GSP": SOFTWARE, "prompt_bundle.hvm": PROMPTS, "D0000300.sid": SID}

    def fetch(urls, dest, size, md5, session=None, progress=None):
        fetched.append(dest.name)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content[dest.name])
        return dest

    monkeypatch.setattr(updates, "cache_path", lambda u: tmp_path / "cache" / u.filename)
    monkeypatch.setattr(download, "fetch", fetch)
    return fetched


HIDDEN_WHY = ("the device keeps it in the folder Garmin/Prompts, hidden from the file "
              "manager, and openExpress cannot open a direct USB connection to this device to "
              "write there (the Flatpak version needs access to USB devices for it)")


# --- the folder the device hides inside a folder that is seen ------------------------------------


def test_a_folder_the_device_names_but_does_not_list_is_hidden(watch):
    _storage, by = _offered(watch)
    assert by["006-D0002-00"].hidden_inside == "Garmin/Prompts"
    assert by["006-B0000-00"].hidden_inside is None and by["006-D0003-00"].hidden_inside is None
    assert [u.part_number for u in updates.installable(list(by.values()))] == [
        "006-B0000-00", "006-D0003-00"]


def test_a_folder_the_device_does_not_name_is_made_for_the_write(device_root, downloads):
    (device_root / "GARMIN" / "SID").mkdir()
    storage, by = _offered(device_root)  # no component in Garmin/Prompts: only not there yet
    assert by["006-D0002-00"].hidden_inside is None
    report = updates.install([by["006-D0002-00"]], storage)
    assert [u.part_number for u in report.written] == ["006-D0002-00"]
    assert (device_root / "GARMIN" / "PROMPTS" / "prompt_bundle.hvm").read_bytes() == PROMPTS


def test_install_all_refuses_the_hidden_one_before_it_downloads_and_installs_the_rest(
        watch, downloads):
    storage, by = _offered(watch)
    report = updates.install(list(by.values()), storage)
    assert sorted(u.part_number for u in report.written) == ["006-B0000-00", "006-D0003-00"]
    assert [(u.part_number, why) for u, why in report.failed] == [("006-D0002-00", HIDDEN_WHY)]
    assert "prompt_bundle.hvm" not in downloads
    assert not any(p.name.casefold() == "prompts" for p in (watch / "GARMIN").iterdir())
    assert (watch / "GARMIN" / "GUPDATE.GSP").read_bytes() == SOFTWARE


def test_the_hidden_one_alone_is_an_error_that_says_why(watch, downloads):
    storage, by = _offered(watch)
    with pytest.raises(updates.UpdateError) as refused:
        updates.install([by["006-D0002-00"]], storage)
    assert str(refused.value) == ("1 update was not installed:\nMade-up Prompt Bundle 2.18: "
                                  + HIDDEN_WHY)
    assert downloads == []


# --- a file the device refuses: the others are written all the same ------------------------------


def _refusing(monkeypatch, name, error):
    """LocalStorage.write_file_checked refuses the file `name` with `error`."""
    real = LocalStorage.write_file_checked

    def write(self, path, *a, **kw):
        if path.casefold().endswith(name.casefold()):
            raise error
        return real(self, path, *a, **kw)

    monkeypatch.setattr(LocalStorage, "write_file_checked", write)


def test_a_file_refused_does_not_stop_the_software(watch, downloads, monkeypatch):
    storage, by = _offered(watch)
    _refusing(monkeypatch, "D0000300.sid",
              DeviceError("Cannot create directory GARMIN/SID: made-up refusal"))
    report = updates.install([by["006-D0003-00"], by["006-B0000-00"]], storage)
    assert [u.part_number for u in report.written] == ["006-B0000-00"]
    assert [(u.part_number, why) for u, why in report.failed] == [
        ("006-D0003-00", "Cannot create directory GARMIN/SID: made-up refusal")]
    assert updates.failed_text(report) == (
        "1 update was not installed:\nMap support file 2.18: Cannot create directory "
        "GARMIN/SID: made-up refusal")


def test_a_device_that_stopped_answering_stops_the_install(watch, downloads, monkeypatch):
    storage, by = _offered(watch)
    _refusing(monkeypatch, "D0000300.sid", DeviceNotResponding("made-up stall"))
    with pytest.raises(DeviceNotResponding):
        updates.install([by["006-D0003-00"], by["006-B0000-00"]], storage)
    assert not (watch / "GARMIN" / "GUPDATE.GSP").exists()


def test_a_device_gone_stops_the_install(watch, downloads, monkeypatch):
    storage, by = _offered(watch)
    _refusing(monkeypatch, "D0000300.sid", DeviceError("made-up: the device is gone"))
    monkeypatch.setattr(updates, "_gone", lambda storage: True)
    with pytest.raises(DeviceError):
        updates.install([by["006-D0003-00"], by["006-B0000-00"]], storage)
    assert not (watch / "GARMIN" / "GUPDATE.GSP").exists()


def test_nothing_written_is_the_first_error_and_the_log_names_each(watch, downloads, monkeypatch,
                                                                      caplog):
    import logging

    storage, by = _offered(watch)
    _refusing(monkeypatch, "D0000300.sid", DeviceError("made-up refusal one"))
    _refusing(monkeypatch, "GUPDATE.GSP", DeviceError("made-up refusal two"))
    with caplog.at_level(logging.INFO, logger="openexpress"), \
            pytest.raises(DeviceError) as refused:
        updates.install([by["006-D0003-00"], by["006-B0000-00"]], storage)
    assert str(refused.value) == "made-up refusal one"
    said = [r.getMessage() for r in caplog.records if "the others go on" in r.getMessage()]
    assert [line.split(" not installed")[0] for line in said] == [
        "software update: 006-D0003-00 2.18", "software update: 006-B0000-00 2.18"]


# --- the command line ----------------------------------------------------------------------------


@pytest.fixture
def offered_to(watch, monkeypatch):
    answer = lower_keys(server_answer())
    monkeypatch.setattr(GarminClient, "software_updates",
                        lambda self, raw, include_reinstalls=False: answer)
    monkeypatch.setattr(GarminClient, "unit_info", lambda self, unit_id, part_number: {})
    return watch


NOT_INSTALLED = ("Not installed: Made-up Prompt Bundle 2.18 goes to the folder Garmin/Prompts, "
                 "which the device hides from the file manager, and openExpress cannot open a "
                 "direct USB connection to this device to write there (the Flatpak version needs "
                 "access to USB devices for it, see the README). The other updates install as "
                 "usual.")


def test_the_list_says_which_one_cannot_be_installed_yet(offered_to, capsys):
    assert main(["--path", str(offered_to), "updates"]) == 0
    out = [line.strip() for line in capsys.readouterr().out.splitlines()]
    assert NOT_INSTALLED in out


def test_install_all_on_the_command_line_installs_the_rest(offered_to, downloads, capsys):
    assert main(["--path", str(offered_to), "updates", "--install", "all", "--apply"]) == 1
    out = capsys.readouterr().out.splitlines()
    assert NOT_INSTALLED in out and "Copied 2 files." in out
    assert "prompt_bundle.hvm" not in downloads
    assert (offered_to / "GARMIN" / "SID" / "D0000300.sid").read_bytes() == SID


def test_a_file_refused_on_the_command_line_is_said_with_why(offered_to, downloads, monkeypatch,
                                                              capsys):
    _refusing(monkeypatch, "D0000300.sid", DeviceError("made-up refusal"))
    assert main(["--path", str(offered_to), "updates", "--install", "all", "--apply"]) == 1
    out = capsys.readouterr().out.splitlines()
    assert "Copied 1 file." in out
    assert out[out.index("Copied 1 file.") + 1:][:2] == [
        "1 update was not installed:", "Map support file 2.18: made-up refusal"]


# --- the window ----------------------------------------------------------------------------------


def _window_of(storage, offered):
    s = types.SimpleNamespace(storage=storage, client=types.SimpleNamespace(http=None),
                              device=types.SimpleNamespace(unit_id=None,
                                                           description="Made-up Watch",
                                                           software_version_str="2.12"),
                              mass_storage=False)
    win = stand_in(session=s, updates_cache=offered, updates_beta=False, progress=None,
                   sections={"updates": Section()}, busy=False, _action_buttons=[],
                   _set_update_state=lambda new, staged=(), not_installed=(): None,
                   confirm_update=lambda u: None, load_section=lambda *a, **kw: None)
    window_methods(win, ["_update_row", "_updates_status", "_button", "show_updates",
                         "_show_installed"])
    win.show_updates(offered)
    return win, s


ROW_LINE = "Cannot be installed without a direct USB connection"
GROUP_NOTE = ("1 of these cannot be installed: the device keeps it in a folder it hides from the "
              "file manager, and openExpress cannot open a direct USB connection to this device "
              "(the Flatpak version needs access to USB devices for it, see the README). The "
              "others install as usual.")


@pytest.mark.needs_display
def test_the_row_of_the_hidden_one_has_its_button_off_and_the_group_says_why(watch):
    from fakes import descendants
    from gi.repository import Adw, Gtk

    storage, by = _offered(watch)
    win, _s = _window_of(storage, list(by.values()))
    rows = [r for g in win.sections["updates"].groups for r in g.rows
            if isinstance(r, Adw.ActionRow) and r.get_title() == "Made-up Prompt Bundle"]
    (row,) = rows
    assert row.get_subtitle().splitlines()[-1] == ROW_LINE
    assert [w.get_sensitive() for w in descendants(row) if isinstance(w, Gtk.Button)] == [False]
    notes = [g.description for g in win.sections["updates"].groups if g.description]
    assert notes == [GROUP_NOTE]


@pytest.mark.needs_display
def test_done_after_install_all_says_what_was_not_installed(watch, downloads, in_the_window):
    from openexpress.gui.updates_page import _install_task

    said = []
    in_the_window("inform", lambda win, name, heading, body, **kw:
                  said.append((name, heading, body)))
    storage, by = _offered(watch)
    win, s = _window_of(storage, list(by.values()))
    work, done, _failed = _install_task(win, s, list(by.values()))
    done(work())
    ((name, heading, body),) = said
    assert (name, heading) == ("update-done", "Done")
    assert "1 update was not installed:\nMade-up Prompt Bundle 2.18: " + HIDDEN_WHY in body


def _group_in_polish(count: int, where: str) -> str:
    return (f"{count} z\u00a0nich nie można zainstalować: urządzenie trzyma {where} przed menedżerem "
            f"plików, a\u00a0openExpress nie może otworzyć bezpośredniego połączenia USB z\u00a0tym "
            "urządzeniem (wersja Flatpak potrzebuje do tego dostępu do urządzeń USB, patrz "
            "README). Pozostałe instalują się normalnie.")


ONE_FOLDER = "ją w\u00a0folderze ukrytym"
FOLDERS = "je w\u00a0folderach ukrytych"


@pytest.mark.parametrize(("count", "said"), [
    (1, _group_in_polish(1, ONE_FOLDER)), (3, _group_in_polish(3, FOLDERS)),
    (5, _group_in_polish(5, FOLDERS)), (12, _group_in_polish(12, FOLDERS)),
    (22, _group_in_polish(22, FOLDERS)),
])
def test_the_note_of_the_group_in_polish_by_count(count, said):
    from fakes import in_language

    from openexpress.gui.updates_page import folders_hidden_text

    hidden = [types.SimpleNamespace(refused_inside=True) for _n in range(count)]
    with in_language("pl"):
        text = folders_hidden_text([*hidden, types.SimpleNamespace(refused_inside=False)])
    assert text == said


def test_a_busy_device_is_said_among_those_not_installed(watch, downloads, monkeypatch):
    """The session for the hidden folder is refused: the sentence that says what to do stands
    after the update's name, and the other updates are installed."""
    from openexpress import hidden_folders
    from openexpress.device.storage import DeviceBusy

    gio_storage = pytest.importorskip("openexpress.device.gio_storage")
    storage, by = _offered(watch)
    by["006-D0002-00"].folder_writable = True

    def busy(*args, **kw):
        raise DeviceBusy(gio_storage.busy_text())

    monkeypatch.setattr(hidden_folders, "write", busy)
    report = updates.install(list(by.values()), storage)
    assert [u.part_number for u, _why in report.failed] == ["006-D0002-00"]
    assert len(report.written) == 2
    assert ("The device is busy with another operation, such as a copy in the file manager or "
            "another task of openExpress. Try again when it ends.") in updates.failed_text(report)


def test_the_command_line_says_why_an_update_in_a_hidden_folder_is_not_installed(watch):
    storage, by = _offered(watch)
    assert updates.not_installed_inside_text(by["006-D0002-00"]) == NOT_INSTALLED


def test_in_polish(watch, downloads, capsys):
    from fakes import in_language

    storage, by = _offered(watch)
    with in_language("pl"):  # why is said as the install goes, in the language of the session
        report = updates.install(list(by.values()), storage)
        said = updates.failed_text(report)
        line = updates.not_installed_inside_text(by["006-D0002-00"])
    nb = " "
    assert said == ("Nie zainstalowano 1 aktualizacji:\nMade-up Prompt Bundle 2.18: urządzenie "
                    f"trzyma ją w{nb}folderze Garmin/Prompts, ukrytym przed menedżerem plików, "
                    f"a{nb}openExpress nie może otworzyć bezpośredniego połączenia USB z{nb}tym "
                    "urządzeniem, aby tam zapisać (wersja Flatpak potrzebuje do tego dostępu do "
                    "urządzeń USB)")
    assert line == ("Nie zainstalowano: Made-up Prompt Bundle 2.18 trafia do folderu "
                    "Garmin/Prompts, który urządzenie ukrywa przed menedżerem plików, "
                    f"a{nb}openExpress nie może otworzyć bezpośredniego połączenia USB z{nb}tym "
                    "urządzeniem, aby tam zapisać (wersja Flatpak potrzebuje do tego dostępu do "
                    "urządzeń USB, patrz README). Pozostałe aktualizacje instalują się normalnie.")
