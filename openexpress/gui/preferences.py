"""The preferences of the window and what they change: the Garmin Connect account (log in, log
out, the key from Garmin Express's installer), the sync in the background, the language, the
cache (its parts, counted and cleared), the copy of the diagnostic files; the shortcuts.
Preferences is a mixin of MainWindow (gui/app.py), without __init__ of its own.

Has: _preferences, _account_row and _storage (while the preferences are open; cleared when
they close), gui_settings, _account and _account_why (after a logout), session (dropped when
the account changes), _account_wanted (resolve_account, after a detection).
Uses: busy, run_task, error, toast, _button, refresh_device, show_overview, _want_account,
_set_account, _fetch_account, get_application; TaskRunner.in_background
(gui/window_tasks.py)."""
from __future__ import annotations

import logging
import traceback
import types
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, GLib, Gtk  # noqa: E402 - imports must follow gi.require_version

from openexpress import cache, diagnostics, i18n, paths, service  # noqa: E402
from openexpress.auth import sso, store  # noqa: E402
from openexpress.gui.connected import open_address  # noqa: E402
from openexpress.gui.gui_settings import update_gui_settings  # noqa: E402
from openexpress.gui.navigation import Cause  # noqa: E402
from openexpress.gui.window_tasks import TaskRunner  # noqa: E402
from openexpress.i18n import N_, _, caption, human_size, ngettext, pgettext  # noqa: E402
from openexpress.settings import (  # noqa: E402
    Settings,
    SetupError,
    extract_consumer_from_dll,
    extract_url_salt_from_dll,
    save_consumer,
)

# The window's logger: the lines of the preferences stay where they were before the split.
log = logging.getLogger("openexpress.gui.app")


# The language of the interface, read and kept in one place (i18n).
load_ui_language = i18n.saved_ui_language
save_ui_language = i18n.save_ui_language


def diagnostics_group(win) -> Adw.PreferencesGroup:
    """The "Diagnostics" group of the preferences: the copy of the device's diagnostic files
    at each connection, off unless chosen (settings.json; the same as `setup
    --diagnostics-copy`). A change that cannot be saved puts the switch back."""
    g = Adw.PreferencesGroup(title=_("Diagnostics"))
    sw = Adw.SwitchRow(title=_("Keep a copy of the device's diagnostic files on this computer"),
                       subtitle=_("They stay on this computer; openExpress never sends them"),
                       active=diagnostics.copy_on())

    def toggled(row, _pspec):
        wanted = row.get_active()
        try:
            settings = Settings.load()
            settings.diagnostics_copy = wanted
            settings.save()
        except (OSError, SetupError) as e:
            row.handler_block_by_func(toggled)
            row.set_active(not wanted)
            row.handler_unblock_by_func(toggled)
            win.error(_("Diagnostics"), str(e))
            return
        log.info("diagnostics: the copy at each connection turned %s", "on" if wanted else "off")

    sw.connect("notify::active", toggled)
    g.add(sw)
    return g


def account_client():
    """The client the start page asks for the account's devices: who is logged in now."""
    return service.make_client(Settings.load())


def logged_in_auth():
    """Who is logged in to Garmin Connect now, as the start page asks it; None: nobody."""
    try:
        return account_client().auth
    except SetupError:  # the settings file is damaged: said where it is used
        return None


def render_account_row(win) -> None:
    """The account's row of the preferences, while they are open: who is logged in, with
    Log out, or nobody, with Log In."""
    shown = getattr(win, "_account_row", None)
    if shown is None:
        return
    row, button = shown
    auth = logged_in_auth()
    if auth is None:
        row.set_title(_("Not logged in"))
        button.set_label(_("Log In"))
    else:
        row.set_title(GLib.markup_escape_text(auth.display))
        button.set_label(_("Log out"))


# The question before a part of the cache is cleared: only for those that take long to download
# again; pictures and temporary files go at once.
CACHE_QUESTIONS = {
    "maps": (N_("Clear the downloaded maps?"),
             N_("This deletes the downloaded maps ({size}). They will have to be downloaded "
                "again.")),
    "updates": (N_("Clear the downloaded updates?"),
                N_("This deletes the downloaded software updates ({size}). They are downloaded "
                   "again when needed.")),
}


