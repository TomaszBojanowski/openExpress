"""The Activities page while the activity journal is kept (after the yes, for a device on the
account the window knows of): what is new since the device's connection before this one
(since_last.py). On top how many and since when, with the last upload to Garmin Connect; the
numbers of the new ones in a card (distance, time, ascent, how many); the trivia; a tile for
each of the newest six, with the shape of its track in the colour of its sport (gui/sports.py)
or, without a track, its sport's symbol; or, none new, the newest three under "Latest". At the
bottom the earlier ones still on the device and Show All, which pushes the page as it was
(gui/activities_page.py) above it. While the journal reads the device, its progress instead
(activities_journal.read_journal). The words are in gui/words.py; units are the device's.
Has: the bar of the journal's progress on the page (activities_progress)."""
from __future__ import annotations

import logging
import re

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, GLib, Gtk  # noqa: E402 - imports must follow gi.require_version

from openexpress import activity_journal, last_sync, service, since_last  # noqa: E402
from openexpress.activities import Source, kind_name  # noqa: E402
from openexpress.gui import sports, words  # noqa: E402
from openexpress.gui.palette import rgba  # noqa: E402
from openexpress.i18n import _, caption, pgettext  # noqa: E402
from openexpress.paths import APP_ID  # noqa: E402

log = logging.getLogger(__name__)

ALL_TAG = "activities-all"  # the page Show All pushes
SHAPE_HEIGHT = 96  # pixels of a tile's shape
SHAPE_PADDING = 14
SHAPE_LINE = 3.0
SYMBOL_SIZE = 48  # pixels of the sport's symbol on a tile without a track
TRIVIA_ICONS = {"climbed": f"{APP_ID}-activity-elevation-symbolic",
                "distance": f"{APP_ID}-activity-distance-symbolic"}
# The symbol of each group of sports on a tile without a track (the program's own, in data/).
SYMBOLS = {group: f"{APP_ID}-activity-sport-{group}-symbolic" for group in sports.COLOURS}
SYMBOLS["foot"] = f"{APP_ID}-activities-symbolic"  # the page's runner
# The day of a tile at the top of its title, its smaller letters this far lower: a title in two
# lines keeps the day beside its first line.
DAY_DROP = 2

CSS = """
.oe-since-number {
  font-size: 2.2em;
  font-weight: 800;
}
.oe-trivia {
  border-radius: 12px;
  padding: 12px 16px;
  background-color: alpha(@accent_bg_color, 0.12);
}
.oe-trivia image {
  color: @accent_color;
}
.oe-tile-shape {
  background-color: alpha(currentColor, 0.05);
  border-top-left-radius: 12px;
  border-top-right-radius: 12px;
}
"""


def shown(win) -> bool:
    """Whether the Activities page of the open device is this one: the journal kept (the yes),
    not a device on another Garmin Connect account."""
    s = win.session
    return (s is not None and activity_journal.answered() is True
            and not activity_journal.on_another_account(getattr(s, "ownership_error", None)))


def journal_read(win) -> bool:
    """Whether the journal's reading of this connection is done (also when it failed): until
    then the page shows its progress."""
    return bool(getattr(win.session, "journal_done", False))


def on_device(found) -> set[str] | None:
    """The names (casefold) of the activity files on the device, as the page listed them."""
    if found is None or found.listing is None:
        return None
    return {a.name.casefold() for a in found.listing if a.source is Source.DEVICE}


def show(win, sec, found, error: str | None = None) -> None:
    """The page in the section `sec` (cleared): the journal's progress while it reads, else
    what is new since the connection before this one. `found`: the listing of the device
    (activities.DeviceActivities; with `error`, its reading failed: the page says so on top,
    with Try Again, and counts nothing on the device)."""
    s = win.session
    win.activities_progress = None
    if not journal_read(win):
        _reading(win, sec)
        return
    view = since_last.of_device(s.device, s.storage, None if error else on_device(found))
    listed = error is None and found is not None and bool(found.listing)
    if error is not None:
        _error(win, sec, error)
        if view.count == 0:
            return  # nothing to show beneath why
    if view.count == 0 and not listed:
        from openexpress.gui.activities_page import no_activities  # it imports this module

        no_activities(sec)
        return
    _heading(sec, view, s)
    if view.new:
        _numbers(sec, view)
        _trivia(sec, view)
        _tiles(sec, view, view.new[:since_last.NEW_TILES], None)
    elif view.latest:
        _tiles(sec, view, view.latest, words.latest_text())
    if listed:
        _footer(win, sec, view)
    log.info("activities page: %d new, %d with a tile, %d earlier on the device",
             len(view.new), min(len(view.new), since_last.NEW_TILES) or len(view.latest),
             view.earlier)


