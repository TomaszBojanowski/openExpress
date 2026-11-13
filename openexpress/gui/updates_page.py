"""The Updates page of the window: the software and the files Garmin offers the open device,
their install, the question before it and the eject of a drive after it (updates.py decides
what is written where). UpdatesPage is a mixin of MainWindow (gui/app.py), without __init__ of
its own.

Has: the updates of the last check: updates_cache, updates_beta, _update_check_session.
"""
from __future__ import annotations

import logging
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, GLib, Gtk  # noqa: E402 - imports must follow gi.require_version

from openexpress import answers, known_devices, service  # noqa: E402
from openexpress.connect import ApiError  # noqa: E402
from openexpress.device.gio_storage import (  # noqa: E402
    DriveNotMounted,
    eject,
    ejected_text,
    not_ejected_text,
)
from openexpress.gui.hidden_reading import read_again  # noqa: E402
from openexpress.gui.pictures import bike_accessory_text, is_bike_accessory  # noqa: E402
from openexpress.gui.tasks import writes_to_device  # noqa: E402
from openexpress.gui.window_tasks import (  # noqa: E402
    WRAP_BOX,
    Answer,
    confirm,
    error_text,
    inform,
    still_in_window,
    wrapping_row,
)
from openexpress.gui.words import (  # noqa: E402
    copied_update_text,
    not_installed_advice,
    not_installed_text,
    software_text,
    update_state_text,
)
from openexpress.i18n import SEPARATOR, _, caption, human_size, ngettext, pgettext  # noqa: E402
from openexpress.updates import FINISH_STEPS_WINDOW  # noqa: E402

log = logging.getLogger(__name__)
# Between two versions ("23.39 → 23.40"): no-break spaces keep them on one line.
ARROW = "\u00a0→\u00a0"


def versions_text(u) -> str:
    """The version of update `u`, after the one the device has when that is known and other
    (u.installed_version: the version GarminDevice.xml lists for its part number, or for the
    device's own software the device's; "23.39 → 23.40"). A part of the device with no
    version listed: only its own ("6.70"), never the device's software against it."""
    old = u.installed_version
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


def hidden_folder_text(folder: str) -> str:
    # TRANSLATORS: subtitle of an update on the Updates page that openExpress does not install,
    # without a full stop; {folder}: the folder of the device's top level its file goes to
    return _("Cannot be installed: openExpress cannot see its folder {folder} on this "
             "device").format(folder=folder)


def hidden_software_text(folder: str) -> str:
    # TRANSLATORS: on the Updates page, under the device's software that openExpress does not
    # install; {folder}: the folder of the device's top level, e.g. .System
    text = pgettext("updates page", "openExpress cannot see the folder {folder}, where this "
                    "device keeps its software, so it installs nothing there. Some devices, such "
                    "as car navigators, hide it from the ordinary USB connection.")
    return text.format(folder=folder)


def nothing_installable_title(count: int) -> str:
    """The title of the Updates page when Garmin offers updates and openExpress can install
    none of them (each goes to a folder the device hides)."""
    # TRANSLATORS: title of the Updates page when updates are offered and openExpress can install
    # none of them on this device
    return ngettext("Update Cannot Be Installed", "Updates Cannot Be Installed", count)


def read_only_folder_text(folder: str) -> str:
    # TRANSLATORS: subtitle of an update on the Updates page that openExpress does not install
    # yet, without a full stop; {folder}: a folder the device hides, e.g. .System, which
    # openExpress reads through a USB session of its own
    return _("Not installed yet: it goes to {folder}, which openExpress only "
             "reads").format(folder=folder)


def read_only_software_text(folder: str) -> str:
    # TRANSLATORS: on the Updates page, under the device's software: the device keeps it in a
    # folder hidden from the ordinary USB connection ({folder}, e.g. .System), which openExpress
    # reads through a USB session of its own
    text = pgettext("updates page", "openExpress reads the folder {folder} through its own USB "
                    "connection, but does not install into it yet.")
    return text.format(folder=folder)


