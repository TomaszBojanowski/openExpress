"""The first start (step 3 after the audit of 0.7.9): settings.json does not exist yet, and the
window, the background service and the command line (or two devices the window opens at
once) load the settings at the same moment. Each made a client GUID of its own and wrote
it, so that the program told Garmin two GUIDs; now the first one writes it under a lock and
the others read it again under the lock. The language of the interface is written under
the same lock, so that it cannot write over a GUID just written. All data is made up."""

import json
import threading
import uuid

from openexpress import i18n, paths, settings
from openexpress.settings import Settings


def _settings_file():
    return paths.config_dir() / "settings.json"


def test_loads_at_the_same_moment_make_one_client_guid(xdg, monkeypatch):
    made = iter([uuid.UUID(int=1), uuid.UUID(int=2)])
    together = threading.Barrier(2)

    def uuid4():
        guid = next(made)
        try:
            together.wait(timeout=0.5)  # both have read the file without a GUID
        except threading.BrokenBarrierError:
            pass  # the other waits for the lock: it reads the GUID written
        return guid

    monkeypatch.setattr(settings.uuid, "uuid4", uuid4)
    loaded = []
    threads = [threading.Thread(target=lambda: loaded.append(Settings.load().client_guid))
               for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)
    kept = json.loads(_settings_file().read_text())["client_guid"]
    assert loaded == [kept, kept]


def test_the_language_is_written_under_the_same_lock(xdg, monkeypatch):
    entered = []
    locked = paths.settings_locked

    def recorded():
        entered.append(True)
        return locked()

    monkeypatch.setattr(paths, "settings_locked", recorded)
    i18n.save_ui_language("pl")
    Settings.load()  # the first start: its GUID written
    Settings.load().save()
    assert len(entered) == 3
    data = json.loads(_settings_file().read_text())
    assert data["ui_language"] == "pl" and data["client_guid"]


def test_a_settings_file_with_a_guid_is_only_read(xdg, monkeypatch):
    Settings.load()
    before = _settings_file().read_text()
    monkeypatch.setattr(paths, "settings_locked",
                        lambda: (_ for _ in ()).throw(AssertionError("no lock to read")))
    assert Settings.load().client_guid == json.loads(before)["client_guid"]
    assert _settings_file().read_text() == before
