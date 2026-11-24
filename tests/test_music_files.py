"""Music: where copied tracks go, and which files a removal pattern matches."""

import shutil
from pathlib import Path

import pytest
from test_ciq_music import iq_device  # noqa: F401 - iq_device: a fixture

from openexpress import cli, music
from openexpress.cli import main

# --- music ----------------------------------------------------------------------------


def _track(local, album, disc=None, size=10):
    return music.Track(local=Path(local), artist="Art", album=album, compilation=False,
                       kind="track", size=size, disc=disc)


def test_plan_copy_disambiguates_duplicates(iq_device):
    st, dev = iq_device
    tracks = [_track("/a/CD1/01 Intro.mp3", "Alb", 1), _track("/a/CD2/01 Intro.mp3", "Alb", 2),
              _track("/b/01 intro.MP3", "alb"), _track("/c/01 Intro.mp3", "Alb")]
    dests = [d for _t, d in music.plan_copy(tracks, dev, st).to_copy]
    assert dests == ["Music/Art/Alb/01 Intro.mp3", "Music/Art/Alb/2-01 Intro.mp3",
                     "Music/Art/alb/01 intro (2).MP3", "Music/Art/Alb/01 Intro (3).mp3"]
    assert len({d.casefold() for d in dests}) == 4


def test_scan_direct_file_does_not_use_parent_folders(iq_device, tmp_path):
    st, dev = iq_device
    home = tmp_path / "home" / "user"
    home.mkdir(parents=True)
    f = home / "song.mp3"
    f.write_bytes(b"\x00" * 10)
    t = music.scan([f], dev)[0]
    assert t.artist == "" and t.album == ""
    assert t.device_path(dev) == "Music/Unknown/Unknown/song.mp3"
    lib = tmp_path / "lib"
    (lib / "Album").mkdir(parents=True)
    shutil.copy(f, lib / "Album" / "x.mp3")
    shutil.copy(f, lib / "top.mp3")
    by_name = {t.local.name: t for t in music.scan([lib], dev)}
    assert (by_name["x.mp3"].artist, by_name["x.mp3"].album) == ("", "Album")
    assert (by_name["top.mp3"].artist, by_name["top.mp3"].album) == ("", "")


# --- music remove matching ---------------------------------------------------------------

PATHS = ["Music/Pink Floyd/The Wall/01 In the Flesh.mp3",
         "Music/Various Artists/Hits/02 Song.mp3",
         "Podcasts/Daily News/ep1.mp3",
         "Music/Road trip.m3u8"]


ROOTS = ["Music/", "Podcasts"]


def test_music_match_rules():
    m = cli.match_music_paths
    assert m(PATHS, ROOTS, ["floyd"]) == [PATHS[0]]
    assert m(PATHS, ROOTS, ["flesh"]) == [PATHS[0]]
    assert m(PATHS, ROOTS, ["daily news"]) == [PATHS[2]]
    assert m(PATHS, ROOTS, ["ROAD TRIP"]) == [PATHS[3]]
    assert m(PATHS, ROOTS, ["/music/various artists/hits/02 song.mp3"]) == [PATHS[1]]
    # The top media folder never matches.
    assert m(PATHS, ROOTS, ["music"]) == []
    assert m(PATHS, ROOTS, ["podcasts"]) == []
    assert m(PATHS, ROOTS, ["   "]) == []


@pytest.mark.parametrize("pattern", ["", "  ", "ab"])
def test_music_remove_rejects_short_patterns(device_root, xdg, capsys, pattern):
    assert main(["--path", str(device_root), "music", "remove", pattern, "--apply"]) == 1
    assert "at least 3 characters" in capsys.readouterr().err


def test_a_short_pattern_is_refused_before_the_device_is_opened(monkeypatch, xdg, capsys):
    """A mistake of the command line: said at once, without waiting for the device."""
    monkeypatch.setattr(cli, "open_device", lambda args: pytest.fail("the device was opened"))
    assert main(["music", "remove", "ab", "--apply"]) == 1
    assert "at least 3 characters" in capsys.readouterr().err
