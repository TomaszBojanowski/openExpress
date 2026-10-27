"""What the pages of the window share: today the widgets of a page (still_in_window, the
wrapping row wrapping_row with WRAP_BOX); analysis 17, stage 7, adds the running of a task
(run_task, the bar at the bottom) here.

Has: no attribute of the window.
Uses: nothing of the window."""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gtk  # noqa: E402 - imports must follow gi.require_version


def still_in_window(buttons: list) -> list:
    """The action buttons that are still in a window: a page that is rebuilt leaves its old
    buttons behind, and the list must not keep them and what they hold."""
    return [b for b in buttons if b.get_root() is not None]


# Adw.WrapBox is in libadwaita 1.7; with an older one (the window works from 1.5) a FlowBox,
# which puts the widgets in columns across its lines: good for a row of buttons.
WRAP_BOX = getattr(Adw, "WrapBox", None)


def wrapping_row(widgets, spacing: int, **props) -> Gtk.Widget:
    """The widgets side by side and centred, `spacing` apart; those that do not fit the width
    of the window (MIN_WINDOW at the narrowest) go on to the next line, centred too, `spacing`
    below."""
    if WRAP_BOX is not None:
        row = WRAP_BOX(child_spacing=spacing, line_spacing=spacing, align=0.5,
                       halign=Gtk.Align.CENTER, **props)
        for widget in widgets:
            row.append(widget)
        return row
    row = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=False,
                      column_spacing=spacing, row_spacing=spacing, min_children_per_line=1,
                      max_children_per_line=max(1, len(widgets)), halign=Gtk.Align.CENTER,
                      **props)
    row.add_css_class("oe-wrap")  # no room of its own around each widget (start.CSS)
    for widget in widgets:
        row.append(widget)
        place = widget.get_parent()  # the FlowBoxChild it is put in
        place.set_focusable(False)  # the widget itself takes the focus, as in a box
        place.set_halign(Gtk.Align.CENTER)
    return row
