"""The page Device Status: what needs attention on the open device (its crashes, little free
space), its storage by kind of file, its crashes and software updates, the files it no longer
needs, its diagnostic files and the report. Only read: its facts are read in the background
(status.read), nothing is asked of the device on the window's thread and nothing is removed.

Has: status_facts (the facts shown), status_show_all (every crash and update shown, after Show
All), status_history and status_history_rows (the group of the crashes and updates and its rows,
drawn again when the window folds or unfolds: Show All beside the group's description, or as its
last row on a narrow window).
"""

from __future__ import annotations

import logging
from datetime import datetime

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from openexpress import diagnostics, history, status  # noqa: E402
from openexpress.gui import words  # noqa: E402
from openexpress.gui.device_page import copy_crash_logs, copy_dumps, device_error_details  # noqa: E402
from openexpress.gui.preferences import diagnostics_switch  # noqa: E402
from openexpress.gui.updates_page import ARROW  # noqa: E402
from openexpress.i18n import _, caption, human_size, ngettext, pgettext, relative_time  # noqa: E402
from openexpress.paths import APP_ID  # noqa: E402

log = logging.getLogger(__name__)

# Until the icon of the page is chosen: an outline, as the other pages' icons are, since the
# status page of a device without diagnostic data shows it large.
PAGE_ICON = "help-about-symbolic"
SUMMARY_ICONS = {"good": "object-select-symbolic", "warning": "dialog-warning-symbolic",
                 "unknown": "dialog-question-symbolic"}
# The kinds of file in GNOME's palette, the same in light and dark; none red, which in
# libadwaita means an error.
PALETTE = {"maps": "#3584e4", "activities": "#33d17a", "music": "#9141ac", "apps": "#ff7800",
           "courses": "#986a44", "updates": "#f6d32d", "logs": "#6f8396", "system": "#9a9996",
           "used": "#9a9996"}
# The kinds of file with a page of their own (the key of its section).
PAGES = ("maps", "activities", "music", "apps", "courses")


def kind_names() -> dict[str, str]:
    """The kinds of file of the storage (history.CATEGORIES), as the page names them."""
    return {
        # TRANSLATORS: a kind of file in the storage of the page Device Status
        "maps": pgettext("storage kind", "Maps"),
        "activities": pgettext("storage kind", "Activities"),
        "music": pgettext("storage kind", "Music and Audio"),
        "apps": pgettext("storage kind", "Apps"),
        "courses": pgettext("storage kind", "Courses and Locations"),
        "updates": pgettext("storage kind", "Updates and Satellite Data"),
        "logs": pgettext("storage kind", "Logs and Memory Dumps"),
        # TRANSLATORS: the rest of the used space: the device's own files and what its file
        # system keeps
        "system": pgettext("storage kind", "System"),
        # TRANSLATORS: the used space when it was not counted by kind of file
        "used": pgettext("storage kind", "Used"),
        # TRANSLATORS: the free space in the storage of the page Device Status
        "free": pgettext("storage kind", "Free"),
    }


class StatusPage:
    def load_status(self) -> None:
        s = self.session
        updates = list(self.updates_cache or ())
        # TRANSLATORS: while the page Device Status reads what it shows
        self._load("status", _("Reading the Device…"), lambda: status.read(s, updates),
                   self.show_status, device=False)

    def show_status(self, facts: status.Facts) -> str | None:
        """The page built from `facts`; "info" for a device without diagnostic data (its
        storage under a status page), else its groups."""
        self.status_facts = facts
        self.status_show_all = False
        self.status_history, self.status_history_rows = None, []
        sec = self.sections["status"]
        now = datetime.now()
        if not facts.has_diagnostics:
            _empty_page(sec, facts)
            return "info"
        _summary(sec, facts, now)
        _storage(self, sec, facts, now)
        # TRANSLATORS: group of the page Device Status
        self.status_history = sec.group(_("Crashes and Updates"),
                                        _("Crashes that restarted the device, and its software "
                                          "updates"))
        render_history(self)
        _space_to_free(sec, facts)
        _diagnostic_files(self, sec, facts, now)
        _report(self, sec)
        return None


# --- the summary ------------------------------------------------------------------------------


