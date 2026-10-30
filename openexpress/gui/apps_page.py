"""The Apps page of the window: the Connect IQ apps on the device and on the Garmin account,
their install, update, removal, switch and order (ciq.py decides what is written where).
AppsPage is a mixin of MainWindow (gui/app.py), without __init__ of its own.

Has: what the Apps page keeps on the window: _updated_apps, apps_count, _order_dialog.
"""
from __future__ import annotations

import logging

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, GLib, Gtk  # noqa: E402 - imports must follow gi.require_version

from openexpress import service  # noqa: E402
from openexpress.gui.pictures import cached_download, load_texture  # noqa: E402
from openexpress.gui.tasks import read_again_after_failure, writes_to_device  # noqa: E402
from openexpress.gui.window_tasks import Answer, confirm, inform  # noqa: E402
from openexpress.gui.words import space_text  # noqa: E402
from openexpress.i18n import _, caption, human_size, ngettext  # noqa: E402
from openexpress.settings import language_tag  # noqa: E402

log = logging.getLogger(__name__)


def order_list_at_its_top(box) -> bool:
    """The list of copies of "Restore the order from a copy", once shown: no row chosen
    (Next waits for the user's choice) and scrolled to its top, its first row whole. Run
    when the window is idle after the dialog is shown: the list chooses its first row as it
    takes the focus then. False: once."""
    box.unselect_all()
    scroller = box.get_ancestor(Gtk.ScrolledWindow)
    if scroller is not None:
        adjustment = scroller.get_vadjustment()
        adjustment.set_value(adjustment.get_lower())
    return False


def apps_count_text(count: int) -> str:
    """The subtitle of "Apps" on the device page: the apps the Apps page lists "On the
    device"."""
    # TRANSLATORS: subtitle of the row "Apps" of the device page
    return ngettext("{count} Connect IQ app", "{count} Connect IQ apps", count).format(
        count=count)


def note_apps_count(win, count: int | None) -> None:
    """How many apps the Apps page found on the open device (None: not read yet), kept for
    the session and said by the row "Apps" of the device page."""
    win.apps_count = count
    row = win.apps_row
    if row is not None:
        row.set_subtitle(apps_count_text(count) if count is not None else "")


def refused_dirs_text(dirs: list[str]) -> str | None:
    """One line on the Apps page: the app folders the device refused to create with its
    software now (ciq.json); None: none."""
    if not dirs:
        return None
    # TRANSLATORS: {folders} are folders of the device, e.g. "garmin/apps/media"
    return _("The device did not let openExpress create {folders} with this software "
             "version: apps that go there stay queued in Garmin Connect.").format(
        folders=", ".join(dirs))


