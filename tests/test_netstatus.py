"""The state of Garmin's servers, from the answers to the program's own requests."""

import threading
import types

import pytest
import requests
from fakes import window as stand_in

from openexpress import download, netstatus
from openexpress.connect import ApiError, GarminClient
from openexpress.settings import ENVIRONMENTS, Settings

PROD, CHINA = ENVIRONMENTS["production"], ENVIRONMENTS["china"]
CONNECT_URL = "https://connectapi.garmin.com/device-service/deviceregistration/devices"
OMT_URL = "https://omt.garmin.com/api/maps/universal/update"


def answer(status, date=None):
    resp = requests.Response()
    resp.status_code, resp._content = status, b"{}"
    if date:
        resp.headers["Date"] = date
    return resp


@pytest.mark.parametrize("url,hosts,service", [
    (CONNECT_URL, PROD, netstatus.CONNECT),
    ("https://connectapi.garmin.com/oauth-service/oauth/preauthorized?ticket=x", PROD,
     netstatus.SIGN_IN),
    ("https://sso.garmin.com/sso/signin", PROD, netstatus.SIGN_IN),
    (OMT_URL, PROD, netstatus.OMT),
    ("https://omt.garmin.cn/api/x", CHINA, netstatus.OMT),
    ("https://omt.garmin.cn/api/x", PROD, None),     # another environment
    ("https://example.org/x", PROD, None),
])
def test_the_service_comes_from_the_host(url, hosts, service):
    assert netstatus.service_of(url, hosts) == service


@pytest.mark.parametrize("status,skew,kind", [
    (200, 0, netstatus.OK), (204, 0, netstatus.OK), (404, 0, netstatus.OK),
    (401, 0, netstatus.AUTH), (403, 0, netstatus.AUTH),
    (401, 2 * 3600, netstatus.CLOCK),
    (500, 0, netstatus.SERVER), (503, 0, netstatus.SERVER),
])
def test_how_an_answer_ended(status, skew, kind):
    assert netstatus.kind_of(status, skew) == kind


def noted(*steps):
    s = netstatus.Status()
    for service, status in steps:
        url = {netstatus.CONNECT: CONNECT_URL, netstatus.OMT: OMT_URL,
               netstatus.SIGN_IN: "https://sso.garmin.com/x"}[service]
        if status is None:
            s.note_failure(service, url)
        else:
            s.note_answer(service, url, status)
    return s.state()


@pytest.mark.parametrize("steps,state", [
    ((), netstatus.UNKNOWN),
    (((netstatus.CONNECT, 200), (netstatus.OMT, 200)), netstatus.ONLINE),
    (((netstatus.CONNECT, None), (netstatus.OMT, None)), netstatus.OFFLINE),
    (((netstatus.CONNECT, None),), netstatus.OFFLINE),
    (((netstatus.CONNECT, 200), (netstatus.OMT, None)), netstatus.OFFLINE),  # nothing answered since
    (((netstatus.OMT, 503),), netstatus.SERVER_PROBLEM),
    (((netstatus.CONNECT, 401), (netstatus.OMT, None)), netstatus.SIGN_IN_EXPIRED),
    (((netstatus.OMT, None), (netstatus.OMT, 200)), netstatus.ONLINE),  # back again
    (((netstatus.CONNECT, 401), (netstatus.CONNECT, 200)), netstatus.ONLINE),  # signed in
    (((netstatus.SIGN_IN, 401), (netstatus.CONNECT, 200)), netstatus.ONLINE),
    (((netstatus.CONNECT, 401), (netstatus.OMT, 200)), netstatus.SIGN_IN_EXPIRED),
])
def test_the_state_of_the_dot(steps, state):
    assert noted(*steps) == state


TEN = 1_800_000_000.0  # "10:00": only the order of the results counts
HOST = {netstatus.CONNECT: "connectapi.garmin.com", netstatus.OMT: "omt.garmin.com"}


