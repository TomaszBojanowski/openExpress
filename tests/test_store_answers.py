"""Answers of the Connect IQ store and queue that are not what we expect (step 3 after the audit
of 0.7.9): an item of the store's list without a proper app ID is left out with one line in
the log, the others are shown (before, one such item failed the whole list, and the Apps
page with it); an app of the queue whose package is empty (204/404) is taken off the queue
and nothing is written, as Garmin Express does, now with a line in the log (before, without
a word). All answers are made up."""

import logging
import uuid

import pytest
from test_ciq_music import FACE, FakeClient, iq_device  # noqa: F401 - the fixture

from openexpress import ciq

GOOD = {"appid": str(FACE), "name": "Face It", "type": "WATCH_FACE", "size": 1000,
        "latestinternalversionnumber": 42, "latestversionname": "4.2"}


class Store(ciq.StoreApi):
    """The store's answer as given, with no request made."""

    def __init__(self, answer):
        self.answer = answer

    def _request(self, *args, **kw):
        return self.answer


class Device:
    unit_id = 1234
    part_number = "006-B0000-00"


@pytest.mark.parametrize("bad", [
    {"name": "No ID"},
    {"appid": "not an id", "name": "Bad ID"},
    {"appid": 7, "name": "A number"},
    "not an item",
], ids=["no ID", "not an ID", "a number", "not an item"])
def test_an_item_without_an_app_id_is_left_out(bad, caplog):
    with caplog.at_level(logging.WARNING, logger="openexpress.ciq"):
        apps = Store([GOOD, bad]).account_apps(Device(), "en_GB")
    assert [a.name for a in apps] == ["Face It"]
    assert [r.getMessage() for r in caplog.records] == [
        "apps: 1 item of the store's answer left out: no app ID"]


def test_the_updates_leave_it_out_too(caplog):
    with caplog.at_level(logging.WARNING, logger="openexpress.ciq"):
        apps = Store([{"name": "No ID"}, GOOD]).updates(Device(), [], "en_GB")
    assert [a.app_id for a in apps] == [FACE]
    assert len(caplog.records) == 1


def test_an_answer_of_good_items_says_nothing(caplog):
    with caplog.at_level(logging.WARNING, logger="openexpress.ciq"):
        assert len(Store([GOOD]).account_apps(Device(), "en_GB")) == 1
    assert caplog.records == []


class EmptyPackage(FakeClient):
    def download_message(self, url, absolute):
        return None  # 204 or 404: the client gives no content


def test_an_empty_package_is_taken_off_the_queue_with_a_line_in_the_log(iq_device, caplog):
    st, dev = iq_device
    messages = [{"messageid": 7, "devicexmldatatype": "IQWatchFaces",
                 "metadata": {"messageurl": "x", "messagename": "Face It",
                              "appdetails": {"id": str(FACE)}}}]
    client = EmptyPackage()
    with caplog.at_level(logging.INFO, logger="openexpress.ciq"):
        rep = ciq.install_queued(dev, st, client, messages, known={})
    assert client.acks == [7] and rep.installed == [] and rep.failed == []
    assert not (st.root / "GARMIN/APPS/MEDIA").exists() or not list(
        (st.root / "GARMIN/APPS/MEDIA").iterdir())
    assert not (st.root / "GARMIN/APPS/IN.BIN").exists()
    assert ("apps: Garmin Connect sent no file for Face It: taken off the queue, nothing "
            "written") in [r.getMessage() for r in caplog.records]


def test_the_app_id_of_a_good_item_is_read(caplog):
    (app,) = Store([GOOD]).account_apps(Device(), "en_GB")
    assert app.app_id == uuid.UUID(str(FACE))