class AppsPage:
    def load_apps(self) -> None:
        from openexpress import ciq

        s = self.session
        sec = self.sections["apps"]
        if not s.logged_in:
            sec.clear()
            sec.error.set_title("Connect IQ")
            sec.error.set_description(GLib.markup_escape_text(_("Sign in to manage apps.")))
            sec.show("error")
            return
        if s.ownership_error is not None:
            sec.clear()
            sec.error.set_title("Connect IQ")
            # Markup: a network error names <path>, <hex> or "<… object at …>" (escaped).
            sec.error.set_description(GLib.markup_escape_text(str(s.ownership_error)))
            sec.show("error")
            return

        def work():
            api = ciq.StoreApi(s.client)
            locale = language_tag(s.settings.locale)
            state = ciq.read_installed(s.device, s.storage)
            # How each app can be removed, told by its own file alone (as on the command
            # line): an app whose file is out of reach cannot be removed over USB.
            for app in state.store_apps:
                way = ciq.removal_way(s.device, s.storage, app)
                app.has_files = way == ciq.MOUNTED
                app.direct_removal = way == ciq.DIRECT
                app.sandboxed = way == ciq.SANDBOXED
            account = api.account_apps(s.device, locale)
            ciq.name_pending(state, account)
            updates = api.updates(s.device, state.store_apps, locale)
            return state, account, updates

        self._load("apps", _("Loading Connect IQ apps…"), work, self.show_apps)

    def show_apps(self, result) -> None:
        if self.session is None:
            return
        state, account, updates = result
        sec = self.sections["apps"]
        if not state.waiting:
            # No app list waits on the device any more, so no update can be waiting either.
            self._updated_apps = set()
        updates = self._updates_to_offer(updates, self._updated_apps)
        upd = {u.app_id: u for u in updates}
        icons: dict = {}
        from openexpress import ciq

        note_apps_count(self, len(state.store_apps))  # the row "Apps" of the device page
        # A folder the device refused with its software now: one line.
        refused = refused_dirs_text(ciq.refused_dirs(self.session.device))
        # No app list yet: none on the device; the account's apps can be installed.
        space = ciq.no_apps_text() if state.no_app_list else self._apps_space(state)
        g = sec.group(_("On the device"), f"{space}\n{refused}" if refused else space)
        if updates:
            b = self._button(_("Update all"), lambda: self.install_apps(updates),
                             "suggested-action")
            g.set_header_suffix(b)
        for a in state.store_apps:
            sub = caption(a.type_name, f"v{a.version or '?'}")
            if a.disabled:
                # TRANSLATORS: state of a Connect IQ app
                sub = caption(sub, _("disabled"))
            waiting = self._waiting_note(a, a.app_id in self._updated_apps)
            if waiting:
                # Written or deleted already; the device applies it once it is unplugged.
                row = Adw.ActionRow(title=GLib.markup_escape_text(a.name),
                                    subtitle=GLib.markup_escape_text(f"{sub}\n{waiting}"),
                                    subtitle_lines=2)
                icons[a.app_id] = self._app_icon(row)
                row.set_sensitive(not a.pending_removal)
                g.add(row)
                continue
            if a.app_id in upd:
                sub = caption(sub, _("update {version}").format(
                    version=upd[a.app_id].version_name))
                if getattr(upd[a.app_id], "permissions_changed", False):
                    # TRANSLATORS: in the line of an app whose update asks for permissions
                    # the user has to allow first
                    sub = caption(sub, _("new permissions"))
            row = Adw.ActionRow(title=GLib.markup_escape_text(a.name),
                                subtitle=GLib.markup_escape_text(sub))
            icons[a.app_id] = self._app_icon(row)
            if a.app_id in upd:
                row.add_suffix(self._button(_("Update"),
                                            lambda u=upd[a.app_id]: self.install_apps([u]),
                                            "suggested-action"))
            if a.removable:
                row.add_suffix(self._trash(_("Remove {name}").format(name=a.name),
                                           lambda a=a: self.confirm_remove_app(a)))
            g.add(row)
        g = sec.group(_("Order"))
        blocked = self._order_blocked(state)
        row = Adw.ActionRow(
            title=_("Restore the order from a copy"),
            subtitle=GLib.markup_escape_text(
                blocked or _("A copy of the app list is made before every change")))
        row.add_suffix(self._button(_("Restore…"), lambda button: self.choose_order(button),
                                    with_button=True))
        if blocked:
            row.set_tooltip_text(blocked)
            row.set_sensitive(False)
        g.add(row)
        installed = {a.app_id for a in state.apps}
        missing = [a for a in account if a.app_id not in installed]
        g = sec.group(
            _("In your Garmin account"),
            _("Apps you added in the Connect IQ store (website or phone).") if missing
            else _("Add apps in the Connect IQ store (website or phone); "
                   "they will show up here."))
        for a in missing:
            row = Adw.ActionRow(title=GLib.markup_escape_text(a.name),
                                subtitle=GLib.markup_escape_text(
                                    caption(self._store_type_name(a), a.version_name,
                                            human_size(a.size), a.developer,
                                            *([_("new permissions")] if a.permissions_changed
                                              else []))))
            icons[a.app_id] = self._app_icon(row)
            row.add_suffix(self._button(_("Install"), lambda a=a: self.install_apps([a])))
            g.add(row)
        self._load_app_icons(icons)

        builtin = sorted((a for a in state.apps if a.preloaded), key=lambda a: a.name)
        if not builtin:
            return  # none known (no app list): no group of none
        g = sec.group(_("Built-in activities and apps"),
                      _("Changes take effect after you disconnect the device."))
        search = Gtk.SearchEntry(placeholder_text=_("Search activities"), margin_bottom=12)
        g.add(search)
        exp = Adw.ExpanderRow(title=ngettext("Show {count} item", "Show {count} items",
                                             len(builtin)).format(count=len(builtin)))
        listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        listbox.add_css_class("boxed-list")
        listbox.append(exp)
        g.add(listbox)
        rows = []
        for a in builtin:
            sw = Adw.SwitchRow(title=GLib.markup_escape_text(a.name),
                               subtitle=GLib.markup_escape_text(a.type_name),
                               active=not a.disabled, sensitive=not self.busy)
            handler: list[int] = []
            handler.append(sw.connect(
                "notify::active",
                lambda row, _p, a=a, h=handler: self.toggle_app(a, row, h[0])))
            self._action_buttons.append(sw)
            exp.add_row(sw)
            rows.append((sw, a.name.casefold()))
        self._search_filter(search, rows, exp)

    @staticmethod
    def _app_icon(row: Adw.ActionRow) -> Gtk.Image:
        image = Gtk.Image(icon_name="application-x-addon-symbolic", pixel_size=32)
        row.add_prefix(image)
        return image

    def _load_app_icons(self, images: dict) -> None:
        from openexpress import ciq

        s = self.session
        if not images or not s.logged_in or s.ownership_error is not None:
            return

        def work():
            urls = ciq.StoreApi(s.client).icon_urls(list(images))
            return {app_id: cached_download(s.client.http, url, f"app-{app_id}.png")
                    for app_id, url in urls.items()}

        def done(files):
            for app_id, path in files.items():
                texture = load_texture(path) if path else None
                if texture and app_id in images:
                    images[app_id].set_from_paintable(texture)
                    images[app_id].set_pixel_size(32)

        self.in_background(work, done)

    @staticmethod
    def _store_type_name(app) -> str:
        from openexpress import ciq

        name = ciq.APP_TYPES.get(app.app_type) if app.store_type in ciq.STORE_TYPES else None
        return _(name) if name else app.store_type

    @staticmethod
    def _apps_space(state) -> str:
        """The free slots and the free space for apps, the space in the words of every page
        (space_text)."""
        space = space_text(state.available_space)
        slots = state.available_slots
        if slots is None:
            return space
        return caption(ngettext("{count} free slot", "{count} free slots", slots).format(
            count=slots), space)

    @staticmethod
    def _waiting_note(app, updated: bool = False) -> str:
        """For an app whose install, removal or update waits for the device to be
        unplugged. `updated`: a new version was written in this run of the program."""
        if app.pending:
            return _("Will be installed after the device is unplugged")
        if app.pending_removal:
            return _("Will be removed after the device is unplugged")
        if updated:
            return _("Will be updated after the device is unplugged")
        return ""

    @staticmethod
    def _updates_to_offer(updates, updated_ids) -> list:
        """The available updates without those already written to the device."""
        return [u for u in updates if u.app_id not in updated_ids]

    # --- the order of the apps -------------------------------------------------------

    @staticmethod
    def _order_blocked(state) -> str:
        """Why the order cannot be changed now; empty when it can."""
        if state.unreadable:
            return _("The app list on the device has entries that cannot be read")
        if state.waiting:
            return _("The device has not applied earlier changes yet. Unplug it, wait until "
                     "it is ready and try again.")
        return ""

    @staticmethod
    def _order_rows(choices) -> list[tuple[str, str, bool]]:
        """(title, subtitle, can be chosen) for each copy, newest first. Only the name of
        a copy is shown, never its path."""
        from openexpress import ciq

        rows = []
        for copy, plan in choices:
            # The apps to move, not the positions that change.
            moved = ciq.apps_moved(plan)
            rows.append((ciq.copy_title(copy),
                         ciq.order_difference(plan) if moved else _("The same order as now"),
                         bool(moved)))
        return rows

    @staticmethod
    def _order_summary(plan, copy_note: str) -> str:
        from openexpress import ciq

        lines = [("  " if indented else "") + text
                 for text, indented in ciq.describe_order(plan, 10)]
        lines += ["", _("The same apps with the same flags; only the order changes."), copy_note]
        return "\n".join(lines)

    def choose_order(self, button: Gtk.Button | None = None) -> None:
        """Pick one of the saved copies of the app list to take the order from; read in a
        task started from `button` (Restore…), which spins meanwhile."""
        from openexpress import ciq

        s = self.session

        def work():
            state = ciq.read_installed(s.device, s.storage)
            # read_installed read the app list (OUT.BIN): it is there, nothing more to ask.
            return state, ciq.order_choices(s.device, state), ciq.copy_note(True)

        def show(result):
            state, choices, note = result
            blocked = self._order_blocked(state)
            if blocked:
                self.error(_("Restore the order from a copy"), blocked)
                return
            if not choices:
                self.error(_("Restore the order from a copy"),
                           _("There are no copies of the app list yet. A copy is made before "
                             "every change of the apps on the device."))
                return
            # Only the copies that would change something.
            rows = [(row, choice) for row, choice in zip(self._order_rows(choices), choices)
                    if row[2]]
            if not rows:
                inform(self, "order-none-differs", _("Restore the order from a copy"),
                       _("No copy differs from the current order"), label=_("Close"))
                return
            box = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
            box.add_css_class("boxed-list")
            for (title, subtitle, usable), choice in rows:
                row = Adw.ActionRow(title=GLib.markup_escape_text(title),
                                    subtitle=GLib.markup_escape_text(subtitle))
                row.set_sensitive(usable)
                row.set_selectable(usable)
                row.choice = choice
                box.append(row)
            scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                          propagate_natural_height=True, max_content_height=360)
            scroller.set_child(box)
            # The window holds the dialog while it is open (keep_as): a handler that held the
            # dialog, or the list, would keep both after it closed. The copy chosen (data, no
            # widget) stays with the answer (confirm says why).
            picked = {"choice": None}

            def selected(_box, row):
                picked["choice"] = row.choice if row is not None else None
                dialog = self._order_dialog
                if dialog is not None:
                    dialog.set_response_enabled("next", row is not None)

            def chosen():
                if picked["choice"] is not None:
                    self.confirm_order(*picked["choice"], note)

            box.connect("row-selected", selected)
            confirm(self, "order-choose", _("Restore the order from a copy"),
                    _("Choose the copy of the app list to take the order from."),
                    # TRANSLATORS: button that leads to the summary of the change
                    Answer("next", _("Next"), chosen, enabled=False),
                    extra=scroller, keep_as="_order_dialog")
            GLib.idle_add(order_list_at_its_top, box)

        self.run_task(_("Reading the copies of the app list…"), work, show, button=button)

    def confirm_order(self, copy, plan, note: str) -> None:
        """`note`: what happens before the change (ciq.copy_note), told by choose_order with
        the copies it read: the question asks the device nothing."""
        from openexpress import ciq

        confirm(self, "order-restore", _("Restore this order?"),
                ciq.copy_title(copy) + "\n\n" + self._order_summary(plan, note),
                Answer("ok", _("Restore the Order"), lambda: self.restore_order(copy),
                       look="suggested"))

    def restore_order(self, copy) -> None:
        from openexpress import ciq

        s = self.session

        def done(result):
            plan, _saved = result
            self.toast(_("Wrote the new order to the device; it takes effect after the device "
                         "is unplugged.") if plan.moved
                       else _("The apps on the device are already in this order."))
            self.load_section("apps", force=True)

        self.run_task(_("Restoring the order…"),
                      writes_to_device(lambda: ciq.restore_order(s.device, s.storage,
                                                                 copy.state)), done,
                      read_again_after_failure(self, "apps"))

    def install_apps(self, apps) -> None:
        """Install or update these apps; those with new permissions only once the user
        allows them (one question for all of them, as Garmin Express asks before each)."""
        from openexpress import ciq

        asking = ciq.needs_consent(apps)
        if asking:
            self.confirm_permissions(apps, asking)
            return
        self._install_apps(apps)

    def confirm_permissions(self, apps, asking) -> None:
        from openexpress import ciq

        count = len(asking)
        others = [a for a in apps if a not in asking]
        allowed = {a.app_id for a in asking}
        body = "\n\n".join([*(ciq.permissions_text(a) for a in asking), ngettext(
            "openExpress installs it only if you allow them.",
            "openExpress installs them only if you allow them.", count)])
        # The choice is data of these closures, not of the dialog (confirm says why).
        answers = [Answer(
            # TRANSLATORS: answer to the question about new permissions: skip the apps that
            # ask for them and install or update only the others
            "others", _("Skip These Apps"), lambda: self._install_apps(others))] if others else []
        answers.append(Answer("allow", _("Allow"), lambda: self._install_apps(apps, allowed),
                              look="suggested"))
        confirm(self, "app-permissions", ngettext(
            "Allow New Permissions?", "Allow New Permissions for {count} Apps?",
            count).format(count=count), body, *answers, keep_as="_permissions_dialog")

    def _install_apps(self, apps, allowed=()) -> None:
        from openexpress import ciq

        s = self.session

        def work():
            service.require_account_device(s.client, s.device.unit_id, fresh=True)
            return ciq.install_apps(s.device, s.storage, s.client, ciq.StoreApi(s.client),
                                    apps, progress=self.progress, allowed=allowed)

        def done(rep):
            if rep.needs_consent:  # only by a mistake of the program: it asked first
                log.warning("apps: %d not installed, their new permissions not allowed",
                            len(rep.needs_consent))
            self._updated_apps = set(self._updated_apps) | set(rep.updated_ids)
            if rep.failed:
                # What was written first, then each app that was not (the USB connection
                # to renew, if any, comes last).
                self.error("Connect IQ", "\n".join(
                    [*AppsPage._written_apps(rep), *(f"{n}: {e}" for n, e in rep.failed)]))
            elif rep.for_phone and not rep.installed:
                self.error(_("Queued in Garmin Connect"), self._for_phone_text(
                    rep.for_phone, rep.sandboxed, rep.sandboxed_updates, rep.no_app_list))
            elif rep.installed and all(n in rep.updated for n in rep.installed):
                self.toast(ciq.update_written_text(rep.installed))
            elif rep.installed and all(n in rep.direct for n in rep.installed):
                self.toast(ciq.written_text(rep.installed))
            elif rep.installed:
                self.toast(_("Installed {names}").format(names=", ".join(rep.installed)))
            else:
                self.toast(_("Queued; it will be installed on the next sync"))
            self.load_section("apps", force=True)

        self.run_task(_("Installing apps…"), writes_to_device(work), done,
                      read_again_after_failure(self, "apps"))

    @staticmethod
    def _written_apps(rep) -> list[str]:
        """What an install wrote, in the words of its toasts, a line for each kind: said
        before its failures when some apps were not installed."""
        from openexpress import ciq

        written = [n for n in rep.installed if n in rep.direct and n not in rep.updated]
        updated = [n for n in rep.installed if n in rep.updated]
        plain = [n for n in rep.installed if n not in rep.direct]
        return ([ciq.written_text(written)] if written else []) + (
            [ciq.update_written_text(updated)] if updated else []) + (
            [_("Installed {names}").format(names=", ".join(plain))] if plain else [])

    @staticmethod
    def _for_phone_text(names, sandboxed=(), updates=(), no_app_list: bool = False) -> str:
        from openexpress import ciq

        if no_app_list:
            return ciq.no_app_list_text(names)
        if names and all(n in sandboxed for n in names):
            return ", ".join(names) + ": " + ciq.sandbox_text(
                all(n in updates for n in names), queued=True)
        return _("{names}: openExpress cannot install apps on this device over USB yet. The "
                 "app waits in the queue in Garmin Connect and will be installed by the phone "
                 "or Garmin Express.").format(
            names=", ".join(names))

    @staticmethod
    def _forget_text(app) -> str:
        from openexpress import ciq

        if getattr(app, "sandboxed", False):
            return (ciq.sandbox_removal_text() + "\n\n"
                    + _("Here you can only remove it from your account list."))
        return _("{name} cannot be removed over USB: this device shows no app files. Remove "
                 "it on the device itself or in Garmin Connect on the phone.\n\nHere you can "
                 "only remove it from your account list.").format(name=app.name)

    def confirm_forget_app(self, app) -> None:
        """For apps that cannot be removed over USB: only the account list can be changed."""
        from openexpress import ciq

        s = self.session

        def work():
            service.require_account_device(s.client, s.device.unit_id, fresh=True)
            ciq.StoreApi(s.client).forget(s.device, [app.app_id])

        def forget():
            self.run_task(
                _("Removing {name}…").format(name=app.name), work,
                lambda _r: (self.toast(_("Removed {name} from your account list.").format(
                    name=app.name)), self.load_section("apps", force=True)),
                read_again_after_failure(self, "apps"), device=False)

        confirm(self, "app-forget", _("Remove {name}?").format(name=app.name),
                self._forget_text(app), Answer("forget", _("Remove from Account"), forget))

    def confirm_remove_app(self, app) -> None:
        from openexpress import ciq

        s = self.session
        direct = bool(getattr(app, "direct_removal", False))
        if app.has_files is False and not direct:
            self.confirm_forget_app(app)
            return
        body = (_("The app and its settings will be deleted from the device over a direct "
                  "USB connection; the device disappears from the file manager for a moment. "
                  "The device finishes the removal after it is unplugged.") if direct
                else _("The app and its settings will be deleted from the device."))

        def remove(forget: bool):
            def work():
                if forget:
                    service.require_account_device(s.client, s.device.unit_id, fresh=True)
                removed = ciq.remove(s.device, s.storage, app)
                if forget:
                    ciq.StoreApi(s.client).forget(s.device, [app.app_id])
                return removed

            def done(_removed):
                text = _("Removed {name}").format(name=app.name)
                if direct:
                    text += ". " + _("The device finishes the removal after it is unplugged.")
                self.toast(text)
                self.load_section("apps", force=True)

            self.run_task(_("Removing {name}…").format(name=app.name),
                          writes_to_device(work), done, read_again_after_failure(self, "apps"))

        confirm(self, "app-remove", _("Remove {name}?").format(name=app.name), body,
                Answer("forget", _("Remove and forget"), lambda: remove(True)),
                Answer("ok", _("Remove"), lambda: remove(False), look="destructive"))

    def toggle_app(self, app, row, handler_id: int) -> None:
        from openexpress import ciq

        enabled = row.get_active()
        if (not app.disabled) == enabled:
            return

        def revert():
            row.handler_block(handler_id)
            row.set_active(not app.disabled)
            row.handler_unblock(handler_id)

        s = self.session
        if self.busy or s is None:
            revert()
            if self.busy:
                self.toast(_("Another operation is in progress"))
            return
        msg = _("{name} enabled") if enabled else _("{name} disabled")

        def done(_r):
            app.disabled = not enabled
            self.toast(msg.format(name=app.name))

        def failed(_e):
            revert()
            return False

        # Gone well, only this row changes; failed, the page is read again: the list that
        # waited for the device (other switches) may be gone with it.
        self.run_task(_("Saving…"),
                      writes_to_device(lambda: ciq.set_enabled(s.device, s.storage, app.app_id,
                                                               enabled, app.app_type)),
                      done, read_again_after_failure(self, "apps", failed))
