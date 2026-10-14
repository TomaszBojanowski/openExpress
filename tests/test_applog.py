"""The program's own log file: where it is, how it rotates, and that nothing personal is
written to it. All identifiers here are made up."""

import logging
import os
import stat

import pytest

from openexpress import applog, paths

UNIT = "3987654321"
SERIAL = "7ZQ123456"
SECRETS = [
    ("unit id", f"device {UNIT} is not on the list", UNIT),
    ("numeric MTP address", f"opened mtp://091e_51b4_{SERIAL}/Internal%20Storage/GARMIN", SERIAL),
    ("named MTP address", f"opened mtp://Garmin_Edge_1050_{SERIAL}/Internal", SERIAL),
    ("gvfs path", f"/run/user/1000/gvfs/mtp:host=091e_51b4_{SERIAL}/Internal Storage", SERIAL),
    ("e-mail", "account anna.nowak+garmin@example.org signed in", "anna.nowak"),
    ("oauth parameter", "GET /x?oauth_token=abcd1234efgh&oauth_signature=Zm9vYmFy%3D", "abcd1234efgh"),
    ("oauth signature", "GET /x?oauth_token=a&oauth_signature=Zm9vYmFy%3D", "Zm9vYmFy"),
    ("ticket in a URL", "redirect https://sso.example/?ticket=ST-0123-AbCdEfGh-cas", "AbCdEfGh"),
    ("bare ticket", "got ST-0123-AbCdEfGh-cas", "AbCdEfGh"),
    ("authorization header", 'Authorization: OAuth oauth_consumer_key="k1", oauth_nonce="n2"', "k1"),
    ("cookie", "Cookie: SESSIONID=s3cr3tvalue; other=1", "s3cr3tvalue"),
    ("set-cookie", "set-cookie: GARMIN-SSO-GUID=Abc123; Path=/", "Abc123"),
    ("password", "login with password=Tr0ub4dor&3 failed", "Tr0ub4dor"),
    ("json token", '{"access_token": "tok-value-1", "x": 1}', "tok-value-1"),
    ("json secret", '{"token_secret":"shhh-9"}', "shhh-9"),
    ("long key", "consumer fc3e99d2-118c-44b8-8ae3-03370dde24c0 key Xy7Kq2Lm9Pw4Rt6Vb8Nc1Zd3Fg5Hj",
     "Xy7Kq2Lm9Pw4Rt6Vb8Nc1Zd3Fg5Hj"),
    ("long hex", "md5 of the key 0123456789abcdef0123456789abcdef", "0123456789abcdef"),
    ("base64 secret", "body Zm9vL2Jhci9iYXovcXV4L3F1dXgvY29yZ2UvZ3JhdWx0LzEyMzQ1Ng== sent",
     "Zm9vL2Jhci9iYXovcXV4"),
    ("profile number", "user profile 12345678 loaded", "12345678"),
    ("activity id", "activity 20123456789 already in Connect", "20123456789"),
    ("home folder", f"reading {os.path.expanduser('~')}/Music/x", os.path.expanduser("~")),
    ("local file", "File not found: /srv/media/Anna Jantar/Tyle słońca.mp3", "Jantar"),
    ("local file in the home folder", "cannot read ~/Muzyka/Moja Lista/01 Utwór.flac: denied",
     "Utwór"),
    ("media file on the device",
     "Cannot write mtp://091e_51b4_X/Internal Storage/Music/Anna Jantar/Album/01.mp3: refused",
     "Jantar"),
    ("playlist on the device", "wrote Music/Playlists/Bieganie rano.m3u8", "Bieganie"),
    ("folder given with --path", "Cannot read /tmp/kopia zegarka/GARMIN/GarminDevice.xml", "kopia"),
]


def lines(path):
    return path.read_text(encoding="utf-8").splitlines()


@pytest.mark.parametrize("what, text, secret", SECRETS, ids=[s[0] for s in SECRETS])
def test_redact_removes(what, text, secret):
    assert secret not in applog.redact(text)