def _cache_words(size: int, files: int) -> str:
    return caption(human_size(size), ngettext("{count} file", "{count} files", files).format(
        count=files))


def show_cache_usage(win, use) -> None:
    """The sizes counted (cache.usage) in the storage group of the preferences, if they are
    still open: for each part and for all of it, its size and files, and its button clickable
    when there is something to clear."""
    shown = getattr(win, "_storage", None)
    if shown is None:
        return  # the preferences closed meanwhile
    shown.usage = use
    for name, (row, trash) in shown.parts.items():
        row.set_subtitle(_cache_words(use.sizes[name], use.files[name]))
        trash.set_sensitive(use.sizes[name] > 0)
    row, button = shown.everything
    row.set_subtitle(_cache_words(use.total, sum(use.files.values())))
    button.set_sensitive(use.total > 0)


def ask_clear_cache_part(win, name: str, button) -> None:
    """The trash of a part of the cache: maps and updates ask first, with their size; the
    others are cleared at once."""
    shown = getattr(win, "_storage", None)
    if shown is None or shown.usage is None:
        return
    if name not in CACHE_QUESTIONS:
        clear_cache(win, [name], button)
        return
    heading, body = CACHE_QUESTIONS[name]
    ask_to_clear(win, _(heading), _(body).format(size=human_size(shown.usage.sizes[name])), [name])


def ask_to_clear(win, heading: str, body: str, names: list[str] | None) -> None:
    """Clear (destructive) or Cancel (the default); Clear clears those parts (None: all) from
    their button, found when the answer comes: the handler holds no widget."""
    d = Adw.AlertDialog(heading=heading, body=body)
    d.add_response("cancel", _("Cancel"))
    # TRANSLATORS: button that clears the cache
    d.add_response("clear", _("Clear"))
    d.set_response_appearance("clear", Adw.ResponseAppearance.DESTRUCTIVE)
    d.set_default_response("cancel")
    d.set_close_response("cancel")

    def response(_d, r):
        shown = getattr(win, "_storage", None)
        if r != "clear" or shown is None:
            return
        button = shown.everything[1] if names is None else shown.parts[names[0]][1]
        clear_cache(win, names, button)

    d.connect("response", response)
    d.present(win)


def clear_cache(win, names: list[str] | None, button) -> None:
    """Clears those parts of the cache (None: all) in a task started from `button`, as
    `cache --clear --only … --apply` does; then the sizes counted again (in the task too) and
    how much was freed."""
    def work():
        return cache.clear(names), cache.usage()

    def done(result):
        freed, now = result
        show_cache_usage(win, now)
        cache_toast(win, _("Freed {size}.").format(size=human_size(freed.total)))

    def failed(e):
        if isinstance(e, cache.CacheError):
            win.error(_("The cache was not cleared"), str(e))
            return True
        return False

    win.run_task(_("Clearing the cache…"), work, done, failed, device=False, button=button)


def cache_toast(win, text: str) -> None:
    """A toast on the preferences while they are open (the window's own is under them)."""
    shown = getattr(win, "_preferences", None)
    if shown is not None:
        shown.add_toast(Adw.Toast(title=GLib.markup_escape_text(text), timeout=5))
    else:
        win.toast(text)


def account_changed(win, cause: Cause = Cause.ACCOUNT_CHANGED) -> None:
    """A change of account (login, logout, the key from the installer): the session made
    with the old one is dropped and the devices detected again. The device open before is
    opened again when it is still there, also among several, and the page shown stays
    (Q3), but for the view of a device of the account after a logout (`cause`
    LOGGED_OUT)."""
    render_account_row(win)
    reopen = open_address(win.session) or None
    win.session = None
    win.refresh_device(cause=cause, reopen=reopen)


