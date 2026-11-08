"""A device over MTP that stopped responding stops what meets it: a sync with what it uploaded
before kept, a backup without a partial folder, the command line with the error said once and
exit code 1, the opening of a device without more tries; no handler below takes it for another
failure (a missing file, a refusal to keep, a try to repeat). All devices, accounts and files
are made up."""

import ast
import types
from pathlib import Path

import pytest
from fakes import ACTIVITY_URL, COURSE, STALLED, DriveAccount, SyncAccount, stalls, usb_devices
from gio_fake import GioOp, MtpFiles

import openexpress
from openexpress import ciq, cli, maps, service, sync, trueup, watch
from openexpress.device.storage import DeviceNotResponding
from openexpress.device.xmldevice import parse
from openexpress.progress import say_to

gio_storage = pytest.importorskip("openexpress.device.gio_storage")

UNIT = SyncAccount.UNIT
SECOND_COURSE = {"messageid": 7, "devicexmldatatype": "FIT_TYPE_6",
                 "metadata": {"messageurl": "course/7", "messagename": "Another course"}}


def stall(text="Cannot read GARMIN/x.fit"):
    return DeviceNotResponding.during(text)


def on_the_command_line(storage, monkeypatch, **account):
    """The command line finds `storage` as the device over MTP; Garmin Connect answers."""
    monkeypatch.setattr(service, "open_device_or_drive", lambda **_kw: storage)
    client = DriveAccount({"connectsupport": "Required"}, **account)
    monkeypatch.setattr(cli, "make_client", lambda settings, need_auth: client)
    return client


def said(capsys):
    out, err = capsys.readouterr()
    return out + err, err


# --- a sync --------------------------------------------------------------------------------


def test_a_sync_stops_at_a_stall_and_keeps_what_it_uploaded(device_root, monkeypatch, capsys):
    """The monitoring file stalls, after the two activities are uploaded: they stay done, the
    queue is not asked, and the error is said once with exit code 1."""
    sync.SyncState(UNIT).save()  # synced here before: no first-sync choice
    st = stalls(device_root, reads=("M1.FIT",))
    account = on_the_command_line(st, monkeypatch, messages=[COURSE])
    assert cli.main(["sync", "--apply"]) == 1
    text, err = said(capsys)
    assert text.count(STALLED) == 1
    assert f"Cannot read GARMIN/Monitor/M1.FIT: {STALLED}" in err
    assert [url for url, _size, _last in account.uploads] == [ACTIVITY_URL, ACTIVITY_URL]
    state = sync.SyncState(UNIT)
    assert state.is_completed("FIT_TYPE_4", "2026-10-01-10-00-00.fit")
    assert state.is_completed("FIT_TYPE_4", "2026-10-02-10-00-00.fit")
    assert not state.is_completed("FIT_TYPE_32", "M1.FIT")
    assert "queue" not in account.asked and account.acks == []


def test_a_sync_stops_when_a_file_of_connect_meets_a_stall(device_root, monkeypatch, capsys):
    """Writing the first course stalls: the second is not downloaded, neither is
    acknowledged (both come again), the uploads stay done."""
    sync.SyncState(UNIT).save()
    st = stalls(device_root, writes=("course",))
    account = on_the_command_line(st, monkeypatch, messages=[COURSE, SECOND_COURSE])
    assert cli.main(["sync", "--apply"]) == 1
    text, _err = said(capsys)
    assert text.count(STALLED) == 1
    assert len(account.uploads) == 3
    assert account.asked.count("download") == 1 and account.acks == []
    assert not list((device_root / "GARMIN" / "NewFiles").iterdir())


def test_trueup_lets_a_stall_through(monkeypatch):
    """Not one error among the others of a sync: the sync stops (TrueUp is told received before
    it is written, so nothing may follow a stall)."""
    monkeypatch.setattr(trueup, "run", lambda *a, **kw: (_ for _ in ()).throw(stall()))
    session = types.SimpleNamespace(device=None, storage=None, client=None)
    errors = []
    with pytest.raises(DeviceNotResponding):
        service._trueup(session, [], say_to(None), errors, [])
    assert errors == []


def test_the_activities_to_sync_are_not_a_guess_after_a_stall(device_root):
    """Not "not known": the window's count of the activities says the stall to its task."""
    sync.SyncState(UNIT).save()
    st = stalls(device_root, lists=("Activity",))
    device = parse((device_root / "GARMIN" / "GarminDevice.xml").read_bytes())
    with pytest.raises(DeviceNotResponding):
        sync.activities_to_sync(device, st)


