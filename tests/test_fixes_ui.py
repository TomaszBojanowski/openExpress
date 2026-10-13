import contextlib
import sys
import types
from pathlib import Path

import pytest
import requests

from openexpress import cli, desktop, service
from openexpress.cli import main
from openexpress.connect import UploadResult

UPLOAD_CONFIG = {
    "unittypename": "test",
    "datatypes": [
        {"datatypename": "FIT_TYPE_4", "uploadlocations": [
            {"url": "https://up.example/activity", "requiresauthentication": True}]},
    ],
}


class FakeClient:
    def __init__(self, status=201, messages=None, fail_config=None):
        self.status = status
        self.messages = messages or []
        self.fail_config = fail_config
        self.uploads = []
        self.auth = object()
        self.verified_unit_id = None

    def account_devices(self):
        return [{"unitid": 1234567890}]

    def require_verified(self, unit_id=None):
        assert self.verified_unit_id is not None

    # The account allows uploads, as a normal account does.
    def consent(self, consent_type):
        return {"useroption": "opt-in", "sourcetype": "CUSTOMER",
                "consentenforcementstate": "HARD", "consenttypeid": consent_type}

    def device_sync_preference(self):
        return {"key": "account.deviceSync", "value": "true"}

    def profile_country(self):
        return "PL"

    def upload_settings(self, unit_id):
        if self.fail_config:
            raise self.fail_config
        return UPLOAD_CONFIG

    def device_messages(self, unit_id):
        return self.messages

    def activity_matches(self, unit_id, ids):
        return {i: False for i in ids}

    def upload_file(self, url, data, *, is_zip, last_file, authenticated):
        self.uploads.append(url)
        return UploadResult(self.status, {}, None, None)

    def poll_location(self, url, delay):
        return 201

    def download_message(self, url, absolute):
        return None

    def ack_message(self, mid):
        pass


@pytest.fixture
def fake_client(monkeypatch):
    holder = {"client": FakeClient()}
    monkeypatch.setattr(cli, "make_client", lambda settings, need_auth: holder["client"])
    monkeypatch.setattr(cli, "GarminClient", lambda *a, **k: holder["client"])
    return holder


def _state_file(device_root):
    from openexpress.sync import SyncState

    return SyncState(1234567890).file


# --- 10: inter-process lock --------------------------------------------------------------


@pytest.mark.parametrize("argv", [["sync", "--apply", "--all"], ["baseline", "--apply"]])
def test_apply_respects_sync_lock(device_root, xdg, fake_client, monkeypatch, capsys, argv):
    @contextlib.contextmanager
    def busy():
        raise service.SyncBusy("A sync is already running in another process.")
        yield  # pragma: no cover

    monkeypatch.setattr(service, "sync_lock", busy)
    assert main(["--path", str(device_root), *argv]) == 1
    assert "already running in another process" in capsys.readouterr().err
    assert fake_client["client"].uploads == []
    assert not _state_file(device_root).exists()


# --- 11: first-sync guard ----------------------------------------------------------------


def test_first_sync_guard_without_candidates(device_root, xdg, fake_client, capsys):
    # Only a type with nothing to upload: the guard must still stop the first sync.
    rc = main(["--path", str(device_root), "sync", "--apply", "--type", "FIT_TYPE_99"])
    assert rc == 2
    assert "baseline --apply" in capsys.readouterr().err
    assert not _state_file(device_root).exists()


def test_download_only_first_run_does_not_create_state(device_root, xdg, fake_client):
    assert main(["--path", str(device_root), "sync", "--apply", "--download-only"]) == 0
    assert not _state_file(device_root).exists()
    # The guard still applies afterwards.
    assert main(["--path", str(device_root), "sync", "--apply"]) == 2


def test_sync_all_uploads_and_records_last_sync(device_root, xdg, fake_client, capsys):
    assert main(["--path", str(device_root), "sync", "--apply", "--all"]) == 0
    assert _state_file(device_root).exists()
    assert len(fake_client["client"].uploads) == 2
    data = service.load_last_sync(1234567890)
    assert data["uploaded"] == 2 and data["errors"] == []
    assert any("Uploaded: 2" in line for line in data["lines"])


