"""A device that declares Connect IQ but has no app list (GARMIN/APPS/OUT.BIN) yet, as no app
was ever installed on it (a fēnix 8 51 mm AMOLED): that is an empty list,
not an error. The Apps page and `openexpress apps` say "No Connect IQ apps on the device", the
apps of the account can be installed: they are queued in Garmin Connect, for the phone, and
nothing is written to the device (a list written without the device's own would name none of
its apps). Garmin Express does not install them either: it cannot read the apps of a device
without an app list. No copy of the app list, and no error. A list that is there but cannot
be read stays an error. All devices and apps are made up."""

import logging
import types

import pytest
from test_ciq_music import (  # noqa: F401 - fixtures
    FACE,
    FACE_MESSAGE,
    CountingClient,
    FakeStore,
    apps_cli,
    apps_files,
    damaged_out_bin,
    iq_device,
    store_app,
)

from openexpress import ciq, i18n
from openexpress.device.storage import DeviceError


@pytest.fixture
def no_list(iq_device, device_root):
    """The made-up watch with Connect IQ declared and no OUT.BIN."""
    st, dev = iq_device
    (device_root / "GARMIN/APPS/OUT.BIN").unlink()
    return st, dev


def test_no_app_list_is_an_empty_list(no_list, caplog):
    st, dev = no_list
    with caplog.at_level(logging.INFO, logger="openexpress"):
        state = ciq.read_installed(dev, st)
    assert state.apps == [] and state.no_app_list
    assert (f"apps: no OUT.BIN on the device ({dev.part_number}), treating as no apps"
            in caplog.messages)


def test_a_list_that_cannot_be_read_stays_an_error(iq_device, device_root):
    st, dev = iq_device
    (device_root / "GARMIN/APPS/OUT.BIN").write_bytes(b"\xff\xff not an app list")
    with pytest.raises(ciq.CiqError):
        ciq.read_installed(dev, st)


def test_a_read_that_fails_stays_an_error(iq_device, monkeypatch):
    """The file is there, the device does not give it (an MTP error): as before."""
    st, dev = iq_device

    def broken(path):
        raise DeviceError("libmtp: Could not get file")

    monkeypatch.setattr(st, "read", broken)
    with pytest.raises(DeviceError):
        ciq.read_installed(dev, st)


def test_no_copy_of_a_list_that_is_not_there(no_list, backups):
    st, dev = no_list
    assert ciq.save_app_list_copy(dev, st) is None
    assert not (backups / str(dev.unit_id)).exists()


def test_installing_from_the_account_queues_it_and_writes_nothing(no_list, apps_cli):
    st, dev = no_list
    before = apps_files(st)
    rep = ciq.install_apps(dev, st, apps_cli, FakeStore(apps_cli), [store_app(FACE, "Face It")])
    assert FakeStore.queued == [["Face It"]]  # in Garmin Connect's queue
    assert rep.for_phone == ["Face It"] and rep.installed == [] and rep.no_app_list
    assert apps_files(st) == before and apps_cli.downloads == 0 and apps_cli.acks == []


def test_a_sync_leaves_the_queued_apps_for_the_phone(no_list, apps_cli):
    st, dev = no_list
    rep = ciq.install_queued(dev, st, apps_cli, [FACE_MESSAGE], {})
    assert rep.for_phone == ["Face It"] and rep.failed == [] and rep.installed == []
    assert apps_files(st) == [] and apps_cli.downloads == 0 and apps_cli.acks == []


def test_changing_an_app_says_why_not(no_list):
    st, dev = no_list
    with pytest.raises(ciq.CiqError) as refused:
        ciq.set_enabled(dev, st, FACE, True)
    # Only the phone is named: Garmin Express cannot install the first app on such a device
    # either, as it cannot read the apps of a device without an app list.
    assert str(refused.value) == (
        "The device has no Connect IQ app list yet (no app was ever installed on it), so "
        "nothing on it can be changed. New apps wait in the queue in Garmin Connect. The phone "
        "installs them, and once the device shows its app list, openExpress can too.")


def test_why_not_in_polish(no_list):
    st, dev = no_list
    i18n.setup("pl")
    try:
        with pytest.raises(ciq.CiqError) as refused:
            ciq.set_enabled(dev, st, FACE, True)
    finally:
        i18n.setup("en")
    # Hard spaces after the one-letter words (tools/po-hard-spaces.py).
    assert str(refused.value) == (
        "Urządzenie nie ma jeszcze listy aplikacji Connect IQ (nie zainstalowano na nim żadnej "
        "aplikacji), więc nie można na nim niczego zmienić. Nowe aplikacje czekają "
        "w\u00a0kolejce Garmin Connect. Zainstaluje je telefon, a\u00a0gdy urządzenie będzie już "
        "miało listę aplikacji, zrobi to także openExpress.")


# --- the command line -----------------------------------------------------------------------


def test_cli_apps_says_there_are_none_and_lists_the_account(no_list, device_root, apps_cli,
                                                           capsys):
    from openexpress.cli import main

    assert main(["--path", str(device_root), "apps"]) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0] == "No Connect IQ apps on the device"
    assert "In your Garmin account but not on the device:" in out and "Face It" in out
    assert "File not found" not in out


