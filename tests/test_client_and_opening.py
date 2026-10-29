"""How the command line and the window make their client of Garmin Connect and open a device.

The command line stops with a message where something it needs is missing (the application
key; the login, when the command needs it); the window goes on without it and says "log in"
where it matters. Opening a device hides what is private about it in the log and remembers a
device found now, never a folder given by hand. All keys, logins and devices are made up.
"""

import argparse
import dataclasses
import logging

import pytest

from openexpress import applog, cli, known_devices, service
from openexpress.auth import store
from openexpress.device.storage import LocalStorage, UmsStorage
from openexpress.device.xmldevice import parse
from openexpress.settings import Consumer, Settings, SetupError, consumer_path, save_consumer

NOT_SET_UP = "openExpress is not set up yet."
NOT_LOGGED_IN = "Not logged in. Run: openexpress login"
DAMAGED_LOGIN = "The saved login is damaged; please log in again."
OTHER_ENVIRONMENT = ("The token is for environment china, but the configured one is "
                     "production. Please log in again.")
SERIAL = "MADE-UP-SERIAL-7"
UUID = "ABCD-1234"


def key():
    save_consumer(Consumer("made-up-key", "made-up-secret"))


def login(environment="production"):
    store.save(store.ConnectAuth("made-up-token", "made-up-token-secret", environment))


def damage_login():
    login()
    store._path().write_text("{")


def damage_key():
    key()
    consumer_path().write_text("{")


# --- the command line's client --------------------------------------------------------------


@pytest.mark.parametrize(("saved", "error", "text"), [
    ((), SetupError, NOT_SET_UP),
    ((login,), SetupError, NOT_SET_UP),  # the key is asked for before the login
    ((damage_key,), SetupError, "The saved client key in "),
    ((key,), store.NotLoggedIn, NOT_LOGGED_IN),
    ((key, damage_login), store.NotLoggedIn, DAMAGED_LOGIN),
    ((key, lambda: login("china")), SetupError, OTHER_ENVIRONMENT),
])
def test_the_command_line_with_the_login_stops_without_the_key_or_the_login(saved, error, text):
    for save in saved:
        save()
    with pytest.raises(error) as raised:
        cli.make_client(Settings(), need_auth=True)
    assert str(raised.value).startswith(text)


def test_the_command_line_with_the_login_has_the_key_and_the_login():
    key()
    login()
    client = cli.make_client(Settings(), need_auth=True)
    assert (client.consumer.key, client.consumer.secret) == ("made-up-key", "made-up-secret")
    assert (client.auth.token, client.auth.token_secret) == ("made-up-token",
                                                             "made-up-token-secret")


@pytest.mark.parametrize("saved", [(), (login,), (damage_login,), (lambda: login("china"),)])
def test_the_command_line_without_the_login_never_reads_it(saved):
    key()
    for save in saved:
        save()
    client = cli.make_client(Settings(), need_auth=False)
    assert client.auth is None and client.consumer.key == "made-up-key"


@pytest.mark.parametrize("saved", [(), (damage_key,)])
def test_the_command_line_without_the_login_still_needs_the_key(saved):
    for save in saved:
        save()
    with pytest.raises(SetupError):
        cli.make_client(Settings(), need_auth=False)


@pytest.mark.parametrize(("saved", "command", "text"), [
    ((login,), ["whoami", "--check"], NOT_SET_UP),
    ((key,), ["whoami"], NOT_LOGGED_IN),
    ((key, lambda: login("china")), ["whoami", "--check"], OTHER_ENVIRONMENT),
])
def test_the_command_line_says_what_is_missing_and_exits_with_1(capsys, saved, command, text):
    for save in saved:
        save()
    assert cli.main(command) == 1
    assert text in capsys.readouterr().err


# --- the window's client --------------------------------------------------------------------


@pytest.mark.parametrize(("saved", "has_key", "has_login"), [
    ((key, login), True, True),
    ((login,), False, True),
    ((damage_key, login), False, True),
    ((key,), True, False),
    ((key, damage_login), True, False),
    ((key, lambda: login("china")), True, False),
    ((), False, False),
])
def test_the_window_goes_on_without_the_key_or_the_login(saved, has_key, has_login):
    for save in saved:
        save()
    client = service.make_client(Settings())
    assert (client.consumer is not None) is has_key
    assert (client.auth is not None) is has_login
    if has_key:
        assert client.consumer.key == "made-up-key"
    if has_login:
        assert client.auth.token == "made-up-token"


# --- opening a device -----------------------------------------------------------------------


def unit_id(device_root):
    return parse((device_root / "GARMIN" / "GarminDevice.xml").read_bytes()).unit_id


def drive(device_root):
    """The made-up watch as a drive, with the USB serial and the UUID of its volume."""
    GarminUsb = pytest.importorskip("openexpress.device.gio_storage").GarminUsb

    return UmsStorage(device_root, device_root.as_uri() + "/",
                      usb=GarminUsb("4f42", 1, 9, serial=SERIAL, volume_uuid=UUID))


