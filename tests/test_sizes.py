"""Sizes as the user reads them: powers of 1000 with the units B, kB, MB and GB, like GNOME
Files, and readable sizes instead of raw byte counts in messages."""

import base64
import re

import pytest
from fakes import SyncClient
from test_sync import UPLOAD_CONFIG, _setup
from test_updates_maps import maps_response, server, sw_response  # noqa: F401 - the fixture

from openexpress import i18n, maps, music, sync, updates
from openexpress.device.storage import LocalStorage
from openexpress.i18n import human_size


@pytest.fixture
def polish():
    i18n.setup("pl")
    yield
    i18n.setup("en")


VALUES = (None, 0, 1, 999, 1000, 1500, 2048, 699392, 999_949, 999_950, 1_000_000,
          64 * 1024 * 1024, 999_999_999, 6_657_199_308, 2_500_000_000_000)


def test_sizes_use_powers_of_1000_like_gnome_files():
    assert [human_size(n) for n in VALUES] == [
        "?", "0\u00a0B", "1\u00a0B", "999\u00a0B", "1.0\u00a0kB", "1.5\u00a0kB", "2.0\u00a0kB",
        "699.4\u00a0kB", "999.9\u00a0kB",
        "1.0\u00a0MB",  # 999.95 kB is not written as "1000.0 kB"
        "1.0\u00a0MB", "67.1\u00a0MB", "1.0\u00a0GB", "6.7\u00a0GB", "2500.0\u00a0GB"]


def test_sizes_in_polish_only_differ_in_the_separator(polish):
    assert [human_size(n) for n in VALUES] == [
        "?", "0\u00a0B", "1\u00a0B", "999\u00a0B", "1,0\u00a0kB", "1,5\u00a0kB", "2,0\u00a0kB",
        "699,4\u00a0kB", "999,9\u00a0kB", "1,0\u00a0MB", "1,0\u00a0MB", "67,1\u00a0MB",
        "1,0\u00a0GB", "6,7\u00a0GB", "2500,0\u00a0GB"]


def test_a_size_never_breaks_between_its_number_and_its_unit():
    """The window at 360 px wraps a size as "4,7 / GB" without it: a hard space (U+00A0) joins
    the number and the unit, in English and in Polish; the device report, text for GitHub,
    keeps a plain one (and the command line and the log write plain spaces)."""
    for language in ("en", "pl"):
        i18n.setup(language)
        try:
            for n in VALUES[1:]:
                number, unit = human_size(n).split("\u00a0")
                assert unit in ("B", "kB", "MB", "GB") and " " not in number, (language, n)
        finally:
            i18n.setup("en")
    assert human_size(4_700_000_000, localize=False) == "4.7 GB"


def test_the_command_line_writes_plain_spaces(device_root, capsys):
    """The hard spaces are for the window, whose texts wrap (a size, a Polish one-letter
    word); what the command line writes is copied, searched and parsed, so every command
    writes plain spaces: here the sizes of `info` and the words of `--help`."""
    from openexpress.cli import main

    try:
        assert main(["--lang", "pl", "--path", str(device_root), "info"]) == 0
        with pytest.raises(SystemExit):
            main(["--lang", "pl", "--help"])
        assert human_size(2_500_000) == "2,5\u00a0MB"  # the window's, still
    finally:
        i18n.setup("en")
    out = capsys.readouterr()
    text = out.out + out.err
    assert re.search(r"\d,\d (kB|MB|GB)", text)  # a size, with a plain space
    assert re.search(r"(?<!\w)[wziouaWZIOUA] \w", text)  # a one-letter word, with a plain one
    assert "\u00a0" not in text


def test_commands_and_window_use_the_same_function():
    from openexpress import cli
    from openexpress.gui import app

    assert cli._human is human_size and app.human_size is human_size


def uploading(device_root, tmp_path):
    (device_root / "GARMIN/Activity/2026-10-03-10-00-00.fit").write_bytes(b"x" * 1500)
    st, dev, state = _setup(device_root, tmp_path)
    plan = sync.plan_uploads(dev, st, UPLOAD_CONFIG, state)
    lines = []
    sync.run_uploads(plan, dev, st, SyncClient(), state, progress=lines.append,
                     wait_for_processing=False)
    return [line for line in lines if "2026-10-03" in line]


