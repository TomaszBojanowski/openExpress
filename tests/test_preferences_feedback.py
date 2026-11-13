"""Where the window tells how a choice made in the preferences went, in the real window on the
hidden display: on the preferences while they are open (the window's own toasts are under them),
on the window when they are closed.

The installer chosen in the preferences (Setup's Choose File… or the account's Choose
Installer…): the button spins while the installer is read, "Application key saved" is a toast
on the preferences, the account row offers Sign In at once, and an installer not read is a
dialog over the preferences. Signing in from them: the account's button spins while the ticket
is exchanged and "Signed in as …" is on them; signing out and the background sync switched: the
toast is on them. From the device page, with the preferences closed (the banner, Sign Out, the
row Background Sync): the bar at the bottom and the toasts are the window's. Files, keys and
accounts are made up. With OPENEXPRESS_PREFERENCES_SHOTS=FOLDER, a picture of each state goes
there."""

import os
import threading
import time
import types
from pathlib import Path

import pytest
from fakes import descendants, program_style, window_picture

pytestmark = pytest.mark.needs_display

SHOTS = os.environ.get("OPENEXPRESS_PREFERENCES_SHOTS")
FONT = "Adwaita Sans 11"  # GNOME's, for the pictures, when it is installed
WIDTH, HEIGHT = 1000, 740

SAVED = {"en": "Application key saved", "pl": "Zapisano klucz aplikacji"}
SETUP_ROW = {"en": "Garmin Express Installer for Windows",
             "pl": "Instalator Garmin Express dla Windows"}
CHOOSE_FILE = {"en": "Choose File…", "pl": "Wybierz plik…"}
CHOOSE_INSTALLER = {"en": "Choose Installer…", "pl": "Wybierz instalator…"}
NOT_SIGNED_IN = {"en": "Not Signed In", "pl": "Nie zalogowano"}
SIGN_IN = {"en": "Sign In", "pl": "Zaloguj się"}
NOT_READ = {"en": "Installer Not Read", "pl": "Nie odczytano instalatora"}
NOT_AN_INSTALLER = {
    "en": "GarminExpress.exe is not the Garmin Express installer for Windows. Download "
          "GarminExpress.exe from garmin.com/express and choose it.",
    "pl": "GarminExpress.exe to nie instalator Garmin Express dla Windows. Pobierz "
          "GarminExpress.exe ze strony garmin.com/express i wybierz go."}
BUSY = {"en": "Another operation is in progress", "pl": "Trwa inna operacja"}
NOT_LOCAL = {"en": "Choose a file stored on this computer",
             "pl": "Wybierz plik zapisany na tym komputerze"}
SIGN_OUT = {"en": "Sign Out", "pl": "Wyloguj"}
SIGNED_IN = {"en": "Signed in as made-up-user", "pl": "Zalogowano jako made-up-user"}
SIGNED_IN_NO_DETAILS = {"en": "Signed in, but the account details could not be loaded",
                        "pl": "Zalogowano, ale nie udało się wczytać danych konta"}
UNKNOWN_ACCOUNT = {"en": "(unknown account)", "pl": "(nieznane konto)"}
SIGNED_OUT = {"en": "Signed out", "pl": "Wylogowano"}
SIGNING_IN = {"en": "Signing In…", "pl": "Logowanie…"}
BACKGROUND_ROW = {"en": "Background Sync", "pl": "Synchronizacja w\u00a0tle"}
BACKGROUND = {True: {"en": "Background sync enabled", "pl": "Włączono synchronizację w\u00a0tle"},
              False: {"en": "Background sync disabled",
                      "pl": "Wyłączono synchronizację w\u00a0tle"}}


def iterate(seconds: float = 0.05) -> None:
    from gi.repository import GLib

    end = time.monotonic() + seconds
    while time.monotonic() < end:
        GLib.MainContext.default().iteration(False)


def wait(until, what: str, seconds: float = 15) -> None:
    from gi.repository import GLib

    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if until():
            iterate(0.1)  # what follows it drawn too
            return
        GLib.MainContext.default().iteration(False)
    raise AssertionError(f"not reached: {what}")