def at_minutes(*steps):
    """Results noted at given minutes after TEN, in this order: (service, status, minute);
    status None is no connection."""
    s = netstatus.Status()
    for service, status, minute in steps:
        kind = netstatus.NETWORK if status is None else netstatus.kind_of(status)
        s.note(service, netstatus.Result(kind, TEN + minute * 60, HOST[service], status))
    return s


CONN, OMT = netstatus.CONNECT, netstatus.OMT


@pytest.mark.parametrize("steps,state", [
    pytest.param([(CONN, 200, 0), (OMT, 200, 1)], netstatus.ONLINE, id="a-all-well"),
    pytest.param([(CONN, 200, 0), (OMT, None, 5)], netstatus.OFFLINE, id="b-nothing-since"),
    pytest.param([(CONN, 200, 0), (OMT, 200, 1), (CONN, None, 5), (OMT, None, 6)],
                 netstatus.OFFLINE, id="c-both-lost"),
    pytest.param([(CONN, None, 0), (OMT, 200, 5)], netstatus.SERVER_PROBLEM, id="d-back-again"),
    pytest.param([(OMT, 503, 0), (CONN, 200, 5)], netstatus.SERVER_PROBLEM, id="e1-503-first"),
    pytest.param([(CONN, 200, 0), (OMT, 503, 5)], netstatus.SERVER_PROBLEM, id="e2-503-last"),
    pytest.param([(OMT, 503, 0), (CONN, None, 5)], netstatus.SERVER_PROBLEM, id="f-503-then-lost"),
    pytest.param([(OMT, 200, 0)], netstatus.ONLINE, id="g1-only-omt"),
    pytest.param([(OMT, None, 0)], netstatus.OFFLINE, id="g2-only-omt-lost"),
    pytest.param([(OMT, 200, 0), (OMT, None, 5)], netstatus.OFFLINE, id="g3-only-omt-then-lost"),
    pytest.param([(OMT, 503, 0)], netstatus.SERVER_PROBLEM, id="g4-only-omt-503"),
    pytest.param([(OMT, 200, 0), (CONN, None, 5), (OMT, 200, 6)], netstatus.SERVER_PROBLEM,
                 id="h-one-service-lost"),
])
def test_no_connection_is_what_came_after_the_last_good_answer(steps, state):
    assert at_minutes(*steps).state() == state


def test_nothing_answered_since_the_last_good_answer_reads_as_no_connection():
    last, answered = at_minutes((CONN, 200, 0), (OMT, None, 5)).snapshot()
    assert netstatus.heading(netstatus.state_of(last, answered)) == "No connection"
    assert netstatus.describe(last, answered, PROD, TEN + 6 * 60)[0] == "No connection"
    rows = {r.service: r for r in netstatus.rows(last, answered, PROD, TEN + 6 * 60)}
    assert (rows[CONN].outcome, rows[OMT].outcome) == ("answered", "no connection")


def test_a_wrong_clock_is_its_own_state():
    s = netstatus.Status()
    s.note_answer(netstatus.CONNECT, CONNECT_URL, 401, skew=3 * 3600)
    assert s.state() == netstatus.CLOCK_OFF
    last, _answered = s.snapshot()
    assert last[netstatus.CONNECT].minutes == 180


@pytest.mark.parametrize("status,skew", [(401, 0), (403, 0), (401, 2 * 3600)])
def test_a_refused_unsigned_request_is_an_answer(status, skew):
    assert netstatus.kind_of(status, skew, signed=False) == netstatus.OK


def test_a_refusal_of_the_update_server_is_no_sign_in_and_no_clock():
    s = netstatus.Status()
    s.note_answer(netstatus.CONNECT, CONNECT_URL, 200)
    s.note_answer(netstatus.OMT, OMT_URL, 403)
    assert s.state() == netstatus.ONLINE  # not netstatus.SIGN_IN_EXPIRED
    s.note_answer(netstatus.OMT, OMT_URL, 401, skew=3 * 3600)
    assert s.state() == netstatus.ONLINE  # not netstatus.CLOCK_OFF
    omt = next(r for r in netstatus.rows(*s.snapshot(), PROD) if r.service == netstatus.OMT)
    assert (omt.kind, omt.outcome) == (netstatus.OK, "answered")


