"""The command line names the account only masked: its output is pasted into reports. A user
name shows its first and last letters ("t…b"), an e-mail address its first letters
("t…@e…"); the window shows the account whole. All names here are made up."""

import types

import pytest

from openexpress import cli
from openexpress.auth import store
from openexpress.cli import main


@pytest.mark.parametrize("name, shown", [
    ("tomek_b", "t…b"), ("tb", "t…"), ("t", "t…"), ("tomek@example.com", "t…@e…"),
    ("@example.com", "…@e…"),
])
def test_masked(name, shown):
    assert store.masked(name) == shown


def test_the_auth_says_its_account_masked_for_the_command_line():
    auth = store.ConnectAuth("t", "ts", account={"username": "tomek_b",
                                                 "emailaddress": "tomek@example.com"})
    assert auth.display == "tomek_b"  # the window's
    assert auth.masked_display == "t…b"
    assert store.ConnectAuth("t", "ts", account={"emailaddress": "tomek@example.com"}
                             ).masked_display == "t…@e…"
    assert store.ConnectAuth("t", "ts").masked_display == "(unknown account)"


@pytest.mark.parametrize("account, whole, shown", [
    ({"username": "tomek_b", "emailaddress": "tomek@example.com"}, "tomek_b", "t…b"),
    ({"username": "", "emailaddress": "tomek@example.com"}, "tomek@example.com", "t…@e…"),
    ({"emailaddress": "tomek@example.com"}, "tomek@example.com", "t…@e…"),
    ({"username": None, "emailaddress": ""}, "(unknown account)", "(unknown account)"),
    ({}, "(unknown account)", "(unknown account)"),
], ids=["user name", "empty user name", "address only", "both empty", "neither"])
def test_the_account_is_named_by_its_user_name_else_its_address(account, whole, shown):
    auth = store.ConnectAuth("t", "ts", account=account)
    assert (auth.display, auth.masked_display) == (whole, shown)


def test_whoami_masks_the_account(xdg, capsys, monkeypatch):
    store.save(store.ConnectAuth("t", "ts", account={"username": "tomek_b"}))
    monkeypatch.setattr(cli, "make_client", lambda settings, need_auth: types.SimpleNamespace(
        user_profile=lambda: {"username": "tomek_b", "emailaddress": "tomek@example.com"}))
    assert main(["whoami", "--check"]) == 0
    out = capsys.readouterr().out
    assert out.splitlines() == ["Account: t…b (environment production)",
                                "Token works: t…b / t…@e…"]
    assert "tomek" not in out and "example.com" not in out


def test_login_masks_the_account(xdg, capsys, monkeypatch):
    from openexpress.auth import sso

    monkeypatch.setattr(sso, "login_manual", lambda settings: types.SimpleNamespace(
        ticket="ST-made-up", service="https://made.up/"))
    client = types.SimpleNamespace(
        auth=None, exchange_ticket=lambda ticket, service: ("token", "secret"),
        user_profile=lambda: {"username": "tomek_b", "emailaddress": "tomek@example.com"})
    monkeypatch.setattr(cli, "make_client", lambda settings, need_auth: client)
    assert main(["login", "--manual"]) == 0
    out = capsys.readouterr().out
    assert "Signed in as t…b." in out and "tomek" not in out
    assert store.load().display == "tomek_b"  # kept whole: the window shows it