# --- 12: errors and exit codes -----------------------------------------------------------


def test_sync_upload_errors_give_exit_code_1(device_root, xdg, fake_client, capsys):
    fake_client["client"] = FakeClient(status=500)
    assert main(["--path", str(device_root), "sync", "--apply", "--all"]) == 1
    data = service.load_last_sync(1234567890)
    assert data["errors"]


def test_network_error_is_a_clean_user_error(device_root, xdg, fake_client, capsys):
    fake_client["client"] = FakeClient(fail_config=requests.ConnectionError("boom"))
    assert main(["--path", str(device_root), "sync"]) == 1
    err = capsys.readouterr().err
    assert "Network error" in err and "Traceback" not in err


# --- 14: setup --key/--secret ------------------------------------------------------------


@pytest.mark.parametrize("argv", [["setup", "--key", "abc"], ["setup", "--secret", "xyz"]])
def test_setup_key_requires_secret(xdg, capsys, argv):
    with pytest.raises(SystemExit) as e:
        main(argv)
    assert e.value.code == 2
    assert "--key and --secret" in capsys.readouterr().err


# --- 15: music remove matching -----------------------------------------------------------

PATHS = ["Music/Pink Floyd/The Wall/01 In the Flesh.mp3",
         "Music/Various Artists/Hits/02 Song.mp3",
         "Podcasts/Daily News/ep1.mp3",
         "Music/Road trip.m3u8"]
ROOTS = ["Music/", "Podcasts"]


def test_music_match_rules():
    m = cli.match_music_paths
    assert m(PATHS, ROOTS, ["floyd"]) == [PATHS[0]]
    assert m(PATHS, ROOTS, ["flesh"]) == [PATHS[0]]
    assert m(PATHS, ROOTS, ["daily news"]) == [PATHS[2]]
    assert m(PATHS, ROOTS, ["ROAD TRIP"]) == [PATHS[3]]
    assert m(PATHS, ROOTS, ["/music/various artists/hits/02 song.mp3"]) == [PATHS[1]]
    # The top media folder never matches.
    assert m(PATHS, ROOTS, ["music"]) == []
    assert m(PATHS, ROOTS, ["podcasts"]) == []
    assert m(PATHS, ROOTS, ["   "]) == []


@pytest.mark.parametrize("pattern", ["", "  ", "ab"])
def test_music_remove_rejects_short_patterns(device_root, xdg, capsys, pattern):
    assert main(["--path", str(device_root), "music", "remove", pattern, "--apply"]) == 1
    assert "at least 3 characters" in capsys.readouterr().err


# --- 16: desktop / systemd quoting -------------------------------------------------------


def test_desktop_exec_quote():
    q = desktop.desktop_exec_quote
    assert q("/usr/bin/python3") == "/usr/bin/python3"
    assert q("PYTHONPATH=/x/y") == "PYTHONPATH=/x/y"
    assert q("/a b/py") == '"/a b/py"'
    assert q("/a%b") == "/a%%b"
    # $ is escaped for the Exec quoting, then the backslash for the string escaping.
    assert q('/a "b" $c') == '"/a \\\\"b\\\\" \\\\$c"'


def test_files_quote_paths_with_spaces_and_percent(xdg, monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "executable", "/opt/my py%/bin/python3")
    monkeypatch.setattr(desktop, "_source_root", lambda: Path("/home/u/My Code/100%"))
    out = {p.name: text for p, text in desktop.files().items()}
    entry = out[f"{desktop.DESKTOP_ID}.desktop"]
    exec_line = next(line for line in entry.splitlines() if line.startswith("Exec="))
    assert exec_line == ('Exec=env "PYTHONPATH=/home/u/My Code/100%%" '
                         '"/opt/my py%%/bin/python3" -m openexpress gui')
    unit = out[desktop.SERVICE]
    assert 'Environment="PYTHONPATH=/home/u/My Code/100%%"' in unit
    assert 'ExecStart="/opt/my py%%/bin/python3" -m openexpress watch' in unit


