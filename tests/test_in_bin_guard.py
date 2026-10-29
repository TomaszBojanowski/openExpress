"""A damaged app list waiting for the device (IN.BIN) is never replaced by one built from it:
read as empty, it would leave out every app from the store, which the device may then
remove. set_enabled and an install over the mounted storage refuse as the other writers of
the list do, with the same words. All data is made up."""

import uuid

import pytest
from test_ciq_music import (  # noqa: F401 - the fixtures
    PRELOADED,
    STORE_ID,
    FakeClient,
    apps_cli,
    iq_device,
    out_bin,
)

from openexpress import ciq, proto
from openexpress.cli import main

OUR_APP = uuid.UUID("a1b2c3d4-0000-4000-8000-00000000abcd")  # a store app on the device
NEW_APP = uuid.UUID("a1b2c3d4-0000-4000-8000-00000000beef")
WAITING = "the device has not applied earlier changes yet"
IN_BIN = "GARMIN/APPS/IN.BIN"


@pytest.fixture
def device(iq_device):
    st, dev = iq_device
    (st.root / "GARMIN/APPS/OUT.BIN").write_bytes(out_bin([
        (STORE_ID, 1, "Connect IQ Store", False, "DADE7E4"),
        (PRELOADED, 8, "Softball", True, ""),
        (OUR_APP, 1, "Made-up Weather", False, "ABCD1234"),
    ]))
    return st, dev


def _entry(app_id, app_type, disabled=False):
    return proto.enc_bytes(1, proto.enc_bytes(1, app_id.bytes) + proto.enc_varint(2, app_type)
                           + proto.enc_varint(3, disabled) + proto.enc_varint(4, False))


DAMAGED = {
    "not a list": b"\xff\xfe made-up garbage \x00\x01",
    "no entries": proto.enc_bytes(3, proto.enc_bytes(5, b"")),
    "an entry cut": proto.enc_bytes(3, proto.enc_bytes(5, _entry(STORE_ID, 1)
                                                       + proto.enc_bytes(1, b"\x0a\x10\x01"))),
}


@pytest.mark.parametrize("kind", DAMAGED)
def test_a_damaged_waiting_list_is_not_replaced_when_an_app_is_switched(device, kind):
    st, dev = device
    (st.root / IN_BIN).write_bytes(DAMAGED[kind])
    with pytest.raises(ciq.CiqError, match=WAITING):
        ciq.set_enabled(dev, st, PRELOADED, True)
    assert (st.root / IN_BIN).read_bytes() == DAMAGED[kind]  # untouched


def test_a_waiting_list_that_reads_well_is_still_changed(device):
    """As before: a switch before the device applied the list goes into that list."""
    st, dev = device
    ciq.set_enabled(dev, st, PRELOADED, True)
    ciq.set_enabled(dev, st, OUR_APP, False)  # the list waits: read, changed, written again
    flags = {p[0]: p[2] for p in ciq.parse_in_bin(st.read(IN_BIN))}
    assert flags == {STORE_ID: False, PRELOADED: False, OUR_APP: True}


def test_the_command_line_refuses_with_exit_code_1(device, apps_cli, capsys, device_root):
    st, _dev = device
    (st.root / IN_BIN).write_bytes(DAMAGED["not a list"])
    assert main(["--path", str(device_root), "apps", "enable", "Softball", "--apply"]) == 1
    assert WAITING in capsys.readouterr().err
    assert (st.root / IN_BIN).read_bytes() == DAMAGED["not a list"]


def test_an_install_over_the_mounted_storage_leaves_a_damaged_list_alone(device):
    """Nothing is written (no app file, no list) and the app stays in the queue."""
    st, dev = device
    (st.root / IN_BIN).write_bytes(DAMAGED["not a list"])
    messages = [{"messageid": 7, "devicexmldatatype": "IQWatchFaces",
                 "metadata": {"messageurl": "x", "messagename": "Made-up Face",
                              "appdetails": {"id": str(NEW_APP)}}}]
    client = FakeClient()
    with pytest.raises(ciq.CiqError, match=WAITING):
        ciq.install_queued(dev, st, client, messages, known={})
    assert (st.root / IN_BIN).read_bytes() == DAMAGED["not a list"]
    media = st.root / "GARMIN/APPS/MEDIA"
    assert not media.exists() or list(media.iterdir()) == []
    assert client.acks == []


