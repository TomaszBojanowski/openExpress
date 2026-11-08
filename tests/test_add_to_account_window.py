"""Add to Account in the window (experimental): offered for a device the account's list does not
hold, by the banner and by a row of the Device page; a question naming the device and the
account first; then service.add_device as an account task, and what came of it said. All IDs
and names here are made up."""

import types

import pytest
from fakes import (
    DEVICE_UNIT,
    DEVICES_PATH,
    OTHER_UNIT,
    RunNow,
    another_account,
    descendants,
    make_client,
    page_window,
    response,
)
from fakes import window as stand_in

from openexpress import service
from openexpress.connect import REGISTER_PATH, NotAccountDevice, OwnershipUnknown
from openexpress.device.storage import LocalStorage
from openexpress.device.xmldevice import parse
from openexpress.gui import add_to_account
from openexpress.settings import Settings

UNIT = DEVICE_UNIT
ACCOUNT = "tomek@example.com"  # the window shows the account's e-mail address whole


def garmin(lists, answer=None):
    """A client whose Garmin lists, at each reading of the account's list, the next of
    `lists`, and answers the registration with `answer` (by default: added, active)."""
    lists = list(lists)
    client = make_client()
    client.auth.account = {"emailaddress": ACCOUNT}
    client.http.routes[DEVICES_PATH] = lambda: response(body=lists.pop(0) if len(lists) > 1
                                                        else lists[0])
    added = response(body={"unitId": UNIT, "newRegistration": True, "deviceStatus": "active"})

    def handler(method, request):
        if method == "POST":
            return answer if answer is not None else added
        return None

    client.http.handlers[REGISTER_PATH] = handler
    return client


def checked(device_root, client):
    device = parse((device_root / "GARMIN" / "GarminDevice.xml").read_bytes())
    s = service.DeviceSession(LocalStorage(device_root), device, Settings(), client)
    return service.check_session(s)


def a_window(s, **changes):
    seen = {"toasts": [], "informed": [], "refreshed": []}
    run_task = RunNow(error="record")
    win = stand_in(session=s, run_task=run_task, busy=False,
                   toast=seen["toasts"].append,
                   refresh_device=lambda cause=None: seen["refreshed"].append(cause), **changes)
    return win, run_task, seen


@pytest.fixture
def informed(in_the_window):
    """inform() as the window would show it: its name, heading and text, in order."""
    said = []
    in_the_window("inform", lambda parent, name, heading, body, **kw: said.append(
        (name, heading, body)))
    return said


@pytest.fixture
def asked(in_the_window):
    """confirm() as the window would show it, the question kept to be answered by the test."""
    said = []

    def confirm(parent, name, heading, body, *answers, **kw):
        said.append(types.SimpleNamespace(name=name, heading=heading, body=body,
                                          answers=answers, kw=kw))
    in_the_window("confirm", confirm)
    return said


# --- when it is offered ------------------------------------------------------------------------


def test_offered_only_for_a_device_off_the_account_while_logged_in(device_root):
    s = checked(device_root, garmin([[{"unitid": OTHER_UNIT}]]))
    assert isinstance(s.ownership_error, NotAccountDevice)
    assert add_to_account.offers_adding(s)
    assert not add_to_account.offers_adding(None)
    another_account()  # its list, not the one kept from the check before
    on_it = checked(device_root, garmin([[{"unitid": UNIT}]]))
    assert not add_to_account.offers_adding(on_it)
    unknown = types.SimpleNamespace(logged_in=True, ownership_error=OwnershipUnknown("x"))
    assert not add_to_account.offers_adding(unknown)
    logged_out = types.SimpleNamespace(logged_in=False, ownership_error=NotAccountDevice("x"))
    assert not add_to_account.offers_adding(logged_out)
    drive = types.SimpleNamespace(logged_in=True, ownership_error=NotAccountDevice("x"),
                                  drive_refusal=("no-upload-types", ""))
    assert not add_to_account.offers_adding(drive)


def test_the_banner_offers_it_and_its_button_asks(device_root, asked):
    from openexpress.gui.app import MainWindow

    s = checked(device_root, garmin([[{"unitid": OTHER_UNIT}]]))
    seen = {}
    win, _run, _seen = a_window(s, _ownership_title=MainWindow._ownership_title,
                                banner=types.SimpleNamespace(
                                    set_title=lambda t: seen.update(title=t),
                                    set_button_label=lambda b: seen.update(button=b),
                                    set_revealed=lambda r: seen.update(revealed=r)))
    MainWindow._update_banner(win)
    assert seen == {"title": "This device is not registered to your Garmin account",
                    "button": "Add to Account", "revealed": True}
    assert win._banner_kind == "add"
    MainWindow._banner_action(win)
    assert [q.name for q in asked] == ["add-to-account"]


# --- the question ------------------------------------------------------------------------------


