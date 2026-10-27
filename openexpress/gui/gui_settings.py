"""The window's own settings: gui.json in the configuration folder (the size of the window, the
view of the start page, the switch of the background sync) and the size of the window read
from them, checked. No GTK here.

Has: no attribute of the window; the window keeps what load_gui_settings and
update_gui_settings give in its gui_settings.
Uses: nothing of the window."""
from __future__ import annotations

import json
from pathlib import Path

from openexpress import paths


def gui_settings_path() -> Path:
    return paths.config_dir() / "gui.json"


def load_gui_settings() -> dict:
    """The window's own settings; {} when there are none, or when the file is not an
    object (edited by hand): the defaults then, never a window that does not start."""
    p = gui_settings_path()
    try:
        data = json.loads(p.read_text()) if p.exists() else {}
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def save_gui_settings(data: dict) -> None:
    paths.write_private(gui_settings_path(), json.dumps(data, indent=2))


def update_gui_settings(**changes) -> dict:
    """Change these keys of the window's settings (gui.json), read again first so that the
    others (the size of the window, the view of the start page, the switch of the background
    sync, kept since by another part) stay; the settings as written. The one way to change
    gui.json."""
    data = load_gui_settings()
    data.update(changes)
    save_gui_settings(data)
    return data


DEFAULT_WINDOW = (1000, 740)
MIN_WINDOW = (360, 480)
MAX_WINDOW = 10000


def window_geometry(settings: dict) -> tuple[int, int, bool]:
    """Saved window size and maximized state, validated (falls back to the defaults)."""
    saved = settings.get("window")
    if not isinstance(saved, dict):
        return (*DEFAULT_WINDOW, False)

    def size(key: str, default: int, minimum: int) -> int:
        value = saved.get(key)
        if isinstance(value, bool) or not isinstance(value, int):
            return default
        return max(minimum, min(MAX_WINDOW, value))

    return (size("width", DEFAULT_WINDOW[0], MIN_WINDOW[0]),
            size("height", DEFAULT_WINDOW[1], MIN_WINDOW[1]),
            saved.get("maximized") is True)


def save_window_geometry(width: int, height: int, maximized: bool) -> None:
    """Stores the window state; the other saved options are kept (update_gui_settings)."""
    update_gui_settings(window={"width": int(width), "height": int(height),
                                "maximized": bool(maximized)})