def test_files_without_source_root(xdg, monkeypatch):
    monkeypatch.setattr(desktop, "_source_root", lambda: None)
    unit = {p.name: t for p, t in desktop.files().items()}[desktop.SERVICE]
    assert "Environment=" not in unit


# --- 17: uninstall -----------------------------------------------------------------------


def test_uninstall_disables_service_and_removes_wants_link(xdg, monkeypatch, tmp_path):
    import shutil
    import subprocess

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    calls = []
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: calls.append(cmd)
                        or types.SimpleNamespace(returncode=0, stdout="", stderr=""))
    written, _removed = desktop.install(dry_run=False)
    unit = next(p for p in written if p.name == desktop.SERVICE)
    wants = unit.parent / "graphical-session.target.wants" / desktop.SERVICE
    wants.parent.mkdir(parents=True)
    wants.symlink_to(unit)

    planned = desktop.uninstall(dry_run=True)
    assert wants in planned and calls == []
    assert all(p.exists() for p in written)

    removed = desktop.uninstall(dry_run=False)
    assert ["systemctl", "--user", "disable", "--now", desktop.SERVICE] in calls
    assert wants in removed and not wants.is_symlink()
    assert not any(p.exists() for p in written)


def test_uninstall_without_systemctl(xdg, monkeypatch, tmp_path):
    import shutil
    import subprocess

    monkeypatch.setattr(shutil, "which", lambda name: None)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("must not run"))
    written, _ = desktop.install(dry_run=False)
    desktop.uninstall(dry_run=False)
    assert not any(p.exists() for p in written)


def test_cli_remove_message(xdg, monkeypatch, capsys, tmp_path):
    from openexpress import background

    monkeypatch.setattr(background, "in_flatpak", lambda: False)
    monkeypatch.setattr(desktop, "uninstall",
                        lambda dry_run: [tmp_path / desktop.SERVICE])
    assert main(["install-desktop", "--remove", "--apply"]) == 0
    assert "stopped and disabled" in capsys.readouterr().out


# --- GUI logic without a display ---------------------------------------------------------


def test_gui_helpers():
    from openexpress.gui.app import MainWindow

    assert MainWindow._removed_files(1) == "Removed 1 file"
    assert MainWindow._removed_files(3) == "Removed 3 files"
    state = types.SimpleNamespace(available_slots=2, available_space=2048)
    assert MainWindow._apps_space(state) == "2 free slots · 2.0 KB free"


class _Sec:
    def __init__(self):
        self.shown, self.errors, self.loaded = [], [], False

    def show(self, name):
        self.shown.append(name)

    def show_error(self, text):
        self.errors.append(text)

    def clear(self):
        pass


def _fake_window(session):
    from openexpress.gui.app import MainWindow

    win = types.SimpleNamespace(session=session, busy=False, sections={"maps": _Sec()},
                                _retry_sources={}, tasks=[])
    win.run_task = lambda title, work, done, failed, device=True: win.tasks.append((done, failed))
    win._load = types.MethodType(MainWindow._load, win)
    return win


def test_section_load_ignores_results_for_a_disconnected_watch():
    shown = []
    win = _fake_window(session="watch-1")
    win._load("maps", "t", lambda: 1, shown.append)
    done, failed = win.tasks[0]
    win.session = None  # unplugged during the load
    done(["map"])
    assert shown == [] and not win.sections["maps"].loaded
    assert failed(RuntimeError("x")) is True and win.sections["maps"].errors == []
    win.session = "watch-2"  # replugged: a result for the old watch is still ignored
    done(["map"])
    assert shown == []


