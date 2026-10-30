"""The Connect IQ store through the kept answers (answers): the Apps page read again a moment
later (Restore the order…) asks nothing; an install, a removal from the account and a sync
forget what it said; the icons already on disk are not asked for; `apps install --apply`
reads the queue once less. Counted on a made-up store; all apps and devices are made up."""
import types

import pytest
from fakes import UpdateServer
from test_ciq_music import apps_cli, iq_device  # noqa: F401 - fixtures

from openexpress import answers, ciq, service
from openexpress.auth.store import ConnectAuth
from openexpress.connect import GarminClient
from openexpress.settings import Consumer, Settings

UNIT = 3000000001
DEVICE = types.SimpleNamespace(unit_id=UNIT, part_number="006-B0000-00")


@pytest.fixture
def store():
    server = UpdateServer([])
    client = GarminClient(Settings(), Consumer("k", "s"), ConnectAuth("t", "ts"), server)
    client.verified_unit_id = UNIT
    return ciq.StoreApi(client), server


def _asked(server):
    return [url.rsplit("/", 1)[-1].split("?")[0] for _m, url, _p, _b in server.sent]


def _page(api):
    """What the Apps page asks the store for."""
    api.account_apps(DEVICE, "en-US")
    api.updates(DEVICE, [], "en-US")


def test_the_apps_page_read_again_a_moment_later_asks_nothing(store, monkeypatch):
    api, server = store
    clock = [1_800_000_000.0]
    monkeypatch.setattr(answers.ANSWERS, "clock", lambda: clock[0])
    _page(api)
    clock[0] += 60
    _page(api)  # Restore the order, a moment later
    clock[0] += 61
    _page(api)  # two minutes: asked anew
    assert _asked(server) == ["downloads", "updates", "downloads", "updates"]


@pytest.mark.parametrize("change", ["install", "forget"])
def test_an_install_or_a_removal_from_the_account_asks_anew(store, change):
    api, server = store
    _page(api)
    if change == "install":
        server.answer = [{"appid": "x", "status": 200}]
        api.queue_install(DEVICE, [])
        server.answer = []
    else:
        api.forget(DEVICE, [])
    _page(api)
    assert _asked(server) == ["downloads", "updates",
                              "installApps" if change == "install" else "removeInstalledApps",
                              "downloads", "updates"]


def test_a_sync_asks_anew(store):
    """After a sync's downloads (the queue may have brought apps)."""
    api, server = store
    _page(api)
    s = types.SimpleNamespace(device=DEVICE, storage=None, client=api.client)
    queue = types.SimpleNamespace(items=[], messages=[])
    service._apply_queue(s, queue, None, None, service.SyncSummary())
    _page(api)
    assert _asked(server) == ["downloads", "updates", "downloads", "updates"]


def test_another_device_keeps_its_answers(store):
    api, server = store
    _page(api)
    answers.forget_device((answers.STORE_APPS, answers.STORE_UPDATES), 3000000002)
    _page(api)
    assert _asked(server) == ["downloads", "updates"]


def test_the_icons_on_disk_are_not_asked_for(monkeypatch, xdg):
    from openexpress import paths
    from openexpress.gui.apps_page import AppsPage

    have, need = "11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222"
    images = paths.cache_dir() / "images"
    images.mkdir(parents=True)
    (images / f"app-{have}.png").write_bytes(b"\x89PNG made up")
    asked = []
    monkeypatch.setattr(ciq, "StoreApi", lambda client: types.SimpleNamespace(
        icon_urls=lambda ids: asked.append(list(ids)) or {}))
    win = types.SimpleNamespace(
        session=types.SimpleNamespace(logged_in=True, ownership_error=None,
                                      client=types.SimpleNamespace(http=None)),
        in_background=lambda work, done: asked.append(sorted(str(k) for k in work())))
    AppsPage._load_app_icons(win, {have: object(), need: object()})
    assert asked == [[need], [have]]  # only the missing one asked; the one on disk taken
    asked.clear()
    AppsPage._load_app_icons(win, {have: object()})
    assert asked == [[have]]  # all on disk: the store not asked


def test_apps_install_reads_the_queue_once_less(iq_device, device_root, apps_cli, monkeypatch,
                                                capsys):
    from test_ciq_music import FACE, FakeStore, store_app

    from openexpress.cli import main

    read = []
    real = apps_cli.device_messages
    apps_cli.device_messages = lambda unit_id: read.append(1) or real(unit_id)
    monkeypatch.setattr(FakeStore, "account_apps",
                        lambda self, device, locale: [store_app(FACE, "Face It")])
    assert main(["--path", str(device_root), "apps", "install", "Face It", "--apply"]) == 0
    # The command's read, and the one after the request (the queue changed): not a third.
    assert len(read) == 2

