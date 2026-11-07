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
from openexpress.gui.words import (  # noqa: E402
    copied_update_text,
    software_text,
    update_state_text,
)
from openexpress.i18n import SEPARATOR, _, caption, human_size, ngettext  # noqa: E402
from openexpress.updates import FINISH_STEPS_WINDOW  # noqa: E402

log = logging.getLogger(__name__)
# Between two versions ("23.39 → 23.40"): no-break spaces keep them on one line.
ARROW = "\u00a0→\u00a0"


def versions_text(u, device) -> str:
    """The version of update `u`, after the one the device has when that is known and other:
    the version GarminDevice.xml lists for its part number, or for the device's own software
    the device's ("23.39 → 23.40"). A part of the device with no version listed: only its
    own ("6.70"), never the device's software against it."""
    from openexpress import updates as upd

    old = u.installed_version or upd.the_device_version(u, device)
    return f"{old}{ARROW}{u.version}" if old and old != u.version else u.version


def data_loss_text(session) -> str:
    """The warning before an update of a device that loses its data on one: a drive that
    openExpress cannot sync (its drive_refusal) is to be synced in the app; any other device
    first here, with how many activities wait to be uploaded, when the window has counted
    them."""
    from openexpress import updates as upd

    if getattr(session, "drive_refusal", None) is not None:
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
    when "Device Software" shows what the last check found (show_update_record), that with
    when it was."""
    if win.update_state is None:
        win.update_state = "failed"
    elif win._update_record_at is not None:
        win.update_checked_at = win._update_record_at
    else:
        return
    win._render_update_state()


def install_updates(s, chosen, progress, offered=None):
    """Copy the updates chosen to the device of session `s` (updates.install: what Garmin's
    server offers it is asked anew next time; `offered`: all it offered, for the log)."""
    from openexpress import updates as upd

    return upd.install(chosen, s.storage, session=s.client.http, progress=progress,
                       unit_id=s.device.unit_id, offered=offered)


def _below_the_question(s, chosen):
    """What the question before an install shows below its text, right above the buttons: the
    licenses of `chosen`, then the warnings (copy_warnings, and that of a device that loses
    its data on an update); None when there is nothing."""
    from openexpress import updates as upd

    below = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
    for url in dict.fromkeys(u.eula_url for u in chosen if u.eula_url):
        if url.casefold().startswith("https://"):
            # GTK opens it (in the Flatpak through the portal); only a web address is a link.
            below.append(Gtk.LinkButton(uri=url, label=_("License Agreement (EULA)"),
                                        halign=Gtk.Align.CENTER))
        else:
            below.append(Gtk.Label(label=_("License: {url}").format(url=url), wrap=True,
                                   justify=Gtk.Justification.CENTER))
    warnings = [_(w) for w in upd.copy_warnings(chosen)]
    # Read from the answer kept with the session: the question asks no server.
    if upd.loses_data_on_update(getattr(s, "unit_info_answer", None)):
        warnings.append(data_loss_text(s))
    for text in warnings:
        # In the warning colour of the theme and in bold.
        warning = Gtk.Label(label=text, wrap=True, justify=Gtk.Justification.CENTER)
        warning.add_css_class("warning")
        warning.add_css_class("heading")
        below.append(warning)
    return below if below.get_first_child() is not None else None


def _install_task(win, s, chosen):
    """The work, its end and its failure, for run_task, of the copy of the updates `chosen` to
    the device of session `s` (its work to be marked writes_to_device). Then the page shows on
    the device what was written, nothing else. A drive is then ejected once, unasked (the
    device takes an update only once its drive is ejected, and a cable pulled before may leave
    it a file it does not install); over MTP the window says it is done and what to do next."""
    # A drive: the copy is on the disk once written (LocalStorage syncs it), but the device may
    # take it only once the drive is ejected, so the cable may go out only then.
    mass_storage = getattr(s, "mass_storage", False)

    def install():
        rep = install_updates(s, chosen, win.progress, offered=win.updates_cache)
        return rep, mass_storage and is_bike_accessory(
            s.client, s.device, ask=lambda: service.unit_info(s))

    def installed(result):
        rep, bike_accessory = result
        win._show_installed(s, rep.written)  # what was written, nothing else
        if not rep.written:
            return
        if not mass_storage:
            tell_update_done(win, rep)
            return
        win.eject_device(s, bike_accessory)

    def failed(e) -> bool:
        # Under a heading of its own, not the name of the task ("Installing Update…").
        # TRANSLATORS: heading of the error message when installing an update failed
        win.error(_("Update Not Installed"), str(e) or type(e).__name__)
        return True

    return install, installed, failed


def tell_update_done(win, rep) -> None:
    """The message after updates were copied to a device over MTP: what to do next. A message,
    not an error."""
    # TRANSLATORS: heading of the message after an update was copied to the device or its
    # drive was ejected (not a button)
    inform(win, "update-done", _("Done"), UpdatesPage._update_done_text(rep))


def tell_update_finish(win) -> None:
    """After a bike accessory was ejected: the steps that finish its update, as Garmin
    Express tells them."""
    # TRANSLATORS: heading of the message after a bike accessory was ejected, with the steps that
    # finish its update
    inform(win, "update-finish", _("Finish the Update"), bike_accessory_text())


def confirm_install_all(win, items) -> None:
    """Install all, for a drive: one question for the updates `items`, then one copy of them
    with their -META entries and one eject (_install_task). Of two for one file one is copied
    (one_per_file); the question says which waits."""
    from openexpress import updates as upd

    s = win.session
    kept, put_off = upd.one_per_file(items, say=False)
    parts = {u.part_number.upper() for u in kept}
    chosen = [x for x in win.updates_cache if any(x is u for u in kept) or (
        x.is_metadata and x.part_number.upper().removesuffix("-META") in parts)]
    lines = [f"{u.title} {versions_text(u, s.device)}" for u in kept]
    lines += [f"{u.title}: " + _("Can be installed once the device has installed {name}").format(
        name=other.title) for u, other in put_off]
    body = ("\n".join(lines) + "\n\n"
            # TRANSLATORS: in the question before installing updates: their size; {size}: e.g. "25.4
            # MB"
            + _("Size: {size}").format(size=human_size(sum(u.size or 0 for u in kept))))
    def install() -> None:
        work, done, failed = _install_task(win, s, chosen)
        win.run_task(_("Installing Updates…"), writes_to_device(work), done, failed)

    confirm(win, "update-install-all", _("Install Updates?"), body,
            Answer("ok", _("Accept and Install"), install, look="suggested"),
            extra=_below_the_question(s, kept))


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
            items = upd.offered(s.storage, answer, s.device)
            # The kind of device, for the question before an update (it asks no server):
            # usually known already from opening the device.
            try:
                service.unit_info(s)
            except ApiError as e:  # only for a warning; the text may quote the answer
                log.info("software update: unit-info failed (%s), no data-loss check",
                         type(e).__name__)
            return items, upd.beta_offered(answer)

        def failed(_e):
            # The "Device Software" row waited for this answer (show_device): it says so.
            if self._update_check_session is s:
                self._update_check_session = None
            update_check_failed(self)

        self._load("updates", _("Checking for Updates…"), work,
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
        # A file holding an update copied already, which the device has not installed yet:
        # what else goes there would replace it.
        waiting = {w.device_path.casefold(): w for w in staged}
        self._updates_status(sec, system, len(new), beta,
                             ready=staged[0] if staged else None,
                             only=new[0] if len(new) == 1 else None,
                             installable=[u for u in new
                                          if u.device_path.casefold() not in waiting])
        rest = [u for u in new if u is not system]
        if rest:
            g = sec.group(_("Available Updates"))
            for u in rest:
                self._update_row(g, u, waits_for=waiting.get(u.device_path.casefold()))
        # A file the device already has is a reinstall, whatever the server calls it; but not
        # a system update that is waiting for the device to install it.
        re = [u for u in items if (u.is_reinstall or u.on_device) and not u.is_metadata
              and not any(u is w for w in staged)]
        if re:
            # TRANSLATORS: heading of a group on the Updates page that holds the reinstall of
            # components already on the device
            g = sec.group(_("Advanced"))
            exp = Adw.ExpanderRow(
                # TRANSLATORS: title of an expandable row on the Updates page that lists components
                # already on the device, each with a Reinstall button
                title=_("Reinstall Components"),
                subtitle=ngettext("{count} component already on the device; useful for "
                                  "troubleshooting",
                                  "{count} components already on the device; useful for "
                                  "troubleshooting", len(re)).format(count=len(re)))
            g.add(exp)
            for u in sorted(re, key=lambda x: x.title):
                self._update_row(exp, u, reinstall=True,
                                 waits_for=waiting.get(u.device_path.casefold()))

    def _updates_status(self, sec, system, count: int, beta: bool = False, ready=None,
                        only=None, installable=()) -> None:
        """Builds the top of the Updates page in `sec`: the state of the updates as "Device
        software" says it (update_state_text): `count` to install (`only`: the one of them),
        one copied (`ready`) with `count` still to install, or up to date; under it the
        `system` update or the device's version; whether Garmin offers it beta versions
        (`beta`). A drive with more than one update `installable`: Install all, which ejects
        it once after all of them (each Install ejects it after its own)."""
        from openexpress import updates as upd

        d = self.session.device
        mass_storage = getattr(self.session, "mass_storage", False)
        installed = software_text(d.software_version_str)
        detail = note = None
        buttons = []
        if system is not None:
            icon = "software-update-available-symbolic"
            title = update_state_text("available", count, only)
            versions = versions_text(system, d)
            size = human_size(system.size)
            subtitle = caption(versions, size)
            if system.is_beta:
                # The versions, "Beta" and the rest apart, so that "Beta" can have the warning
                # colour of the theme (light and dark); Pango markup knows no colours of the
                # theme.
                # TRANSLATORS: marks a beta version of the device software
                subtitle = (versions, _("Beta"), size)
            detail = caption(system.title, system.severity_text, system.filename)
            # Copied before and offered again: the device has not installed it.
            note = copied_update_text(system)
            # TRANSLATORS: button
            buttons.append(self._button(_("Install") if note is None else _("Reinstall"),
                                        lambda u=system: self.confirm_update(u),
                                        "suggested-action" if note is None else None))
        elif ready is not None:
            # What to do is in the title (as on the row "Device Software"), with how many are
            # still to install (listed below); under it the versions.
            icon = "software-update-available-symbolic"
            title = update_state_text("ready", count, mass_storage=mass_storage)
            versions = versions_text(ready, d)
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
        if mass_storage and len(installable) > 1:
            # TRANSLATORS: button that installs every update offered to a device connected as
            # a USB drive, then ejects it once
            buttons.append(self._button(
                # TRANSLATORS: button that installs every update offered to a device connected as a
                # USB drive, then ejects it once
                _("Install All"), lambda items=list(installable): confirm_install_all(self, items),
                None if buttons else "suggested-action"))
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
                             (note, ["caption", "dim-label"]),
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

    def _update_row(self, parent, u, reinstall: bool = False, waits_for=None) -> None:
        """A row of update `u`; `waits_for`: the update copied already to its file, which the
        device has not installed yet: then no button, as a copy would replace it. One copied
        before that is offered again says so, with Reinstall."""
        details = caption(versions_text(u, self.session.device), human_size(u.size),
                          u.severity_text, u.filename)
        # A system update says so in its title; the name the server gives it goes first.
        subtitle = caption(u.title, details) if u.is_system else details
        if waits_for is not None:
            # TRANSLATORS: below an update whose file on the device holds another update,
            # copied already and not installed yet by the device
            subtitle += "\n" + _("Can be installed once the device has installed {name}").format(
                name=waits_for.title)
        note = copied_update_text(u)
        if note is not None:
            subtitle += "\n" + note
            reinstall = True
        row = Adw.ActionRow(
            # TRANSLATORS: title of the row of an update of the device's own software (firmware), on
            # the Updates page
            title=GLib.markup_escape_text(_("System Software") if u.is_system else u.title),
            subtitle=GLib.markup_escape_text(subtitle),
        )
        if waits_for is None:
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
        s = self.session
        # The update clicked, with its -META entries: not another version of its part.
        chosen = [x for x in self.updates_cache if x is u or (
            x.is_metadata and x.part_number.upper().removesuffix("-META") == u.part_number.upper())]
        body = (f"{u.title} {versions_text(u, s.device)}\n"
                + _("Size: {size}").format(size=human_size(u.size)) + "\n"
                + _("On the device: {path}").format(path=u.device_path)
                # TRANSLATORS: in the question before installing an update: above the list of what
                # the update changes
                + ("\n\n" + _("Changes:") + "\n" + "\n".join("• " + c for c in u.changes[:10])
                   if u.changes else "")
                + ("\n\n" + _("This is a beta version.") if u.is_beta else ""))
        def install() -> None:
            work, done, failed = _install_task(self, s, chosen)
            self.run_task(_("Installing Update…"), writes_to_device(work), done, failed)

        confirm(self, "update-install", _("Install Update?"), body,
                Answer("ok", _("Accept and Install"), install, look="suggested"),
                extra=_below_the_question(s, [u]))

    def ask_to_eject(self, s) -> None:
        """After a sync wrote the files of Garmin Connect's queue to a Garmin drive: it is to
        be ejected first, so that the device takes them; that the cable can go out is said
        only once it is (eject_device). An update is ejected unasked (confirm_update)."""
        confirm(self, "eject-ask", _("Files Saved on the Device"),
                _("Eject the device before you unplug the cable: until then it may not see "
                  "the files from Garmin Connect."),
                # TRANSLATORS: button that ejects the device (its USB drive)
                Answer("eject", _("Eject"), lambda: self.eject_device(s, files=True),
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
                   then=(lambda: tell_update_finish(self)) if bike_accessory else None)

        def failed(e) -> bool:
            if isinstance(e, DriveNotMounted):
                self.error(_("Device Not Mounted"), str(e))
                return True
            confirm(self, "eject-retry", _("Device Not Ejected"),
                    f"{e}\n\n{not_ejected_text(files=files)}",
                    Answer("retry", _("Try Again"),
                           lambda: self.eject_device(s, bike_accessory, files=files),
                           look="suggested"),
                    cancel=_("Close"))
            return True

        self.run_task(_("Ejecting the Device…"), lambda: eject(s.storage), ejected, failed,
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
