"""The rows of the device page with the readings of the Garmin Connect account
(docs/design/odczyty-z-konta.md): "Account Sensors" under the rows of the account, "Solar
Charging" under it. Their states: the answers (the sensors in a closed row that opens on them),
no sensor (no row), not signed in ("Sign in to see"), an account that could not be asked ("Could
not check"), both in a plain row, and a week the sun gained nothing. All devices, sensors and
answers here are made up."""

import types
from datetime import date, timedelta

import pytest
from conftest import NOON
from fakes import in_language

from openexpress import answers
from openexpress.answers import ANSWERS
from openexpress.connect import ApiError

NB = "\u00a0"
UNIT = 3000000001
DEVICE_ID = 91234567


def utc_text(local):
    from datetime import UTC

    return local.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.0")


SENSORS = [
    {"devicename": "Made-up Strap", "batterylevel": 80, "batterystatus": "OK",
     "lastconnected": utc_text(NOON - timedelta(hours=3))},
    {"devicename": "Made-up Light", "batterystatus": "LOW",
     "lastconnected": utc_text(NOON - timedelta(days=2))},
    {"devicename": "Made-up <Speed>", "batterystatus": "GOOD",
     "lastconnected": utc_text(NOON - timedelta(days=1, hours=1))},
]
SOLAR = [{"localconnectdate": "2026-10-21", "totalactivitytimegainedms": 2_100_000},
         {"localconnectdate": "2026-10-19", "totalactivitytimegainedms": 3_900_000}]


def account(solar=True, on_it=True):
    entry = {"unitid": UNIT if on_it else UNIT + 9, "deviceid": DEVICE_ID,
             "productdisplayname": "Made-up 8", "solarpanelutilizationcapable": solar}
    return [entry]


def at_once(fn, done, failed=None):
    """in_background, but now: the work, then what is said of it."""
    try:
        result = fn()
    except Exception as e:  # noqa: BLE001 - as in_background
        if failed is not None:
            failed(e)
        return
    done(result)


def _page(logged_in=True, devices=None, sensors=SENSORS, solar=SOLAR, fail=False,
          description="Made-up 8 Solar"):
    """The device page as the window builds it, the account's readings asked at once; returns
    the window, its section and the solar requests asked."""
    from fakes import device_session, page_window
    from gi.repository import Adw

    from openexpress.gui.app import MainWindow

    asked = []

    def account_sensors():
        if fail:
            raise ApiError("Account sensors: network error (made-up)")
        return sensors

    def solar_gain(device_id, start, end):
        asked.append((device_id, start, end))
        if fail:
            raise ApiError("Solar charging: network error (made-up)")
        return solar

    win, section = page_window("watch")
    device = types.SimpleNamespace(description=description, software_version_str="1.00",
                                   part_number="006-B0000-00", unit_id=UNIT)
    client = types.SimpleNamespace(auth=types.SimpleNamespace(display="x"),
                                   account_sensors=account_sensors, solar_gain=solar_gain)
    win.session = device_session(device=device, storage=None, logged_in=logged_in,
                                 ownership_error=None,
                                 account_devices=account() if devices is None else devices,
                                 mass_storage=False, space=(None, None), to_sync_asked=True,
                                 client=client)
    win._account, win.update_state, win.update_count, win.map_state = None, None, 0, None
    win._update_check_session = None
    for name in ("_show_device_picture", "_check_updates_quietly", "_check_maps_quietly",
                 "_show_last_sync", "_render_map_state", "select_section", "backup", "logout",
                 "_show_primary_training"):
        setattr(win, name, lambda *a, **kw: None)
    win._auto_sync_row = lambda: Adw.ActionRow(title="Syncs each time")
    for name in ("show_overview", "_set_update_state", "_render_update_state",
                 "_highlight_update"):
        setattr(win, name, types.MethodType(getattr(MainWindow, name), win))
    win.in_background = at_once
    win.show_overview()
    return win, section, asked