def test_redact_keeps_what_is_useful():
    text = ("sync: 2 files uploaded, 0 failed in 12.5 s; USB 091e:0003 then 091e:51b4; "
            "part 006-B4532-00, software 23.39, HTTP 204, 265.5 KB, FIT_TYPE_39")
    assert applog.redact(text) == text
    assert applog.redact("mtp://091e_51b4_X/Internal/GARMIN/Apps") == "mtp://<device>/Internal/GARMIN/Apps"
    assert applog.redact("cannot read ~/x y/z.fit: denied") == "cannot read <path>: denied"
    assert applog.redact("HTTP GET https://example.org/device-service/a/b -> 200 in 0.4 s") == (
        "HTTP GET https://example.org/device-service/a/b -> 200 in 0.4 s")
    assert applog.redact("wrote 1.2 KB to Garmin/NewFiles, 3/5 done, /sys/bus/usb/devices") == (
        "wrote 1.2 KB to Garmin/NewFiles, 3/5 done, /sys/bus/usb/devices")


def test_known_values_are_hidden_even_when_they_look_harmless():
    applog.hide("K9-42", "serial")
    applog.hide(None, "unit")
    applog.hide("ab", "short")  # too short to replace safely
    assert applog.redact("serial K9-42, tab") == "serial <serial>, tab"


def test_every_line_of_the_file_is_redacted(log_file):
    applog.hide(UNIT, "unit")
    assert applog.setup("cli") == log_file
    log = logging.getLogger("openexpress.test")
    log.info("device %s at %s", UNIT, f"mtp://091e_51b4_{SERIAL}/")
    try:
        raise RuntimeError(f"upload of {UNIT} for anna@example.org failed, token=abc123def")
    except RuntimeError:
        log.exception("sync failed")
    logging.getLogger("urllib3.connectionpool").warning(
        "https://example.org:443 GET /device/%s?oauth_token=zzz999 200", UNIT)
    text = log_file.read_text()
    assert "Traceback" in text and "RuntimeError" in text  # the traceback is there…
    assert 'File "tests/test_applog.py", line ' in text  # …and still says where
    for secret in (UNIT, SERIAL, "anna@example.org", "abc123def", "zzz999"):
        assert secret not in text  # …and nothing private is
    assert "device <unit> at mtp://<device>/" in text


def test_log_file_is_private_and_in_the_state_directory(log_file, tmp_path, monkeypatch):
    assert applog.setup("cli") == log_file == paths.state_dir() / "openexpress.log"
    logging.getLogger("openexpress.test").info("hello")
    assert stat.S_IMODE(log_file.stat().st_mode) == 0o600
    first = lines(log_file)[-1]
    assert " cli[" in first and " INFO openexpress.test: hello" in first

    monkeypatch.delenv("XDG_STATE_HOME")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    assert applog.log_path() == tmp_path / "home/.local/state/openexpress/openexpress.log"


def test_role_names_the_part_of_the_program(log_file):
    for role in ("gui", "watch"):
        applog.setup(role)
        logging.getLogger("openexpress.test").info("from %s", role)
    assert [line.split()[2].split("[")[0] for line in lines(log_file)] == ["gui", "watch"]


@pytest.mark.parametrize("env, verbose, debug, info", [
    (None, False, False, True),      # default: INFO
    (None, True, True, True),        # -v
    ("debug", False, True, True),    # OPENEXPRESS_LOG=debug
    ("DEBUG", False, True, True),
])
def test_levels(log_file, monkeypatch, env, verbose, debug, info):
    if env:
        monkeypatch.setenv("OPENEXPRESS_LOG", env)
    applog.setup("cli", verbose)
    log = logging.getLogger("openexpress.test")
    log.debug("a detail")
    log.info("a step")
    text = log_file.read_text()
    assert ("a detail" in text) is debug and ("a step" in text) is info


