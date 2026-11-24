"""The program's own files: written privately, and damaged ones said clearly."""

import json
import os
import stat

import pytest

from openexpress import i18n, paths
from openexpress import settings as settings_mod
from openexpress.auth import store
from openexpress.settings import Settings, SetupError

# --- private files --------------------------------------------------------------------

def test_write_private(tmp_path, monkeypatch):
    target = tmp_path / "d" / "secret.json"
    stale = tmp_path / "d" / "secret.json.tmp"
    stale.parent.mkdir()
    stale.write_text("old")
    os.chmod(stale, 0o644)
    paths.write_private(target, "data")
    assert target.read_text() == "data"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600

    def boom(*a):
        raise OSError("fail")
    monkeypatch.setattr(paths.os, "replace", boom)
    with pytest.raises(OSError):
        paths.write_private(target, "new")
    assert target.read_text() == "data"
    assert sorted(p.name for p in target.parent.iterdir()) == ["secret.json", "secret.json.tmp"]


def test_write_private_writes_bytes_as_they_are(tmp_path):
    """A copy of a file from the device: bytes, not text."""
    target = tmp_path / "d" / "a.fit"
    data = bytes(range(256)) + b"\r\n"
    paths.write_private(target, data)
    assert target.read_bytes() == data
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


# --- damaged config files -------------------------------------------------------------

def test_damaged_settings(xdg):
    path = paths.config_dir() / "settings.json"
    path.parent.mkdir(parents=True)
    for content in ("{bad", "[1, 2]"):
        path.write_text(content)
        with pytest.raises(SetupError, match="settings.json"):
            Settings.load()
        assert i18n._configured_language() in (None, "en")  # OPENEXPRESS_LANG may be set


def test_configured_language_ignores_non_dict(xdg, monkeypatch):
    monkeypatch.delenv("OPENEXPRESS_LANG", raising=False)
    path = paths.config_dir() / "settings.json"
    path.parent.mkdir(parents=True)
    path.write_text("[1]")
    assert i18n._configured_language() is None
    path.write_text(json.dumps({"ui_language": "pl"}))
    assert i18n._configured_language() == "pl"


def test_damaged_login(xdg):
    p = paths.config_dir() / "connect_auth.json"
    p.parent.mkdir(parents=True)
    for content in ("{bad", "[]", json.dumps({"token": "x"})):
        p.write_text(content)
        with pytest.raises(store.NotLoggedIn, match="damaged"):
            store.load()


def test_damaged_consumer(xdg):
    p = settings_mod.consumer_path()
    p.parent.mkdir(parents=True)
    for content in ("{bad", "[]", json.dumps({"key": "x"})):
        p.write_text(content)
        with pytest.raises(SetupError):
            settings_mod.load_consumer()
