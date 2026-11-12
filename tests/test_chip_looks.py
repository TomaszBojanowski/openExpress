"""Tests that the grey chips of a tile ("Primary", "USB drive", "Not on your account", "Never
synced", "Synced") stand out from the grey line under them in the dark and the light theme,
though libadwaita 1.7 dims their style class "dimmed" too (opacity 0.55); and that every chip
has one shape and size, made by one function. Made-up words."""

from pathlib import Path

import pytest


def _drawn(window):
    """The window as it is drawn (the Cairo renderer, as on any display): (RGBA bytes, width)."""
    from gi.repository import Gdk, Graphene, Gsk, Gtk

    width, height = window.get_width(), window.get_height()
    snapshot = Gtk.Snapshot()
    Gtk.WidgetPaintable.new(window).snapshot(snapshot, width, height)
    renderer = Gsk.CairoRenderer()
    renderer.realize_for_display(Gdk.Display.get_default())
    try:
        texture = renderer.render_texture(snapshot.to_node(),
                                          Graphene.Rect().init(0, 0, width, height))
    finally:
        renderer.unrealize()
    downloader = Gdk.TextureDownloader.new(texture)
    downloader.set_format(Gdk.MemoryFormat.R8G8B8A8)
    data, stride = downloader.download_bytes()
    return data.get_data(), stride


def _lightness(raw, stride, x, y):
    i = y * stride + x * 4
    return sum(raw[i:i + 3]) / 3


def _ink(raw, stride, widget, window, ground):
    """How far the most visible point of `widget` is from the tile's own colour `ground`."""
    ok, bounds = widget.compute_bounds(window)
    assert ok
    x0, y0 = int(bounds.get_x()), int(bounds.get_y())
    x1, y1 = x0 + int(bounds.get_width()), y0 + int(bounds.get_height())
    return max(abs(_lightness(raw, stride, x, y) - ground)
               for y in range(y0, y1) for x in range(x0, x1))


@pytest.mark.needs_display
@pytest.mark.filterwarnings("ignore:'asyncio.*:DeprecationWarning")
@pytest.mark.parametrize("scheme", ["dark", "light"])
def test_the_words_of_a_grey_chip_stand_out_from_the_line_under_them(scheme):
    from gi.repository import Adw, Gdk, GLib, Gtk

    from openexpress.gui import start

    start.load_css(Gdk.Display.get_default())
    manager = Adw.StyleManager.get_default()
    before = manager.get_color_scheme()
    manager.set_color_scheme(Adw.ColorScheme.FORCE_DARK if scheme == "dark"
                             else Adw.ColorScheme.FORCE_LIGHT)
    tile = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, css_classes=["card"])
    inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_top=24,
                    margin_bottom=24, margin_start=24, margin_end=24)
    row = start.chips_row([("Primary", "dimmed", None)])
    under = Gtk.Label(label="Primary", css_classes=["dim-label", "caption"])  # as on a tile
    inner.append(row)
    inner.append(under)
    tile.append(inner)
    window = Gtk.Window(child=tile)
    try:
        window.present()
        context = GLib.MainContext.default()
        for _ in range(100):
            context.iteration(False)
        raw, stride = _drawn(window)
        ok, place = tile.compute_bounds(window)
        assert ok
        ground = _lightness(raw, stride, int(place.get_x()) + 8, int(place.get_y()) + 8)
        chip = _ink(raw, stride, row, window, ground)
        line = _ink(raw, stride, under, window, ground)
        assert chip > 1.2 * line, (chip, line)
    finally:
        window.destroy()
        manager.set_color_scheme(before)


# Every chip of the window has the class "oe-chip" (the tiles, the rows of the list view, the
# chip of an update on the device page) and one shape: its font size, padding and rounding
# are of that class alone; a style ("warning", "accent", "dimmed") gives only its colours.
SHAPE = {"padding": "1px 11px", "border-radius": "999px", "font-size": "0.9em",
         "font-weight": "600"}