@pytest.fixture(params=["en", "pl"])
def real_window(request, monkeypatch, tmp_path):
    """The real window in the language, nothing detected, loaded or asked, without the
    application key. Its file dialog gives `chosen["file"]` (None: a file not stored on this
    computer); the installer is read once `gate` is set: a made-up key when `chosen["key"]`,
    else by the real reader. Garmin's sign-in gives a ticket at once, exchanged once `gate` is
    set, with the account's details unless `profile["fails"]`. The background sync is on or off
    as `background["on"]`, switched at once. `told`: each toast, (where, words), "preferences"
    or "window"."""
    from gi.repository import Adw, Gtk

    from openexpress import background as sync_in_background
    from openexpress import i18n, installer, service, settings
    from openexpress.auth import sso, store
    from openexpress.gui.app import MainWindow

    for name in ("_start", "_want_account", "_show_device_picture", "_check_updates_quietly",
                 "_check_maps_quietly", "_load_app_icons", "_fetch_account", "_on_mount_added",
                 "_on_mount_removed", "_remember_geometry"):
        monkeypatch.setattr(MainWindow, name, lambda self, *a, **kw: None)
    monkeypatch.setattr(MainWindow, "load_section", lambda self, key, force=False: None)
    monkeypatch.setattr(MainWindow, "_poll_usb", lambda self, clock=None: False)
    background = {"on": False}
    monkeypatch.setattr(MainWindow, "_background_enabled", lambda self: background["on"])

    def switched(enabled, done):
        background["on"] = enabled
        done(True, "")

    monkeypatch.setattr(sync_in_background, "set_enabled", switched)
    refreshed = []
    monkeypatch.setattr(MainWindow, "refresh_device",
                        lambda self, **kw: refreshed.append(kw.get("cause")))

    chosen = {"file": None, "key": False}

    class ChosenFile:
        def get_path(self):
            return None if chosen["file"] is None else str(chosen["file"])

    monkeypatch.setattr(Gtk.FileDialog, "open",
                        lambda self, parent, cancellable, callback: callback(self, None))
    monkeypatch.setattr(Gtk.FileDialog, "open_finish", lambda self, result: ChosenFile())
    gate = threading.Event()
    real_reader = installer.consumer_from_installer

    def read(path):
        assert gate.wait(15), "the test did not let the installer be read"
        if chosen["key"]:
            return settings.Consumer("made-up-key", "made-up-secret", "made-up-salt")
        return real_reader(path)

    monkeypatch.setattr(installer, "consumer_from_installer", read)

    class LoginWindow:
        def __init__(self, on_ticket):
            self.on_ticket = on_ticket

        def present(self):  # signed in at once
            self.on_ticket(types.SimpleNamespace(ticket="made-up-ticket",
                                                 service="made-up-service"))

    monkeypatch.setattr(sso, "create_login_window",
                        lambda settings, on_ticket, application=None, parent=None:
                        LoginWindow(on_ticket))
    profile = {"fails": False}

    class Client:
        """Signed in as the saved sign-in says (the window asks it who is signed in)."""

        def __init__(self):
            try:
                self.auth = store.load()
            except store.NotLoggedIn:
                self.auth = None

        def exchange_ticket(self, ticket, service_url):
            assert gate.wait(15), "the test did not let the ticket be exchanged"
            return "made-up-token", "made-up-token-secret"

        def user_profile(self):
            if profile["fails"]:
                raise OSError("made-up failure")
            return {"userprofilepk": 1, "username": "made-up-user",
                    "emailaddress": "made-up@example.invalid", "garminguid": "made-up-guid"}

    monkeypatch.setattr(service, "make_client", lambda settings, need_auth=None: Client())

    told = []

    def spy(where, original):
        def add_toast(self, toast):
            custom = toast.get_custom_title()
            told.append((where, custom.get_label() if custom is not None else toast.get_title()))
            return original(self, toast)
        return add_toast

    monkeypatch.setattr(Adw.PreferencesDialog, "add_toast",
                        spy("preferences", Adw.PreferencesDialog.add_toast))
    monkeypatch.setattr(Adw.ToastOverlay, "add_toast", spy("window", Adw.ToastOverlay.add_toast))

    gtk_settings = Gtk.Settings.get_default()
    families = {f.get_name() for f in Gtk.Label().get_pango_context().get_font_map()
                .list_families()}
    changed = {"gtk-enable-animations": False, "gtk-xft-dpi": 96 * 1024}
    if FONT.rsplit(" ", 1)[0] in families:
        changed["gtk-font-name"] = FONT
    language = request.param
    i18n.setup(language)
    for name, value in changed.items():
        gtk_settings.set_property(name, value)
    program_style()
    win = MainWindow(None)
    try:
        win.set_default_size(WIDTH, HEIGHT)
        win.present()
        wait(lambda: win.get_width() > 0, "the window drawn")
        yield types.SimpleNamespace(win=win, language=language, chosen=chosen, gate=gate,
                                    told=told, refreshed=refreshed, folder=tmp_path, taken=0,
                                    profile=profile, background=background)
    finally:
        gate.set()
        win.destroy()
        for name in changed:
            gtk_settings.reset_property(name)
        i18n.setup("en")