def _reading(win, sec) -> None:
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_top=24,
                  margin_bottom=24)
    words_ = Gtk.Label(label=words.reading_journal_text(), wrap=True, xalign=0)
    words_.add_css_class("title-3")
    box.append(words_)
    bar = Gtk.ProgressBar()
    done, of = getattr(win.session, "journal_progress", None) or (0, 0)
    bar.set_fraction(done / of if of else 0.0)
    box.append(bar)
    win.activities_progress = bar
    sec.group().add(box)


def progress(win, done: int, of: int) -> None:
    """The journal's progress on the page, when it shows it."""
    bar = win.activities_progress
    if bar is not None:
        bar.set_fraction(done / of if of else 0.0)


def _error(win, sec, text: str) -> None:
    """The listing of the device failed: why, and Try Again (the page read again)."""
    box = Gtk.Box(spacing=12)
    said = Gtk.Label(label=text, wrap=True, xalign=0, hexpand=True)
    said.add_css_class("warning")
    box.append(said)
    again = Gtk.Button(label=_("Try Again"), valign=Gtk.Align.CENTER)
    again.add_css_class("pill")
    again.connect("clicked", lambda _button: win.load_section("activities", force=True))
    box.append(again)
    sec.group().add(box)


def _upload_text(view, s) -> str | None:
    """When the device's last sync uploaded, if it did after the connection before this one
    and went without an error."""
    record = service.load_last_sync(s.device.unit_id, getattr(s.storage, "test_drive", False))
    if record is None:
        return None
    told = last_sync.describe(record)
    if view.since is not None and told.when < view.since:
        return None
    said = told.uploaded_text()
    # A number kept with the word after it when the line wraps ("6 min ago").
    return re.sub(r"(\d) ", "\\1\u00a0", said) if said else None


def _heading(sec, view, s) -> None:
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    if view.started:
        title, under = words.journal_started_text(), (
            words.journal_read_text(view.count, view.oldest) if view.oldest is not None
            else "")
    else:
        title = words.new_activities_text(len(view.new))
        under = caption(words.since_text(view.since), _upload_text(view, s) or "")
    heading = Gtk.Label(label=title, wrap=True, xalign=0)
    heading.add_css_class("title-1")
    box.append(heading)
    if under:
        said = Gtk.Label(label=under, wrap=True, xalign=0)
        said.add_css_class("dim-label")
        box.append(said)
    if view.gap:
        gap = Gtk.Label(label=words.gap_text(), wrap=True, xalign=0)
        gap.add_css_class("dim-label")
        box.append(gap)
    sec.group().add(box)


def _markup(template: str, values: dict[str, str]) -> str:
    """A template of a number and its unit as markup: the numbers large, the rest as it is."""
    out = []
    for part in re.split(r"(\{\w+\})", template):
        name = re.fullmatch(r"\{(\w+)\}", part)
        if name:
            value = GLib.markup_escape_text(values[name[1]])
            out.append(f'<span font_features="tnum" size="180%" weight="heavy">{value}</span>')
        elif part:
            out.append(GLib.markup_escape_text(part))
    return "".join(out)


def _number(markup: str, name: str) -> Gtk.Widget:
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, margin_top=12,
                  margin_bottom=12, margin_start=12, margin_end=12)
    value = Gtk.Label(use_markup=True, label=markup, xalign=0, wrap=True)
    box.append(value)
    named = Gtk.Label(label=name, xalign=0, wrap=True)
    named.add_css_class("dim-label")
    box.append(named)
    return box