def test_section_load_keeps_one_retry(monkeypatch):
    from gi.repository import GLib

    added, removed = [], []
    monkeypatch.setattr(GLib, "timeout_add", lambda ms, cb: added.append(cb) or len(added))
    monkeypatch.setattr(GLib, "source_remove", lambda sid: removed.append(sid))
    win = _fake_window(session="watch-1")
    win.busy = True
    win._load("maps", "t", lambda: 1, lambda r: None)
    win._load("maps", "t", lambda: 1, lambda r: None)
    assert removed == [1] and win._retry_sources == {"maps": 2}
    win.session = None
    assert added[-1]() is False and win.tasks == []


def test_pending_refresh_runs_when_idle():
    from openexpress.gui.app import MainWindow

    calls = []
    win = types.SimpleNamespace(_refresh_pending=True, busy=True,
                                refresh_device=lambda: calls.append(1))
    MainWindow._run_pending_refresh(win)
    assert calls == [] and win._refresh_pending
    win.busy = False
    MainWindow._run_pending_refresh(win)
    assert calls == [1] and not win._refresh_pending


def test_window_geometry(xdg):
    from openexpress.gui.app import (
        DEFAULT_WINDOW,
        load_gui_settings,
        save_gui_settings,
        save_window_geometry,
        window_geometry,
    )

    assert window_geometry({}) == (*DEFAULT_WINDOW, False)
    assert window_geometry({"window": "bad"}) == (*DEFAULT_WINDOW, False)
    assert window_geometry({"window": {"width": 1400, "height": 900, "maximized": True}}) == \
        (1400, 900, True)
    # Too small, too large, wrong types, "maximized" must be a real boolean
    assert window_geometry({"window": {"width": 10, "height": 99999, "maximized": 1}}) == \
        (360, 10000, False)
    assert window_geometry({"window": {"width": True, "height": "800"}}) == (*DEFAULT_WINDOW, False)

    save_gui_settings({"background": True})
    save_window_geometry(1200, 800, False)
    data = load_gui_settings()
    assert data["background"] is True  # other options are kept
    assert window_geometry(data) == (1200, 800, False)


# --- "Do not unplug the device" while a task works with the device ----------------------------


class _Widget:
    def __init__(self):
        self.visible, self.text = None, None

    def set_visible(self, visible):
        self.visible = visible

    def set_text(self, text):
        self.text = text

    def set_revealed(self, revealed):
        self.visible = revealed

    def set_fraction(self, fraction):
        pass

    def set_sensitive(self, sensitive):
        pass


def _busy_window(session):
    from openexpress.gui.app import MainWindow

    win = types.SimpleNamespace(
        session=session, busy=False, action_bar=_Widget(), progress_label=_Widget(),
        progress_bar=_Widget(), unplug_label=_Widget(), banner=_Widget(), _action_buttons=[],
        _update_actions=lambda: None, _run_pending_refresh=lambda: None, toasts=[], shown=[])
    win.toast = win.toasts.append
    win.set_busy = types.MethodType(MainWindow.set_busy, win)
    win.run_task = types.MethodType(MainWindow.run_task, win)
    return win


def _run_now(monkeypatch):
    """Tasks run at once instead of in a thread; what the label showed meanwhile is kept."""
    import threading

    from gi.repository import GLib

    class Now:
        def __init__(self, target, daemon=None):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr(threading, "Thread", Now)
    monkeypatch.setattr(GLib, "idle_add", lambda fn, *args: fn(*args))


def test_window_says_not_to_unplug_while_a_task_works_with_the_device(monkeypatch):
    _run_now(monkeypatch)
    win = _busy_window(session="watch")
    during = []
    win.run_task("Syncing…", lambda: during.append((win.busy, win.unplug_label.visible,
                                                    win.action_bar.visible)))
    assert during == [(True, True, True)]  # shown for as long as the task runs
    assert (win.busy, win.unplug_label.visible, win.action_bar.visible) == (False, False, False)


def test_warning_goes_away_after_a_failed_task_too(monkeypatch):
    _run_now(monkeypatch)
    win = _busy_window(session="watch")
    seen = []

    def fail():
        seen.append(win.unplug_label.visible)
        raise RuntimeError("the device went away")

    win.run_task("Copying music…", fail, on_error=lambda e: True)
    assert seen == [True] and win.unplug_label.visible is False and win.busy is False


