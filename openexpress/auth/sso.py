"""Garmin SSO login.

As in Garmin Express: Garmin's sign-in page opens in an embedded browser,
and the program watches the URLs the browser navigates to and extracts
the `ticket` and `service` parameters from them. The password is entered
only on Garmin's page; the program never sees it.

The log tells what the window runs on (WebKit, GTK, the graphics) and each step of the page
by its host only: the ticket is in the address, never in the log (issue #10).
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from openexpress import applog, i18n, paths
from openexpress.i18n import _
from openexpress.settings import SSO_CLIENT_ID, Settings

log = logging.getLogger(__name__)

# The variables of the environment that change how the window draws, told in the log when set.
RENDERING_ENV = ("GDK_BACKEND", "GDK_DEBUG", "GDK_DISABLE", "GSK_RENDERER",
                 "LIBGL_ALWAYS_SOFTWARE", "WEBKIT_DISABLE_COMPOSITING_MODE",
                 "WEBKIT_DISABLE_DMABUF_RENDERER", "WEBKIT_SKIA_ENABLE_CPU_RENDERING")
GPU_VENDORS = {"0x10de": "NVIDIA", "0x1002": "AMD", "0x8086": "Intel", "0x1af4": "virtio",
               "0x15ad": "VMware", "0x1234": "QEMU", "0x1414": "Microsoft"}
FLATPAK_INFO = Path("/.flatpak-info")
DRM = Path("/sys/class/drm")
# Signing in without the window: the address is opened in a web browser and pasted back.
MANUAL_LOGIN_FLATPAK = "flatpak run io.github.tomaszbojanowski.OpenExpress login --manual"
MANUAL_LOGIN_SOURCE = "openexpress login --manual"


@dataclass(frozen=True)
class ServiceTicket:
    ticket: str = field(repr=False)  # signs in to the account: kept out of repr()
    service: str


class LoginCancelled(Exception):
    pass


def sign_in_url(settings: Settings) -> str:
    return f"{settings.hosts['sso']}mobile/sso/{settings.locale}/sign-in?clientId={SSO_CLIENT_ID}"


def browser_languages() -> list[str]:
    """The languages for the sign-in browser (its Accept-Language), for Garmin's pages that do
    not read the language from the address: the one chosen for the program, else those of the
    system, without encodings and modifiers."""
    chosen = i18n.chosen_language()
    names = [i18n.garmin_locale(), chosen] if chosen else i18n.system_language_names()
    return list(dict.fromkeys(n for n in names
                              if n not in ("C", "POSIX") and not any(c in n for c in ".@")))


def ticket_from_url(url: str) -> ServiceTicket | None:
    """Extract the ticket from a URL (query or fragment). Returns None if absent."""
    parts = urlsplit(url)
    params = parse_qs(parts.query)
    for k, v in parse_qs(parts.fragment).items():
        params.setdefault(k, v)
    ticket = (params.get("ticket") or [""])[0]
    if not ticket.startswith("ST-"):
        return None
    service = (params.get("service") or [""])[0]
    if not service:
        service = _without_query(url)
    return ServiceTicket(ticket, service)


def _without_query(url: str) -> str:
    p = urlsplit(url)
    return f"{p.scheme}://{p.netloc}{p.path}"


# --- what the window runs on, and what happens in it: for the log --------------------------


def flatpak_runtime(info: Path = FLATPAK_INFO) -> str:
    """The runtime of the Flatpak the program runs in, e.g. "org.gnome.Platform/x86_64/50";
    "none" outside one."""
    try:
        text = info.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "none"
    found = re.search(r"^runtime=(?:runtime/)?(\S+)", text, re.MULTILINE)
    return found.group(1) if found else "none"


def gpu_vendors(drm: Path = DRM) -> str:
    """The makers of the graphics cards, by their PCI vendor ID ("0x10de NVIDIA"): "none"
    when /sys tells none."""
    seen: list[str] = []
    try:
        cards = sorted(p for p in drm.iterdir() if re.fullmatch(r"card\d+", p.name))
    except OSError:
        cards = []
    for card in cards:
        try:
            vendor = (card / "device" / "vendor").read_text().strip().lower()
        except OSError:
            continue
        if vendor not in seen:
            seen.append(vendor)
    return ", ".join(f"{v} {GPU_VENDORS[v]}" if v in GPU_VENDORS else v for v in seen) or "none"


def rendering_env() -> str:
    """The variables of RENDERING_ENV that are set, with their values: "none" when none is."""
    told = [f"{name}={os.environ[name][:40]}" for name in RENDERING_ENV if os.environ.get(name)]
    return " ".join(told) or "none"


def _nick(value) -> str:
    return getattr(value, "value_nick", None) or str(value)


def _host(url: str | None) -> str:
    """Only the host of an address: its path and query (the ticket) stay out of the log."""
    return (urlsplit(url).hostname if url else None) or "-"


def window_environment(win) -> str:
    """What the sign-in window runs on, for the log: the versions of WebKit and GTK, the
    display and the renderer (once it is realized), the session and the desktop, the runtime,
    the makers of the graphics cards, WebKit's hardware acceleration and the variables that
    change the drawing. Nothing of the user, no address."""
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("WebKit", "6.0")
    from gi.repository import Gtk, WebKit

    view = win.get_child()
    renderer = win.get_renderer()
    session = (f"{os.environ.get('XDG_SESSION_TYPE') or '?'}/"
               f"{os.environ.get('XDG_CURRENT_DESKTOP') or '?'}")
    webkit = (WebKit.get_major_version(), WebKit.get_minor_version(), WebKit.get_micro_version())
    return ", ".join((
        "WebKit {}.{}.{}".format(*webkit),
        f"GTK {Gtk.get_major_version()}.{Gtk.get_minor_version()}.{Gtk.get_micro_version()}",
        f"GDK {win.get_display().__gtype__.name}",
        f"GSK {renderer.__gtype__.name if renderer is not None else 'none'}",
        f"session {session}",
        f"runtime {flatpak_runtime()}",
        f"GPU {gpu_vendors()}",
        f"acceleration {_nick(view.get_settings().get_hardware_acceleration_policy())}",
        f"env {rendering_env()}"))


def _log_load(view, event) -> None:
    log.info("login: page %s (%s)", _nick(event), _host(view.get_uri()))


def _log_load_failed(_view, _event, uri: str | None, error) -> bool:
    # The message of the error may quote the address: only its domain and code.
    log.info("login: the page did not load (%s): %s %s", _host(uri),
             getattr(error, "domain", "?"), getattr(error, "code", "?"))
    return False  # WebKit's own error page


# --- the window is open: a mark that the next start reads if the program ended meanwhile ---


def sign_in_marker() -> Path:
    return paths.state_dir() / "sign-in-open"


def _mark_open() -> None:
    try:
        paths.make_dir(sign_in_marker().parent)
        sign_in_marker().touch()
    except OSError:
        pass


def _mark_closed() -> None:
    try:
        sign_in_marker().unlink(missing_ok=True)
    except OSError:
        pass


def note_unfinished_sign_in() -> None:
    """At the start: the last run ended while the sign-in window was open (it crashed, say),
    told once in the log, with whether crash.log has a trace from then."""
    marker = sign_in_marker()
    try:
        opened = marker.stat().st_mtime
    except OSError:
        return
    try:
        crashed = applog.crash_path().stat().st_mtime >= opened
    except OSError:
        crashed = False
    log.warning("login: the last run ended while the sign-in window was open; %s",
                "crash.log has its trace (report --log attaches it)" if crashed
                else "no crash was recorded")
    _mark_closed()


def _shown(win) -> None:
    try:
        told = window_environment(win)
    except Exception as e:  # noqa: BLE001 - a line of the log never stops the sign-in
        told = f"not known ({type(e).__name__})"
    log.info("login: sign-in window: %s", told)
    _mark_open()


# --- the page's web process ended: say so, offer to try again and the sign-in without it ---


def _page_stopped(view, reason, url: str) -> None:
    log.warning("login: the page's web process ended (%s)", _nick(reason))
    win = view.get_root()
    if win is not None and win.get_child() is view:
        win.set_child(_stopped_page(url, view))


def _stopped_page(url: str, view):
    """What the window shows instead of the page: what happened, the command that signs in
    without this window (for the Flatpak, or the version from source), and Try Again. It
    keeps the page (`view`) for Try Again, which puts it back."""
    from gi.repository import Gtk

    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, valign=Gtk.Align.CENTER,
                  margin_start=24, margin_end=24, margin_top=24, margin_bottom=24)
    box.append(Gtk.Image(icon_name="dialog-warning-symbolic", pixel_size=64))
    title = Gtk.Label(label=_("The sign-in page stopped working"), wrap=True)
    title.add_css_class("title-2")
    box.append(title)
    text = Gtk.Label(label=_("Try again, or sign in without this window: run this command in "
                             "a terminal, sign in in your web browser and paste the address "
                             "you end up on."),
                     wrap=True, justify=Gtk.Justification.CENTER, max_width_chars=50)
    text.add_css_class("dim-label")
    box.append(text)
    command = Gtk.Label(label=MANUAL_LOGIN_FLATPAK if paths.in_flatpak() else MANUAL_LOGIN_SOURCE,
                        selectable=True, wrap=True)
    command.add_css_class("monospace")
    box.append(command)
    again = Gtk.Button(label=_("Try Again"), halign=Gtk.Align.CENTER)
    again.add_css_class("suggested-action")
    again.add_css_class("pill")
    # The handler takes the page from the button: it holds no widget.
    again.connect("clicked", lambda button: _try_again(button, url))
    box.append(again)
    box.stopped_view = view
    return box


def _try_again(button, url: str) -> None:
    view = button.get_parent().stopped_view
    log.info("login: the sign-in page loaded again")
    button.get_root().set_child(view)
    view.load_uri(url)


def create_login_window(settings: Settings, on_ticket, application=None, parent=None):
    """GTK4 + WebKitGTK window with Garmin's sign-in page.

    `on_ticket(ServiceTicket | None)` is called exactly once: with the ticket
    after signing in, or with None when the user closes the window.
    """
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("WebKit", "6.0")
    from gi.repository import Gtk, WebKit

    done: list[bool] = []
    win = Gtk.Window(title=_("Sign in to Garmin"), default_width=520, default_height=760)
    if application is not None:
        win.set_application(application)
    if parent is not None:
        win.set_transient_for(parent)
        win.set_modal(True)
    languages = browser_languages()
    if languages:
        WebKit.WebContext.get_default().set_preferred_languages(languages)
    # Ephemeral session: no persistent cookies, every login starts fresh.
    view = WebKit.WebView(network_session=WebKit.NetworkSession.new_ephemeral())

    def finish(st: ServiceTicket | None) -> None:
        if not done:
            done.append(True)
            on_ticket(st)

    # The handlers take the view from the signal and the window from the view: one that held
    # either would keep both alive after the window is closed.
    def inspect(v, uri: str | None) -> bool:
        st = ticket_from_url(uri) if uri else None
        if st and not done:
            log.info("login: ticket received, the sign-in window closes")
            finish(st)
            root = v.get_root()
            if root is not None:
                root.close()
            return True
        return False

    def on_policy(v, decision, decision_type):
        if decision_type in (
            WebKit.PolicyDecisionType.NAVIGATION_ACTION,
            WebKit.PolicyDecisionType.NEW_WINDOW_ACTION,
        ) and inspect(v, decision.get_navigation_action().get_request().get_uri()):
            decision.ignore()
            return True
        return False

    def on_close(_win) -> bool:
        if not done:
            log.info("login: sign-in window closed by the user")
        finish(None)
        return False

    url = sign_in_url(settings)
    view.connect("decide-policy", on_policy)
    view.connect("load-changed", lambda v, *_: inspect(v, v.get_uri()))
    view.connect("notify::uri", lambda v, *_: inspect(v, v.get_uri()))
    view.connect("load-changed", _log_load)
    view.connect("load-failed", _log_load_failed)
    view.connect("web-process-terminated", lambda v, reason: _page_stopped(v, reason, url))
    win.connect("close-request", on_close)
    win.connect("realize", _shown)  # what it runs on, once its renderer is known
    win.connect("unrealize", lambda _w: _mark_closed())  # closed, or gone with the program
    win.set_child(view)  # before loading: a ticket found at once still has a window to close
    view.load_uri(url)
    return win


def login_with_webview(settings: Settings) -> ServiceTicket:
    """Standalone login window (for the CLI)."""
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk

    result: list[ServiceTicket | None] = []
    app = Gtk.Application(application_id="io.github.tomaszbojanowski.OpenExpress.Login")

    def on_activate(application):
        create_login_window(settings, result.append, application=application).present()

    app.connect("activate", on_activate)
    app.run([])
    if not result or result[0] is None:
        raise LoginCancelled(_("Login cancelled or no SSO ticket received."))
    return result[0]


def login_manual(settings: Settings) -> ServiceTicket:
    """Fallback mode: log in using a regular browser and paste the final URL."""
    print(_("1. Open this URL in your browser:"))
    print("   " + sign_in_url(settings))
    print(_("2. Sign in. Afterwards, copy the full URL from the browser's address bar\n"
            "   (it contains 'ticket=ST-...'). If the browser shows an error, "
            "the URL still works."))
    url = input(_("3. Paste the URL: ")).strip()
    st = ticket_from_url(url)
    if st is None:
        raise LoginCancelled(_("The given URL has no ticket=ST-... parameter"))
    return st