def test_cli_apps_in_polish(no_list, device_root, apps_cli, capsys):
    from openexpress.cli import main

    try:
        assert main(["--lang", "pl", "--path", str(device_root), "apps"]) == 0
    finally:
        i18n.setup("en")
    assert capsys.readouterr().out.splitlines()[0] == "Brak aplikacji Connect IQ na urządzeniu"


def test_cli_install_queues_it(no_list, device_root, apps_cli, capsys):
    from openexpress.cli import main

    st, _dev = no_list
    assert main(["--path", str(device_root), "apps", "install", "Face It"]) == 0
    out = capsys.readouterr().out
    assert "Dry run. Add --apply." in out and "IN.BIN" not in out and FakeStore.queued == []
    assert main(["--path", str(device_root), "apps", "install", "Face It", "--apply"]) == 0
    out = capsys.readouterr().out
    assert FakeStore.queued == [["Face It"]] and apps_files(st) == []
    assert "Face It" in out and "has not provided its Connect IQ app list yet" in out


def test_cli_with_an_unreadable_list_is_as_before(iq_device, device_root, apps_cli, capsys):
    from openexpress.cli import main

    damaged_out_bin(device_root, "truncated")
    assert main(["--path", str(device_root), "apps", "install", "Face It", "--apply"]) == 1
    assert "1 entry that cannot be read" in capsys.readouterr().err


# --- the Apps page --------------------------------------------------------------------------


def _apps_page(state, account):
    """The Apps page of a window that is never shown, filled from `state` and `account`."""
    import inspect

    from test_fixes_ui import page_window

    from openexpress.gui.app import MainWindow

    win, section = page_window("apps")
    device = types.SimpleNamespace(unit_id=3000000001, software_version_str="23.39",
                                   description="fēnix 8 - 51mm, AMOLED")
    win.session = types.SimpleNamespace(device=device, logged_in=True, ownership_error=None)
    win._updated_apps, win.installed = set(), []
    for name in ("_updates_to_offer", "_apps_space", "_waiting_note", "_app_icon",
                 "_order_blocked", "_store_type_name", "_search_filter", "show_apps"):
        member = inspect.getattr_static(MainWindow, name)
        setattr(win, name, getattr(MainWindow, name) if isinstance(member, staticmethod)
                else types.MethodType(getattr(MainWindow, name), win))
    win._load_app_icons = lambda icons: None
    win.install_apps = win.installed.append
    for name in ("toggle_app", "confirm_remove_app", "choose_order"):
        setattr(win, name, lambda *a, **kw: None)
    win.show_apps((state, account, []))
    return win, section


def _groups(section):
    from gi.repository import Adw

    found = {}
    for g in section.groups:
        rows, stack = [], [g]
        while stack:
            w = stack.pop()
            if isinstance(w, Adw.ActionRow):
                rows.append(w)
            child = w.get_first_child()
            while child is not None:
                stack.append(child)
                child = child.get_next_sibling()
        found[g.get_title()] = (g.get_description(), rows)
    return found


@pytest.mark.needs_display
def test_the_apps_page_says_there_are_none_and_offers_the_account(no_list):
    from gi.repository import Gtk

    st, dev = no_list
    state = ciq.read_installed(dev, st)
    win, section = _apps_page(state, [store_app(FACE, "Face It")])
    groups = _groups(section)
    assert groups["On the device"][0] == "No Connect IQ apps on the device"
    (row,) = groups["In your Garmin account"][1]
    assert row.get_title() == "Face It"
    (install,) = [w for w in _descendants(row) if isinstance(w, Gtk.Button)]
    install.emit("clicked")
    assert [[a.name for a in apps] for apps in win.installed] == [["Face It"]]
    assert "Built-in activities and apps" not in groups  # none known without the list


@pytest.mark.needs_display
def test_the_apps_page_in_polish(no_list):
    st, dev = no_list
    i18n.setup("pl")
    try:
        state = ciq.read_installed(dev, st)
        _win, section = _apps_page(state, [])
        groups = _groups(section)
    finally:
        i18n.setup("en")
    assert groups["Na urządzeniu"][0] == "Brak aplikacji Connect IQ na urządzeniu"


def test_the_window_says_why_an_app_was_only_queued():
    from openexpress.gui.app import MainWindow

    text = MainWindow._for_phone_text(["Face It"], no_app_list=True)
    assert text == ("Face It: the device has not provided its Connect IQ app list yet. The app "
                    "waits in the queue in Garmin Connect. Once the device shows its app list, "
                    "open the Apps page and press Install again; the phone may install it "
                    "earlier.")


def test_the_words_of_an_app_only_queued_in_polish():
    i18n.setup("pl")
    try:
        text = ciq.no_app_list_text(["Face It"])
    finally:
        i18n.setup("en")
    # Hard spaces after the one-letter words (tools/po-hard-spaces.py).
    assert text == ("Face It: urządzenie nie udostępniło jeszcze listy aplikacji Connect IQ. "
                    "Aplikacja czeka w\u00a0kolejce Garmin Connect. Gdy urządzenie pokaże listę "
                    "aplikacji, otwórz stronę Aplikacje i\u00a0ponownie kliknij Zainstaluj; "
                    "telefon może zainstalować ją wcześniej.")


def _descendants(widget):
    child = widget.get_first_child()
    while child is not None:
        yield child
        yield from _descendants(child)
        child = child.get_next_sibling()