@pytest.mark.parametrize("session, device", [("watch", False), (None, True)])
def test_no_warning_for_a_task_that_does_not_touch_a_device(monkeypatch, session, device):
    _run_now(monkeypatch)
    win = _busy_window(session=session)  # e.g. logging in, or no device is open
    during = []
    win.run_task("Logging in…", lambda: during.append(win.unplug_label.visible), device=device)
    assert during == [False] and win.action_bar.visible is False


@pytest.mark.parametrize("argv, expected", [
    (["sync", "--apply"], True), (["backup", "--apply"], True),
    (["apps", "install", "X", "--apply"], True), (["music", "add", "x.mp3", "--apply"], True),
    (["maps", "update", "--apply"], True), (["updates", "--install", "P", "--apply"], True),
    (["sync"], False), (["apps", "install", "X"], False),      # dry runs
    (["primary", "set", "--apply"], False), (["info"], False),  # account only / read in a moment
])
def test_cli_says_not_to_unplug_only_when_it_really_works_on_the_device(monkeypatch, capsys,
                                                                       argv, expected):
    from openexpress import cli
    from openexpress.device import gio_storage

    monkeypatch.setattr(gio_storage, "open_device_storage",
                        lambda uri=None, wait=False, notify=None: "storage")
    args = cli.build_parser().parse_args(argv)
    assert cli.open_storage(args) == "storage"
    out = capsys.readouterr()
    line = "Do not unplug the device until the operation is finished.\n"
    assert out.out == "" and out.err == (line if expected else "")


def test_cli_does_not_warn_for_a_folder_instead_of_a_device(tmp_path, capsys):
    from openexpress import cli

    args = cli.build_parser().parse_args(["--path", str(tmp_path), "sync", "--apply"])
    cli.open_storage(args)
    assert capsys.readouterr().err == ""


# --- widgets are destroyed on the GTK thread ------------------------------------------------


def _gtk():
    """GTK with a display, or the test is skipped (e.g. on a machine without a session)."""
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk

    if not Gtk.init_check():
        pytest.skip("no display")
    return Gtk


def _find(widget, kind):
    if isinstance(widget, kind):
        return widget
    child = widget.get_first_child()
    while child is not None:
        found = _find(child, kind)
        if found is not None:
            return found
        child = child.get_next_sibling()
    return None


def test_report_preview_does_not_keep_itself_alive(log_file):
    """In 0.4.1 the "attach the log" switch referred to itself through its own handler, so
    the preview outlived the dialog until Python next collected reference cycles."""
    import gc
    import weakref

    from openexpress.gui.app import MainWindow

    Gtk = _gtk()
    log_file.parent.mkdir(parents=True)
    log_file.write_text("a line of the log\n")
    gc.collect()
    gc.disable()
    try:
        box, buffer = MainWindow._report_view("### Device\n")
        switch, view = _find(box, Gtk.CheckButton), _find(box, Gtk.TextView)
        text = lambda: buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False)  # noqa: E731
        assert text() == "### Device\n" and not switch.get_active()  # off by default
        switch.set_active(True)
        assert text() == "### Device\n\n### Log (last 1 lines)\n```\na line of the log\n```\n"
        switch.set_active(False)
        assert text() == "### Device\n"

        alive = [weakref.ref(switch), weakref.ref(view), weakref.ref(box)]
        del box, buffer, switch, view, text
        # Freed at once, by reference counting alone: nothing is left for a later collection.
        assert [ref() for ref in alive] == [None, None, None]
    finally:
        gc.enable()


