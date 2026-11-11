"""The page Device Status: what needs attention on the open device (its crashes, little free
space), its storage by kind of file, its crashes and software updates, the files it no longer
needs, its diagnostic files and the report. Its facts are read in the background
(status.read), nothing is asked of the device on the window's thread; the only writes are the
removals of Free Up Space, each asked first (freeing.py). For a remembered device not connected,
the same page from the records of this computer (status.read_kept), led to by a row of its view,
without what needs the device.

Has: status_facts (the facts shown), status_show_all (every crash and update shown, after Show
All), status_history and status_history_rows (the group of the crashes and updates and its rows,
drawn again when the window folds or unfolds: Show All beside the group's description, or as its
last row on a narrow window), kept_status (the page of a device not connected, from its
records: KeptStatus).
"""

from __future__ import annotations

import logging
import posixpath
from dataclasses import dataclass, field
from datetime import datetime

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Graphene, Gtk  # noqa: E402

from openexpress import applog, diagnostics, freeing, history, status  # noqa: E402
from openexpress.device.storage import DeviceNotResponding  # noqa: E402
from openexpress.gui import words  # noqa: E402
from openexpress.gui.device_page import (  # noqa: E402
    Section,
    read_diagnostics,
    show_loaded,
    status_changed,
)
from openexpress.gui.preferences import diagnostics_switch  # noqa: E402
from openexpress.gui.tasks import read_again_after_failure, writes_to_device  # noqa: E402
from openexpress.gui.updates_page import ARROW  # noqa: E402
from openexpress.gui.window_tasks import (  # noqa: E402
    Answer,
    confirm,
    inform,
    read_space,
    refresh_space,
    render_space,
    row_title,
)
from openexpress.i18n import (  # noqa: E402
    _,
    caption,
    full_time,
    human_size,
    ngettext,
    pgettext,
    relative_time,
)
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


def device_error_details(entry) -> str:
    """What else the device logged with the error (its number, where, on which screen)."""
    parts = []
    if entry.error:
        # TRANSLATORS: the number of an error the device logged, e.g. "error 0x003"
        parts.append(_("error {number}").format(number=entry.error))
    if entry.program_counter:
        # TRANSLATORS: where in its software the device stopped (a program counter)
        parts.append(_("at {address}").format(address=entry.program_counter))
    if entry.screen:
        # TRANSLATORS: the number of the screen the device showed when the error came
        parts.append(_("screen {number}").format(number=entry.screen))
    return caption(*parts)


def copy_crash_logs(win, button=None) -> None:
    """Copy Crash Logs: one read-only session of our own (the device leaves the file manager
    for a moment); none found: the button is not offered again."""
    s = win.session
    if s is None:
        return

    def work():
        # Its boot log is among the copies then: the row, the page and the report count the
        # crashes from them anew (status.told, report).
        return diagnostics.copy_crash_logs(s.device, s.storage, progress=win.progress)

    def done(copied):
        if win.session is not s:
            return
        if copied.saved:
            win.toast(_("Crash logs copied to this computer"))
            status_changed(win)  # the crashes of its boot log, the copies kept
            return
        win.toast(_("The device has no hidden crash logs"))
        if s.diagnostics is not None:
            s.diagnostics.hidden_logs = False
        status_changed(win)

    win.run_task(_("Copying the Crash Logs…"), work, done, button=button)


def copy_dumps(win, button=None) -> None:
    """Copy Memory Dumps: through the ordinary connection, the newest first while they fit
    within the limit of the copies."""
    s = win.session
    if s is None:
        return

    def work():
        return diagnostics.copy_dumps(s.device, s.storage, progress=win.progress)

    def done(copied):
        if win.session is not s:
            return
        copied_count = copied.new + copied.unchanged
        text = ngettext("{count} memory dump copied to this computer",
                        "{count} memory dumps copied to this computer",
                        copied_count).format(count=copied_count)
        if copied.not_copied:
            # TRANSLATORS: after the files copied to this computer: those left out as they did
            # not fit in the room the copies may take ({size}: "50 MB")
            text = caption(text, ngettext(
                "{count} file skipped: did not fit in {size}",
                "{count} files skipped: did not fit in {size}",
                len(copied.not_copied)).format(count=len(copied.not_copied),
                                               size=human_size(diagnostics.LIMIT)))
        win.toast(text)
        status_changed(win)  # the copies kept

    win.run_task(_("Copying the Memory Dumps…"), work, done, button=button)


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


def read_page(s) -> status.Facts:
    """The page's reading, off the window's thread: the free space read now (the session's,
    the one source of the space the window shows: the page, the row Device Storage, the
    report), then the facts of the device (status.read)."""
    s.space = read_space(s.storage, status.known_capacity(s))
    return status.read(s)


