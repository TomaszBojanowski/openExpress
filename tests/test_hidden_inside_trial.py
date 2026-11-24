"""The trial on the command line (updates --install PART --hidden-folder-trial): an update whose
folder a watch hides inside GARMIN written there through openExpress's own USB session, the
file there before copied to this computer first, read back after, and put back once when the
new one is not written whole. A made-up watch whose GarminDevice.xml names Garmin/Prompts, which
its ordinary listing does not show and its own session does. All devices, files and answers
here are made up."""

import hashlib
import types

import pytest
from fakes import offered_update, ptp_opened, xml_with
from ptp_fake import FakeDevice

from openexpress import download, updates
from openexpress.cli import main
from openexpress.connect import GarminClient, lower_keys
from openexpress.device import ptp
from openexpress.device.storage import LocalStorage

SOFTWARE = b"made-up watch software"
META = b"made-up information about the voice prompts"
OLD_PROMPTS = b"made-up voice prompts of the version on the watch"
NEW_PROMPTS = b"made-up voice prompts, the version offered"

PROMPTS_COMPONENT = """  <UpdateFile><PartNumber>006-D0002-00</PartNumber><Version><Major>2</Major>
   <Minor>12</Minor></Version><Path>Garmin/Prompts</Path><FileName>prompt_bundle.hvm</FileName>
  </UpdateFile>
"""


def server_answer():
    return {"updates": [
        offered_update("006-B0000-00", "Made-up Watch", "Garmin", "GUPDATE.GSP", SOFTWARE,
                       primary=True),
        offered_update("006-D0002-00", "Made-up Prompt Bundle", "GARMIN\\PROMPTS",
                       "prompt_bundle.hvm", NEW_PROMPTS),
        # Its information, named by the server as the update itself.
        offered_update("006-D0002-00-META", "Made-up Prompt Bundle", "Garmin\\UpdateMetadata",
                       "006-D0002-00.upd", META),
    ]}


class _Watch(FakeDevice):
    """The watch's own session; `refused_sends`: which SendObject it refuses, counted from 1
    (the test file is the first, the new file the second, the put back the third);
    `unreadable`: a file it does not give; `steps`: what changed or read a file, in order."""

    def __init__(self):
        super().__init__()
        self.refused_sends, self.sends, self.unreadable, self.steps = set(), 0, None, []
        self.add("GARMIN/GarminDevice.xml", b"<Device/>")
        self.add("GARMIN/Prompts", hidden=True)
        self.add("GARMIN/Prompts/prompt_bundle.hvm", OLD_PROMPTS)

    def _handle(self, operation, params, data):
        if operation == ptp.DELETE_OBJECT and params[0] in self.objects:
            self.steps.append("delete")
        elif operation == ptp.SEND_OBJECT_INFO:
            self.steps.append("info")
        elif operation == ptp.SEND_OBJECT:
            self.sends += 1
            if self.sends in self.refused_sends:
                self.steps.append("send refused")
                return (ptp.GENERAL_ERROR,)
            self.steps.append("send")
        elif operation == ptp.GET_OBJECT and params[0] in self.objects:
            if params[0] == self.handle(self.unreadable or "-"):
                self.steps.append("read refused")
                return (ptp.ACCESS_DENIED,)
            self.steps.append("read")
        return super()._handle(operation, params, data)

    def names_in_prompts(self):
        return sorted(o.name for o in self.objects.values()
                      if o.parent == self.handle("GARMIN/Prompts"))

    def prompts(self):
        handle = self.handle("GARMIN/Prompts/prompt_bundle.hvm")
        return self.objects[handle].data if handle is not None else None


class _OverUsb(LocalStorage):
    """The made-up watch over MTP: ordinary MTP does not show Garmin/Prompts; `fake` answers
    a session of its own."""

    def __init__(self, root, fake):
        super().__init__(root)
        self.device_uri = "mtp://made-up-watch/"
        self.usb = types.SimpleNamespace(bus=1, address=7, usb_id="091e:0000")
        self.fake = fake

    def usb_blocked(self):
        return False

    def exclusive_ptp(self):
        return ptp_opened(self.fake)


@pytest.fixture
def watch(device_root):
    xml_with(device_root, PROMPTS_COMPONENT)
    fake = _Watch()
    return _OverUsb(device_root, fake), fake


