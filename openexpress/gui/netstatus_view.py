"""The state of Garmin's servers at the bottom of the window's side panel: a button with a
coloured dot and a word, which opens a popover in sections as GNOME's popover menus: the state,
a line for each service the program has asked so far, and Try Again when something is wrong
(netstatus says what to show; this only draws it). NetStatusPanel is a mixin of MainWindow
(gui/app.py), without __init__ of its own."""
from __future__ import annotations

import logging
import types

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")

from gi.repository import Gdk, Gio, Gtk  # noqa: E402 - imports must follow gi.require_version

from openexpress import netstatus  # noqa: E402
from openexpress.i18n import _  # noqa: E402
from openexpress.settings import Settings  # noqa: E402

log = logging.getLogger(__name__)

# The colour of the dot for each state of Garmin's servers (a word always goes with it).
NET_COLOUR = {
    netstatus.UNKNOWN: "dim-label",
    netstatus.ONLINE: "success",
    netstatus.OFFLINE: "error",
    netstatus.SERVER_PROBLEM: "error",
    netstatus.SIGN_IN_EXPIRED: "warning",
    netstatus.CLOCK_OFF: "warning",
}
# … and of each service in the popover (one not asked yet is not shown).
NET_ROW_COLOUR = {
    netstatus.OK: "success",
    netstatus.NETWORK: "error",
    netstatus.SERVER: "error",
    netstatus.AUTH: "warning",
    netstatus.CLOCK: "warning",
}
NET_COLOURS = set(NET_COLOUR.values()) | set(NET_ROW_COLOUR.values())
# The states in which the popover offers to try again.
NET_BAD = (netstatus.OFFLINE, netstatus.SERVER_PROBLEM, netstatus.SIGN_IN_EXPIRED,
           netstatus.CLOCK_OFF)
NET_POPOVER_WIDTH = 320  # opened upwards from the left edge of the button: inside the window
DOT = "\u25cf"  # ●


def _dot() -> Gtk.Label:
    dot = Gtk.Label(label=DOT)
    dot.update_property([Gtk.AccessibleProperty.LABEL], [""])  # the word beside it says it
    return dot


def _paint(dot, colour: str) -> None:
    for c in NET_COLOURS:
        dot.remove_css_class(c)
    dot.add_css_class(colour)


def _render_net_status_later(ref) -> bool:
    """On the GTK thread: draws the server state, if the window is still there."""
    win = ref()
    if win is not None:
        win._render_net_status()
    return False


def _retry_from_net_status(button) -> None:
    """ "Try Again" in the popover: closes it and refreshes the page shown, as its Refresh
    button does. The window comes from the button itself, so the handler holds no widget."""
    popover = button.get_ancestor(Gtk.Popover)
    if popover is not None:
        popover.popdown()
    refresh = getattr(button.get_root(), "_on_refresh", None)
    if refresh is not None:
        refresh()


def network_monitor():
    """The computer's network as GIO sees it (in the Flatpak through its portal)."""
    return Gio.NetworkMonitor.get_default()


def _network_changed(_monitor, available: bool) -> None:
    """The network changed: what counts for the state of Garmin's servers (netstatus). The
    handler holds no window."""
    log.info("network: changed (available: %s)", "yes" if available else "no")
    netstatus.STATUS.network_changed()


def stop_watching_network(win) -> None:
    """The window closes: the network monitor no longer calls it."""
    if win._net_monitor is not None:
        win._net_monitor.disconnect(win._net_monitor_handler)
    win._net_monitor = win._net_monitor_handler = None


def _render_net_status_on_show(popover) -> None:
    """When the popover opens, its times are told against now: an answer of another day gets
    its date. The window comes from the popover itself, so the handler holds no widget."""
    render = getattr(popover.get_root(), "_render_net_status", None)
    if render is not None:
        render()