# --- a backup (a guard: it stopped at the first error already) ----------------------------


def test_a_backup_stops_at_a_stall_and_leaves_no_partial_folder(device_root, monkeypatch,
                                                               capsys, tmp_path):
    st = stalls(device_root, reads=("2026-10-02",))
    on_the_command_line(st, monkeypatch)
    dest = tmp_path / "backups"
    assert cli.main(["backup", "--apply", "--dest", str(dest)]) == 1
    text, _err = said(capsys)
    assert text.count(STALLED) == 1
    assert [p.name for p in (dest / str(UNIT)).iterdir()] == []


# --- opening a device ----------------------------------------------------------------------


def test_opening_a_device_with_waiting_stops_at_a_stall(monkeypatch):
    """The command line waits for a device on its way: not for one that stopped responding,
    which is not tried again (each try would wait for the same blocked device), and not with
    the advice about another program holding it."""
    error = stall("Cannot list /")
    monkeypatch.setattr(gio_storage, "_open_device_storage",
                        lambda uri: (_ for _ in ()).throw(error))
    monkeypatch.setattr(gio_storage, "usb_state", lambda: gio_storage.PRESENT)
    monkeypatch.setattr(gio_storage, "mtp_missing", lambda: False)
    now, slept = [0.0], []

    def sleep(seconds):
        slept.append(seconds)
        now[0] += seconds

    with pytest.raises(DeviceNotResponding) as raised:
        gio_storage.open_device_storage(wait=True, sleep=sleep, clock=lambda: now[0])
    assert str(raised.value) == f"Cannot list /: {STALLED}" and slept == []


@pytest.mark.parametrize("where", ["opening", "GarminDevice.xml"])
def test_a_look_at_a_device_for_the_list_says_a_stall(monkeypatch, where):
    """Not one more device whose file cannot be read: `status` and the list of the start page
    say the stall."""
    error = stall("Cannot list /")
    if where == "opening":
        monkeypatch.setattr(gio_storage, "_mtp_storage",
                            lambda uri, usb: (_ for _ in ()).throw(error))
    else:
        monkeypatch.setattr(gio_storage, "_mtp_storage", lambda uri, usb: types.SimpleNamespace(
            read=lambda path: (_ for _ in ()).throw(error)))
    with pytest.raises(DeviceNotResponding):
        gio_storage.probe(gio_storage.FoundDevice("mtp://Made_Up_Device/", "made-up", True))


def test_the_background_service_does_not_open_a_stalled_device_again(monkeypatch):
    monkeypatch.setattr(service, "open_session", lambda: (_ for _ in ()).throw(stall()))
    slept = []
    with pytest.raises(DeviceNotResponding):
        watch.Watcher._open_session(types.SimpleNamespace(quit_requested=False),
                                    sleep=slept.append)
    assert slept == []


# --- maps and apps -------------------------------------------------------------------------


class Writes:
    """A storage whose writes stall; `tries`: how many were asked."""

    def __init__(self):
        self.tries = 0

    def write(self, path, data):
        self.tries += 1
        raise stall(f"Cannot read {path}")


def test_an_unlock_file_is_not_written_again_after_a_stall():
    st, slept = Writes(), []
    with pytest.raises(DeviceNotResponding):
        maps._write_unlock(st, "GARMIN/x.unl", b"KEY", say_to(None), slept.append)
    assert st.tries == 1 and slept == []


def test_a_file_other_maps_use_is_not_written_again_after_a_stall(monkeypatch, tmp_path):
    tries = []
    monkeypatch.setattr(maps, "_write_checked", lambda *a, **kw: tries.append(a)
                        or (_ for _ in ()).throw(stall()))
    with pytest.raises(DeviceNotResponding):
        maps._write_shared(None, types.SimpleNamespace(device_path="GARMIN/x.img"),
                           tmp_path / "x.img", None, ["Made-up map"])
    assert len(tries) == 1


