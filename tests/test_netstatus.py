"""The state of Garmin's servers, from the answers to the program's own requests."""

import threading
import types

import pytest
import requests

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
    (((netstatus.CONNECT, 200), (netstatus.OMT, None)), netstatus.SERVER_PROBLEM),  # a part
    (((netstatus.OMT, 503),), netstatus.SERVER_PROBLEM),
    (((netstatus.CONNECT, 401), (netstatus.OMT, None)), netstatus.SIGN_IN_EXPIRED),
    (((netstatus.OMT, None), (netstatus.OMT, 200)), netstatus.ONLINE),  # back again
    (((netstatus.CONNECT, 401), (netstatus.CONNECT, 200)), netstatus.ONLINE),  # signed in
    (((netstatus.SIGN_IN, 401), (netstatus.CONNECT, 200)), netstatus.ONLINE),
    (((netstatus.CONNECT, 401), (netstatus.OMT, 200)), netstatus.SIGN_IN_EXPIRED),
])
def test_the_state_of_the_dot(steps, state):
    assert noted(*steps) == state


def test_a_wrong_clock_is_its_own_state():
    s = netstatus.Status()
    s.note_answer(netstatus.CONNECT, CONNECT_URL, 401, skew=3 * 3600)
    assert s.state() == netstatus.CLOCK_OFF
    last, _answered = s.snapshot()
    assert last[netstatus.CONNECT].minutes == 180


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
    assert network_state.state() == netstatus.SERVER_PROBLEM


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
            download.fetch(["https://download.garmin.com/a.img"], tmp_path / "a", None, None,
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
    with pytest.raises(requests.ConnectionError):
        ciq.StoreApi(client)._request("GET", "express/x", "x")
    assert network_state.snapshot()[0][netstatus.CONNECT].kind == netstatus.NETWORK


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
    assert text == "Garmin server problem"
    assert tooltip.splitlines() == [
        "Garmin server problem",
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
    from gi.repository import Gtk

    from openexpress.gui.app import MainWindow

    if not Gtk.init_check():
        pytest.skip("no display")
    win = types.SimpleNamespace(session=None)
    bar = MainWindow._build_net_status(win)
    return win, bar, MainWindow


def note(state, steps):
    for service, status in steps:
        url = CONNECT_URL if service == netstatus.CONNECT else OMT_URL
        if status is None:
            state.note_failure(service, url)
        else:
            state.note_answer(service, url, status)


@pytest.mark.parametrize("steps,state", [
    ((), netstatus.UNKNOWN),
    (((netstatus.CONNECT, 200),), netstatus.ONLINE),
    (((netstatus.CONNECT, None),), netstatus.OFFLINE),
    (((netstatus.CONNECT, 200), (netstatus.OMT, 500)), netstatus.SERVER_PROBLEM),
    (((netstatus.CONNECT, 401),), netstatus.SIGN_IN_EXPIRED),
])
def test_the_button_shows_the_state_in_words_and_colour(steps, state, network_state):
    from openexpress.gui.app import DOT, NET_COLOUR, NET_COLOURS

    win, _bar, window = _status_window()
    note(network_state, steps)
    window._render_net_status(win)
    assert win.net_label.get_label() == netstatus.STATE_TEXT[state]
    assert win.net_icon.get_label() == DOT and win.net_icon.has_css_class(NET_COLOUR[state])
    assert len(NET_COLOURS & set(win.net_icon.get_css_classes())) == 1
    assert win.net_button.get_tooltip_text() is None  # a popover instead of a tooltip
    assert win.net_button.get_popover() is not None


def test_the_popover_names_each_service_with_its_server_and_time(network_state):
    from openexpress.gui.app import DOT

    win, _bar, window = _status_window()
    note(network_state, [(netstatus.CONNECT, 200), (netstatus.OMT, 200), (netstatus.OMT, 503)])
    window._render_net_status(win)
    assert win.net_heading.get_label() == "Garmin server problem"
    connect, omt, sign_in = (win.net_rows[s] for s in netstatus.SERVICES)
    assert (connect.name.get_label(), connect.host.get_label()) == ("Garmin Connect",
                                                                    "connectapi.garmin.com")
    assert connect.when.get_label().startswith("at ") and connect.outcome.get_label() == "answered"
    assert connect.dot.get_label() == DOT and connect.dot.has_css_class("success")
    assert connect.row.get_sensitive() and not connect.later.get_visible()
    assert (omt.name.get_label(), omt.host.get_label()) == ("Updates and maps", "omt.garmin.com")
    assert omt.outcome.get_label() == "server error (HTTP 503)" and omt.dot.has_css_class("error")
    assert omt.later.get_visible() and omt.later.get_label().startswith("last answer at ")
    assert (sign_in.name.get_label(), sign_in.host.get_label()) == ("Sign-in", "sso.garmin.com")
    assert sign_in.outcome.get_label() == "not needed yet" and sign_in.when.get_label() == ""
    assert sign_in.dot.has_css_class("dim-label") and not sign_in.row.get_sensitive()  # greyed


def test_the_popover_heading_when_all_is_well_and_its_note(network_state):
    from gi.repository import Gtk

    win, _bar, window = _status_window()
    note(network_state, [(netstatus.CONNECT, 200)])
    window._render_net_status(win)
    assert win.net_heading.get_label() == "Connected to Garmin's servers"
    box = win.net_button.get_popover().get_child()
    labels = []
    child = box.get_first_child()
    while child is not None:
        if isinstance(child, Gtk.Label):
            labels.append(child.get_label())
        child = child.get_next_sibling()
    assert labels[-1] == "The state comes from the program's last requests."


def test_an_open_popover_follows_new_answers_in_the_same_rows(network_state):
    win, _bar, window = _status_window()
    window._render_net_status(win)
    rows_before = {s: r.row for s, r in win.net_rows.items()}
    assert win.net_heading.get_label() == "Status unknown"
    note(network_state, [(netstatus.OMT, None)])
    window._render_net_status(win)  # what the listener does on the GTK thread
    assert win.net_heading.get_label() == "No connection"
    assert {s: r.row for s, r in win.net_rows.items()} == rows_before  # the same widgets
    assert win.net_rows[netstatus.OMT].outcome.get_label() == "no connection"


def test_the_popover_opens_upwards_from_the_left_edge_of_the_button():
    from gi.repository import Gtk

    from openexpress.gui.app import NET_POPOVER_WIDTH

    win, _bar, _window = _status_window()
    popover = win.net_button.get_popover()
    assert popover.get_position() == Gtk.PositionType.TOP and not popover.get_has_arrow()
    ok, rect = popover.get_pointing_to()
    # Centred over a rectangle as wide as itself that starts at the button: never to the left.
    assert ok and (rect.x, rect.width) == (0, NET_POPOVER_WIDTH)
    assert popover.get_child().get_size_request()[0] == NET_POPOVER_WIDTH - 16


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

    from openexpress.gui.app import _render_net_status_later

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
                        f"o 120 min ({day} o {hour})")
    assert lines[2] == f"Aktualizacje i mapy (omt.garmin.com): odpowiedź {day} o {hour}"
    assert lines[3] == "Logowanie (sso.garmin.com): nie było potrzeby"


def test_today_has_only_the_hour_in_polish(polish):
    s = netstatus.Status()
    s.note_answer(netstatus.OMT, OMT_URL, 200)
    s.note_answer(netstatus.OMT, OMT_URL, 503)
    last, answered = s.snapshot()
    now = last[netstatus.OMT].at
    hour = netstatus.when(now, now)
    line = netstatus.describe(last, answered, PROD, now)[1].splitlines()[2]
    assert line == (f"Aktualizacje i mapy (omt.garmin.com): błąd serwera (HTTP 503) o {hour}; "
                    f"ostatnia odpowiedź o {hour}")
