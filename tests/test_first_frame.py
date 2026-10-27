"""Guard 5 of splitting gui/app.py (analysis 17): the first frame of the start page in the real
window. The tests of the first frame (test_tile_facts, test_sync_count_record,
test_sync_chips) use a window made of chosen methods and the real StartPage; here the window
is MainWindow itself, from its own start (__init__ → _start → the detection → _show_tiles).
Its first frame must say what tile_facts kept of the devices, before the account's list comes
and before anything is counted. The devices are the four made-up ones of test_tile_facts."""

import types

import pytest
from test_forget_window import _turn_until
from test_tile_facts import ACCOUNT, FENIX, _run, _said, _window, four  # noqa: F401 - four: a fixture

LAST = {"fēnix 8": ["Update available", "Primary"], "Edge 1050": ["Synced"],
        "Venu 2": ["Not on your account"], "Varia RTL515": ["USB drive"]}


@pytest.mark.needs_display
def test_the_first_frame_of_the_window_says_what_was_kept_of_the_devices(four, monkeypatch):
    from openexpress import netstatus
    from openexpress.gui import app

    # An opening before, on a window of chosen methods (as in test_tile_facts): the account
    # came, the devices were counted, the fēnix's page found an update. What the tiles said
    # is kept with the devices (tile_facts).
    before = _window(four())
    before._show_tiles(*before._start_summaries())
    before._set_account(ACCOUNT)
    _run(before)
    fenix = next(s for s in before.connected.devices if s.unit_id == FENIX)
    app.remember_update_found(fenix.device, [types.SimpleNamespace(
        title="Map Region Grouping file", version="2.31", is_system=False)])
    before._render_start()
    assert dict(_said(before)) == LAST

    # The real window, from its own start: the same devices detected; the account's list and
    # the counts are held back, so that the first frame has only what was kept.
    detected, asked, counted, frames = [], [], [], []
    monkeypatch.setattr(app, "detect_devices", lambda current_uri=None: (
        detected.append(current_uri), app.DeviceChoices(four()))[1])
    monkeypatch.setattr(app.MainWindow, "_fetch_account", lambda self: asked.append(1))
    monkeypatch.setattr(app.MainWindow, "_count_activities",
                        lambda self, entries: counted.append(len(entries)))
    real = app.MainWindow._show_tiles

    def drawn(self, *args, **kwargs):
        real(self, *args, **kwargs)
        frames.append((_said(self), self._account))

    monkeypatch.setattr(app.MainWindow, "_show_tiles", drawn)
    win = app.MainWindow(None)
    try:
        assert _turn_until(lambda: frames)
        first, account = frames[0]
        assert detected == [None]  # the window's own detection, once, nothing open
        assert account is None  # before the account's list
        assert dict(first) == LAST
        assert first == _said(before)  # in the same order too
        assert win.navigation.get_visible_page() is win.start.page
    finally:
        netstatus.STATUS.remove_listener(win._net_listener)
        win.destroy()
