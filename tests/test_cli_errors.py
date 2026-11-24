"""Tests how the command line reports errors: a message for the user (errors.UserFacingError) is
told and logged in one line, without a traceback; only an unexpected error, a ProtoError among
them (as in the window), has its traceback in the log. The classes are found with ast; only
their modules are imported. All texts are made up."""

import ast
import importlib
import types
from pathlib import Path

import pytest
from fakes import in_language

import openexpress

PACKAGE = Path(openexpress.__file__).parent


def _lines(log_file, words):
    return [line for line in log_file.read_text().splitlines() if words in line]


def _run(error, log_file):
    """`error` raised by a command; its exit code and what the log says."""
    from openexpress import applog, cli

    def command(_args):
        raise error

    applog.setup("cli")
    code = cli._run(types.SimpleNamespace(func=command, verbose=False))
    applog.close()
    return code, log_file.read_text()


def _messages_for_the_user() -> list[tuple[str, str]]:
    """Every class of the package that is, through its bases, a UserFacingError: (module,
    name), from the source alone."""
    bases: dict[str, tuple[str, list[str]]] = {}
    for path in sorted(PACKAGE.rglob("*.py")):
        module = ".".join(path.relative_to(PACKAGE.parent).with_suffix("").parts)
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ClassDef):
                names = [b.attr if isinstance(b, ast.Attribute) else getattr(b, "id", "")
                         for b in node.bases]
                bases[node.name] = (module, names)

    def of_the_user(name, seen=()):
        if name == "UserFacingError":
            return True
        if name not in bases or name in seen:
            return False
        return any(of_the_user(b, (*seen, name)) for b in bases[name][1])

    return sorted((module, name) for name, (module, _b) in bases.items()
                  if name != "UserFacingError" and of_the_user(name))


MESSAGES = _messages_for_the_user()


def test_the_classes_are_found():
    names = {name for _module, name in MESSAGES}
    assert {"PrimaryWearableError", "DiagnosticsError", "CacheError", "ApiError",
            "DeviceError", "SyncBusy"} <= names
    assert all(not module.endswith("__main__") for module, _name in MESSAGES)


@pytest.mark.parametrize("module,name", MESSAGES, ids=[name for _m, name in MESSAGES])
def test_every_message_for_the_user_is_logged_without_a_traceback(module, name, log_file,
                                                                   capsys):
    cls = getattr(importlib.import_module(module), name)
    error = cls.__new__(cls)
    Exception.__init__(error, "made-up words for the user")
    code, log = _run(error, log_file)
    assert code == 1
    err = capsys.readouterr().err
    assert "made-up words for the user" in err
    assert "bug in openExpress" not in err
    assert "unexpected error" not in log and "Traceback" not in log


def test_the_primary_wearable_refused_is_one_line_of_info(log_file, capsys):
    from openexpress import service

    code, _log = _run(service.PrimaryWearableError(
        "Test watch cannot be a primary wearable."), log_file)
    assert code == 1
    assert capsys.readouterr().err.strip() == (
        "Error: Test watch cannot be a primary wearable.")
    (line,) = _lines(log_file, "error: PrimaryWearableError: ")
    assert " INFO " in line and line.endswith("cannot be a primary wearable.")


def test_a_proto_error_is_unexpected_with_its_traceback(log_file, capsys):
    from openexpress.proto import ProtoError

    code, log = _run(ProtoError("field 3 is cut short"), log_file)
    assert code == 1
    assert capsys.readouterr().err.splitlines() == ["Error: field 3 is cut short", FAULT]
    (line,) = _lines(log_file, "unexpected error: ProtoError")
    assert " INFO " in line
    assert "Traceback (most recent call last)" in log


@pytest.mark.parametrize("error,said", [
    ("Unauthorized", "Error: "),
    ("ClockSkew", "Error: "),
    ("ApiError", "Server error: "),
])
def test_the_answers_of_a_server_keep_their_words(error, said, log_file, capsys):
    from openexpress import connect

    cls = getattr(connect, error)
    made = cls.__new__(cls)
    Exception.__init__(made, "made-up answer")
    made.body = None
    code, log = _run(made, log_file)
    assert code == 1
    assert capsys.readouterr().err.strip().startswith(said)
    assert "Traceback" not in log


@pytest.mark.parametrize(("language", "status", "said"), [
    ("en", 526, "Error: Connect IQ apps: server error (HTTP 526)"),
    ("pl", 526, "Błąd: Aplikacje Connect IQ: błąd serwera (HTTP 526)"),
    ("en", 404, "Server error: Connect IQ apps: HTTP 404"),
    ("pl", 404, "Błąd serwera: Aplikacje Connect IQ: HTTP 404")])
def test_a_server_error_is_not_called_one_twice_on_the_command_line(language, status, said,
                                                                     log_file, capsys):
    """An answer of 500 or above says "server error" itself: the command line puts "Error:"
    before it, not "Server error:" a second time; other answers keep "Server error:"."""
    from fakes import DEVICE_UNIT, make_client, response

    from openexpress import ciq
    from openexpress.connect import ApiError

    client = make_client(**{"express/appstore/rest/apps/downloads": response(status, b"")})
    client.verified_unit_id = DEVICE_UNIT
    device = types.SimpleNamespace(unit_id=DEVICE_UNIT, part_number="006-B0000-00")
    with in_language(language):
        with pytest.raises(ApiError) as raised:
            ciq.StoreApi(client).account_apps(device, "en-US")
        code, _log = _run(raised.value, log_file)
    assert code == 1
    assert capsys.readouterr().err.strip() == said


FAULT = ("This is a bug in openExpress. To report it, run openexpress report --log and follow "
         "what it prints.")


def test_an_error_nobody_foresaw_says_it_is_a_bug_in_openexpress_on_the_command_line(log_file,
                                                                                    capsys):
    code, _log = _run(RuntimeError("made-up failure"), log_file)
    assert code == 1
    assert capsys.readouterr().err.splitlines() == ["Error: made-up failure", FAULT]


def test_the_line_about_a_bug_in_polish(log_file, capsys):
    with in_language("pl"):
        code, _log = _run(RuntimeError("made-up failure"), log_file)
    assert code == 1
    assert capsys.readouterr().err.splitlines()[-1] == (
        "To błąd openExpressu. Aby go zgłosić, uruchom openexpress report --log i\u00a0postępuj "
        "według tego, co wypisze.")


def test_a_file_the_system_could_not_write_says_no_fault(log_file, capsys):
    code, _log = _run(OSError(28, "No space left on device"), log_file)
    assert code == 1
    assert "bug in openExpress" not in capsys.readouterr().err
