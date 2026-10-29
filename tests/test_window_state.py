"""The window's state: every attribute its code reads or sets on the window is there as soon as
the window is built (MainWindow(None), the detection held back), whatever is shown later; and
the real window, opened from its own start with no device or with two, stays on the start page
with their tiles. All devices are made up."""

import ast
from pathlib import Path

import pytest
from test_forget_window import _turn_until
from test_tile_facts import EDGE, FENIX, _said, four  # noqa: F401 - four: a fixture
from test_window_build import window  # noqa: F401 - a fixture

pytestmark = pytest.mark.needs_display

GUI = Path(__file__).resolve().parent.parent / "openexpress" / "gui"


def _window_classes(trees):
    classes = {node.name: node for tree in trees for node in tree.body
               if isinstance(node, ast.ClassDef)}
    main = classes["MainWindow"]
    return [main] + [classes[b.id] for b in main.bases
                     if isinstance(b, ast.Name) and b.id in classes]


def named_on_the_window() -> set[str]:
    """The names the window's code reads or sets on the window: self.X in MainWindow and its
    mixins, win.X in the functions of gui/ that take the window first, and the names given
    to getattr and hasattr there."""
    trees = [ast.parse(p.read_text(encoding="utf-8")) for p in sorted(GUI.glob("*.py"))]
    scopes = [(c, "self") for c in _window_classes(trees)]
    scopes += [(f, "win") for tree in trees for f in tree.body
               if isinstance(f, ast.FunctionDef) and f.args.args and f.args.args[0].arg == "win"]
    names = set()
    for scope, var in scopes:
        for node in ast.walk(scope):
            if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                    and node.value.id == var):
                names.add(node.attr)
            elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                  and node.func.id in ("getattr", "hasattr") and len(node.args) >= 2
                  and isinstance(node.args[0], ast.Name) and node.args[0].id == var
                  and isinstance(node.args[1], ast.Constant)):
                names.add(node.args[1].value)
    return names


def test_the_names_are_found():
    names = named_on_the_window()
    assert {"session", "busy", "connected", "start", "sections", "map_row"} <= names


def test_every_attribute_the_window_names_is_there_once_it_is_built(window):
    missing = sorted(name for name in named_on_the_window()
                     if name not in vars(window) and not hasattr(type(window), name))
    assert missing == []


def test_the_stand_in_has_every_attribute_of_the_real_window(window):
    from fakes import window as stand_in

    assert sorted(vars(stand_in())) == sorted(vars(window))
    changed = stand_in(busy=True, session="a session")
    assert (changed.busy, changed.session, changed.connected.devices) == (True, "a session", [])


@pytest.mark.parametrize("units", [(), (FENIX, EDGE)], ids=["no-device", "two-devices"])
def test_the_real_window_from_its_own_start_stays_on_the_start_page(four, monkeypatch,
                                                                    in_the_window, units):
    from openexpress import netstatus
    from openexpress.gui import app
    from openexpress.gui.detection import DeviceChoices

    in_the_window("detect_devices", lambda current_uri=None: DeviceChoices(
        [s for s in four() if s.unit_id in units]))
    monkeypatch.setattr(app.MainWindow, "_fetch_account", lambda self: None)
    monkeypatch.setattr(app.MainWindow, "_count_activities", lambda self, entries: None)
    drawn = []
    real = app.MainWindow._show_tiles

    def show_tiles(self, *args, **kwargs):
        real(self, *args, **kwargs)
        drawn.append(1)

    monkeypatch.setattr(app.MainWindow, "_show_tiles", show_tiles)
    win = app.MainWindow(None)
    try:
        assert _turn_until(lambda: drawn)
        assert win.navigation.get_visible_page() is win.start.page and win.session is None
        connected = [name for item, (name, _chips) in zip(win.start.tiles(), _said(win))
                     if item.has_css_class("connected")]
        assert sorted(connected) == sorted({FENIX: "fēnix 8", EDGE: "Edge 1050"}[u]
                                           for u in units)
    finally:
        netstatus.STATUS.remove_listener(win._net_listener)
        win.destroy()
