"""Updates the device already has: not counted, left out of 'all', and what to do after an
install."""
import types

import pytest
from fakes import (
    OFFERED_BYTES,
    in_language,
    page_window,
    software_update,
    update_answer,
)
from fakes import window as stand_in

from openexpress.cli import main

# --- files the device already has are not updates ---------------------------------------------

def test_file_with_the_same_size_and_md5_is_not_an_update(offered):
    from openexpress import updates as upd
    from openexpress.device.storage import LocalStorage

    root, answer = offered
    before = sorted((p.name, p.read_bytes()) for p in (root / "GARMIN/SID").iterdir())
    items = upd.mark_present(LocalStorage(root), upd.parse_updates(answer))
    assert [(u.part_number, u.on_device) for u in items] == [
        ("006-DA001-00", True),    # same size, same MD5
        ("006-DA002-00", False),   # same size, other content
        ("006-DA003-00", False),   # not on the device
        ("006-B0000-00", False)]
    assert [u.part_number for u in upd.pending(items)] == [
        "006-DA002-00", "006-DA003-00", "006-B0000-00"]
    # Only read: nothing on the device changed.
    assert sorted((p.name, p.read_bytes()) for p in (root / "GARMIN/SID").iterdir()) == before


def test_nothing_can_be_said_without_a_size_or_an_md5(offered, monkeypatch):
    from openexpress import updates as upd
    from openexpress.device.storage import DeviceError, LocalStorage

    root, answer = offered
    st = LocalStorage(root)
    u = upd.parse_updates(answer)[0]
    assert upd.mark_present(st, [u])[0].on_device
    for change in ({"md5": None}, {"md5": "not-an-md5"}, {"size": None}, {"size": 0}):
        other = upd.parse_updates(answer)[0]
        for name, value in change.items():
            setattr(other, name, value)
        assert not upd.mark_present(st, [other])[0].on_device, change

    def broken(path):
        raise DeviceError("the device went away")

    # Read before and the same size and time: its MD5 is known without reading it again.
    st.read = st.read_at = lambda path, told=None: broken(path)
    assert upd.mark_present(st, [u])[0].on_device
    # Never read: not known to be there, offered.
    monkeypatch.setattr(upd, "_md5_read", {})
    monkeypatch.setattr(upd, "_kept_md5", dict)  # nor kept for the next window
    assert not upd.mark_present(st, [u])[0].on_device
    # Its size and time not known either.
    st.info = broken
    assert not upd.mark_present(st, [u])[0].on_device


def test_a_file_the_device_gives_no_time_is_read_again_for_its_md5(offered, monkeypatch):
    """A device that gives a file no time (0): the MD5 read before may be of other bytes of the
    same size, so the file is read again each time, never taken from what was read before."""
    import dataclasses

    from openexpress import updates as upd
    from openexpress.device.storage import LocalStorage

    root, answer = offered
    st = LocalStorage(root)
    real_info = st.info
    st.info = lambda path: dataclasses.replace(real_info(path), mtime=0.0)
    monkeypatch.setattr(upd, "_md5_read", {})
    u = upd.parse_updates(answer)[0]
    assert upd.mark_present(st, [u])[0].on_device
    real = st.resolve(u.device_path)
    (root / real).write_bytes(bytes(len((root / real).read_bytes())))  # same size, other bytes
    assert not upd.mark_present(st, [u])[0].on_device


def test_readable_names_of_types_and_severities(offered):
    from openexpress import updates as upd

    _root, answer = offered
    etyp, _two, _three, firmware = upd.parse_updates(answer)
    assert (etyp.title, etyp.filename, etyp.severity_text) == (
        "Map support file", "DA00100.sid", "Recommended")
    assert firmware.title == "Test watch Device Software"  # a real name is kept
    assert firmware.severity_text == "Critical"
    etyp.datatype = etyp.name = "SomethingNew"
    etyp.severity = "Urgent"  # made up
    assert (etyp.title, etyp.severity_text) == ("SomethingNew", "Urgent")  # unknown: as sent
    etyp.severity = "OPTIONAL"
    assert etyp.severity_text == "Optional"  # a name of its own, in any letter case
    etyp.name, etyp.datatype = "", "CONFIG"
    assert etyp.title == "Configuration"

    with in_language("pl"):
        pl = upd.parse_updates(answer)
        pl[1].severity = "Optional"
        assert (pl[0].title, pl[0].severity_text, pl[1].severity_text, pl[3].severity_text) == (
            "Plik dodatkowy mapy", "Zalecana", "Opcjonalna", "Krytyczna")