class StatusPage:
    def load_status(self) -> None:
        """The page read once the window's reading of the device's error logs is done (its
        results are in the session then); until then it shows that it reads, without a task:
        the window stays free (Sync, closing), and read_diagnostics reads the page when done."""
        s = self.session
        if s is None:
            return
        reading = getattr(s, "diagnostics_reading", None)
        if reading is None and getattr(s, "diagnostics", None) is None:
            read_diagnostics(self)
            reading = getattr(s, "diagnostics_reading", None)
        if reading is not None and not reading.is_set():
            # On the session (the window holds nothing of it): never read for another one.
            s.page_waits = True
            self.sections["status"].show("loading")
            return
        s.page_waits = False
        if self.busy:
            # Another task runs (a sync: TrueUp waits for minutes): the page at once from what
            # needs nothing of the device, read in full once the task ends.
            show_loaded(self, "status", self.show_status, status.read(s, ask_device=False))
            _read_when_free(self, s)
            return
        # TRANSLATORS: while the page Device Status reads what it shows
        self._load("status", _("Reading the Device…"), lambda: read_page(s),
                   self.show_status, device=False)

    def show_status(self, facts: status.Facts) -> str | None:
        """The page built from `facts`; "info" for a device without diagnostic data (its
        storage under a status page), else its groups, under a banner while another task
        runs."""
        sec = self.sections["status"]
        # TRANSLATORS: banner on top of the page Device Status while another task (such as a
        # sync) runs: freeing space and copying crash logs and memory dumps wait until it ends
        sec.banner.set_title(_("Another task is running\u00a0– free up space or copy diagnostic "
                               "files when it ends"))
        sec.banner.set_revealed(facts.device_busy)
        render_space(self)  # the space the page read: the row Device Storage tells it too
        return _build(self, sec, facts, self)


def _read_when_free(win, s) -> None:
    """The page read in full once the window's task ends (status_changed: at once while it is
    shown, else when it is chosen); nothing for a device no longer open."""
    def check():
        if win.session is s and win.busy:
            return True
        win._retry_sources.pop("status", None)
        if win.session is s:
            status_changed(win)
        return False

    old = win._retry_sources.pop("status", None)
    if old is not None:
        GLib.source_remove(old)
    win._retry_sources["status"] = GLib.timeout_add(700, check)


def _build(win, sec, facts: status.Facts, view, unit: int | None = None) -> str | None:
    """The page of `facts` in `sec`: that of the open device (`view` the window), or of a
    device not connected, from its records (facts.connected false; `view` its holder,
    win.kept_status): then what needs the device is left out (Free Up Space, the copies of its
    crash logs and dumps, the report). `view` holds the page's crashes and updates and their
    rows (render_history). `unit`: the device whose copies Open Folder shows (the open one's
    when None). "info" for a device without diagnostic data."""
    view.status_facts = facts
    view.status_show_all = False
    view.status_history, view.status_history_rows = None, []
    now = datetime.now()
    _log_shown(facts, now)
    if not facts.has_diagnostics:
        _empty_page(sec, facts)
        return "info"
    _summary(sec, facts, now)
    _storage(win, sec, facts, now)
    # TRANSLATORS: group of the page Device Status
    view.status_history = sec.group(_("Crashes and Updates"),
                                    _("Crashes that restarted the device, and its software "
                                      "updates"))
    render_history(win, view)
    if facts.connected and not facts.device_busy:
        _free_up_space(win, sec, facts)
    _diagnostic_files(win, sec, facts, now, unit)
    if facts.connected:
        _report(win, sec)
    return None


def _log_shown(facts: status.Facts, now: datetime) -> None:
    """What the page shows, in one line of the log: the look of its summary and why it is not
    known, the crashes, the space, what can be freed and whether the update files were checked
    (no file name, no title of the window)."""
    look = words.status_summary(facts.crashes, facts.errors_known, facts.free, facts.capacity,
                                now)[0] if facts.has_diagnostics else "no diagnostic data"
    why = " (error logs not read)" if facts.has_diagnostics and not facts.errors_known else ""
    older = freeing.older_dumps(facts.dumps)
    dumps = ("older dumps not known" if older is None else
             f"{len(older)} older dumps ({applog.size(sum(s for _p, s, _t in older))})")
    files = [u for u in facts.not_installed if not freeing.waits_to_install(u)]
    waiting = len(facts.not_installed) - len(files)
    checked = ("not checked (another task is running)" if facts.device_busy else
               f"not checked ({facts.updates_why})" if facts.updates_why is not None
               else "checked")
    log.info("device status%s: %s%s; crashes in the last 30 days: %d; %s free of %s; to free: "
             "%s, %d update files%s; update files %s",
             " (not connected, from the records)" if not facts.connected else
             " (another task is running)" if facts.device_busy else "", look, why,
             len(status.recent(facts.crashes, now)), applog.size(facts.free),
             applog.size(facts.capacity), dumps, len(files),
             f", {waiting} waiting to be installed" if waiting else "", checked)


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


