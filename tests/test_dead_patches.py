"""Guard 3 of splitting gui/app.py (analysis 17): a test that replaces a name in gui.app alone
(monkeypatch.setattr(app, "name", …)) changes only what gui/app.py itself looks up. Once the
code that uses the name moves to another module of the window, the replacement does nothing
and the test checks something else, without failing; and a name the window's modules share
(Adw, Gtk, eject, …) may be read in a module other than app.py already. So no test replaces a
name in gui.app itself: it asks in_the_window (conftest, fakes.replace_in_window), which
replaces the name in every module of the window that holds it. Each name replaced so must be
read by a module of the window, and every module that reads it must hold it itself (an import
inside a function is out of reach). A name imported by several modules of the window is not
replaced in one of them alone either (the others would keep it), and no method a mixin calls
through its own class (TaskRunner.in_background(…)) is replaced on MainWindow (those calls
would not see it). In the same way, the made-up clock (conftest.clock) must stand in every
module of gui/ that asks datetime.now(). Read from the sources only."""

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


def _names_of(arg: ast.AST, loops: dict) -> list[str]:
    """The names an argument stands for: a string, or the variable of a loop over strings
    it is in (for name in ("a", "b"): …); a name made otherwise stands as its text in <>."""
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        return [arg.value]
    if id(arg) in loops:
        return loops[id(arg)]
    return [f"<{ast.unparse(arg)}>"]


def _loops(tree: ast.AST) -> dict[int, list[str]]:
    """Each use of the variable of a loop over a tuple or a list of strings, inside that
    loop, with those strings (by the id of the use)."""
    out = {}
    for node in ast.walk(tree):
        if (isinstance(node, ast.For) and isinstance(node.target, ast.Name)
                and isinstance(node.iter, (ast.Tuple, ast.List))
                and all(isinstance(e, ast.Constant) and isinstance(e.value, str)
                        for e in node.iter.elts)):
            strings = [e.value for e in node.iter.elts]
            for statement in node.body:
                for use in ast.walk(statement):
                    if isinstance(use, ast.Name) and use.id == node.target.id:
                        out[id(use)] = strings
    return out


def direct_in_the_window(tree: ast.AST) -> list[str]:
    """The names a source replaces in gui.app itself: setattr(app, "name", …) (or delattr)
    with any alias of the module, also with the name in a variable,
    setattr("openexpress.gui.app.name", …) and setattr(openexpress.gui.app, "name", …)."""
    aliases, found, loops = _aliases(tree), [], _loops(tree)
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and _called(node) in ("setattr", "delattr")
                and node.args):
            continue
        target = node.args[0]
        if (len(node.args) > 1
                and ((isinstance(target, ast.Name) and target.id in aliases)
                     or (isinstance(target, ast.Attribute) and ast.unparse(target) == WINDOW))):
            found += _names_of(node.args[1], loops)
        elif (isinstance(target, ast.Constant) and isinstance(target.value, str)
              and target.value.startswith(WINDOW + ".")):
            found.append(target.value[len(WINDOW) + 1:].split(".")[0])
    return found


def _gui_aliases(tree: ast.AST) -> dict[str, str]:
    """The names a test file gives the other modules of the window: alias → module (start,
    detection, …; gui.app is direct_in_the_window's)."""
    out = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "openexpress.gui":
            out |= {a.asname or a.name: a.name for a in node.names if a.name != "app"}
        elif isinstance(node, ast.Import):
            out |= {a.asname: a.name.rsplit(".", 1)[1] for a in node.names
                    if a.asname and a.name.startswith("openexpress.gui.") and a.name != WINDOW}
    return out


def in_one_module(tree: ast.AST) -> list[tuple[str, str]]:
    """The names a source replaces in another module of the window, as (module, name):
    setattr(start, "name", …) (or delattr) with any alias of the module, also with the name
    in the variable of a loop, and setattr("openexpress.gui.start.name", …)."""
    aliases, found, loops = _gui_aliases(tree), [], _loops(tree)
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and _called(node) in ("setattr", "delattr")
                and node.args):
            continue
        target = node.args[0]
        if isinstance(target, ast.Name) and target.id in aliases and len(node.args) > 1:
            found += [(aliases[target.id], name) for name in _names_of(node.args[1], loops)]
        elif (isinstance(target, ast.Constant) and isinstance(target.value, str)
              and target.value.startswith("openexpress.gui.")
              and not target.value.startswith(WINDOW + ".")):
            module, name = target.value[len("openexpress.gui."):].split(".")[:2]
            found.append((module, name))
    return found