@pytest.fixture
def downloads(monkeypatch, tmp_path):
    fetched = []
    content = {"GUPDATE.GSP": SOFTWARE, "prompt_bundle.hvm": NEW_PROMPTS, "006-D0002-00.upd": META}

    def fetch(urls, dest, size, md5, session=None, progress=None):
        fetched.append(dest.name)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content[dest.name])
        return dest

    monkeypatch.setattr(updates, "cache_path", lambda u: tmp_path / "cache" / u.filename)
    monkeypatch.setattr(download, "fetch", fetch)
    return fetched


PROBE = ["info", "send", "read", "delete"]  # the test file written, read back, deleted


def _prompts(storage):
    from openexpress import service

    device = service.read_device(storage, found_now=False)
    items = updates.offered(storage, lower_keys(server_answer()), device, forget_installed=False)
    (u,) = [u for u in items if u.part_number == "006-D0002-00"]
    assert u.hidden_inside == "Garmin/Prompts"
    return u


def test_without_usb_and_the_trial_it_is_refused_and_the_watch_untouched(watch, downloads):
    storage, fake = watch
    storage.usb_blocked = lambda: True  # the Flatpak without the permission
    with pytest.raises(updates.UpdateError):
        updates.install([_prompts(storage)], storage)
    assert (fake.steps, downloads, fake.prompts()) == ([], [], OLD_PROMPTS)


def test_the_trial_copies_the_file_there_then_writes_and_reads_back(watch, downloads, tmp_path):
    storage, fake = watch
    report = updates.install([_prompts(storage)], storage, hidden_trial=tmp_path / "kept")
    assert fake.steps == [*PROBE, "read", "delete", "info", "send", "read"]
    assert fake.prompts() == NEW_PROMPTS and fake.names_in_prompts() == ["prompt_bundle.hvm"]
    ((u, kept),) = report.kept
    assert kept.path == tmp_path / "kept" / "006-D0002-00-2.18-prompt_bundle.hvm"
    assert kept.path.read_bytes() == OLD_PROMPTS
    assert kept.sha256 == hashlib.sha256(OLD_PROMPTS).hexdigest()
    assert [w.part_number for w in report.written] == ["006-D0002-00"]


def test_each_step_of_the_trial_is_told(watch, downloads, tmp_path):
    storage, _fake = watch
    told = []
    updates.install([_prompts(storage)], storage, hidden_trial=tmp_path / "kept",
                    progress=lambda step: told.append(getattr(step, "head", step)))
    assert list(dict.fromkeys(told)) == [
        "Downloading Made-up Prompt Bundle", "Reading the folder GARMIN…",
        "Trying a small test file in GARMIN/Prompts…",
        "Copying the old Made-up Prompt Bundle to this computer",
        "Removing the old Made-up Prompt Bundle from the device",
        "Copying Made-up Prompt Bundle to the device", "Checking Made-up Prompt Bundle on the device"]


def test_a_test_file_refused_leaves_the_folder_as_it_was(watch, downloads, tmp_path):
    storage, fake = watch
    fake.refused_sends = {1}
    with pytest.raises(updates.UpdateError) as refused:
        updates.install([_prompts(storage)], storage, hidden_trial=tmp_path / "kept")
    assert str(refused.value) == (
        "Made-up Prompt Bundle was not written: the device did not take a small test file into "
        "that folder, so the file there now was left as it is.")
    assert fake.steps[:2] == ["info", "send refused"] and "read" not in fake.steps
    assert fake.prompts() == OLD_PROMPTS and fake.names_in_prompts() == ["prompt_bundle.hvm"]
    assert not (tmp_path / "kept").exists()


def test_a_refusal_puts_the_old_file_back_and_stops(watch, downloads, tmp_path):
    storage, fake = watch
    fake.refused_sends = {2}
    with pytest.raises(updates.UpdateError) as refused:
        updates.install([_prompts(storage)], storage, hidden_trial=tmp_path / "kept")
    copy = tmp_path / "kept" / "006-D0002-00-2.18-prompt_bundle.hvm"
    assert fake.steps[:len(PROBE) + 4] == [*PROBE, "read", "delete", "info", "send refused"]
    assert fake.steps[-3:] == ["info", "send", "read"]
    assert fake.prompts() == OLD_PROMPTS and fake.names_in_prompts() == ["prompt_bundle.hvm"]
    assert str(refused.value) == (
        "Made-up Prompt Bundle was not written: its old file was removed and the new one is not "
        "on the device. Nothing else was changed; install it again. The file that was there "
        f"before was put back and read back the same as its copy ({copy}, SHA-256 "
        f"{hashlib.sha256(OLD_PROMPTS).hexdigest()}).")


