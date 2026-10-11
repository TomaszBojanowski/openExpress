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
    win.run_task = lambda title, work, done, failed: win.tasks.append((done, failed))
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
    from openexpress.gui.app import (DEFAULT_WINDOW, load_gui_settings, save_gui_settings,
                                     save_window_geometry, window_geometry)

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