def _paint_dot(widget, cr, kind: str, x: float, y: float) -> None:
    """The colour of a kind of file around (x, y); the free space as a ring, the track of the
    bar."""
    if kind == "free":
        rgba = widget.get_color()
        cr.set_source_rgba(rgba.red, rgba.green, rgba.blue, 0.55)
        cr.set_line_width(1.5)
        cr.arc(x, y, 4.2, 0, 6.2832)
        cr.stroke()
        return
    rgba = _colour(kind)
    cr.set_source_rgba(rgba.red, rgba.green, rgba.blue, 1)
    cr.arc(x, y, 5, 0, 6.2832)
    cr.fill()


def _dot(kind: str) -> Gtk.DrawingArea:
    """The colour of a kind of file in the legend, level with the first line of its name."""
    area = Gtk.DrawingArea(content_width=10, content_height=10, valign=Gtk.Align.START,
                           margin_top=5)
    area.set_draw_func(lambda w, cr, width, height: _paint_dot(w, cr, kind, width / 2,
                                                                height / 2))
    return area


def title_level(area) -> float | None:
    """How far below the top of `area` the middle of the first line of the title of the row
    holding it is; None outside a row or before the row is laid out."""
    row = area.get_ancestor(Adw.ActionRow)
    title = row_title(row) if row is not None else None
    if title is None:
        return None
    _ink, line = title.get_layout().get_line_readonly(0).get_pixel_extents()
    ok, point = title.compute_point(area, Graphene.Point().init(0, line.height / 2))
    return point.y if ok else None


def _row_dot(kind: str) -> Gtk.DrawingArea:
    """The colour of a kind of file beside its row of Category Details, level with the first
    line of the row's title as in the legend: drawn where the title is, which the row's height
    and the title's wrapping move (a prefix of a row is as tall as the row)."""
    area = Gtk.DrawingArea(content_width=10)

    def draw(widget, cr, width, height):
        level = title_level(widget)
        _paint_dot(widget, cr, kind, width / 2, height / 2 if level is None else level)

    area.set_draw_func(draw)
    return area


def _legend(facts: status.Facts) -> Gtk.Box:
    """Each kind on a line of its own, its size at the end, one column at any width (the
    layout stays the same when sizes change); a name wraps only in a window too narrow for it,
    and only its line is the taller for it."""
    names = kind_names()
    legend = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                     css_classes=["oe-status-legend"])
    for kind, size in _parts(facts):
        item = Gtk.Box(spacing=10)
        item.append(_dot(kind))
        # At the top, with its dot and size, when the name wraps.
        item.append(Gtk.Label(label=names[kind], wrap=True, xalign=0, hexpand=True,
                              valign=Gtk.Align.START))
        item.append(Gtk.Label(label=human_size(size), xalign=1, valign=Gtk.Align.START,
                              css_classes=["dim-label", "numeric"]))
        legend.append(item)
    return legend


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


def _counted_text(facts: status.Facts, now: datetime) -> str:
    """When the kinds of file were counted: at the connection, or again after a removal."""
    if facts.counted_at is None:
        return ""
    when = relative_time(facts.counted_at, now)
    if not facts.connected and not facts.recounted:
        # TRANSLATORS: subtitle of "Category Details" of a device not connected, from the
        # records: when its last connection counted them, e.g. "… connection, 2 days ago"
        return _("Counted at the last connection, {when}").format(when=when)
    if facts.recounted:
        # TRANSLATORS: subtitle of "Category Details" after files were written to the device or
        # removed from it (Free Up Space), e.g. "… change, just now"
        return _("Counted again after a change, {when}").format(when=when)
    # TRANSLATORS: subtitle of "Category Details": when the kinds of file were counted,
    # e.g. "… connected, 10 min ago"
    return _("Counted when the device was connected, {when}").format(when=when)


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
        subtitle=_counted_text(facts, now))
    for kind, size in kinds:
        row = Adw.ActionRow(title=names[kind], subtitle=human_size(size))
        row.add_prefix(_row_dot(kind))
        if facts.connected and kind in PAGES and kind in win.sections:
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