def test_the_question_names_the_device_and_the_account(device_root, asked):
    s = checked(device_root, garmin([[], [{"unitid": UNIT}]]))
    win, run_task, seen = a_window(s)
    add_to_account.confirm_add_to_account(win)
    (q,) = asked
    assert q.heading == "Add Test watch to Your Garmin Account?"
    assert ACCOUNT in q.body and "GarminDevice.xml" in q.body and "Unit ID" in q.body
    assert "same account" in q.body and "only in Garmin Connect" in q.body
    assert q.body.endswith("This is an experimental feature.")
    (answer,) = q.answers
    assert (answer.id, answer.label, answer.look) == ("add", "Add to Account", "suggested")
    assert q.kw.get("default") == "cancel"
    assert run_task.calls == [] and s.client.http.paths() == [DEVICES_PATH]  # asked only
    answer.run()
    assert run_task.titles == [f"Adding to the Account {ACCOUNT}…"]


def test_not_asked_for_a_device_on_the_account(device_root, asked):
    s = checked(device_root, garmin([[{"unitid": UNIT}]]))
    win, run_task, _seen = a_window(s)
    add_to_account.confirm_add_to_account(win)
    add_to_account.add_to_account(win)
    assert asked == [] and run_task.calls == []


# --- the task and what came of it --------------------------------------------------------------


def test_added(device_root, informed):
    # The list at the check, again before the request, and after it.
    s = checked(device_root, garmin([[], [], [{"unitid": UNIT}]]))
    win, run_task, seen = a_window(s)
    add_to_account.add_to_account(win)
    (call,) = run_task.calls
    assert call.device is False  # an account task: the device may be unplugged meanwhile
    assert s.client.http.paths() == [DEVICES_PATH, DEVICES_PATH, REGISTER_PATH, DEVICES_PATH]
    # Short: a toast is one line, at 360 px too (the account is on the page).
    assert seen["toasts"] == ["Added to your Garmin account"]
    assert len(seen["refreshed"]) == 1 and informed == []  # the device checked again


def test_on_the_account_meanwhile(device_root, informed):
    s = checked(device_root, garmin([[], [{"unitid": UNIT}]]))
    win, _run, seen = a_window(s)
    add_to_account.add_to_account(win)  # the list read before the request holds it now
    assert REGISTER_PATH not in s.client.http.paths()
    assert seen["toasts"] == ["On your Garmin account already"]


def test_accepted_but_not_on_the_list_yet(device_root, informed):
    s = checked(device_root, garmin([[]]))
    win, _run, seen = a_window(s)
    add_to_account.add_to_account(win)
    assert [(n, h) for n, h, _b in informed] == [
        ("added-not-listed",
         "Device Not Listed Yet")]
    assert seen["toasts"] == [] and len(seen["refreshed"]) == 1


@pytest.mark.parametrize("answer, heading, said", [
    (response(409, {"error": {"code": 409}}), "Device on Another Account",
     "registered to another Garmin account"),
    (response(409, {"error": {"code": 409, "subcode": 4091}}),
     "Subscription on Another Account", "active subscription"),
    (response(400, b""), "Device Not Added",
     "did not accept the description of this device"),
])
def test_a_refusal_is_said_and_nothing_else(device_root, informed, answer, heading, said):
    s = checked(device_root, garmin([[]], answer=answer))
    win, run_task, seen = a_window(s)
    add_to_account.add_to_account(win)
    ((name, shown_heading, body),) = informed
    assert (name, shown_heading) == ("add-to-account-refused", heading)
    assert said in body and "Nothing was changed." in body
    assert run_task.errors == [] and seen["toasts"] == [] and seen["refreshed"] == []


def test_a_network_error_is_the_window_s_usual_error(device_root, informed):
    import requests

    s = checked(device_root, garmin([[]], answer=requests.ConnectionError("offline")))
    s.client.http.handlers[REGISTER_PATH] = lambda method, request: (
        None if method == "GET" else (_ for _ in ()).throw(requests.ConnectionError("offline")))
    win, run_task, seen = a_window(s)
    add_to_account.add_to_account(win)
    assert informed == [] and len(run_task.errors) == 1 and seen["toasts"] == []


# --- the row of the Device page -------------------------------------------------------------


def _row_texts(group):
    from gi.repository import Adw

    return [(w.get_title(), w.get_subtitle()) for w in descendants(group)
            if isinstance(w, Adw.ActionRow)]


@pytest.mark.needs_display
def test_the_row_of_the_page_offers_it(asked):
    from gi.repository import Adw, Gtk

    win, _section = page_window("watch")
    win.session = types.SimpleNamespace(
        logged_in=True, ownership_error=NotAccountDevice("x"),
        client=types.SimpleNamespace(auth=types.SimpleNamespace(display=ACCOUNT)),
        device=types.SimpleNamespace(description="Test watch", unit_id=None))
    group = Adw.PreferencesGroup()
    group.add(add_to_account.add_to_account_row(win, win.session))
    assert _row_texts(group) == [("Garmin Account", f"Not on the account {ACCOUNT}")]
    (button,) = [w for w in descendants(group) if isinstance(w, Gtk.Button)
                 and w.get_label() == "Add to Account"]
    button.emit("clicked")
    assert [q.name for q in asked] == ["add-to-account"]
