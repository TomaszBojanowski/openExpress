"""The progress of copying music: how many files and how many bytes, never a file nor a
track ("Copying music · 1 of 2 · 103 B of 306 B"): the names are the user's own. All files
and devices are made up."""
from test_ciq_music import iq_device, library, songs  # noqa: F401 - fixtures

from openexpress import i18n, music
from openexpress.progress import Step

NB = " "


def _plan(iq_device, songs):
    st, dev = iq_device
    return st, dev, music.plan_copy(music.scan(songs, dev), dev, st)


def test_the_copy_counts_the_files_and_the_bytes(iq_device, songs):
    st, _dev, plan = _plan(iq_device, songs)
    seen = []
    music.copy(plan, st, progress=seen.append)
    assert seen and all(isinstance(m, Step) for m in seen)
    assert {m.head for m in seen} == {"Copying music"}
    assert seen[0] == f"Copying music{NB}· 1 of 2{NB}· 0{NB}B of 306{NB}B"
    assert seen[-1] == f"Copying music{NB}· 2 of 2{NB}· 306{NB}B of 306{NB}B"
    assert seen[-1].last
    # Each file in turn, and the bytes only grow.
    assert [m.done for m in seen] == sorted(m.done for m in seen)
    assert [m.bytes_done for m in seen] == sorted(m.bytes_done for m in seen)
    # The bar follows the bytes.
    assert seen[-1].fraction == 1.0
    assert not [m for m in seen if ".mp3" in m or "one" in m or "two" in m or "Music/" in m]


def test_a_playlist_copies_its_tracks_the_same_way(iq_device, library):
    st, dev = iq_device
    plan = music.plan_playlist(library, dev, st)
    seen = []
    music.add_playlist(plan, dev, st, progress=seen.append)
    assert seen[-1] == f"Copying music{NB}· 2 of 2{NB}· 306{NB}B of 306{NB}B"


def test_music_progress_in_polish(iq_device, songs):
    st, _dev, plan = _plan(iq_device, songs)
    seen = []
    i18n.setup("pl")
    try:
        music.copy(plan, st, progress=seen.append)
    finally:
        i18n.setup("en")
    assert seen[-1] == f"Kopiowanie muzyki{NB}· 2 z{NB}2{NB}· 306{NB}B z{NB}306{NB}B"


def test_a_copy_nobody_listens_to_tells_nothing(iq_device, songs):
    """No progress: the copy is told nothing (over MTP in the Flatpak, gio copy without -p)."""
    st, _dev, plan = _plan(iq_device, songs)
    told = []
    real = st.write_file

    def write_file(path, local, progress=None, **kw):
        told.append(progress)
        return real(path, local, progress, **kw)

    st.write_file = write_file
    music.copy(plan, st)
    assert told == [None, None]


def test_the_command_line_tells_the_copy_once(iq_device, device_root, songs, capsys):
    """Not on a terminal: one line for the copy, not a line a file."""
    from openexpress.cli import main

    assert main(["--path", str(device_root), "music", "add", *map(str, songs), "--apply"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert [line for line in out if line.startswith("  Copying")] == ["  Copying music"]