def render_history(win, view=None) -> None:
    """The crashes and updates, newest first: the five newest, or all after Show All; Show All
    beside the group's description, or its last row on a narrow window (where the description
    leaves it no room). Drawn again when the window folds or unfolds. `view`: what holds the
    page's group and rows (_build): the window's page by default."""
    view = win if view is None else view
    kept = view is not win
    g, facts = view.status_history, view.status_facts
    if g is None or facts is None:
        return
    for row in view.status_history_rows:
        g.remove(row)
    view.status_history_rows = []
    now = datetime.now()
    shown = facts.events if view.status_show_all else facts.events[:status.SHOWN_EVENTS]
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
        last.connect("activated", lambda _r, kept=kept: show_all(win, kept=kept))
        added.append(last)
    for row in added:
        g.add(row)
    view.status_history_rows = added
    if more and not narrow:
        button = Gtk.Button(label=_("Show All"), valign=Gtk.Align.CENTER, css_classes=["flat"])
        button.connect("clicked", lambda _b, kept=kept: show_all(win, kept=kept))
        g.set_header_suffix(button)
    else:
        g.set_header_suffix(None)


def show_all(win, kept: bool = False) -> None:
    """Show All of the page of the open device, or of that of a device not connected (`kept`):
    looked up when clicked, so that the handler holds no page (the page is released once it
    is closed)."""
    view = win.kept_status if kept else win
    if view is None:
        return
    view.status_show_all = True
    render_history(win, view)


def on_folded(win) -> None:
    """The window folded or unfolded: Show All in its place, on the page of the open device and
    on that of a device not connected."""
    render_history(win)
    kept = win.kept_status
    if kept is not None and kept.status_history is not None:
        render_history(win, kept)


# --- freeing space ----------------------------------------------------------------------------


def _free_up_space(win, sec, facts: status.Facts) -> None:
    """What the device no longer needs, with its size and a button that removes it after a
    question (freeing.py); an update file in the main folder only shown, as waiting to be
    installed."""
    older = freeing.older_dumps(facts.dumps)
    plan = facts.dumps_plan
    if older == [] and not facts.not_installed:
        return
    # TRANSLATORS: group of the page Device Status: files the device no longer needs, each
    # with a button that removes it
    g = sec.group(_("Free Up Space"),
                  # TRANSLATORS: description of "Free Up Space"
                  _("Activities, maps, music, courses and settings are never removed"))
    # TRANSLATORS: a row of "Free Up Space": the memory dumps but the newest
    title = _("Older Memory Dumps")
    if older is None:
        g.add(Adw.ActionRow(title=title, subtitle=_("Not known")))
    elif older:
        # The size at the end of the subtitle: the end of the row is the button's.
        row = Adw.ActionRow(title=title, subtitle=caption(ngettext(
            # TRANSLATORS: subtitle of "Older Memory Dumps", followed by their size
            "{count} file; the newest dump is not counted",
            "{count} files; the newest dump is not counted", len(older)).format(count=len(older)),
            human_size(sum(size for _p, size, _t in older))))
        # Remove on the row (its question names the copy first): Copy and Remove would leave
        # its subtitle no room in a narrow window. The full name in its tooltip and for screen
        # readers. None when they may not be removed (freeing.dumps_removable).
        if plan is not None:
            button = win._button(_("Remove"), lambda button: confirm_dumps(win, plan, button),
                                 style="destructive-action", with_button=True)
            button.set_tooltip_text(_dumps_answer(plan))
            button.update_property([Gtk.AccessibleProperty.LABEL], [_dumps_answer(plan)])
            row.add_suffix(button)
        g.add(row)
    for u in facts.not_installed:
        # TRANSLATORS: the title of the row of an update file in "Free Up Space": the update's
        # name and version, e.g. "GPS software 6.20"; what became of it is the subtitle
        title = _("{name} {version}").format(name=u.name, version=u.version)
        if freeing.waits_to_install(u):
            # TRANSLATORS: subtitle of a row of "Free Up Space": a file of the device's own
            # software it has not installed yet; removing it would cancel the update, so the
            # row has no button
            g.add(Adw.ActionRow(title=title, subtitle=_(
                "Waits to be installed; removing the file would cancel the update")))
            continue
        # TRANSLATORS: subtitle of a row of "Free Up Space": an update openExpress copied
        # that the device did not install, followed by the size of its file
        subtitle = _("The device took the file in but did not install it")
        row = Adw.ActionRow(title=title, subtitle=caption(
            subtitle, human_size(u.size) if u.size else ""))
        # TRANSLATORS: button of the row of an update file the device did not install:
        # removes the file
        row.add_suffix(win._button(_("Remove"), lambda button, u=u: confirm_update(win, u, button),
                                   style="destructive-action", with_button=True))
        g.add(row)