def _numbers(sec, view) -> None:
    """The card of the new activities' numbers: distance, time, ascent and how many (four in a
    row, two by two in a narrow window)."""
    t = since_last.totals(view.new)
    flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, min_children_per_line=2,
                       max_children_per_line=4)
    template, number = words.since_distance(t.distance, view.units.miles)
    flow.append(_number(_markup(template, {"distance": number, "value": number}),
                        pgettext("activity tile", "Distance")))
    flow.append(_number(_markup("{time}", {"time": words.hours_and_minutes(t.time)}),
                        pgettext("activity tile", "Time")))
    template, number = words.since_height(t.ascent, view.units.feet)
    flow.append(_number(_markup(template, {"metres": number, "value": number}),
                        pgettext("activity tile", "Total Ascent")))
    flow.append(_number(_markup("{count}", {"count": str(t.count)}),
                        pgettext("tab", "Activities")))
    for child in _children(flow):
        child.set_focusable(False)
    card = Gtk.Box()
    card.add_css_class("card")
    flow.set_hexpand(True)
    card.append(flow)
    sec.group().add(card)


def _children(widget):
    child = widget.get_first_child()
    while child is not None:
        yield child
        child = child.get_next_sibling()


def _trivia(sec, view) -> None:
    found = since_last.trivia(view.new)
    if found is None:
        return
    box = Gtk.Box(spacing=12)
    box.add_css_class("oe-trivia")
    box.append(Gtk.Image(icon_name=TRIVIA_ICONS[found.kind], pixel_size=24,
                         valign=Gtk.Align.CENTER,
                         accessible_role=Gtk.AccessibleRole.PRESENTATION))
    lead = GLib.markup_escape_text(words.trivia_lead(found.kind, found.metres, view.units))
    thing = GLib.markup_escape_text(words.trivia_object(found.thing, found.times))
    said = Gtk.Label(use_markup=True, label=f"<b>{lead}</b> {thing}", wrap=True, xalign=0,
                     hexpand=True)
    box.append(said)
    sec.group().add(box)


def _draw_shape(_area, cr, width: int, height: int, points, colour) -> None:
    """The track's shape (0-1000 along its longer side, north up) in the middle of the area,
    as large as it fits, in the colour of its sport."""
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    spread_x, spread_y = max(xs) - min(xs), max(ys) - min(ys)
    room_x, room_y = width - 2 * SHAPE_PADDING, height - 2 * SHAPE_PADDING
    if room_x <= 0 or room_y <= 0:
        return
    scale = min(room_x / max(spread_x, 1), room_y / max(spread_y, 1))
    left = (width - spread_x * scale) / 2 - min(xs) * scale
    top = (height - spread_y * scale) / 2 - min(ys) * scale
    cr.set_source_rgba(colour.red, colour.green, colour.blue, colour.alpha)
    cr.set_line_width(SHAPE_LINE)
    cr.set_line_cap(1)  # round
    cr.set_line_join(1)  # round
    cr.move_to(left + points[0][0] * scale, top + points[0][1] * scale)
    for x, y in points[1:]:
        cr.line_to(left + x * scale, top + y * scale)
    cr.stroke()


def _shape(activity: dict) -> Gtk.Widget:
    """The top of a tile: the shape of the track, or the sport's symbol without one; in the
    colour of the sport's group, for the eye only."""
    group = sports.sport_group(activity.get("sport"))
    shape = activity.get("shape") if activity.get("has_track") else None
    points = shape.get("points") if isinstance(shape, dict) else None
    if points and len(points) >= 2:
        area = Gtk.DrawingArea(content_height=SHAPE_HEIGHT, hexpand=True,
                               accessible_role=Gtk.AccessibleRole.PRESENTATION)
        area.set_draw_func(_draw_shape, points, rgba(sports.COLOURS[group]))
        area.add_css_class("oe-tile-shape")
        return area
    box = Gtk.Box(height_request=SHAPE_HEIGHT, hexpand=True)
    box.add_css_class("oe-tile-shape")
    symbol = Gtk.Image(icon_name=SYMBOLS[group], pixel_size=SYMBOL_SIZE, hexpand=True,
                       halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER,
                       accessible_role=Gtk.AccessibleRole.PRESENTATION)
    symbol.add_css_class(f"oe-sport-{group}")
    box.append(symbol)
    return box


