"""Guard 3 of splitting gui/app.py (analysis 17): a test that replaces a name in gui.app alone
(monkeypatch.setattr(app, "name", …)) changes only what gui/app.py itself looks up. Once the
code that uses the name moves to another module of the window, the replacement does nothing
and the test checks something else, without failing; and a name the window's modules share
(Adw, Gtk, eject, …) may be read in a module other than app.py already. So no test replaces a
name in gui.app itself: it asks in_the_window (conftest, fakes.replace_in_window), which
replaces the name in every module of the window that holds it. Each name replaced so must be
read by a module of the window, and every module that reads it must hold it itself (an import
inside a function is out of reach). In the same way, the made-up clock (conftest.clock) must
stand in every module of gui/ that asks datetime.now(). Read from the sources only."""

import ast
from pathlib import Path

TESTS = Path(__file__).parent
GUI = TESTS.parent / "openexpress" / "gui"
WINDOW = "openexpress.gui.app"
WAYS = ("in_the_window", "replace_in_window")  # the fixture, the function for helpers


def _aliases(tree: ast.AST) -> set[str]:
    """The names a test file gives the window's module (app, gui_app, …), anywhere in it."""
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "openexpress.gui":
            out |= {a.asname or a.name for a in node.names if a.name == "app"}
        elif isinstance(node, ast.Import):
            out |= {a.asname for a in node.names if a.name == WINDOW and a.asname}
    return out


def _called(node: ast.Call) -> str | None:
    func = node.func
    return func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)


def direct_in_the_window(tree: ast.AST) -> list[str]:
    """The names a source replaces in gui.app itself: setattr(app, "name", …) with any alias
    of the module, setattr("openexpress.gui.app.name", …) and
    setattr(openexpress.gui.app, "name", …)."""
    aliases, found = _aliases(tree), []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and _called(node) == "setattr" and node.args):
            continue
        target = node.args[0]
        named = (len(node.args) > 1 and isinstance(node.args[1], ast.Constant)
                 and isinstance(node.args[1].value, str))
        if named and ((isinstance(target, ast.Name) and target.id in aliases)
                      or (isinstance(target, ast.Attribute) and ast.unparse(target) == WINDOW)):
            found.append(node.args[1].value)
        elif (isinstance(target, ast.Constant) and isinstance(target.value, str)
              and target.value.startswith(WINDOW + ".")):
            found.append(target.value[len(WINDOW) + 1:].split(".")[0])
    return found


def through_the_fixture(tree: ast.AST) -> list[str]:
    """The names a source replaces through in_the_window("name", …) or
    replace_in_window(monkeypatch, "name", …)."""
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _called(node) in WAYS:
            args = node.args[1:] if _called(node) == "replace_in_window" else node.args
            if args and isinstance(args[0], ast.Constant) and isinstance(args[0].value, str):
                found.append(args[0].value)
    return found


def _by_name(find) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for path in sorted(TESTS.glob("*.py")):
        for name in find(ast.parse(path.read_text(encoding="utf-8"))):
            found.setdefault(name, []).append(path.name)
    return found


def read_in(tree: ast.AST) -> set[str]:
    """The names a module reads (a name in code, not in an import)."""
    return {n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and isinstance(n.ctx,
                                                                                 ast.Load)}


def held_by(tree: ast.Module) -> set[str]:
    """The names a module binds itself, outside its functions and classes: imports, functions,
    classes and assignments, also under a top-level if, try or with."""
    out, todo = set(), list(tree.body)
    while todo:
        node = todo.pop()
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            out |= {(a.asname or a.name).split(".")[0] for a in node.names}
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            out |= {n.id for t in targets for n in ast.walk(t) if isinstance(n, ast.Name)}
        elif isinstance(node, (ast.If, ast.Try, ast.With)):
            todo += node.body + getattr(node, "orelse", []) + getattr(node, "finalbody", [])
            for handler in getattr(node, "handlers", []):
                todo += handler.body
    return out


def _window_modules() -> dict[str, ast.Module]:
    return {p.name: ast.parse(p.read_text(encoding="utf-8")) for p in sorted(GUI.glob("*.py"))}


def test_the_guard_sees_every_way_of_replacing_a_name():
    """The guard itself: the three forms of a direct replacement are seen, and the tests'
    replacements through the fixture are found (24 names on 27.10.2026)."""
    planted = ast.parse(
        "from openexpress.gui import app as gui_app\n"
        "import openexpress.gui.app\n"
        "def test_x(monkeypatch, in_the_window):\n"
        "    monkeypatch.setattr(gui_app, 'eject', None)\n"
        "    monkeypatch.setattr('openexpress.gui.app.Gtk.FileDialog', None)\n"
        "    monkeypatch.setattr(openexpress.gui.app, 'clear_cache', None)\n"
        "    in_the_window('Adw', None)\n"
        "    replace_in_window(monkeypatch, 'save_consumer', None)\n")
    assert direct_in_the_window(planted) == ["eject", "Gtk", "clear_cache"]
    assert through_the_fixture(planted) == ["Adw", "save_consumer"]
    found = _by_name(through_the_fixture)
    assert len(found) >= 20
    assert {"account_client", "datetime", "Adw", "Gtk", "eject", "save_consumer",
            "clear_cache"} <= set(found)


def test_no_test_replaces_a_name_in_the_window_s_module_itself():
    direct = _by_name(direct_in_the_window)
    assert direct == {}, (
        "replaced in gui.app alone (code moved out of app.py would not see it): use "
        f"in_the_window(name, value), or replace_in_window(monkeypatch, …) in a helper: {direct}")


def test_every_name_replaced_in_the_window_is_read_there():
    modules = _window_modules()
    read = set().union(*(read_in(tree) for tree in modules.values()))
    dead = {name: files for name, files in _by_name(through_the_fixture).items()
            if name not in read}
    assert dead == {}, f"replaced, but no module of the window reads it any more: {dead}"


def test_every_module_that_reads_a_replaced_name_holds_it_itself():
    """A module that reads a replaced name without holding it (an import inside a function,
    a name of a function's own) is out of reach of the replacement."""
    replaced = set(_by_name(through_the_fixture))
    out_of_reach = {
        module: sorted(names) for module, tree in _window_modules().items()
        if (names := (replaced & read_in(tree)) - held_by(tree))}
    assert out_of_reach == {}, out_of_reach


def _clock_modules() -> set[str]:
    """The modules whose datetime conftest.clock replaces."""
    tree = ast.parse((TESTS / "conftest.py").read_text(encoding="utf-8"))
    clock = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
                 and n.name == "clock")
    return {n.value for n in ast.walk(clock) if isinstance(n, ast.Constant)
            and isinstance(n.value, str) and n.value.startswith("openexpress.")}


def _asks_the_time(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
               and n.func.attr in ("now", "today") and isinstance(n.func.value, ast.Name)
               and n.func.value.id == "datetime" for n in ast.walk(tree))


def test_the_clock_stands_in_every_module_of_the_window_that_asks_the_time():
    asking = {f"openexpress.gui.{p.stem}" for p in GUI.glob("*.py") if _asks_the_time(p)}
    assert "openexpress.gui.app" in asking  # the guard itself sees them
    assert sorted(asking - _clock_modules()) == []
