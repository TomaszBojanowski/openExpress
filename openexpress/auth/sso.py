"""Garmin SSO login.

As in Garmin Express: Garmin's sign-in page opens in an embedded browser,
and the program watches the URLs the browser navigates to and extracts
the `ticket` and `service` parameters from them. The password is entered
only on Garmin's page; the program never sees it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlsplit

from openexpress.i18n import _
from openexpress.settings import SSO_CLIENT_ID, Settings


@dataclass(frozen=True)
class ServiceTicket:
    ticket: str = field(repr=False)  # signs in to the account: kept out of repr()
    service: str


class LoginCancelled(Exception):
    pass


def sign_in_url(settings: Settings) -> str:
    return f"{settings.hosts['sso']}mobile/sso/{settings.locale}/sign-in?clientId={SSO_CLIENT_ID}"


def ticket_from_url(url: str, default_service: str | None = None) -> ServiceTicket | None:
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
        service = default_service or urlunsplit_no_query(url)
    return ServiceTicket(ticket, service)


def urlunsplit_no_query(url: str) -> str:
    p = urlsplit(url)
    return f"{p.scheme}://{p.netloc}{p.path}"


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
    # Ephemeral session: no persistent cookies, every login starts fresh.
    view = WebKit.WebView(network_session=WebKit.NetworkSession.new_ephemeral())

    def finish(st: ServiceTicket | None) -> None:
        if not done:
            done.append(True)
            on_ticket(st)

    def inspect(uri: str | None) -> bool:
        st = ticket_from_url(uri) if uri else None
        if st and not done:
            finish(st)
            win.close()
            return True
        return False

    def on_policy(_view, decision, decision_type):
        if decision_type in (
            WebKit.PolicyDecisionType.NAVIGATION_ACTION,
            WebKit.PolicyDecisionType.NEW_WINDOW_ACTION,
        ) and inspect(decision.get_navigation_action().get_request().get_uri()):
            decision.ignore()
            return True
        return False

    view.connect("decide-policy", on_policy)
    view.connect("load-changed", lambda *_: inspect(view.get_uri()))
    view.connect("notify::uri", lambda *_: inspect(view.get_uri()))
    win.connect("close-request", lambda *_: (finish(None), False)[1])
    view.load_uri(sign_in_url(settings))
    win.set_child(view)
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