def _dumps_answer(plan: freeing.DumpPlan) -> str:
    """Copy and Remove when at least one dump is copied first, else Remove."""
    # TRANSLATORS: button and answer that copy the older memory dumps to this computer, then
    # remove them from the device
    return _("Copy and Remove") if plan.copied else _("Remove")


def _dumps_question(plan: freeing.DumpPlan) -> str:
    """The question before the older dumps are removed: how many, the space, which are copied
    first and which are not (their copies would not fit within the limit); the newest
    stays."""
    copied, without_copy, older = plan.copied, plan.without_copy, plan.older
    count, size = len(older), human_size(sum(s for _p, s, _t in older))
    limit = freeing.limit_words()
    if not without_copy:
        text = ngettext("{count} memory dump ({size}) will be copied to this computer and then "
                        "removed from the device.",
                        "{count} memory dumps ({size}) will be copied to this computer and then "
                        "removed from the device.", count).format(count=count, size=size)
    elif not copied:
        # TRANSLATORS: {limit} is the most the copies of removed dumps may take, "100 MB"
        text = ngettext("{count} memory dump ({size}) will be removed from the device without a "
                        "copy, as the copies would not fit in {limit}.",
                        "{count} memory dumps ({size}) will be removed from the device without a "
                        "copy, as the copies would not fit in {limit}.", count).format(
            count=count, size=size, limit=limit)
    else:
        # TRANSLATORS: the first part of the question when some dumps are copied first and
        # some are too large; "{copied part}, {not copied part}." follows
        head = ngettext("{count} memory dump ({size}) will be removed from the device:",
                        "{count} memory dumps ({size}) will be removed from the device:",
                        count).format(count=count, size=size)
        n, room = len(copied), human_size(sum(s for _p, s, _t in copied))
        # TRANSLATORS: a part of the question: how many dumps are copied first, e.g.
        # "2 (75.3 MB) after a copy on this computer"
        first = ngettext("{count} ({size}) after a copy on this computer",
                         "{count} ({size}) after a copy on this computer", n).format(
            count=n, size=room)
        n, room = len(without_copy), human_size(sum(s for _p, s, _t in without_copy))
        # TRANSLATORS: a part of the question: how many dumps are removed without a copy
        # TRANSLATORS: {limit} is the most the copies of removed dumps may take, "100 MB"
        second = ngettext("{count} ({size}) without a copy, as the copies would not fit in "
                          "{limit}",
                          "{count} ({size}) without a copy, as the copies would not fit in "
                          "{limit}", n).format(count=n, size=room, limit=limit)
        text = f"{head} {first}, {second}."
    return f"{text} {_('The newest dump stays on the device.')}"


def _stopped_words(e: BaseException) -> str:
    if isinstance(e, DeviceNotResponding):
        # TRANSLATORS: why a removal from the device did not go to its end
        return _("the device stopped responding")
    # TRANSLATORS: why a removal from the device did not go to its end, or was not counted
    # after it
    return _("the device could not be read after the removal")


def _then_counted(s, remove, kind: str = "logs"):
    """The work of a removal: `remove(freed)`, which fills a result of its own as it goes,
    then, when something was removed, the kinds of file, the space and the dumps counted again
    (status.recount), still off the window's thread. What comes after the first removal (the
    device stopping, a count that fails) never loses what was done: it is told with it
    (freed.stopped). Before it (the files changed since the question: freeing.ListChanged, or
    nothing removed) the task fails as it is, and the page is read again."""
    def work():
        freed = freeing.Freed()
        try:
            freed = remove(freed) or freed
        except freeing.ListChanged:
            raise
        except Exception as e:
            if not freed.removed and not freed.kept:
                raise  # nothing removed: the failure as it is
            log.info("freeing: stopped after a removal (%s)", type(e).__name__)
            freed.stopped = _stopped_words(e)
        counted = None
        if freed.removed:
            try:
                # Not counted (the device stopped or went): the records put right by what was
                # removed, so that the page from them does not tell what is gone.
                counted = status.recount(s.device, s.storage, removed={kind: freed.removed})
            except Exception as e:  # noqa: BLE001 - the removal is told; its count is not known
                log.info("freeing: not counted again after the removal (%s)", type(e).__name__)
                freed.stopped = freed.stopped or _stopped_words(e)
        return freed, counted

    work.counts_again = True  # the window does not count the device again after it
    return work


def _for_the_question(win, s, remove):
    """The answer of a question of the page, run only for the session it was asked for: a
    device unplugged, or opened again (a restart, plugged in again), meanwhile has nothing
    removed, and its page is read again."""
    def answered():
        if win.session is s:
            remove()
            return
        log.info("freeing: the device changed since the question, nothing removed")
        # TRANSLATORS: heading of the message when the device was unplugged or opened again
        # while a question of the page Device Status was open
        inform(win, "free-device-changed", _("Nothing Removed"),
               _("The device was unplugged or connected again since the question. Look at the "
                 "list again."))
        status_changed(win)

    return answered