def shot(r, state: str) -> None:
    """A picture of the window in this state, when they are gathered."""
    if not SHOTS:
        return
    iterate(0.3)
    r.taken += 1
    window_picture(r.win, Path(SHOTS) / f"{r.language}-{state}-{r.taken:02d}.png")


def preferences_open(r) -> None:
    r.win.show_preferences()
    wait(lambda: r.win._preferences is not None
         and r.win.get_visible_dialog() is r.win._preferences, "the preferences")


def button_of(r, row_title: str):
    from gi.repository import Adw, Gtk

    (row,) = [w for w in descendants(r.win._preferences)
              if isinstance(w, Adw.ActionRow) and w.get_title() == row_title]
    (button,) = [w for w in descendants(row) if isinstance(w, Gtk.Button)]
    return button


def spinning(button) -> bool:
    from gi.repository import Gtk

    return not button.get_sensitive() and isinstance(button.get_child(), Gtk.Overlay)


def test_the_key_saved_in_the_preferences_is_told_on_them(real_window):
    r = real_window
    lang = r.language
    preferences_open(r)
    r.chosen.update(file=r.folder / "GarminExpress.exe", key=True)
    button = button_of(r, SETUP_ROW[lang])
    assert button.get_label() == CHOOSE_FILE[lang]
    button.emit("clicked")
    wait(lambda: spinning(button), "the button spins while the installer is read")
    r.gate.set()
    wait(lambda: not r.win.busy, "the installer read")
    assert r.told == [("preferences", SAVED[lang])]
    assert r.win.get_visible_dialog() is r.win._preferences
    row, account_button = r.win._account_row
    assert (row.get_title(), row.get_subtitle(), account_button.get_label()) == (
        NOT_SIGNED_IN[lang], "", SIGN_IN[lang])
    assert button.get_sensitive() and button.get_label() == CHOOSE_FILE[lang]
    shot(r, "saved")


def test_an_installer_not_read_is_a_dialog_over_the_preferences(real_window):
    from gi.repository import Adw

    r = real_window
    lang = r.language
    chosen = r.folder / "GarminExpress.exe"
    chosen.write_bytes(b"MZ" + bytes(4096))  # a program of no installer
    r.chosen.update(file=chosen, key=False)
    preferences_open(r)
    row, button = r.win._account_row
    assert button.get_label() == CHOOSE_INSTALLER[lang]
    button.emit("clicked")
    wait(lambda: spinning(button), "the button spins while the installer is read")
    r.gate.set()
    wait(lambda: not r.win.busy, "the installer read")
    dialog = r.win.get_visible_dialog()
    assert isinstance(dialog, Adw.AlertDialog)
    assert (dialog.get_heading(), dialog.get_body()) == (NOT_READ[lang], NOT_AN_INSTALLER[lang])
    assert r.win._preferences is not None  # under the dialog, still open
    assert r.told == []
    assert button.get_sensitive() and button.get_label() == CHOOSE_INSTALLER[lang]
    assert row.get_title() == NOT_SIGNED_IN[lang]
    shot(r, "not-read")
    dialog.force_close()
    wait(lambda: r.win.get_visible_dialog() is r.win._preferences, "back on the preferences")


@pytest.mark.parametrize("why", ["busy", "not-local"])
def test_an_installer_not_read_at_all_is_told_on_the_preferences(real_window, why):
    """A task that began while the file was being chosen, or a file the system does not
    store on this computer: nothing is read, and the toast that says why is on the
    preferences."""
    from openexpress.settings import has_consumer

    r = real_window
    lang = r.language
    preferences_open(r)
    r.chosen.update(file=None if why == "not-local" else r.folder / "GarminExpress.exe",
                    key=True)
    r.gate.set()
    button = button_of(r, SETUP_ROW[lang])
    if why == "busy":
        r.win.busy = True  # a task the window started meanwhile
    button.emit("clicked")
    iterate(0.2)
    r.win.busy = False
    assert r.told == [("preferences", {"busy": BUSY, "not-local": NOT_LOCAL}[why][lang])]
    assert not has_consumer()