def _summary(sec, facts: status.Facts, now: datetime) -> None:
    look, title, line = words.status_summary(facts.crashes, facts.errors_known, facts.free,
                                             facts.capacity, now)
    box = Gtk.Box(spacing=16, css_classes=["card", "oe-status-summary"])
    box.append(Gtk.Image(icon_name=SUMMARY_ICONS[look], pixel_size=32, valign=Gtk.Align.CENTER,
                         css_classes=["oe-status-icon", look]))
    text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, valign=Gtk.Align.CENTER,
                   hexpand=True)
    text.append(Gtk.Label(label=title, wrap=True, xalign=0, css_classes=["title-3"]))
    text.append(Gtk.Label(label=line, wrap=True, xalign=0, css_classes=["dim-label"]))
    box.append(text)
    sec.group().add(box)


# --- the storage ------------------------------------------------------------------------------


def _kinds(facts: status.Facts) -> list[tuple[str, int]]:
    """The kinds of file counted at the connection and their sizes, the largest first: the
    order of the bar, its legend and Category Details. A kind of 0 B is left out (a USB drive
    holds none of most)."""
    return sorted(((kind, facts.categories[kind]) for kind in history.CATEGORIES
                   if facts.categories.get(kind)), key=lambda part: -part[1])


def _parts(facts: status.Facts) -> list[tuple[str, int]]:
    """The kinds of file and their sizes, as the bar and the legend show them: those counted
    at the connection (_kinds), else the used space as one; the free space last, always."""
    parts = _kinds(facts)
    if not facts.categories and facts.capacity and facts.free is not None:
        parts = [("used", facts.capacity - facts.free)]
    if facts.free is not None:
        parts.append(("free", facts.free))
    return parts


def _colour(kind: str) -> Gdk.RGBA:
    rgba = Gdk.RGBA()
    rgba.parse(PALETTE[kind])
    return rgba


def _bar(facts: status.Facts) -> Gtk.DrawingArea:
    """The one drawing of its own of the page: the kinds of file side by side, then the free
    space as the track."""
    capacity = facts.capacity
    parts = [(kind, size) for kind, size in _parts(facts) if kind != "free"]
    area = Gtk.DrawingArea(content_height=14, hexpand=True)

    def draw(widget, cr, width, height):
        r = height / 2
        cr.new_sub_path()
        cr.arc(width - r, r, r, -1.5708, 1.5708)
        cr.arc(r, r, r, 1.5708, 4.7124)
        cr.close_path()
        cr.clip()
        fg = widget.get_color()
        cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.14)
        cr.paint()
        x = 0.0
        for kind, size in parts:
            w = width * size / capacity
            if w >= 1:
                rgba = _colour(kind)
                cr.set_source_rgba(rgba.red, rgba.green, rgba.blue, 1)
                cr.rectangle(x, 0, max(w - 2, 1.5), height)
                cr.fill()
            x += w

    area.set_draw_func(draw)
    return area


def _dot(kind: str) -> Gtk.DrawingArea:
    """The colour of a kind of file; the free space as a ring, the track of the bar."""
    area = Gtk.DrawingArea(content_width=10, content_height=10, valign=Gtk.Align.START,
                           margin_top=5)

    def draw(widget, cr, width, height):
        if kind == "free":
            rgba = widget.get_color()
            cr.set_source_rgba(rgba.red, rgba.green, rgba.blue, 0.55)
            cr.set_line_width(1.5)
            cr.arc(width / 2, height / 2, 4.2, 0, 6.2832)
            cr.stroke()
            return
        rgba = _colour(kind)
        cr.set_source_rgba(rgba.red, rgba.green, rgba.blue, 1)
        cr.arc(width / 2, height / 2, 5, 0, 6.2832)
        cr.fill()

    area.set_draw_func(draw)
    return area


