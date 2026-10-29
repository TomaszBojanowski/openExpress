"""Tests how the command line reports errors: a message for the user (errors.UserFacingError) is
told and logged in one line, without a traceback; only an unexpected error, a ProtoError among
them (as in the window), has its traceback in the log. The classes are found with ast; only
their modules are imported. All texts are made up."""

import ast
import importlib
import types
from pathlib import Path

import pytest
import requests

import openexpress

PACKAGE = Path(openexpress.__file__).parent


def _lines(log_file, words):
    return [line for line in log_file.read_text().splitlines() if words in line]


def _run(error, log_file):
    """`error` raised by a command; its exit code and what the log says."""
    from openexpress import applog, cli, service

    def command(_args):
        raise error

    applog.setup("cli")
    code = cli._run(types.SimpleNamespace(func=command, verbose=False), service, requests)
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
    assert "made-up words for the user" in capsys.readouterr().err
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
    assert capsys.readouterr().err.strip() == "Error: field 3 is cut short"
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
