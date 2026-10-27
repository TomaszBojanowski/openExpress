"""Guard 3 of splitting gui/app.py (analysis 17): a test that replaces a name in gui.app
(monkeypatch.setattr(app, "name", …)) changes only what gui/app.py itself looks up. Once the
code that uses the name moves to another module, the replacement does nothing and the test
checks something else, without failing. So every name the tests replace in gui.app must be
used in app.py, not only imported there. In the same way, the made-up clock (conftest.clock)
must stand in every module of gui/ that asks datetime.now(). Read from the sources only."""

import ast
from pathlib import Path

TESTS = Path(__file__).parent
GUI = TESTS.parent / "openexpress" / "gui"
WINDOW = "openexpress.gui.app"


def _aliases(tree: ast.AST) -> set[str]:
    """The names a test file gives the window's module (app, gui_app, …), anywhere in it."""
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "openexpress.gui":
            out |= {a.asname or a.name for a in node.names if a.name == "app"}
        elif isinstance(node, ast.Import):
            out |= {a.asname for a in node.names if a.name == WINDOW and a.asname}
    return out


def replaced_in_the_window() -> dict[str, list[str]]:
    """Each name the tests replace in gui.app, with the files that do it."""
    found: dict[str, list[str]] = {}
    for path in sorted(TESTS.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        aliases = _aliases(tree)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "setattr" and node.args):
                continue
            target, name = node.args[0], None
            if (isinstance(target, ast.Name) and target.id in aliases and len(node.args) > 1
                    and isinstance(node.args[1], ast.Constant)):
                name = node.args[1].value
            elif (isinstance(target, ast.Constant) and isinstance(target.value, str)
                  and target.value.startswith(WINDOW + ".")):
                name = target.value[len(WINDOW) + 1:].split(".")[0]
            if isinstance(name, str):
                found.setdefault(name, []).append(path.name)
    return found


def used_in(path: Path) -> set[str]:
    """The names a module reads (a name in code, not in an import)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and isinstance(n.ctx,
                                                                                 ast.Load)}


def test_the_tests_replace_names_in_the_window():
    """The guard itself: it finds the replacements (23 names on 27.10.2026)."""
    found = replaced_in_the_window()
    assert len(found) >= 20 and "account_client" in found and "datetime" in found


def test_every_name_the_tests_replace_in_the_window_is_used_there():
    used = used_in(GUI / "app.py")
    dead = {name: files for name, files in replaced_in_the_window().items() if name not in used}
    assert dead == {}, (
        "replaced in gui.app but not used in app.py (the code moved: replace it where it "
        f"is used now): {dead}")


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
