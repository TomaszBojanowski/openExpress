"""Point 5 of package 3: a Connect IQ app whose version in the store asks for other
permissions than before ("permissionsChanged" of the store, with the list "permissions") is
installed or updated only after the user allows them. Garmin Express asks in a window of its
own before each such install and update; openExpress asks once for all of them in the window,
and the command line needs --allow-permissions. A sync installs only what already waits in the
queue (asked for by the phone, the store or openExpress) and never asks the store for updates,
so it asks nothing. All apps, answers and devices are made up."""

import dataclasses
import types

import pytest
from fakes import RunNow
from test_ciq_music import (
    FACE,
    STORE_ID,
    CountingClient,
    FakeStore,
    apps_cli,  # noqa: F401 - a fixture of these tests
    iq_device,  # noqa: F401 - a fixture of these tests
    queue_once_requested,
    store_app,
)

from openexpress import ciq


def flagged(app, *permissions):
    return dataclasses.replace(app, permissions_changed=True, permissions=tuple(permissions))


FACE_IT = flagged(store_app(FACE, "Face It"), "Positioning", "Communications")


def test_the_store_answer_keeps_the_list_of_permissions():
    answer = {"appid": str(FACE), "name": "Face It", "type": "watchface", "size": 10,
              "latestinternalversionnumber": 2, "latestversionname": "1.1",
              "permissionschanged": True, "permissions": ["Positioning", "Communications"]}
    app = ciq._store_app(answer)
    assert app.permissions_changed and app.permissions == ("Positioning", "Communications")
    # Read leniently: no list is none, another value is its text.
    assert ciq._store_app({**answer, "permissions": "Positioning"}).permissions == ()
    assert ciq._store_app({**answer, "permissions": [7, "Fit"]}).permissions == ("7", "Fit")
    assert ciq._store_app({k: v for k, v in answer.items() if k != "permissions"}
                          ).permissions == ()


def test_the_words_of_the_permissions():
    assert ciq.permission_text("Positioning") == "GPS location"
    assert ciq.permission_text("communications") == "the internet, through the phone"
    assert ciq.permission_text("SomethingNew") == "SomethingNew"  # not known: as it came
    assert ciq.permissions_text(FACE_IT) == (
        "Face It 1.0 needs these permissions:\n"
        "• GPS location\n"
        "• the internet, through the phone")
    assert ciq.permissions_text(flagged(store_app(FACE, "Face It"))) == (
        "Face It 1.0 needs other permissions than before; the Connect IQ store does not say "
        "which.")


def test_only_the_apps_with_new_permissions_need_consent():
    plain = store_app(STORE_ID, "Connect IQ Store", "watch-app")
    assert ciq.needs_consent([plain, FACE_IT]) == [FACE_IT]


def _client():
    client = CountingClient()
    client.device_messages = queue_once_requested
    return client


@pytest.fixture
def store():
    FakeStore.queued = []
    return FakeStore(None)


def test_an_app_with_new_permissions_is_not_requested_without_consent(iq_device, store):
    st, dev = iq_device
    rep = ciq.install_apps(dev, st, _client(), store, [FACE_IT])
    assert rep.needs_consent == ["Face It"]
    assert FakeStore.queued == [] and not rep.installed
    assert not st.exists("GARMIN/APPS/IN.BIN")


def test_allowed_it_is_installed_as_before(iq_device, store):
    st, dev = iq_device
    rep = ciq.install_apps(dev, st, _client(), store, [FACE_IT], allowed={FACE})
    assert rep.installed == ["Face It"] and rep.needs_consent == []
    assert FakeStore.queued == [["Face It"]]
    assert st.exists("GARMIN/APPS/IN.BIN")


def test_a_device_without_an_app_list_does_not_queue_it_either(iq_device, store):
    st, dev = iq_device
    st.delete("GARMIN/APPS/OUT.BIN")
    rep = ciq.install_apps(dev, st, _client(), store, [FACE_IT])
    assert rep.needs_consent == ["Face It"] and FakeStore.queued == []