def test_log_can_be_turned_off(log_file, monkeypatch, capsys):
    monkeypatch.setenv("OPENEXPRESS_LOG", "off")
    assert applog.setup("cli") is None
    logging.getLogger("openexpress.test").warning("only on stderr")
    assert not log_file.exists() and "only on stderr" in capsys.readouterr().err


def test_stderr_is_as_before(log_file, capsys):
    applog.setup("cli")
    log = logging.getLogger("openexpress.test")
    log.info("quiet")
    log.warning("loud")
    assert capsys.readouterr().err == "WARNING openexpress.test: loud\n"
    applog.setup("watch", stderr_level=logging.INFO, stderr_format="%(message)s")
    log.info("for the journal")
    assert capsys.readouterr().err == "for the journal\n"


PRIVATE_LINE = ("device at mtp://091e_51b4_0000abcdef12/Internal%20Storage, unit 3987654321, "
                "for a@example.org, map Holiday Map, file ~/Music/Someone/song.mp3")


def test_terminal_lines_are_redacted_like_the_file(log_file, capsys):
    applog.setup("cli", verbose=True)  # -v
    applog.hide("Holiday Map", "map")
    logging.getLogger("openexpress.test").debug(PRIVATE_LINE)
    err = capsys.readouterr().err
    assert err == ("DEBUG openexpress.test: device at mtp://<device>/Internal%20Storage, unit "
                   "<number>, for <email>, map <map>, file <path>\n")  # the same line format
    assert lines(log_file)[-1].endswith(err.split(": ", 1)[1].rstrip("\n"))  # as in the file


def test_the_journal_of_the_watcher_is_redacted(log_file, capsys):
    applog.setup("watch", stderr_level=logging.INFO, stderr_format="%(message)s")
    logging.getLogger("openexpress.watch").info("synced %s", "mtp://091e_51b4_0000abcdef12/")
    assert capsys.readouterr().err == "synced mtp://<device>/\n"  # no prefix, as before


def test_a_traceback_on_the_terminal_is_redacted(log_file, capsys):
    applog.setup("cli", verbose=True)
    try:
        raise OSError("cannot read mtp://091e_51b4_0000abcdef12/GARMIN for 3987654321")
    except OSError:
        logging.getLogger("openexpress.test").debug("failed", exc_info=True)
    err = capsys.readouterr().err
    assert "Traceback" in err and "OSError: cannot read mtp://<device>/GARMIN for <number>" in err
    assert "0000abcdef12" not in err and "3987654321" not in err


def test_messages_for_the_user_keep_the_addresses(log_file, monkeypatch, capsys):
    """The addresses to choose one from with --device-uri are printed as they are; only the
    log line about the error, shown with -v, is filtered."""
    from openexpress.cli import main
    from openexpress.device import gio_storage

    found = [gio_storage.FoundDevice("mtp://091e_51b4_0000abcdef12/", "fenix", True),
             gio_storage.FoundDevice("mtp://Garmin_Edge_1050_0000fedcba98/", "Edge", True)]
    monkeypatch.setattr(gio_storage, "find_devices", lambda: found)
    assert main(["-v", "info"]) == 1
    err = capsys.readouterr().err
    assert ("Error: Several devices connected, choose one with --device-uri: "
            "mtp://091e_51b4_0000abcdef12/, mtp://Garmin_Edge_1050_0000fedcba98/") in err
    log_lines = [line for line in err.splitlines() if line.startswith(("INFO ", "DEBUG "))]
    assert log_lines and not any("0000abcdef12" in line or "0000fedcba98" in line
                                 for line in log_lines)


def test_program_works_without_a_log_file(tmp_path, monkeypatch, capsys):
    blocked = tmp_path / "state-is-a-file"
    blocked.write_text("")
    monkeypatch.setenv("XDG_STATE_HOME", str(blocked))
    assert applog.setup("cli") is None
    assert capsys.readouterr().err.startswith("No log file: ")
    logging.getLogger("openexpress.test").warning("still running")
    assert "still running" in capsys.readouterr().err


