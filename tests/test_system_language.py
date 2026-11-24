"""The language the system prefers, and the one chosen over it."""

import pytest

from openexpress import i18n


@pytest.fixture
def sys_lang(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("OPENEXPRESS_LANG", raising=False)
    for v in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        monkeypatch.delenv(v, raising=False)
    yield monkeypatch
    i18n.setup("en")


@pytest.mark.parametrize("env,expected", [
    ({"LANGUAGE": "en_US:pl"}, "en"),
    ({"LANGUAGE": "pl_PL:en"}, "pl"),
    ({"LANGUAGE": "sv:pl"}, "pl"),  # Swedish: a language openExpress is not translated into
    ({"LANG": "pl_PL.UTF-8"}, "pl"),
    ({"LC_ALL": "C", "LANG": "pl_PL.UTF-8"}, "en"),
    ({"LANG": "POSIX"}, "en"),
    ({"LANG": "sv_SE.UTF-8"}, "en"),
    ({}, "en"),
])
def test_system_language(sys_lang, env, expected):
    for k, v in env.items():
        sys_lang.setenv(k, v)
    assert i18n.setup() == expected
    assert (i18n._("Install") == "Install") == (expected == "en")


def test_explicit_language_wins(sys_lang):
    sys_lang.setenv("LANGUAGE", "en_US:pl")
    assert i18n.setup("pl") == "pl"
    sys_lang.setenv("OPENEXPRESS_LANG", "pl")
    assert i18n.setup() == "pl"
