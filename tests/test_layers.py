"""The layers of the program (code audit of 25.10.2026): the window and its pages import
nothing of the command line (cli.py), which itself starts the window; what both use (texts,
sizes, a line on stderr) lives in modules of their own."""

import ast
from pathlib import Path

import openexpress

PACKAGE = Path(openexpress.__file__).parent


def _imported(path: Path) -> set[str]:
    names = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            names.update(f"{node.module}.{a.name}" for a in node.names)
    return names


def test_the_window_imports_nothing_of_the_command_line():
    found = {path.name: sorted(n for n in _imported(path) if n == "openexpress.cli"
                               or n.startswith("openexpress.cli."))
             for path in sorted((PACKAGE / "gui").glob("*.py"))}
    assert {name: names for name, names in found.items() if names} == {}


def test_the_texts_of_the_end_of_an_install_are_with_the_updates():
    from openexpress import updates

    assert "Disconnect the device" in updates.FINISH_STEPS
    assert "check for updates again" in updates.FINISH_STEPS_WINDOW