def _legend(facts: status.Facts) -> Gtk.FlowBox:
    """Each kind with its size at the end of its line: two to a line only where both names fit
    on one line (the flow box lays them out at their natural width), else one; a name wraps
    only in a window too narrow for it, and only its line is the taller for it (not
    homogeneous: that would make every line as tall as the tallest)."""
    names = kind_names()
    flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, column_spacing=24, row_spacing=8,
                       min_children_per_line=1, max_children_per_line=2, homogeneous=False,
                       css_classes=["oe-plain", "oe-wrap"])
    for kind, size in _parts(facts):
        item = Gtk.Box(spacing=10)
        item.append(_dot(kind))
        # At the top, with its dot and size: the lines are all as tall as the tallest.
        item.append(Gtk.Label(label=names[kind], wrap=True, xalign=0, hexpand=True,
                              valign=Gtk.Align.START))
        item.append(Gtk.Label(label=human_size(size), xalign=1, valign=Gtk.Align.START,
                              css_classes=["dim-label", "numeric"]))
        flow.append(item)
    return flow


def _storage_card(facts: status.Facts) -> Gtk.Box:
    card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                   css_classes=["card", "oe-status-storage"])
    top = Gtk.Box(spacing=12)
    top.append(Gtk.Label(label=words.space_text(facts.free, facts.capacity), wrap=True, xalign=0,
                         hexpand=True, css_classes=["heading"]))
    if facts.free is not None and facts.capacity:
        # TRANSLATORS: beside "… free of …" on the page Device Status
        top.append(Gtk.Label(label=_("{size} used").format(
            size=human_size(facts.capacity - facts.free)), xalign=1,
            css_classes=["dim-label"]))
    card.append(top)
    if facts.free is not None and facts.capacity:
        card.append(_bar(facts))
    card.append(_legend(facts))
    return card


def _storage(win, sec, facts: status.Facts, now: datetime) -> None:
    # TRANSLATORS: group of the page Device Status: the device's own storage
    g = sec.group(_("Storage"))
    g.add(_storage_card(facts))
    kinds = _kinds(facts)
    if not kinds:
        return
    names = kind_names()
    details = Adw.ExpanderRow(
        # TRANSLATORS: row of the page Device Status that opens to each kind of file
        title=_("Category Details"),
        # TRANSLATORS: subtitle of "Category Details": when the kinds of file were counted,
        # e.g. "… connected, 10 min ago"
        subtitle=_("Counted when the device was connected, {when}").format(
            when=relative_time(facts.counted_at, now)) if facts.counted_at else "")
    for kind, size in kinds:
        row = Adw.ActionRow(title=names[kind], subtitle=human_size(size))
        row.add_prefix(_dot(kind))
        if kind in PAGES and kind in win.sections:
            row.set_activatable(True)
            row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
            row.connect("activated", lambda _r, k=kind: win.select_section(k))
        details.add_row(row)
    rows = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["boxed-list"],
                       margin_top=12)
    rows.append(details)
    g.add(rows)


# --- the crashes and updates ------------------------------------------------------------------


def _collapsed(win) -> bool:
    split = getattr(win, "split", None)
    return bool(split is not None and split.get_collapsed())


def _event_row(event: status.Event, now: datetime) -> Adw.PreferencesRow:
    when = relative_time(event.at, now)
    if event.kind == "crash":
        crash = event.crash
        parts = [when]
        if crash.software:
            # TRANSLATORS: in the subtitle of a crash on the page Device Status
            parts.append(pgettext("crash", "software {version}").format(version=crash.software))
        if event.same_before:
            # TRANSLATORS: in the subtitle of a crash on the page Device Status
            parts.append(ngettext("the same error as the one before",
                                  "the same error as the {count} before",
                                  event.same_before).format(count=event.same_before))
        # TRANSLATORS: a row of "Crashes and Updates" on the page Device Status
        row = Adw.ExpanderRow(title=_("Crash"), subtitle=caption(*parts))
        row.add_prefix(Gtk.Image(icon_name="dialog-warning-symbolic", css_classes=["warning"]))
        details = [device_error_details(crash)]
        if crash.errnum:
            # TRANSLATORS: the error number of a crash in the boot log, e.g. "boot log error
            # 0x6e5d8003"
            details.append(_("boot log error {number}").format(number=crash.errnum))
        row.add_row(Adw.ActionRow(title=_("Error Details"),
                                  subtitle=GLib.markup_escape_text(caption(*details))))
        return row
    if event.kind == "update":
        # TRANSLATORS: a row of "Crashes and Updates": a new version of the device's software
        row = Adw.ActionRow(title=_("Software Updated"),
                            subtitle=caption(when, f"{event.previous}{ARROW}{event.software}"))
        row.add_prefix(Gtk.Image(icon_name="software-update-available-symbolic"))
        return row
    # The version only when the device log began with that connection (status.events).
    version = (pgettext("crash", "software {version}").format(version=event.software)
               if event.software else "")
    # TRANSLATORS: a row of "Crashes and Updates": the first connection to this computer
    row = Adw.ActionRow(title=_("First Connected Here"), subtitle=caption(when, version))
    row.add_prefix(Gtk.Image(icon_name=f"{APP_ID}-device-small-symbolic"))
    return row


