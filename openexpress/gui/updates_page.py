"""The Updates page of the window: the software and the files Garmin offers the open device,
their install, the question before it and the eject of a drive after it (updates.py decides
what is written where). UpdatesPage is a mixin of MainWindow (gui/app.py), without __init__ of
its own.

Has: the updates of the last check: updates_cache, updates_beta, _update_check_session.
"""
from __future__ import annotations

import logging

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, GLib, Gtk  # noqa: E402 - imports must follow gi.require_version

from openexpress import answers, service  # noqa: E402
from openexpress.connect import ApiError  # noqa: E402
from openexpress.device.gio_storage import (  # noqa: E402
    DriveNotMounted,
    eject,
    ejected_text,
    not_ejected_text,
)
from openexpress.gui.pictures import bike_accessory_text, is_bike_accessory  # noqa: E402
from openexpress.gui.tasks import writes_to_device  # noqa: E402
from openexpress.gui.window_tasks import (  # noqa: E402
    WRAP_BOX,
    Answer,
    confirm,
    inform,
    still_in_window,
    wrapping_row,
)
from openexpress.gui.words import software_text, update_state_text  # noqa: E402
from openexpress.i18n import SEPARATOR, _, caption, human_size, ngettext  # noqa: E402
from openexpress.updates import FINISH_STEPS_WINDOW  # noqa: E402

log = logging.getLogger(__name__)
# Between two versions ("23.39 → 23.40"): no-break spaces keep them on one line.
ARROW = "\u00a0→\u00a0"


def data_loss_text(session) -> str:
    """The warning before an update of a device that loses its data on one: a drive is not
    synced by openExpress; otherwise how many activities wait to be uploaded, when the window
    has counted them."""
    from openexpress import updates as upd

    if getattr(session, "mass_storage", False):
        return _(upd.DATA_LOSS_WARNING_DRIVE)
    text = _(upd.DATA_LOSS_WARNING)
    count = getattr(getattr(session, "to_sync", None), "count", None)
    if count:
        text += " " + ngettext("{count} activity on the device has not been uploaded yet.",
                               "{count} activities on the device have not been uploaded yet.",
                               count).format(count=count)
    return text


def update_check_failed(win) -> None:
    """A check of updates for the open device failed: "Could not check for updates", or,
    when "Device software" shows what the last check found (show_update_record), that with
    when it was."""
    if win.update_state is None:
        win.update_state = "failed"
    elif win._update_record_at is not None:
        win.update_checked_at = win._update_record_at
    else:
        return
    win._render_update_state()


def install_updates(s, chosen, progress):
    """Copy the updates chosen to the device of session `s` (updates.install: what Garmin's
    server offers it is asked anew next time)."""
    from openexpress import updates as upd

    return upd.install(chosen, s.storage, session=s.client.http, progress=progress,
                       unit_id=s.device.unit_id)


def check_updates_again(win) -> None:
    """"Check Again" on the Updates page: Garmin's server is asked anew."""
    answers.forget_session(answers.SOFTWARE_UPDATES, win.session)
    win.load_section("updates", force=True)


