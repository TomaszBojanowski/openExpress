"""--device-uri, --path and --no-wait are taken after the command too, as the message that names
the devices to choose from says it ("choose one with --device-uri"): for every command that
opens a device, also after a command of a command (maps install); given before the command,
as always. A command that opens no device takes none of them after it. Made-up addresses."""

import pytest

from openexpress import cli
from openexpress.device import storage

URI = "mtp://Made_Up_Device_1/"


def parsed(*argv):
    return cli.build_parser().parse_args(list(argv))


@pytest.mark.parametrize("argv", [
    ["sync", "--device-uri", URI],
    ["--device-uri", URI, "sync"],
    ["sync", "--apply", f"--device-uri={URI}"],
    ["maps", "install", "Made-up map", "--device-uri", URI],
    ["maps", "--device-uri", URI, "install", "Made-up map"],
    ["primary", "set", "--device-uri", URI, "--apply"],
    ["activities", "export", "made-up.fit", "--device-uri", URI],
    ["courses", "add", "made-up.gpx", "--device-uri", URI, "--apply"],
], ids=" ".join)
def test_the_device_uri_after_the_command_names_the_device(argv):
    assert parsed(*argv).device_uri == URI


def test_path_and_no_wait_after_the_command_too():
    args = parsed("files", "--path", "/made/up", "--no-wait")
    assert (args.path, args.no_wait) == ("/made/up", True)


def test_given_before_and_not_after_it_stays_and_left_out_it_has_its_default():
    args = parsed("--device-uri", URI, "--no-wait", "--path", "/made/up", "primary", "set")
    assert (args.device_uri, args.no_wait, args.path) == (URI, True, "/made/up")
    args = parsed("primary", "set")
    assert (args.device_uri, args.no_wait, args.path) == (None, False, None)


def test_every_command_that_opens_a_device_takes_it_after_it():
    for name in sorted(cli.DEVICE_COMMANDS):
        assert parsed(name, "--device-uri", URI).device_uri == URI, name


@pytest.mark.parametrize("command", ["login", "setup", "cache", "watch"])
def test_a_command_that_opens_no_device_takes_none_after_it(command, capsys):
    with pytest.raises(SystemExit) as stopped:
        parsed(command, "--device-uri", URI)
    assert stopped.value.code == 2 and "--device-uri" in capsys.readouterr().err


def test_the_command_line_opens_the_device_named_after_the_command(monkeypatch, capsys):
    from openexpress.device import gio_storage

    opened = []

    def open_device_storage(uri, wait=True, notify=None):
        opened.append((uri, wait))
        raise storage.DeviceError("made-up: not there")

    monkeypatch.setattr(gio_storage, "open_device_storage", open_device_storage)
    assert cli.main(["info", "--device-uri", URI, "--no-wait"]) == 1
    assert opened == [(URI, False)]