def render_history(win) -> None:
    """The crashes and updates, newest first: the five newest, or all after Show All; Show All
    beside the group's description, or its last row on a narrow window (where the description
    leaves it no room). Drawn again when the window folds or unfolds."""
    g, facts = win.status_history, win.status_facts
    if g is None or facts is None:
        return
    for row in win.status_history_rows:
        g.remove(row)
    win.status_history_rows = []
    now = datetime.now()
    shown = facts.events if win.status_show_all else facts.events[:status.SHOWN_EVENTS]
    more = len(shown) < len(facts.events)
    added = [_event_row(event, now) for event in shown]
    if not facts.errors_known:
        # TRANSLATORS: a row of "Crashes and Updates" when the device's error logs could not be
        # read
        added.append(Adw.ActionRow(title=_("Error Logs Not Read"),
                                   subtitle=_("Only the crashes kept on this computer are shown")))
    elif not facts.events:
        # TRANSLATORS: the only row of "Crashes and Updates" when there is none of either
        added.append(Adw.ActionRow(title=_("No Crashes or Updates Yet")))
    narrow = _collapsed(win)
    if more and narrow:
        # TRANSLATORS: shows every crash and update on the page Device Status
        last = Adw.ActionRow(title=_("Show All"), activatable=True)
        last.add_suffix(Gtk.Image(icon_name="go-down-symbolic"))
        last.connect("activated", lambda _r: show_all(win))
        added.append(last)
    for row in added:
        g.add(row)
    win.status_history_rows = added
    if more and not narrow:
        button = Gtk.Button(label=_("Show All"), valign=Gtk.Align.CENTER, css_classes=["flat"])
        button.connect("clicked", lambda _b: show_all(win))
        g.set_header_suffix(button)
    else:
        g.set_header_suffix(None)


def show_all(win) -> None:
    win.status_show_all = True
    render_history(win)


def on_folded(win) -> None:
    """The window folded or unfolded: Show All in its place."""
    render_history(win)


# --- the space to free ------------------------------------------------------------------------


def _size_label(size: int) -> Gtk.Label:
    return Gtk.Label(label=human_size(size), valign=Gtk.Align.CENTER,
                     css_classes=["dim-label", "numeric"])


def _space_to_free(sec, facts: status.Facts) -> None:
    """What the device no longer needs, with its size: only told (removing it is not offered
    here)."""
    older = facts.older_dumps()
    if older == [] and not facts.not_installed:
        return
    # TRANSLATORS: group of the page Device Status: files the device no longer needs
    g = sec.group(_("Space to Free"), _("Files the device no longer needs"))
    # TRANSLATORS: a row of "Space to Free": the memory dumps but the newest
    title = _("Older Memory Dumps")
    if older is None:
        g.add(Adw.ActionRow(title=title, subtitle=_("Not known")))
    elif older:
        row = Adw.ActionRow(title=title, subtitle=ngettext(
            # TRANSLATORS: subtitle of "Older Memory Dumps"
            "{count} file; the newest dump is not counted",
            "{count} files; the newest dump is not counted", len(older)).format(count=len(older)))
        row.add_suffix(_size_label(sum(size for _p, size, _t in older)))
        g.add(row)
    for u in facts.not_installed:
        # TRANSLATORS: a row of "Space to Free": an update openExpress copied that the device
        # did not install
        title = pgettext("space to free", "Update Not Installed by the Device")
        # TRANSLATORS: subtitle of "Update Not Installed by the Device", e.g. "GPS 6.20; …"
        subtitle = _("{name} {version}; the device took the file in but did not install "
                     "it").format(name=u.name, version=u.version)
        row = Adw.ActionRow(title=title, subtitle=subtitle)
        if u.size:
            row.add_suffix(_size_label(u.size))
        g.add(row)


