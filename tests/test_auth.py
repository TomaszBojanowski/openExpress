import stat
import types
from dataclasses import asdict

import pytest

from openexpress import paths
from openexpress.auth import store
from openexpress.auth.oauth1 import authorization_header
from openexpress.auth.sso import ServiceTicket, ticket_from_url
from openexpress.auth.store import ConnectAuth
from openexpress.settings import _utf16_strings, extract_consumer_from_dll


def test_oauth1_known_vector():
    # Example from the Twitter docs "Creating a signature" (OAuth 1.0a, HMAC-SHA1).
    h = authorization_header(
        "POST",
        "https://api.twitter.com/1.1/statuses/update.json?include_entities=true",
        "xvz1evFS4wEEPTGEFPHBog",
        "kAcSOqF21Fu85e7zjz7ZN2U4ZRhfV3WpwPAoE3Z7kBw",
        "370773112-GmHxMAgYyLbNEtIKZeRNFsMKPR9EyMZeS9weJAEb",
        "LswwdoUaIvS8ltyTt5jkRh4J50vUPVVHtR2YPi5kE",
        form_params=[("status", "Hello Ladies + Gentlemen, a signed OAuth request!")],
        nonce="kYjzVBB8Y0ZFabxSWbWovY3uYSQ2pTgmZeNu2VS4cg",
        timestamp=1318622958,
    )
    assert 'oauth_signature="hCtSmYh%2BiHYCEqBWrE7C7hYmtUk%3D"' in h


def test_ticket_from_url():
    st = ticket_from_url("https://sso.garmin.com/sso/embed?ticket=ST-123-abc&service=https%3A%2F%2Fx")
    assert st.ticket == "ST-123-abc"
    assert st.service == "https://x"
    st = ticket_from_url("https://example.com/done?ticket=ST-9")
    assert st.service == "https://example.com/done"
    assert ticket_from_url("https://sso.garmin.com/mobile/sso/pl_PL/sign-in") is None


def test_the_repr_of_a_login_and_a_ticket_shows_no_secret():
    auth = ConnectAuth("tok-made-up-1", "sec-made-up-2", "production", {"username": "someone"})
    st = ServiceTicket("ST-0123-AbCdEf-cas", "https://sso.example/embed")
    for text in (repr(auth), str(auth), repr(st), str(st)):
        assert "tok-made-up-1" not in text and "sec-made-up-2" not in text
        assert "ST-0123" not in text and "AbCdEf" not in text
    assert "production" in repr(auth) and "sso.example" in repr(st)  # the rest stays readable
    # Only the text changes: the fields are still compared and saved as before.
    assert auth == ConnectAuth("tok-made-up-1", "sec-made-up-2", "production", {"username": "someone"})
    assert asdict(auth)["token"] == "tok-made-up-1" and asdict(auth)["token_secret"] == "sec-made-up-2"
    assert st == ServiceTicket("ST-0123-AbCdEf-cas", "https://sso.example/embed")


# --- the saved login: private on disk, and gone after logging out ------------------------------


def saved_login() -> ConnectAuth:
    auth = ConnectAuth("tok-made-up-1", "sec-made-up-2", "production",
                       {"username": "someone", "userprofilepk": 1, "garminguid": "g-1"})
    store.save(auth)
    return auth


def login_file():
    return paths.config_dir() / "connect_auth.json"


def test_the_login_is_saved_for_the_owner_only_and_read_back_whole():
    assert not login_file().parent.exists()  # a new installation: the folder is made too
    auth = saved_login()
    assert login_file().parent.is_dir()
    assert stat.S_IMODE(login_file().stat().st_mode) == 0o600
    assert sorted(p.name for p in login_file().parent.iterdir()) == ["connect_auth.json"]  # no .tmp
    back = store.load()
    assert (back.token, back.token_secret, back.environment, back.account) == (
        auth.token, auth.token_secret, auth.environment, auth.account)


def test_logout_on_the_command_line_removes_the_saved_login(capsys):
    from openexpress.cli import main

    saved_login()
    assert main(["logout"]) == 0
    assert capsys.readouterr().out == "Logged out (local token removed).\n"
    assert not login_file().exists()
    with pytest.raises(store.NotLoggedIn):
        store.load()
    # Once more, with nothing saved: no error, and it says so.
    assert main(["logout"]) == 0
    assert capsys.readouterr().out == "No saved token found.\n"


def test_logout_in_the_window_removes_the_saved_login():
    from openexpress.gui.app import MainWindow

    saved_login()
    said, refreshed = [], []
    win = types.SimpleNamespace(session=object(), toast=said.append,
                                refresh_device=lambda: refreshed.append(True))
    MainWindow.logout(win)
    assert not login_file().exists()
    with pytest.raises(store.NotLoggedIn):
        store.load()
    assert said == ["Logged out"] and win.session is None and refreshed == [True]


def test_extract_consumer_from_dll(tmp_path):
    def u(s):
        return s.encode("utf-16-le") + b"\x00\x00"

    blob = b"\x01junk" + u("https://connectapi.garmin.cn/") + u(
        "11111111-2222-3333-4444-555555555555") + u("AbCdEfGhIjKlMnOpQrStUvWxYz0123") + b"\x00"
    dll = tmp_path / "Networking.dll"
    dll.write_bytes(blob)
    assert len(_utf16_strings(blob)) == 3
    c = extract_consumer_from_dll(dll)
    assert c.key == "11111111-2222-3333-4444-555555555555"
    assert c.secret == "AbCdEfGhIjKlMnOpQrStUvWxYz0123"
