"""Tests the first frame of the start page in MainWindow itself, not a window of chosen methods,
from its own start (__init__ → _start → the detection → _show_tiles): it says what tile_facts
kept of the devices, before the account's list comes and anything is counted. The devices are
the four made-up ones of test_tile_facts."""

import types

import pytest
from test_forget_window import _turn_until
from test_tile_facts import ACCOUNT, FENIX, _run, _said, _window, four  # noqa: F401 - four: a fixture

LAST = {"fēnix 8": ["Update available", "Primary"], "Edge 1050": ["Synced"],
        "Venu 2": ["Not on your account"], "Varia RTL515": ["USB drive"]}


@pytest.mark.needs_display
def test_the_first_frame_of_the_window_says_what_was_kept_of_the_devices(four, monkeypatch,
                                                                         in_the_window):
    from openexpress import netstatus
    from openexpress.gui import app
    from openexpress.gui.detection import DeviceChoices
    from openexpress.gui.device_page import remember_update_found

    # An opening before, on a window of chosen methods (as in test_tile_facts): the account
    # came, the devices were counted, the fēnix's page found an update. What the tiles said
    # is kept with the devices (tile_facts).
    before = _window(four())
    before._show_tiles(*before._start_summaries())
    before._set_account(ACCOUNT)
    _run(before)
    fenix = next(s for s in before.connected.devices if s.unit_id == FENIX)
    remember_update_found(fenix.device, [types.SimpleNamespace(
        title="Map Region Grouping file", version="2.31", is_system=False)])
    before._render_start()
    assert dict(_said(before)) == LAST

    # The real window, from its own start: the same devices detected; the account's list and
    # the counts are held back, so that the first frame has only what was kept.
    detected, asked, counted, frames = [], [], [], []
    in_the_window("detect_devices", lambda current_uri=None: (
        detected.append(current_uri), DeviceChoices(four()))[1])
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