def test_the_client_notes_a_refusal_of_the_update_server_as_an_answer(monkeypatch,
                                                                      network_state):
    monkeypatch.setattr(requests.Session, "request", lambda self, m, u, **kw: answer(403))
    GarminClient(Settings())._request("GET", OMT_URL, "x")
    assert network_state.state() == netstatus.ONLINE


@pytest.mark.parametrize("service,status,skew,state", [
    (netstatus.CONNECT, 401, 0, netstatus.SIGN_IN_EXPIRED),
    (netstatus.CONNECT, 403, 0, netstatus.SIGN_IN_EXPIRED),
    (netstatus.SIGN_IN, 401, 0, netstatus.SIGN_IN_EXPIRED),
    (netstatus.CONNECT, 401, 3 * 3600, netstatus.CLOCK_OFF),
])
def test_a_refusal_of_a_signed_request_still_tells_the_sign_in_or_the_clock(service, status,
                                                                           skew, state):
    s = netstatus.Status()
    s.note_answer(service, CONNECT_URL, status, skew=skew)
    assert s.state() == state


def test_the_client_notes_every_answer_and_failure(monkeypatch, network_state):
    replies = [answer(200), answer(503), requests.ConnectionError("down")]

    def request(self, method, url, **kw):
        r = replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    monkeypatch.setattr(requests.Session, "request", request)
    c = GarminClient(Settings())
    c._request("GET", CONNECT_URL, "x")
    c._request("GET", OMT_URL, "x")
    with pytest.raises(ApiError):
        c._request("GET", OMT_URL, "x")
    last, answered = network_state.snapshot()
    assert last[netstatus.CONNECT].kind == netstatus.OK
    assert last[netstatus.OMT].kind == netstatus.NETWORK
    assert netstatus.OMT not in answered  # the 503 was no good answer
    assert network_state.state() == netstatus.OFFLINE


def test_a_refused_signature_with_a_wrong_clock_is_noted_as_such(monkeypatch, network_state):
    monkeypatch.setattr(requests.Session, "request", lambda self, m, u, **kw: answer(
        401, "Mon, 01 Jan 2001 00:00:00 GMT"))
    GarminClient(Settings())._request("GET", CONNECT_URL, "x")
    assert network_state.state() == netstatus.CLOCK_OFF


def test_downloads_are_noted_and_a_refused_address_is_no_sign_in(tmp_path, network_state):
    class Http:
        def __init__(self, status):
            self.status = status

        def get(self, url, **kw):
            raise download.DownloadError("refused", self.status)

    for status, state in ((403, netstatus.ONLINE), (502, netstatus.SERVER_PROBLEM)):
        network_state.clear()
        with pytest.raises(download.DownloadError):
            download.fetch(["https://download.garmin.com/a.img"], tmp_path / "a", None, "0" * 32,
                           session=Http(status))
        assert network_state.state() == state


def test_the_connect_iq_store_is_noted_under_garmin_connect(monkeypatch, network_state):
    from openexpress import ciq

    client = GarminClient(Settings())
    client.require_verified = lambda unit_id: None
    client._sign = lambda method, url, *a, **kw: "OAuth x"
    monkeypatch.setattr(requests.Session, "request",
                        lambda self, m, u, **kw: (_ for _ in ()).throw(
                            requests.ConnectionError("down")))
    with pytest.raises(ApiError):  # the client's way, without the address
        ciq.StoreApi(client)._request("GET", "express/x", "x")
    assert network_state.snapshot()[0][netstatus.CONNECT].kind == netstatus.NETWORK