@pytest.fixture
def cli_store(apps_cli, monkeypatch):
    """The store of the command line: Face It in the account, with new permissions."""
    monkeypatch.setattr(FakeStore, "account_apps", lambda self, device, locale: [FACE_IT])
    return apps_cli


def test_cli_install_with_new_permissions_needs_the_option(iq_device, device_root, cli_store,
                                                          capsys):
    from openexpress.cli import main

    st, _dev = iq_device
    assert main(["--path", str(device_root), "apps", "install", "Face It", "--apply"]) == 1
    out = capsys.readouterr().out
    assert "  • GPS location" in out.splitlines()
    assert ("  Face It: needs new permissions; nothing is done for it. Add "
            "--allow-permissions to allow them.") in out.splitlines()
    assert FakeStore.queued == [] and not st.exists("GARMIN/APPS/IN.BIN")


def test_cli_dry_run_says_so_too(iq_device, device_root, cli_store, capsys):
    from openexpress.cli import main

    assert main(["--path", str(device_root), "apps", "install", "Face It"]) == 1
    out = capsys.readouterr().out.splitlines()
    assert "  Face It 1.0 needs these permissions:" in out
    assert any("--allow-permissions" in line for line in out)


def test_cli_allow_permissions_installs(iq_device, device_root, cli_store, capsys):
    from openexpress.cli import main

    assert main(["--path", str(device_root), "apps", "install", "Face It", "--apply",
                 "--allow-permissions"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert "  Face It: needs new permissions; allowed with --allow-permissions." in out
    assert FakeStore.queued == [["Face It"]]


def test_cli_update_with_new_permissions_needs_the_option(iq_device, device_root, apps_cli,
                                                         monkeypatch, capsys):
    from openexpress.cli import main

    update = flagged(store_app(STORE_ID, "Connect IQ Store", "watch-app"), "Fit")
    monkeypatch.setattr(FakeStore, "updates", lambda self, device, installed, locale: [update])
    assert main(["--path", str(device_root), "apps", "update", "--apply"]) == 1
    assert "  • activity files (FIT)" in capsys.readouterr().out.splitlines()
    assert FakeStore.queued == []
    assert main(["--path", str(device_root), "apps", "update", "--apply",
                 "--allow-permissions"]) == 0
    assert FakeStore.queued == [["Connect IQ Store"]]


def test_cli_list_says_an_update_has_new_permissions(iq_device, device_root, apps_cli,
                                                    monkeypatch, capsys):
    from openexpress.cli import main

    update = dataclasses.replace(
        flagged(store_app(STORE_ID, "Connect IQ Store", "watch-app"), "Fit"), version_name="2.0")
    monkeypatch.setattr(FakeStore, "updates", lambda self, device, installed, locale: [update])
    assert main(["--path", str(device_root), "apps"]) == 0
    line = next(line for line in capsys.readouterr().out.splitlines()
                if "Connect IQ Store" in line)
    assert line.endswith("update available: 2.0 (permissions changed)")


def test_window_asks_before_installing_an_app_with_new_permissions():
    from openexpress.gui.app import MainWindow

    asked, installed = [], []
    win = types.SimpleNamespace(
        run_task=RunNow(), confirm_permissions=lambda apps, asking: asked.append(
            ([a.name for a in apps], [a.name for a in asking])),
        _install_apps=lambda apps, allowed=(): installed.append(([a.name for a in apps],
                                                                 set(allowed))))
    plain = store_app(STORE_ID, "Connect IQ Store", "watch-app")
    MainWindow.install_apps(win, [plain, FACE_IT])
    assert asked == [(["Connect IQ Store", "Face It"], ["Face It"])] and installed == []
    MainWindow.install_apps(win, [plain])  # nothing to allow: at once, as before
    assert installed == [(["Connect IQ Store"], set())]


def _question(monkeypatch, apps, asking):
    """The window's question, as the window makes it; nothing is shown."""
    from gi.repository import Adw

    from openexpress.gui.app import MainWindow

    shown, installed = [], []
    monkeypatch.setattr(Adw.AlertDialog, "present", lambda self, parent: shown.append(self))
    win = types.SimpleNamespace(_install_apps=lambda apps, allowed=(): installed.append(
        ([a.name for a in apps], set(allowed))))
    MainWindow.confirm_permissions(win, apps, asking)
    (dialog,) = shown
    return win, dialog, installed


def _answer(dialog, response):
    """As a click does it: "closed" first, then "response" (libadwaita 1.9)."""
    dialog.emit("closed")
    dialog.emit("response", response)


@pytest.mark.needs_display
def test_allow_installs_all_and_cancel_nothing(monkeypatch):
    plain = store_app(STORE_ID, "Connect IQ Store", "watch-app")
    _win, dialog, installed = _question(monkeypatch, [plain, FACE_IT], [FACE_IT])
    assert dialog.get_heading() == "Allow New Permissions?"
    assert "Face It 1.0 needs these permissions:\n• GPS location" in dialog.get_body()
    assert dialog.get_body().endswith("openExpress installs it only if you allow them.")
    assert [dialog.get_response_label(r) for r in ("cancel", "others", "allow")] == [
        "Cancel", "Skip These Apps", "Allow"]
    _answer(dialog, "allow")
    assert installed == [(["Connect IQ Store", "Face It"], {FACE})]
    _win, dialog, installed = _question(monkeypatch, [plain, FACE_IT], [FACE_IT])
    _answer(dialog, "others")
    assert installed == [(["Connect IQ Store"], set())]
    _win, dialog, installed = _question(monkeypatch, [plain, FACE_IT], [FACE_IT])
    _answer(dialog, "cancel")
    assert installed == []


@pytest.mark.needs_display
def test_one_question_for_several_apps_and_no_others_button_without_others(monkeypatch):
    other = flagged(store_app(STORE_ID, "Connect IQ Store", "watch-app"))
    _win, dialog, installed = _question(monkeypatch, [other, FACE_IT], [other, FACE_IT])
    assert dialog.get_heading() == "Allow New Permissions for 2 Apps?"
    assert dialog.get_body().endswith("openExpress installs them only if you allow them.")
    assert not dialog.has_response("others")
    _answer(dialog, "allow")
    assert installed == [(["Connect IQ Store", "Face It"], {STORE_ID, FACE})]


def test_a_sync_never_asks_the_store_for_updates(iq_device, monkeypatch):
    from openexpress import service

    class Store:
        def __init__(self, client):
            pass

        def account_apps(self, device, locale):
            return []

        def updates(self, *_a):
            raise AssertionError("a sync asked the store for updates")

    monkeypatch.setattr(ciq, "StoreApi", Store)
    st, dev = iq_device
    s = types.SimpleNamespace(device=dev, storage=st, client=_client(),
                              settings=types.SimpleNamespace(locale="en_US"))
    errors = []
    assert service._install_queued_apps(s, [], lambda m: None, errors) == []
    assert errors == []


@pytest.mark.needs_display
def test_the_question_in_polish(monkeypatch):
    from openexpress import i18n

    i18n.setup("pl")
    try:
        plain = store_app(STORE_ID, "Connect IQ Store", "watch-app")
        _win, dialog, _installed = _question(monkeypatch, [plain, FACE_IT], [FACE_IT])
        heading, body = dialog.get_heading(), dialog.get_body()
        labels = [dialog.get_response_label(r) for r in ("cancel", "others", "allow")]
        _win, several, _installed = _question(monkeypatch, [FACE_IT, FACE_IT], [FACE_IT, FACE_IT])
        several_heading = several.get_heading()
    finally:
        i18n.setup("en")
    assert heading == "Zezwolić na nowe uprawnienia?"
    assert body == ("Face It 1.0 potrzebuje tych uprawnień:\n"
                    "• lokalizacja GPS\n"
                    "• internet przez telefon\n\n"
                    "openExpress zainstaluje ją tylko wtedy, gdy na nie zezwolisz.")
    assert labels == ["Anuluj", "Pomiń te aplikacje", "Zezwól"]
    assert several_heading == "Zezwolić na nowe uprawnienia dla 2 aplikacji?"
