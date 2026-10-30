"""Logging in needs Garmin Express's application key, read from its installer for Windows: until
it is chosen the window's account row offers Choose Installer… instead of Log In and, asked to
log in all the same, the window says what is needed and offers the installer without opening
Garmin's sign-in; the command line says what to run and opens nothing. Neither speaks of OAuth:
that stays in the log. No real key here."""
import logging
import types

import pytest
from fakes import window as stand_in
from fakes import window_methods

from openexpress import i18n, settings

ROW = {"en": "First choose the Garmin Express installer for Windows: openExpress reads the "
             "application key from it",
       "pl": "Najpierw wskaż instalator Garmin Express dla Windows: openExpress odczyta "
             "z niego klucz aplikacji"}
HEADING = {"en": "Choose the Garmin Express installer first",
           "pl": "Najpierw wskaż instalator Garmin Express"}
NO_KEY_IN_CONNECT = {"en": "No key needs to be created in Garmin Connect.",
                     "pl": "W Garmin Connect nie trzeba tworzyć żadnego klucza."}
CHOOSE = {"en": "Choose Installer…", "pl": "Wybierz instalator…"}


def _key_saved():
    settings.save_consumer(settings.Consumer("made-up-key", "made-up-secret"))


@pytest.fixture(params=["en", "pl"])
def language(request):
    i18n.setup(request.param)
    yield request.param
    i18n.setup("en")


def _account_row():
    from openexpress.gui.app import MainWindow

    calls = []
    win = window_methods(stand_in(choose_dll=lambda: calls.append("installer"),
                                  login=lambda: calls.append("login"),
                                  logout=lambda: calls.append("logout")),
                         ["_button", "_account_action"])
    MainWindow._account_group(win)
    row, button = win._account_row
    return row, button, calls


@pytest.mark.needs_display
def test_until_the_installer_is_chosen_the_account_row_offers_it(language):
    row, button, calls = _account_row()
    assert row.get_title() == {"en": "Not logged in", "pl": "Nie zalogowano"}[language]
    assert row.get_subtitle() == ROW[language]
    assert button.get_label() == CHOOSE[language]
    button.emit("clicked")
    assert calls == ["installer"]


@pytest.mark.needs_display
def test_with_the_key_the_account_row_offers_log_in():
    _key_saved()
    row, button, calls = _account_row()
    assert (row.get_title(), row.get_subtitle() or "", button.get_label()) == (
        "Not logged in", "", "Log In")
    button.emit("clicked")
    assert calls == ["login"]


def _log_in(in_the_window, monkeypatch):
    from openexpress.auth import sso
    from openexpress.gui import app

    opened, asked, calls = [], [], []
    monkeypatch.setattr(sso, "create_login_window", lambda *a, **kw: opened.append(1)
                        or types.SimpleNamespace(present=lambda: None))
    in_the_window("confirm", lambda parent, name, heading, body, *answers, **kw:
                  asked.append((name, heading, body, answers)))
    win = stand_in(choose_dll=lambda: calls.append("installer"), get_application=lambda: None)
    app.MainWindow.login(win)
    return opened, asked, calls


def test_asked_to_log_in_without_the_key_it_offers_the_installer(language, in_the_window,
                                                                  monkeypatch, caplog):
    with caplog.at_level(logging.INFO, logger="openexpress"):
        opened, asked, calls = _log_in(in_the_window, monkeypatch)
    ((name, heading, body, answers),) = asked
    assert opened == []  # Garmin's sign-in not opened
    assert name == "login-needs-installer" and heading == HEADING[language]
    assert NO_KEY_IN_CONNECT[language] in body and "GarminExpress.exe" in body
    assert "OAuth" not in heading + body
    (answer,) = answers
    assert answer.label == CHOOSE[language]
    answer.run()
    assert calls == ["installer"]
    assert "no OAuth1 consumer key" in caplog.text  # the technical words: in the log only


def test_with_the_key_log_in_opens_the_sign_in(in_the_window, monkeypatch):
    _key_saved()
    opened, asked, _calls = _log_in(in_the_window, monkeypatch)
    assert opened == [1] and asked == []


SIGN = {"en": "openExpress has no application key yet: first choose the Garmin Express installer "
              "for Windows (in the window: Preferences, Setup; on the command line: openexpress "
              "setup --from-installer GarminExpress.exe).",
        "pl": "openExpress nie ma jeszcze klucza aplikacji: najpierw wskaż instalator Garmin "
              "Express dla Windows (w oknie: Preferencje, Konfiguracja; w wierszu "
              "poleceń: openexpress setup --from-installer GarminExpress.exe)."}


def test_a_request_without_the_key_says_what_to_do(language, caplog):
    """A request signed without the key (a login whose key went away meanwhile): the words for
    the user, the technical ones in the log."""
    from openexpress.connect import ApiError, GarminClient, NoApplicationKey

    client = GarminClient(settings.Settings(), None, None, object())
    with caplog.at_level(logging.WARNING, logger="openexpress"), \
            pytest.raises(NoApplicationKey) as refused:
        client._sign("GET", "https://connectapi.example/made-up")
    assert isinstance(refused.value, ApiError)  # handled where any answer of a server is
    assert str(refused.value) == SIGN[language]
    assert "no OAuth1 consumer key" in caplog.text


def test_the_command_line_says_what_to_run_and_opens_no_sign_in(monkeypatch, capsys):
    from openexpress.auth import sso
    from openexpress.cli import main

    monkeypatch.setattr(sso, "login_with_webview",
                        lambda s: pytest.fail("Garmin's sign-in opened without the key"))
    assert main(["login"]) == 1
    said = " ".join(capsys.readouterr().err.split())
    assert "openexpress setup --from-installer /path/to/GarminExpress.exe" in said
    assert "no key needs to be created in Garmin Connect" in said and "OAuth" not in said