def resolve_account(win) -> None:
    """After a detection: the account's devices for the start page from the device just
    opened (its ownership check read them, also when it is not on the account), else, if
    they are wanted, asked now."""
    s = win.session
    listed = getattr(s, "account_devices", None) if s is not None else None
    if listed is None and s is not None:
        listed = getattr(getattr(s, "ownership_error", None), "devices", None)
    if listed is not None:
        win._account_wanted = False
        # Not asked again; kept as any list of the account is, with the kinds it tells (Q4).
        # The caller draws the page shown, after deciding where the user goes.
        win._set_account(listed, draw=False)
    elif getattr(win, "_account_wanted", False):
        win._account_wanted = False
        win._fetch_account()


# The keys of the actions, as App.do_startup binds them; the menu and the window of
# shortcuts show the first. Ctrl+? needs Shift where "?" does (as on a Polish keyboard), so
# Ctrl+/ comes first.
KEYS = {"win.sync": ["<Control>s"], "win.refresh": ["<Control>r", "F5"],
        "app.preferences": ["<Control>comma"],
        "app.shortcuts": ["<Control>slash", "<Control>question"], "app.quit": ["<Control>q"]}
# The window of shortcuts, in this order.
SHORTCUTS = ((N_("Sync"), "win.sync"), (N_("Refresh"), "win.refresh"),
             (N_("Preferences"), "app.preferences"),
             (N_("Keyboard Shortcuts"), "app.shortcuts"), (N_("Quit"), "app.quit"))


