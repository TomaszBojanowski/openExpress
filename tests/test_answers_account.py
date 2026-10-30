"""The account's devices and its primary training device are kept a few minutes (answers):
opening a device again does not ask again; a check before a write to the account or an
upload asks anew; Refresh, a change of a primary device and another login (or none) forget
them. Counted on a made-up Garmin Connect; all accounts and devices are made up."""
from datetime import timedelta

import pytest
from fakes import DEVICE_UNIT as UNIT
from fakes import DEVICES_PATH, make_client
from test_primary_both_roles import LIST, READ_TRAINING, SET_TRAINING, Account
from test_primary_both_roles import device as made_up_device  # noqa: F401 - the fixture

from openexpress import answers, service


def test_opening_a_device_again_uses_the_list_kept(monkeypatch, clock):
    client = make_client([{"unitid": UNIT}])
    service.require_account_device(client, UNIT)  # the device opened
    clock.current += timedelta(seconds=4 * 60)
    service.require_account_device(client, UNIT)  # opened again, 4 minutes later
    clock.current += timedelta(seconds=2 * 60)
    service.require_account_device(client, UNIT)  # 6 minutes: asked anew
    assert client.http.paths() == [DEVICES_PATH, DEVICES_PATH]


class KeptList:
    """The account of a sync whose list of devices is kept as Garmin Connect's client keeps it
    (answers): `asked` says when it was asked of the server."""

    def __init__(self):
        from fakes import SyncAccount

        self.account = SyncAccount(verified_unit_id=None)

    def __getattr__(self, name):
        return getattr(self.account, name)

    def account_devices(self, fresh=False):
        return answers.ANSWERS.ask(answers.ACCOUNT_DEVICES, ("made-up login",),
                                   lambda: self.account.account_devices(), fresh=fresh)


@pytest.mark.parametrize("preview", [False, True])
def test_a_sync_checks_the_owner_anew_and_a_plan_shown_uses_the_list_kept(device_root, xdg,
                                                                          preview):
    """Before uploads the account's list is asked anew; a sync only planned to be shown
    (the command line's dry run) takes the one kept when the device was opened."""
    from openexpress.device.storage import LocalStorage
    from openexpress.device.xmldevice import parse
    from openexpress.settings import Settings

    client = KeptList()
    st = LocalStorage(device_root)
    s = service.DeviceSession(st, parse(st.read("GARMIN/GarminDevice.xml")), Settings(), client)
    service.require_account_device(client, s.device.unit_id)  # the device opened
    client.asked.clear()
    service.plan_sync(s, service.SyncOptions(preview=preview, first_run="baseline"))
    assert ("account" in client.asked) is not preview


@pytest.mark.parametrize("task", ["install", "remove from the account", "remove and forget"])
def test_a_change_of_the_apps_on_the_account_checks_the_owner_anew(task, monkeypatch,
                                                                   in_the_window):
    """Installing apps, removing one from the account list, removing one and forgetting it:
    the ownership check asks the account's list anew before the change."""
    import types
    import uuid

    from fakes import RunNow, window_methods
    from fakes import window as stand_in

    asked = []

    class Checked(Exception):
        pass

    def require(client, unit_id, fresh=False):
        asked.append(fresh)
        raise Checked  # nothing further: only the check is looked at

    monkeypatch.setattr(service, "require_account_device", require)
    in_the_window("confirm", lambda win, name, heading, body, *answer, **kw: next(
        a for a in answer if a.id == "forget").run())
    app = types.SimpleNamespace(name="Face It", app_id=uuid.UUID(int=1), has_files=True,
                                direct_removal=False)
    runner = RunNow(error="record")
    win = window_methods(stand_in(
        session=types.SimpleNamespace(client=object(), storage=None,
                                      device=types.SimpleNamespace(unit_id=UNIT)),
        run_task=runner, progress=None, load_section=lambda key, force=False: None),
        ["_install_apps", "confirm_forget_app", "confirm_remove_app", "_forget_text"])
    {"install": lambda: win._install_apps([app]),
     "remove from the account": lambda: win.confirm_forget_app(app),
     "remove and forget": lambda: win.confirm_remove_app(app)}[task]()
    assert asked == [True] and len(runner.calls) == 1


# --- what forgets them ------------------------------------------------------------------------