def found_now(monkeypatch, storage):
    """The device the command line and the window find connected now."""
    monkeypatch.setattr(service, "open_device_or_drive", lambda **kw: storage)


def args(path=None, command="info"):
    return argparse.Namespace(path=path, device_uri=None, no_wait=True, cmd=command,
                              apply=False)


def entry(unit):
    """What devices.json keeps of the device, without when it was seen and written."""
    kept = dataclasses.asdict(known_devices.load()[unit])
    kept["synced"].pop("at")
    return {k: v for k, v in kept.items() if not k.endswith("_seen")}


def written(log_file, unit):
    """The log after a line with each private value of the device in it."""
    logging.getLogger("openexpress.test").info(
        "private: %s %s %s %s %s %s", unit, f"{unit:x}", f"{unit:012X}", SERIAL,
        SERIAL.lower(), UUID.lower())
    return log_file.read_text()


@pytest.mark.parametrize("kind", ["mtp", "ums"])
def test_the_command_line_and_the_window_remember_a_device_alike(monkeypatch, device_root,
                                                                   kind):
    unit = unit_id(device_root)
    found_now(monkeypatch, drive(device_root) if kind == "ums" else LocalStorage(device_root))
    assert cli.main(["info"]) == 0
    by_the_command_line = entry(unit)
    known_devices.path().unlink()
    service.open_session(allow_ums=True)
    assert entry(unit) == by_the_command_line
    assert by_the_command_line["kind"] == kind
    keys = (by_the_command_line["usb_product"], by_the_command_line["usb_serial"],
            by_the_command_line["volume_uuid"])
    assert keys == (("4f42", SERIAL, UUID) if kind == "ums" else (None, None, None))
    assert (by_the_command_line["description"], by_the_command_line["part_number"]) == (
        "Test watch", "006-B0000-00")


def test_a_folder_given_by_hand_is_not_remembered(device_root):
    assert cli.main(["--path", str(device_root), "info"]) == 0
    service.open_session(storage=LocalStorage(device_root))
    assert not known_devices.path().exists()


@pytest.mark.parametrize("given", ["path", "mtp", "ums"])
def test_the_command_line_hides_the_device_in_the_log(monkeypatch, device_root, log_file,
                                                      given):
    unit = unit_id(device_root)
    applog.setup("cli")
    if given != "path":
        found_now(monkeypatch,
                  drive(device_root) if given == "ums" else LocalStorage(device_root))
    cli.open_device(args(path=str(device_root) if given == "path" else None))
    text = written(log_file, unit)
    hidden = "<serial> <serial> <volume>" if given == "ums" else (
        f"{SERIAL} {SERIAL.lower()} {UUID.lower()}")
    assert f"private: <unit> <unit> <unit> {hidden}" in text
    assert str(unit) not in text and f"{unit:x}" not in text.casefold()


@pytest.mark.parametrize("given", ["storage", "mtp", "ums"])
def test_the_window_hides_the_device_in_the_log(monkeypatch, device_root, log_file, given):
    unit = unit_id(device_root)
    applog.setup("cli")
    if given == "storage":
        service.open_session(storage=LocalStorage(device_root))
    else:
        found_now(monkeypatch,
                  drive(device_root) if given == "ums" else LocalStorage(device_root))
        service.open_session(allow_ums=True)
    text = written(log_file, unit)
    hidden = "<serial> <serial> <volume>" if given == "ums" else (
        f"{SERIAL} {SERIAL.lower()} {UUID.lower()}")
    assert f"private: <unit> <unit> <unit> {hidden}" in text


@pytest.mark.parametrize("given", ["path", "mtp"])
def test_the_command_line_says_nothing_of_drive_keys_for_a_device_not_a_drive(
        monkeypatch, device_root, caplog, given):
    if given == "mtp":
        found_now(monkeypatch, LocalStorage(device_root))
    with caplog.at_level(logging.INFO, logger="openexpress"):
        cli.open_device(args(path=str(device_root) if given == "path" else None))
    assert not any(m.startswith("device: this drive") for m in caplog.messages)


@pytest.mark.parametrize("given", ["storage", "mtp"])
def test_the_window_says_nothing_of_drive_keys_for_a_device_not_a_drive(
        monkeypatch, device_root, caplog, given):
    with caplog.at_level(logging.INFO, logger="openexpress"):
        if given == "storage":
            service.open_session(storage=LocalStorage(device_root))
        else:
            found_now(monkeypatch, LocalStorage(device_root))
            service.open_session(allow_ums=True)
    assert not any(m.startswith("device: this drive") for m in caplog.messages)


def test_the_window_says_which_keys_the_drive_gives_and_none_of_them(monkeypatch,
                                                                     device_root, caplog):
    found_now(monkeypatch, drive(device_root))
    with caplog.at_level(logging.INFO, logger="openexpress"):
        service.open_session(allow_ums=True)
    lines = [m for m in caplog.messages if m.startswith("device: this drive")]
    assert lines == ["device: this drive's USB serial is not its unit ID; its volume has a UUID"]
