"""The app list waiting for the device (IN.BIN) is never left half written: a list cut short
may read as a shorter one, and the device would drop the apps it no longer names. Each write
is read back; when it fails, what was there before is written back (the list that waited, or
none); when even that fails, what is there is deleted and the error says so. The three
writers: an install over the mounted storage, the order restored from a copy, an app switched
on or off. All data is made up."""

import pytest
from test_ciq_music import (  # noqa: F401 - the fixtures
    FACE,
    PRELOADED,
    STORE_ID,
    FakeClient,
    iq_device,
    order_device,
)

from openexpress import ciq
from openexpress.device.storage import DeviceError, LocalStorage

IN_BIN = "GARMIN/APPS/IN.BIN"
FACE_MESSAGE = {"messageid": 7, "devicexmldatatype": "IQWatchFaces",
                "metadata": {"messageurl": "x", "messagename": "Face It",
                             "appdetails": {"id": str(FACE)}}}


class Breaking(LocalStorage):
    """A device whose writes of IN.BIN stop half way: the first `breaks` of them leave half the
    list under the name, as a cable that goes out would. `undeletable`: IN.BIN cannot be
    deleted either; `misread`: what is read back of it differs from what was written."""

    def __init__(self, root, breaks=1, undeletable=False, misread=False):
        super().__init__(root)
        self.breaks, self.undeletable, self.misread = breaks, undeletable, misread

    def _write(self, real_path, data):
        if real_path.upper().endswith("IN.BIN") and self.breaks:
            self.breaks -= 1
            (self.root / real_path).write_bytes(data[: len(data) // 2])
            raise DeviceError(f"Cannot write {real_path}: the device is gone")
        super()._write(real_path, data)

    def _read(self, real_path):
        data = super()._read(real_path)
        if self.misread and real_path.upper().endswith("IN.BIN"):
            return data[:-1]
        return data

    def _delete(self, real_path):
        if self.undeletable and real_path.upper().endswith("IN.BIN"):
            raise DeviceError(f"Cannot delete {real_path}")
        super()._delete(real_path)


def waiting(st) -> bytes | None:
    path = st.root / IN_BIN
    return path.read_bytes() if path.exists() else None


# --- an app switched on or off (set_enabled) ------------------------------------------------------

def test_a_switch_cut_short_leaves_no_list_when_none_waited(iq_device):
    st, dev = iq_device
    broken = Breaking(st.root)
    with pytest.raises(DeviceError, match="the device is gone"):
        ciq.set_enabled(dev, broken, PRELOADED, True)
    assert waiting(st) is None  # half a list deleted: the device changes nothing


def test_a_switch_cut_short_puts_back_the_list_that_waited(iq_device):
    """An earlier change waits for the device (a list it has not applied): the list is as it
    was before the failed write, so that change is not lost."""
    st, dev = iq_device
    ciq.set_enabled(dev, st, PRELOADED, True)  # waits for the device
    before = waiting(st)
    with pytest.raises(DeviceError, match="the device is gone"):
        ciq.set_enabled(dev, Breaking(st.root), STORE_ID, False)
    assert waiting(st) == before


def test_a_list_that_cannot_be_put_back_is_deleted_and_said(iq_device):
    """Writing back the list that waited fails too: it is deleted (no half list), and the
    error says that the earlier changes are lost."""
    st, dev = iq_device
    ciq.set_enabled(dev, st, PRELOADED, True)
    with pytest.raises(ciq.CiqError) as error:
        ciq.set_enabled(dev, Breaking(st.root, breaks=2), STORE_ID, False)
    assert waiting(st) is None
    assert str(error.value) == (
        "The app list for the device could not be written (Cannot write GARMIN/APPS/IN.BIN: "
        "the device is gone), nor the one that waited written back: it was deleted, so the "
        "changes of the apps that waited for the device are lost. Make them again.")


def test_a_half_list_that_cannot_be_deleted_is_said_before_unplugging(iq_device):
    st, dev = iq_device
    with pytest.raises(ciq.CiqError) as error:
        ciq.set_enabled(dev, Breaking(st.root, undeletable=True), PRELOADED, True)
    assert str(error.value) == (
        "The app list for the device could not be written (Cannot write GARMIN/APPS/IN.BIN: "
        "the device is gone), and what was written of it could not be deleted. Delete "
        "GARMIN/APPS/IN.BIN on the device before you unplug it (e.g. in Files): the device "
        "could read half a list and remove apps.")


def test_a_list_that_reads_back_otherwise_counts_as_not_written(iq_device):
    st, dev = iq_device
    with pytest.raises(ciq.CiqError, match="Write verification of GARMIN/APPS/IN.BIN failed"):
        ciq.set_enabled(dev, Breaking(st.root, breaks=0, misread=True), PRELOADED, True)
    assert waiting(st) is None


def test_a_switch_that_goes_well_is_read_back(iq_device):
    """As before: the list is written whole, and nothing else changes."""
    st, dev = iq_device
    ciq.set_enabled(dev, Breaking(st.root, breaks=0), PRELOADED, True)
    flags = {p[0]: p[2] for p in ciq.parse_in_bin(waiting(st))}
    assert flags == {STORE_ID: False, PRELOADED: False}


# --- the order restored from a copy (restore_order) ----------------------------------------------

def test_an_order_cut_short_leaves_no_list(order_device):
    """Nothing waits when the order is restored (it is refused otherwise): the half list goes."""
    st, dev, backups = order_device
    copy = ciq.load_app_list_copy(dev, backups, "03.04.05")
    out_before = (st.root / "GARMIN/APPS/OUT.BIN").read_bytes()
    with pytest.raises(DeviceError, match="the device is gone"):
        ciq.restore_order(dev, Breaking(st.root), copy.state, backups)
    assert waiting(st) is None
    assert (st.root / "GARMIN/APPS/OUT.BIN").read_bytes() == out_before


def test_an_order_half_written_that_cannot_be_deleted_is_said(order_device):
    st, dev, backups = order_device
    copy = ciq.load_app_list_copy(dev, backups, "03.04.05")
    with pytest.raises(ciq.CiqError, match="before you unplug it"):
        ciq.restore_order(dev, Breaking(st.root, undeletable=True), copy.state, backups)


# --- an install over the mounted storage (install_queued) -----------------------------------------

def test_an_install_whose_list_is_cut_short_takes_back_its_app(iq_device):
    """The app file is written, its list is not: the app file goes again, no half list stays,
    the app stays in the queue (not acknowledged)."""
    st, dev = iq_device
    client = FakeClient()
    with pytest.raises(ciq.CiqError, match="Cannot register the apps on the device"):
        ciq.install_queued(dev, Breaking(st.root), client, [FACE_MESSAGE], known={})
    assert waiting(st) is None
    media = st.root / "GARMIN/APPS/MEDIA"
    assert not media.exists() or list(media.iterdir()) == []
    assert client.acks == []


def test_an_install_puts_back_the_list_that_waited(iq_device):
    """A list waits (an app switched before); the install's list is cut short: the list
    that waited is there again."""
    st, dev = iq_device
    ciq.set_enabled(dev, st, PRELOADED, True)
    before = waiting(st)
    with pytest.raises(ciq.CiqError, match="Cannot register the apps on the device"):
        ciq.install_queued(dev, Breaking(st.root), FakeClient(), [FACE_MESSAGE], known={})
    assert waiting(st) == before