def test_window_collects_cycles_itself_on_the_gtk_thread(monkeypatch):
    import gc
    import threading

    from gi.repository import GLib

    from openexpress.gui import app

    calls = []
    monkeypatch.setattr(gc, "disable", lambda: calls.append("automatic collection off"))
    monkeypatch.setattr(GLib, "timeout_add_seconds", lambda s, fn: calls.append((s, fn)))
    app.collect_garbage_on_the_gtk_thread()
    assert calls == ["automatic collection off", (app.GC_INTERVAL_S, app.collect_garbage)]
    assert app.collect_garbage() is True  # the timer keeps running

    # After every task, successful or not, in the thread that ends it (the GTK thread).
    _run_now(monkeypatch)
    where = []
    monkeypatch.setattr(gc, "collect", lambda: where.append(threading.current_thread()))
    win = _busy_window(session="watch")
    win.run_task("Syncing…", lambda: None)
    win.run_task("Syncing…", lambda: 1 / 0, on_error=lambda e: True)
    assert where == [threading.main_thread()] * 2


def test_updates_page_with_several_updates(monkeypatch):
    """The page as it was when the window crashed: three updates, one of them with a list of
    changes, and a component that can be reinstalled."""
    from gi.repository import Adw

    from openexpress.gui.app import MainWindow
    from openexpress.updates import SoftwareUpdate

    _gtk()

    def update(part, name, version, reinstall=False, changes=(), datatype="Firmware",
               severity="Recommended"):
        return SoftwareUpdate(part, name, version, severity, False, reinstall, False,
                              f"Garmin/SID/{part}.sid", "https://example.org/x", None, 2048, 0,
                              changes=list(changes), datatype=datatype)

    items = [update("006-D0001-00", "Etyp", "2.70", changes=["Better <maps> & more", "Fixes"],
                    datatype="Etyp"),
             update("006-D0002-00", "Voice <pack>", "2.40", severity="Critical"),
             update("006-D0003-00", "Thing", "2.60", datatype="NewKind", severity="Optional"),
             update("006-D0004-00", "Sensor Hub", "9.10", reinstall=True),
             update("006-D0005-META", "Metadata", "1.00"),
             update("006-D0006-00", "Etyp", "1.10", datatype="Etyp")]
    items[-1].on_device = True  # offered by the server, but the device has this very file

    class Group:
        def __init__(self, title):
            self.title, self.rows = title, []

        def add(self, row):
            self.rows.append(row)

    class Section:
        groups = []

        def group(self, title="", description=""):
            self.groups.append(Group(title))
            return self.groups[-1]

    states = []
    win = types.SimpleNamespace(
        session=types.SimpleNamespace(device=types.SimpleNamespace(
            description="Test watch", software_version_str="23.39")),
        sections={"updates": Section()}, busy=False, _action_buttons=[],
        _set_update_state=lambda new: states.append(len(new)), confirm_update=lambda u: None,
        load_section=lambda *a, **kw: None)
    win._update_row = types.MethodType(MainWindow._update_row, win)
    win._button = types.MethodType(MainWindow._button, win)
    MainWindow.show_updates(win, items)

    available, advanced = win.sections["updates"].groups
    assert states == [3] and win.updates_cache == items
    assert available.title == "Available updates"
    assert [(r.get_title(), r.get_subtitle()) for r in available.rows] == [
        # The server only repeats the type: a readable name, the file name tells them apart.
        ("Map support file", "2.70 · 2.0 KB · Recommended · 006-D0001-00.sid"),
        ("Voice &lt;pack&gt;", "2.40 · 2.0 KB · Critical · 006-D0002-00.sid"),
        ("Thing", "2.60 · 2.0 KB · Optional · 006-D0003-00.sid")]  # unknown values stay raw
    assert advanced.title == "Advanced" and isinstance(advanced.rows[0], Adw.ExpanderRow)
    # The file that is already on the device is listed with the reinstalls, not as an update.
    assert advanced.rows[0].get_subtitle().startswith("2 components already on the device")
    assert len(win._action_buttons) == 5  # three "Install" and two "Reinstall"


# --- files the device already has are not updates ---------------------------------------------


