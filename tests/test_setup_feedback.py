"""Where the one-time setup tells how it went, in the real window on the hidden display. The
installer chosen in the preferences (Setup's Choose File… or the account's Choose Installer…):
the button spins while the installer is read, "Application key saved" is a toast on the
preferences (the window's own toasts are under them), the account row offers Sign In at once,
and an installer not read is a dialog over the preferences. Chosen from the banner of the device
page, with the preferences closed: the bar at the bottom and the toast are the window's. Files
and keys are made up. With OPENEXPRESS_SETUP_SHOTS=FOLDER, a picture of each state goes there."""

import os
import threading
import time
import types
from pathlib import Path

import pytest
from fakes import descendants, program_style, window_picture

pytestmark = pytest.mark.needs_display

SHOTS = os.environ.get("OPENEXPRESS_SETUP_SHOTS")
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
def setup_window(request, monkeypatch, tmp_path):
    """The real window in the language, nothing detected, loaded or asked, without the
    application key. Its file dialog gives `chosen["file"]` (None: a file not stored on this
    computer); the installer is read once `gate` is set: a made-up key when `chosen["key"]`,
    else by the real reader. `told`: each toast, (where, words), "preferences" or "window"."""
    from gi.repository import Adw, Gtk

    from openexpress import i18n, installer, settings
    from openexpress.gui.app import MainWindow

    for name in ("_start", "_want_account", "_show_device_picture", "_check_updates_quietly",
                 "_check_maps_quietly", "_load_app_icons", "_fetch_account", "_on_mount_added",
                 "_on_mount_removed", "_remember_geometry"):
        monkeypatch.setattr(MainWindow, name, lambda self, *a, **kw: None)
    monkeypatch.setattr(MainWindow, "load_section", lambda self, key, force=False: None)
    monkeypatch.setattr(MainWindow, "_poll_usb", lambda self, clock=None: False)
    monkeypatch.setattr(MainWindow, "_background_enabled", lambda self: False)
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
                                    told=told, refreshed=refreshed, folder=tmp_path, taken=0)
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
    window_picture(r.win, Path(SHOTS) / f"setup-{r.language}-{r.taken:02d}-{state}.png")


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


def test_the_key_saved_in_the_preferences_is_told_on_them(setup_window):
    r = setup_window
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


def test_an_installer_not_read_is_a_dialog_over_the_preferences(setup_window):
    from gi.repository import Adw

    r = setup_window
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
def test_an_installer_not_read_at_all_is_told_on_the_preferences(setup_window, why):
    """A task that began while the file was being chosen, or a file the system does not
    store on this computer: nothing is read, and the toast that says why is on the
    preferences."""
    from openexpress.settings import has_consumer

    r = setup_window
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


def test_chosen_from_the_banner_it_is_told_on_the_window(setup_window):
    """The banner of the device page (One-time setup…): the preferences are closed, so the bar
    at the bottom says the installer is being read and the toast is the window's."""
    r = setup_window
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