def test_a_refusal_of_the_put_back_too_says_where_the_copy_is(watch, downloads, tmp_path):
    storage, fake = watch
    fake.refused_sends = {2, 3}
    with pytest.raises(updates.UpdateError) as refused:
        updates.install([_prompts(storage)], storage, hidden_trial=tmp_path / "kept")
    copy = tmp_path / "kept" / "006-D0002-00-2.18-prompt_bundle.hvm"
    assert fake.steps.count("send refused") == 2 and fake.sends == 3  # nothing tried after
    assert fake.prompts() is None and copy.read_bytes() == OLD_PROMPTS
    assert str(refused.value).endswith(
        f"The file that was there before could not be put back; its copy is at {copy} "
        f"(SHA-256 {hashlib.sha256(OLD_PROMPTS).hexdigest()}).")


def test_a_copy_not_read_changes_nothing_on_the_watch(watch, downloads, tmp_path):
    storage, fake = watch
    fake.unreadable = "GARMIN/Prompts/prompt_bundle.hvm"
    with pytest.raises(updates.UpdateError) as refused:
        updates.install([_prompts(storage)], storage, hidden_trial=tmp_path / "kept")
    assert str(refused.value) == (
        "Made-up Prompt Bundle was not written: the file there now could not be copied whole to "
        "this computer first. Nothing on the device was changed.")
    assert fake.steps == [*PROBE, "read refused"]
    assert fake.prompts() == OLD_PROMPTS and fake.names_in_prompts() == ["prompt_bundle.hvm"]


# --- the command line ------------------------------------------------------------------------


@pytest.fixture
def on_the_line(watch, monkeypatch, tmp_path):
    from openexpress import cli, paths

    storage, fake = watch
    monkeypatch.setattr(GarminClient, "software_updates",
                        lambda self, raw, include_reinstalls=False: lower_keys(server_answer()))
    monkeypatch.setattr(GarminClient, "unit_info", lambda self, unit, part: {})
    monkeypatch.setattr(cli, "open_storage", lambda args: storage)
    monkeypatch.setattr(paths, "data_dir", lambda: tmp_path / "data")
    return fake


def test_the_information_goes_only_after_the_trial_wrote_its_file(on_the_line, downloads,
                                                                   device_root, capsys):
    on_the_line.refused_sends = {2}
    assert main(["updates", "--install", "006-D0002-00", "--hidden-folder-trial",
                 "--apply"]) == 1
    assert not (device_root / "GARMIN" / "UpdateMetadata").exists()
    assert on_the_line.prompts() == OLD_PROMPTS
    on_the_line.refused_sends, on_the_line.sends = set(), 0
    assert main(["updates", "--install", "006-D0002-00", "--hidden-folder-trial",
                 "--apply"]) == 0
    assert (device_root / "GARMIN" / "UpdateMetadata" / "006-D0002-00.upd").read_bytes() == META
    assert on_the_line.prompts() == NEW_PROMPTS
    said = capsys.readouterr()
    out = said.out + said.err
    # Its information is told apart from the update itself, though the server names both alike.
    assert "Downloading the update information of Made-up Prompt Bundle" in out
    meta = [u for u in updates.parse_updates(lower_keys(server_answer())) if u.is_metadata]
    assert [(updates.copying_step(u), updates.checking_step(u)) for u in meta] == [(
        "Copying the update information of Made-up Prompt Bundle to the device",
        "Checking the update information of Made-up Prompt Bundle on the device")]


def test_the_trial_takes_named_parts_only(on_the_line, downloads, capsys):
    assert main(["updates", "--install", "all", "--hidden-folder-trial", "--apply"]) == 2
    assert "not “all”" in capsys.readouterr().err
    assert on_the_line.steps == [] and downloads == []