def _answer(*entries):
    """An answer of the update server; `entries`: (part, name, datatype, file, data, severity)."""
    import hashlib

    return {"updates": [{
        "partnumber": part, "productname": name, "datatype": datatype, "version": "2.70",
        "severity": severity,
        "installation": {"path": "Garmin\\SID", "filename": file, "isreinstall": False},
        "deliverable": {"downloadurl": "https://example.org/" + file,
                        "md5": hashlib.md5(data).hexdigest().upper(), "sizeinbytes": len(data)},
    } for part, name, datatype, file, data, severity in entries]}


@pytest.fixture
def offered(device_root, monkeypatch):
    """The server offers three map support files and a real update. The device has the first
    file as offered, the second with other content of the same size, and not the third."""
    from openexpress.connect import GarminClient

    (device_root / "GARMIN/SID").mkdir()
    (device_root / "GARMIN/SID/da00100.sid").write_bytes(b"same file")  # other letter case
    (device_root / "GARMIN/SID/DA00200.sid").write_bytes(b"other txt")
    answer = _answer(
        ("006-DA001-00", "Etyp", "Etyp", "DA00100.sid", b"same file", "Recommended"),
        ("006-DA002-00", "Etyp", "Etyp", "DA00200.sid", b"new bytes", "Recommended"),
        ("006-DA003-00", "Etyp", "Etyp", "DA00300.sid", b"missing", "Recommended"),
        ("006-B0000-00", "Test watch System Software", "Firmware", "GUPDATE.GCD", b"fw", "Critical"))
    monkeypatch.setattr(GarminClient, "software_updates",
                        lambda self, raw, include_reinstalls=False: answer)
    return device_root, answer


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


def test_nothing_can_be_said_without_a_size_or_an_md5(offered):
    from openexpress import updates as upd
    from openexpress.device.storage import DeviceError, LocalStorage

    root, answer = offered
    st = LocalStorage(root)
    u = upd.parse_updates(answer)[0]
    assert upd.is_on_device(st, u)
    for change in ({"md5": None}, {"md5": "not-an-md5"}, {"size": None}, {"size": 0}):
        other = upd.parse_updates(answer)[0]
        for name, value in change.items():
            setattr(other, name, value)
        assert not upd.is_on_device(st, other), change

    def broken(path):
        raise DeviceError("the device went away")

    st.read = broken
    assert not upd.is_on_device(st, u)  # not known to be there: offered as before


def test_readable_names_of_types_and_severities(offered):
    from openexpress import updates as upd

    _root, answer = offered
    etyp, _two, _three, firmware = upd.parse_updates(answer)
    assert (etyp.title, etyp.filename, etyp.severity_text) == (
        "Map support file", "DA00100.sid", "Recommended")
    assert firmware.title == "Test watch System Software"  # a real name is kept
    assert firmware.severity_text == "Critical"
    etyp.datatype = etyp.name = "SomethingNew"
    etyp.severity = "Optional"
    assert (etyp.title, etyp.severity_text) == ("SomethingNew", "Optional")  # unknown: as sent
    etyp.name, etyp.datatype = "", "CONFIG"
    assert etyp.title == "Configuration"

    from openexpress import i18n

    i18n.setup("pl")
    try:
        pl = upd.parse_updates(answer)
        assert (pl[0].title, pl[0].severity_text, pl[3].severity_text) == (
            "Plik dodatkowy mapy", "Zalecana", "Krytyczna")
    finally:
        i18n.setup("en")


def test_cli_updates_counts_only_real_updates(offered, capsys):
    root, _answer_ = offered
    assert main(["--path", str(root), "updates"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[1] == "Available updates: 3"
    assert out[2:5] == [
        "  006-B0000-00          2.70  Critical           2 B  Test watch System Software",
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
    win = types.SimpleNamespace(session=s, _update_check_session=None, update_state=None,
                                _set_update_state=states.append,
                                _render_update_state=lambda: None,
                                in_background=lambda work, done, failed: done(work()))
    MainWindow._check_updates_quietly(win)
    assert states == [[]]  # "Software is up to date", no "update available"
    assert upd.pending(upd.mark_present(s.storage, upd.parse_updates(answer))) == []