def confirm_dumps(win, plan: freeing.DumpPlan, button=None) -> None:
    """Remove Older Memory Dumps?: after the question, the task that copies and removes them as
    the question names them (freeing.remove_dumps with its plan: nothing removed when the
    device holds other dumps now), then the page read again."""
    s = win.session

    def done(result):
        freed, counted = result
        _freed(win, s, freed, ngettext(
            "{count} memory dump removed, {size} freed",
            "{count} memory dumps removed, {size} freed", len(freed.removed)).format(
            count=len(freed.removed), size=human_size(freed.freed)), counted)

    def remove():
        win.run_task(
            # TRANSLATORS: while the older memory dumps are copied and removed
            _("Removing the Memory Dumps…"),
            writes_to_device(_then_counted(s, lambda freed: freeing.remove_dumps(
                s.storage, s.device.unit_id, plan, progress=win.progress, freed=freed))),
            done, read_again_after_failure(win, "status"), button=button)

    # TRANSLATORS: heading of the question before the older memory dumps are removed
    confirm(win, "free-dumps", _("Remove Older Memory Dumps?"), _dumps_question(plan),
            Answer("ok", _dumps_answer(plan), _for_the_question(win, s, remove),
                   look="destructive"))


def confirm_update(win, u, button=None) -> None:
    """Remove the Update File?: after the question, the task that removes it
    (freeing.remove_update); the Updates page offers the update again."""
    s = win.session

    def done(result):
        freed, counted = result
        if freed.removed:
            u.on_device = u.changed_on_device = False  # gone: offered again (updates.pending)
            updates = getattr(win, "sections", {}).get("updates")
            if updates is not None:
                updates.loaded = False
        # TRANSLATORS: after the file of an update the device did not install was removed
        _freed(win, s, freed, _("Update file removed, {size} freed").format(
            size=human_size(freed.freed)), counted)

    def remove():
        # TRANSLATORS: while the file of an update the device did not install is removed
        win.run_task(_("Removing the Update File…"),
                     writes_to_device(_then_counted(s, lambda freed: freeing.remove_update(
                         s.storage, u, getattr(s, "client", None), s.device, freed=freed),
                         kind="updates")),
                     done, read_again_after_failure(win, "status"), button=button)

    confirm(win, "free-update",
            # TRANSLATORS: heading of the question before the file of an update the device did
            # not install is removed
            _("Remove the Update File?"),
            _("The device took in {name} {version} ({size}) but did not install it. The file "
              "will be removed from the device; the Updates page will offer the update "
              "again.").format(name=u.name, version=u.version, size=human_size(
                  u.listed_file[0] if u.listed_file else u.size or 0)),
            Answer("ok", _("Remove"), _for_the_question(win, s, remove), look="destructive"))


def _freed(win, s, freed: freeing.Freed, done_text: str, counted=None) -> None:
    """After a removal: the kinds of file and the space counted again kept for the page and the
    device page (`counted`, status.recount; the row Device Storage drawn again; its free space
    read again when the count failed), what was freed (a toast), what the device did not remove
    or what was not removed as its copy failed, and why the removal stopped (one message), then
    the page read again. A device unplugged meanwhile: what was removed is still told, its
    pages left alone."""
    gone = win.session is not s
    read_later = False  # the page read again when the free space is (refresh_space)
    if not gone:
        if counted is not None:
            status.counted_again(s, counted)
            render_space(win)
        elif freed.removed:
            refresh_space(win, s)
            read_later = True
    elif freed.removed and freed.stopped is None and counted is None:
        # Unplugged before it was counted again (one counted went to its end).
        # TRANSLATORS: why a removal from the device did not go to its end
        freed.stopped = _("the device was unplugged")
    problems = [(path, why) for path, why in freed.kept]
    parts = []
    if freed.stopped is not None and freed.removed:
        # TRANSLATORS: in the message when a removal from the device stopped, or was not
        # counted again, after files were removed; {done} tells what was removed, {why} why
        parts.append(_("{done}. Then {why}.").format(done=done_text, why=freed.stopped))
    if problems:
        n = len(problems)
        if freed.removed:
            text = ngettext("The device did not remove {count} of the files; the others are "
                            "removed.",
                            "The device did not remove {count} of the files; the others are "
                            "removed.", n).format(count=n)
        else:
            text = ngettext("The device did not remove the file.",
                            "The device did not remove the {count} files.", n).format(count=n)
        reasons = "\n".join(f"{posixpath.basename(path)}: {why}" for path, why in problems)
        parts.append(f"{text}\n\n{reasons}")
    if freed.copy_failed:
        n = len(freed.copy_failed)
        # TRANSLATORS: after Copy and Remove, the memory dumps left on the device because
        # their copy on this computer failed; their names follow
        text = ngettext("{count} memory dump was not removed, as its copy on this computer "
                        "failed.",
                        "{count} memory dumps were not removed, as their copies on this "
                        "computer failed.", n).format(count=n)
        names = "\n".join(posixpath.basename(path) for path in freed.copy_failed)
        parts.append(f"{text}\n\n{names}")
    if freed.stopped is not None and freed.removed:
        # TRANSLATORS: heading of the message when a removal from the device stopped, or was
        # not counted again, after files were removed
        inform(win, "free-stopped", _("Removal Not Finished"), "\n\n".join(parts))
    elif problems or freed.copy_failed:
        # TRANSLATORS: heading of the message when the device did not remove every file
        inform(win, "free-not-removed", _("Not All Files Removed"), "\n\n".join(parts))
    elif freed.removed:
        win.toast(done_text)
    elif freed.nothing:
        # TRANSLATORS: toast after Remove on the page Device Status when the files were removed
        # by something else meanwhile
        win.toast(_("Nothing removed: the files were no longer on the device"))
    if not gone and not read_later:
        status_changed(win)  # the page and the row of the device page read again


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