def sensors_of(win):
    """The row Account Sensors as shown: (shown, subtitle, opens, open, [(name, line, low)]);
    "opens": a row that opens on the sensors, not a plain one, with no arrow."""
    from fakes import descendants
    from gi.repository import Adw

    from openexpress.gui.window_tasks import row_label

    shown = [r for r in (win.sensors_row, win.sensors_list) if r is not None and r.get_visible()]
    if not shown:
        return False, None, False, False, []
    (row,) = shown
    if not isinstance(row, Adw.ExpanderRow):
        return True, row.get_subtitle(), False, False, []
    children = [w for w in descendants(row) if isinstance(w, Adw.ActionRow)
                and w.get_parent() is not None and w.get_title() != row.get_title()]
    return (True, row.get_subtitle(), True, row.get_expanded(),
            [(c.get_title(), c.get_subtitle(),
              row_label(c, "subtitle").has_css_class("warning")) for c in children])


def account_titles(section):
    """The titles of the rows shown in the group of the account, in order; a row that opens
    counts once, its sensors not."""
    from fakes import descendants
    from gi.repository import Adw

    (group,) = [g for g in section.groups if g.get_title() == "Garmin Connect Account"]
    titles = []
    for w in descendants(group):
        if not isinstance(w, Adw.PreferencesRow) or not w.get_visible():
            continue
        if any(isinstance(a, Adw.ExpanderRow) for a in ancestors(w, group)):
            continue  # the row inside a row that opens, and the sensors in it
        titles.append(w.get_title())
    return titles


def ancestors(widget, up_to):
    """The widgets between `widget` and `up_to`."""
    found, w = [], widget.get_parent()
    while w is not None and w is not up_to:
        found.append(w)
        w = w.get_parent()
    return found


@pytest.mark.needs_display
def test_the_sensors_low_ones_first_and_in_the_warning_colour(clock):
    win, _section, _asked = _page()
    shown, subtitle, opens, is_open, rows = sensors_of(win)
    assert (shown, subtitle, opens) == (True, f"3 sensors{NB}· 1 with a low battery", True)
    assert not is_open  # its subtitle tells of the low battery
    assert rows == [
        ("Made-up Light", f"Battery low{NB}· last connected 2 days ago", True),
        ("Made-up Strap", f"Battery 80%{NB}· last connected 3 h ago", False),
        ("Made-up &lt;Speed&gt;", f"Battery good{NB}· last connected yesterday, 11:00", False)]


@pytest.mark.needs_display
def test_five_sensors_closed_also_with_a_critical_battery(clock):
    five = [*SENSORS, {"devicename": "Made-up Power", "batterystatus": "CRITICAL"},
            {"devicename": "Made-up Radar", "batterylevel": 40}]
    win, _section, _asked = _page(sensors=five)
    shown, subtitle, opens, is_open, rows = sensors_of(win)
    assert (shown, subtitle, opens, is_open) == (
        True, f"5 sensors{NB}· 2 with a low battery", True, False)
    assert [name for name, _line, _low in rows][:2] == ["Made-up Light", "Made-up Power"]


@pytest.mark.needs_display
def test_no_sensor_no_row_and_solar_charging_in_its_place(clock):
    win, section, _asked = _page(sensors=[])
    assert sensors_of(win)[0] is False
    titles = account_titles(section)
    assert "Account Sensors" not in titles and titles[-1] == "Solar Charging"


@pytest.mark.needs_display
def test_no_sensors_on_the_page_of_a_device_off_the_account(clock):
    asked = []
    win, _section, _asked = _page(devices=account(on_it=False))
    win.in_background = lambda *a, **kw: asked.append(a)
    win.show_overview()
    assert sensors_of(win)[0] is False and asked == []


@pytest.mark.needs_display
def test_not_signed_in_sign_in_to_see_in_a_plain_row(clock):
    win, _section, asked = _page(logged_in=False)
    assert sensors_of(win) == (True, "Sign in to see", False, False, [])
    assert win.solar_row.get_subtitle() == "Sign in to see"
    assert asked == []
    win, _section, _asked = _page(logged_in=False, description="Made-up 8")
    assert win.solar_row is None


@pytest.mark.needs_display
def test_an_account_not_asked_could_not_check_in_a_plain_row(clock):
    win, _section, _asked = _page(fail=True)
    assert sensors_of(win) == (True, "Could not check", False, False, [])
    assert win.solar_row.get_subtitle() == "Could not check"