def file_size_line(u) -> str | None:
    """What openExpress's own session found of the file of `u` in a folder the device hides,
    by name and size (its contents are never read); None when nothing was found."""
    if u.hidden_size_same is None:
        return None
    if u.hidden_size_same:
        # TRANSLATORS: on the Updates page, without a full stop: a file in a folder the device
        # hides, found by name and size only
        return _("On the device at the same size; its contents not compared")
    # TRANSLATORS: on the Updates page, without a full stop: a file of the update's name is on
    # the device, of another size than offered; {file}: e.g. B444900.update
    return _("On the device: {file} of another size").format(file=u.filename)


def not_read_text(u) -> str | None:
    """Why openExpress's own session could not read the folder of `u` the device hides; None
    when it read it, or did not try."""
    from openexpress import hidden_folders

    if u.hidden_folder is None or u.hidden_session in (None, hidden_folders.READ):
        return None
    if u.hidden_session == hidden_folders.NO_USB:
        # TRANSLATORS: on the Updates page; {folder}: a folder the device hides from the
        # ordinary USB connection, e.g. .System
        text = pgettext("updates page", "The Flatpak version has no access to USB devices, so "
                        "it cannot read the folder {folder}. Give it the permission (see the "
                        "README) or use the version from source.")
    else:
        # TRANSLATORS: on the Updates page; {folder}: a folder the device hides from the
        # ordinary USB connection, e.g. .System
        text = pgettext("updates page", "openExpress cannot open its own USB session with this "
                        "device, so it cannot read the folder {folder}.")
    return text.format(folder=u.hidden_folder)


def add_ons_row_text(count: int) -> str:
    # TRANSLATORS: subtitle of the row "Add-Ons" on the Updates page: items Garmin offers to
    # add to the device (voices, vehicles) that it does not have, without a full stop
    return ngettext("{count} add-on that is not on the device; openExpress does not install "
                    "add-ons yet",
                    "{count} add-ons that are not on the device; openExpress does not install "
                    "add-ons yet", count).format(count=count)


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
            tell_update_done(win, s, rep)
            return
        win.eject_device(s, bike_accessory)

    def failed(e) -> bool:
        # Under a heading of its own, not the name of the task ("Installing Update…").
        # TRANSLATORS: heading of the error message when installing an update failed
        win.error(_("Update Not Installed"), error_text(e))
        return True

    return install, installed, failed


def tell_update_done(win, s, rep) -> None:
    """The message after updates were copied to the device of session `s` over MTP: what to
    do next, and how many updates are still available. A message, not an error."""
    text = UpdatesPage._update_done_text(rep)
    still = still_available_text(win, s)
    # TRANSLATORS: heading of the message after an update was copied to the device or its
    # drive was ejected (not a button)
    inform(win, "update-done", _("Done"), text + "\n\n" + still if still else text)


def still_available_text(win, s) -> str:
    """After updates were copied to the device of session `s`: how many the Updates page still
    offers it (updates.pending), so that the next check finding one is no surprise; "" when
    none, or when the window shows another device now."""
    from openexpress import updates as upd

    cache = win.updates_cache
    if win.session is not s or cache is None:
        return ""
    count = len(upd.pending(cache))
    if not count:
        return ""
    # TRANSLATORS: the last line of the message after updates were copied to the device or its
    # drive was ejected: how many updates the Updates page still lists
    return ngettext("Still available: {count} update", "Still available: {count} updates",
                    count).format(count=count)


def tell_update_finish(win) -> None:
    """After a bike accessory was ejected: the steps that finish its update, as Garmin
    Express tells them."""
    # TRANSLATORS: heading of the message after a bike accessory was ejected, with the steps that
    # finish its update
    inform(win, "update-finish", _("Finish the Update"), bike_accessory_text())


