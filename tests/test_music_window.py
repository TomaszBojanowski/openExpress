"""The Music page of the window: the trash of a file or of a playlist asks before it removes, as
every other trash of the window does; nothing goes before the yes. Made-up files; nothing reaches
a device."""

import types

import pytest
from fakes import descendants, device_session, page_window, window_methods

from openexpress import music
from openexpress.music import DeviceMedia

pytestmark = pytest.mark.needs_display

TRACK = DeviceMedia("Music/Someone/Album/Morning Song.mp3", 3_000_000, "track")
PLAYLIST = "Music/Morning run.m3u8"


@pytest.fixture
def page(in_the_window, monkeypatch):
    """The page of a device that plays tracks and keeps playlists, the questions it asks and
    what it removes."""
    questions, removed = [], []

    def confirm(_parent, name, heading, body, *answers, **_kw):
        questions.append(types.SimpleNamespace(name=name, heading=heading, body=body,
                                               answers=answers))

    in_the_window("confirm", confirm)
    monkeypatch.setattr(music, "remove", lambda storage, paths: removed.extend(paths) or paths)
    win, section = page_window("music")
    targets = {music.KINDS["track"]: ["Music"], music.PLAYLIST: ["Music"]}
    dev = types.SimpleNamespace(targets=lambda datatype: targets.get(datatype, []), unit_id=None)
    win.session = device_session(device=dev, storage=None)
    win.choose_music = win.choose_playlist = lambda *a, **kw: None
    win.load_section = lambda key, force=False: None
    win.toasts = []
    win.toast = win.toasts.append
    window_methods(win, ["show_music", "confirm_remove_music", "remove_music", "_removed_files"])
    win.show_music(([TRACK], [PLAYLIST]), free=None)
    return win, section, questions, removed


def trash(section, tooltip: str):
    from gi.repository import Gtk

    (button,) = [w for w in descendants(section.page)
                 if isinstance(w, Gtk.Button) and w.get_tooltip_text() == tooltip]
    return button


@pytest.mark.parametrize("tooltip, heading, body, path", [
    ("Remove Morning Song.mp3", "Remove Morning Song.mp3?",
     "Music/Someone/Album/Morning Song.mp3 (3.0 MB) will be removed from the device.",
     TRACK.path),
    ("Remove Morning run.m3u8", "Remove Morning run.m3u8?",
     "Music/Morning run.m3u8 will be removed from the device; its music stays.", PLAYLIST),
])
def test_the_trash_asks_before_it_removes(page, tooltip, heading, body, path):
    win, section, questions, removed = page
    trash(section, tooltip).emit("clicked")
    assert removed == []  # nothing before the yes
    (q,) = questions
    assert (q.name, q.heading, q.body) == ("music-remove", heading, body)
    (yes,) = q.answers
    assert (yes.label, yes.look) == ("Remove", "destructive")
    yes.run()
    assert removed == [path] and win.toasts == ["Removed 1 file"]


def test_no_answer_removes_nothing(page):
    win, section, questions, removed = page
    trash(section, "Remove Morning Song.mp3").emit("clicked")
    assert len(questions) == 1 and removed == []  # closed or Cancel: no answer runs
