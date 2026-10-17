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
    assert ("New files uploaded: 2 · already in Connect: 0 · rejected by Garmin: 0 · upload "
            "errors: 0") in service.describe_lines(data["lines"])  # the window's own words


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
    assert MainWindow._apps_space(state) == "2 free slots · 2.0 kB free"


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
        self.visible, self.text, self.icon = None, None, None

    def set_visible(self, visible):
        self.visible = visible

    def set_from_icon_name(self, name):  # the device view's icon of the kind (T5.2)
        self.icon = name

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
    """GTK 4; the tests that use it are marked needs_display (skipped without a display)."""
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk

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


def _text(buffer) -> str:
    return buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False)


@pytest.mark.needs_display
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
        assert _text(buffer) == "### Device\n" and not switch.get_active()  # off by default
        switch.set_active(True)
        assert _text(buffer) == (
            "### Device\n\n### Log (last 1 lines)\n```\na line of the log\n```\n")
        switch.set_active(False)
        assert _text(buffer) == "### Device\n"

        alive = [weakref.ref(switch), weakref.ref(view), weakref.ref(box)]
        del box, buffer, switch, view
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


@pytest.mark.needs_display
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
        _set_update_state=lambda new, staged=(): states.append(len(new)), confirm_update=lambda u: None,
        load_section=lambda *a, **kw: None)
    win._update_row = types.MethodType(MainWindow._update_row, win)
    win._updates_status = types.MethodType(MainWindow._updates_status, win)
    win._button = types.MethodType(MainWindow._button, win)
    MainWindow.show_updates(win, items)

    status, available, advanced = win.sections["updates"].groups  # the status on top
    assert states == [3] and win.updates_cache == items
    assert available.title == "Available updates"
    assert [(r.get_title(), r.get_subtitle()) for r in available.rows] == [
        # The server only repeats the type: a readable name, the file name tells them apart.
        ("Map support file", "2.70 · 2.0 kB · Recommended · 006-D0001-00.sid"),
        ("Voice &lt;pack&gt;", "2.40 · 2.0 kB · Critical · 006-D0002-00.sid"),
        ("Thing", "2.60 · 2.0 kB · Optional · 006-D0003-00.sid")]  # unknown values stay raw
    assert advanced.title == "Advanced" and isinstance(advanced.rows[0], Adw.ExpanderRow)
    # The file that is already on the device is listed with the reinstalls, not as an update.
    assert advanced.rows[0].get_subtitle().startswith("2 components already on the device")
    # Three "Install", two "Reinstall" and "Check Again" in the status.
    assert len(win._action_buttons) == 6


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
        # GUPDATE.GCD: a system update, although the server sets neither flag.
        "  006-B0000-00          2.70  Critical           2 B  Test watch System Software [PRIMARY]",
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
                                _set_update_state=lambda new, staged=(): states.append(new),
                                _render_update_state=lambda: None,
                                in_background=lambda work, done, failed: done(work()))
    MainWindow._check_updates_quietly(win)
    assert states == [[]]  # "Software is up to date", no "update available"
    assert upd.pending(upd.mark_present(s.storage, upd.parse_updates(answer))) == []


# --- the picture of a device the account cannot give -------------------------------------------

PNG = b"\x89PNG\r\n\x1a\n" + b"picture bytes"
PICTURE_URL = "https://res.garmin.com/en/products/010-00000-00/v/cf-lg-1234.png"
SECRET_UNIT, SECRET_SERIAL = 3987654321, "7ZQ123456"


class PictureResponse:
    def __init__(self, status=200, kind="image/png", body=PNG, length=None, error=None):
        self.status_code, self.body, self.error = status, body, error
        self.headers = {"Content-Type": kind}
        if length is not None:
            self.headers["Content-Length"] = str(length)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def iter_content(self, size):
        if self.error is not None:
            raise self.error
        for i in range(0, len(self.body), size):
            yield self.body[i:i + size]


class PictureHttp:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.calls = response or PictureResponse(), error, []

    def get(self, url, **kw):
        self.calls.append((url, kw))
        if self.error is not None:
            raise self.error
        return self.response