def test_refresh_forgets_the_account(made_up_device):
    from openexpress.gui.tasks import forget_on_refresh

    account = Account()
    client = account.client()
    service.primary_training_status(client, made_up_device)
    forget_on_refresh(None)
    service.primary_training_status(client, made_up_device)
    assert account.sent == [LIST, READ_TRAINING, LIST, READ_TRAINING]


def test_the_device_page_drawn_again_asks_nothing(made_up_device):
    """Set as Primary, background sync turned on or off: the device page is drawn again and
    reads the primary device kept."""
    account = Account()
    client = account.client()
    devices = service.require_account_device(client, UNIT)
    service.primary_training_status(client, made_up_device, devices)
    service.primary_training_status(client, made_up_device, devices)
    assert account.sent == [LIST, READ_TRAINING]


def test_a_change_of_the_primary_device_asks_anew_and_keeps_what_it_read_back(made_up_device):
    account = Account()
    client = account.client()
    devices = service.require_account_device(client, UNIT)
    service.primary_training_status(client, made_up_device, devices)  # the row, before
    account.sent.clear()
    service.set_primary_training(client, made_up_device, apply=True)
    # Decided on what the account says now: nothing kept is used.
    assert account.sent == [LIST, READ_TRAINING, SET_TRAINING, READ_TRAINING]
    account.sent.clear()
    # The row drawn again, with the session's list (as the window draws it).
    after = service.primary_training_status(client, made_up_device, devices)
    assert after.is_this_device and account.sent == []  # what was read back


def test_a_change_that_failed_forgets_the_primary_device_all_the_same(made_up_device):
    account = Account(put_status=500)
    client = account.client()
    with pytest.raises(Exception):  # noqa: B017 - the server's error, as it is
        service.set_primary_training(client, made_up_device, apply=True)
    account.sent.clear()
    service.primary_training_status(client, made_up_device)
    assert account.sent == [LIST, READ_TRAINING]


@pytest.mark.parametrize("action", ["logout", "login"])
def test_another_login_or_none_forgets_the_account(action):
    from openexpress.auth import store

    client = make_client([{"unitid": UNIT}])
    service.require_account_device(client, UNIT)
    if action == "logout":
        store.delete()
    else:
        store.save(store.ConnectAuth("another-token", "another-secret"))
    service.require_account_device(client, UNIT)
    assert client.http.paths() == [DEVICES_PATH, DEVICES_PATH]


def test_the_login_is_part_of_the_key():
    """A login saved by another process (the command line's `login`) is another key, also
    before this process forgets anything."""
    from openexpress.auth import store

    client = make_client([{"unitid": UNIT}])
    store.save(store.ConnectAuth("one", "one-secret"))
    service.require_account_device(client, UNIT)
    store._path().write_text(store._path().read_text() + " ")  # written again elsewhere
    service.require_account_device(client, UNIT)
    assert client.http.paths() == [DEVICES_PATH, DEVICES_PATH]


class SlowList(Account):
    """The account's list asked first waits until let go, with what the account said then."""

    def __init__(self):
        import threading

        super().__init__()
        self.held, self.go, self.slow = threading.Event(), threading.Event(), True

    def request(self, method, url, **kw):
        answer = super().request(method, url, **kw)
        if self.slow and method == "GET" and url.split("?")[0].endswith(DEVICES_PATH):
            self.slow = False
            self.held.set()
            assert self.go.wait(5)
        return answer


def test_a_change_of_the_primary_wearable_takes_no_list_asked_before_it(made_up_device):
    """Another task of the window reads the account's list just before the change: the
    change does not wait for that request, its check and read-back ask anew, and the older
    list, coming later, does not take the place of the one read back."""
    import threading

    account = SlowList()
    client = account.client()
    before = threading.Thread(target=lambda: service.require_account_device(client, UNIT))
    before.start()
    assert account.held.wait(5)
    changed, done = [], threading.Event()

    def change():
        try:
            changed.append(service.set_primary_wearable(client, made_up_device, apply=True))
        except Exception as e:  # noqa: BLE001 - checked below
            changed.append(e)
        finally:
            done.set()

    worker = threading.Thread(target=change)
    worker.start()
    try:
        assert done.wait(3), "the change waited for a list asked before it"
    finally:
        account.go.set()
        before.join(5)
        worker.join(5)
    assert not isinstance(changed[0], Exception), changed[0]
    account.sent.clear()
    assert service.primary_wearable_status(client, made_up_device).is_this_device
    assert account.sent == []  # the list read back, kept: not the older one
