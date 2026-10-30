"""The progress of a sync: what is done, to what kind of data and how far ("Uploading
activities · 1 of 4", "Downloading from Garmin Connect · 1 of 2"), one count for all the
uploads; never the name of a file. All devices, files and answers are made up."""
from fakes import SyncClient
from test_sync import UPLOAD_CONFIG, _setup

from openexpress import sync
from openexpress.progress import Step

NB = " "


def test_the_uploads_say_the_kind_of_data_and_one_count(device_root, tmp_path):
    st, dev, state = _setup(device_root, tmp_path)
    plan = sync.plan_uploads(dev, st, UPLOAD_CONFIG, state)
    seen = []
    sync.run_uploads(plan, dev, st, SyncClient(), state, progress=seen.append,
                     wait_for_processing=False)
    assert [str(m) for m in seen if isinstance(m, Step)] == [
        f"Uploading activities{NB}· 1 of 4", f"Uploading activities{NB}· 2 of 4",
        f"Uploading other data (FIT_TYPE_2){NB}· 3 of 4", f"Uploading monitoring{NB}· 4 of 4"]
    assert not [m for m in seen if ".fit" in m.lower()]


def test_the_downloads_count_what_comes_from_garmin_connect(device_root, tmp_path):
    st, dev, state = _setup(device_root, tmp_path)
    items = sync.plan_downloads(dev, [
        {"messageid": n, "messagetype": "file", "devicexmldatatype": "FIT_TYPE_6",
         "metadata": {"messageurl": f"course/{n}", "messagename": f"Route {n}"}}
        for n in (7, 8)])
    seen = []
    rep = sync.run_downloads(items, st, SyncClient(), state=state, progress=seen.append)
    assert len(rep.written) == 2
    assert [str(m) for m in seen] == [f"Downloading from Garmin Connect{NB}· 1 of 2",
                                      f"Downloading from Garmin Connect{NB}· 2 of 2"]