def test_upload_progress_shows_a_readable_size(device_root, tmp_path):
    assert uploading(device_root, tmp_path) == [
        "Uploading FIT_TYPE_4/2026-10-03-10-00-00.fit (1.5\u00a0kB)"]


def test_upload_progress_in_polish(polish, device_root, tmp_path):
    assert uploading(device_root, tmp_path) == [
        "Wysyłanie FIT_TYPE_4/2026-10-03-10-00-00.fit (1,5\u00a0kB)"]


def test_update_download_progress_shows_a_readable_size(server, device_root, xdg):
    items = updates.parse_updates(sw_response(server))
    items[1].size = None  # the server does not always say
    lines = []
    updates.install(items, LocalStorage(device_root), progress=lines.append)
    downloads = [line for line in lines if line.startswith("Downloading ")]
    assert downloads == [
        f"Downloading System {items[0].version} ({human_size(items[0].size)})",
        f"Downloading Aux {items[1].version} (?)"]


def test_map_download_progress_shows_a_readable_size(server, device_root, xdg):
    all_maps = maps.parse_maps(maps_response(server))
    east = all_maps[1]
    lines = []
    maps.install(east, all_maps, LocalStorage(device_root),
                 lambda ident: {"unlocks": [{"filename": "Garmin/E2.gma",
                                             "gma": base64.b64encode(b"UNLOCK").decode()}]},
                 progress=lines.append)
    downloads = [line for line in lines if line.startswith("Downloading ")]
    assert downloads == [f"Downloading {f.device_path} ({human_size(f.size)})"
                         for f in sorted(east.files, key=lambda f: f.size)]
    assert downloads  # every file, each with its own size


def not_enough_space(server, device_root, tmp_path, monkeypatch):
    """The three places that refuse to write when the device is too full (a big one: an
    update keeps 2 MiB free)."""
    monkeypatch.setattr(LocalStorage, "free_space", lambda self: 1_200_000)
    monkeypatch.setattr(LocalStorage, "capacity", lambda self: 32_000_000_000)
    st = LocalStorage(device_root)
    said = []
    with pytest.raises(updates.UpdateError) as error:
        updates.install(updates.parse_updates(sw_response(server)), st)
    said.append(str(error.value))
    all_maps = maps.parse_maps(maps_response(server))
    with pytest.raises(maps.MapError) as error:
        maps.install(all_maps[1], all_maps, st, lambda ident: {})
    said.append(str(error.value))
    monkeypatch.setattr(music.CopyPlan, "size", property(lambda self: 2_500_000))
    with pytest.raises(music.MusicError) as error:
        music.copy(music.CopyPlan(), st)
    said.append(str(error.value))
    return said


def test_not_enough_space_is_said_with_readable_sizes(server, device_root, xdg, tmp_path,
                                                      monkeypatch):
    said = not_enough_space(server, device_root, tmp_path, monkeypatch)
    for text in said:
        assert text.startswith("Not enough space on the device: ") and "1200000" not in text
    # An update says what it keeps free besides (maps and music do not).
    assert said[0].endswith(" needed and 2.1\u00a0MB kept free, but only 1.2\u00a0MB free")
    assert all(text.endswith(" needed, 1.2\u00a0MB free") for text in said[1:])
    assert said[2] == "Not enough space on the device: 2.5\u00a0MB needed, 1.2\u00a0MB free"


def test_not_enough_space_in_polish(polish, server, device_root, xdg, tmp_path,
                                    monkeypatch):
    said = not_enough_space(server, device_root, tmp_path, monkeypatch)
    assert said[2] == "Za mało miejsca na urządzeniu: potrzeba 2,5\u00a0MB, wolne 1,2\u00a0MB"
    assert all(text.endswith(", wolne 1,2\u00a0MB") for text in said)


# --- the device report and the log -------------------------------------------------------------