class PictureClient:
    """Stands in for GarminClient: the account answer, the unit-info answer, the downloads."""

    def __init__(self, account_url=None, unit_info=None, http=None):
        self.account_url, self.info, self.http = account_url, unit_info, http or PictureHttp()
        self.asked = []

    def device_image_url(self, unit_id):
        self.asked.append("account")
        if isinstance(self.account_url, Exception):
            raise self.account_url
        return self.account_url

    def unit_info(self, unit_id, part_number):
        self.asked.append("unit-info")
        if isinstance(self.info, Exception):
            raise self.info
        return self.info


def _unit_info(url=PICTURE_URL):
    return {"unitid": SECRET_UNIT, "serialnumber": SECRET_SERIAL, "devicemodel": "Test watch",
            "graphicurl": url}


def _picture_window(client, logged_in=False, ownership_error=None, monkeypatch=None):
    """A window that shows a picture as a (file, size) pair instead of a GTK texture."""
    from openexpress.gui import app
    from openexpress.gui.app import MainWindow

    if monkeypatch is not None:
        monkeypatch.setattr(app, "load_texture",
                            lambda path, size: ("texture", path.read_bytes())
                            if path.read_bytes().startswith(PNG[:8]) else None)
    device = types.SimpleNamespace(unit_id=SECRET_UNIT, part_number="006-B0000-00")
    win = types.SimpleNamespace(
        session=types.SimpleNamespace(device=device, client=client, logged_in=logged_in,
                                      ownership_error=ownership_error),
        device_image=_Widget(), device_picture=_Widget(), shown=[], jobs=[])
    win._set_device_texture = win.shown.append
    win.in_background = lambda work, done, on_error=None: win.jobs.append((work, done))
    win.show = lambda: MainWindow._show_device_picture(win)
    return win


def _finish(win):
    work, done = win.jobs.pop()
    done(work())


def _cache(xdg):
    folder = xdg / "cache" / "openexpress" / "images"
    return sorted(p.name for p in folder.iterdir()) if folder.is_dir() else []


@pytest.fixture
def pictures(xdg, monkeypatch, log_file):
    from openexpress import applog

    monkeypatch.setenv("XDG_CACHE_HOME", str(xdg / "cache"))
    monkeypatch.setenv("OPENEXPRESS_LOG", "debug")
    applog.setup("gui")
    return xdg, log_file, monkeypatch


def test_device_outside_the_account_gets_its_picture_from_unit_info(pictures):
    xdg, log_file, monkeypatch = pictures
    client = PictureClient(unit_info=_unit_info())
    win = _picture_window(client, logged_in=True, ownership_error="not yours",
                          monkeypatch=monkeypatch)
    win.show()
    assert win.shown == [] and len(win.jobs) == 1  # only handed to the background: no waiting
    assert client.asked == [] and client.http.calls == []
    _finish(win)
    assert client.asked == ["unit-info"]  # the account is not asked about a foreign device
    url, options = client.http.calls[0]
    assert url == PICTURE_URL and options == {
        "timeout": 15, "stream": True, "allow_redirects": False}
    assert _cache(xdg) == ["device-006-B0000-00.png"] and win.shown == [("texture", PNG)]

    # The answer of unit-info went nowhere: not into the log, not into any file.
    log = log_file.read_text()
    assert "device picture: from unit-info" in log
    for secret in (str(SECRET_UNIT), SECRET_SERIAL, "unitids", "res.garmin.com", "cf-lg-1234"):
        assert secret not in log
    for path in xdg.rglob("*"):
        if path.is_file() and path.suffix != ".png":
            assert str(SECRET_UNIT) not in path.read_text(errors="replace")
            assert SECRET_SERIAL not in path.read_text(errors="replace")


def test_picture_in_the_cache_needs_no_request(pictures):
    xdg, log_file, monkeypatch = pictures
    folder = xdg / "cache" / "openexpress" / "images"
    folder.mkdir(parents=True)
    (folder / "device-006-B0000-00.png").write_bytes(PNG)
    client = PictureClient(unit_info=_unit_info())
    win = _picture_window(client, monkeypatch=monkeypatch)
    win.show()
    win.show()  # the second time from memory
    assert win.jobs == [] and client.asked == [] and client.http.calls == []
    assert win.shown == [("texture", PNG)] * 2
    assert log_file.read_text().count("device picture: from the cache") == 1