def test_a_stall_is_not_remembered_as_a_refusal_to_make_the_app_folder(monkeypatch):
    """Remembered, the device would not be asked again for this software version."""
    monkeypatch.setattr(ciq, "app_dir", lambda device, datatype: "GARMIN/APPS/NEW")
    monkeypatch.setattr(ciq, "_refusals", lambda device: {})
    monkeypatch.setattr(ciq, "usb_dir_state", lambda device, st, datatype: ciq.MISSING)
    monkeypatch.setattr(ciq, "save_app_list_copy", lambda device, st: None)
    remembered = []
    monkeypatch.setattr(ciq, "_remember_refusal", lambda device, path: remembered.append(path))
    st = types.SimpleNamespace(resolve=lambda path: None,
                               make_dir=lambda path: (_ for _ in ()).throw(stall()))
    with pytest.raises(DeviceNotResponding):
        ciq.prepare_app_dir(types.SimpleNamespace(software_version_str="1.00"), st,
                            "IQWatchFaces")
    assert remembered == []


# --- the place on USB of a device that stopped responding --------------------------------

LIMIT = 0.2
FENIX = gio_storage.GarminUsb("51b4", 3, 14)
EDGE = gio_storage.GarminUsb("4e86", 3, 20)


@pytest.fixture
def quick(monkeypatch):
    monkeypatch.setattr(gio_storage, "STALL_S", LIMIT)
    monkeypatch.setattr(gio_storage, "CANCEL_WAIT_S", LIMIT)


def places(usbs) -> list[tuple[int, int]]:
    return sorted((u.bus, u.address) for u in usbs)


def mounted(monkeypatch, devices: dict):
    """The volume monitor shows a mount of each device, {address: GarminUsb}."""
    def mount(uri):
        return types.SimpleNamespace(get_root=lambda: types.SimpleNamespace(get_uri=lambda: uri),
                                     get_name=lambda: "made-up")

    monkeypatch.setattr(gio_storage.Gio.VolumeMonitor, "get", staticmethod(
        lambda: types.SimpleNamespace(get_mounts=lambda: [mount(u) for u in devices],
                                      get_volumes=list)))
    monkeypatch.setattr(gio_storage, "garmin_usb",
                        lambda obj, monitor=None: devices.get(obj.get_root().get_uri()))


def stall_at(usb, plan_kind="list"):
    """A storage of the device at `usb` whose first listing gets no answer: its error."""
    files = MtpFiles({"GARMIN/GarminDevice.xml": b"<Device/>"},
                     plan={(plan_kind, 1): GioOp("never", strict=True)})
    st = gio_storage.GioStorage("mtp://Made_Up_Device/")
    st.root, st.usb = files.file(), usb
    try:
        with pytest.raises(DeviceNotResponding) as error:
            st._list("")
    finally:
        files.clean()
    return error.value


def test_a_stall_keeps_its_device_out_until_it_is_gone_from_its_place(quick, monkeypatch,
                                                                      tmp_path):
    """Every try would wait for the same blocked gvfs: the device at that place is not found,
    nor counted on USB (so the window starts no detection of it), until it is unplugged or
    restarted (a new device number)."""
    put = usb_devices(monkeypatch, tmp_path / "usb")
    put({"3-1": ("51b4", 3, 14)})
    mounted(monkeypatch, {"mtp://Made_Up_Device/": FENIX})
    error = stall_at(FENIX)
    assert (error.usb.bus, error.usb.address) == (3, 14)
    assert places(gio_storage.stalled_places()) == [(3, 14)]
    assert gio_storage.find_devices() == []
    assert gio_storage.usb_state() == gio_storage.ABSENT
    assert gio_storage.garmin_products_on_usb() == []
    put({"3-1": ("51b4", 3, 15)})  # unplugged and plugged in again
    assert gio_storage.stalled_places() == []
    found = gio_storage.find_devices()
    assert [f.uri for f in found] == ["mtp://Made_Up_Device/"]
    assert gio_storage.usb_state() == gio_storage.PRESENT


def test_only_the_device_that_stopped_responding_is_left_out(quick, monkeypatch, tmp_path):
    put = usb_devices(monkeypatch, tmp_path / "usb")
    put({"3-1": ("51b4", 3, 14), "3-2": ("4e86", 3, 20)})
    mounted(monkeypatch, {"mtp://Made_Up_Fenix/": FENIX, "mtp://Made_Up_Edge/": EDGE})
    stall_at(FENIX)
    assert [f.uri for f in gio_storage.find_devices()] == ["mtp://Made_Up_Edge/"]
    assert gio_storage.usb_state() == gio_storage.PRESENT
    assert gio_storage.garmin_products_on_usb() == ["4e86"]