def test_rotation_keeps_three_files(log_file, monkeypatch):
    monkeypatch.setattr(applog, "MAX_BYTES", 2000)
    applog.setup("cli")
    log = logging.getLogger("openexpress.test")
    for n in range(400):
        log.info("line %03d %s", n, "x" * 40)
    files = sorted(p.name for p in log_file.parent.iterdir() if not p.name.endswith(".lock"))
    assert files == ["openexpress.log", "openexpress.log.1", "openexpress.log.2"]
    for name in files:
        path = log_file.parent / name
        assert path.stat().st_size < 2000 + 200 and stat.S_IMODE(path.stat().st_mode) == 0o600
    assert lines(log_file)[-1].endswith("line 399 " + "x" * 40)  # the newest line is kept


def test_two_processes_share_the_file_without_losing_lines(log_file, monkeypatch):
    """Two handlers stand in for two processes: either may rotate, both keep writing."""
    monkeypatch.setattr(applog, "MAX_BYTES", 500)
    monkeypatch.setattr(applog, "OLD_FILES", 50)  # keep everything, to count the lines
    log_file.parent.mkdir(parents=True)
    handlers = []
    for role in ("gui", "watch"):
        handler = applog._LogFile(log_file, encoding="utf-8")
        handler.setFormatter(applog._Formatter("%(role)s %(message)s", role))
        handlers.append(handler)
    for n in range(300):
        record = logging.LogRecord("t", logging.INFO, "", 0, "line %03d", (n,), None)
        handlers[n % 2].emit(record)
    for handler in handlers:
        handler.close()
    written = [line for p in log_file.parent.iterdir() if not p.name.endswith(".lock")
               for line in lines(p)]
    assert sorted(line.split()[-1] for line in written) == [f"{n:03d}" for n in range(300)]
    assert len(list(log_file.parent.glob("openexpress.log.*"))) > 2  # it did rotate


def test_cli_logs_start_and_end_without_arguments(log_file, device_root):
    from openexpress.cli import main

    assert main(["--path", str(device_root), "files", "--type", "FIT_TYPE_4"]) == 0
    assert main(["--path", str(device_root / "nowhere"), "info"]) == 1
    text = lines(log_file)
    assert [line.split(" start: ")[1].split(" (")[0] for line in text if " start: " in line] == [
        "files --path --type", "info --path"]  # option names only, no values
    ends = [line.split(" end: ")[1] for line in text if " end: " in line]
    assert ends[0].startswith("files, exit code 0, ") and ends[1].startswith("info, exit code 1, ")
    assert str(device_root) not in "\n".join(text) and "FIT_TYPE_4" not in "\n".join(text)
    assert "1234567890" not in "\n".join(text)  # the unit ID of the test device


def test_subcommand_is_logged(log_file, device_root):
    from openexpress.cli import main

    main(["--path", str(device_root), "maps", "remove", "Some Private Name"])
    start = next(line for line in lines(log_file) if " start: " in line)
    assert "start: maps remove --path (" in start and "Private" not in start


# --- what the program writes about its work ---------------------------------------------------

from test_ciq_music import (  # noqa: E402, F401 - the fixtures
    FACE,
    CountingClient,
    FakeStore,
    hidden_apps,
    iq_device,
    queue_once_requested,
    store_app,
)
from test_upload_consent import QUEUED, Account  # noqa: E402

DEVICE_UNIT = "1234567890"  # the unit ID of the test device (tests/conftest.py)