# --- the diagnostic files and the report ------------------------------------------------------


def _copies_text(facts: status.Facts, now: datetime) -> str:
    count, size, last = facts.copies
    if not count:
        # TRANSLATORS: subtitle of "Copies on This Computer" when none was made
        return pgettext("copies", "None yet")
    parts = [ngettext("{count} copy", "{count} copies", count).format(count=count),
             human_size(size)]
    if last is not None:
        # TRANSLATORS: in the subtitle of "Copies on This Computer": when the last was made
        parts.append(_("the last {when}").format(when=relative_time(last, now)))
    return caption(*parts)


def open_copies(win) -> None:
    """Open Folder: the folder of the device's copies in the file manager (its name is the
    device's unit ID: never shown, nor written to the log)."""
    s = win.session
    unit = getattr(getattr(s, "device", None), "unit_id", None)
    if not unit:
        return
    log.info("status page: the folder of the copies opened")
    Gtk.FileLauncher.new(Gio.File.new_for_path(str(diagnostics.folder(unit)))).launch(
        win, None, None, None)


def _diagnostic_files(win, sec, facts: status.Facts, now: datetime) -> None:
    # TRANSLATORS: group of the page Device Status
    g = sec.group(_("Diagnostic Files"))
    g.add(diagnostics_switch(win))
    # TRANSLATORS: row of the page Device Status: the copies of the diagnostic files kept here
    copies = Adw.ActionRow(title=_("Copies on This Computer"),
                           subtitle=_copies_text(facts, now))
    if facts.copies[0]:
        # TRANSLATORS: button that opens the folder of the copies in the file manager
        copies.add_suffix(win._button(_("Open Folder"), lambda: open_copies(win)))
    g.add(copies)
    if facts.hidden_logs:
        row = Adw.ActionRow(title=_("Crash Logs"), subtitle=_(
            "Hidden from the file manager: while they are copied, the device disappears from it "
            "for a moment"))
        row.add_suffix(win._button(_("Copy Crash Logs"),
                                   lambda button: copy_crash_logs(win, button), with_button=True))
        g.add(row)
    dumps = facts.dumps
    if dumps is None:
        subtitle = _("Not known")
    elif not dumps:
        # TRANSLATORS: subtitle of "Memory Dumps" when the device holds none
        subtitle = pgettext("dumps", "None")
    else:
        subtitle = caption(
            ngettext("{count} dump", "{count} dumps", len(dumps)).format(count=len(dumps)),
            human_size(sum(size for _p, size, _t in dumps)), _("encrypted"))
    row = Adw.ActionRow(title=_("Memory Dumps"), subtitle=subtitle)
    if dumps:
        row.add_suffix(win._button(_("Copy Memory Dumps"),
                                   lambda button: copy_dumps(win, button), with_button=True))
    g.add(row)


def _report(win, sec) -> None:
    # TRANSLATORS: group of the page Device Status
    g = sec.group(_("Report a Problem"))
    # TRANSLATORS: row of the page Device Status: the report for openExpress's issues
    row = Adw.ActionRow(title=_("Report to openExpress"), subtitle=_(
        "The device, the system and the recent crashes; no serial number, Unit ID or e-mail"))
    # TRANSLATORS: button that opens the report of the device (a dialog with more to do)
    row.add_suffix(win._button(_("Report…"), lambda: win.show_report()))
    g.add(row)


# --- a device without diagnostic data ---------------------------------------------------------


def _empty_page(sec, facts: status.Facts) -> None:
    sec.info.set_icon_name(PAGE_ICON)
    # TRANSLATORS: the page Device Status of a device that keeps no diagnostic data
    sec.info.set_title(_("No Diagnostic Data"))
    sec.info.set_description(GLib.markup_escape_text(
        _("This device keeps no error logs or memory dumps that openExpress can read")))
    if facts.capacity:
        # Not the card itself under the clamp: the status page's style spaces the boxes there.
        holder = Gtk.Box()
        card = _storage_card(facts)
        card.set_hexpand(True)
        holder.append(card)
        sec.info.set_child(Adw.Clamp(maximum_size=520, valign=Gtk.Align.START, child=holder))
    else:
        sec.info.set_child(None)