def on_the_window_class(tree: ast.AST) -> list[str]:
    """The names a source replaces on the class MainWindow: setattr(MainWindow, "name", …)
    (or delattr), also as app.MainWindow and with the name in the variable of a loop."""
    found, loops = [], _loops(tree)
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and _called(node) in ("setattr", "delattr")
                and len(node.args) > 1
                and ast.unparse(node.args[0]).split(".")[-1] == "MainWindow"):
            found += _names_of(node.args[1], loops)
    return found


def through_the_fixture(tree: ast.AST) -> list[str]:
    """The names a source replaces through in_the_window("name", …) or
    replace_in_window(monkeypatch, "name", …)."""
    found, loops = [], _loops(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _called(node) in WAYS:
            args = node.args[1:] if _called(node) == "replace_in_window" else node.args
            if args:
                found += _names_of(args[0], loops)
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


def imported_by(tree: ast.Module) -> set[str]:
    """The names a module binds by an import of its own, outside its functions and classes:
    the same object as in the module it comes from (not a function or a logger of its own)."""
    out, todo = set(), list(tree.body)
    while todo:
        node = todo.pop()
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            out |= {(a.asname or a.name).split(".")[0] for a in node.names}
        elif isinstance(node, (ast.If, ast.Try, ast.With)):
            todo += node.body + getattr(node, "orelse", []) + getattr(node, "finalbody", [])
            for handler in getattr(node, "handlers", []):
                todo += handler.body
    return out


def _window_modules() -> dict[str, ast.Module]:
    return {p.name: ast.parse(p.read_text(encoding="utf-8")) for p in sorted(GUI.glob("*.py"))}


def shared_names() -> dict[str, list[str]]:
    """The names two or more modules of the window hold, one of them at least by an import:
    name → the modules (start, app, …)."""
    modules = {name[:-3]: tree for name, tree in _window_modules().items()}
    holders: dict[str, list[str]] = {}
    for module, tree in modules.items():
        for name in held_by(tree):
            holders.setdefault(name, []).append(module)
    return {name: sorted(held) for name, held in holders.items() if len(held) > 1
            and any(name in imported_by(modules[m]) for m in held)}


def called_through_a_mixin() -> set[str]:
    """The methods a module of the window calls through the class of a mixin of MainWindow
    that defines them (TaskRunner.in_background(…)): a replacement on MainWindow misses
    those calls."""
    modules = _window_modules()
    window = next(n for n in ast.walk(modules["app.py"])
                  if isinstance(n, ast.ClassDef) and n.name == "MainWindow")
    bases = {ast.unparse(b) for b in window.bases}
    own = {n.name: {m.name for m in n.body if isinstance(m, ast.FunctionDef)}
           for tree in modules.values() for n in tree.body
           if isinstance(n, ast.ClassDef) and n.name in bases}
    return {node.func.attr for tree in modules.values() for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.attr in own.get(node.func.value.id, ())}


def test_the_guard_sees_every_way_of_replacing_a_name():
    """The guard itself: every form of a direct replacement is seen (a name written out or
    in the variable of a loop, setattr and delattr), and the tests' replacements through the
    fixture are found, also in a loop (24 names on 27.10.2026, 26 with the loops)."""
    planted = ast.parse(
        "from openexpress.gui import app as gui_app\n"
        "import openexpress.gui.app\n"
        "def test_x(monkeypatch, in_the_window):\n"
        "    monkeypatch.setattr(gui_app, 'eject', None)\n"
        "    monkeypatch.setattr('openexpress.gui.app.Gtk.FileDialog', None)\n"
        "    monkeypatch.setattr(openexpress.gui.app, 'clear_cache', None)\n"
        "    in_the_window('Adw', None)\n"
        "    replace_in_window(monkeypatch, 'save_consumer', None)\n"
        "    for name in ('probe', 'usb_state'):\n"
        "        monkeypatch.setattr(gui_app, name, None)\n"
        "    for each in ('garmin_usb',):\n"
        "        in_the_window(each, None)\n"
        "    monkeypatch.delattr(gui_app, 'Gdk')\n"
        "    monkeypatch.setattr(gui_app, 'x' + 'y', None)\n")
    assert sorted(direct_in_the_window(planted)) == sorted(
        ["eject", "Gtk", "clear_cache", "probe", "usb_state", "Gdk", "<'x' + 'y'>"])
    assert sorted(through_the_fixture(planted)) == ["Adw", "garmin_usb", "save_consumer"]
    found = _by_name(through_the_fixture)
    assert len(found) >= 20
    assert {"account_client", "datetime", "Adw", "Gtk", "eject", "save_consumer",
            "clear_cache"} <= set(found)


# A name shared by modules of the window that a test replaces in one of them alone, on
# purpose: (test file, module, name) → why.
ALONE_ON_PURPOSE = {
    ("test_narrow_window.py", "start", "Adw"):
        "the row of chips of start.py without Adw.WrapBox (libadwaita < 1.7), nothing else",
}


def test_the_guard_sees_a_replacement_in_one_module_and_on_the_window_class():
    planted = ast.parse(
        "from openexpress.gui import start, detection as found\n"
        "import openexpress.gui.maps_page as maps\n"
        "from openexpress.gui import app\n"
        "from openexpress.gui.app import MainWindow\n"
        "def test_x(monkeypatch):\n"
        "    monkeypatch.setattr(start, 'Adw', None)\n"
        "    monkeypatch.setattr(found, 'probe', None)\n"
        "    monkeypatch.delattr(maps, 'space_text')\n"
        "    monkeypatch.setattr('openexpress.gui.words.space_text', None)\n"
        "    monkeypatch.setattr(app, 'eject', None)\n"
        "    monkeypatch.setattr(MainWindow, 'in_background', None)\n"
        "    for name in ('_written_apps', 'busy'):\n"
        "        monkeypatch.setattr(app.MainWindow, name, None)\n")
    assert sorted(in_one_module(planted)) == [
        ("detection", "probe"), ("maps_page", "space_text"), ("start", "Adw"),
        ("words", "space_text")]
    assert sorted(on_the_window_class(planted)) == ["_written_apps", "busy", "in_background"]
    shared = shared_names()
    assert {"app", "start"} <= set(shared["Adw"]) and "maps_page" in shared["space_text"]
    assert "log" not in shared  # a logger of each module's own
    assert {"in_background", "_report_and_log", "_written_apps"} <= called_through_a_mixin()
    assert "__subclasses__" not in called_through_a_mixin()  # not a method of the mixin


def test_a_name_shared_by_modules_of_the_window_is_not_replaced_in_one_alone():
    shared = shared_names()
    found = {(path.name, module, name)
             for path in sorted(TESTS.glob("*.py"))
             for module, name in in_one_module(ast.parse(path.read_text(encoding="utf-8")))
             if name in shared}
    assert found - set(ALONE_ON_PURPOSE) == set(), (
        "replaced in one module of the window, though others hold it too (they would keep "
        "it): use in_the_window(name, value), or say why in ALONE_ON_PURPOSE: "
        f"{sorted(found - set(ALONE_ON_PURPOSE))}")
    assert set(ALONE_ON_PURPOSE) <= found  # no entry left over


def test_no_method_a_mixin_calls_through_its_class_is_replaced_on_the_window_class():
    through = called_through_a_mixin()
    found = {name: files for name, files in _by_name(on_the_window_class).items()
             if name in through}
    assert found == {}, (
        "replaced on MainWindow, but the window calls it through its mixin's class, which "
        f"the replacement misses: replace it on the mixin: {found}")


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
    assert "openexpress.gui.start_window" in asking  # the guard itself sees them
    assert sorted(asking - _clock_modules()) == []