def test_sync_is_logged_step_by_step_without_identifiers(log_file, device_root, xdg, monkeypatch):
    from openexpress import service, sync
    from openexpress.device.storage import LocalStorage
    from openexpress.device.xmldevice import parse
    from openexpress.settings import Settings

    monkeypatch.setenv("OPENEXPRESS_LOG", "debug")
    applog.setup("watch")
    st = LocalStorage(device_root)
    s = service.open_session(storage=st)  # hides the unit ID, like the window and the watcher
    s = service.DeviceSession(st, parse(st.read("GARMIN/GarminDevice.xml")), Settings(),
                              Account(messages=QUEUED))
    state = sync.SyncState(int(DEVICE_UNIT))
    if state.file.exists():
        state.file.unlink()
    summary = service.run_sync(s, first_run="all")
    assert summary.uploaded == 2 and summary.downloaded == 1

    text = log_file.read_text()
    steps = [line.split(": ", 1)[1] for line in text.splitlines() if " INFO " in line]
    assert steps[0] == "sync: start (first run: all, diagnostics: False)"
    assert "uploads allowed: True" in steps
    assert any(s.startswith("upload plan: 2 new (") for s in steps)
    assert any(s.startswith("uploads: 2 uploaded, 0 duplicates") for s in steps)
    assert any(s.startswith("queue from Garmin Connect: 1 items (FIT_TYPE_6×1)") for s in steps)
    assert "downloads: 1 written, 0 empty, 0 failed, 0 not acknowledged" in steps
    assert steps[-1].startswith("sync: end after ") and steps[-1].endswith(
        "2 uploaded, 0 already known, 1 downloaded, 0 errors")
    assert DEVICE_UNIT not in text and str(device_root) not in text


def test_blocked_uploads_and_sync_errors_are_logged(log_file, device_root, xdg):
    from openexpress import service
    from openexpress.device.storage import LocalStorage
    from openexpress.device.xmldevice import parse
    from openexpress.settings import Settings

    applog.setup("gui")
    st = LocalStorage(device_root)
    s = service.DeviceSession(st, parse(st.read("GARMIN/GarminDevice.xml")), Settings(),
                              Account(messages=QUEUED, preference="false"))
    service.run_sync(s, first_run="all")
    text = log_file.read_text()
    assert "uploads allowed: False (" in text and "upload plan: " in text
    assert "uploads: " not in text  # nothing was sent, so no upload result

    s.client.device_messages = lambda unit_id: (_ for _ in ()).throw(RuntimeError("no network"))
    with pytest.raises(RuntimeError):
        service.run_sync(s, first_run="all")
    assert "WARNING openexpress.service: sync: stopped after " in log_file.read_text()
    assert "RuntimeError: no network" in log_file.read_text()


def test_app_install_over_ptp_is_logged(log_file, hidden_apps, monkeypatch):
    from openexpress import ciq

    monkeypatch.setenv("OPENEXPRESS_LOG", "debug")
    applog.setup("cli")
    st, dev, _fake = hidden_apps
    FakeStore.queued = []
    client = CountingClient()
    client.device_messages = queue_once_requested
    rep = ciq.install_apps(dev, st, client, FakeStore(client), [store_app(FACE, "Face It")])
    assert rep.installed == ["Face It"]
    text = log_file.read_text()
    assert "apps install: Face It; app folder: direct×1; requested in Garmin Connect: 1" in text
    assert "apps written: 1 (over PTP 1, updates 0); left in the queue 0 (sandbox 0); failed 0" in text
    assert "DEBUG openexpress.device.ptp: PTP 0x1002 -> 0x2001 (sent 0 B, received 0 B)" in text
    assert "PTP 0x100D -> 0x2001" in text  # SendObject: only its size is logged
    assert DEVICE_UNIT not in text