def open_copies(win, unit: int | None = None) -> None:
    """Open Folder: the folder of the device's copies in the file manager (its name is the
    device's unit ID: never shown, nor written to the log); the open device's when `unit` is
    None."""
    test_drive = False
    if unit is None:
        unit = getattr(getattr(win.session, "device", None), "unit_id", None)
        test_drive = bool(getattr(getattr(win.session, "storage", None), "test_drive", False))
    if not unit:
        return
    log.info("status page: the folder of the copies opened")
    Gtk.FileLauncher.new(Gio.File.new_for_path(str(diagnostics.folder(unit, test_drive)))).launch(
        win, None, None, None)


def _diagnostic_files(win, sec, facts: status.Facts, now: datetime,
                      unit: int | None = None) -> None:
    # TRANSLATORS: group of the page Device Status
    g = sec.group(_("Diagnostic Files"))
    g.add(diagnostics_switch(win))
    # TRANSLATORS: row of the page Device Status: the copies of the diagnostic files kept here
    copies = Adw.ActionRow(title=_("Copies on This Computer"),
                           subtitle=_copies_text(facts, now))
    if facts.copies[0]:
        # Open on the row, Open Folder in its tooltip and for screen readers: the longer words
        # would leave its subtitle no room in a narrow window.
        # TRANSLATORS: button of "Copies on This Computer": opens the folder of the copies in
        # the file manager; short, for a narrow window
        button = win._button(pgettext("open the folder of the copies", "Open"),
                             lambda: open_copies(win, unit))
        # TRANSLATORS: the full name of the button that opens the folder of the copies
        button.set_tooltip_text(_("Open Folder"))
        button.update_property([Gtk.AccessibleProperty.LABEL], [_("Open Folder")])
        copies.add_suffix(button)
    g.add(copies)
    if not facts.connected:
        g.add(Adw.ActionRow(title=_("Memory Dumps"), subtitle=_dumps_at_last(facts)))
        return
    if facts.device_busy:
        return  # the copies of its logs wait until the task ends (the banner)
    if facts.hidden_logs:
        row = Adw.ActionRow(title=_("Crash Logs"), subtitle=_(
            "Hidden from the file manager: while they are copied, the device disappears from it "
            "for a moment"))
        row.add_suffix(_copy_button(win, _("Copy Crash Logs"),
                                    lambda button: copy_crash_logs(win, button)))
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
        row.add_suffix(_copy_button(win, _("Copy Memory Dumps"),
                                    lambda button: copy_dumps(win, button)))
    g.add(row)


def _copy_button(win, name: str, cb) -> Gtk.Button:
    """A button of Diagnostic Files that says only "Copy", so that its row keeps room for its
    subtitle in a narrow window; its full `name` in its tooltip and for screen readers."""
    # TRANSLATORS: a button of the rows "Crash Logs" and "Memory Dumps" on the page Device
    # Status: copies them to this computer (the row says what); short, for a narrow window
    button = win._button(pgettext("copy diagnostic files", "Copy"), cb, with_button=True)
    button.set_tooltip_text(name)
    button.update_property([Gtk.AccessibleProperty.LABEL], [name])
    return button