@pytest.mark.needs_display
def test_solar_charging_today_and_the_week(clock):
    win, section, asked = _page()
    assert win.solar_row.get_subtitle() == (
        f"Activity time gained: today 35{NB}min{NB}· last 7{NB}days 1{NB}h{NB}40{NB}min")
    assert asked == [(DEVICE_ID, date(2026, 10, 15), date(2026, 10, 21))]
    assert account_titles(section)[-2:] == ["Account Sensors", "Solar Charging"]
    from fakes import descendants
    from gi.repository import Adw

    (storage,) = [g for g in section.groups if g.get_title() == "Storage"]
    assert "Solar Charging" not in [w.get_title() for w in descendants(storage)
                                    if isinstance(w, Adw.PreferencesRow)]


@pytest.mark.needs_display
@pytest.mark.parametrize("solar", [
    [], [{"localconnectdate": "2026-10-21", "totalactivitytimegainedms": 0}],
    [{"localconnectdate": "2026-10-17", "totalactivitytimegainedms": 12}],
    [{"localconnectdate": "2026-10-21", "totalactivitytimegainedms": 9_000},
     {"localconnectdate": "2026-10-18", "totalactivitytimegainedms": 20_000}]],
    ids=["no day named", "today nothing", "12 ms", "under half a minute"])
def test_a_week_under_a_minute_in_a_sentence(solar, clock):
    win, _section, _asked = _page(solar=solar)
    assert win.solar_row.get_subtitle() == (
        f"Activity time gained: under a minute in the last 7{NB}days")


@pytest.mark.needs_display
def test_a_minute_in_the_week_is_told_as_the_others(clock):
    win, _section, _asked = _page(
        solar=[{"localconnectdate": "2026-10-16", "totalactivitytimegainedms": 60_000}])
    assert win.solar_row.get_subtitle() == (
        f"Activity time gained: today 0{NB}min{NB}· last 7{NB}days 1{NB}min")


@pytest.mark.needs_display
@pytest.mark.parametrize("devices", [account(solar=False), account(on_it=False)],
                         ids=["not of the sun", "off the account"])
def test_no_solar_row_for_another_device(devices, clock):
    win, _section, asked = _page(devices=devices)
    assert win.solar_row is None and asked == []


@pytest.mark.needs_display
def test_in_polish(clock):
    with in_language("pl"):
        win, _section, _asked = _page()
        sensors = sensors_of(win)
        titles = win.sensors_list.get_title(), win.solar_row.get_title()
        solar = win.solar_row.get_subtitle()
        win, _section, _asked = _page(solar=[])
        winter = win.solar_row.get_subtitle()
    assert titles == ("Czujniki na koncie", "Ładowanie słoneczne")
    assert sensors[1] == f"3 czujniki{NB}· 1 ze słabą baterią"
    assert sensors[4][0][1] == f"Bateria słaba{NB}· ostatnio połączony 2 dni temu"
    assert solar == (f"Zyskany czas aktywności: dziś 35{NB}min{NB}· ostatnie 7{NB}dni "
                     f"1{NB}h{NB}40{NB}min")
    assert winter == f"Zyskany czas aktywności: poniżej minuty w{NB}ostatnich 7{NB}dniach"


@pytest.mark.needs_display
def test_a_sensor_of_last_week_with_its_date_in_the_language(clock):
    old = [{"devicename": "Made-up Cadence", "batterylevel": 70,
            "lastconnected": utc_text(NOON - timedelta(days=9))}]
    with in_language("pl"):
        win, _section, _asked = _page(sensors=old)
        polish = sensors_of(win)[4][0][1]
    win, _section, _asked = _page(sensors=old)
    assert polish == f"Bateria 70%{NB}· ostatnio połączony 12.10.2026 o{NB}12:00"
    assert sensors_of(win)[4][0][1] == f"Battery 70%{NB}· last connected 2026-10-12 12:00"


@pytest.mark.needs_display
def test_refresh_asks_the_readings_anew(clock):
    from openexpress.gui.tasks import forget_on_refresh

    ANSWERS.ask(answers.SENSORS, ("scope",), list)
    ANSWERS.ask(answers.SOLAR, ("scope", DEVICE_ID), list)
    forget_on_refresh(None)
    assert ANSWERS.kept(answers.SENSORS, ("scope",)) is None
    assert ANSWERS.kept(answers.SOLAR, ("scope", DEVICE_ID)) is None