def test_waiting_for_a_starting_device_is_logged(log_file, monkeypatch):
    from openexpress.device import gio_storage

    applog.setup("cli")
    answers = [gio_storage.NotThereYet("x"), gio_storage.NotThereYet("x"), "storage"]
    states = iter([("starting", ["0003"]), ("absent", []), ("present", ["51b4"])])

    class Opened:
        usb_id = "091e:51b4"

    def open_now(uri):
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            current.update(zip(("state", "products"), next(states)))
            raise answer
        return Opened()

    current = {}
    monkeypatch.setattr(gio_storage, "_open_device_storage", open_now)
    monkeypatch.setattr(gio_storage, "usb_state", lambda: current["state"])
    monkeypatch.setattr(gio_storage, "garmin_products_on_usb", lambda: current["products"])
    ticks = iter(range(100))
    gio_storage.open_device_storage(wait=True, notify=lambda line: None, sleep=lambda s: None,
                                    clock=lambda: float(next(ticks)))
    lines_ = [line.split("gio_storage: ")[1] for line in lines(log_file)]
    assert lines_[0].startswith("device: not available after ")
    assert "USB state: starting (091e:0003), waiting: True" in lines_[0]
    assert "USB state: absent (-), waiting: True" in lines_[1]  # the gap before MTP
    assert lines_[2].startswith("device: opened over MTP after ") and lines_[2].endswith(
        "(USB 091e:51b4)")


def test_http_is_logged_without_query_headers_or_body(log_file, monkeypatch):
    import types

    from openexpress.connect import GarminClient

    applog.setup("cli")
    sent = {}

    def request(method, url, **kw):
        sent.update(kw, url=url)
        return types.SimpleNamespace(status_code=204)

    client = types.SimpleNamespace(http=types.SimpleNamespace(request=request))
    GarminClient._request(client, "PUT", "https://example.org/a/b?unitId=3987654321&x=y", "what",
                          headers={"Authorization": "OAuth secret-part"}, json={"name": "Anna"})
    line = lines(log_file)[-1]
    assert line.split("connect: ")[1].startswith("HTTP PUT https://example.org/a/b -> 204 in ")
    assert "3987654321" not in line and "secret-part" not in line and "Anna" not in line
    assert sent["url"].endswith("?unitId=3987654321&x=y")  # the request itself is unchanged


def test_cli_logs_the_error_shown_to_the_user(log_file, tmp_path, capsys):
    from openexpress.cli import main

    assert main(["--path", str(tmp_path / "Moja kopia"), "info"]) == 1
    assert "Moja kopia" in capsys.readouterr().err  # the user sees the path…
    text = log_file.read_text()
    assert " INFO openexpress.cli: error: DeviceError: " in text
    assert "Moja kopia" not in text and "<path>" in text  # …the log does not keep it


def test_media_folders_come_from_the_device(log_file):
    """A made-up device that keeps music in folders with names that are not English."""
    from conftest import DEVICE_XML

    from openexpress.device.xmldevice import parse

    def datatype(name, path):
        return (f"<DataType><Name>{name}</Name><File><Location><Path>{path}</Path>"
                "<FileExtension>MP3</FileExtension></Location>"
                "<TransferDirection>InputOutput</TransferDirection></File></DataType>")

    xml = DEVICE_XML.replace("</MassStorageMode>", (
        datatype("MEDIA_TRACK", "Muzyka") + datatype("MEDIA_AUDIOBOOK", "Media/Hörbücher")
        + datatype("MEDIA_PLAYLIST", "Muzyka/Listy") + "</MassStorageMode>"))
    device = parse(xml.encode())
    before = "Cannot write Muzyka/Anna Jantar/Tyle słońca.mp3: refused"
    assert "Jantar" in applog.redact(before)  # not known to be a media folder yet

    applog.hide_device(device)
    assert applog.redact(before) == "Cannot write Muzyka/<file>: refused"
    assert applog.redact("copied to mtp://091e_51b4_X/Internal Storage/MUZYKA/ANNA/01.MP3") == (
        "copied to mtp://<device>/Internal Storage/MUZYKA/<file>")
    assert applog.redact("wrote Media/Hörbücher/Lem/Solaris 01.mp3") == "wrote Media/Hörbücher/<file>"
    assert applog.redact("wrote Muzyka/Listy/Bieganie rano.m3u8") == "wrote Muzyka/<file>"
    assert applog.redact("wrote Music/Anna/01.mp3") == "wrote Music/<file>"  # the fallback stays
    assert applog.redact("read Garmin/Activity/2026-10-12-18-00-00.fit, 2 files in Garmin/NewFiles"
                         ) == "read Garmin/Activity/2026-10-12-18-00-00.fit, 2 files in Garmin/NewFiles"
    assert applog.redact(f"device {device.unit_id}") == "device <unit>"

    applog.setup("cli")
    logging.getLogger("openexpress.test").warning("Cannot write Muzyka/Anna Jantar/01.mp3: refused")
    assert "Jantar" not in log_file.read_text()