# --- nothing is requested in Garmin Connect for an install that would be refused -------------

FACE_IT = uuid.UUID("a1b2c3d4-0000-4000-8000-00000000face")


def _store_app(app_id, name):
    return ciq.StoreApp(app_id, name, "dev", "watchface", 1000, 5, "1.0", False)


class Requests:
    """ciq.StoreApi for install_apps: what was requested in Garmin Connect is noted."""

    def __init__(self):
        self.requested = []

    def queue_install(self, device, apps):
        self.requested += [a.app_id for a in apps]


def _client():
    client = FakeClient()
    client.device_messages = lambda unit_id: []
    return client


@pytest.mark.parametrize("kind", DAMAGED)
def test_an_install_requests_nothing_while_a_damaged_list_waits(device, kind):
    """The install over the mounted storage would refuse (the list cannot be read whole):
    the app is not requested in Garmin Connect either, so it does not stay in the queue
    of the account."""
    st, dev = device
    (st.root / IN_BIN).write_bytes(DAMAGED[kind])
    store = Requests()
    rep = ciq.install_apps(dev, st, _client(), store, [_store_app(FACE_IT, "Made-up Face")])
    assert store.requested == []
    assert [(name, error.split(";")[0]) for name, error in rep.failed] == [
        ("Made-up Face", WAITING)]
    assert (st.root / IN_BIN).read_bytes() == DAMAGED[kind]


def test_an_install_over_ptp_requests_nothing_while_any_list_waits(monkeypatch, iq_device,
                                                                   device_root, xdg):
    """Over a PTP session nothing is written while a list waits, readable or not: the app
    is not requested in Garmin Connect either."""
    from ptp_fake import FakeDevice
    from test_ciq_music import HidingStorage

    _st, dev = iq_device
    (device_root / "GARMIN/APPS/MEDIA").rmdir()
    fake = FakeDevice()
    fake.add("GARMIN/Apps/OUT.BIN", (device_root / "GARMIN/APPS/OUT.BIN").read_bytes())
    fake.add("GARMIN/Apps/Media", hidden=True)
    st = HidingStorage(device_root, fake)
    assert ciq.usb_dir_state(dev, st, "IQWatchFaces") == ciq.DIRECT
    ciq.set_enabled(dev, st, PRELOADED, True)  # a list that reads well waits for the device
    store = Requests()
    rep = ciq.install_apps(dev, st, _client(), store, [_store_app(FACE_IT, "Made-up Face")])
    assert store.requested == [] and st.sessions == 0
    assert [(name, error.split(";")[0]) for name, error in rep.failed] == [
        ("Made-up Face", WAITING)]


def test_the_dry_run_of_an_install_says_the_list_is_damaged(device, apps_cli, capsys,
                                                            device_root, monkeypatch):
    """Not "every app on the device is kept": the install would be refused, and it says so
    (exit code 1, as for another refusal of the dry run)."""
    from test_ciq_music import FakeStore

    st, _dev = device
    (st.root / IN_BIN).write_bytes(DAMAGED["not a list"])
    monkeypatch.setattr(FakeStore, "account_apps",
                        lambda self, device, locale: [_store_app(FACE_IT, "Made-up Face")])
    assert main(["--path", str(device_root), "apps", "install", "Made-up Face"]) == 1
    out = capsys.readouterr().out
    assert f"ERROR Made-up Face: {WAITING}" in out
    assert "every app on the device is kept" not in out


@pytest.mark.parametrize("command", ["enable", "disable"])
def test_the_dry_run_of_a_switch_says_the_list_is_damaged(device, apps_cli, capsys,
                                                          device_root, command):
    st, _dev = device
    (st.root / IN_BIN).write_bytes(DAMAGED["not a list"])
    assert main(["--path", str(device_root), "apps", command, "Softball"]) == 1
    assert WAITING in capsys.readouterr().err
    assert (st.root / IN_BIN).read_bytes() == DAMAGED["not a list"]
