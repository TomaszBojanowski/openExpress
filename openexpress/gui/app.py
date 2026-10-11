"""openexpress graphical interface (GTK 4 + libadwaita)."""

from __future__ import annotations

import json
import logging
import threading
import traceback
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gio", "2.0")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from openexpress import i18n, paths, service  # noqa: E402
from openexpress.auth import sso, store  # noqa: E402
from openexpress.cli import RESTART_STEPS, _human  # noqa: E402
from openexpress.device.gio_storage import _is_garmin_uri  # noqa: E402
from openexpress.i18n import _, ngettext, pgettext  # noqa: E402
from openexpress.settings import (  # noqa: E402
    SetupError, Settings, extract_consumer_from_dll, extract_url_salt_from_dll, save_consumer,
)

APP_ID = "io.github.tomaszbojanowski.OpenExpress"
log = logging.getLogger(__name__)


def gui_settings_path() -> Path:
    return paths.config_dir() / "gui.json"


def load_gui_settings() -> dict:
    p = gui_settings_path()
    return json.loads(p.read_text()) if p.exists() else {"auto_sync": False}


def save_gui_settings(data: dict) -> None:
    paths.write_private(gui_settings_path(), json.dumps(data, indent=2))


def _settings_json() -> Path:
    return paths.config_dir() / "settings.json"


def load_ui_language() -> str:
    try:
        return json.loads(_settings_json().read_text()).get("ui_language") or ""
    except (OSError, ValueError):
        return ""