def test_report_sizes_match_the_window_but_keep_the_point(polish, device_root, monkeypatch):
    from openexpress import report
    from openexpress.device.xmldevice import parse

    assert human_size(6_657_199_308) == "6,7\u00a0GB"  # the window, in Polish
    assert human_size(6_657_199_308, localize=False) == "6.7 GB"
    assert human_size(512, localize=False) == "512 B" and human_size(None, localize=False) == "?"

    monkeypatch.setattr(LocalStorage, "free_space", lambda self: 6_657_199_308)
    monkeypatch.setattr(LocalStorage, "capacity", lambda self: 7_623_566_950)
    st = LocalStorage(device_root)
    text = report.build(parse(st.read("GARMIN/GarminDevice.xml")), st)
    assert "- Storage: 7.6 GB total, 6.7 GB free" in text  # English, also in a Polish session

    monkeypatch.setattr(LocalStorage, "free_space", lambda self: None)
    monkeypatch.setattr(LocalStorage, "capacity", lambda self: 0)
    assert "- Storage: unknown total, unknown free" in report.build(
        parse(st.read("GARMIN/GarminDevice.xml")), st)


@pytest.mark.parametrize("n, text", [
    (None, "?"), (0, "0 B"), (999, "999 B"), (1000, "1_000 B"), (699_392, "699_392 B"),
    (6_657_384_448, "6_657_384_448 B"), (2_500_000_000_000, "2_500_000_000_000 B")])
def test_log_sizes_are_exact_bytes(n, text):
    from openexpress import applog

    assert applog.size(n) == text
    if n is not None:
        assert int(text.removesuffix(" B")) == n  # exact, and still a number for a program


def test_log_sizes_pass_the_filter_and_identifiers_do_not(log_file):
    import logging

    from openexpress import applog

    unit, profile = 3987654321, 12345678  # made up
    assert applog.redact(f"download: {applog.size(6_657_384_448)} from example.org") == (
        "download: 6_657_384_448 B from example.org")  # not "<number>"
    # The filter itself is unchanged: a long number is hidden, with or without " B" after it.
    assert applog.redact(f"device {unit}") == "device <number>"
    assert applog.redact(f"device {unit} B") == "device <number> B"
    assert applog.redact(f"profile {profile} wrote {applog.size(1_234_567)}") == (
        "profile <number> wrote 1_234_567 B")

    applog.setup("cli")
    applog.hide(unit, "unit")
    logging.getLogger("openexpress.test").info(
        "backup: 97 files (%s) for unit %s, profile %s", applog.size(3_254_879), unit, profile)
    line = log_file.read_text().splitlines()[-1]
    assert line.endswith("backup: 97 files (3_254_879 B) for unit <unit>, profile <number>")


def test_ptp_debug_line_keeps_the_size_of_a_large_file(log_file, monkeypatch):
    import logging

    from ptp_fake import FakeDevice

    from openexpress import applog
    from openexpress.device import ptp

    monkeypatch.setenv("OPENEXPRESS_LOG", "debug")
    applog.setup("cli")
    fake = FakeDevice()
    fake.add("GARMIN/Apps/big.bin", b"x" * 1_500_000)
    with ptp.Session(fake) as session:
        storage = session.storages()[0]
        handle = session.lookup(storage, "GARMIN/Apps/big.bin").handle
        assert len(session.read_file(handle)) == 1_500_000
    logging.shutdown()
    text = log_file.read_text()
    assert "received 1_500_000 B)" in text and "<number>" not in text
    assert "(sent 0 B, received 0 B)" in text  # small numbers stay plain


def test_log_size_in_a_report_survives_the_second_filter(log_file):
    from openexpress import applog, report

    log_file.parent.mkdir(parents=True, exist_ok=True)
    log_file.write_text(f"download: {applog.size(6_657_384_448)} from example.org\n"
                        "device 3987654321\n")
    text = report.with_log("### System\n", applog.tail(report.LOG_LINES))
    assert "download: 6_657_384_448 B from example.org" in text
    assert "3987654321" not in text and "device <number>" in text
