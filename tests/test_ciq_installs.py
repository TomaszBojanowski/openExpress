"""Connect IQ: the app list, free slots, apps queued in Garmin Connect, files that do not read."""

import types
import uuid

import pytest
from test_ciq_music import (  # noqa: F401 - iq_device: a fixture
    FACE,
    PRELOADED,
    STORE_ID,
    FakeClient,
    iq_device,
)

from openexpress import ciq, proto
from openexpress.device.storage import LocalStorage


def iq_message(mid, app_id, name):
    return {"messageid": mid, "devicexmldatatype": "IQWatchFaces",
            "metadata": {"messageurl": "x", "messagename": name, "appdetails": {"id": str(app_id)}}}


def test_in_bin_only_apps_are_kept(iq_device):
    st, dev = iq_device
    pending = ciq.InstalledApp(FACE, 3, "", disabled=True)
    state = ciq.parse_out_bin(st.read("GARMIN/APPS/OUT.BIN"))
    st.write("GARMIN/APPS/IN.BIN", ciq.build_in_bin(state.apps + [pending]))
    state = ciq.read_installed(dev, st)
    app = state.find(FACE)
    assert app is not None and app.pending and app.disabled and app.app_type == 3
    assert app in state.store_apps
    ciq.set_enabled(dev, st, PRELOADED, True)
    ids = [p[0] for p in ciq.parse_in_bin(st.read("GARMIN/APPS/IN.BIN"))]
    assert ids == [STORE_ID, PRELOADED, FACE]


def test_pending_apps_count_in_slot_check(iq_device):
    st, dev = iq_device
    dev.iq_max_apps = 2  # Store app + one pending app
    st.write("GARMIN/APPS/IN.BIN", ciq.build_in_bin([ciq.InstalledApp(FACE, 3, "", False)]))
    other = uuid.UUID(int=1)
    rep = ciq.install_queued(dev, st, FakeClient(), [iq_message(1, other, "X")], known={})
    assert rep.installed == [] and rep.failed[0][0] == "X"


def app_list(slots, apps, pending=()):
    """What `read_installed` gives: the device reports `slots` free slots and lists `apps`;
    `pending` wait in IN.BIN and are not in OUT.BIN yet."""
    state = ciq.InstalledState(available_slots=slots, available_space=66_000_000)
    state.apps = [ciq.InstalledApp(app_id, 1, "app", False) for app_id in apps]
    state.apps += [ciq.InstalledApp(app_id, 1, "app", False, pending=True) for app_id in pending]
    return state


def limit(max_apps):
    return types.SimpleNamespace(iq_max_apps=max_apps)


def test_free_slots_come_from_the_device():
    # Like a Venu 2: one slot is taken by something the app list does not name.
    assert ciq._free_slots(limit(32), app_list(31, [PRELOADED])) == 31
    assert ciq._free_slots(limit(32), app_list(0, [PRELOADED])) == 0
    # No number in the app list: the declared limit less the store apps listed.
    assert ciq._free_slots(limit(32), app_list(None, [STORE_ID, PRELOADED])) == 31
    assert ciq._free_slots(limit(None), app_list(None, [STORE_ID])) is None
    assert ciq._free_slots(limit(None), app_list(5, [STORE_ID])) == 5


def test_free_slots_count_a_waiting_app_once():
    # Written earlier, not yet in OUT.BIN: the device's number does not count it.
    assert ciq._free_slots(limit(32), app_list(31, [PRELOADED], pending=[FACE])) == 30
    # The device took it: it is in OUT.BIN and in the device's number, not counted again.
    assert ciq._free_slots(limit(32), app_list(30, [PRELOADED, FACE])) == 30
    # Never below zero.
    assert ciq._free_slots(limit(32), app_list(0, [PRELOADED], pending=[FACE])) == 0


@pytest.mark.parametrize("waiting", range(4))
def test_free_slots_on_a_device_that_lists_all_its_store_apps(waiting):
    # Like a fēnix 8: 31 free slots of 32, the one taken is the store app in the list. The
    # result is what the count of listed store apps gave before.
    pending = [uuid.UUID(int=n) for n in range(1, waiting + 1)]
    state = app_list(31, [STORE_ID, PRELOADED], pending)
    assert ciq._free_slots(limit(32), state) == 32 - len(state.store_apps) == 31 - waiting


def install_with(monkeypatch, iq_device, state, names):
    st, dev = iq_device
    monkeypatch.setattr(ciq, "read_installed", lambda device, storage: state)
    messages = [iq_message(n, uuid.UUID(int=n), name) for n, name in enumerate(names, 1)]
    return ciq.install_queued(dev, st, FakeClient(), messages, known={})


def test_install_refuses_when_the_device_reports_no_free_slot(iq_device, monkeypatch):
    st, _dev = iq_device
    before = sorted(p.name for p in st.root.rglob("*"))
    # No store app in the list, and still full.
    rep = install_with(monkeypatch, iq_device, app_list(0, [PRELOADED]), ["X"])
    assert rep.installed == [] and rep.failed == [("X", "no free app slots on the device")]
    assert sorted(p.name for p in st.root.rglob("*")) == before


