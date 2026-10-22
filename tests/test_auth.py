import gc
import os
import stat
import sys
import types
import weakref
from dataclasses import asdict

import pytest

from openexpress import applog, paths, sync
from openexpress.auth import sso, store
from openexpress.auth.oauth1 import authorization_header
from openexpress.auth.sso import ServiceTicket, ticket_from_url
from openexpress.auth.store import ConnectAuth
from openexpress.settings import Settings, _utf16_strings, extract_consumer_from_dll


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
                                refresh_device=lambda **_kw: refreshed.append(True))
    MainWindow.logout(win)
    assert not login_file().exists()
    with pytest.raises(store.NotLoggedIn):
        store.load()
    assert said == ["Logged out"] and win.session is None and refreshed == [True]


@pytest.fixture
def usual_umask():
    """The most common umask: a folder made without a mode of its own would be 0755."""
    old = os.umask(0o022)
    yield
    os.umask(old)


def mode(path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_a_new_folder_for_the_login_is_the_owners_alone(usual_umask, tmp_path, monkeypatch):
    # ~/.config is there already, as the system made it (here: readable by everyone).
    config = tmp_path / "home-config"
    config.mkdir()
    config.chmod(0o755)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config))
    saved_login()
    assert mode(login_file().parent) == 0o700
    assert mode(config) == 0o755  # left as it was
    # No ~/.config at all: it gets the usual mode, only the app's own folder is private.
    config = tmp_path / "no-home" / "config"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config))
    saved_login()
    assert mode(config) == 0o755 and mode(config.parent) == 0o755
    assert mode(login_file().parent) == 0o700


def test_the_data_and_the_log_get_private_folders_but_old_folders_keep_theirs(usual_umask,
                                                                             log_file):
    login_file().parent.mkdir(parents=True)
    login_file().parent.chmod(0o755)  # made by an older version: never changed
    saved_login()
    assert mode(login_file().parent) == 0o755
    # What came from the device (the folders are named after it) and the log.
    sync.SyncState(4000000001).save()
    assert mode(paths.data_dir()) == 0o700
    assert applog.setup("cli") == log_file
    assert mode(log_file.parent) == 0o700


# --- the sign-in window: WebKit stands in, nothing is shown or loaded --------------------------

_FAKE_VIEW = []  # a GObject type can be made only once per process


def _fake_view_type():
    if not _FAKE_VIEW:
        import gi

        gi.require_version("Gtk", "4.0")
        from gi.repository import GObject, Gtk

        class FakeWebView(Gtk.Widget):
            """The signals and the address of WebKit.WebView; `load_uri` fetches nothing."""

            __gsignals__ = {
                "decide-policy": (GObject.SignalFlags.RUN_LAST, bool, (object, int)),
                "load-changed": (GObject.SignalFlags.RUN_FIRST, None, (int,)),
                "load-failed": (GObject.SignalFlags.RUN_LAST, bool, (int, str, object)),
                "web-process-terminated": (GObject.SignalFlags.RUN_LAST, None, (int,)),
            }
            uri = GObject.Property(type=str, default="")

            def __init__(self, network_session=None):
                super().__init__()
                self.loaded = []

            def get_uri(self):
                return self.uri or None

            def load_uri(self, uri):
                self.loaded.append(uri)

        _FAKE_VIEW.append(FakeWebView)
    return _FAKE_VIEW[0]


@pytest.fixture
def fake_webkit(monkeypatch):
    import gi
    import gi.repository

    webkit = types.ModuleType("gi.repository.WebKit")
    webkit.WebView = _fake_view_type()
    webkit.NetworkSession = types.SimpleNamespace(new_ephemeral=lambda: None)
    webkit.languages = []  # every list given to WebContext.set_preferred_languages
    context = types.SimpleNamespace(set_preferred_languages=webkit.languages.append)
    webkit.WebContext = types.SimpleNamespace(get_default=lambda: context)
    webkit.PolicyDecisionType = types.SimpleNamespace(NAVIGATION_ACTION=0, NEW_WINDOW_ACTION=1,
                                                      RESPONSE=2)
    real = gi.require_version
    monkeypatch.setattr(gi, "require_version",
                        lambda name, version: None if name == "WebKit" else real(name, version))
    monkeypatch.setitem(sys.modules, "gi.repository.WebKit", webkit)
    monkeypatch.setattr(gi.repository, "WebKit", webkit, raising=False)
    return webkit


