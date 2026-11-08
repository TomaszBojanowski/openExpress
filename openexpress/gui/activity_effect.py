"""The training effect of an activity's page, under its heart rate zones, as its device worked
it out (fit.ActivityData): the aerobic and the anaerobic effect, each its value with the word of
the public scale (gui/words.py) and a bar over 0-5 in the desktop's accent colour, and the
training load as a whole number. A row the file does not give is left out; with none of them, no
section.
Has: no attribute of the window; the list keeps its rows."""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, Gtk  # noqa: E402 - imports must follow gi.require_version

from openexpress.gui.activity_colours import accent_colour  # noqa: E402
from openexpress.gui.activity_zones import ZoneBar  # noqa: E402
from openexpress.gui.words import (  # noqa: E402
    effect_name,
    effect_text,
    effect_title,
    training_load_name,
    training_load_text,
)

TOP = 5.0  # the top of the scale of a training effect


class EffectBar(ZoneBar):
    """A training effect over the scale, in the accent colour as it is when drawn."""

    __gtype_name__ = "OpenExpressEffectBar"

    def __init__(self, value: float):
        super().__init__(value / TOP, "")

    def fill(self) -> Gdk.RGBA:
        return accent_colour()


class EffectRow(Gtk.Box):
    """A row: its name, its value and, for an effect, its bar under them."""

    __gtype_name__ = "OpenExpressEffectRow"

    def __init__(self, name: str, value_text: str, bar: EffectBar | None = None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=10,
                         margin_bottom=10, margin_start=12, margin_end=12)
        line = Gtk.Box(spacing=10)
        line.append(Gtk.Label(label=name, xalign=0, hexpand=True, wrap=True))
        value = Gtk.Label(label=value_text, xalign=1)
        value.add_css_class("numeric")
        line.append(value)
        self.append(line)
        if bar is not None:
            self.append(bar)


class EffectList(Gtk.Box):
    """The section of the training effect: its title and the rows the activity gives."""

    __gtype_name__ = "OpenExpressEffectList"

    def __init__(self, data):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        group = Adw.PreferencesGroup(title=effect_title())
        listed = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        listed.add_css_class("boxed-list")
        self.rows = [EffectRow(effect_name(kind), effect_text(value), EffectBar(value))
                     for kind, value in (("aerobic", data.aerobic_effect),
                                         ("anaerobic", data.anaerobic_effect))
                     if value is not None]
        if data.training_load is not None:
            self.rows.append(EffectRow(training_load_name(),
                                       training_load_text(data.training_load)))
        for row in self.rows:
            listed.append(Gtk.ListBoxRow(child=row, activatable=False))
        group.add(listed)
        self.append(group)


def has_effect(data) -> bool:
    """Whether the activity gives its training effect or load."""
    return any(v is not None for v in (data.aerobic_effect, data.anaerobic_effect,
                                       data.training_load))