def test_cli_updates_counts_only_real_updates(offered, capsys):
    root, _answer_ = offered
    assert main(["--path", str(root), "updates"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[1] == "Available updates: 3"
    assert out[2:5] == [
        # GUPDATE.GCD: a system update, although the server sets neither flag.
        "  006-B0000-00          2.70  Critical           2 B  Test watch Device Software [PRIMARY]",
        "  006-DA002-00          2.70  Recommended        9 B  Map support file (DA00200.sid)",
        "  006-DA003-00          2.70  Recommended        7 B  Map support file (DA00300.sid)"]
    assert "Already on the device: 1" in out and not any("DA00100" in line for line in out)

    assert main(["--path", str(root), "-v", "updates"]) == 0
    out = capsys.readouterr().out.splitlines()
    at = out.index("Already on the device: 1")
    assert out[at + 1:at + 3] == [
        "  006-DA001-00          2.70  Recommended        9 B  Map support file (DA00100.sid)",
        "      -> Garmin/SID/DA00100.sid"]


def test_cli_says_up_to_date_when_everything_offered_is_on_the_device(offered, capsys):
    root, answer = offered
    del answer["updates"][1:]
    assert main(["--path", str(root), "updates"]) == 0
    assert capsys.readouterr().out.splitlines()[1:] == [
        "Software is up to date.", "Already on the device: 1"]


def test_window_does_not_count_files_already_on_the_device(offered):
    from openexpress import updates as upd
    from openexpress.device.storage import LocalStorage
    from openexpress.gui.app import MainWindow

    root, answer = offered
    del answer["updates"][1:]  # only the file the device already has
    client = types.SimpleNamespace(software_updates=lambda raw, include_reinstalls=False: answer)
    s = types.SimpleNamespace(client=client, storage=LocalStorage(root),
                              device=types.SimpleNamespace(raw=b""))
    states = []
    win = stand_in(session=s, _update_check_session=None, update_state=None,
                   _set_update_state=lambda new, staged=(), not_installed=(): states.append(new),
                   _render_update_state=lambda: None,
                   in_background=lambda work, done, failed: done(work()))
    MainWindow._check_updates_quietly(win)
    assert states == [[]]  # "Software is up to date", no "update available"
    assert upd.pending(upd.mark_present(s.storage, upd.parse_updates(answer))) == []


# --- "updates --install all" leaves out what the device already has -----------------------------


def _marked(root, answer):
    from openexpress import updates as upd
    from openexpress.device.storage import LocalStorage

    return upd.mark_present(LocalStorage(root), upd.parse_updates(answer))


def _with_metadata(answer, *parts):
    """Add the -META entry the server sends along with these parts."""
    for part in parts:
        answer["updates"] += update_answer((part + "-META", "Update information", "UpdateMetadata",
                                      part + ".json", b"{}", "Critical"))["updates"]
    return answer


def test_all_leaves_out_the_files_already_on_the_device(offered):
    from openexpress import updates as upd

    root, answer = offered
    items = _marked(root, _with_metadata(answer, "006-DA001-00", "006-DA002-00"))
    chosen = [u.part_number for u in upd.select(items, ["all"])]
    assert chosen == ["006-DA002-00", "006-DA003-00", "006-B0000-00", "006-DA002-00-META"]
    # Neither the file nor the entry that only describes it.
    assert [u.part_number for u in upd.present_with_metadata(items)] == [
        "006-DA001-00", "006-DA001-00-META"]
    # With the reinstalls, nothing is left out.
    assert len(upd.select(items, ["all"], keep_present=True)) == len(items) == 6


def test_part_number_given_by_name_is_always_taken(offered):
    from openexpress import updates as upd

    root, answer = offered
    items = _marked(root, _with_metadata(answer, "006-DA001-00"))
    assert items[0].on_device
    assert [u.part_number for u in upd.select(items, ["006-da001-00"])] == [
        "006-DA001-00", "006-DA001-00-META"]


def test_cli_install_all_says_what_it_skips(offered, capsys):
    root, _a = offered
    assert main(["--path", str(root), "updates", "--install", "all"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[1] == "Skipped 1 file that is already on the device."
    assert out[2] == "To install:"
    listed = [line.split()[0] for line in out[3:6]]
    assert sorted(listed) == ["006-B0000-00", "006-DA002-00", "006-DA003-00"]
    assert not any("DA00100" in line for line in out)

    assert main(["--path", str(root), "-v", "updates", "--install", "all"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[1:3] == [
        "Skipped 1 file that is already on the device.",
        "  006-DA001-00          2.70  Recommended        9 B  Map support file (DA00100.sid)"]


def test_cli_install_all_with_nothing_left_writes_nothing(offered, capsys, monkeypatch):
    from openexpress import download

    root, answer = offered
    del answer["updates"][1:]  # the server offers only the file the device already has
    monkeypatch.setattr(download, "fetch", lambda *a, **kw: pytest.fail("a download was started"))
    before = sorted((p.name, p.read_bytes()) for p in (root / "GARMIN/SID").iterdir())
    for argv in (["updates", "--install", "all"], ["updates", "--install", "all", "--apply"]):
        assert main(["--path", str(root), *argv]) == 0
        assert capsys.readouterr().out.splitlines()[1:] == [
            "Skipped 1 file that is already on the device.",
            "Nothing to install: every offered file is already on the device."]
    assert sorted((p.name, p.read_bytes()) for p in (root / "GARMIN/SID").iterdir()) == before


def test_cli_install_all_with_reinstalls_and_by_name_still_take_the_file(offered, capsys):
    root, _a = offered
    for argv in (["updates", "--reinstalls", "--install", "all"],
                 ["updates", "--install", "006-DA001-00"]):
        assert main(["--path", str(root), *argv]) == 0
        out = capsys.readouterr().out
        assert "Skipped" not in out and "006-DA001-00 2.70  Map support file" in out


def test_cli_install_all_with_nothing_offered_says_up_to_date(offered, capsys, monkeypatch):
    from openexpress import download

    root, answer = offered
    answer["updates"].clear()
    monkeypatch.setattr(download, "fetch", lambda *a, **kw: pytest.fail("a download was started"))
    before = sorted((p.name, p.read_bytes()) for p in (root / "GARMIN/SID").iterdir())
    for apply in ([], ["--apply"]):
        assert main(["--path", str(root), "updates", "--install", "all", *apply]) == 0
        assert capsys.readouterr().out.splitlines() == [
            "Test watch, software 23.39", "Software is up to date."]  # no empty plan
    assert sorted((p.name, p.read_bytes()) for p in (root / "GARMIN/SID").iterdir()) == before

    assert main(["--path", str(root), "updates", "--reinstalls", "--install", "all"]) == 0
    assert capsys.readouterr().out.splitlines()[1:] == ["The server returned no items."]


def test_part_number_that_is_not_offered_is_still_an_error(offered, capsys):
    root, answer = offered
    for offer in ("some", "none"):
        if offer == "none":
            answer["updates"].clear()
        assert main(["--path", str(root), "updates", "--install", "006-NOPE-00"]) == 1
        out = capsys.readouterr()
        assert "Not in the list: 006-NOPE-00" in out.err
        assert "up to date" not in out.out and "To install" not in out.out


def test_skipped_files_message_in_polish():
    from openexpress.i18n import ngettext

    with in_language("pl"):
        said = [ngettext("Skipped {count} file that is already on the device.",
                         "Skipped {count} files that are already on the device.",
                         n).format(count=n) for n in (1, 3, 5)]
    assert said == ["Pominięto 1 plik, który już jest na urządzeniu.",
                    "Pominięto 3 pliki, które już są na urządzeniu.",
                    "Pominięto 5 plików, które już są na urządzeniu."]


# --- after an update is installed: what to do next ----------------------------------------------

NEXT_STEP = "choose to install now"


TURN_ON_STEP = "  2. If the device does not turn on by itself, turn it on.\n"


@pytest.fixture
def downloads(monkeypatch):
    """Downloads without the network: the bytes of the update offered at that address (with
    its size and MD5, as a real download's are)."""
    from openexpress import download

    def fetch(urls, dest, size, md5, session=None, progress=None):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(OFFERED_BYTES[urls[0].rsplit("/", 1)[1]])
        return dest

    monkeypatch.setattr(download, "fetch", fetch)


def test_cli_says_what_to_do_after_every_install(offered, downloads, xdg, capsys, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(xdg / "cache"))
    root, answer = offered
    assert not answer["updates"][2]["installation"].get("isrestartrequired")  # no restart flag

    assert main(["--path", str(root), "updates", "--install", "006-DA003-00"]) == 0
    out = capsys.readouterr().out
    assert "Dry run" in out and "To finish the installation" not in out  # nothing was copied

    assert main(["--path", str(root), "updates", "--install", "006-DA003-00", "--apply"]) == 0
    out = capsys.readouterr().out
    assert (root / "GARMIN/SID/DA00300.sid").read_bytes() == b"missing"
    lines = out.rstrip().splitlines()
    assert lines[lines.index("Copied 1 file."):] == [
        "Copied 1 file.",
        "",
        "To finish the installation:",
        "  1. Unplug the device from the computer.",
        "  2. If the device does not turn on by itself, turn it on.",
        "  3. The device installs the update by itself. It may ask whether to install it:",
        "     choose to install now. It may also restart. Wait until it has finished.",
        "  4. Plug it in again and run `openexpress updates`: the installed update",
        "     should no longer be listed.",
    ]


def test_steps_are_shown_once_also_for_an_update_that_needs_a_restart(offered, downloads, xdg,
                                                                      capsys, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(xdg / "cache"))
    root, answer = offered
    answer["updates"][2]["installation"]["isrestartrequired"] = True
    assert main(["--path", str(root), "updates", "--install", "006-DA003-00", "--apply"]) == 0
    out = capsys.readouterr().out
    assert out.count("To finish the installation") == 1 and out.count(NEXT_STEP) == 1


def test_window_says_what_to_do_next_without_naming_a_command():
    from openexpress.gui.app import MainWindow
    from openexpress.updates import InstallReport

    text = MainWindow._update_done_text(InstallReport(written=[software_update()]))
    assert text.startswith("Files copied to the device.\nTo finish the installation:")
    assert NEXT_STEP in text and "check for updates again" in text
    assert "openexpress updates" not in text
    # An accessory may stay off once unplugged (e.g. a Varia): then it is turned on, and
    # nothing promises a restart that may not come.
    assert TURN_ON_STEP.strip() in text.split("\n")
    assert "It may also restart. Wait until it has finished." in text
    # Whether the server says a restart is needed makes no difference.
    assert MainWindow._update_done_text(
        InstallReport(written=[software_update(restart_required=True)])) == text
    assert MainWindow._update_done_text(InstallReport()) == "Files copied to the device."


@pytest.mark.parametrize("lang", ["en", "pl"])
def test_the_window_s_steps_wrap_by_themselves(lang):
    """The window wraps its words itself: each step one line, no sentence broken in two, no
    indent, and no empty line at the end of the message."""
    from openexpress.gui.app import MainWindow
    from openexpress.updates import InstallReport

    with in_language(lang):
        text = MainWindow._update_done_text(InstallReport(written=[software_update()]))
    lines = text.split("\n")
    assert len(lines) == 6  # copied, the heading of the steps, the four steps
    assert all(line == line.strip() and line.endswith((".", ":")) for line in lines)
    assert [line[:3] for line in lines[2:]] == ["1. ", "2. ", "3. ", "4. "]


def test_next_steps_in_polish():
    from openexpress.i18n import _
    from openexpress.updates import FINISH_STEPS, FINISH_STEPS_WINDOW

    with in_language("pl"):
        cli_text, window_text = _(FINISH_STEPS), _(FINISH_STEPS_WINDOW)
    for text in (cli_text, window_text):
        assert "wybierz instalację teraz" in text and "Może też uruchomić się ponownie" in text
        assert "2. Jeśli urządzenie nie włączy się samo, włącz je.\n" in text
    assert cli_text.startswith("\nAby dokończyć instalację:\n  1. ") and cli_text.endswith(".\n")
    assert "Może też uruchomić się ponownie.\n     Poczekaj, aż skończy.\n  4. " in cli_text
    # The window wraps the words itself: a step a line, nothing around them.
    assert window_text.startswith("Aby dokończyć instalację:\n1. ") and window_text.endswith(".")
    assert "Może też uruchomić się ponownie. Poczekaj, aż skończy.\n4. " in window_text
    assert "`openexpress updates`" in cli_text and "openexpress" not in window_text
    assert "sprawdź aktualizacje jeszcze raz" in window_text


# --- the list of action buttons after a page is rebuilt -----------------------------------------



@pytest.mark.needs_display
@pytest.mark.parametrize("key", ["maps", "updates", "apps"])
def test_a_rebuilt_page_leaves_no_removed_buttons_in_the_list(key):
    win, section = page_window(key)

    def show(result):
        group = section.group("On the Device")
        group.set_header_suffix(win._button("Check Again", lambda: None))
        group.add(win._trash("Remove", lambda: None))

    for built in range(1, 4):
        win._load(key, "Loading…", lambda: None, show)
        # Only the buttons of the page as it is now: none of an earlier build.
        assert len(win._action_buttons) == 2, built
        assert all(b.get_root() is win.toplevel for b in win._action_buttons)


@pytest.mark.needs_display
def test_buttons_of_other_pages_stay_in_the_list():
    win, section = page_window("maps")
    elsewhere = win._button("Sync", lambda: None)  # e.g. on the device page
    win.toplevel.set_titlebar(elsewhere)
    show = lambda result: section.group().add(win._button("Check Again", lambda: None))  # noqa: E731 - a one-line page
    win._load("maps", "Loading…", lambda: None, show)
    win._load("maps", "Loading…", lambda: None, show)
    assert elsewhere in win._action_buttons and len(win._action_buttons) == 2


def test_cli_update_install_tells_how_far_the_copy_is(offered, monkeypatch, capsys):
    from openexpress import updates as upd
    from openexpress.progress import Step

    root, _answer_ = offered

    def install(chosen, storage, session=None, progress=None, unit_id=None, offered=None):
        progress(Step("Copying Font to the device", 1000, 2000, in_bytes=True))  # half of it
        return upd.InstallReport(written=[software_update()])

    monkeypatch.setattr(upd, "install", install)
    assert main(["--path", str(root), "updates", "--install", "006-DA003-00", "--apply"]) == 0
    captured = capsys.readouterr()
    # Not a terminal: a line when the step begins.
    assert "  Copying Font to the device\n" in captured.out + captured.err


def test_about_links_to_the_support_page(monkeypatch):
    """"About openExpress" links to the support page, beside the website and the issue
    tracker."""
    from gi.repository import Adw

    from openexpress.gui.app import MainWindow

    dialogs = []

    class About:
        def __init__(self, **props):
            self.props, self.links = props, []
            dialogs.append(self)

        def add_link(self, title, url):
            self.links.append((title, url))

        def set_translator_credits(self, credits):
            pass

        def present(self, parent):
            pass

    monkeypatch.setattr(Adw, "AboutDialog", About)
    MainWindow.show_about(stand_in())
    (about,) = dialogs
    assert about.props["website"] == "https://openexpress.bojanowski.me"
    assert about.props["issue_url"] == "https://github.com/TomaszBojanowski/openExpress/issues"
    assert about.links == [("Source Code", "https://github.com/TomaszBojanowski/openExpress"),
                           ("Support the Project", "https://buymeacoffee.com/sojer2005")]