def test_a_store_refusal_with_a_wrong_clock_is_noted_as_such(monkeypatch, network_state):
    import email.utils
    import time

    from openexpress import ciq
    from openexpress.connect import ClockSkew

    client = GarminClient(Settings())
    client.require_verified = lambda unit_id: None
    client._sign = lambda method, url, *a, **kw: "OAuth x"
    two_hours_ago = email.utils.formatdate(time.time() - 2 * 3600, usegmt=True)
    monkeypatch.setattr(requests.Session, "request",
                        lambda self, m, u, **kw: answer(401, two_hours_ago))
    with pytest.raises(ClockSkew):
        ciq.StoreApi(client)._request("GET", "express/x", "x")
    assert network_state.state() == netstatus.CLOCK_OFF  # not netstatus.SIGN_IN_EXPIRED
    assert network_state.snapshot()[0][netstatus.CONNECT].minutes == 120


STATUS_URL = "https://connectapi.garmin.com/activity-service/activity/status/1"


def _polling_client():
    client = GarminClient(Settings())
    client.require_verified = lambda *a: None
    client._sign = lambda method, url, *a, **kw: "OAuth x"
    return client


def test_a_lost_connection_while_checking_an_upload_is_noted(monkeypatch, network_state):
    def get(self, url, **kw):
        raise requests.ConnectionError("down")

    monkeypatch.setattr(requests.Session, "get", get)
    assert _polling_client().poll_location(STATUS_URL, 1, timeout_s=5) == 0
    last = network_state.snapshot()[0].get(netstatus.CONNECT)
    assert last is not None and last.kind == netstatus.NETWORK
    assert network_state.state() == netstatus.OFFLINE


def test_the_answer_to_an_upload_check_is_noted(monkeypatch, network_state):
    monkeypatch.setattr(requests.Session, "get", lambda self, url, **kw: answer(201))
    assert _polling_client().poll_location(STATUS_URL, 1, timeout_s=5) == 201
    last = network_state.snapshot()[0].get(netstatus.CONNECT)
    assert last is not None and (last.kind, last.status) == (netstatus.OK, 201)


def test_noting_never_breaks_a_request(monkeypatch):
    def broken(*a, **kw):
        raise RuntimeError("bug")

    monkeypatch.setattr(netstatus.STATUS, "note_answer", broken)
    client = types.SimpleNamespace(
        http=types.SimpleNamespace(request=lambda method, url, **kw: answer(200)))
    assert GarminClient._request(client, "GET", CONNECT_URL, "x").status_code == 200


def test_listeners_are_told_and_can_go(network_state):
    told = []

    def fn():
        told.append(1)

    network_state.add_listener(fn)
    network_state.note_answer(netstatus.OMT, OMT_URL, 200)
    network_state.remove_listener(fn)
    network_state.note_answer(netstatus.OMT, OMT_URL, 200)
    assert told == [1]


