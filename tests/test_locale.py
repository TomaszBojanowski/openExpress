"""The language Garmin's pages and answers come in: the user's choice (`setup --locale`), else
the language of the system, as Garmin Express does with the culture of Windows."""

import json
import logging
import uuid

import pytest
import requests

from openexpress import i18n, paths
from openexpress.auth import sso
from openexpress.cli import main
from openexpress.connect import GarminClient
from openexpress.settings import Settings

SIGN_IN = "https://sso.garmin.com/mobile/sso/{}/sign-in?clientId=GarminExpress_WIN_Default"
GERMAN = ["de_DE.UTF-8", "de_DE", "de.UTF-8", "de", "C"]  # as GLib lists them


def system(monkeypatch, names, program=None):
    """The languages of the system and the one chosen for the program (None: not chosen; the
    tests choose English for the program, see conftest)."""
    monkeypatch.setattr(i18n, "system_language_names", lambda: list(names), raising=False)
    monkeypatch.setattr(i18n, "chosen_language", lambda: program, raising=False)


def settings_file():
    return paths.config_dir() / "settings.json"


@pytest.mark.parametrize("names, expected", [
    (GERMAN, "de_DE"),
    (["en_GB.UTF-8", "en_GB", "en.UTF-8", "en", "C"], "en_GB"),
    (["pl_PL.UTF-8", "pl_PL", "pl.UTF-8", "pl", "C"], "pl_PL"),
    (["pl_PL.UTF-8@euro", "pl_PL@euro", "pl_PL.UTF-8", "pl_PL", "pl", "C"], "pl_PL"),
    (["de-DE"], "de_DE"),
    # A language without a country: the obvious country for a few, the rest as it is.
    (["de", "C"], "de_DE"), (["en"], "en_US"), (["pl"], "pl_PL"), (["fr"], "fr_FR"),
    (["nb"], "nb"),
    (["C"], "en_US"), (["POSIX"], "en_US"), (["C.UTF-8", "C"], "en_US"), ([""], "en_US"),
    ([], "en_US"),
])
def test_the_sign_in_page_asks_for_the_language_of_the_system(monkeypatch, names, expected):
    system(monkeypatch, names)
    assert sso.sign_in_url(Settings()) == SIGN_IN.format(expected)


@pytest.mark.parametrize("lang, expected", [("de_DE.UTF-8", "de_DE"), ("", "en_US")])
def test_the_language_of_the_system_is_read_from_the_environment(monkeypatch, lang, expected):
    for var in ("LANGUAGE", "LC_ALL", "LC_MESSAGES"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("LANG", lang)
    monkeypatch.setattr(i18n, "chosen_language", lambda: None, raising=False)
    assert Settings().locale == expected


def test_without_glib_the_locale_of_the_c_library_is_used(monkeypatch):
    import locale

    monkeypatch.setattr(i18n, "_glib", lambda: None, raising=False)
    monkeypatch.setattr(i18n, "chosen_language", lambda: None, raising=False)
    monkeypatch.setattr(locale, "getlocale", lambda *a: ("de_DE", "UTF-8"))
    assert Settings().locale == "de_DE"
    monkeypatch.setattr(locale, "getlocale", lambda *a: (None, None))
    assert Settings().locale == "en_US"


@pytest.mark.parametrize("program, names, expected", [
    ("en", ["pl_PL.UTF-8", "pl_PL", "pl", "C"], "en_US"),
    ("pl", GERMAN, "pl_PL"),
    ("pl_PL.UTF-8", GERMAN, "pl_PL"),
])
def test_a_language_chosen_for_the_program_comes_before_the_system(monkeypatch, program, names,
                                                                    expected):
    """An English window on a Polish system signs in on an English page, not a Polish one."""
    system(monkeypatch, names, program=program)
    assert sso.sign_in_url(Settings()) == SIGN_IN.format(expected)


def test_the_language_chosen_in_preferences_reaches_garmin(monkeypatch):
    monkeypatch.setattr(i18n, "system_language_names", lambda: ["pl_PL.UTF-8", "pl_PL", "pl"])
    monkeypatch.delenv("OPENEXPRESS_LANG")
    paths.write_private(settings_file(), json.dumps({"ui_language": "en"}))
    try:
        i18n.setup()
        assert Settings.load().locale == "en_US"
        assert sso.browser_languages() == ["en_US", "en"]
        paths.write_private(settings_file(), json.dumps({"ui_language": ""}))  # "system"
        i18n.setup()
        assert Settings.load().locale == "pl_PL"
    finally:
        i18n.setup("en")  # as conftest left it


def test_garmin_s_servers_are_told_the_language_of_the_system(monkeypatch):
    system(monkeypatch, GERMAN)
    sent = []

    def request(self, method, url, **kw):
        sent.append(kw.get("headers") or {})
        r = requests.Response()
        r.status_code, r._content = 200, b"{}"
        return r

    monkeypatch.setattr(requests.Session, "request", request)
    client = GarminClient(Settings())
    client.upload_settings(1234567890)
    client._omt("GET", "Rce/x", "x")
    assert [(h["Garmin-Client-LocaleCode"], h["Accept-Language"]) for h in sent] == [
        ("de_DE", "de-DE"), ("de-DE", "de-DE")]


def test_a_locale_saved_by_an_older_version_gives_way_to_the_system(monkeypatch, caplog):
    """Up to 0.6.1 every settings.json got "locale": "pl_PL" on the first start, chosen or not."""
    system(monkeypatch, GERMAN)
    paths.write_private(settings_file(), json.dumps({
        "environment": "production", "locale": "pl_PL", "client_guid": str(uuid.uuid4()),
        "ui_language": "en"}))
    with caplog.at_level(logging.INFO, logger="openexpress"):
        settings = Settings.load()
    assert settings.locale == "de_DE"
    lines = [r for r in caplog.records if "locale" in r.getMessage()]
    assert len(lines) == 1 and lines[0].levelno == logging.INFO
    assert "pl_PL" in lines[0].getMessage()
    saved = json.loads(settings_file().read_text())
    assert "locale" not in saved and "chosen_locale" not in saved
    assert saved["ui_language"] == "en"  # the other keys stay
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="openexpress"):
        Settings.load()
    assert not [r for r in caplog.records if "locale" in r.getMessage()]  # said once