TICKET_URL = "https://sso.garmin.com/sso/embed?ticket=ST-0123-AbCdEf-cas&service=https%3A%2F%2Fx"


@pytest.mark.needs_display
def test_the_sign_in_window_does_not_keep_itself_alive(fake_webkit):
    """A handler of a widget must not hold the widget or its siblings: once the window is closed
    and nobody holds them, the view and the window go by reference counting alone."""
    got, closed = [], []
    gc.collect()
    gc.disable()
    try:
        win = sso.create_login_window(Settings(), got.append)
        win.realize()  # GTK closes only a realized window; it is still not shown
        win.connect("close-request", lambda _w: closed.append(True) or False)
        view = win.get_child()
        assert view.loaded == [sso.sign_in_url(Settings())]  # the only page asked for
        view.uri = TICKET_URL  # the browser arrives at the address with the ticket
        assert got == [ServiceTicket("ST-0123-AbCdEf-cas", "https://x")]  # told once
        assert closed == [True]  # and the window was closed
        alive = [weakref.ref(win), weakref.ref(view)]
        del win, view
        assert [ref() for ref in alive] == [None, None]
    finally:
        gc.enable()


@pytest.mark.needs_display
def test_the_sign_in_window_takes_the_ticket_from_a_navigation_and_tells_a_cancel(fake_webkit):
    class Decision:
        def __init__(self, uri):
            self.uri, self.ignored = uri, False

        def get_navigation_action(self):
            return self

        def get_request(self):
            return self

        def get_uri(self):
            return self.uri

        def ignore(self):
            self.ignored = True

    got = []
    win = sso.create_login_window(Settings(), got.append)
    page = Decision("https://sso.garmin.com/mobile/sso/en/sign-in")
    assert win.get_child().emit("decide-policy", page, 0) is False and not page.ignored
    done = Decision(TICKET_URL)
    assert win.get_child().emit("decide-policy", done, 0) is True and done.ignored
    assert got == [ServiceTicket("ST-0123-AbCdEf-cas", "https://x")]
    # Closed by the user before signing in: told once, with None.
    got = []
    win = sso.create_login_window(Settings(), got.append)
    win.realize()
    win.close()
    assert got == [None]


@pytest.mark.needs_display
@pytest.mark.parametrize("names, expected", [
    (["de_DE.UTF-8", "de_DE", "de.UTF-8", "de", "C"], [["de_DE", "de"]]),
    (["sr_RS.UTF-8@latin", "sr_RS@latin", "sr_RS.UTF-8", "sr_RS", "sr", "C"], [["sr_RS", "sr"]]),
    (["C"], []),  # nothing to ask for: WebKit keeps its own choice
])
def test_the_sign_in_browser_asks_for_the_languages_of_the_system(fake_webkit, monkeypatch,
                                                                    names, expected):
    """Garmin's pages that do not take the language from the address read Accept-Language."""
    from openexpress import i18n

    monkeypatch.setattr(i18n, "system_language_names", lambda: list(names), raising=False)
    monkeypatch.setattr(i18n, "chosen_language", lambda: None, raising=False)
    sso.create_login_window(Settings(), [].append)
    assert fake_webkit.languages == expected


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


def test_the_repr_of_the_installer_key_shows_no_secret():
    """The key, the secret and the salt of the map addresses come from the user's own
    installer (setup --from-installer): never in a repr() that may reach the log."""
    from dataclasses import asdict

    from openexpress.settings import Consumer

    c = Consumer("key-made-up-1", "sec-made-up-2", "salt-made-up-3")
    for text in (repr(c), str(c)):
        for secret in ("key-made-up-1", "sec-made-up-2", "salt-made-up-3"):
            assert secret not in text
    # Only the text changes: compared and saved as before.
    assert c == Consumer("key-made-up-1", "sec-made-up-2", "salt-made-up-3")
    assert asdict(c) == {"key": "key-made-up-1", "secret": "sec-made-up-2",
                         "url_salt": "salt-made-up-3"}