class NetStatusPanel:
    """The server state panel of the window: built once (_build_net_status), drawn again on
    each answer of Garmin's servers (_render_net_status)."""

    def _watch_network(self) -> None:
        """Changes of the computer's network go to netstatus: after one, a request that got no
        answer before it no longer makes the state red."""
        self._net_monitor = network_monitor()
        self._net_monitor_handler = self._net_monitor.connect("network-changed",
                                                              _network_changed)

    def _build_net_status(self):
        """The state of Garmin's servers at the bottom of the side panel: a button with a
        coloured dot and a word that opens a popover with each service, upwards and from the
        left edge of the button, so that it stays inside the window."""
        self.net_icon = _dot()
        self.net_label = Gtk.Label(xalign=0, hexpand=True)
        self.net_label.add_css_class("caption")
        inner = Gtk.Box(spacing=6)
        inner.append(self.net_icon)
        inner.append(self.net_label)
        self.net_button = Gtk.MenuButton(child=inner, direction=Gtk.ArrowType.UP)
        self.net_button.add_css_class("flat")

        # What the state comes from, on the button rather than at the bottom of the popover.
        self.net_button.set_tooltip_text(_(netstatus.NOTE))

        # Sections as in GNOME's popover menus, a line between them: the state; the services;
        # Try Again, only when something is wrong.
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, margin_top=6, margin_bottom=6,
                      width_request=NET_POPOVER_WIDTH - 16)
        head = Gtk.Box(spacing=6, margin_start=12, margin_end=12, margin_top=6, margin_bottom=8)
        self.net_heading_dot = _dot()
        # Wrapped labels ask for the width of their whole text: a small limit keeps the
        # popover at its own width.
        self.net_heading = Gtk.Label(xalign=0, wrap=True, max_width_chars=20, hexpand=True)
        self.net_heading.add_css_class("heading")
        head.append(self.net_heading_dot)
        head.append(self.net_heading)
        box.append(head)
        box.append(Gtk.Separator(margin_start=6, margin_end=6))
        services = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, margin_top=4, margin_bottom=4)
        # TRANSLATORS: in the popover of the server state button, in place of the parts of
        # Garmin's servers, while this window has asked none of them (what its pages show may
        # be kept from earlier)
        self.net_none = Gtk.Label(label=_("Not checked yet in this window"), xalign=0, wrap=True,
                                  max_width_chars=24, margin_start=12, margin_end=12,
                                  margin_top=6, margin_bottom=6)
        self.net_none.add_css_class("dim-label")
        services.append(self.net_none)
        self.net_rows = {}
        for key in netstatus.SERVICES:
            # ● name                           5 min ago
            #   server error (HTTP 503)                     (a problem only)
            # Shown once the program has asked it. Plain labels: nothing in it takes the focus;
            # its host and its last good answer are in its tooltip.
            row = Gtk.Box(spacing=8, margin_start=12, margin_end=12, height_request=32,
                          visible=False)
            dot = _dot()
            dot.set_valign(Gtk.Align.CENTER)
            middle = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True,
                             valign=Gtk.Align.CENTER, margin_top=3, margin_bottom=3)
            name = Gtk.Label(xalign=0)
            state = Gtk.Label(xalign=0, visible=False)
            state.add_css_class("caption")
            middle.append(name)
            middle.append(state)
            when = Gtk.Label(xalign=1, valign=Gtk.Align.CENTER)
            when.add_css_class("dim-label")
            row.append(dot)
            row.append(middle)
            row.append(when)
            services.append(row)
            self.net_rows[key] = types.SimpleNamespace(row=row, dot=dot, name=name, state=state,
                                                       when=when)
        box.append(services)
        self.net_retry_line = Gtk.Separator(margin_start=6, margin_end=6, visible=False)
        box.append(self.net_retry_line)
        # The same as Refresh of the page shown: no request of its own to Garmin's servers. An
        # item of a menu, not a button: flat, its words at the start and not bold (the CSS).
        self.net_retry = Gtk.Button(label=_("Try Again"), margin_start=6, margin_end=6,
                                    margin_top=4, visible=False)
        self.net_retry.add_css_class("flat")
        self.net_retry.add_css_class("oe-menu-item")
        words = self.net_retry.get_child()
        words.set_xalign(0)
        words.set_margin_start(6)
        self.net_retry.connect("clicked", _retry_from_net_status)
        box.append(self.net_retry)
        popover = Gtk.Popover(child=box, has_arrow=False, position=Gtk.PositionType.TOP)
        # Centred over a rectangle as wide as the popover that starts at the button's left
        # edge: the popover goes up and to the right, never out of the side of the window.
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = 0, 0, NET_POPOVER_WIDTH, 1
        popover.set_pointing_to(rect)
        popover.connect("show", _render_net_status_on_show)
        self.net_button.set_popover(popover)
        bar = Gtk.Box(margin_top=2, margin_bottom=4, margin_start=6, margin_end=6)
        bar.append(self.net_button)
        return bar

    def _render_net_status(self) -> None:
        """Shows the state of Garmin's servers on the button and in its window (open or
        not: the same widgets get the new texts, so an open window follows at once)."""
        # The dot, the heading and the rows from the same results: what counts since the
        # network last changed (Status.counted).
        last, answered = netstatus.STATUS.counted()
        state = netstatus.state_of(last, answered)
        session = self.session
        try:
            hosts = (session.client.settings if session is not None else Settings.load()).hosts
        except Exception:  # noqa: BLE001 - only for the names of the servers
            hosts = {}
        text, whole = netstatus.describe(last, answered, hosts)
        _paint(self.net_icon, NET_COLOUR[state])
        self.net_label.set_label(text)
        self.net_button.update_property(
            [Gtk.AccessibleProperty.LABEL, Gtk.AccessibleProperty.DESCRIPTION],
            [text, _("Show the state of Garmin's servers") + "\n" + whole])
        self.net_heading.set_label(netstatus.heading(state))
        _paint(self.net_heading_dot, NET_COLOUR[state])
        self.net_retry.set_visible(state in NET_BAD)
        self.net_retry_line.set_visible(state in NET_BAD)
        shown = 0
        for r in netstatus.rows(last, answered, hosts):
            w = self.net_rows[r.service]
            w.row.set_visible(r.kind is not None)  # a service not asked yet is not shown
            if r.kind is None:
                continue
            shown += 1
            w.name.set_label(r.name)
            _paint(w.dot, NET_ROW_COLOUR[r.kind])
            # The words only for a problem, under the name; a good answer has its dot and time.
            problem = r.kind != netstatus.OK
            w.state.set_label(r.outcome if problem else "")
            w.state.set_visible(problem)
            if problem:
                _paint(w.state, NET_ROW_COLOUR[r.kind])
            w.when.set_label(r.time)
            w.row.set_tooltip_text("\n".join(t for t in (r.host, r.answered) if t))
        self.net_none.set_visible(shown == 0)
