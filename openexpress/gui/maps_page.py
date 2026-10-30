"""The Maps page of the window: Garmin's maps for the device (installed, available, updates;
maps.py decides what is written where) and maps of your own (ownmaps.py). MapsPage is a
mixin of MainWindow (gui/app.py), without __init__ of its own; what it uses of the window
(session, run_task, toast, sections, maps_cache, …) is the window's."""
from __future__ import annotations

import logging
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402 - imports must follow gi.require_version

from openexpress import service  # noqa: E402
from openexpress.gui.tasks import read_again_after_failure, writes_to_device  # noqa: E402
from openexpress.gui.window_tasks import Answer, confirm  # noqa: E402
from openexpress.gui.words import space_text  # noqa: E402
from openexpress.i18n import _, caption, human_size  # noqa: E402

log = logging.getLogger(__name__)


def _check_again(win) -> None:
    """"Check Again" on the Maps page: Garmin's servers are asked anew, unit-info too."""
    if win.session is not None:
        service.forget_unit_info(win.session)
    win.load_section("maps", force=True)


def map_update_type_text(word: str) -> str:
    """Under "Map updates for this device aren't supported yet": the map update type Garmin
    gives the device (unit-info), so that a report can name it."""
    # TRANSLATORS: {type} is a word of Garmin's server, e.g. "Gemini"
    return _("Garmin's map update type for this device: {type}").format(type=word)