def _tile(view, activity: dict) -> Gtk.Widget:
    tile = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    tile.add_css_class("card")
    tile.append(_shape(activity))
    body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_top=10,
                   margin_bottom=12, margin_start=12, margin_end=12)
    top = Gtk.Box(spacing=8)
    # Short natural widths (max_width_chars): three tiles share a wide line, each label
    # wrapping in the width its tile gets.
    name = Gtk.Label(label=kind_name(activity.get("sport"), activity.get("sub_sport")),
                     wrap=True, xalign=0, hexpand=True, max_width_chars=6)
    name.add_css_class("heading")
    top.append(name)
    day = Gtk.Label(label=words.tile_day(since_last.local_day(activity)), xalign=1,
                    valign=Gtk.Align.START, margin_top=DAY_DROP)
    day.add_css_class("dim-label")
    day.add_css_class("caption")
    top.append(day)
    body.append(top)
    numbers = words.tile_caption(activity, view.units)
    if numbers:
        said = Gtk.Label(label=numbers, wrap=True, xalign=0, max_width_chars=10)
        said.add_css_class("dim-label")
        body.append(said)
    state = view.upload_state(activity)
    if state is not None:
        line = Gtk.Box(spacing=6)
        style = "success" if state == "uploaded" else "warning"
        icon = "object-select-symbolic" if state == "uploaded" else "dialog-warning-symbolic"
        image = Gtk.Image(icon_name=icon, accessible_role=Gtk.AccessibleRole.PRESENTATION)
        image.add_css_class(style)
        line.append(image)
        words_ = Gtk.Label(label=words.upload_state_text(state), wrap=True, xalign=0)
        words_.add_css_class(style)
        line.append(words_)
        body.append(line)
    tile.append(body)
    return tile


def _tiles(sec, view, activities: list[dict], title: str | None) -> None:
    """A tile for each of `activities`, newest first: three in a row in a wide window, one in
    a narrow one."""
    flow = Gtk.FlowBox(homogeneous=True, selection_mode=Gtk.SelectionMode.NONE,
                       min_children_per_line=1, max_children_per_line=3,
                       column_spacing=12, row_spacing=12)
    for a in activities:
        flow.append(_tile(view, a))
    for child in _children(flow):
        child.set_focusable(False)
    g = sec.group(title=GLib.markup_escape_text(title) if title else "")
    g.add(flow)


def _footer(win, sec, view) -> None:
    """How many earlier activities the device holds (none: not said), and Show All."""
    box = Gtk.Box(spacing=12)
    said = Gtk.Label(label=words.earlier_text(view.earlier) if view.earlier else "",
                     wrap=True, xalign=0, hexpand=True)
    said.add_css_class("dim-label")
    box.append(said)
    button = Gtk.Button(label=_("Show All"), valign=Gtk.Align.CENTER)
    button.add_css_class("flat")
    button.connect("clicked", lambda _button: show_all(win))
    box.append(button)
    sec.group().add(box)


# --- Show All ------------------------------------------------------------------------------------

class Sheet:
    """A page of groups like a section's content, for the page Show All pushes."""

    def __init__(self) -> None:
        self.page = Adw.PreferencesPage(vexpand=True)
        self.groups: list[Adw.PreferencesGroup] = []

    def group(self, title: str = "", description: str = "") -> Adw.PreferencesGroup:
        g = Adw.PreferencesGroup(title=title, description=description)
        self.page.add(g)
        self.groups.append(g)
        return g


def show_all(win) -> None:
    """The page as it was, with every activity of the device and its backups, pushed above the
    page (its back arrow returns), its first activities read in a task."""
    from openexpress import activities
    from openexpress.gui.activities_page import list_read, show_list

    found = win.activities
    if found is None or win.session is None:
        return
    if win.busy:
        win.toast(_("Another operation is in progress"))
        return
    sheet = Sheet()
    toolbar = Adw.ToolbarView(content=sheet.page)
    toolbar.add_top_bar(Adw.HeaderBar())
    page = Adw.NavigationPage(title=pgettext("tab", "Activities"), tag=ALL_TAG, child=toolbar)
    win.navigation.push(page)
    count = max(activities.PAGE, len(found.read))

    def done(read) -> None:
        if win.activities is not read or win.navigation.get_visible_page() is not page:
            return  # another device opened, or the page left meanwhile
        show_list(win, sheet, read)

    win.run_task(_("Reading Activities…"), lambda: list_read(found, count, win.progress), done,
                 device=True, in_page=True,
                 # TRANSLATORS: heading of the error when more activities could not be read
                 failed_title=_("Activities Not Read"))