def save_ui_language(code: str) -> None:
    """Store the language in settings.json, keeping the other keys."""
    p = _settings_json()
    data = json.loads(p.read_text()) if p.exists() else {}
    data["ui_language"] = code
    paths.write_private(p, json.dumps(data, indent=2))


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application):
        super().__init__(application=app, title="openExpress", default_width=1160,
                         default_height=720)
        self.session: service.DeviceSession | None = None
        self.busy = False
        self.gui_settings = load_gui_settings()
        self.maps_cache = None
        self.apps_cache = None
        self.music_cache = None
        self.updates_cache = None
        self._action_buttons: list[Gtk.Widget] = []

        self.toasts = Adw.ToastOverlay()
        view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        self.stack = Adw.ViewStack()
        header.set_title_widget(Adw.ViewSwitcher(stack=self.stack,
                                                 policy=Adw.ViewSwitcherPolicy.WIDE))
        refresh = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text=_("Refresh"))
        refresh.connect("clicked", lambda *_: self.refresh_device())
        header.pack_start(refresh)
        about = Gtk.Button(icon_name="help-about-symbolic", tooltip_text=_("About openExpress"))
        about.connect("clicked", lambda *_a: self.show_about())
        header.pack_end(about)
        view.add_top_bar(header)

        self.progress_bar = Gtk.ProgressBar(show_text=False, hexpand=True)
        self.progress_label = Gtk.Label(xalign=0, ellipsize=3, hexpand=True)
        self.spinner = Adw.Spinner() if hasattr(Adw, "Spinner") else Gtk.Spinner(spinning=True)
        bar = Gtk.ActionBar(revealed=False)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, hexpand=True)
        box.append(self.progress_label)
        box.append(self.progress_bar)
        bar.pack_start(self.spinner)
        bar.set_center_widget(box)
        self.action_bar = bar
        view.add_bottom_bar(bar)

        self.device_page = Adw.PreferencesPage()
        self.sync_page = Adw.PreferencesPage()
        self.updates_page = Adw.PreferencesPage()
        self.maps_page = Adw.PreferencesPage()
        self.apps_page = Adw.PreferencesPage()
        self.music_page = Adw.PreferencesPage()
        self.no_device = Adw.StatusPage(
            icon_name="phone-symbolic", title=_("Connect your watch"),
            description=_("Connect your Garmin watch with a USB cable. "
                          "It will be detected automatically."),
        )
        self.device_stack = Gtk.Stack()
        self.device_stack.add_named(self.no_device, "empty")
        self.device_stack.add_named(self.device_page, "device")
        # TRANSLATORS: tab title
        self.stack.add_titled_with_icon(self.device_stack, "device", pgettext("tab", "Watch"),
                                        "computer-symbolic")
        # TRANSLATORS: tab title
        self.stack.add_titled_with_icon(self.sync_page, "sync", pgettext("tab", "Sync"),
                                        "mail-send-receive-symbolic")
        # TRANSLATORS: tab title
        self.stack.add_titled_with_icon(self.updates_page, "updates", pgettext("tab", "Updates"),
                                        "software-update-available-symbolic")
        # TRANSLATORS: tab title
        self.stack.add_titled_with_icon(self.maps_page, "maps", pgettext("tab", "Maps"),
                                        "mark-location-symbolic")
        # TRANSLATORS: tab title (Connect IQ apps)
        self.stack.add_titled_with_icon(self.apps_page, "apps", pgettext("tab", "Apps"),
                                        "view-app-grid-symbolic")
        # TRANSLATORS: tab title
        self.stack.add_titled_with_icon(self.music_page, "music", pgettext("tab", "Music"),
                                        "audio-x-generic-symbolic")
        self.stack.connect("notify::visible-child-name", self._on_page_changed)
        view.set_content(self.stack)
        self.toasts.set_child(view)
        self.set_content(self.toasts)

        self._build_sync_page()
        self._groups: dict[str, list] = {}

        self.monitor = Gio.VolumeMonitor.get()
        self.monitor.connect("mount-added", self._on_mount_added)
        self.monitor.connect("mount-removed", self._on_mount_removed)
        self.refresh_device()

    # --- helpers ----------------------------------------------------------------------

    def toast(self, text: str) -> None:
        self.toasts.add_toast(Adw.Toast(title=GLib.markup_escape_text(text), timeout=5))

    def error(self, title: str, text: str) -> None:
        d = Adw.AlertDialog(heading=title, body=text)
        d.add_response("ok", _("OK"))
        d.present(self)

    def set_busy(self, busy: bool, text: str = "") -> None:
        self.busy = busy
        self.action_bar.set_revealed(busy)
        self.progress_label.set_text(text)
        self.progress_bar.set_fraction(0)
        for b in self._action_buttons:
            b.set_sensitive(not busy)

    def progress(self, text: str) -> None:
        GLib.idle_add(self.progress_label.set_text, text)
        GLib.idle_add(self.progress_bar.pulse)

    def fraction(self, done: int, total: int | None) -> None:
        if total:
            GLib.idle_add(self.progress_bar.set_fraction, min(1.0, done / total))

    def run_task(self, title: str, fn, on_done=None, on_error=None) -> None:
        """Run `fn()` in a thread; the result is passed back to the GUI thread."""
        if self.busy:
            self.toast(_("Another operation is in progress"))
            return
        self.set_busy(True, title)

        def worker():
            try:
                result = fn()
            except Exception as e:  # noqa: BLE001
                log.debug("%s", traceback.format_exc())
                GLib.idle_add(finish_error, e)
            else:
                GLib.idle_add(finish_ok, result)

        def finish_ok(result):
            self.set_busy(False)
            if on_done:
                on_done(result)
            return False

        def finish_error(e):
            self.set_busy(False)
            if on_error and on_error(e):
                return False
            self.error(title, str(e) or e.__class__.__name__)
            return False

        threading.Thread(target=worker, daemon=True).start()

    def _button(self, label: str, cb, style: str | None = None) -> Gtk.Button:
        b = Gtk.Button(label=label, valign=Gtk.Align.CENTER)
        if style:
            b.add_css_class(style)
        b.connect("clicked", lambda *_: cb())
        b.set_sensitive(not self.busy)
        self._action_buttons.append(b)
        return b

    def _reset_group(self, page: Adw.PreferencesPage, key: str, title: str,
                     description: str = "") -> Adw.PreferencesGroup:
        old = self._groups.pop(f"{id(page)}:{key}", None)
        if old is not None:
            page.remove(old)
        g = Adw.PreferencesGroup(title=title, description=description)
        page.add(g)
        self._groups[f"{id(page)}:{key}"] = g
        return g

    @staticmethod
    def _removed_files(count: int) -> str:
        return ngettext("Removed {count} file", "Removed {count} files", count).format(count=count)

    def _clear_page(self, page: Adw.PreferencesPage) -> None:
        for k in [k for k in self._groups if k.startswith(f"{id(page)}:")]:
            page.remove(self._groups.pop(k))

    # --- device ----------------------------------------------------------------------

    def _on_mount_added(self, _mon, mount):
        if _is_garmin_uri(mount.get_root().get_uri()):
            GLib.timeout_add(1500, self._device_arrived)

    def _device_arrived(self):
        self.refresh_device(auto_sync=True)
        return False

    def _on_mount_removed(self, _mon, mount):
        if _is_garmin_uri(mount.get_root().get_uri()):
            self.session = None
            self.maps_cache = self.updates_cache = self.apps_cache = self.music_cache = None
            self.show_device()

    def refresh_device(self, auto_sync: bool = False) -> None:
        def done(sess):
            self.session = sess
            self.maps_cache = self.updates_cache = self.apps_cache = self.music_cache = None
            self.show_device()
            if (auto_sync and self.gui_settings.get("auto_sync") and sess.logged_in
                    and not sess.first_sync):
                self.start_sync()

        def failed(e):
            self.session = None
            self.show_device()
            return True

        if self.busy:
            return
        self.run_task(_("Detecting the watch…"), service.open_session, done, failed)

    def show_device(self) -> None:
        s = self.session
        if s is None:
            self.device_stack.set_visible_child_name("empty")
            for page in (self.updates_page, self.maps_page, self.apps_page, self.music_page):
                self._clear_page(page)
            self._refresh_sync_page()
            return
        self.device_stack.set_visible_child_name("device")
        d = s.device
        g = self._reset_group(self.device_page, "info", d.description)
        for title, value in (
            # TRANSLATORS: watch software version
            (_("Software"), d.software_version_str),
            (_("Part number"), d.part_number),
            (_("Free space"), _human(s.storage.free_space())),
        ):
            g.add(Adw.ActionRow(title=title, subtitle=value, subtitle_selectable=True))

        g = self._reset_group(self.device_page, "account", _("Garmin Connect account"))
        if s.client.consumer is None:
            row = Adw.ActionRow(
                title=_("Setup required"),
                subtitle=_("Select the official Garmin Express installer (GarminExpress.exe)"),
            )
            row.add_suffix(self._button(_("Choose file…"), self.choose_dll, "suggested-action"))
            g.add(row)
        elif s.logged_in:
            row = Adw.ActionRow(title=_("Logged in"),
                                subtitle=GLib.markup_escape_text(s.client.auth.display))
            row.add_suffix(self._button(_("Log out"), self.logout))
            g.add(row)
        else:
            row = Adw.ActionRow(title=_("Not logged in"),
                                subtitle=_("Logging in is required for syncing"))
            row.add_suffix(self._button(_("Log in"), self.login, "suggested-action"))
            g.add(row)

        g = self._reset_group(self.device_page, "opts", _("Settings"))
        sw = Adw.SwitchRow(title=_("Sync when connected"),
                           subtitle=_("While the application window is open"),
                           active=bool(self.gui_settings.get("auto_sync")))
        sw.connect("notify::active", self._on_auto_sync)
        g.add(sw)
        g.add(self._background_row())
        row = Adw.ActionRow(title=_("Backup"),
                            subtitle=GLib.markup_escape_text(str(paths.default_backup_dir())))
        # TRANSLATORS: button that creates a backup
        row.add_suffix(self._button(_("Create"), self.backup))
        g.add(row)
        g.add(self._language_row())
        self._refresh_sync_page()

    def _on_auto_sync(self, row, _pspec):
        self.gui_settings["auto_sync"] = row.get_active()
        save_gui_settings(self.gui_settings)

    def _background_row(self) -> Adw.SwitchRow:
        from openexpress import background

        state = background.is_enabled()
        if state is None:
            state = bool(self.gui_settings.get("background"))
        row = Adw.SwitchRow(
            title=_("Sync in the background"),
            subtitle=_("Starts at login and syncs whenever the watch is connected, "
                       "also when this window is closed"),
            active=state)
        row.connect("notify::active", self._on_background)
        return row

    def _on_background(self, row, _pspec):
        from openexpress import background

        enabled = row.get_active()
        if enabled == bool(self.gui_settings.get("background")) and background.in_flatpak():
            return

        def done(ok: bool, error: str) -> None:
            if not ok:
                row.handler_block_by_func(self._on_background)
                row.set_active(not enabled)
                row.handler_unblock_by_func(self._on_background)
                self.error(_("Background sync"), error or _("The change failed."))
                return
            self.gui_settings["background"] = enabled
            save_gui_settings(self.gui_settings)
            self.toast(_("Background sync enabled") if enabled
                       else _("Background sync disabled"))

        background.set_enabled(enabled, done)

    def _language_row(self) -> Adw.ComboRow:
        codes = [""] + i18n.available_languages()
        names = [_("System default")] + [i18n.LANGUAGE_NAMES.get(c, c) for c in codes[1:]]
        current = load_ui_language()
        row = Adw.ComboRow(title=_("Language"), model=Gtk.StringList.new(names))
        row.set_selected(codes.index(current) if current in codes else 0)
        row.connect("notify::selected",
                    lambda r, _p: self._on_language(codes[r.get_selected()]))
        return row

    def _on_language(self, code: str) -> None:
        if code == load_ui_language():
            return
        try:
            save_ui_language(code)
        except (OSError, ValueError) as e:
            self.error(_("Language"), str(e))
            return
        self.toast(_("Restart the application to change the language"))

    def choose_dll(self) -> None:
        dialog = Gtk.FileDialog(
            title=_("Select the Garmin Express installer (GarminExpress.exe)"))

        def read(path: Path):
            if path.suffix.lower() == ".exe":
                from openexpress.installer import consumer_from_installer

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
            path = Path(f.get_path())

            def saved(consumer):
                save_consumer(consumer)
                self.toast(_("Application key saved"))
                self.refresh_device()

            self.run_task(_("Reading the installer…"), lambda: read(path), saved)

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
                prof = client.user_profile()
                auth.account = {k: prof.get(k) for k in
                                ("userprofilepk", "username", "emailaddress", "garminguid")}
                store.save(auth)
                return auth

            GLib.idle_add(lambda: self.run_task(
                _("Logging in…"), work,
                lambda auth: (self.toast(_("Logged in as {account}").format(account=auth.display)),
                              self.refresh_device())) and False)

        sso.create_login_window(settings, on_ticket, application=self.get_application(),
                                parent=self).present()

    def logout(self) -> None:
        store.delete()
        self.toast(_("Logged out"))
        self.refresh_device()

    def backup(self) -> None:
        from openexpress.backup import run_backup

        s = self.session
        if not s:
            return
        self.run_task(
            _("Creating backup…"),
            lambda: run_backup(s.device, s.storage, paths.default_backup_dir(),
                               progress=self.progress),
            lambda r: self.toast(ngettext("Copied {count} file to {path}",
                                          "Copied {count} files to {path}",
                                          r.files).format(count=r.files, path=r.path)),
        )

    # --- sync -------------------------------------------------------------------------

    def _build_sync_page(self) -> None:
        g = Adw.PreferencesGroup(title=_("Sync with Garmin Connect"))
        self.sync_row = Adw.ActionRow(title=_("Sync now"))
        # TRANSLATORS: button that starts syncing
        self.sync_button = self._button(_("Sync"), self.start_sync, "suggested-action")
        self.sync_row.add_suffix(self.sync_button)
        g.add(self.sync_row)
        self.sync_page.add(g)
        lg = Adw.PreferencesGroup(title=_("Last sync"))
        self.sync_log = Gtk.Label(xalign=0, wrap=True, selectable=True,
                                  label=_("Not synced yet in this session."))
        self.sync_log.add_css_class("dim-label")
        lg.add(self.sync_log)
        self.sync_page.add(lg)

    def _refresh_sync_page(self) -> None:
        s = self.session
        if s is None:
            self.sync_row.set_subtitle(_("No watch connected"))
        elif not s.logged_in:
            self.sync_row.set_subtitle(_("Log in on the Watch tab"))
        else:
            self.sync_row.set_subtitle(GLib.markup_escape_text(
                _("Account {account}").format(account=s.client.auth.display)))
        self.sync_button.set_sensitive(bool(s and s.logged_in) and not self.busy)

    def start_sync(self, first_run: str | None = None) -> None:
        s = self.session
        if not s or not s.logged_in:
            return

        def done(summary: service.SyncSummary):
            text = summary.text + ("\n\n" + _("Errors:") + "\n" + "\n".join(summary.errors)
                                   if summary.errors else "")
            self.sync_log.set_label(text)
            self.toast(_("Sync finished") if not summary.errors
                       else _("Sync finished with errors"))
            self._refresh_sync_page()

        def failed(e):
            if isinstance(e, service.FirstSyncChoiceNeeded):
                self.ask_first_sync()
                return True
            return False

        self.run_task(_("Syncing…"),
                      lambda: service.run_sync(s, first_run, self.progress), done, failed)

    def ask_first_sync(self) -> None:
        d = Adw.AlertDialog(
            heading=_("First sync"),
            body=_("If the watch also syncs via your phone, most data is already "
                   "in Garmin Connect. You can mark the current files as uploaded "
                   "and upload only new ones, or upload everything (Connect skips duplicates)."),
        )
        d.add_response("cancel", _("Cancel"))
        d.add_response("all", _("Upload everything"))
        d.add_response("baseline", _("New data only"))
        d.set_response_appearance("baseline", Adw.ResponseAppearance.SUGGESTED)
        d.set_default_response("baseline")
        d.connect("response", lambda _d, r: r != "cancel" and self.start_sync(r))
        d.present(self)

    # --- updates ----------------------------------------------------------------------

    def _on_page_changed(self, *_):
        name = self.stack.get_visible_child_name()
        if name == "updates" and self.session and self.updates_cache is None:
            self.load_updates()
        elif name == "maps" and self.session and self.maps_cache is None:
            self.load_maps()
        elif name == "apps" and self.session and self.apps_cache is None:
            self.load_apps()
        elif name == "music" and self.session and self.music_cache is None:
            self.load_music()

    def load_updates(self) -> None:
        from openexpress import updates as upd

        s = self.session
        if not s:
            return

        def work():
            resp = s.client.software_updates(s.device.raw, include_reinstalls=True)
            return upd.parse_updates(resp)

        self.run_task(_("Checking for updates…"), work, self.show_updates)

    def show_updates(self, items) -> None:
        self.updates_cache = items
        self._clear_page(self.updates_page)
        new = [u for u in items if not u.is_reinstall and not u.is_metadata]
        g = self._reset_group(self.updates_page, "new", _("Available updates"),
                              _("Software is up to date.") if not new else "")
        for u in new:
            self._update_row(g, u)
        re = [u for u in items if u.is_reinstall and not u.is_metadata]
        g = self._reset_group(
            # TRANSLATORS: group of updates that can be installed again
            self.updates_page, "re", _("Reinstall"),
            _("Components already on the watch. Useful for troubleshooting."),
        )
        exp = Adw.ExpanderRow(title=ngettext("Show {count} component", "Show {count} components",
                                             len(re)).format(count=len(re)))
        g.add(exp)
        for u in sorted(re, key=lambda x: x.name):
            self._update_row(exp, u, reinstall=True)

    def _update_row(self, parent, u, reinstall: bool = False) -> None:
        row = Adw.ActionRow(
            title=GLib.markup_escape_text(u.name),
            subtitle=GLib.markup_escape_text(
                f"{u.version} · {_human(u.size)} · {u.severity}"
                # TRANSLATORS: marks the main firmware in an update list
                + (" · " + _("system") if u.is_primary else "")),
        )
        # TRANSLATORS: button
        row.add_suffix(self._button(_("Reinstall") if reinstall else _("Install"),
                                    lambda u=u: self.confirm_update(u),
                                    None if reinstall else "suggested-action"))
        parent.add_row(row) if isinstance(parent, Adw.ExpanderRow) else parent.add(row)

    def confirm_update(self, u) -> None:
        from openexpress import updates as upd

        s = self.session
        chosen = upd.select(self.updates_cache, [u.part_number])
        body = (f"{u.name} {u.version}\n"
                + _("Size: {size}").format(size=_human(u.size)) + "\n"
                + _("On the watch: {path}").format(path=u.device_path)
                + ("\n\n" + _("Changes:") + "\n" + "\n".join("• " + c for c in u.changes[:10])
                   if u.changes else "")
                + ("\n\n" + _("License: {url}").format(url=u.eula_url) if u.eula_url else "")
                + ("\n\n" + _("Do not disconnect the watch while copying.")
                   if u.is_primary else ""))
        d = Adw.AlertDialog(heading=_("Install update?"), body=body)
        d.add_response("cancel", _("Cancel"))
        d.add_response("ok", _("Accept and install"))
        d.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)

        def response(_d, r):
            if r != "ok":
                return
            self.run_task(
                _("Installing update…"),
                lambda: upd.install(chosen, s.storage, session=s.client.http,
                                    progress=self.progress),
                lambda rep: self.error(_("Done"), _("Files copied to the watch.")
                                       + (_(RESTART_STEPS) if rep.restart_required else "")),
            )

        d.connect("response", response)
        d.present(self)

    # --- maps -------------------------------------------------------------------------

    def load_maps(self) -> None:
        from openexpress import maps as mp

        s = self.session
        if not s:
            return
        self.run_task(_("Checking maps…"),
                      lambda: mp.parse_maps(s.client.universal_maps(s.device.raw)),
                      self.show_maps)

    def show_maps(self, all_maps) -> None:
        self.maps_cache = all_maps
        self._clear_page(self.maps_page)
        free = self.session.storage.free_space() if self.session else None
        g = self._reset_group(self.maps_page, "installed", _("On the watch"),
                              _("Free space: {size}").format(size=_human(free)))
        for m in sorted((m for m in all_maps if m.installed), key=lambda m: m.name):
            row = Adw.ActionRow(title=GLib.markup_escape_text(m.name),
                                subtitle=GLib.markup_escape_text(
                                    f"{m.version} · {_human(m.size)} · {m.status}"))
            if m.update_available:
                # TRANSLATORS: button
                row.add_suffix(self._button(_("Update"), lambda m=m: self.confirm_map(m),
                                            "suggested-action"))
            if m.can_uninstall:
                # TRANSLATORS: button
                row.add_suffix(self._button(_("Remove"), lambda m=m: self.confirm_remove(m),
                                            "destructive-action"))
            g.add(row)
        # TRANSLATORS: group title: maps that can be installed
        g = self._reset_group(self.maps_page, "available", _("Available"),
                              _("Free maps available for this watch"))
        for m in sorted((m for m in all_maps if not m.installed), key=lambda m: m.name):
            row = Adw.ActionRow(title=GLib.markup_escape_text(m.name),
                                subtitle=GLib.markup_escape_text(
                                    f"{m.region} · {m.version} · {_human(m.size)}"))
            row.add_suffix(self._button(_("Install"), lambda m=m: self.confirm_map(m)))
            g.add(row)

    def confirm_map(self, m) -> None:
        from openexpress import maps as mp

        s = self.session
        if not (s.client.consumer and s.client.consumer.url_salt):
            self.error(_("Setup missing"),
                       _("Downloading maps requires the Utilities.dll file from Garmin Express.\n"
                         "Run: openexpress setup --utilities-dll /path/to/Utilities.dll"))
            return
        removing = mp.removal_list(self.maps_cache, m, rewriting=True)
        need = mp.space_needed(s.storage, m, removing)
        body = (_("Download: {size}").format(size=_human(m.size)) + "\n"
                + _("Space on the watch: {needed} ({free} free)").format(
                    needed=_human(need), free=_human(s.storage.free_space())) + "\n"
                + _("Files: {count}").format(count=len(m.files))
                + ("\n" + _("Old files to remove: {count}").format(count=len(removing))
                   if removing else "")
                + "".join("\n\n" + _("License: {url}").format(url=e) for e in m.eula_urls)
                + "\n\n" + _("Copying large maps over USB can take tens of minutes."))
        heading = (_("Update {name}?") if m.installed else _("Install {name}?")).format(
            name=m.name)
        d = Adw.AlertDialog(heading=heading, body=body)
        d.add_response("cancel", _("Cancel"))
        d.add_response("ok", _("Accept and install"))
        d.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)

        def response(_d, r):
            if r != "ok":
                return
            self.run_task(
                _("Installing {name}…").format(name=m.name),
                lambda: mp.install(m, self.maps_cache, s.storage,
                                   activate=lambda ident: s.client.activate_maps(
                                       s.device.raw, [ident]),
                                   session=s.client.http, progress=self.progress,
                                   transfer_progress=self.fraction,
                                   url_salt=s.client.consumer.url_salt),
                lambda rep: (self.toast(_("Installed {name}").format(name=m.name)),
                             self.load_maps()),
            )

        d.connect("response", response)
        d.present(self)

    def confirm_remove(self, m) -> None:
        from openexpress import maps as mp

        s = self.session
        files = mp.removal_list(self.maps_cache, m, rewriting=False)
        d = Adw.AlertDialog(heading=_("Remove {name}?").format(name=m.name),
                            body=_("These files will be removed from the watch:") + "\n"
                            + "\n".join(files))
        d.add_response("cancel", _("Cancel"))
        d.add_response("ok", _("Remove"))
        d.set_response_appearance("ok", Adw.ResponseAppearance.DESTRUCTIVE)
        d.connect("response", lambda _d, r: r == "ok" and self.run_task(
            _("Removing {name}…").format(name=m.name),
            lambda: mp.uninstall(m, self.maps_cache, s.storage, progress=self.progress),
            lambda removed: (self.toast(self._removed_files(len(removed))), self.load_maps())))
        d.present(self)


    def show_about(self) -> None:
        from openexpress import __version__

        about = Adw.AboutDialog(
            application_name="openExpress",
            application_icon=APP_ID,
            version=__version__,
            developer_name="Tomasz Bojanowski",
            website="https://github.com/TomaszBojanowski/openExpress",
            issue_url="https://github.com/TomaszBojanowski/openExpress/issues",
            license_type=Gtk.License.GPL_3_0,
            comments=_("Unofficial Linux app for Garmin watches: sync with Garmin Connect, "
                       "software updates, maps, Connect IQ apps and music.\n\n"
                       "openExpress is not affiliated with, endorsed by or supported by "
                       "Garmin Ltd. Garmin, fēnix, Garmin Connect and Garmin Express are "
                       "trademarks of Garmin Ltd. or its subsidiaries."),
        )
        # TRANSLATORS: replace with your name(s) in the translated version
        credits = _("translator-credits")
        if credits != "translator-credits":
            about.set_translator_credits(credits)
        about.present(self)

    # --- Connect IQ --------------------------------------------------------------------

    def load_apps(self) -> None:
        from openexpress import ciq

        s = self.session
        if not s:
            return
        if not s.logged_in:
            self._clear_page(self.apps_page)
            self._reset_group(self.apps_page, "info", "Connect IQ",
                              _("Sign in on the Watch tab to manage apps."))
            return

        def work():
            api = ciq.StoreApi(s.client)
            locale = s.settings.locale.replace("_", "-")
            state = ciq.read_installed(s.device, s.storage)
            account = api.account_apps(s.device, locale)
            updates = api.updates(s.device, state.store_apps, locale)
            return state, account, updates

        self.run_task(_("Loading Connect IQ apps…"), work, self.show_apps)

    def show_apps(self, result) -> None:
        state, account, updates = result
        self.apps_cache = result
        self._clear_page(self.apps_page)
        upd = {u.app_id: u for u in updates}
        g = self._reset_group(
            self.apps_page, "installed", _("On the watch"),
            self._apps_space(state))
        if updates:
            b = self._button(_("Update all"), lambda: self.install_apps(updates),
                             "suggested-action")
            g.set_header_suffix(b)
        for a in state.store_apps:
            sub = f"{a.type_name} · v{a.version or '?'}"
            if a.disabled:
                # TRANSLATORS: state of a Connect IQ app
                sub += " · " + _("disabled")
            if a.app_id in upd:
                sub += " · " + _("update {version}").format(version=upd[a.app_id].version_name)
            row = Adw.ActionRow(title=GLib.markup_escape_text(a.name),
                                subtitle=GLib.markup_escape_text(sub))
            if a.app_id in upd:
                row.add_suffix(self._button(_("Update"),
                                            lambda u=upd[a.app_id]: self.install_apps([u]),
                                            "suggested-action"))
            if a.removable:
                row.add_suffix(self._button(_("Remove"), lambda a=a: self.confirm_remove_app(a),
                                            "destructive-action"))
            g.add(row)
        installed = {a.app_id for a in state.apps}
        missing = [a for a in account if a.app_id not in installed]
        g = self._reset_group(
            self.apps_page, "account", _("In your Garmin account"),
            _("Apps you added in the Connect IQ store (website or phone).") if missing
            else _("Add apps in the Connect IQ store (website or phone); "
                   "they will show up here."))
        for a in missing:
            row = Adw.ActionRow(title=GLib.markup_escape_text(a.name),
                                subtitle=GLib.markup_escape_text(
                                    f"{self._store_type_name(a)} · {a.version_name} · "
                                    f"{_human(a.size)} · "
                                    f"{a.developer}"))
            row.add_suffix(self._button(_("Install"), lambda a=a: self.install_apps([a])))
            g.add(row)
        g = self._reset_group(self.apps_page, "builtin", _("Built-in activities and apps"),
                              _("Changes take effect after you disconnect the watch."))
        n = sum(1 for a in state.apps if a.preloaded)
        exp = Adw.ExpanderRow(title=ngettext("Show {count} item", "Show {count} items",
                                             n).format(count=n))
        g.add(exp)
        for a in sorted((a for a in state.apps if a.preloaded), key=lambda a: a.name):
            sw = Adw.SwitchRow(title=GLib.markup_escape_text(a.name),
                               subtitle=GLib.markup_escape_text(a.type_name),
                               active=not a.disabled)
            sw.connect("notify::active", lambda row, _p, a=a: self.toggle_app(a, row.get_active()))
            exp.add_row(sw)

    @staticmethod
    def _store_type_name(app) -> str:
        from openexpress import ciq

        name = ciq.APP_TYPES.get(app.app_type) if app.store_type in ciq.STORE_TYPES else None
        return _(name) if name else app.store_type

    @staticmethod
    def _apps_space(state) -> str:
        slots = state.available_slots
        space = _human(state.available_space)
        if slots is None:
            return _("{size} free").format(size=space)
        return ngettext("{count} free slot · {size} free", "{count} free slots · {size} free",
                        slots).format(count=slots, size=space)

    def install_apps(self, apps) -> None:
        from openexpress import ciq

        s = self.session

        def work():
            api = ciq.StoreApi(s.client)
            api.queue_install(s.device, apps)
            return ciq.install_queued(s.device, s.storage, s.client,
                                      s.client.device_messages(s.device.unit_id),
                                      {a.app_id: a for a in apps},
                                      only={a.app_id for a in apps}, progress=self.progress)

        def done(rep):
            if rep.failed:
                self.error("Connect IQ", "\n".join(f"{n}: {e}" for n, e in rep.failed))
            elif rep.installed:
                self.toast(_("Installed {names}").format(names=", ".join(rep.installed)))
            else:
                self.toast(_("Queued; it will be installed on the next sync"))
            self.load_apps()

        self.run_task(_("Installing apps…"), work, done)

    def confirm_remove_app(self, app) -> None:
        from openexpress import ciq

        s = self.session
        d = Adw.AlertDialog(heading=_("Remove {name}?").format(name=app.name),
                            body=_("The app and its settings will be deleted from the watch."))
        d.add_response("cancel", _("Cancel"))
        d.add_response("forget", _("Remove and forget"))
        d.add_response("ok", _("Remove"))
        d.set_response_appearance("ok", Adw.ResponseAppearance.DESTRUCTIVE)

        def response(_d, r):
            if r == "cancel":
                return

            def work():
                removed = ciq.remove(s.device, s.storage, app)
                if r == "forget":
                    ciq.StoreApi(s.client).forget(s.device, [app.app_id])
                return removed

            self.run_task(_("Removing {name}…").format(name=app.name), work,
                          lambda removed: (self.toast(_("Removed {name}").format(name=app.name)),
                                           self.load_apps()))

        d.connect("response", response)
        d.present(self)

    def toggle_app(self, app, enabled: bool) -> None:
        from openexpress import ciq

        if (not app.disabled) == enabled:
            return
        s = self.session
        app.disabled = not enabled
        msg = _("{name} enabled") if enabled else _("{name} disabled")
        self.run_task(_("Saving…"),
                      lambda: ciq.set_enabled(s.device, s.storage, app.app_id, enabled),
                      lambda _r: self.toast(msg.format(name=app.name)))

    # --- music ------------------------------------------------------------------------

    def load_music(self) -> None:
        from openexpress import music

        s = self.session
        if not s:
            return
        self.run_task(_("Reading music on the watch…"),
                      lambda: (music.list_device(s.device, s.storage),
                               music.list_playlists(s.device, s.storage)),
                      self.show_music)

    def show_music(self, result) -> None:
        from openexpress import music

        items, playlists = result
        self.music_cache = result
        self._clear_page(self.music_page)
        free = self.session.storage.free_space() if self.session else None
        g = self._reset_group(self.music_page, "add", _("Add to the watch"),
                              _("Free space: {size}").format(size=_human(free)))
        for kind, label in music.KIND_NAMES.items():
            row = Adw.ActionRow(title=_(label))
            row.add_suffix(self._button(_("Folders…"), lambda k=kind: self.choose_music(k, True)))
            row.add_suffix(self._button(_("Files…"), lambda k=kind: self.choose_music(k, False)))
            g.add(row)
        on_watch = {"track": _("Music on the watch"), "podcast": _("Podcasts on the watch"),
                    "audiobook": _("Audiobooks on the watch")}
        for kind in music.KIND_NAMES:
            files = [i for i in items if i.kind == kind]
            n = len(files)
            g = self._reset_group(
                self.music_page, kind, on_watch[kind],
                ngettext("{count} file · {size}", "{count} files · {size}", n).format(
                    count=n, size=_human(sum(f.size for f in files))))
            if not files:
                continue
            exp = Adw.ExpanderRow(title=ngettext("Show {count} file", "Show {count} files",
                                                 n).format(count=n))
            g.add(exp)
            for f in files:
                row = Adw.ActionRow(title=GLib.markup_escape_text(f.path.split("/")[-1]),
                                    subtitle=GLib.markup_escape_text(f.path))
                row.add_suffix(self._button(_("Remove"), lambda p=f.path: self.remove_music([p]),
                                            "destructive-action"))
                exp.add_row(row)
        if playlists:
            g = self._reset_group(self.music_page, "playlists", _("Playlists"))
            for p in playlists:
                row = Adw.ActionRow(title=GLib.markup_escape_text(p.split("/")[-1]))
                row.add_suffix(self._button(_("Remove"), lambda p=p: self.remove_music([p]),
                                            "destructive-action"))
                g.add(row)

    def choose_music(self, kind: str, folders: bool) -> None:
        dialog = Gtk.FileDialog(title=_("Choose music to copy to the watch"))

        def chosen(dlg, res):
            try:
                files = (dlg.select_multiple_folders_finish(res) if folders
                         else dlg.open_multiple_finish(res))
            except GLib.Error:
                return
            paths_ = [Path(files.get_item(i).get_path()) for i in range(files.get_n_items())]
            self.add_music(paths_, kind)

        if folders:
            dialog.select_multiple_folders(self, None, chosen)
        else:
            dialog.open_multiple(self, None, chosen)

    def add_music(self, paths_, kind: str) -> None:
        from openexpress import music

        s = self.session

        def plan():
            tracks = music.scan(paths_, s.device, forced_kind=None if kind == "track" else kind)
            return music.plan_copy(tracks, s.device, s.storage)

        def confirm(p):
            if not p.to_copy:
                self.toast(_("Nothing new to copy"))
                return
            n = len(p.to_copy)
            d = Adw.AlertDialog(
                heading=ngettext("Copy {count} file?", "Copy {count} files?", n).format(count=n),
                body=ngettext("{size} will be copied to the watch ({count} file already there).",
                              "{size} will be copied to the watch ({count} files already there).",
                              len(p.already)).format(size=_human(p.size), count=len(p.already)))
            d.add_response("cancel", _("Cancel"))
            # TRANSLATORS: button
            d.add_response("ok", _("Copy"))
            d.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
            d.connect("response", lambda _d, r: r == "ok" and self.run_task(
                _("Copying music…"),
                lambda: music.copy(p, s.storage, progress=self.progress,
                                   transfer_progress=self.fraction),
                lambda written: (self.toast(ngettext("Copied {count} file", "Copied {count} files",
                                                     len(written)).format(count=len(written))),
                                 self.load_music())))
            d.present(self)

        self.run_task(_("Scanning files…"), plan, confirm)

    def remove_music(self, device_paths) -> None:
        from openexpress import music

        s = self.session
        self.run_task(_("Removing…"), lambda: music.remove(s.storage, device_paths),
                      lambda removed: (self.toast(self._removed_files(len(removed))),
                                       self.load_music()))


class App(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)

    def do_startup(self):
        Adw.Application.do_startup(self)
        # Lets the icon work also when running from the source tree (not installed).
        from gi.repository import Gdk

        Gtk.IconTheme.get_for_display(Gdk.Display.get_default()).add_search_path(
            str(Path(__file__).resolve().parent.parent / "data"))
        Gtk.Window.set_default_icon_name(APP_ID)

    def do_activate(self):
        win = self.get_active_window() or MainWindow(self)
        win.present()


def main(argv=None) -> int:
    return App().run(argv or [])


if __name__ == "__main__":
    raise SystemExit(main())