def test_account_is_the_first_source_and_unit_info_the_fallback(pictures, monkeypatch):
    xdg, log_file, monkeypatch = pictures
    from openexpress.gui import app

    monkeypatch.setattr(app, "cached_download", lambda http, url, name: _save(xdg, name))
    client = PictureClient(account_url="https://connect.example/picture.png",
                           unit_info=_unit_info())
    win = _picture_window(client, logged_in=True, monkeypatch=monkeypatch)
    win.show()
    _finish(win)
    assert client.asked == ["account"] and client.http.calls == []  # unit-info not needed
    assert "device picture: from the account" in log_file.read_text()

    for account_url in (None, RuntimeError("no network")):  # no picture there, or an error
        (xdg / "cache" / "openexpress" / "images" / "device-006-B0000-00.png").unlink()
        client = PictureClient(account_url=account_url, unit_info=_unit_info())
        win = _picture_window(client, logged_in=True, monkeypatch=monkeypatch)
        win.show()
        _finish(win)
        assert client.asked == ["account", "unit-info"] and win.shown == [("texture", PNG)]


def _save(xdg, name):
    path = xdg / "cache" / "openexpress" / "images" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(PNG)
    return path


def test_without_a_login_the_fallback_is_used_too(pictures):
    xdg, _log, monkeypatch = pictures
    client = PictureClient(unit_info=_unit_info())
    win = _picture_window(client, logged_in=False, monkeypatch=monkeypatch)
    win.show()
    _finish(win)
    assert client.asked == ["unit-info"] and win.shown == [("texture", PNG)]


@pytest.mark.parametrize("url, response, error, why", [
    ("https://example.org/x.png", None, None, "not res.garmin.com"),
    ("http://res.garmin.com/x.png", None, None, "not res.garmin.com"),
    ("https://res.garmin.com.example.org/x.png", None, None, "not res.garmin.com"),
    (PICTURE_URL, PictureResponse(status=302), None, "HTTP 302"),  # redirects are not followed
    (PICTURE_URL, PictureResponse(status=404), None, "HTTP 404"),
    (PICTURE_URL, PictureResponse(kind="text/html"), None, "not a PNG"),
    (PICTURE_URL, PictureResponse(kind="image/jpeg"), None, "not a PNG"),
    (PICTURE_URL, PictureResponse(body=b"<html>not a picture</html>"), None, "not a PNG"),
    (PICTURE_URL, PictureResponse(length=5_000_000), None, "too large"),
    (PICTURE_URL, PictureResponse(body=PNG + b"x" * (2 * 1024 * 1024)), None, "too large"),
    (PICTURE_URL, None, requests.ConnectionError("down"), "network error"),
    (PICTURE_URL, None, requests.ReadTimeout("slow"), "timeout"),
    (PICTURE_URL, PictureResponse(error=requests.ReadTimeout("slow")), None, "timeout"),
])
def test_anything_unexpected_leaves_the_generic_icon(pictures, url, response, error, why):
    xdg, log_file, monkeypatch = pictures
    client = PictureClient(unit_info=_unit_info(url), http=PictureHttp(response, error))
    win = _picture_window(client, monkeypatch=monkeypatch)
    win.show()
    _finish(win)  # no exception: the picture is optional
    assert win.shown == [] and _cache(xdg) == []  # nothing shown, nothing left in the cache
    assert win.device_image.visible is True and win.device_picture.visible is False
    assert f"device picture: none (download refused: {why})" in log_file.read_text()
    if not url.startswith("https://res.garmin.com/"):
        assert client.http.calls == []  # another host is not even asked


def test_a_download_that_takes_too_long_is_given_up(pictures):
    xdg, _log, _monkeypatch = pictures
    from openexpress.gui import app

    ticks = iter([0.0, 5.0, 20.0, 40.0])
    http = PictureHttp(PictureResponse(body=PNG + b"x" * 200_000))
    assert app.download_picture(http, PICTURE_URL, "device-x.png",
                                clock=lambda: next(ticks)) == (None, "timeout")
    assert _cache(xdg) == []


@pytest.mark.parametrize("info", [{}, {"graphicurl": ""}, {"graphicurl": None}, [], None,
                                  RuntimeError(f"HTTP 500: serial {SECRET_SERIAL}")])