def test_requests_from_many_threads(network_state):
    def work(n):
        for _ in range(200):
            network_state.note_answer(netstatus.CONNECT, CONNECT_URL, 200 if n % 2 else 503)

    threads = [threading.Thread(target=work, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert network_state.snapshot()[0][netstatus.CONNECT].kind in (netstatus.OK,
                                                                   netstatus.SERVER)


def test_the_tooltip_names_each_service_its_host_and_time():
    s = netstatus.Status()
    s.note_answer(netstatus.CONNECT, CONNECT_URL, 200)
    s.note_failure(netstatus.OMT, OMT_URL)
    last, answered = s.snapshot()
    now = last[netstatus.CONNECT].at
    text, tooltip = netstatus.describe(last, answered, PROD, now)
    hhmm = netstatus.when(now, now)
    assert text == "No connection"
    assert tooltip.splitlines() == [
        "No connection",
        f"Garmin Connect (connectapi.garmin.com): answered at {hhmm}",
        f"Updates and maps (omt.garmin.com): no connection at {hhmm}",
        "Sign-in (sso.garmin.com): not needed yet",
    ]


def test_the_tooltip_tells_the_last_good_answer_after_a_failure():
    s = netstatus.Status()
    s.note_answer(netstatus.OMT, OMT_URL, 200)
    s.note_answer(netstatus.OMT, OMT_URL, 503)
    last, answered = s.snapshot()
    now = last[netstatus.OMT].at
    line = netstatus.describe(last, answered, PROD, now)[1].splitlines()[2]
    assert line.endswith(f"server error (HTTP 503) at {netstatus.when(now, now)}; "
                         f"last answer at {netstatus.when(answered[netstatus.OMT], now)}")


def test_a_time_of_another_day_has_its_date():
    now = 1_800_000_000.0
    assert len(netstatus.when(now, now)) == 5
    assert netstatus.when(now - 3 * 86400, now).count("-") == 2


def _status_window():
    import gi

    gi.require_version("Gtk", "4.0")

    from openexpress.gui.app import MainWindow

    win = stand_in(session=None)
    bar = MainWindow._build_net_status(win)
    return win, bar, MainWindow


def note(state, steps):
    for service, status in steps:
        url = CONNECT_URL if service == netstatus.CONNECT else OMT_URL
        if status is None:
            state.note_failure(service, url)
        else:
            state.note_answer(service, url, status)


@pytest.mark.needs_display
@pytest.mark.parametrize("steps,state", [
    ((), netstatus.UNKNOWN),
    (((netstatus.CONNECT, 200),), netstatus.ONLINE),
    (((netstatus.CONNECT, None),), netstatus.OFFLINE),
    (((netstatus.CONNECT, 200), (netstatus.OMT, 500)), netstatus.SERVER_PROBLEM),
    (((netstatus.CONNECT, 401),), netstatus.SIGN_IN_EXPIRED),
])
def test_the_button_shows_the_state_in_words_and_colour(steps, state, network_state):
    from openexpress.gui.netstatus_view import DOT, NET_COLOUR, NET_COLOURS

    win, _bar, window = _status_window()
    note(network_state, steps)
    window._render_net_status(win)
    assert win.net_label.get_label() == netstatus.STATE_TEXT[state]
    assert win.net_icon.get_label() == DOT and win.net_icon.has_css_class(NET_COLOUR[state])
    assert len(NET_COLOURS & set(win.net_icon.get_css_classes())) == 1
    assert win.net_button.get_tooltip_text() == (
        "The state comes from the program's last requests.")  # the note of the popover
    assert win.net_button.get_popover() is not None


@pytest.mark.needs_display
def test_the_popover_names_each_service_with_its_server_and_time(network_state):
    from openexpress.gui.netstatus_view import DOT

    win, _bar, window = _status_window()
    note(network_state, [(netstatus.CONNECT, 200), (netstatus.OMT, 200), (netstatus.OMT, 503)])
    window._render_net_status(win)
    assert win.net_heading.get_label() == "Garmin server problem"
    connect, omt, sign_in = (win.net_rows[s] for s in netstatus.SERVICES)
    assert (connect.row.get_title(), connect.row.get_subtitle()) == ("Garmin Connect",
                                                                    "connectapi.garmin.com")
    assert connect.when.get_label() == "just now" and not connect.state.get_visible()
    assert connect.dot.get_label() == DOT and connect.dot.has_css_class("success")
    assert connect.row.get_sensitive()
    assert (omt.row.get_title(), omt.row.get_subtitle()) == ("Updates and maps",
                                                            "omt.garmin.com")
    assert omt.state.get_visible() and omt.state.get_label() == "server error (HTTP 503)"
    assert omt.state.has_css_class("error") and omt.dot.has_css_class("error")
    assert omt.row.get_tooltip_text().startswith("last answer at ")
    assert (sign_in.row.get_title(), sign_in.row.get_subtitle()) == ("Sign-in",
                                                                    "sso.garmin.com")
    assert sign_in.when.get_label() == "not asked yet" and not sign_in.state.get_visible()
    assert sign_in.dot.has_css_class("dim-label") and not sign_in.row.get_sensitive()  # greyed


@pytest.mark.needs_display
def test_the_popover_heading_when_all_is_well(network_state):
    win, _bar, window = _status_window()
    note(network_state, [(netstatus.CONNECT, 200)])
    window._render_net_status(win)
    assert win.net_heading.get_label() == "Connected to Garmin's servers"


@pytest.mark.needs_display
def test_an_open_popover_follows_new_answers_in_the_same_rows(network_state):
    win, _bar, window = _status_window()
    window._render_net_status(win)
    rows_before = {s: r.row for s, r in win.net_rows.items()}
    assert win.net_heading.get_label() == "Status unknown"
    note(network_state, [(netstatus.OMT, None)])
    window._render_net_status(win)  # what the listener does on the GTK thread
    assert win.net_heading.get_label() == "No connection"
    assert {s: r.row for s, r in win.net_rows.items()} == rows_before  # the same widgets
    assert win.net_rows[netstatus.OMT].state.get_label() == "no connection"


@pytest.mark.needs_display
def test_the_popover_tells_the_date_of_an_answer_from_yesterday_when_opened(monkeypatch,
                                                                           network_state):
    import time as real

    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import GObject, Gtk

    from openexpress.gui import app
    from openexpress.gui.netstatus_view import _render_net_status_on_show

    class Root(Gtk.Window):  # never shown: a stand-in for the main window
        session = None
        _render_net_status = app.MainWindow._render_net_status

    y = real.localtime(real.time() - 86400)
    yesterday_evening = real.mktime((y.tm_year, y.tm_mon, y.tm_mday, 23, 40, 0, 0, 0, -1))
    clock = [yesterday_evening + 60]
    monkeypatch.setattr(netstatus, "time", types.SimpleNamespace(
        time=lambda: clock[0], localtime=real.localtime, strftime=real.strftime))
    root = Root()
    root.set_child(app.MainWindow._build_net_status(root))
    network_state.note(netstatus.CONNECT, netstatus.Result(
        netstatus.OK, yesterday_evening, "connectapi.garmin.com", 200))
    root._render_net_status()  # the answer came a minute ago
    when = root.net_rows[netstatus.CONNECT].when
    assert when.get_label() == "1 min ago"

    clock[0] = yesterday_evening + 10 * 3600 + 20 * 60  # the popover opened at 10:00 today
    popover = root.net_button.get_popover()
    show = GObject.signal_lookup("show", Gtk.Widget)
    assert GObject.signal_has_handler_pending(popover, show, 0, False)  # opening redraws
    _render_net_status_on_show(popover)  # what opening it does
    assert when.get_label() == "yesterday, 23:40"
    root.destroy()


@pytest.mark.needs_display
def test_the_popover_opens_upwards_from_the_left_edge_of_the_button():
    from gi.repository import Gtk

    from openexpress.gui.netstatus_view import NET_POPOVER_WIDTH

    win, _bar, _window = _status_window()
    popover = win.net_button.get_popover()
    assert popover.get_position() == Gtk.PositionType.TOP and not popover.get_has_arrow()
    ok, rect = popover.get_pointing_to()
    # Centred over a rectangle as wide as itself that starts at the button: never to the left.
    assert ok and (rect.x, rect.width) == (0, NET_POPOVER_WIDTH)
    assert popover.get_child().get_size_request()[0] == NET_POPOVER_WIDTH - 16


@pytest.mark.needs_display
def test_the_button_tells_screen_readers_everything(network_state):
    from gi.repository import Gtk

    win, _bar, window = _status_window()
    note(network_state, [(netstatus.CONNECT, 200)])
    window._render_net_status(win)
    seen = {}
    original = Gtk.Accessible.update_property

    def update_property(self, props, values):
        seen.update(zip(props, values))
        return original(self, props, values)

    Gtk.MenuButton.update_property = update_property
    try:
        window._render_net_status(win)
    finally:
        del Gtk.MenuButton.update_property
    assert seen[Gtk.AccessibleProperty.LABEL] == "Online"
    description = seen[Gtk.AccessibleProperty.DESCRIPTION].splitlines()
    assert description[0] == "Show the state of Garmin's servers"
    assert description[2].startswith("Garmin Connect (connectapi.garmin.com): answered at ")


def test_rows_for_every_kind_of_answer():
    s = netstatus.Status()
    s.note_answer(netstatus.CONNECT, CONNECT_URL, 401)
    s.note_answer(netstatus.OMT, OMT_URL, 200)
    s.note_failure(netstatus.OMT, OMT_URL)
    rows = netstatus.rows(*s.snapshot(), PROD)
    assert [(r.service, r.kind) for r in rows] == [
        (netstatus.CONNECT, netstatus.AUTH), (netstatus.OMT, netstatus.NETWORK),
        (netstatus.SIGN_IN, None)]
    assert rows[0].detail.startswith("sign-in refused (HTTP 401) at ") and not rows[0].last_answer
    assert rows[1].last_answer.startswith("last answer at ")
    assert rows[2].detail == "not needed yet" and rows[2].host == "sso.garmin.com"


def test_the_window_draws_later_only_while_it_is_there():
    import gc
    import weakref

    from openexpress.gui.netstatus_view import _render_net_status_later

    class Win:
        drawn = 0

        def _render_net_status(self):
            Win.drawn += 1

    w = Win()
    ref = weakref.ref(w)
    assert _render_net_status_later(ref) is False and Win.drawn == 1
    del w
    gc.collect()
    assert _render_net_status_later(ref) is False and Win.drawn == 1


@pytest.fixture
def polish():
    from openexpress import i18n

    i18n.setup("pl")
    yield
    i18n.setup("en")


def another_day_tooltip():
    s = netstatus.Status()
    s.note_answer(netstatus.OMT, OMT_URL, 200)
    s.note_answer(netstatus.CONNECT, CONNECT_URL, 401, skew=2 * 3600)
    last, answered = s.snapshot()
    then = 1_800_000_000.0  # 15 Jan 2027, about noon UTC
    last = {k: netstatus.Result(r.kind, then, r.host, r.status, r.minutes)
            for k, r in last.items()}
    answered = {k: then for k in answered}
    return netstatus.describe(last, answered, PROD, then + 2 * 86400)[1].splitlines()


def test_another_day_has_its_date_in_english():
    import time

    day = time.strftime("%Y-%m-%d %H:%M", time.localtime(1_800_000_000.0))
    lines = another_day_tooltip()
    assert lines[1] == f"Garmin Connect (connectapi.garmin.com): computer clock off by 120 min at {day}"
    assert lines[2] == f"Updates and maps (omt.garmin.com): answered at {day}"


def test_another_day_has_its_date_first_in_polish(polish):
    import time

    t = time.localtime(1_800_000_000.0)
    day, hour = time.strftime("%d.%m.%Y", t), time.strftime("%H:%M", t)
    lines = another_day_tooltip()
    assert lines[0] == "Nieprawidłowy czas komputera"
    assert lines[1] == (f"Garmin Connect (connectapi.garmin.com): czas komputera różni się "
                        f"o\u00a0120 min ({day} o\u00a0{hour})")
    assert lines[2] == f"Aktualizacje i\u00a0mapy (omt.garmin.com): odpowiedź {day} o\u00a0{hour}"
    assert lines[3] == "Logowanie (sso.garmin.com): nie było potrzeby"


def test_today_has_only_the_hour_in_polish(polish):
    s = netstatus.Status()
    s.note_answer(netstatus.OMT, OMT_URL, 200)
    s.note_answer(netstatus.OMT, OMT_URL, 503)
    last, answered = s.snapshot()
    now = last[netstatus.OMT].at
    hour = netstatus.when(now, now)
    line = netstatus.describe(last, answered, PROD, now)[1].splitlines()[2]
    assert line == (f"Aktualizacje i\u00a0mapy (omt.garmin.com): błąd serwera (HTTP 503) o\u00a0{hour}; "
                    f"ostatnia odpowiedź o\u00a0{hour}")


# --- a tidy popover -----------------------------------------------------------------------------

@pytest.mark.needs_display
def test_a_good_row_has_its_dot_and_time_but_no_word(network_state):
    win, _bar, window = _status_window()
    note(network_state, [(netstatus.CONNECT, 200)])
    window._render_net_status(win)
    connect = win.net_rows[netstatus.CONNECT]
    assert (connect.row.get_title(), connect.row.get_subtitle()) == (
        "Garmin Connect", "connectapi.garmin.com")
    assert connect.dot.has_css_class("success") and not connect.state.get_visible()
    assert connect.when.get_label() == "just now"
    tooltip = connect.row.get_tooltip_text()  # the whole date and hour of the answer
    assert tooltip.startswith("last answer at ") and tooltip.count("-") == 2


@pytest.mark.needs_display
def test_a_row_with_a_problem_says_it_in_its_colour(network_state):
    win, _bar, window = _status_window()
    note(network_state, [(netstatus.OMT, 200), (netstatus.OMT, 503)])
    window._render_net_status(win)
    omt = win.net_rows[netstatus.OMT]
    assert omt.state.get_visible() and omt.state.get_label() == "server error (HTTP 503)"
    assert omt.state.has_css_class("error") and omt.state.has_css_class("caption-heading")
    assert omt.when.get_label() == "just now"
    assert omt.row.get_tooltip_text().startswith("last answer at ")  # the last good one


@pytest.mark.needs_display
def test_a_service_not_asked_yet_is_greyed(network_state):
    win, _bar, window = _status_window()
    note(network_state, [(netstatus.CONNECT, 200)])
    window._render_net_status(win)
    sign_in = win.net_rows[netstatus.SIGN_IN]
    assert sign_in.when.get_label() == "not asked yet" and not sign_in.state.get_visible()
    assert not sign_in.row.get_sensitive() and sign_in.row.get_tooltip_text() is None


@pytest.mark.needs_display
def test_the_note_is_the_tooltip_of_the_button_not_a_footer(network_state):
    from gi.repository import Gtk

    win, _bar, window = _status_window()
    note(network_state, [(netstatus.CONNECT, 200)])
    window._render_net_status(win)
    note_text = "The state comes from the program's last requests."
    assert win.net_button.get_tooltip_text() == note_text

    def labels(widget):
        found, child = [], widget.get_first_child()
        if isinstance(widget, Gtk.Label):
            found.append(widget.get_label())
        while child is not None:
            found += labels(child)
            child = child.get_next_sibling()
        return found

    assert note_text not in labels(win.net_button.get_popover().get_child())
    assert win.net_heading.get_label() == "Connected to Garmin's servers"
    assert win.net_heading_dot.has_css_class("success")


@pytest.mark.needs_display
def test_try_again_shows_only_when_something_is_wrong_and_refreshes_the_page(network_state):
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk

    from openexpress.gui import app

    refreshed = []

    class Root(Gtk.Window):  # never shown: a stand-in for the main window
        session = None
        _render_net_status = app.MainWindow._render_net_status

        def _on_refresh(self):
            refreshed.append(True)

    root = Root()
    root.set_child(app.MainWindow._build_net_status(root))
    note(network_state, [(netstatus.CONNECT, 200)])
    root._render_net_status()
    assert not root.net_retry.get_visible()  # all is well: nothing to try
    note(network_state, [(netstatus.CONNECT, None)])
    root._render_net_status()
    assert root.net_retry.get_visible() and root.net_retry.get_label() == "Try Again"
    root.net_retry.emit("clicked")
    assert refreshed == [True]  # Refresh of the page: no request of its own
    root.destroy()