class UpdatesPage:
    def load_updates(self) -> None:
        from openexpress import updates as upd

        s = self.session

        def work():
            # Asks Garmin's server, then compares the offered files with those on the device.
            answer = s.client.software_updates(s.device.raw, include_reinstalls=True)
            items = upd.mark_present(s.storage, upd.parse_updates(answer),
                                     upd.component_count(s.device))
            # The kind of device, for the question before an update (it asks no server):
            # usually known already from opening the device.
            try:
                service.unit_info(s)
            except ApiError as e:  # only for a warning; the text may quote the answer
                log.info("software update: unit-info failed (%s), no data-loss check",
                         type(e).__name__)
            return items, upd.beta_offered(answer)

        def failed(_e):
            # The "Device software" row waited for this answer (show_device): it says so.
            if self._update_check_session is s:
                self._update_check_session = None
            update_check_failed(self)

        self._load("updates", _("Checking for updates…"), work,
                   lambda result: self.show_updates(*result), on_error=failed)

    def show_updates(self, items, beta: bool = False) -> None:
        if self.session is None:
            return
        self.updates_cache, self.updates_beta = items, beta
        sec = self.sections["updates"]
        from openexpress import updates as upd

        new = upd.pending(items)
        staged = upd.staged(items)
        self._set_update_state(new, staged)
        # One copied already is told at the top of the page (it wins, as on the row "Device
        # software"), else a system update; the others (a second system one, rare) are listed.
        system = None if staged else next((u for u in new if u.is_system), None)
        self._updates_status(sec, system, len(new), beta,
                             ready=staged[0] if staged else None,
                             only=new[0] if len(new) == 1 else None)
        rest = [u for u in new if u is not system]
        if rest:
            g = sec.group(_("Available updates"))
            for u in rest:
                self._update_row(g, u)
        # A file the device already has is a reinstall, whatever the server calls it; but not
        # a system update that is waiting for the device to install it.
        re = [u for u in items if (u.is_reinstall or u.on_device) and not u.is_metadata
              and not any(u is w for w in staged)]
        if re:
            g = sec.group(_("Advanced"))
            exp = Adw.ExpanderRow(
                title=_("Reinstall components"),
                subtitle=ngettext("{count} component already on the device; useful for "
                                  "troubleshooting",
                                  "{count} components already on the device; useful for "
                                  "troubleshooting", len(re)).format(count=len(re)))
            g.add(exp)
            for u in sorted(re, key=lambda x: x.title):
                self._update_row(exp, u, reinstall=True)

    def _updates_status(self, sec, system, count: int, beta: bool = False, ready=None,
                        only=None) -> None:
        """Builds the top of the Updates page in `sec`: the state of the updates as "Device
        software" says it (update_state_text): `count` to install (`only`: the one of them),
        one copied (`ready`) with `count` still to install, or up to date; under it the
        `system` update or the device's version; whether Garmin offers it beta versions
        (`beta`)."""
        from openexpress import updates as upd

        d = self.session.device
        mass_storage = getattr(self.session, "mass_storage", False)
        installed = software_text(d.software_version_str)
        detail = None
        buttons = []
        if system is not None:
            icon = "software-update-available-symbolic"
            title = update_state_text("available", count, only)
            versions = f"{d.software_version_str}{ARROW}{system.version}"
            size = human_size(system.size)
            subtitle = caption(versions, size)
            if system.is_beta:
                # The versions, "Beta" and the rest apart, so that "Beta" can have the warning
                # colour of the theme (light and dark); Pango markup knows no colours of the
                # theme.
                # TRANSLATORS: marks a beta version of the device software
                subtitle = (versions, _("Beta"), size)
            detail = caption(system.title, system.severity_text, system.filename)
            # TRANSLATORS: button
            buttons.append(self._button(_("Install"), lambda u=system: self.confirm_update(u),
                                        "suggested-action"))
        elif ready is not None:
            # What to do is in the title (as on the row "Device software"), with how many are
            # still to install (listed below); under it the versions.
            icon = "software-update-available-symbolic"
            title = update_state_text("ready", count, mass_storage=mass_storage)
            versions = f"{d.software_version_str}{ARROW}{ready.version}"
            if mass_storage:
                # A drive: the device takes the update once it is ejected.
                buttons.append(self._button(
                    _("Eject"), lambda button, s=self.session: self.eject_device(s, button=button),
                    "suggested-action", with_button=True))
            subtitle = (versions, _("Beta"), "") if ready.is_beta else versions
            detail = caption(ready.title, ready.severity_text, ready.filename)
        elif count:
            icon, subtitle = "software-update-available-symbolic", installed
            title = update_state_text("available", count, only)
        else:
            icon, subtitle = "object-select-symbolic", installed
            title = update_state_text("current")
        buttons.append(self._button(_("Check Again"), lambda: check_updates_again(self)))
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                      halign=Gtk.Align.CENTER, margin_top=24, margin_bottom=24)
        image = Gtk.Image(icon_name=icon, pixel_size=96)
        if system is None and ready is None and not count:
            image.add_css_class("success")  # up to date only
        box.append(image)

        def label(text, styles, **props):
            widget = Gtk.Label(label=text, **props)
            for style in styles:
                widget.add_css_class(style)
            return widget

        for text, styles in ((title, ["title-1"]), (subtitle, ["dim-label"]),
                             (detail, ["caption", "dim-label"]),
                             (_(upd.BETA_OFFERED) if beta else None, ["caption", "dim-label"])):
            if isinstance(text, tuple):  # a beta version: "Beta" stands out
                versions, word, after = text
                # In a narrow window the parts go on to the next line: a separator stays
                # with the part before it, never at the start of a line.
                if WRAP_BOX is None:
                    # A FlowBox puts parts in columns: one text that wraps, "Beta" in bold
                    # (Pango markup knows no colours of the theme); a hard space before
                    # each separator.
                    line = label("", styles, wrap=True, justify=Gtk.Justification.CENTER,
                                 max_width_chars=50)
                    tail = f"{SEPARATOR}{GLib.markup_escape_text(after)}" if after else ""
                    line.set_markup(f"{GLib.markup_escape_text(versions)}{SEPARATOR}"
                                    f"<b>{GLib.markup_escape_text(word)}</b>{tail}")
                    box.append(line)
                    continue
                marked = Gtk.Box()
                marked.append(label(word, ["warning", "heading"]))
                parts = [label(versions + SEPARATOR, styles), marked]
                if after:  # none after "Beta" for an update copied already
                    marked.append(label(SEPARATOR, styles))
                    parts.append(label(after, styles, wrap=True,
                                       justify=Gtk.Justification.CENTER))
                box.append(wrapping_row(parts, 0))
            elif text:
                box.append(label(text, styles, wrap=True, justify=Gtk.Justification.CENTER,
                                 max_width_chars=50))
        for b in buttons:
            b.add_css_class("pill")
        box.append(wrapping_row(buttons, 12, margin_top=8))  # one under the other if need be
        sec.group().add(box)

    def _update_row(self, parent, u, reinstall: bool = False) -> None:
        details = caption(u.version, human_size(u.size), u.severity_text, u.filename)
        # A system update says so in its title; the name the server gives it goes first.
        row = Adw.ActionRow(
            title=GLib.markup_escape_text(_("System software") if u.is_system else u.title),
            subtitle=GLib.markup_escape_text(caption(u.title, details) if u.is_system
                                             else details),
        )
        # TRANSLATORS: button
        row.add_suffix(self._button(_("Reinstall") if reinstall else _("Install"),
                                    lambda u=u: self.confirm_update(u),
                                    None if reinstall else "suggested-action"))
        parent.add_row(row) if isinstance(parent, Adw.ExpanderRow) else parent.add(row)

    @staticmethod
    def _update_done_text(rep) -> str:
        """What the window says after an update was copied, with what to do next."""
        return _("Files copied to the device.") + (_(FINISH_STEPS_WINDOW) if rep.installed
                                                    else "")

    def confirm_update(self, u) -> None:
        from openexpress import updates as upd

        s = self.session
        # A drive: the copy is on the disk once written (LocalStorage syncs it), but the device
        # may take it only once the drive is ejected, so the cable may go out only then.
        mass_storage = getattr(s, "mass_storage", False)
        chosen = upd.select(self.updates_cache, [u.part_number])
        version = (f"{s.device.software_version_str}{ARROW}{u.version}" if u.is_system
                   else u.version)
        body = (f"{u.title} {version}\n"
                + _("Size: {size}").format(size=human_size(u.size)) + "\n"
                + _("On the device: {path}").format(path=u.device_path)
                + ("\n\n" + _("Changes:") + "\n" + "\n".join("• " + c for c in u.changes[:10])
                   if u.changes else "")
                + ("\n\n" + _("This is a beta version.") if u.is_beta else ""))
        # Below the text: the license, then the warning, right above the buttons.
        below = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        if u.eula_url.casefold().startswith("https://"):
            # GTK opens it (in the Flatpak through the portal); only a web address is a link.
            below.append(Gtk.LinkButton(uri=u.eula_url, label=_("License agreement (EULA)"),
                                        halign=Gtk.Align.CENTER))
        elif u.eula_url:
            below.append(Gtk.Label(label=_("License: {url}").format(url=u.eula_url), wrap=True,
                                   justify=Gtk.Justification.CENTER))
        warnings = [_(upd.SYSTEM_UPDATE_WARNING)] if u.is_system else []
        # Read from the answer kept with the session: the question asks no server.
        if upd.loses_data_on_update(getattr(s, "unit_info_answer", None)):
            warnings.append(data_loss_text(s))
        for text in warnings:
            # In the warning colour of the theme and in bold.
            warning = Gtk.Label(label=text, wrap=True, justify=Gtk.Justification.CENTER)
            warning.add_css_class("warning")
            warning.add_css_class("heading")
            below.append(warning)

        def install():
            rep = install_updates(s, chosen, self.progress)
            return rep, mass_storage and is_bike_accessory(
                s.client, s.device, ask=lambda: service.unit_info(s))

        def installed(result):
            rep, bike_accessory = result
            self._show_installed(s, chosen)
            if not mass_storage:
                self.error(_("Done"), self._update_done_text(rep))
                return
            self.ask_to_eject(s, bike_accessory)

        def failed(e) -> bool:
            # Under a heading of its own, not the name of the task ("Installing update…").
            self.error(_("Update not installed"), str(e) or type(e).__name__)
            return True

        confirm(self, "update-install", _("Install update?"), body,
                Answer("ok", _("Accept and install"),
                       lambda: self.run_task(_("Installing update…"), writes_to_device(install),
                                             installed, failed), look="suggested"),
                extra=below if below.get_first_child() is not None else None)

    def ask_to_eject(self, s, bike_accessory: bool = False) -> None:
        """After an update was saved on a Garmin drive: it is to be ejected first; that the
        cable can go out is said only once it is (eject_device)."""
        confirm(self, "eject-ask", _("Update saved"),
                _("Eject the device before you unplug the cable: until then "
                  "it may not see the update."),
                # TRANSLATORS: button that ejects the device (its USB drive)
                Answer("eject", _("Eject"), lambda: self.eject_device(s, bike_accessory),
                       look="suggested"),
                # TRANSLATORS: button; the device is ejected later
                cancel=_("Later"), default="eject")

    def eject_device(self, s, bike_accessory: bool = False,
                     button: Gtk.Button | None = None, files: bool = False) -> None:
        """Eject the drive of a device in mass storage mode; then the cable can go out. When
        the system refuses, why, that the cable must stay in, and Try Again. `button`: the
        Eject of the Updates page, which spins meanwhile; `files`: after a sync wrote the
        files of Garmin Connect's queue to it, not an update."""
        def ejected(_result):
            # For a bike accessory, as Garmin Express does: how it finishes the update.
            inform(self, "ejected", _("Done"), ejected_text(),
                   then=(lambda: self.error(_("Finish the update"), bike_accessory_text()))
                   if bike_accessory else None)

        def failed(e) -> bool:
            if isinstance(e, DriveNotMounted):
                self.error(_("The device is not mounted"), str(e))
                return True
            confirm(self, "eject-retry", _("The device was not ejected"),
                    f"{e}\n\n{not_ejected_text(files=files)}",
                    Answer("retry", _("Try Again"),
                           lambda: self.eject_device(s, bike_accessory, files=files),
                           look="suggested"),
                    cancel=_("Close"))
            return True

        self.run_task(_("Ejecting the device…"), lambda: eject(s.storage), ejected, failed,
                      button=button)

    def _show_installed(self, session, chosen) -> None:
        """After an update was copied: the files are on the device now. The page shows that
        without asking the server or reading the device again (a system update is then ready
        to install, the others are no updates any more)."""
        cache = self.updates_cache
        if (self.session is not session or cache is None
                or not all(any(c is u for u in cache) for c in chosen)):
            return  # another device, or the page was checked again meanwhile
        for u in chosen:
            u.on_device = True
        sec = self.sections["updates"]
        sec.clear()
        self._action_buttons = still_in_window(self._action_buttons)
        self.show_updates(self.updates_cache, self.updates_beta)
