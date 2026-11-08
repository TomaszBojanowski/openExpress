"""The details of an activity's page, at its end under the training effect: a row "Details",
closed each time the page opens, that opens to a row for each detail the file gives, in their
order (gui/words.py, activity_details): its name, which wraps in a narrow window, and its value
on one line.
Has: no attribute of the window; the list keeps its rows."""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gtk  # noqa: E402 - imports must follow gi.require_version

from openexpress.gui.words import details_title  # noqa: E402


class DetailRow(Adw.ActionRow):
    """A detail: its name as the row's title, its value at its end."""

    __gtype_name__ = "OpenExpressDetailRow"

    def __init__(self, name: str, value_text: str):
        super().__init__(title=name, use_markup=False, activatable=False)
        self.value = Gtk.Label(label=value_text, xalign=1)  # short: the name wraps instead
        self.value.add_css_class("numeric")
        self.add_suffix(self.value)


class DetailsList(Gtk.Box):
    """The closed row of the details and in it a row for each (name, value)."""

    __gtype_name__ = "OpenExpressDetailsList"

    def __init__(self, details: list[tuple[str, str]]):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        listed = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        listed.add_css_class("boxed-list")
        self.expander = Adw.ExpanderRow(title=details_title(), use_markup=False)
        self.rows = [DetailRow(name, value) for name, value in details]
        for row in self.rows:
            self.expander.add_row(row)
        listed.append(self.expander)
        self.append(listed)