def test_no_address_from_unit_info_leaves_the_icon(pictures, info):
    xdg, log_file, monkeypatch = pictures
    client = PictureClient(unit_info=info)
    win = _picture_window(client, monkeypatch=monkeypatch)
    win.show()
    _finish(win)
    assert win.shown == [] and client.http.calls == [] and _cache(xdg) == []
    log = log_file.read_text()
    assert "device picture: none (unit-info gave no address)" in log
    assert SECRET_SERIAL not in log  # an error that quotes the answer is not written down


def test_result_for_a_device_that_is_gone_is_not_shown(pictures):
    _xdg, _log, monkeypatch = pictures
    client = PictureClient(unit_info=_unit_info())
    win = _picture_window(client, monkeypatch=monkeypatch)
    win.show()
    win.session = None  # unplugged while the picture was on its way
    _finish(win)
    assert win.shown == []


def test_unit_info_request_keeps_the_device_out_of_the_log(log_file):
    from openexpress import applog
    from openexpress.connect import GarminClient
    from openexpress.settings import Settings

    applog.setup("gui")
    sent = []

    class Http:
        def request(self, method, url, **kw):
            sent.append(url)
            return types.SimpleNamespace(status_code=200, ok=True, content=b"{}",
                                         json=lambda: {"GraphicUrl": PICTURE_URL},
                                         headers={}, text="{}")

    client = GarminClient(Settings(), None, None)
    client.http = Http()
    assert client.unit_info(SECRET_UNIT, "006-B0000-00") == {"graphicurl": PICTURE_URL}
    assert sent[0].endswith(f"/unitids/{SECRET_UNIT}")  # the request itself is as before
    line = log_file.read_text().splitlines()[-1]
    assert "HTTP GET omt.garmin.com -> 200 in " in line
    assert "unitids" not in line and str(SECRET_UNIT) not in line and "<unit>" not in line


# --- "updates --install all" leaves out what the device already has -----------------------------


def _marked(root, answer):
    from openexpress import updates as upd
    from openexpress.device.storage import LocalStorage

    return upd.mark_present(LocalStorage(root), upd.parse_updates(answer))


