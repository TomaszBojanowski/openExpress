"""The progress of Connect IQ apps, in the Apps page and during a sync: each app by its name
and which of how many ("Downloading Face It · 1 of 2", "Writing Face It to the device · 1 of
2"); never the name of a file on the device. All apps and devices are made up."""
from test_ciq_music import (  # noqa: F401 - fixtures
    FACE_MESSAGE,
    OTHER_MESSAGE,
    CountingClient,
    apps_cli,
    hidden_apps,
    iq_device,
)

from openexpress import ciq, i18n
from openexpress.progress import Step

NB = " "


def _no_file_names(seen):
    return not [m for m in seen if ".prg" in m.lower() or "GARMIN/" in m.upper()]


def test_each_app_is_downloaded_and_written_by_its_name(iq_device):
    st, dev = iq_device
    seen = []
    rep = ciq.install_queued(dev, st, CountingClient(), [FACE_MESSAGE, OTHER_MESSAGE],
                             known={}, progress=seen.append)
    assert rep.installed == ["Face It", "Other"]
    assert all(isinstance(m, Step) for m in seen)
    assert [str(m) for m in seen] == [
        f"Downloading Face It{NB}· 1 of 2", f"Writing Face It to the device{NB}· 1 of 2",
        f"Downloading Other{NB}· 2 of 2", f"Writing Other to the device{NB}· 2 of 2"]
    assert _no_file_names(seen)


def test_an_app_written_over_ptp_is_told_by_its_name(hidden_apps):
    st, dev, _fake = hidden_apps
    seen = []
    rep = ciq.install_queued(dev, st, CountingClient(), [FACE_MESSAGE], known={},
                             progress=seen.append, direct=True)
    assert rep.installed == ["Face It"]
    assert [str(m) for m in seen] == [f"Downloading Face It{NB}· 1 of 1",
                                      f"Writing Face It to the device{NB}· 1 of 1"]


def test_app_progress_in_polish(iq_device):
    st, dev = iq_device
    seen = []
    i18n.setup("pl")
    try:
        ciq.install_queued(dev, st, CountingClient(), [FACE_MESSAGE], known={},
                           progress=seen.append)
    finally:
        i18n.setup("en")
    assert [str(m) for m in seen] == [f"Pobieranie Face It{NB}· 1 z{NB}1",
                                      f"Zapisywanie Face It na urządzenie{NB}· 1 z{NB}1"]


def test_the_command_line_tells_each_app_once(iq_device, device_root, apps_cli, monkeypatch,
                                              capsys):
    """Not on a terminal: a line when a step of another app begins, never a file."""
    from test_ciq_music import FACE, FakeStore, store_app

    from openexpress.cli import main

    monkeypatch.setattr(FakeStore, "account_apps",
                        lambda self, device, locale: [store_app(FACE, "Face It")])
    assert main(["--path", str(device_root), "apps", "install", "Face It", "--apply"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert [line for line in out if line.startswith(("  Downloading", "  Writing"))] == [
        "  Downloading Face It", "  Writing Face It to the device"]
    assert _no_file_names(out)