def confirm_install_all(win, items) -> None:
    """Install all: one question for the updates `items`, then one copy of them with their
    -META entries (_install_task): a drive is then ejected once, over MTP the window says what
    to do next. Of two for one file one is copied (one_per_file); the question says which
    waits."""
    from openexpress import updates as upd

    s = win.session
    kept, put_off = upd.one_per_file(items, say=False)
    parts = {u.part_number.upper() for u in kept}
    chosen = [x for x in win.updates_cache if any(x is u for u in kept) or (
        x.is_metadata and x.part_number.upper().removesuffix("-META") in parts)]
    lines = [f"{u.title} {versions_text(u)}" for u in kept]
    lines += [f"{u.title}: " + _("Can be installed once the device has installed {name}").format(
        name=other.title) for u, other in put_off]
    body = ("\n".join(lines) + "\n\n"
            # TRANSLATORS: in the confirmation before installing updates: their size; {size}: e.g.
            # "25.4 MB"
            + _("Size: {size}").format(size=human_size(sum(u.size or 0 for u in kept))))
    def install() -> None:
        work, done, failed = _install_task(win, s, chosen)
        win.run_task(_("Installing Updates…"), writes_to_device(work), done, failed)

    confirm(win, "update-install-all", _("Install Updates?"), body,
            Answer("ok", _("Accept and Install"), install, look="suggested"),
            extra=_below_the_question(s, kept))


def _copied_lines(copied, lead, installed: str):
    """The subtitle and the detail of the Updates page for updates copied to the device
    (`copied`: waiting for it, or not installed by it): the versions of `lead`, the one the
    page names ("Beta" apart, a tuple), else the version `installed`; the title, severity and
    file of the only one, else the names of all."""
    if lead is None:
        subtitle = installed
    else:
        versions = versions_text(lead)
        subtitle = (versions, _("Beta"), "") if lead.is_beta else versions
    detail = (caption(lead.title, lead.severity_text, lead.filename) if len(copied) == 1
              else caption(*(u.title for u in copied)))
    return subtitle, detail


def _label(text: str, styles, **props) -> Gtk.Label:
    """A label in the style classes `styles`."""
    widget = Gtk.Label(label=text, **props)
    for style in styles:
        widget.add_css_class(style)
    return widget


def _status_line(text, styles) -> Gtk.Widget | None:
    """A line of the top of the Updates page in the style classes `styles`: `text`, or the
    tuple of a beta version (its versions, "Beta", the rest), whose "Beta" stands out. None
    without text."""
    if not isinstance(text, tuple):
        return (_label(text, styles, wrap=True, justify=Gtk.Justification.CENTER,
                       max_width_chars=50) if text else None)
    versions, word, after = text
    # In a narrow window the parts go on to the next line: a separator stays with the part
    # before it, never at the start of a line.
    if WRAP_BOX is None:
        # A FlowBox puts parts in columns: one text that wraps, "Beta" in bold (Pango markup
        # knows no colours of the theme); a hard space before each separator.
        line = _label("", styles, wrap=True, justify=Gtk.Justification.CENTER,
                      max_width_chars=50)
        tail = f"{SEPARATOR}{GLib.markup_escape_text(after)}" if after else ""
        line.set_markup(f"{GLib.markup_escape_text(versions)}{SEPARATOR}"
                        f"<b>{GLib.markup_escape_text(word)}</b>{tail}")
        return line
    marked = Gtk.Box()
    marked.append(_label(word, ["warning", "heading"]))
    parts = [_label(versions + SEPARATOR, styles), marked]
    if after:  # none after "Beta" for an update copied already
        marked.append(_label(SEPARATOR, styles))
        parts.append(_label(after, styles, wrap=True, justify=Gtk.Justification.CENTER))
    return wrapping_row(parts, 0)