def _rules(css):
    """{selector: {property: value}} of a style sheet without nesting."""
    import re

    css = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    return {selector.strip(): dict((name.strip(), value.strip()) for name, value in
                                   (line.split(":", 1) for line in body.split(";")
                                    if ":" in line))
            for selector, body in re.findall(r"([^{}]+)\{([^}]*)\}", css)}


def test_every_chip_has_one_shape():
    from openexpress.gui import start

    rules = {k: v for k, v in _rules(start.CSS).items() if k.startswith("label.oe-chip")}
    assert {k: v for k, v in rules["label.oe-chip"].items() if k in SHAPE} == SHAPE
    for selector, properties in rules.items():
        if selector != "label.oe-chip":
            assert not set(properties) & set(SHAPE), (selector, properties)  # colours only
    assert set(rules) == {"label.oe-chip", "label.oe-chip.accent", "label.oe-chip.warning",
                          "label.oe-chip.dimmed"}


@pytest.mark.needs_display
def test_chips_of_every_style_have_one_size():
    """The same words make a chip of the same width and height, whatever its colours: a row
    of chips keeps one height."""
    from gi.repository import Gdk, Gtk

    from openexpress.gui import start
    from openexpress.gui.window_tasks import chip

    start.load_css(Gdk.Display.get_default())
    sizes = set()
    for style in ("warning", "accent", "dimmed"):
        label = chip("Primary", style)
        sizes.add((label.measure(Gtk.Orientation.HORIZONTAL, -1)[1],
                   label.measure(Gtk.Orientation.VERTICAL, -1)[1]))
    assert len(sizes) == 1, sizes


def _gui_files_saying(words: str) -> list[str]:
    """The modules of the window other than window_tasks whose code holds `words`."""
    from openexpress import gui

    folder = Path(gui.__file__).parent
    return sorted(p.name for p in folder.glob("*.py")
                  if p.name != "window_tasks.py" and words in p.read_text(encoding="utf-8"))


def test_every_chip_is_made_by_one_function():
    """Only window_tasks.chip gives a label the class oe-chip (start.CSS gives it its shape),
    and only window_tasks asks whether libadwaita has Adw.WrapBox: the lines of a name and its
    chips are its wrapping_line."""
    assert _gui_files_saying('"oe-chip"') == []
    assert _gui_files_saying("WrapBox") == []


@pytest.mark.needs_display
def test_a_chip_has_its_words_and_style():
    from openexpress.gui.window_tasks import chip

    made = chip("Made-up", "accent", tooltip_text="Made-up words")
    assert (made.get_label(), sorted(made.get_css_classes()), made.get_tooltip_text()) == (
        "Made-up", ["accent", "oe-chip"], "Made-up words")
    assert chip("").get_css_classes() == ["oe-chip"]  # no colour: nothing to see


@pytest.mark.needs_display
@pytest.mark.parametrize("wraps", [True, False], ids=["libadwaita-1.7", "older"])
def test_a_line_of_a_name_and_its_chips(wraps, in_the_window):
    """With Adw.WrapBox (libadwaita 1.7) what does not fit goes on to the next line, 4 px
    below; before it one line (a box). Aligned as asked."""
    from gi.repository import Gtk

    from openexpress.gui import window_tasks

    if wraps and window_tasks.WRAP_BOX is None:
        pytest.skip("this libadwaita has no Adw.WrapBox")
    if not wraps:
        in_the_window("WRAP_BOX", None)
    line = window_tasks.wrapping_line(6, Gtk.Align.CENTER)
    assert line.get_halign() == Gtk.Align.CENTER
    if wraps:
        assert (line.get_child_spacing(), line.get_line_spacing(), line.get_align()) == (6, 4, 0.5)
    else:
        assert isinstance(line, Gtk.Box) and line.get_spacing() == 6