def test_a_stall_as_the_device_is_opened_knows_its_place(quick, monkeypatch, tmp_path):
    """Its storages get the place on USB from the start: a stall at its first listing keeps
    the device out too."""
    put = usb_devices(monkeypatch, tmp_path / "usb")
    put({"3-1": ("51b4", 3, 14)})
    files = MtpFiles({"GARMIN/GarminDevice.xml": b"<Device/>"},
                     plan={("list", 1): GioOp("never", strict=True)})
    real = gio_storage.GioStorage

    class OnTheFiles(real):
        def __init__(self, uri):
            super().__init__(uri)
            self.root = files.file()

    monkeypatch.setattr(gio_storage, "GioStorage", OnTheFiles)
    try:
        with pytest.raises(DeviceNotResponding) as error:
            gio_storage._mtp_storage("mtp://Made_Up_Device/", FENIX)
    finally:
        files.clean()
    assert (error.value.usb.bus, error.value.usb.address) == (3, 14)
    assert places(gio_storage.stalled_places()) == [(3, 14)]


def test_a_device_still_stalled_is_not_opened_again(quick, monkeypatch, tmp_path):
    """Opened again while it stays at its place (its tile chosen before the list left it out),
    the device is refused at once: a new storage would wait for the blocked gvfs again."""
    put = usb_devices(monkeypatch, tmp_path / "usb")
    put({"3-1": ("51b4", 3, 14)})
    stall_at(FENIX)
    monkeypatch.setattr(gio_storage.GioStorage, "_list",
                        lambda self, real: pytest.fail("the device was asked again"))
    with pytest.raises(DeviceNotResponding) as error:
        gio_storage._mtp_storage("mtp://Made_Up_Device/", FENIX)
    words, place = str(error.value), (error.value.usb.bus, error.value.usb.address)
    assert words == f"Cannot open the device: {STALLED}"
    assert place == (3, 14)
    put({"3-1": ("51b4", 3, 15)})  # unplugged and plugged in again: opened as usual
    monkeypatch.setattr(gio_storage.GioStorage, "_list", lambda self, real: [])
    monkeypatch.setattr(gio_storage.GioStorage, "exists", lambda self, path: False)
    with pytest.raises(gio_storage.NotThereYet):
        gio_storage._mtp_storage("mtp://Made_Up_Device/", gio_storage.GarminUsb("51b4", 3, 15))


def test_a_quiet_clean_up_lets_a_stall_through(tmp_path):
    """A file deleted after a failure said already: its own failure goes to the log, but a
    stall stops what meets it, as everywhere."""
    from openexpress.device.storage import LocalStorage

    class StallsAtDelete(LocalStorage):
        def _delete(self, real_path):
            raise DeviceNotResponding.during(f"Cannot delete {real_path}")

    (tmp_path / "x.fit").write_bytes(b"made-up")
    with pytest.raises(DeviceNotResponding):
        StallsAtDelete(tmp_path).delete_quietly("x.fit")


# --- no handler takes it for another failure ---------------------------------------------

PACKAGE = Path(openexpress.__file__).parent
# Handlers of DeviceError that only ever see a drive's folder (LocalStorage), never a device
# over MTP.
DRIVE_ONLY = {("device/gio_storage.py", "find_ums_devices"),
              ("device/gio_storage.py", "open_ums_storage"),
              ("cli.py", "_eject_drive")}


def _caught(handler) -> set[str]:
    kinds = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    return {k.id if isinstance(k, ast.Name) else getattr(k, "attr", "") for k in kinds if k}


BROAD = {"Exception", "BaseException", "OSError"}


def _asks_a_storage(nodes, in_a_storage: bool = False) -> bool:
    """Whether the statements call a method of a storage or pass one on; in a storage's own
    class (`in_a_storage`), also a method of `self`."""
    def named(node) -> str:
        return node.id if isinstance(node, ast.Name) else getattr(node, "attr", "")

    for node in nodes:
        for call in (c for c in ast.walk(node) if isinstance(c, ast.Call)):
            if isinstance(call.func, ast.Attribute):
                owner = named(call.func.value)
                if "storage" in owner.lower() or (in_a_storage and owner == "self"):
                    return True
            if any("storage" in named(a).lower() for a in call.args):
                return True
    return False