def _no_maps(win, sec, text: str, detail: str | None = None) -> None:
    """The Maps page without Garmin's maps: why (and `detail` under it), and a button that
    checks again."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                  halign=Gtk.Align.CENTER, margin_top=24, margin_bottom=24)
    box.append(Gtk.Image(icon_name="mark-location-symbolic", pixel_size=64))
    title = Gtk.Label(label=text, wrap=True, justify=Gtk.Justification.CENTER)
    title.add_css_class("title-2")
    box.append(title)
    if detail:
        line = Gtk.Label(label=detail, wrap=True, justify=Gtk.Justification.CENTER,
                         css_classes=["dim-label"])
        box.append(line)
    check = Gtk.Button(label=_("Check Again"), halign=Gtk.Align.CENTER, margin_top=8)
    check.add_css_class("pill")
    check.connect("clicked", lambda *_a: _check_again(win))
    box.append(check)
    sec.group().add(box)


class MapsPage:
    """The Maps page and its tasks, for Garmin's maps and maps of your own."""

    def load_maps(self) -> None:
        from openexpress import maps as mp
        from openexpress import ownmaps

        s = self.session

        def work():
            # Garmin's server, then the map folder of the device for the maps that are not
            # Garmin's (the folder and the first bytes of each such file).
            try:
                result = mp.check(s.client, s.device, ask=lambda: service.unit_info(s),
                                  country=lambda: service.account_country(s))
            except mp.MapsNotSupported as e:
                # No check of Garmin's maps, which the page says (with the map update type
                # Garmin gives); the maps of your own do not depend on it (Garmin's list:
                # none, so their names alone are kept apart).
                return (None, ownmaps.overview(s.device, s.storage, []), (None, None),
                        e.map_update_type, [])
            mp.mark_waiting(s.storage, result[0])  # written or removed, not taken in yet
            # The space too: asked of the device here, never as the page is drawn; and the
            # old files Garmin's cleanup rules would take (only shown in the questions).
            return (result, ownmaps.overview(s.device, s.storage, result[0]),
                    (s.storage.free_space(), s.storage.capacity()), None,
                    mp.cleanup_plan(s.storage, s.device, result[0]))

        def failed(_e):
            # The "Maps" row waited for this answer (show_device): it says the check failed.
            if self._map_check_session is s:
                self._map_check_session = None
            if self.map_state is None:
                self.map_state = "failed"
                self._render_map_state()

        def done(r):
            self.map_cleanup = r[4]  # for the questions before a map is installed or removed
            self.show_maps(r[0], r[1], space=r[2], map_update_type=r[3])

        self._load("maps", _("Checking maps…"), work, done, on_error=failed)

    def show_maps(self, result, own=None, space=(None, None),
                  map_update_type: str | None = None) -> None:
        """`space`: the free space and the size of the device, read with the maps (none told:
        the page has no row of them); nothing is asked of the device here. Map updates not
        supported (`result` None): the map update type Garmin gives, if any, is named."""
        from openexpress import maps as mp

        if self.session is None:
            return
        sec = self.sections["maps"]
        if result is None:  # map updates for this device are not supported (maps.check)
            self.maps_cache = []
            self._set_map_state(None)  # the "Maps" row of the device page
            _no_maps(self, sec, mp.not_supported_text(),
                     map_update_type_text(map_update_type) if map_update_type else None)
            if own is not None:  # the maps of your own, whatever serves Garmin's
                self.show_own_maps(own)
            return
        all_maps, listed = result  # the maps read, and how many the server listed
        self.maps_cache = all_maps
        self._set_map_state(result)  # the "Maps" row of the device page
        if not listed:
            _no_maps(self, sec, mp.no_maps_message())
            if own is not None:
                self.show_own_maps(own)
            return
        free, total = space
        # Also the maps written or removed that the device takes in when it is unplugged.
        # Also a map partly on the device (to be installed again).
        installed = sorted((m for m in all_maps if m.listed or m.waiting),
                           key=lambda m: (m.map_type, m.name))
        g = sec.group(_("On the device"))
        # The list is what Garmin's server said when the page was opened; a device that has
        # just been plugged in may not have named all its map files yet.
        g.set_header_suffix(self._button(
            _("Check Again"), lambda: _check_again(self)))
        if free is not None and total:
            used_by_maps = sum(m.size for m in installed if m.occupies)
            row = Adw.ActionRow(
                title=space_text(free, total),
                subtitle=_("Maps use {size}").format(size=human_size(used_by_maps)))
            row.add_suffix(Gtk.LevelBar(min_value=0, max_value=1, value=1 - free / total,
                                        valign=Gtk.Align.CENTER, width_request=120))
            g.add(row)
        for m in installed:
            row = Adw.ActionRow(title=GLib.markup_escape_text(m.name),
                                subtitle=GLib.markup_escape_text(self._map_subtitle(m)))
            if m.waiting:
                # Nothing to do until the device is unplugged, as for apps waiting there.
                row.set_sensitive(m.waiting != mp.WAIT_REMOVE)
                g.add(row)
                continue
            if (m.would_update or m.incomplete) and not mp.can_install(m):
                # No button: say why instead (the same reason the install would give).
                row.set_subtitle(GLib.markup_escape_text(
                    self._map_subtitle(m) + "\n" + mp.cannot_install_reason(m)))
            elif m.update_available:
                # TRANSLATORS: button
                row.add_suffix(self._button(_("Update"),
                                            lambda button, m=m: self.confirm_map(m, button),
                                            "suggested-action", with_button=True))
            elif m.incomplete:
                # The whole map again: the question and the install are those of a new map.
                row.add_suffix(self._button(_("Install"),
                                            lambda button, m=m: self.confirm_map(m, button),
                                            with_button=True))
            if m.can_uninstall:
                row.add_suffix(self._trash(_("Remove {name}").format(name=m.name),
                                           lambda m=m: self.confirm_remove(m)))
            g.add(row)

        if own is not None:
            self.show_own_maps(own)  # right under the installed maps, before those to download
        available = sorted((m for m in all_maps if not m.listed and not m.waiting),
                           key=lambda m: (m.region, m.name))
        if not available:
            return
        # TRANSLATORS: group title: maps that can be installed
        g = sec.group(_("Available"), _("Free maps available for this device"))
        search = Gtk.SearchEntry(placeholder_text=_("Search maps or regions"), margin_bottom=12)
        g.add(search)
        rows = []
        listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        listbox.add_css_class("boxed-list")
        for m in available:
            line = caption(m.region, m.version, human_size(m.size))
            if not mp.can_install(m):
                line += "\n" + mp.cannot_install_reason(m)  # instead of the button
            elif (note := mp.restriction_note(m)) is not None:
                line += "\n" + note  # the account's country not known: only marked
            row = Adw.ActionRow(title=GLib.markup_escape_text(m.name),
                                subtitle=GLib.markup_escape_text(line))
            if mp.can_install(m):
                row.add_suffix(self._button(_("Install"),
                                            lambda button, m=m: self.confirm_map(m, button),
                                            with_button=True))
            listbox.append(row)
            rows.append((row, f"{m.name} {m.region} {m.map_type}".casefold()))
        g.add(listbox)
        self._search_filter(search, rows)

    # --- maps that are not from Garmin: everything is decided in ownmaps.py -------------

    def show_own_maps(self, overview) -> None:
        if self.session is None or overview.folder is None:
            return  # no device, or one without a folder for maps
        g = self.sections["maps"].group(
            _("Own maps"), _("Maps not from Garmin, e.g. from OpenStreetMap"))
        g.set_header_suffix(self._button(_("Add Map…"), self.choose_map))
        if overview.unreadable:
            g.add(Adw.ActionRow(title=_("The map folder could not be read")))
        elif not overview.maps:
            g.add(Adw.ActionRow(title=_("No maps of your own on the device")))
        for m in overview.maps:
            ours, foreign = m.filename in overview.removable, m.filename in overview.foreign
            row = Adw.ActionRow(title=GLib.markup_escape_text(m.title or m.filename),
                                subtitle=GLib.markup_escape_text(
                                    self._own_map_subtitle(m, ours, foreign)))
            if ours or foreign:
                row.add_suffix(self._trash(
                    _("Remove {name}").format(name=m.filename),
                    lambda m=m, foreign=foreign: self.confirm_remove_own(m, foreign)))
            g.add(row)

    @staticmethod
    def _own_map_subtitle(m, ours: bool, foreign: bool) -> str:
        parts = [m.filename, human_size(m.size)]
        if foreign:
            parts.append(_("not copied by this installation"))
        elif not ours:  # not a map by its header: it has no remove button
            parts.append(_("not recognised as a map; remove it in the file manager"))
        return caption(*parts)

    def _own_map_refused(self, e, what: str) -> bool:
        """Show a refusal of ownmaps.py under the title `what` (what could not be done), with
        the hint for the window; True when it was one."""
        from openexpress import maps as mp
        from openexpress import ownmaps

        if isinstance(e, ownmaps.OwnMapError):
            self.error(what, e.text(window=True))
        elif isinstance(e, mp.MapError):
            self.error(what, str(e))
        else:
            return False
        return True

    def choose_map(self) -> None:
        dialog = Gtk.FileDialog(title=_("Choose a map file (.img)"))
        only_maps = Gtk.FileFilter(name=_("Garmin map files (*.img)"))
        only_maps.add_pattern("*.img")
        only_maps.add_pattern("*.IMG")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(only_maps)
        dialog.set_filters(filters)

        def chosen(dlg, res):
            try:
                path = dlg.open_finish(res).get_path()
            except GLib.Error:
                return  # cancelled
            if path is None:
                self.toast(_("Choose files stored on this computer"))
                return
            self.add_own_map(Path(path))

        dialog.open(self, None, chosen)

    @staticmethod
    def _add_map_text(plan) -> str:
        """The body of the confirmation: the name of the map on a line of its own, then the
        file and the space. Hyphens in the name do not break ("13-Oct-2026" stays together);
        only what is shown changes, not the name."""
        lines = [(plan.title or plan.name).replace("-", "\u2011"), "",
                 caption(plan.name, human_size(plan.size))]
        if plan.free is not None:
            lines.append(_("Space on the device: {space} (the map takes {size})").format(
                space=space_text(plan.free), size=human_size(plan.size)))
        return "\n".join(lines)

    def add_own_map(self, path) -> None:
        from openexpress import ownmaps

        s = self.session
        garmin_maps = self.maps_cache or []

        def refused(e):
            return self._own_map_refused(e, _("Cannot add the map"))

        def written(real, plan):
            self.toast(_("Wrote {path} ({size}).").format(path=real, size=human_size(plan.size)))
            self.load_section("maps", force=True)

        def copy(plan):
            self.run_task(
                _("Copying the map…"),
                writes_to_device(lambda: ownmaps.add(plan, s.device, s.storage,
                                                     progress=self.progress)),
                lambda real: written(real, plan), read_again_after_failure(self, "maps", refused))

        def ask_copy(plan):
            # Wide: room for the name of a map on one line.
            confirm(self, "own-map-add", _("Copy this map to the device?"),
                    self._add_map_text(plan),
                    Answer("ok", _("Copy"), lambda: copy(plan), look="suggested"), wide=True)

        self.run_task(_("Checking maps…"),
                      lambda: ownmaps.plan_add(s.device, s.storage, garmin_maps, path),
                      ask_copy, refused)

    def confirm_remove_own(self, m, foreign: bool = False) -> None:
        """`foreign`: a map this installation did not copy; asked about more strongly, and
        checked in ownmaps.py against what this page showed (`m`)."""
        from openexpress import ownmaps

        s = self.session
        garmin_maps = self.maps_cache or []

        def work():
            if foreign:
                plan = ownmaps.plan_remove_foreign(s.device, s.storage, garmin_maps, m)
            else:
                plan = ownmaps.plan_remove(s.device, s.storage, garmin_maps, m.filename)
            ownmaps.remove(plan, s.device, s.storage)
            return plan

        def refused(e):
            return self._own_map_refused(e, _("Cannot remove the map"))

        def removed(plan):
            if plan.on_device:
                self.toast(_("Removed {path} ({size}).").format(
                    path=plan.path, size=human_size(plan.size)))
            else:
                self.toast(_("Took {name} off the list of own maps; the file was not on the "
                             "device.").format(name=plan.name))
            self.load_section("maps", force=True)

        body = _("{path} ({size}) will be removed from the device.").format(
            path=m.path, size=human_size(m.size))
        if foreign:
            # A statement: the heading already asks.
            body = _("openExpress did not copy this map to the device.") + "\n\n" + body
        def remove():
            self.run_task(
                _("Removing {name}…").format(name=m.title or m.filename),
                writes_to_device(work), removed,
                # Refused or failed, the page is read again: it may be out of date.
                read_again_after_failure(self, "maps", refused))

        confirm(self, "own-map-remove", _("Remove {name}?").format(name=m.title or m.filename),
                body, Answer("ok", _("Remove Anyway") if foreign else _("Remove"), remove,
                             look="destructive"), wide=True)

    @staticmethod
    def _map_subtitle(m) -> str:
        """Type, version, size and status of an installed map; without the type when the map
        is named like its type ("Base maps")."""
        from openexpress import maps as mp

        kind = m.map_type.strip()
        parts = [kind if kind.casefold() != m.name.strip().casefold() else "",
                 m.version, human_size(m.size), m.status]
        note = mp.restriction_note(m)  # the account's country not known: only marked
        return caption(*parts) + ("\n" + note if note else "")

    def confirm_map(self, m, button: Gtk.Button | None = None) -> None:
        """Install or Update of a map (`button`, which spins while the space is checked):
        refused at once when it cannot be installed, else the space is read in a task and the
        question comes when it is done."""
        from openexpress import maps as mp

        if not mp.can_install(m):
            # Refused before anything is asked or promised, as on the command line.
            self.error(_("Cannot install the map"), mp.cannot_install_message(m))
            return
        s = self.session
        if not (s.client.consumer and s.client.consumer.url_salt):
            self.error(_("Setup missing"),
                       _("Downloading maps requires the Utilities.dll file from Garmin Express.\n"
                         "Run: openexpress setup --utilities-dll /path/to/Utilities.dll"))
            return
        removing = mp.removal_list(self.maps_cache, m, rewriting=True)

        def work():
            # Over MTP each size is a round trip through gvfs: never on the window's thread.
            return mp.space_needed(s.storage, m, removing), s.storage.free_space()

        def done(result):
            if self.session is not s or m not in (self.maps_cache or []):
                # Unplugged, or the list read again without it, meanwhile: nothing to ask.
                log.info("maps: the device or its list changed while the space was checked, "
                         "nothing asked")
                return
            need, free = result
            self._ask_install_map(m, need, free, removing)

        def failed(_e):
            return self.session is not s  # gone meanwhile: the window says so already

        self.run_task(_("Checking the space…"), work, done, failed, button=button)

    def _ask_install_map(self, m, need: int, free: int | None, removing: list[str]) -> None:
        """The question before a map is installed, with the space it needs and the space
        there is (read in the background by confirm_map)."""
        from openexpress import maps as mp

        s = self.session
        body = (_("Download: {size}").format(size=human_size(m.size)) + "\n"
                + _("Space on the device: {needed} ({space})").format(
                    needed=human_size(need), space=space_text(free)) + "\n"
                + _("Files: {count}").format(count=len(m.files))
                + ("\n" + _("Old files to remove: {count}").format(count=len(removing))
                   if removing else "")
                + "".join("\n\n" + _("License: {url}").format(url=e) for e in m.eula_urls)
                + "\n\n" + _("Copying large maps over USB can take tens of minutes.")
                + _cleanup_paragraph(self))
        heading = (_("Update {name}?") if m.installed else _("Install {name}?")).format(
            name=m.name)
        def unlock_not_written(e) -> bool:
            # The words of the window ("come back to this page"), not the command line's.
            if not isinstance(e, mp.UnlockNotWritten):
                return False
            self.error(_("Installing {name}…").format(name=m.name), e.window_text)
            return True

        def install():
            self.run_task(
                _("Installing {name}…").format(name=m.name),
                writes_to_device(lambda: mp.install(
                    m, self.maps_cache, s.storage,
                    activate=lambda ident: s.client.activate_maps(s.device.raw, [ident]),
                    session=s.client.http, progress=self.progress,
                    url_salt=s.client.consumer.url_salt)),
                lambda rep: (self.toast(_("{name} copied; unplug the device to load it")
                                        .format(name=m.name)),
                             self.load_section("maps", force=True)),
                read_again_after_failure(self, "maps", unlock_not_written),
            )

        confirm(self, "map-install", heading, body,
                Answer("ok", _("Accept and install"), install, look="suggested"))

    def confirm_remove(self, m) -> None:
        from openexpress import maps as mp

        s = self.session
        files = mp.removal_list(self.maps_cache, m, rewriting=False)
        def remove():
            self.run_task(
                _("Removing {name}…").format(name=m.name),
                writes_to_device(lambda: mp.uninstall(m, self.maps_cache, s.storage,
                                                      progress=self.progress)),
                lambda removed: (self.toast(self._removed_files(len(removed))),
                                 self.load_section("maps", force=True)),
                read_again_after_failure(self, "maps"))

        confirm(self, "map-remove", _("Remove {name}?").format(name=m.name),
                _("These files will be removed from the device:") + "\n" + "\n".join(files)
                + _cleanup_paragraph(self),
                Answer("ok", _("Remove"), remove, look="destructive"))


def _cleanup_paragraph(win) -> str:
    """The old files Garmin's cleanup rules would take, at the end of a question before a map
    is installed, updated or removed (shown, never removed); "" without."""
    from openexpress import maps as mp

    lines = mp.cleanup_lines(win.map_cleanup or [])
    return "\n\n" + "\n".join(lines) if lines else ""