def _with_metadata(answer, *parts):
    """Add the -META entry the server sends along with these parts."""
    for part in parts:
        answer["updates"] += _answer((part + "-META", "Update information", "UpdateMetadata",
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
    # With the reinstalls nothing is left out, as before.
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
    from openexpress import i18n
    from openexpress.i18n import ngettext

    i18n.setup("pl")
    try:
        said = [ngettext("Skipped {count} file that is already on the device.",
                         "Skipped {count} files that are already on the device.",
                         n).format(count=n) for n in (1, 3, 5)]
    finally:
        i18n.setup("en")
    assert said == ["Pominięto 1 plik, który już jest na urządzeniu.",
                    "Pominięto 3 pliki, które już są na urządzeniu.",
                    "Pominięto 5 plików, które już są na urządzeniu."]


# --- after an update is installed: what to do next ----------------------------------------------

NEXT_STEP = "choose to install now"


@pytest.fixture
def downloads(monkeypatch):
    """Downloads without the network: a file of the announced size."""
    from openexpress import download

    def fetch(urls, dest, size, md5, session=None, progress=None):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"x" * size)
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
    assert (root / "GARMIN/SID/DA00300.sid").read_bytes() == b"x" * 7
    lines = out.rstrip().splitlines()
    assert lines[lines.index("Copied 1 file."):] == [
        "Copied 1 file.",
        "",
        "To finish the installation:",
        "  1. Disconnect the device from the computer.",
        "  2. The device installs the update by itself. It may ask whether to install it:",
        "     choose to install now. It may also restart; wait until it is ready again.",
        "  3. Reconnect it and run `openexpress updates`: the installed update",
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

    text = MainWindow._update_done_text(InstallReport(installed=["Etyp 2.70 -> x"]))
    assert text.startswith("Files copied to the device.\nTo finish the installation:")
    assert NEXT_STEP in text and "check for updates again" in text
    assert "openexpress updates" not in text
    # Whether the server says a restart is needed makes no difference.
    assert MainWindow._update_done_text(
        InstallReport(installed=["x"], restart_required=True)) == text
    assert MainWindow._update_done_text(InstallReport()) == "Files copied to the device."


def test_next_steps_in_polish():
    from openexpress import i18n
    from openexpress.cli import FINISH_STEPS, FINISH_STEPS_WINDOW
    from openexpress.i18n import _

    i18n.setup("pl")
    try:
        cli_text, window_text = _(FINISH_STEPS), _(FINISH_STEPS_WINDOW)
    finally:
        i18n.setup("en")
    for text in (cli_text, window_text):
        assert "wybierz instalację teraz" in text and "Może też uruchomić się ponownie" in text
        assert text.startswith("\nAby dokończyć instalację:\n  1. ") and text.endswith(".\n")
    assert "`openexpress updates`" in cli_text and "openexpress" not in window_text
    assert "sprawdź aktualizacje jeszcze raz" in window_text


# --- the list of action buttons after a page is rebuilt -----------------------------------------


def page_window(key):
    """A page of the real kind in a window that is never shown, and a stand-in for the main
    window that loads it the way MainWindow does. A task runs at once, in the order run_task
    keeps: busy on, the work, busy off, then the page is rebuilt."""
    from openexpress.gui.app import Gtk, MainWindow, Section, still_in_window

    section = Section(key, key.title(), "x-symbolic", "Loading…")
    toplevel = Gtk.Window()
    toplevel.set_child(section.stack)

    def run_task(title, work, done, failed, device=True):
        win.set_busy(True)
        result = work()
        win.set_busy(False)
        done(result)

    def set_busy(busy):
        win.busy = busy
        win._action_buttons = still_in_window(win._action_buttons)

    win = types.SimpleNamespace(session=types.SimpleNamespace(), sections={key: section},
                                busy=False, _action_buttons=[], _retry_sources={},
                                run_task=run_task, set_busy=set_busy, toplevel=toplevel)
    for name in ("_load", "_button", "_trash"):
        setattr(win, name, types.MethodType(getattr(MainWindow, name), win))
    return win, section


@pytest.mark.needs_display
@pytest.mark.parametrize("key", ["maps", "updates", "apps"])
def test_a_rebuilt_page_leaves_no_removed_buttons_in_the_list(key):
    win, section = page_window(key)

    def show(result):
        group = section.group("On the device")
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


def test_cli_update_install_shows_a_bar_while_copying(offered, monkeypatch, capsys):
    from openexpress import updates as upd

    root, _answer_ = offered
    asked = {}

    def install(chosen, storage, session=None, progress=lambda s: None, transfer_progress=None):
        asked["bar"] = transfer_progress
        transfer_progress(1, 2)  # half of a file
        return upd.InstallReport(installed=["x"])

    monkeypatch.setattr(upd, "install", install)
    assert main(["--path", str(root), "updates", "--install", "006-DA003-00", "--apply"]) == 0
    assert isinstance(asked["bar"], cli._Bar)
    captured = capsys.readouterr()
    assert " 50%" in captured.out + captured.err  # the bar of the command line


def test_about_links_to_the_support_page(monkeypatch):
    """"About openExpress" links to the support page, beside the website and the issue
    tracker."""
    from openexpress.gui import app
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

    monkeypatch.setattr(app.Adw, "AboutDialog", About)
    MainWindow.show_about(types.SimpleNamespace())
    (about,) = dialogs
    assert about.props["website"] == "https://github.com/TomaszBojanowski/openExpress"
    assert about.props["issue_url"] == "https://github.com/TomaszBojanowski/openExpress/issues"
    assert about.links == [("Support the project", "https://buymeacoffee.com/sojer2005")]


# --- names from the server never lead out of the picture cache (review of 106cd25) ------------


@pytest.mark.parametrize("name", ["app-a/b.png", "app-../../x.png", "..", "", "/tmp/x.png"])
def test_a_picture_name_that_is_no_file_name_is_not_saved(xdg, name):
    """An icon is cached as app-<app ID>.png, the app ID as the Connect IQ server gives it:
    a name that is no plain file name is not saved (and not even asked for), the icon stays
    as it is; nothing is written outside the cache. The same for device pictures."""
    from openexpress.gui import app

    asked = []

    class Http:
        def get(self, url, **kw):
            asked.append(url)
            raise AssertionError("asked for a picture with a bad name")

    before = {p for p in Path(xdg).rglob("*") if p.is_file()}
    assert app.cached_download(Http(), "https://res.garmin.com/x.png", name) is None
    assert app.download_picture(Http(), "https://res.garmin.com/x.png", name) == (
        None, "not a file name")
    assert asked == []
    assert {p for p in Path(xdg).rglob("*") if p.is_file()} == before