def _dumps_at_last(facts: status.Facts) -> str:
    """The memory dumps of a device not connected, as its last connection counted them."""
    if facts.dumps_at_last is None:
        return _("Not known")
    count, size = facts.dumps_at_last
    # TRANSLATORS: in the subtitle of "Memory Dumps" of a device not connected: the dumps were
    # counted at its last connection, e.g. "6 dumps · 54.0 MB · at the last connection"
    when = _("at the last connection")
    if not count:
        return caption(pgettext("dumps", "None"), when)
    return caption(ngettext("{count} dump", "{count} dumps", count).format(count=count),
                   human_size(size), when)


def _report(win, sec) -> None:
    # TRANSLATORS: group of the page Device Status
    g = sec.group(_("Report a Problem"))
    # TRANSLATORS: row of the page Device Status: the report for openExpress's issues
    row = Adw.ActionRow(title=_("Report to openExpress"), subtitle=_(
        "The device, the system and the recent crashes; no serial number, Unit ID or e-mail"))
    # TRANSLATORS: button that opens the report of the device (a dialog with more to do)
    row.add_suffix(win._button(_("Report…"), lambda: win.show_report()))
    g.add(row)


# --- a device not connected: the page from its records ----------------------------------------


@dataclass
class KeptStatus:
    """The page Device Status of a device not connected (win.kept_status): its unit ID, the
    facts its records give (status.read_kept), its page once shown, and what render_history
    keeps of the page (as the window keeps it of the open device's)."""

    unit: int
    facts: status.Facts
    page: Adw.NavigationPage | None = None
    status_facts: status.Facts | None = None
    status_show_all: bool = False
    status_history: Adw.PreferencesGroup | None = None
    status_history_rows: list = field(default_factory=list)


def _as_of(facts: status.Facts) -> str:
    """When the records of a device not connected are from: its last connection."""
    if facts.counted_at is None:
        return ""
    # TRANSLATORS: in the subtitle of the row "Device Status" of a device not connected: when
    # its records are from (its last connection), e.g. "as of 2026-11-09 21:52"
    return _("as of {when}").format(when=full_time(facts.counted_at))


def kept_status_row(win, unit_id: int) -> None:
    """The row Device Status of the view of a device not connected: its records read in the
    background (status.read_kept), then the row shown with their summary and when they are
    from; hidden without records."""
    kept = win.kept_status
    if kept is None or kept.unit != unit_id:
        win.kept_status = None
    win.remembered.show_status_row(None)

    def done(facts):
        entry = win._remembered_entry
        if facts is None or getattr(entry, "unit_id", None) != unit_id:
            return  # no records, or another device shown meanwhile
        kept = win.kept_status
        if kept is not None and kept.unit == unit_id:
            # Its page may be shown: it stays the view of this device (navigation, Refresh,
            # the window folding), with the records read now for its next showing.
            kept.facts = facts
        else:
            win.kept_status = KeptStatus(unit_id, facts)
        title = words.status_summary(facts.crashes, facts.errors_known, facts.free,
                                     facts.capacity, datetime.now(), facts.has_diagnostics)[1]
        win.remembered.show_status_row(caption(title, _as_of(facts)))

    win.in_background(lambda: status.read_kept(unit_id), done)


def kept_page_closed(win, page) -> None:
    """Back from the page Device Status of a device not connected: the page let go, with what
    was kept of it (built anew when its row is chosen again)."""
    kept = win.kept_status
    if kept is not None and kept.page is not None and page is kept.page:
        kept.page = None
        kept.status_history = None
        kept.status_history_rows = []


def show_kept_status(win) -> None:
    """The page Device Status of the device not connected (its row), above its view: built
    from its records, with a banner that says so; what needs the device left out (_build)."""
    kept = win.kept_status
    if kept is None:
        return
    facts = kept.facts
    sec = Section("kept-status", pgettext("tab", "Device Status"), PAGE_ICON, "")
    sec.show(_build(win, sec, facts, kept, unit=kept.unit) or "content")
    if facts.counted_at is not None:
        # TRANSLATORS: banner on top of the page Device Status of a device not connected: what
        # it shows is from its records, as of its last connection
        title = _("Device not connected\u00a0– as of {when}; connect it to free up space or "
                  "copy its logs").format(when=full_time(facts.counted_at))
    else:
        # TRANSLATORS: banner on top of the page Device Status of a device not connected whose
        # last connection is not known
        title = _("Device not connected\u00a0– connect it to free up space or copy its logs")
    sec.banner.set_title(title)
    sec.banner.set_revealed(True)
    view = Adw.ToolbarView(content=sec.stack)
    view.add_top_bar(Adw.HeaderBar())
    kept.page = Adw.NavigationPage(title=pgettext("tab", "Device Status"), tag="kept-status",
                                   child=view)
    log.info("status page: shown from the records of a device not connected")
    win.navigation.push(kept.page)


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
