"""The keyboard shortcuts of the window: the window of shortcuts opens with Ctrl+/ as well as
with Ctrl+? (which needs Shift where "?" does, as on a Polish keyboard),
and that window shows each shortcut as it is bound, the first of its keys."""

import json
import subprocess
import sys
from pathlib import Path

import pytest
import separate_bus
from fakes import window as stand_in

pytestmark = pytest.mark.needs_display

# The application registered as the program registers it, in a process of its own on a D-Bus
# of its own (separate_bus): never on the bus of the session, where an openExpress of the user
# may run. It prints the keys of each action.
BOUND = """
import json
from openexpress.gui import app
application = app.App()
assert application.register(None)
print(json.dumps({name: application.get_accels_for_action(name)
                  for name in application.list_action_descriptions()}))
"""


@pytest.fixture(scope="module")
def bound(tmp_path_factory) -> dict[str, list[str]]:
    from gi.repository import Adw

    if not hasattr(Adw, "ShortcutsDialog"):
        pytest.skip("libadwaita without Adw.ShortcutsDialog (before 1.8)")
    if not separate_bus.available():
        pytest.skip("no dbus-run-session")
    folder = tmp_path_factory.mktemp("bus")
    done = subprocess.run(separate_bus.command([sys.executable, "-c", BOUND], folder),
                          cwd=Path(__file__).parent.parent, env=separate_bus.environment(folder),
                          capture_output=True, text=True, timeout=60, check=True)
    return json.loads(done.stdout.splitlines()[-1])


def test_ctrl_slash_opens_the_keyboard_shortcuts_too(bound):
    # Ctrl+/ first: the menu shows the first.
    assert bound["app.shortcuts"] == ["<Control>slash", "<Control>question"]


def test_the_window_of_shortcuts_shows_each_as_it_is_bound(bound, monkeypatch):

    from gi.repository import Adw

    from openexpress.gui.app import MainWindow

    sections = []
    monkeypatch.setattr(Adw.ShortcutsDialog, "add", lambda self, section: sections.append(
        section))
    monkeypatch.setattr(Adw.ShortcutsDialog, "present", lambda self, parent: None)
    MainWindow.show_shortcuts(stand_in())
    shown = {item.get_title(): item.get_accelerator() for section in sections
             for item in (section.get_item(i) for i in range(section.get_n_items()))}
    assert shown == {"Sync": "<Control>s", "Refresh": "<Control>r",
                     "Preferences": "<Control>comma", "Keyboard Shortcuts": "<Control>slash",
                     "Quit": "<Control>q"}
    firsts = {keys[0] for keys in bound.values()}
    assert set(shown.values()) <= firsts  # each as the first key of its action
