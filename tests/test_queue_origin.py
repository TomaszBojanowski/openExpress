"""Each file written from Garmin Connect's queue is said with its maker and the time it was made,
as its FIT file_id gives them: another device of the account by its name, the open device as
"this device", Garmin Connect for the files it makes itself, another Garmin product by its
number; a file that is no FIT file has no maker. The serial number of file_id is never said.
Made-up files, products and names only."""

import logging
import time

from fakes import FitData, FitDefinition, SyncClient, fit_file

from openexpress import sync
from openexpress.device.storage import LocalStorage
from openexpress.device.xmldevice import parse

FIT_EPOCH_OFFSET = 631065600
ENUM, UINT16, UINT32, UINT32Z = 0x00, 0x84, 0x86, 0x8C
# file_id: type, manufacturer, product, serial number, time created
FILE_ID = FitDefinition(0, 0, ((0, ENUM), (1, UINT16), (2, UINT16), (3, UINT32Z), (4, UINT32)))
SERIAL = 3000000099
MADE = 1_100_000_000


def made_by(product: int) -> bytes:
    return fit_file(FILE_ID, FitData(0, (44, 1, product, SERIAL, MADE)))


# The queue: one file of each kind of maker.
FILES = {"other": made_by(4532), "own": made_by(4440), "connect": made_by(65534),
         "unknown": made_by(9999), "app": b"not a FIT file"}
MAKERS = sync.Makers(own=4440, names={4532: "Made-up Watch 7", 4440: "Made-up Edge 3"})


class Queue(SyncClient):
    def download_message(self, url, absolute):
        return FILES[url]


def items(dev):
    return sync.plan_downloads(dev, [
        {"messageid": n, "devicexmldatatype": "FIT_TYPE_6", "metadata": {"messageurl": key}}
        for n, key in enumerate(FILES, 1)])


def test_each_file_written_is_said_with_its_maker_and_when_it_was_made(device_root, caplog):
    st = LocalStorage(device_root)
    dev = parse(st.read("GARMIN/GarminDevice.xml"))
    with caplog.at_level(logging.INFO, logger="openexpress.sync"):
        report = sync.run_downloads(items(dev), st, Queue(), state=sync.SyncState(dev.unit_id),
                                    makers=MAKERS)
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(MADE + FIT_EPOCH_OFFSET))
    said = [r.getMessage() for r in caplog.records if r.getMessage().startswith("download")]
    assert len(report.written) == 5
    assert said == [
        f"download: FIT_TYPE_6 written, made by Made-up Watch 7 on {when}",
        f"download: FIT_TYPE_6 written, made by this device on {when}",
        f"download: FIT_TYPE_6 written, made by Garmin Connect on {when}",
        f"download: FIT_TYPE_6 written, made by Garmin product 9999 on {when}",
        "download: FIT_TYPE_6 written, maker not given",
        ("downloads: 5 written (Garmin Connect×1, Garmin product 9999×1, Made-up Watch 7×1, "
         "maker not given×1, this device×1), 0 empty, 0 failed, 0 not acknowledged")]
    assert str(SERIAL) not in caplog.text


def test_without_the_names_a_product_is_said_by_its_number(device_root, caplog):
    st = LocalStorage(device_root)
    dev = parse(st.read("GARMIN/GarminDevice.xml"))
    with caplog.at_level(logging.INFO, logger="openexpress.sync"):
        sync.run_downloads(items(dev)[:1], st, Queue(), state=sync.SyncState(dev.unit_id))
    assert "made by Garmin product 4532 on" in caplog.text


def test_the_maker_of_a_file_in_words():
    from openexpress import fit

    def words(data):
        try:
            described = fit.describe(data)
        except fit.FitError:
            described = None
        return MAKERS.words(described)

    assert [words(FILES[k]) for k in FILES] == [
        "Made-up Watch 7", "this device", "Garmin Connect", "Garmin product 9999", None]
    described = fit.describe(FILES["other"])
    assert (described.manufacturer, described.product) == (1, 4532)
    assert SERIAL not in vars(described).values()


def test_a_time_of_making_out_of_range_neither_stops_the_queue_nor_writes_the_file_twice(
        device_root, caplog):
    """A file_id whose time created no clock can show (a 64-bit field, made up): the file is
    written once, acknowledged, and said without its time."""
    huge = FitDefinition(0, 0, ((0, ENUM), (1, UINT16), (2, UINT16), (4, 0x8F)))
    data = fit_file(huge, FitData(0, (44, 1, 4532, 2 ** 62)))

    class Odd(SyncClient):
        def download_message(self, url, absolute):
            return data

    st = LocalStorage(device_root)
    dev = parse(st.read("GARMIN/GarminDevice.xml"))
    client = Odd()
    state = sync.SyncState(3000000099)
    with caplog.at_level(logging.INFO, logger="openexpress.sync"):
        report = sync.run_downloads(items(dev)[:1], st, client, state=state, makers=MAKERS)
    assert len(report.written) == 1 and client.acks == [1] and state.unacked() == set()
    assert "download: FIT_TYPE_6 written, made by Made-up Watch 7" in caplog.messages