def test_without_a_choice_no_locale_is_saved(monkeypatch):
    system(monkeypatch, GERMAN)
    Settings.load().save()
    saved = json.loads(settings_file().read_text())
    assert "locale" not in saved and "chosen_locale" not in saved


def test_a_locale_chosen_with_setup_wins_and_is_kept(monkeypatch, capsys):
    system(monkeypatch, GERMAN)
    monkeypatch.setattr(i18n, "chosen_language", lambda: "pl", raising=False)  # also beats this
    assert main(["setup", "--locale", "fr_FR"]) == 0
    assert "locale: fr_FR" in capsys.readouterr().out
    assert Settings.load().locale == "fr_FR"
    assert sso.sign_in_url(Settings.load()) == SIGN_IN.format("fr_FR")
    saved = json.loads(settings_file().read_text())
    assert saved["chosen_locale"] == "fr_FR" and "locale" not in saved


# --- a locale the system has and the Flatpak's runtime has not (#10) --------------------------

CATEGORIES = ("LC_ALL", "LC_CTYPE", "LC_MESSAGES", "LC_NUMERIC", "LC_TIME", "LC_COLLATE",
              "LC_MONETARY")


def danish_system(monkeypatch, language=None):
    """LANG=da_DK.UTF-8 and nothing else of the locale set; LANGUAGE as given."""
    for var in CATEGORIES:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("LANG", "da_DK.UTF-8")
    if language is None:
        monkeypatch.delenv("LANGUAGE", raising=False)
    else:
        monkeypatch.setenv("LANGUAGE", language)
    monkeypatch.setattr(i18n, "chosen_language", lambda: None, raising=False)


def not_in_the_runtime(monkeypatch):
    """setlocale cannot take the locale of the environment (as in a runtime without it); the
    others (C.UTF-8) as they are."""
    import locale

    real = locale.setlocale

    def setlocale(category, value=None):
        if value == "":
            raise locale.Error("unsupported locale setting")
        return real(category, value)

    monkeypatch.setattr(locale, "setlocale", setlocale)


@pytest.mark.parametrize("language", ["en_US:en", None])
def test_a_locale_the_runtime_lacks_gives_c_utf_8_to_the_program(monkeypatch, language):
    """GTK would fall back to "C" (ASCII), and GLib could then convert no name with a
    character outside ASCII: flatpak-spawn failed and the sign-in window took the program
    down (#10). LC_ALL=C.UTF-8 for the program and the processes it starts; LANGUAGE stays,
    Python's own locale stays as it was, and so does the language for Garmin."""
    import locale
    import os

    danish_system(monkeypatch, language)
    before = locale.setlocale(locale.LC_ALL)
    for_garmin = Settings().locale
    not_in_the_runtime(monkeypatch)
    assert i18n.ensure_utf8_locale() == "locale da_DK.UTF-8 not available, using C.UTF-8"
    assert os.environ["LC_ALL"] == "C.UTF-8" and os.environ["LANG"] == "da_DK.UTF-8"
    assert os.environ.get("LANGUAGE") == language
    assert locale.setlocale(locale.LC_ALL) == before
    assert Settings().locale == for_garmin == ("en_US" if language else "da_DK")


def test_a_locale_that_is_there_changes_nothing(monkeypatch):
    import os

    for var in (*CATEGORIES, "LANGUAGE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("LANG", "C.UTF-8")  # in every runtime and on every system
    assert i18n.ensure_utf8_locale() is None
    assert "LC_ALL" not in os.environ


def test_a_locale_that_is_not_utf_8_gives_c_utf_8(monkeypatch):
    import locale
    import os

    for var in (*CATEGORIES, "LANGUAGE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("LANG", "C")
    real = locale.nl_langinfo
    monkeypatch.setattr(locale, "nl_langinfo", lambda item: "ANSI_X3.4-1968"
                        if item == locale.CODESET else real(item))
    assert i18n.ensure_utf8_locale() == ("locale C is not UTF-8 (ANSI_X3.4-1968), "
                                         "using C.UTF-8")
    assert os.environ["LC_ALL"] == "C.UTF-8"


def test_the_command_line_sets_it_before_anything_starts_and_logs_it(monkeypatch, device_root,
                                                                    log_file):
    """Before the start line (which may ask flatpak-spawn already) and before GTK: one INFO
    line after the start line."""
    import os

    danish_system(monkeypatch, "en_US:en")
    not_in_the_runtime(monkeypatch)
    assert main(["--path", str(device_root), "files"]) == 0
    assert os.environ["LC_ALL"] == "C.UTF-8"
    text = log_file.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(text) if " start: " in line)
    assert text[start + 1].endswith(
        "INFO openexpress.cli: locale da_DK.UTF-8 not available, using C.UTF-8")