def _a_storage_class(node: ast.ClassDef) -> bool:
    """A class of a storage: named so, or made from one."""
    bases = [b.id if isinstance(b, ast.Name) else getattr(b, "attr", "") for b in node.bases]
    return node.name.endswith("Storage") or any(b.endswith("Storage") for b in bases)


def _raises_again(handler) -> bool:
    return bool(handler.body) and isinstance(handler.body[-1], ast.Raise) \
        and handler.body[-1].exc is None


class _Handlers(ast.NodeVisitor):
    """Each handler of DeviceError, and each broad one (Exception, BaseException, OSError, a
    bare except) around what asks a storage that does not raise again, with no handler of
    DeviceNotResponding before it in its try, as (function, line)."""

    def __init__(self):
        self.functions: list[str] = []
        self.storages: list[bool] = []
        self.found: list[tuple[str, int]] = []

    def visit_ClassDef(self, node):
        self.storages.append(_a_storage_class(node))
        self.generic_visit(node)
        self.storages.pop()

    def visit_FunctionDef(self, node):
        self.functions.append(node.name)
        self.generic_visit(node)
        self.functions.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Try(self, node):
        passed_on = False
        for handler in node.handlers:
            caught = _caught(handler) if handler.type is not None else set()
            broad = handler.type is None or bool(caught & BROAD)
            in_a_storage = bool(self.storages) and self.storages[-1]
            if "DeviceNotResponding" in caught:
                passed_on = True
            elif not passed_on and ("DeviceError" in caught or (
                    broad and _asks_a_storage(node.body, in_a_storage)
                    and not _raises_again(handler))):
                self.found.append((self.functions[-1] if self.functions else "-",
                                   handler.lineno))
        self.generic_visit(node)

    visit_TryStar = visit_Try


def test_the_guard_sees_a_handler_that_would_take_a_stall():
    """Planted handlers: of DeviceError, and a broad one around a storage (also its own
    methods, in a storage's class) that does not raise again, are found; with a handler of the
    stall before them, or raising again, not."""
    planted = ast.parse(
        "def missing_file(storage):\n"
        "    try:\n"
        "        return storage.read('x')\n"
        "    except DeviceError:\n"
        "        return None\n"
        "def broad(storage):\n"
        "    try:\n"
        "        storage.listdir('y')\n"
        "    except Exception:\n"
        "        pass\n"
        "def let_through(storage):\n"
        "    try:\n"
        "        storage.listdir('y')\n"
        "    except DeviceNotResponding:\n"
        "        raise\n"
        "    except (DeviceError, OSError):\n"
        "        pass\n"
        "def raises_again(storage):\n"
        "    try:\n"
        "        storage.listdir('y')\n"
        "    except Exception:\n"
        "        log.info('noted')\n"
        "        raise\n"
        "def no_storage(answer):\n"
        "    try:\n"
        "        return answer.json()\n"
        "    except Exception:\n"
        "        return None\n"
        "class MadeUpStorage(Storage):\n"
        "    def quietly(self, path):\n"
        "        try:\n"
        "            self.delete(path)\n"
        "        except Exception:\n"
        "            pass\n"
        "class Answer:\n"
        "    def text(self):\n"
        "        try:\n"
        "            return self.decode()\n"
        "        except Exception:\n"
        "            return ''\n")
    handlers = _Handlers()
    handlers.visit(planted)
    assert [function for function, _line in handlers.found] == ["missing_file", "broad", "quietly"]


def test_every_handler_of_device_errors_lets_a_stall_through():
    """A handler of DeviceError outside the window, and a broad one around what asks a
    storage that does not raise again, has one of DeviceNotResponding before it, which lets
    the stall through: a stall stops what meets it. The window (gui/) has its own way of
    saying it."""
    missing, exempt = [], set()
    for path in sorted(PACKAGE.rglob("*.py")):
        rel = path.relative_to(PACKAGE).as_posix()
        if rel.startswith("gui/"):
            continue
        handlers = _Handlers()
        handlers.visit(ast.parse(path.read_text(encoding="utf-8")))
        for function, line in handlers.found:
            if (rel, function) in DRIVE_ONLY:
                exempt.add((rel, function))
            else:
                missing.append(f"{rel}:{line} ({function})")
    assert missing == []
    assert exempt == DRIVE_ONLY  # no exemption kept for a handler that is gone