def check_updates_again(win) -> None:
    """"Check Again" on the Updates page: Garmin's server is asked anew; the folders of the
    device's top level ordinary MTP does not show that it needs are read anew first, and the
    page after them (hidden_reading)."""
    answers.forget_session(answers.SOFTWARE_UPDATES, win.session)
    if not read_again(win):
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
        # Copied, not installed by the device although it was unplugged since: nothing waits.
        untaken = upd.not_installed(items)
        self._set_update_state(new, staged, untaken)
        # One copied already is told at the top of the page (it wins, as on the row "Device
        # software"), else a system update; the others (a second system one, rare) are listed.
        system = None if staged else next((u for u in new if u.is_system), None)
        # A file holding an update copied already, which the device has not installed yet:
        # what else goes there would replace it.
        waiting = {w.device_path.casefold(): w for w in staged}
        self._updates_status(sec, system, len(new), beta, ready=staged,
                             only=new[0] if len(new) == 1 else None,
                             installable=[u for u in upd.installable(new)
                                          if u.device_path.casefold() not in waiting],
                             untaken=untaken)
        rest = [u for u in new if u is not system]
        if rest:
            g = sec.group(_("Available Updates"))
            for u in rest:
                self._update_row(g, u, waits_for=waiting.get(u.device_path.casefold()))
        if untaken and (staged or new):
            # The top tells what there is to do; these are listed with what became of them.
            # TRANSLATORS: heading of a group on the Updates page: updates openExpress copied
            # that the device has not installed although it was unplugged since
            g = sec.group(_("Not Installed by the Device"))
            for u in untaken:
                self._update_row(g, u, not_installed=True)
        # A file the device already has is a reinstall, whatever the server calls it; but not
        # an update copied that is waiting for the device to install it, nor one it did not
        # install.
        re = [u for u in items if (u.is_reinstall or u.on_device) and not u.is_metadata
              and not any(u is w for w in staged) and not any(u is n for n in untaken)]
        extra = upd.add_ons(items)
        if re or extra:
            # TRANSLATORS: heading of a group on the Updates page that holds the reinstall of
            # components already on the device
            g = sec.group(_("Advanced"))
        if extra:
            # TRANSLATORS: title of a row on the Updates page: items Garmin offers to add to the
            # device (voices, vehicles of a car navigator) that it does not have
            g.add(Adw.ActionRow(title=_("Add-Ons"), subtitle=add_ons_row_text(len(extra))))
        if re:
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

    def _updates_status(self, sec, system, count: int, beta: bool = False, ready=(),
                        only=None, installable=(), untaken=()) -> None:
        """Builds the top of the Updates page in `sec`: the state of the updates as the row
        "Device Software" says it (update_state_text), under it the `system` update or the
        device's version. The state: `count` to install (`only`: the one of them); some
        copied that wait for the device (`ready`), with `count` still to install; when
        nothing else is to do, those copied and not installed by the device although it was
        unplugged since (`untaken`), without Eject or Install; else up to date. `beta`:
        Garmin offers the device beta versions. More than one update `installable`: Install
        All, one question and one copy (a drive is ejected once after all of them; each
        Install ejects it after its own)."""
        from openexpress import updates as upd

        d = self.session.device
        mass_storage = getattr(self.session, "mass_storage", False)
        installed = software_text(d.software_version_str)
        detail = note = advice = None
        buttons = []
        if system is not None:
            icon = "software-update-available-symbolic"
            title = (update_state_text("available", count, only) if installable
                     else nothing_installable_title(count))
            versions = versions_text(system)
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
            if system.hidden_read:
                # Its folder the device hides was read: what is there, nothing installed.
                note = read_only_software_text(system.hidden_folder)
                advice = file_size_line(system)
            elif system.hidden_folder is not None:
                # Its folder is not seen: nothing to install, and why.
                note = hidden_software_text(system.hidden_folder)
                advice = not_read_text(system)
            else:
                # TRANSLATORS: button
                buttons.append(self._button(_("Install") if note is None else _("Reinstall"),
                                            lambda u=system: self.confirm_update(u),
                                            "suggested-action" if note is None else None))
        elif ready:
            # What to do is in the title (as on the row "Device Software"), with how many are
            # still to install (listed below); under it the versions of the one copied (of
            # several, the device's software, if it is one of them, and the names of all).
            icon = "software-update-available-symbolic"
            title = update_state_text("ready", count, mass_storage=mass_storage,
                                      waiting=len(ready))
            lead = next((u for u in ready if u.is_system), ready[0] if len(ready) == 1 else None)
            if mass_storage:
                # A drive: the device takes the update once it is ejected.
                buttons.append(self._button(
                    _("Eject"), lambda button, s=self.session: self.eject_device(s, button=button),
                    "suggested-action", with_button=True))
            subtitle, detail = _copied_lines(ready, lead, installed)
        elif count:
            icon, subtitle = "software-update-available-symbolic", installed
            title = (update_state_text("available", count, only) if installable
                     else nothing_installable_title(count))
        elif untaken:
            icon = "dialog-information-symbolic"
            # TRANSLATORS: title of the Updates page: updates openExpress copied are on the
            # device, which was unplugged since and has not installed them
            title = ngettext("Update Not Installed by the Device",
                             "Updates Not Installed by the Device", len(untaken))
            lead = untaken[0] if len(untaken) == 1 else None
            subtitle, detail = _copied_lines(untaken, lead, installed)
            if lead is not None:
                note = not_installed_text(lead)
                if lead.changed_on_device:  # copied again, it may be taken this time
                    buttons.append(self._button(_("Reinstall"),
                                                lambda u=lead: self.confirm_update(u), None))
            advice = not_installed_advice()
        else:
            icon, subtitle = "object-select-symbolic", installed
            title = update_state_text("current")
        if len(installable) > 1:
            buttons.append(self._button(
                # TRANSLATORS: button that installs every update offered to the device in one go
                # (a device connected as a USB drive is then ejected once)
                _("Install All"), lambda items=list(installable): confirm_install_all(self, items),
                None if buttons else "suggested-action"))
        buttons.append(self._button(_("Check Again"), lambda: check_updates_again(self)))
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                      halign=Gtk.Align.CENTER, margin_top=24, margin_bottom=24)
        image = Gtk.Image(icon_name=icon, pixel_size=96)
        if system is None and not ready and not count and not untaken:
            image.add_css_class("success")  # up to date only
        box.append(image)
        for text, styles in ((title, ["title-1"]), (subtitle, ["dim-label"]),
                             (detail, ["caption", "dim-label"]),
                             (note, ["caption", "dim-label"]),
                             (advice, ["caption", "dim-label"]),
                             (_(upd.BETA_OFFERED) if beta else None, ["caption", "dim-label"])):
            line = _status_line(text, styles)
            if line is not None:
                box.append(line)
        for b in buttons:
            b.add_css_class("pill")
        box.append(wrapping_row(buttons, 12, margin_top=8))  # one under the other if need be
        sec.group().add(box)

    def _update_row(self, parent, u, reinstall: bool = False, waits_for=None,
                    not_installed: bool = False) -> None:
        """A row of update `u`; `waits_for`: the update copied already to its file, which the
        device has not installed yet: then no button, as a copy would replace it. One copied
        before that is offered again says so, with Reinstall. `not_installed`: copied, and not
        installed by the device although it was unplugged since: what became of it, and
        Reinstall only when the device changed its file (the same file again changes
        nothing)."""
        if not_installed:
            subtitle = caption(versions_text(u), human_size(u.size), u.filename)
            subtitle += "\n" + not_installed_text(u)
            reinstall, button = True, u.changed_on_device
        else:
            details = caption(versions_text(u), human_size(u.size),
                              u.severity_text, u.filename)
            # A system update says so in its title; the name the server gives it goes first.
            subtitle = caption(u.title, details) if u.is_system else details
            if waits_for is not None:
                # TRANSLATORS: below an update whose file on the device holds another update,
                # copied already and not installed yet by the device
                waiting = _("Can be installed once the device has installed {name}")
                subtitle += "\n" + waiting.format(name=waits_for.title)
            note = copied_update_text(u)
            if note is not None:
                subtitle += "\n" + note
                reinstall = True
            button = waits_for is None and u.hidden_folder is None
            if u.hidden_read:
                subtitle += "\n" + read_only_folder_text(u.hidden_folder)
                if (size := file_size_line(u)) is not None:
                    subtitle += "\n" + size
            elif u.hidden_folder is not None:
                subtitle += "\n" + hidden_folder_text(u.hidden_folder)
        row = Adw.ActionRow(
            # TRANSLATORS: title of the row of an update of the device's own software (firmware), on
            # the Updates page
            title=GLib.markup_escape_text(_("Device Software") if u.is_system else u.title),
            subtitle=GLib.markup_escape_text(subtitle),
        )
        if button:
            # TRANSLATORS: button
            row.add_suffix(self._button(_("Reinstall") if reinstall else _("Install"),
                                        lambda u=u: self.confirm_update(u),
                                        None if reinstall else "suggested-action"))
        parent.add_row(row) if isinstance(parent, Adw.ExpanderRow) else parent.add(row)

    @staticmethod
    def _update_done_text(rep) -> str:
        """What the window says after an update was copied, with what to do next."""
        return _("Files copied to the device.") + ("\n" + _(FINISH_STEPS_WINDOW)
                                                    if rep.written else "")

    def confirm_update(self, u) -> None:
        s = self.session
        # The update clicked, with its -META entries: not another version of its part.
        chosen = [x for x in self.updates_cache if x is u or (
            x.is_metadata and x.part_number.upper().removesuffix("-META") == u.part_number.upper())]
        body = (f"{u.title} {versions_text(u)}\n"
                + _("Size: {size}").format(size=human_size(u.size)) + "\n"
                + _("On the device: {path}").format(path=u.device_path)
                # TRANSLATORS: in the confirmation before installing an update: above the list of
                # what the update changes
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
        files of Garmin Connect's queue to it, not an update. Otherwise an update copied to it
        waits: it is to stay unplugged. Seen unplugged later (gio_storage), that is kept with
        the device: an update it has not installed by then it will not install so."""
        update_waits = not files

        def ejected(_result):
            # After an update (not the files of a sync): how many are still available.
            still = "" if files else still_available_text(self, s)
            # For a bike accessory, as Garmin Express does: how it finishes the update.
            inform(self, "ejected", _("Done"),
                   ejected_text(update_waits) + ("\n\n" + still if still else ""),
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

        unit_id = getattr(getattr(s, "device", None), "unit_id", None)

        def unplugged() -> None:
            if unit_id:
                known_devices.note_unplugged(unit_id)

        self.run_task(_("Ejecting the Device…"),
                      lambda: eject(s.storage, update_waits=update_waits, on_unplugged=unplugged),
                      ejected, failed, button=button)

    def _show_installed(self, session, chosen) -> None:
        """After an update was copied: the files are on the device now, and wait for it to
        take them in (updates.staged: ready to install). The page shows that without asking
        the server or reading the device again."""
        cache = self.updates_cache
        if (self.session is not session or cache is None
                or not all(any(c is u for u in cache) for c in chosen)):
            return  # another device, or the page was checked again meanwhile
        now = time.time()
        for u in chosen:
            u.on_device = True
            u.copied_at = now  # as the next check finds it (mark_copied)
        sec = self.sections["updates"]
        sec.clear()
        self._action_buttons = still_in_window(self._action_buttons)
        self.show_updates(self.updates_cache, self.updates_beta)
