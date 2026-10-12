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
    ("profile number", "user profile 12345678 loaded", "12345678"),
    ("activity id", "activity 20123456789 already in Connect", "20123456789"),
    ("home folder", f"reading {os.path.expanduser('~')}/Music/x", os.path.expanduser("~")),
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


def test_program_works_without_a_log_file(tmp_path, monkeypatch, capsys):
    blocked = tmp_path / "state-is-a-file"
    blocked.write_text("")
    monkeypatch.setenv("XDG_STATE_HOME", str(blocked))
    assert applog.setup("cli") is None
    assert "no log file" in capsys.readouterr().err
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