class Preferences:
    def _background_enabled(self) -> bool:
        from openexpress import background

        try:
            state = background.is_enabled()
        except Exception:  # noqa: BLE001 - e.g. no systemctl: treat as unknown
            log.debug("%s", traceback.format_exc())
            state = None
        return bool(self.gui_settings.get("background")) if state is None else bool(state)

    def set_background(self, enabled: bool, on_result=None) -> None:
        from openexpress import background

        def done(ok: bool, error: str) -> None:
            if ok:
                # gui.json read again: what was kept since the window opened (the view of
                # the start page) stays.
                self.gui_settings = update_gui_settings(background=enabled)
                self.toast(_("Background sync enabled") if enabled
                           else _("Background sync disabled"))
                if self.session is not None:
                    self.show_overview()
            else:
                self.error(_("Background sync"), error or _("The change failed."))
            if on_result:
                on_result(ok)

        try:
            background.set_enabled(enabled, done)
        except Exception as e:  # noqa: BLE001
            log.debug("%s", traceback.format_exc())
            done(False, str(e))

    def _auto_sync_row(self) -> Adw.SwitchRow:
        """The background sync, on or off as it is, from the device page (the same as in
        Preferences): a change that fails puts the switch back."""
        row = Adw.SwitchRow(title=_("Sync automatically when connected"),
                            active=self._background_enabled())

        def toggled(switch, _pspec):
            # The row from the signal; it is held only until the change is done.
            wanted = switch.get_active()
            if wanted == self._background_enabled():
                return

            def result(ok):
                if not ok:
                    switch.handler_block_by_func(toggled)
                    switch.set_active(not wanted)
                    switch.handler_unblock_by_func(toggled)

            self.set_background(wanted, result)

        row.connect("notify::active", toggled)
        return row

    def show_preferences(self) -> None:
        dialog = Adw.PreferencesDialog()
        page = Adw.PreferencesPage(title=_("General"), icon_name="preferences-system-symbolic")
        page.add(self._account_group())
        # The window holds the dialog while it is open, for the handlers of its rows (one
        # that held the dialog itself would keep it after it closed); closed, the window
        # keeps no widget of the preferences.
        self._preferences = dialog
        dialog.connect("closed", lambda _d: (setattr(self, "_account_row", None),
                                             setattr(self, "_storage", None),
                                             setattr(self, "_preferences", None)))
        g = Adw.PreferencesGroup(title=_("Sync with Garmin Connect"))
        sw = Adw.SwitchRow(
            title=_("Sync in the background"),
            subtitle=_("Starts at login and syncs whenever the device is connected, "
                       "also when this window is closed"),
            active=self._background_enabled())

        def toggled(row, _pspec):
            wanted = row.get_active()
            if wanted == self._background_enabled():
                return

            def result(ok):
                if not ok:
                    row.handler_block_by_func(toggled)
                    row.set_active(not wanted)
                    row.handler_unblock_by_func(toggled)

            self.set_background(wanted, result)

        sw.connect("notify::active", toggled)
        g.add(sw)
        page.add(g)

        g = Adw.PreferencesGroup(title=_("Language"))
        codes = [""] + i18n.available_languages()
        names = [_("System default")] + [i18n.LANGUAGE_NAMES.get(c, c) for c in codes[1:]]
        current = load_ui_language()
        combo = Adw.ComboRow(title=_("Interface language"), model=Gtk.StringList.new(names))
        combo.set_selected(codes.index(current) if current in codes else 0)

        def language_changed(r, _p):
            code = codes[r.get_selected()]
            if code == load_ui_language():
                return
            try:
                save_ui_language(code)
            except (OSError, ValueError) as e:
                self.error(_("Language"), str(e))
                return
            shown = getattr(self, "_preferences", None)
            if shown is not None:
                shown.add_toast(Adw.Toast(
                    title=_("Restart the application to change the language")))

        combo.connect("notify::selected", language_changed)
        g.add(combo)
        page.add(g)

        g = Adw.PreferencesGroup(title=_("Setup"))
        row = Adw.ActionRow(
            title=_("Garmin Express installer for Windows"),
            subtitle=_("GarminExpress.exe from garmin.com: the application key is read from it "
                       "again, nothing is installed"))
        row.add_suffix(self._button(_("Choose File…"), self.choose_dll))
        g.add(row)
        page.add(g)
        page.add(self._cache_group())
        page.add(diagnostics_group(self))
        dialog.add(page)
        dialog.present(self)

    def _account_group(self) -> Adw.PreferencesGroup:
        """The "Garmin Connect account" group of the preferences: one row, who is logged in
        with Log out, or nobody with Log In; drawn again when the account changes."""
        g = Adw.PreferencesGroup(title=_("Garmin Connect account"))
        row = Adw.ActionRow()
        button = self._button("", self._account_action)
        row.add_suffix(button)
        g.add(row)
        self._account_row = (row, button)
        render_account_row(self)
        return g

    def _account_action(self) -> None:
        """Log out or Log In in the preferences: the same as in the device view."""
        if logged_in_auth() is None:
            self.login()
        else:
            self.logout()

    def _cache_group(self) -> Adw.PreferencesGroup:
        """The "Storage" group of the preferences: a row for each part of the cache, with its
        size, its files and a trash (grey while the part is empty), and one for all of it
        with "Clear All…". The sizes are counted in the background, never on the window's
        thread; the group is about the cache of this installation alone."""
        kind = _("Flatpak") if paths.in_flatpak() else _("version from source")
        # TRANSLATORS: group in the preferences: the cache (downloaded maps and updates,
        # pictures, temporary files) on the disk of the computer (not the device's storage of
        # the device page)
        g = Adw.PreferencesGroup(title=pgettext("preferences", "Storage"), description=_(
            "Only the files of this installation ({kind}); openExpress downloads them again "
            "when needed").format(kind=kind))
        parts = {}
        for name in cache.CATEGORIES:
            row = Adw.ActionRow(title=_(cache.CATEGORY_NAMES[name]), subtitle=_("Counting…"))
            trash = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER,
                               tooltip_text=_("Delete these files"), sensitive=False)
            trash.add_css_class("flat")
            # The handler gets its button from the signal and holds none.
            trash.connect("clicked", lambda button, name=name: ask_clear_cache_part(
                self, name, button))
            row.add_suffix(trash)
            g.add(row)
            parts[name] = (row, trash)
        # TRANSLATORS: the last row of the cache in the preferences, with "Clear All…"
        everything = Adw.ActionRow(title=_("All of the cache"), subtitle=_("Counting…"))
        # TRANSLATORS: button that clears every part of the cache
        button = Gtk.Button(label=_("Clear All…"), valign=Gtk.Align.CENTER, sensitive=False)
        button.connect("clicked", self._ask_clear_cache)
        everything.add_suffix(button)
        g.add(everything)
        # Held by the window while the preferences are open (dropped when they close), so
        # that what comes back from a thread finds the rows without holding them.
        self._storage = types.SimpleNamespace(parts=parts, everything=(everything, button),
                                              usage=None)
        TaskRunner.in_background(cache.usage, lambda use: show_cache_usage(self, use))
        return g

    def _ask_clear_cache(self, button) -> None:
        """"Clear All…": asks first, naming the maps and their size; then clears as
        `cache --clear --apply` does, from the button."""
        shown = getattr(self, "_storage", None)
        use = shown.usage if shown is not None else None
        if use is None:
            return
        maps, other = use.sizes["maps"], use.total - use.sizes["maps"]
        if maps:
            body = _("This deletes the downloaded maps ({maps}) and the other files of the cache "
                     "({other}). The maps will have to be downloaded again.").format(
                maps=human_size(maps), other=human_size(other))
        else:
            body = _("This deletes the files of the cache ({size}). They are downloaded again "
                     "when needed.").format(size=human_size(use.total))
        ask_to_clear(self, _("Clear the cache?"), body, None)

    def show_shortcuts(self) -> None:
        """The window of shortcuts: each as the first of the keys App.do_startup binds."""
        dialog = Adw.ShortcutsDialog()
        section = Adw.ShortcutsSection(title=_("General"))
        for title, action in SHORTCUTS:
            section.add(Adw.ShortcutsItem(title=_(title), accelerator=KEYS[action][0]))
        dialog.add(section)
        dialog.present(self)

    def choose_dll(self) -> None:
        dialog = Gtk.FileDialog(
            title=_("Select the Garmin Express installer for Windows (GarminExpress.exe)"))

        def read(path: Path):
            from openexpress.installer import consumer_from_installer, refuse_macos_installer

            refuse_macos_installer(path)
            if path.suffix.lower() == ".exe":
                return consumer_from_installer(path)
            consumer = extract_consumer_from_dll(path)
            util = path.with_name("Utilities.dll")
            if util.exists():
                consumer.url_salt = extract_url_salt_from_dll(util)
            return consumer

        def chosen(dlg, res):
            try:
                f = dlg.open_finish(res)
            except GLib.Error:
                return
            if f.get_path() is None:
                self.toast(_("Choose a file stored on this computer"))
                return
            path = Path(f.get_path())

            def saved(consumer):
                save_consumer(consumer)
                self.toast(_("Application key saved"))
                account_changed(self)

            self.run_task(_("Reading the installer…"), lambda: read(path), saved, device=False)

        dialog.open(self, None, chosen)

    def login(self) -> None:
        settings = Settings.load()

        def on_ticket(st):
            if st is None:
                return

            def work():
                client = service.make_client(settings)
                token, secret = client.exchange_ticket(st.ticket, st.service)
                auth = store.ConnectAuth(token, secret, settings.environment)
                client.auth = auth
                profile_ok = True
                try:
                    prof = client.user_profile()
                    auth.account = {k: prof.get(k) for k in
                                    ("userprofilepk", "username", "emailaddress", "garminguid")}
                except Exception:  # noqa: BLE001 - the token works without the profile
                    log.debug("%s", traceback.format_exc())
                    profile_ok = False
                store.save(auth)
                return auth, profile_ok

            def done(result):
                auth, profile_ok = result
                self.toast(_("Logged in as {account}").format(account=auth.display) if profile_ok
                           else _("Logged in, but the account details could not be loaded"))
                self._want_account()
                account_changed(self)

            def start():
                if self.busy:
                    return True  # the ticket expires only after minutes: wait for the task
                self.run_task(_("Logging in…"), work, done, device=False)
                return False

            def first():
                if start():
                    GLib.timeout_add(500, start)
                return False

            GLib.idle_add(first)

        sso.create_login_window(settings, on_ticket, application=self.get_application(),
                                parent=self).present()

    def logout(self) -> None:
        store.delete()
        self.toast(_("Logged out"))
        self._account = None  # the start page lists no device of the account any more
        self._account_why = "logged-out"  # and says so
        account_changed(self, Cause.LOGGED_OUT)