def test_the_trial_refuses_an_update_in_a_folder_seen(on_the_line, downloads, capsys):
    assert main(["updates", "--install", "006-B0000-00", "--hidden-folder-trial",
                 "--apply"]) == 1
    assert ("Not tried: Made-up Watch 2.18 does not go to a folder the device hides inside "
            "GARMIN.") in capsys.readouterr().out
    assert on_the_line.steps == [] and downloads == []


def test_the_dry_run_says_the_plan_and_touches_nothing(on_the_line, downloads, capsys,
                                                       tmp_path):
    assert main(["updates", "--install", "006-D0002-00", "--hidden-folder-trial"]) == 0
    out = capsys.readouterr().out
    assert ("a trial: written into Garmin/Prompts, which the device hides, over a direct USB "
            "connection; the file there now is copied to "
            f"{tmp_path / 'data' / 'hidden-folder-trial'} first") in " ".join(out.split())
    assert on_the_line.steps == [] and downloads == []


def test_the_trial_on_the_line_says_where_the_old_file_is(on_the_line, downloads, capsys,
                                                         tmp_path):
    assert main(["updates", "--install", "006-D0002-00", "--hidden-folder-trial",
                 "--apply"]) == 0
    copy = tmp_path / "data" / "hidden-folder-trial" / "006-D0002-00-2.18-prompt_bundle.hvm"
    assert (f"The file Made-up Prompt Bundle replaced is kept at {copy} (SHA-256 "
            f"{hashlib.sha256(OLD_PROMPTS).hexdigest()}).") in capsys.readouterr().out
    assert on_the_line.prompts() == NEW_PROMPTS and copy.read_bytes() == OLD_PROMPTS


# --- the ordinary install (no trial): through openExpress's own session where it can be had ------


def test_where_a_session_of_its_own_can_be_had_the_update_is_installable(watch):
    storage, _fake = watch
    u = _prompts(storage)
    assert u.folder_writable and not u.refused_inside
    assert updates.installable([u]) == [u]


def test_without_usb_in_the_flatpak_it_is_refused(watch):
    storage, _fake = watch
    storage.usb_blocked = lambda: True
    u = _prompts(storage)
    assert u.refused_inside and updates.installable([u]) == []


def test_install_all_writes_it_through_its_own_session_and_keeps_no_copy(watch, downloads, xdg):
    from openexpress import paths

    storage, fake = watch
    items = _all(storage)
    report = updates.install(items, storage)
    assert fake.prompts() == NEW_PROMPTS and fake.names_in_prompts() == ["prompt_bundle.hvm"]
    assert {u.part_number for u in report.written} >= {"006-D0002-00", "006-B0000-00"}
    assert report.failed == [] and report.kept == []
    assert not list((paths.data_dir() / "replaced").glob("*"))  # the copy gone once checked


def test_a_refusal_in_install_all_is_listed_and_the_rest_goes_on(watch, downloads, xdg):
    storage, fake = watch
    fake.refused_sends = {1}  # the test file
    report = updates.install(_all(storage), storage)
    assert [u.part_number for u, _why in report.failed] == ["006-D0002-00"]
    assert "006-B0000-00" in [u.part_number for u in report.written]
    assert "006-D0002-00-META" not in [u.part_number for u in report.written]
    assert fake.prompts() == OLD_PROMPTS


def test_a_copy_put_back_is_removed_and_one_not_put_back_kept(watch, downloads, xdg):
    from openexpress import paths

    storage, fake = watch
    fake.refused_sends = {2}  # the new file; put back
    updates.install(_all(storage), storage)
    assert fake.prompts() == OLD_PROMPTS
    assert not list((paths.data_dir() / "replaced").glob("*"))
    fake.refused_sends, fake.sends = {2, 3}, 0  # the new file and the put back
    report = updates.install(_all(storage), storage)
    (why,) = [why for u, why in report.failed if u.part_number == "006-D0002-00"]
    kept = list((paths.data_dir() / "replaced").glob("*"))
    assert [p.read_bytes() for p in kept] == [OLD_PROMPTS] and str(kept[0]) in why


def _all(storage):
    from openexpress import service

    device = service.read_device(storage, found_now=False)
    return updates.offered(storage, lower_keys(server_answer()), device, forget_installed=False)