def test_install_stops_at_the_last_free_slot(iq_device, monkeypatch):
    rep = install_with(monkeypatch, iq_device, app_list(1, [PRELOADED]), ["A", "B"])
    assert rep.installed == ["A"]
    assert rep.failed == [("B", "no free app slots on the device")]


def test_install_counts_an_app_that_waits_for_the_device(iq_device, monkeypatch):
    rep = install_with(monkeypatch, iq_device, app_list(1, [PRELOADED], pending=[FACE]), ["A"])
    assert rep.installed == [] and rep.failed == [("A", "no free app slots on the device")]
    # The same app, taken by the device: its slot is in the device's number already.
    rep = install_with(monkeypatch, iq_device, app_list(1, [PRELOADED, FACE]), ["A"])
    assert rep.installed == ["A"] and rep.failed == []


class FailingAckClient(FakeClient):
    def ack_message(self, mid):
        if mid == 2:
            raise RuntimeError("network")
        super().ack_message(mid)


def test_install_queued_acks_after_in_bin_and_reports_ack_failure(iq_device):
    st, dev = iq_device
    a, b = uuid.UUID(int=1), uuid.UUID(int=2)
    client = FailingAckClient()
    rep = ciq.install_queued(dev, st, client, [iq_message(1, a, "A"), iq_message(2, b, "B")],
                             known={})
    assert rep.installed == ["A"] and [n for n, _e in rep.failed] == ["B"]
    assert client.acks == [1]
    ids = [p[0] for p in ciq.parse_in_bin(st.read("GARMIN/APPS/IN.BIN"))]
    assert ids[-2:] == [a, b]  # B is installed even though the ack failed


def test_install_queued_registers_written_apps_on_crash(iq_device):
    st, dev = iq_device
    a = uuid.UUID(int=1)

    class Crash(FakeClient):
        def download_message(self, url, absolute):
            if url == "boom":
                raise ciq.ApiError("server down")
            return b"PRG-DATA"

    client = Crash()
    msgs = [iq_message(1, a, "A"), iq_message(2, uuid.UUID(int=2), "B")]
    msgs[1]["metadata"]["messageurl"] = "boom"
    with pytest.raises(ciq.ApiError):
        ciq.install_queued(dev, st, client, msgs, known={})
    assert a in [p[0] for p in ciq.parse_in_bin(st.read("GARMIN/APPS/IN.BIN"))]
    assert client.acks == [1]


def test_install_queued_write_failure_removes_orphan(iq_device):
    st, dev = iq_device

    class ShortWrite(LocalStorage):
        def _write(self, real_path, data):
            super()._write(real_path, data[:-1] if real_path.endswith(".PRG") else data)

    st = ShortWrite(st.root)
    client = FakeClient()
    rep = ciq.install_queued(dev, st, client, [iq_message(1, FACE, "Face")], known={})
    assert rep.installed == [] and rep.failed and client.acks == []
    assert list((st.root / "GARMIN/APPS/MEDIA").iterdir()) == []
    assert not st.exists("GARMIN/APPS/IN.BIN")


def test_install_queued_total_space(iq_device):
    st, dev = iq_device
    # available space 12 bytes: one 8-byte app fits, two do not
    resp = proto.enc_varint(1, 12) + proto.enc_bytes(3, proto.enc_bytes(1, STORE_ID.bytes))
    (st.root / "GARMIN/APPS/OUT.BIN").write_bytes(proto.enc_bytes(3, proto.enc_bytes(2, resp)))
    assert ciq.read_installed(dev, st).available_space == 12
    rep = ciq.install_queued(dev, st, FakeClient(),
                             [iq_message(1, uuid.UUID(int=1), "A"),
                              iq_message(2, uuid.UUID(int=2), "B")],
                             known={})
    assert rep.installed == ["A"] and [n for n, _e in rep.failed] == ["B"]


def test_malformed_ciq_files():
    good = uuid.UUID(int=1)
    bad_entries = (
        proto.enc_bytes(1, proto.enc_varint(1, 5))  # id as varint
        + proto.enc_bytes(1, proto.enc_bytes(1, b"short"))
        + proto.enc_bytes(1, b"\xff")  # undecodable entry
        + proto.enc_bytes(1, proto.enc_bytes(1, good.bytes) + proto.enc_bytes(2, b"x"))
    )
    data = proto.enc_bytes(3, proto.enc_bytes(5, bad_entries))
    assert ciq.parse_in_bin(data) == [(good, 0, False, False)]
    assert ciq.parse_in_bin(b"\xff\xff\xff") == []
    assert ciq.parse_in_bin(proto.enc_varint(3, 1)) == []
    with pytest.raises(ciq.CiqError):
        ciq.parse_out_bin(b"\xff\xff\xff")
    with pytest.raises(ciq.CiqError):
        ciq.parse_out_bin(proto.enc_varint(3, 1))
    entry = proto.enc_bytes(3, proto.enc_bytes(1, good.bytes) + proto.enc_varint(3, 7))
    state = ciq.parse_out_bin(proto.enc_bytes(3, proto.enc_bytes(2, proto.enc_bytes(1, b"x")
                                                                    + entry)))
    assert state.available_space is None and state.apps[0].name == ""