def test_chosen_from_the_banner_it_is_told_on_the_window(real_window):
    """The banner of the device page (One-time setup…): the preferences are closed, so the bar
    at the bottom says the installer is being read and the toast is the window's."""
    r = real_window
    lang = r.language
    r.chosen.update(file=r.folder / "GarminExpress.exe", key=True)
    r.win._banner_kind = "setup"
    r.win._banner_action()
    wait(lambda: r.win.busy and r.win.action_bar.get_reveal_child(), "the bar at the bottom")
    assert r.win.progress_label.get_text() == {"en": "Reading the Installer…",
                                               "pl": "Odczytywanie instalatora…"}[lang]
    r.gate.set()
    wait(lambda: not r.win.busy, "the installer read")
    assert r.told == [("window", SAVED[lang])]


# --- the account and the background sync -----------------------------------------------------


def with_key(signed_in: bool = False) -> None:
    """The made-up application key saved, and a made-up account signed in when asked."""
    from openexpress import settings
    from openexpress.auth import store

    settings.save_consumer(settings.Consumer("made-up-key", "made-up-secret", "made-up-salt"))
    if signed_in:
        store.save(store.ConnectAuth("made-up-token", "made-up-token-secret",
                                     account={"username": "made-up-user"}))


def switch_of(r, row_title: str):
    from gi.repository import Adw

    (row,) = [w for w in descendants(r.win._preferences)
              if isinstance(w, Adw.SwitchRow) and w.get_title() == row_title]
    return row


@pytest.mark.parametrize("details", ["read", "not-read"])
def test_signing_in_from_the_preferences_is_told_on_them(real_window, details):
    """The account's Sign In: Garmin's page (its own window) gives the ticket; while it is
    exchanged the button spins, and the toast is on the preferences, still open."""
    r = real_window
    lang = r.language
    with_key()
    r.profile["fails"] = details == "not-read"
    preferences_open(r)
    row, button = r.win._account_row
    assert button.get_label() == SIGN_IN[lang]
    button.emit("clicked")
    wait(lambda: spinning(button), "the button spins while the ticket is exchanged")
    r.gate.set()
    wait(lambda: not r.win.busy, "signed in")
    words = SIGNED_IN if details == "read" else SIGNED_IN_NO_DETAILS
    assert r.told == [("preferences", words[lang])]
    assert r.win.get_visible_dialog() is r.win._preferences
    name = "made-up-user" if details == "read" else UNKNOWN_ACCOUNT[lang]
    assert (row.get_title(), button.get_label()) == (name, SIGN_OUT[lang])
    shot(r, f"signed-in-{details}")


def test_signing_out_from_the_preferences_is_told_on_them(real_window):
    r = real_window
    lang = r.language
    with_key(signed_in=True)
    preferences_open(r)
    row, button = r.win._account_row
    assert (row.get_title(), button.get_label()) == ("made-up-user", SIGN_OUT[lang])
    button.emit("clicked")
    iterate(0.2)
    assert r.told == [("preferences", SIGNED_OUT[lang])]
    assert (row.get_title(), button.get_label()) == (NOT_SIGNED_IN[lang], SIGN_IN[lang])
    shot(r, "signed-out")


@pytest.mark.parametrize("on", [True, False], ids=["on", "off"])
def test_the_background_sync_switched_in_the_preferences_is_told_on_them(real_window, on):
    r = real_window
    lang = r.language
    r.background["on"] = not on
    preferences_open(r)
    switch = switch_of(r, BACKGROUND_ROW[lang])
    assert switch.get_active() is not on
    switch.set_active(on)
    iterate(0.2)
    assert r.told == [("preferences", BACKGROUND[on][lang])]
    assert r.background["on"] is on and switch.get_active() is on
    shot(r, f"background-{'on' if on else 'off'}")


@pytest.mark.parametrize("what", ["sign-in", "sign-out", "background"])
def test_with_the_preferences_closed_the_window_tells_it(real_window, what):
    """The same from the device page (the banner's Sign In, Sign Out, the row Background Sync),
    with the preferences closed: the toast is the window's, and signing in shows on the bar."""
    r = real_window
    lang = r.language
    with_key(signed_in=what == "sign-out")
    if what == "sign-in":
        r.win.login()
        wait(lambda: r.win.busy and r.win.action_bar.get_reveal_child(), "the bar at the bottom")
        assert r.win.progress_label.get_text() == SIGNING_IN[lang]
        r.gate.set()
        wait(lambda: not r.win.busy, "signed in")
        assert r.told == [("window", SIGNED_IN[lang])]
    elif what == "sign-out":
        r.win.logout()
        assert r.told == [("window", SIGNED_OUT[lang])]
    else:
        r.win.set_background(True)
        assert r.told == [("window", BACKGROUND[True][lang])]
