"""The bar at the bottom of a device's view (the progress of a task, Stop Waiting) keeps to the
column of the page above it: its words and its bar begin at the left edge of the groups, Stop
Waiting ends at their right edge, at every width of the window."""
import time

import pytest

pytestmark = pytest.mark.needs_display


def _bounds(widget, window):
    ok, rect = widget.compute_bounds(window)
    assert ok
    return round(rect.get_x()), round(rect.get_x() + rect.get_width())


@pytest.mark.parametrize("width", [1000, 750, 600, 420, 360])
def test_the_bar_keeps_to_the_column_of_the_page(width):
    from gi.repository import Adw, GLib, Gtk

    from openexpress.gui.window_tasks import bottom_bar

    spinner = Adw.Spinner() if hasattr(Adw, "Spinner") else Gtk.Spinner(spinning=True)
    spinner.set_visible(False)  # the bar shown: the spinner is gone
    label = Gtk.Label(label="Waiting 29 s for Garmin Connect…", xalign=0, ellipsize=3,
                      hexpand=True)
    progress = Gtk.ProgressBar(hexpand=True, fraction=0.4)
    words = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, hexpand=True)
    words.append(label)
    words.append(progress)
    stop = Gtk.Button(label="Stop Waiting", valign=Gtk.Align.CENTER)
    bar = bottom_bar(spinner, words, stop)
    bar.set_reveal_child(True)
    page = Adw.PreferencesPage()
    group = Adw.PreferencesGroup(title="Storage")
    group.add(Adw.ActionRow(title="Device storage"))
    page.add(group)
    view = Adw.ToolbarView(content=page)
    view.add_bottom_bar(bar)
    window = Gtk.Window(default_width=width, default_height=400, child=view)
    window.present()
    try:
        context, start = GLib.MainContext.default(), time.monotonic()
        while (window.get_width() != width or not stop.get_width()) \
                and time.monotonic() - start < 5:
            context.iteration(False)
        for _ in range(20):
            context.iteration(False)
        left, right = _bounds(group, window)
        assert abs(_bounds(label, window)[0] - left) <= 1
        assert abs(_bounds(progress, window)[0] - left) <= 1
        assert abs(_bounds(stop, window)[1] - right) <= 1
        assert _bounds(progress, window)[1] <= _bounds(stop, window)[0]
    finally:
        window.destroy()