# --- the run marker and the start line ----------------------------------------------------------


def markers(path):
    """The part between the brackets of every line: "cli[a3f1]" -> "a3f1"."""
    return [line.split()[2].split("[")[1].rstrip("]") for line in lines(path)]


def test_every_line_of_a_run_carries_the_same_marker(log_file):
    import re

    applog.setup("cli")
    log = logging.getLogger("openexpress.test")
    log.info("one")
    log.warning("two")
    applog.setup("watch")  # set up again in the same process, as the watcher does
    log.info("three")
    assert re.fullmatch(r"[0-9a-f]{4}", applog.RUN)
    assert markers(log_file) == [applog.RUN] * 3
    assert [line.split()[2] for line in lines(log_file)] == [
        f"cli[{applog.RUN}]", f"cli[{applog.RUN}]", f"watch[{applog.RUN}]"]


def test_two_runs_of_the_same_role_can_be_told_apart(log_file, monkeypatch):
    # As in the Flatpak, where both processes have PID 2.
    monkeypatch.setattr(os, "getpid", lambda: 2)
    for run in ("a3f1", "0042"):
        monkeypatch.setattr(applog, "RUN", run)
        applog.setup("cli")
        logging.getLogger("openexpress.test").info("hello")
        applog.close()
    assert markers(log_file) == ["a3f1", "0042"]
    assert all(" cli[2]" not in line for line in lines(log_file))


def test_the_filter_leaves_every_marker_alone():
    applog.hide(3987654321, "unit")  # a made-up unit ID is hidden meanwhile
    for n in range(0x10000):
        text = f"2026-10-13 20:20:56,433 cli[{n:04x}] INFO openexpress.cli: end: apps"
        assert applog.redact(text) == text


@pytest.mark.parametrize("flatpak, packaging", [(True, "Flatpak"), (False, "source")])
def test_start_line_says_what_runs_and_nothing_about_the_user(flatpak, packaging, log_file,
                                                              device_root, monkeypatch):
    import getpass
    import platform
    import socket
    from pathlib import Path

    from openexpress import __version__
    from openexpress.cli import main

    monkeypatch.setattr(paths, "in_flatpak", lambda: flatpak)
    assert main(["--path", str(device_root), "files"]) == 0
    start = next(line for line in lines(log_file) if " start: " in line)
    said = start.split(" start: ", 1)[1]
    assert said == (f"files --path (openExpress {__version__}, {packaging}, "
                    f"Python {platform.python_version()}, language en, pid {os.getpid()})")
    assert applog.redact(start) == start  # the filter changes nothing in it
    for private in (str(Path.home()), getpass.getuser(), socket.gethostname(),
                    str(device_root)):
        assert private not in start
    assert f" cli[{applog.RUN}] " in start


def test_report_log_keeps_the_markers(log_file, device_root, capsys):
    from openexpress.cli import main

    assert main(["--path", str(device_root), "info"]) == 0
    capsys.readouterr()
    assert main(["--path", str(device_root), "report", "--log"]) == 0
    log_part = capsys.readouterr().out.split("### Log", 1)[1]
    assert f" cli[{applog.RUN}] INFO openexpress.cli: start: info --path (" in log_part
