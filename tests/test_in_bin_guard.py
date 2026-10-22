"""A damaged app list waiting for the device (IN.BIN) is never replaced by one built from it:
read as empty, it would leave out every app from the store, which the device may then
remove (review of 22.10.2026, point 2). set_enabled and an install over the mounted storage
refuse as the other writers of the list do, with the same words. All data is made up."""

import uuid

import pytest
from test_ciq_music import (  # noqa: F401 - the fixtures
    FACE,
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
    assert FACE  # (the fixture's own face is not part of this test)
