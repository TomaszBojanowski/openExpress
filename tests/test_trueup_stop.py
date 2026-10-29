"""The stop of the wait for TrueUp: a stop asked during a sync reaches the wait, also a quit
asked before the background service kept its session, and the window clears a stop left from
its previous sync when a sync starts (the service passes the stop on as it is:
test_trueup_wait.py). All data is made up."""

import threading
import types

from fakes import RunNow

from openexpress import service


def _session(stopped: bool):
    s = types.SimpleNamespace(
        device=types.SimpleNamespace(unit_id=1234, description="Test watch"),
        storage=None, client=None, mass_storage=False, logged_in=True, ownership_error=None,
        first_sync=False, stop_waiting=threading.Event())
    if stopped:
        s.stop_waiting.set()
    return s


def test_the_window_clears_a_stop_left_from_its_previous_sync(monkeypatch):
    from openexpress.gui import app

    seen = []

    def run_sync(s, first_run, progress):
        seen.append(s.stop_waiting.is_set())
        return service.SyncSummary()

    monkeypatch.setattr(service, "run_sync", run_sync)
    win = types.SimpleNamespace(
        session=_session(stopped=True),  # Stop Waiting pressed in the sync before
        run_task=RunNow(), progress=lambda *a: None, toast=lambda text: None,
        _show_last_sync=lambda: None, _count_activities_again=lambda: None)
    app.MainWindow.start_sync(win)
    assert seen == [False]


def test_a_quit_asked_before_the_background_service_kept_its_session(monkeypatch):
    from openexpress import watch

    w = watch.Watcher()
    w.running = True  # its sync has started (start_sync)
    released, seen = [], []
    monkeypatch.setattr(w, "release", lambda: released.append(True))
    s = _session(stopped=False)

    def open_session():
        w.request_quit()  # the session is still being opened
        return s

    def run_sync(session, progress):
        seen.append(session.stop_waiting.is_set())
        return service.SyncSummary()

    monkeypatch.setattr(w, "_open_session", open_session)
    monkeypatch.setattr(watch.service, "require_account_device", lambda client, unit_id: [])
    monkeypatch.setattr(watch.service, "run_sync", run_sync)
    monkeypatch.setattr(watch.GLib, "idle_add", lambda fn, *args: None)
    w._sync()
    assert seen == [True]
    assert released == []  # released after the sync, by _worker
