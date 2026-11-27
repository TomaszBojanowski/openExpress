"""Fakes shared by the tests (imported as `from fakes import …`, like ptp_fake). Only made-up
data; nothing here reaches a device, the network or the disk of this computer outside the
test's own folder."""

import contextlib
import hashlib
import http.server
import os
import struct
import subprocess
import threading
import types
from collections import namedtuple
from dataclasses import dataclass
from datetime import UTC, datetime

Call = namedtuple("Call", "title fn done failed device in_page button failed_title")


class _Session(types.SimpleNamespace):
    """A stand-in session whose `mass_storage` is read from its storage, as the real one's."""

    @property
    def mass_storage(self) -> bool:
        return bool(getattr(self.storage, "mass_storage", False))


def device_session(**fields):
    """A stand-in of service.DeviceSession: every field of the real one at its default (storage,
    device, settings and client None) unless given; `mass_storage`, as the real one's property,
    read from its storage unless given; other names (such as the real one's other properties,
    logged_in) as given."""
    import dataclasses

    from openexpress import service

    values = {}
    for f in dataclasses.fields(service.DeviceSession):
        if f.default is not dataclasses.MISSING:
            values[f.name] = f.default
        elif f.default_factory is not dataclasses.MISSING:
            values[f.name] = f.default_factory()
        else:
            values[f.name] = None
    made = types.SimpleNamespace if "mass_storage" in fields else _Session
    return made(**{**values, **fields})


class Waits:
    """A stand-in of the Event that stops a wait (TrueUp's `stop`): each step of the wait
    (wait(seconds)) noted and never stopped; `then`: raised at the first step instead (e.g.
    KeyboardInterrupt, Ctrl+C)."""

    def __init__(self, then: BaseException | type | None = None):
        self.waited: list[float] = []
        self.then = then

    def wait(self, seconds: float) -> bool:
        if self.then is not None:
            raise self.then
        self.waited.append(seconds)
        return False


class RunNow:
    """The window's run_task (gui/window_tasks.py), run at once: the work (in a thread,
    waited for, with `thread=True`), then done or failed; an error `failed` does not take goes
    to `error`: "raise" (default), "record" (into `errors`) or a function (heading, words: the
    heading as the window's, error_heading).
    WindowClosed ends the task quietly; `button` spins during the work. Leaves `calls`,
    `titles` and `written` (writes_to_device works)."""

    def __init__(self, error="raise", thread=False, on_call=None, before_done=None):
        self.error, self.thread = error, thread
        self.on_call, self.before_done = on_call, before_done
        self.calls: list[Call] = []
        self.errors: list[str] = []
        self.written: list[str] = []

    @property
    def titles(self) -> list[str]:
        return [call.title for call in self.calls]

    def __call__(self, title, fn, done=None, failed=None, device=True, in_page=False,
                 button=None, failed_title=None):
        # Unlike the window: no check of `busy`, no bar, no pending refresh afterwards.
        call = Call(title, fn, done, failed, device, in_page, button, failed_title)
        self.calls.append(call)
        if self.on_call is not None:
            self.on_call(call)
        restore = None
        if button is not None:
            from openexpress.gui.window_tasks import show_working

            restore = show_working(button)
        result, raised = [], []

        def work():
            try:
                result.append(fn())
            except Exception as e:  # noqa: BLE001 - as the window's run_task: to `failed`
                raised.append(e)

        if self.thread:
            worker = threading.Thread(target=work)
            worker.start()
            worker.join()
        else:
            work()
        if self.before_done is not None:
            self.before_done()
        if restore is not None:
            restore(True)
        from openexpress.gui.tasks import WindowClosed

        try:
            if raised and isinstance(raised[0], WindowClosed):
                pass  # stopped as the window closed: nothing to tell
            elif raised:
                self._failed(failed_title, raised[0], failed)
            elif done is not None:
                done(result[0])  # an error of done is not caught, as in the window
        finally:
            if getattr(fn, "writes_to_device", False):
                self.written.append(title)

    def _failed(self, failed_title, error, failed) -> None:
        if failed is not None and failed(error):
            return
        if self.error == "raise":
            raise error
        words = str(error) or type(error).__name__
        if self.error == "record":
            self.errors.append(words)
        else:
            from openexpress.gui.window_tasks import error_heading

            self.error(error_heading(failed_title), words)


class SyncClient:
    """The Garmin Connect account as sync uses it: uploads are noted and answered with
    `statuses` by address (201 otherwise); Connect has the activities in `matches`; the queue
    gives a course for an address with "course" in it; acknowledgements are noted.
    `require_verified` refuses, as the real client does, without a device checked against the
    account (`verified_unit_id`) or for another."""

    def __init__(self, statuses=None, matches=None, messages=None, verified_unit_id=None):
        self.statuses = statuses or {}
        self.matches = matches or {}
        self.messages = messages or []
        self.verified_unit_id = verified_unit_id
        self.uploads = []
        self.acks = []

    def require_verified(self, unit_id=None):
        from openexpress.connect import DeviceNotVerified

        if self.verified_unit_id is None or unit_id not in (None, self.verified_unit_id):
            raise DeviceNotVerified("this device was not checked against the account")

    def activity_matches(self, unit_id, ids):
        return {i: i in self.matches for i in ids}

    def upload_file(self, url, data, *, is_zip, last_file, authenticated):
        from openexpress.connect import UploadResult

        self.uploads.append((url, len(data), last_file))
        return UploadResult(self.statuses.get(url, 201), {}, None, None)

    def poll_location(self, url, delay):
        return 201

    def download_message(self, url, absolute):
        return b"COURSE" if "course" in url else None

    def ack_message(self, mid):
        self.acks.append(mid)


# --- Garmin Connect as a whole sync asks it ------------------------------------------------

ACTIVITY_URL = "https://up.example/activity"
MONITORING_URL = "https://up.example/monitoring"
# The upload settings of the test device (tests/conftest.py): its activities and monitoring.
UPLOAD_SETTINGS = {"unittypename": "test", "datatypes": [
    {"datatypename": "FIT_TYPE_4",
     "uploadlocations": [{"url": ACTIVITY_URL, "requiresauthentication": True}]},
    {"datatypename": "FIT_TYPE_32",
     "uploadlocations": [{"url": MONITORING_URL, "requiresauthentication": True}]},
]}
OPT_IN = {"useroption": "opt-in", "sourcetype": "CUSTOMER", "consentenforcementstate": "HARD"}
COURSE = {"messageid": 5, "devicexmldatatype": "FIT_TYPE_6",
          "metadata": {"messageurl": "course/5", "messagename": "A course"}}
# A type the test device does not declare (it takes courses only).
NOT_TAKEN = {"messageid": 6, "devicexmldatatype": "FIT_TYPE_99",
             "metadata": {"messageurl": "other/6", "messagename": "Something"}}


class SyncAccount(SyncClient):
    """Garmin Connect, logged in, as a whole sync asks it, both ways: the account lists the
    test device; `consent` False: no upload consent; the upload settings of
    UPLOAD_SETTINGS; the queue of `messages`; the acknowledgement of each message ID of
    `ack_fails` fails. `asked`: what was asked of it, in order."""

    UNIT = 1234567890  # the test device (tests/conftest.py)

    def __init__(self, *, consent=True, ack_fails=(), **kw):
        super().__init__(**kw)
        self.auth = object()
        self.consent_given = consent
        self.ack_fails = set(ack_fails)
        self.asked = []

    def account_devices(self, fresh=False):
        self.asked.append("account")
        return [{"unitid": self.UNIT}]

    def consent(self, consent_type):
        self.asked.append("consent")
        return OPT_IN if self.consent_given else {**OPT_IN, "useroption": "opt-out"}

    def device_sync_preference(self):
        self.asked.append("device sync")
        return {"key": "account.deviceSync", "value": "true"}

    def profile_country(self, fresh=False):
        return "PL"

    def upload_settings(self, unit_id):
        self.asked.append("upload settings")
        return UPLOAD_SETTINGS

    def activity_matches(self, unit_id, ids):
        self.asked.append("matches")
        return super().activity_matches(unit_id, ids)

    def upload_file(self, url, data, **kw):
        self.asked.append("upload")
        return super().upload_file(url, data, **kw)

    def device_messages(self, unit_id):
        self.asked.append("queue")
        return self.messages

    def download_message(self, url, absolute):
        self.asked.append("download")
        return super().download_message(url, absolute)

    def ack_message(self, mid):
        self.asked.append("acknowledge")
        if mid in self.ack_fails:
            from openexpress.connect import ApiError

            raise ApiError("the queue did not answer", 503)
        super().ack_message(mid)


class DriveAccount(SyncAccount):
    """Garmin Connect as a sync asks it (the account lists `devices`, by default the test
    device), and Garmin's update server answering unit-info with `answer` (an exception:
    raised). unit-info goes through the kept answers as the real client's does; `asked` notes
    each question that reached a server."""

    def __init__(self, answer, devices=None, **kw):
        from openexpress.settings import Settings

        super().__init__(verified_unit_id=None, **kw)
        self.settings = Settings()
        self.answer = answer
        self.devices = [{"unitid": self.UNIT}] if devices is None else devices

    def unit_info(self, unit_id, part_number):
        from openexpress.connect import GarminClient

        return GarminClient.unit_info(self, unit_id, part_number)

    def account_devices(self, fresh=False):
        self.asked.append("account, fresh" if fresh else "account")
        return self.devices

    def _omt(self, method, path, what, quiet=False, **kw):
        self.asked.append("unit-info")
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def drive_session(root, account, storage_class=None, vendor=None):
    """A session on the made-up device in `root` connected as a USB drive (UmsStorage, or
    `storage_class`), as service.open_session makes it; with `vendor`, a USB drive of that
    vendor taken for a Garmin drive (a test drive, gio_storage.TEST_DRIVE_ENV)."""
    from openexpress import service
    from openexpress.device.gio_storage import GarminUsb
    from openexpress.device.storage import UmsStorage
    from openexpress.device.xmldevice import parse
    from openexpress.settings import Settings

    usb = GarminUsb("4f42", 1, 9, serial="MADE-UP-SERIAL-8")
    if vendor is not None:
        usb = GarminUsb("5678", 1, 9, serial="MADE-UP-SERIAL-9", vendor=vendor)
    storage = (storage_class or UmsStorage)(root, root.as_uri() + "/", usb=usb)
    device = parse((root / "GARMIN" / "GarminDevice.xml").read_bytes())
    return service.DeviceSession(storage, device, Settings(), account)


def stalls(root, reads=(), writes=(), lists=()):
    """The made-up device in `root` over MTP, which stops responding when it reads a file whose
    name holds one of `reads`, writes one whose name holds one of `writes`, or lists a folder
    whose path ends with one of `lists`; from then on every operation of it fails at once
    with the same error, as GioStorage does after a stall. A write stalls as GioStorage's
    does, when the size of the file there is asked before the copy ("Cannot read …").
    `stalled`: that error."""
    import posixpath

    from openexpress.device.storage import DeviceNotResponding, LocalStorage

    class Stalling(LocalStorage):
        stall = None  # the first DeviceNotResponding, said again by every later operation

        @property
        def stalled(self) -> bool:
            return self.stall is not None

        def _go_on(self, real_path="", action=None, stall=False):
            if self.stall is not None:
                raise DeviceNotResponding(str(self.stall))
            if stall:
                self.stall = DeviceNotResponding.during(action.format(path=real_path or "/"))
                raise self.stall

        def _named(self, real_path, names):
            return any(n in posixpath.basename(real_path) for n in names)

        def _list(self, real_path):
            self._go_on(real_path, "Cannot list {path}", real_path.endswith(lists) if lists
                        else False)
            return super()._list(real_path)

        def info(self, real_path):
            self._go_on()
            return super().info(real_path)

        def _read(self, real_path):
            self._go_on(real_path, "Cannot read {path}", self._named(real_path, reads))
            return super()._read(real_path)

        def _read_start(self, real_path, count):
            self._go_on(real_path, "Cannot read {path}", self._named(real_path, reads))
            return super()._read_start(real_path, count)

        def _write(self, real_path, data):
            self._go_on(real_path, "Cannot read {path}", self._named(real_path, writes))
            super()._write(real_path, data)

        def _write_file(self, real_path, local, progress=None):
            self._go_on(real_path, "Cannot read {path}", self._named(real_path, writes))
            super()._write_file(real_path, local, progress)

        def _delete(self, real_path):
            self._go_on()
            super()._delete(real_path)

        def _mkdir(self, real_path):
            self._go_on()
            super()._mkdir(real_path)

        def free_space(self):
            self._go_on()
            return super().free_space()

        def capacity(self):
            self._go_on()
            return super().capacity()

    return Stalling(root)


def other_bytes_drive():
    """A kind of drive (LocalStorage) that keeps each file written at its size but with other
    bytes (zeros), as a drive whose medium lost what was written while its folder kept the
    size: only reading the file back tells."""
    from openexpress.device.storage import LocalStorage

    class OtherBytes(LocalStorage):
        def _replace(self, real_path, chunks, progress=None, total=0):
            super()._replace(real_path, chunks, progress, total)
            target = self._p(real_path)
            target.write_bytes(bytes(target.stat().st_size))

    return OtherBytes


def stale_sizes(cut, *, still_written=(), fails=None):
    """A kind of storage (LocalStorage) of a device over MTP as gvfs shows it, keeping the size
    a file had when it was first looked at: each file of `cut` (its path from the device's
    root, in any case, and a number of bytes) is listed at that size and reads cut to it until
    `refresh`, after which it is whole, unless it is in `still_written` too (the device still
    writes it). `refreshes` keeps the progress each refresh was given; `fails`: what a refresh
    raises instead (the device not mounted again)."""
    import dataclasses
    import posixpath

    from openexpress.device.storage import LocalStorage

    sizes = {p.casefold(): size for p, size in cut.items()}
    still = {p.casefold() for p in still_written}

    class StaleSizes(LocalStorage):
        def __init__(self, root):
            super().__init__(root)
            self.refreshes = []

        def _stale(self, real_path):
            """The size gvfs gives the file, when it is not its own."""
            key = real_path.casefold()
            return sizes.get(key) if not self.refreshes or key in still else None

        def _read(self, real_path):
            data = super()._read(real_path)
            size = self._stale(real_path)
            return data if size is None else data[:size]

        def _list(self, real_path):
            out = []
            for e in super()._list(real_path):
                size = self._stale(posixpath.join(real_path, e.name))
                out.append(e if size is None else dataclasses.replace(e, size=size))
            return out

        def info(self, real_path):
            e = super().info(real_path)
            size = None if e is None else self._stale(real_path)
            return e if size is None else dataclasses.replace(e, size=size)

        def refresh(self, progress=None):
            self.refreshes.append(progress)
            if fails is not None:
                raise fails
            return True

    return StaleSizes

# --- what the window writes of the devices before it draws their tiles ------------------


def login(saved=True):
    """A login saved, as `openexpress login` saves it (made up), and its version
    (store.stamp); `saved` False: none saved (logged out)."""
    from openexpress.auth import store

    if not saved:
        store.delete()
        return None
    if store.stamp() is None:
        store.save(store.ConnectAuth("token", "secret", account={"username": "made-up"}))
    return store.stamp()


def noted(summaries=(), account=None):
    """What the window writes of these connected devices before it draws their tiles
    (tile_facts): each one seen by its detection, what the account's list says of them
    (`account`, read with the login saved now; None: not read) and each count it holds
    (summary.to_sync)."""
    from openexpress import known_devices, tile_facts

    for s in summaries:
        if s.unit_id:
            known_devices.seen(s.unit_id, s.description, s.part_number, s.software_version,
                               s.found.kind)
    if account is not None:
        tile_facts.note_account(account)
    for s in summaries:
        tile_facts.note_count(getattr(s, "to_sync", None))


# --- sync records, as a sync would leave them ----------------------------------------------


def record_sync(unit_id, uploaded, downloaded, skipped_known, errors, lines):
    """A last sync of this device recorded (last_sync.json, as service.save_last_sync writes
    it after a sync): the numbers and the steps (`lines`, as SyncSummary.lines has them)."""
    from openexpress import service

    service.save_last_sync(unit_id, service.SyncSummary(
        uploaded=uploaded, skipped_known=skipped_known, downloaded=downloaded,
        errors=list(errors), lines=list(lines)))


def step_words(lines):
    """The steps of a sync record in words, as the window shows them (service.describe_steps),
    without whether each is a warning."""
    from openexpress import service

    return [text for text, _warning in service.describe_steps(lines)]


# --- the window's own methods on a stand-in ------------------------------------------------


def start_section(start, key):
    """A section of the start page (gui.start.StartPage) by its key, as devicelist names the
    sections ("connected", "remembered", "on-account"): its title, grid and list."""
    return start._sections[key]


def row_words(row):
    """The title and the words under it that a row of the start page's list (gui.start.
    DeviceRow) shows: its labels "title" and "subtitle", "" for one it has not."""
    from gi.repository import Gtk

    def labels(widget):
        child = widget.get_first_child()
        while child is not None:
            if isinstance(child, Gtk.Label):
                yield child
            yield from labels(child)
            child = child.get_next_sibling()

    words = {"title": "", "subtitle": ""}
    for label in labels(row):
        for kind in words:
            if not words[kind] and label.has_css_class(kind):
                words[kind] = label.get_label()
    return words["title"], words["subtitle"]


def descendants(widget):
    """The widgets under `widget`, at any depth: its children, theirs, and so on (the
    window's own walk, window_tasks.descendants)."""
    from openexpress.gui.window_tasks import descendants as walk

    return walk(widget)


def turn_until(done, seconds=5.0):
    """Turns the main loop until `done()` is true or `seconds` have passed: what `done()` says
    at the end."""
    import time

    from gi.repository import GLib

    deadline = time.monotonic() + seconds
    while not done() and time.monotonic() < deadline:
        GLib.MainContext.default().iteration(False)
    return done()


def iterate(seconds: float = 0.05) -> None:
    """Turns the main loop for `seconds`."""
    import time

    from gi.repository import GLib

    end = time.monotonic() + seconds
    while time.monotonic() < end:
        GLib.MainContext.default().iteration(False)


def wait(until, what: str, seconds: float = 15) -> None:
    """Turns the main loop until `until()` is true, then a moment more so that what follows it
    is drawn too; fails naming `what` after `seconds`."""
    import time

    from gi.repository import GLib

    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if until():
            iterate(0.1)  # what follows it drawn too
            return
        GLib.MainContext.default().iteration(False)
    raise AssertionError(f"not reached: {what}")


def children(widget):
    """The widgets right under `widget`, in order."""
    child = widget.get_first_child()
    while child is not None:
        yield child
        child = child.get_next_sibling()


def labels_of(widget):
    """Every Gtk.Label in `widget`, itself included, at any depth."""
    from gi.repository import Gtk

    found = [widget] if isinstance(widget, Gtk.Label) else []
    child = widget.get_first_child()
    while child is not None:
        found += labels_of(child)
        child = child.get_next_sibling()
    return found


def answer_dialog(dialog, response):
    """An answer as a click gives it in libadwaita 1.9: "closed" first, then "response"."""
    dialog.emit("closed")
    dialog.emit("response", response)


def tile(**fields):
    """A tile of the start page: a connected fēnix 8 on the account, unless told otherwise."""
    from openexpress.devicelist import Tile

    values = dict(description="fēnix 8", part_number="006-B4532-00", software_version="23.39",
                  kind="mtp", connected=True, on_account=True, primary=False, last_seen=None,
                  image_part_number="006-B4532-00", image_url=None, category=None,
                  account_known=True)
    values.update(fields)
    return Tile(**values)


def edge_tile(**fields):
    """A tile of the start page: a connected Edge 1050 on the account, unless told otherwise."""
    from openexpress.devicelist import Tile

    values = dict(description="Edge 1050", part_number="006-B4440-00", software_version="33.13",
                  kind="mtp", connected=True, on_account=True, primary=False, last_seen=None,
                  image_part_number="006-B4440-00", image_url=None, category=None,
                  update_found=None, account_known=True)
    values.update(fields)
    return Tile(**values)


def sync_setup(device_root, tmp_path):
    """The storage of the folder `device_root`, its device and a sync state of its own."""
    from openexpress import sync
    from openexpress.device.storage import LocalStorage
    from openexpress.device.xmldevice import parse

    st = LocalStorage(device_root)
    dev = parse(st.read("GARMIN/GarminDevice.xml"))
    state = sync.SyncState(dev.unit_id, base=tmp_path / "state")
    return st, dev, state


def pending_of(unit_id, count):
    """`count` files of the device waiting to be uploaded."""
    from openexpress import paths

    for n in range(count):
        path = paths.device_dir(unit_id) / "sync" / "pending" / "FIT_TYPE_4" / f"ACT{n}.FIT"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fit")


def kept_copy(kind, files, when="2026-10-21 11.00.00"):
    """A copy of diagnostic files of the test device as the program keeps it: its note and each
    content once (diagnostics: objects, snapshots)."""
    import hashlib
    import json

    from openexpress import diagnostics

    where = diagnostics.folder(DEVICE_UNIT)
    (where / "objects").mkdir(parents=True, exist_ok=True)
    (where / "snapshots").mkdir(exist_ok=True)
    listed = {}
    for path, data in files.items():
        sha = hashlib.sha256(data).hexdigest()
        (where / "objects" / sha).write_bytes(data)
        listed[path] = {"sha256": sha, "size": len(data), "mtime": None}
    (where / "snapshots" / f"{when}.json").write_text(json.dumps(
        {"kind": kind, "at": when, "software_version": "23.39", "files": listed, "listed": {}}))


def last_sync_details(record, language="en"):
    """The lines of the details of a sync record, as the window words them in `language`."""
    from openexpress import last_sync

    with in_language(language):
        return [text for text, _warning in last_sync.describe(record).steps]


@contextlib.contextmanager
def gtk_complaints(domains=("Adwaita", "Gtk")):
    """What Adwaita and GTK (or `domains`) say as CRITICAL or WARNING inside the block, as
    (domain, message) pairs: a row removed from a group it is not in, a widget given to a second
    parent, a window narrower than its content… They go to the terminal only and the window
    carries on, so a test asks for them."""
    from gi.repository import GLib

    said = []
    levels = GLib.LogLevelFlags.LEVEL_CRITICAL | GLib.LogLevelFlags.LEVEL_WARNING
    handlers = [(domain, GLib.log_set_handler(
        domain, levels, lambda domain, _level, message, _data: said.append((domain, message)),
        None)) for domain in domains]
    try:
        yield said
    finally:
        for domain, handler in handlers:
            GLib.log_remove_handler(domain, handler)


# What a device that stopped responding says after the step it stopped at.
STALLED = "the device stopped responding. Unplug it and plug it in again."


def stalled_session(usb, name, stalled=False):
    """A session as the banner of a stall reads it: its storage at `usb` (gio_storage.GarminUsb;
    None: not known), whether it stalled, and its device's name."""
    storage = types.SimpleNamespace(usb=usb, device_uri=f"mtp://Made_Up_{len(name)}/",
                                    stalled=stalled)
    return device_session(storage=storage,
                          device=types.SimpleNamespace(description=name, unit_id=None))


def tells_stalls(win, monkeypatch, connected=()):
    """What of the window a stall goes through, on the stand-in `win`: the window's own
    error(), inform() said into `told.errors`, the banners of the start page (`told.start`)
    and of the device view (`told.banner`, a count), the list of devices connected, set_busy
    and the refresh after a task. Returns `told`."""
    told = types.SimpleNamespace(start=[], errors=[], banner=0)

    def banner():
        told.banner += 1

    win.start = types.SimpleNamespace(set_stalled=told.start.append,
                                      set_empty=lambda *notice, icon=None: None)
    win._update_banner = banner
    win.connected = types.SimpleNamespace(devices=list(connected))
    win._run_pending_refresh = lambda: None
    win.set_busy = lambda busy, *a, **kw: setattr(win, "busy", busy)
    window_methods(win, ["error"])
    replace_in_window(monkeypatch, "inform",
                      lambda parent, name, heading, body, **kw: told.errors.append(body))
    return told


def window(**changes):
    """A stand-in for the window without GTK: every attribute the real window has, with the
    value it has before anything is shown (MainWindow._init_state), and the window's own
    in_background (it holds nothing of the window: what the window does off its thread, the
    stand-in does so too); then `changes`. None of the window's other methods: a test gives
    those it needs, or binds them with window_methods."""
    import types

    from openexpress.gui.app import MainWindow

    win = types.SimpleNamespace()
    MainWindow._init_state(win)
    win.in_background = MainWindow.in_background
    for name, value in changes.items():
        setattr(win, name, value)
    return win


def widget_tree(w, opaque: tuple = ()) -> dict:
    """One widget and all under it: its type, CSS classes, what it says and shows, whether it
    is visible and takes clicks; of a widget of the types `opaque` (another library's, whose
    inner widgets change with its version), the widget alone."""
    from gi.repository import Adw, Gtk

    said: dict = {"type": w.__gtype__.name, "css": sorted(w.get_css_classes())}
    if not w.get_visible():
        said["hidden"] = True
    if not w.get_sensitive():
        said["insensitive"] = True
    if w.get_tooltip_text():
        said["tooltip"] = w.get_tooltip_text()
    if isinstance(w, Gtk.Label):
        said["label"] = w.get_label()
    elif isinstance(w, Gtk.Button):
        said["label"] = w.get_label()
        said["icon"] = w.get_icon_name()
    elif isinstance(w, Gtk.Image):
        said["icon"] = w.get_icon_name()
    if isinstance(w, Adw.PreferencesRow):
        said["title"] = w.get_title()
    if isinstance(w, Adw.ActionRow):
        said["subtitle"] = w.get_subtitle()
        said["activatable"] = w.get_activatable()
    if isinstance(w, Adw.PreferencesGroup):
        said["title"], said["description"] = w.get_title(), w.get_description()
    if isinstance(w, Adw.StatusPage):
        said["title"], said["description"] = w.get_title(), w.get_description()
        said["icon"] = w.get_icon_name()
    if isinstance(w, opaque):
        return said
    children, child = [], w.get_first_child()
    while child is not None:
        children.append(widget_tree(child, opaque))
        child = child.get_next_sibling()
    if children:
        said["children"] = children
    return said


def window_picture(win, path) -> None:
    """The window as it is drawn now, its dialogs and toasts too, at twice its size: a PNG
    at `path` (its folder made)."""
    from pathlib import Path

    from gi.repository import Graphene, Gtk

    w, h = win.get_width(), win.get_height()
    snap = Gtk.Snapshot()
    snap.scale(2, 2)
    _found, bounds = win.compute_bounds(win)
    snap.save()
    snap.translate(Graphene.Point().init(bounds.get_x(), bounds.get_y()))
    Gtk.WidgetPaintable.new(win).snapshot(snap, bounds.get_width(), bounds.get_height())
    snap.restore()
    texture = win.get_native().get_renderer().render_texture(
        snap.to_node(), Graphene.Rect().init(0, 0, w * 2, h * 2))
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    texture.save_to_png(str(path))


# A Drive 53 EX as one was seen on its screenshots and listing: 6.7 GB free of 14.8 GB, and City
# Navigator in four folders of .System taking 7.6 GB (the folder Map 5.26 GB); its new version
# 8.3 GB in the same folders. How the sizes split among JCV, SID and SQLite is made up.
DRIVE_SPACE = (6_700_000_000, 14_800_000_000)
DRIVE_MAP = {"Map": 5_260_000_000, "JCV": 1_100_000_000, "SID": 840_000_000,
             "SQLite": 400_000_000}
DRIVE_NEW_MAP = {"Map": 5_750_000_000, "JCV": 1_200_000_000, "SID": 900_000_000,
                 "SQLite": 450_000_000}


class Section:
    """A stand-in for a section of the device view: its groups kept as plain records (their
    title, rows and the widget at the end of their header), all of them gone at a clear."""

    def __init__(self):
        self.groups = []

    def group(self, title="", description=""):
        group = types.SimpleNamespace(title=title, description=description, rows=[], header=None)
        group.add = group.rows.append
        group.set_header_suffix = lambda widget: setattr(group, "header", widget)
        self.groups.append(group)
        return group

    def clear(self):
        self.groups = []


@contextlib.contextmanager
def in_language(language):
    """openExpress speaks `language` inside the block, English again after it (as the tests
    run), also when the block fails."""
    from openexpress import i18n

    i18n.setup(language)
    try:
        yield
    finally:
        i18n.setup("en")


def maps_window(storage):
    """A stand-in for the window around the Maps page: its groups and rows are kept (the
    groups as plain records), the buttons are real, a reload of a page is recorded."""
    import types

    from openexpress.gui.app import MainWindow

    reloads = []
    win = window(
        session=types.SimpleNamespace(storage=storage), sections={"maps": Section()},
        busy=False, _action_buttons=[], confirm_map=lambda m: None,
        confirm_remove=lambda m: None, _search_filter=lambda search, rows: None,
        load_section=lambda key, force=False: reloads.append((key, force)),
        _set_map_state=lambda result: None)
    window_methods(win, ["_button", "_trash"])
    win._map_subtitle = MainWindow._map_subtitle
    return win, reloads

def window_methods(win, names):
    """Returns the stand-in window `win` with these methods of MainWindow bound to it, each
    looked up along MainWindow's MRO as the window finds it (in app.py or a mixin), so a method
    moved to a mixin stays in the stand-in. A name no class of openExpress defines (renamed,
    removed, or GTK's only) fails at once, naming it."""
    import types

    from openexpress.gui.app import MainWindow

    missing = []
    for name in names:
        owner = next((cls for cls in MainWindow.__mro__ if name in vars(cls)), None)
        if owner is None or not owner.__module__.startswith("openexpress."):
            missing.append(name)
            continue
        member = vars(owner)[name]
        if isinstance(member, staticmethod):
            setattr(win, name, member.__func__)
        elif isinstance(member, classmethod):
            setattr(win, name, types.MethodType(member.__func__, MainWindow))
        else:
            setattr(win, name, types.MethodType(member, win))
    assert not missing, ("MainWindow has no method " + ", ".join(missing)
                         + " (renamed or removed?): change the stand-in to what the window has")
    return win


def tasks_at_once(monkeypatch):
    """The window's thread and main loop, run at once: a task of run_task (and a work of
    in_background) runs and tells its end within the call."""
    import threading
    import types

    from gi.repository import GLib

    # The rest of the module as it is (a count after a write is waited for with an Event).
    replace_in_window(monkeypatch, "threading", types.SimpleNamespace(**{
        **vars(threading), "Thread": lambda target, daemon: types.SimpleNamespace(start=target)}))
    monkeypatch.setattr(GLib, "idle_add", lambda fn, *args: fn(*args))


def usb_devices(monkeypatch, folder):
    """/sys/bus/usb/devices as gio_storage reads it, in `folder`: returns put(devices), which
    lays out Garmin devices in their normal mode, {place ("3-1"): (product, bus, device
    number)}; a device left out is unplugged, one at another device number came back."""
    import shutil

    from openexpress.device import gio_storage

    monkeypatch.setattr(gio_storage, "SYSFS_USB", folder)

    def put(devices):
        shutil.rmtree(folder, ignore_errors=True)
        folder.mkdir(parents=True)
        for place, (product, bus, devnum) in devices.items():
            base = folder / place
            base.mkdir()
            for name, value in (("idVendor", "091e"), ("idProduct", product),
                                ("busnum", str(bus)), ("devnum", str(devnum))):
                (base / name).write_text(value + "\n")

    put({})
    return put



def garmin_on_usb(folder, place="1-4", product_id="0b89", classes=("03", "08"), devnum=9,
                  **files):
    """A Garmin device in a made-up /sys/bus/usb/devices (`folder`, read as SYSFS_USB): its
    folder `place` with vendor 091e, `product_id`, bus 1 and `devnum`, any other file of
    `files` (product="…", serial="…"), and an interface folder "<place>:1.<n>" for each of
    `classes` with its class (None: a folder without it). Returns the device's folder."""
    base = folder / place
    base.mkdir(parents=True)
    for name, value in {"idVendor": "091e", "idProduct": product_id, "busnum": 1,
                        "devnum": devnum, **files}.items():
        (base / name).write_text(f"{value}\n")
    for number, cls in enumerate(classes):
        interface = base / f"{place}:1.{number}"
        interface.mkdir()
        if cls is not None:
            (interface / "bInterfaceClass").write_text(f"{cls}\n")
    return base


# --- a name of the window replaced where the window looks it up -----------------------------


def replace_in_window(monkeypatch, name, value):
    """Replaces `name` (e.g. "eject" or "Adw") in every module of openexpress.gui that holds
    it, never in gui.app alone (test_dead_patches), and returns those modules' names. Fails,
    replacing nothing, when no module holds the name or the modules hold different objects
    under it (some code would not see the replacement). Tests use the fixture in_the_window;
    a helper with `monkeypatch` calls this."""
    import importlib
    import pkgutil

    import openexpress.gui as gui

    modules = [importlib.import_module(f"openexpress.gui.{m.name}")
               for m in pkgutil.iter_modules(gui.__path__)]
    holders = [m for m in modules if name in vars(m)]
    assert holders, f"no module of the window holds {name!r}"
    app = importlib.import_module("openexpress.gui.app")
    original = vars(app)[name] if name in vars(app) else vars(holders[0])[name]
    others = sorted(m.__name__ for m in holders if vars(m)[name] is not original)
    assert not others, (
        f"{name!r} is another object in {', '.join(others)} than in the other modules of "
        "the window: the code there would not see the replacement")
    for module in holders:
        monkeypatch.setattr(module, name, value)
    return [module.__name__ for module in holders]


# --- helpers several test modules use ---------------------------------------------------


def page_window(key):
    """A page of the real kind in a window that is never shown, and a stand-in for the main
    window that loads it the way MainWindow does. A task runs at once, in the order run_task
    keeps: busy on, the work, busy off, then the page is rebuilt."""
    import types

    from gi.repository import Gtk

    from openexpress.gui.app import MainWindow
    from openexpress.gui.device_page import Section
    from openexpress.gui.window_tasks import still_in_window

    section = Section(key, key.title(), "x-symbolic", "Loading…")
    toplevel = Gtk.Window()
    toplevel.set_child(section.stack)

    run_task = RunNow(on_call=lambda call: win.set_busy(True),
                      before_done=lambda: win.set_busy(False))

    def set_busy(busy):
        win.busy = busy
        win._action_buttons = still_in_window(win._action_buttons)

    win = window(session=types.SimpleNamespace(), sections={key: section},
                 busy=False, _action_buttons=[], _retry_sources={},
                 run_task=run_task, set_busy=set_busy, toplevel=toplevel)
    for name in ("_load", "_button", "_trash"):
        setattr(win, name, types.MethodType(getattr(MainWindow, name), win))
    return win, section


# What the Apps page asks of the window to show the apps on the device and to switch one.
APPS_PAGE_METHODS = ("_updates_to_offer", "_apps_space", "_waiting_note", "_app_icon",
                     "_order_blocked", "_store_type_name", "_search_filter", "show_apps",
                     "toggle_app")


def apps_page(apps, **changes):
    """The Apps page (page_window) of a made-up watch not signed in, filled with `apps`
    (ciq.InstalledApp) on the device and nothing from the account; `changes`: other attributes
    of the stand-in window, set before the page is filled. Returns the stand-in and the page."""
    from openexpress import ciq

    win, section = page_window("apps")
    device = types.SimpleNamespace(unit_id=3000000001, software_version_str="23.39",
                                   description="Made-up watch")
    win.session = types.SimpleNamespace(device=device, storage=None, logged_in=False,
                                        ownership_error=None)
    win._updated_apps = set()
    win.toast = lambda text: None
    win.load_section = lambda key, force=False: None
    win._load_app_icons = lambda icons: None
    for name, value in changes.items():
        setattr(win, name, value)
    window_methods(win, APPS_PAGE_METHODS)
    win.show_apps((ciq.InstalledState(apps=list(apps), available_space=10**9,
                                      available_slots=31), [], []))
    return win, section


# What MainWindow.show_preferences asks of the window to build the groups of the preferences.
PREFERENCES_METHODS = ("_button", "_cache_group", "_ask_clear_cache", "_account_group",
                       "_account_action", "_auto_sync_row")


def preferences_window(**changes):
    """A stand-in for the window around its preferences (MainWindow.show_preferences): not
    busy, background sync off, nothing it is asked to do done; the groups built by the
    window's own methods; `changes`: other attributes, or other values of these."""
    win = window(**{"busy": False, "_action_buttons": [], "_background_enabled": lambda: False,
                    "set_background": lambda on, done=None: None, "choose_dll": lambda: None,
                    "error": lambda title, text: None, "run_task": None, "toast": None,
                    **changes})
    return window_methods(win, PREFERENCES_METHODS)


# The view of a remembered device and its pictures (once picked by name: "picture",
# "remembered"), found along the whole MRO (window_methods).
REMEMBERED_METHODS = ("_show_remembered", "_show_account", "_fill_account", "_last_sync_text",
                      "_fill_remembered", "_load_tile_pictures", "_on_remembered_page",
                      "_redraw_remembered", "_refresh_remembered", "_refresh_remembered_texts",
                      "_show_device_picture", "_show_page_picture", "_tile_picture_done")


def remembered_device(category="watch", part="006-B0000-00"):
    """A made-up device remembered an hour ago."""
    import time

    from openexpress.known_devices import KnownDevice

    now = time.time()
    return KnownDevice(unit_id=1234567890, description="Test watch", part_number=part,
                       software_version="1.00", kind="mtp", first_seen=now - 86400,
                       last_seen=now - 3600, category=category)


def remembered_window(textures=None):
    """The parts of the window that open the view, with the real view in it; background work
    runs at once."""
    import types

    from openexpress.gui.start import RememberedPage
    from openexpress.gui.status_page import show_kept_status

    pushed = []
    win = window(
        remembered=RememberedPage(), navigation=types.SimpleNamespace(push=pushed.append),
        pushed=pushed, _device_textures=dict(textures or {}), _pictures_asked=set(),
        _account=None, _on_start_page=lambda: True,
        in_background=lambda fn, on_done, on_error=None: on_done(fn()))
    # As the window connects it: its page Device Status from the records.
    win.remembered.status_row.connect("activated", lambda _row: show_kept_status(win))
    return window_methods(win, REMEMBERED_METHODS)


RTL515_XML = """<?xml version="1.0" encoding="UTF-8"?>
<Device xmlns="http://www.garmin.com/xmlschemas/GarminDevice/v2">
 <Model><PartNumber>006-B3592-03</PartNumber><SoftwareVersion>336</SoftwareVersion>
  <Description>Varia RTL515</Description></Model>
 <Id>3000000003</Id>
 <MassStorageMode>
  <UpdateFile><PartNumber>006-B3592-03</PartNumber>
   <Version><Major>3</Major><Minor>36</Minor></Version>
   <Path>GARMIN/</Path><FileName>GUPDATE.GCD</FileName></UpdateFile>
  <UpdateFile><PartNumber>006-B3593-06</PartNumber>
   <Version><Major>3</Major><Minor>27</Minor></Version>
   <Path>GARMIN/REMOTESW/</Path><FileName>6B359306.GCD</FileName></UpdateFile>
 </MassStorageMode>
</Device>
"""


class UpdateServer:
    """Stands in for the HTTP session of GarminClient: records each request, answers with JSON."""

    def __init__(self, answer):
        self.answer, self.sent = answer, []

    def request(self, method, url, **kw):
        import json

        import requests

        self.sent.append((method, url, kw.get("params"), kw.get("json")))
        resp = requests.Response()
        resp.status_code, resp.url = 200, url
        resp._content = json.dumps(self.answer).encode()
        resp.headers["Content-Type"] = "application/json"
        return resp


DEVICES_PATH = "device-service/deviceregistration/devices"
DEVICE_UNIT = 1234567890  # the test device (conftest.DEVICE_XML)
OTHER_UNIT = 4222333444  # another device of the account


def response(status=200, body=b"[]"):
    import json

    import requests

    r = requests.Response()
    r.status_code = status
    r._content = body if isinstance(body, bytes) else json.dumps(body).encode()
    return r


class RecordingSession:
    """Stands in for requests.Session: records every request, answers from `routes`."""

    def __init__(self, devices=None, **routes):
        self.routes = {DEVICES_PATH: response(body=devices if devices is not None else [])}
        self.routes.update(routes)
        # url part -> function(method, request kwargs) -> response, or None for the routes
        self.handlers = {}
        self.sent = []

    def request(self, method, url, **kw):
        self.sent.append({"method": method, "url": url, "headers": kw.get("headers") or {},
                          "body": repr((kw.get("data"), kw.get("json"), kw.get("files")))})
        for part, handler in self.handlers.items():
            if part in url:
                answer = handler(method, kw)
                if answer is not None:  # None: the routes answer it
                    return answer
        for part, answer in self.routes.items():
            if part in url:
                if isinstance(answer, Exception):
                    raise answer
                return answer() if callable(answer) else answer
        return response(404, b"")

    def get(self, url, **kw):
        return self.request("GET", url, **kw)

    def signed_for(self, unit_id):
        """Token-signed requests that name this watch."""
        return [r for r in self.sent
                if "Authorization" in r["headers"] and str(unit_id) in r["url"] + r["body"]]

    def paths(self):
        return [r["url"].split("garmin.com/")[-1].split("?")[0] for r in self.sent]


def another_account() -> None:
    """The next client is of another Garmin Connect account: what the one before was told of
    its account does not hold for it (in the program another account is another login, so
    another key of the kept answers; in a test the login stays the same)."""
    from openexpress import answers

    answers.ANSWERS.forget(answers.ACCOUNT_KINDS)


def make_client(devices=None, auth=True, **routes):
    """A client of a Garmin Connect of its own (RecordingSession)."""
    from openexpress.auth.store import ConnectAuth
    from openexpress.connect import GarminClient
    from openexpress.settings import Consumer, Settings

    return GarminClient(Settings(), Consumer("ck", "cs"),
                        ConnectAuth("t", "ts") if auth else None,
                        RecordingSession(devices, **routes))


def no_answer(error):
    """What the client raises when a request of its gets no answer (`error`, a requests
    error): its own error, made by its own code (the upload settings, asked of a session that
    raises `error`)."""
    from openexpress.connect import ApiError

    try:
        make_client(UnitUploadSettings=error).upload_settings(DEVICE_UNIT)
    except ApiError as raised:
        return raised
    raise AssertionError("the client raised nothing")


FIT_EPOCH = int(datetime(1989, 12, 31, tzinfo=UTC).timestamp())


def fit_time(*when) -> int:
    """A moment of local time as a time of the FIT protocol."""
    return int(datetime(*when).timestamp()) - FIT_EPOCH


# Made-up FIT files. The base types of the FIT protocol, by the byte of a field definition:
# the struct format of one value and the value that means "invalid" (written here apart from
# the program's own table, openexpress/fit.py).
FIT_BASE_TYPES = {
    0x00: ("B", 0xFF), 0x01: ("b", 0x7F), 0x02: ("B", 0xFF), 0x83: ("h", 0x7FFF),
    0x84: ("H", 0xFFFF), 0x85: ("i", 0x7FFFFFFF), 0x86: ("I", 0xFFFFFFFF), 0x07: ("s", None),
    0x88: ("f", float("nan")), 0x89: ("d", float("nan")), 0x0A: ("B", 0), 0x8B: ("H", 0),
    0x8C: ("I", 0), 0x0D: ("B", 0xFF), 0x8E: ("q", 0x7FFFFFFFFFFFFFFF),
    0x8F: ("Q", 0xFFFFFFFFFFFFFFFF), 0x90: ("Q", 0),
}


@dataclass(frozen=True)
class FitDefinition:
    """A definition message: its local type (0–15), the global message number, the fields as
    (field number, base type) or (field number, base type, size) – a string or an array needs
    its size – and developer fields as (field number, size, developer data index)."""

    local: int
    message: int
    fields: tuple = ()
    big_endian: bool = False
    developer: tuple = ()


@dataclass(frozen=True)
class FitData:
    """A data message of the definition last given for `local`: one value for each of its
    fields (None: the invalid value of the field's base type; bytes: written as they are; a
    tuple: an array). `developer`: the bytes of its developer fields (zeros when None).
    `time_offset` (0–31): a compressed-timestamp header instead of the normal one (local
    types 0–3 only)."""

    local: int
    values: tuple = ()
    developer: bytes | None = None
    time_offset: int | None = None


def _fit_size(field: tuple) -> tuple[int, int, int]:
    """(number, base type, size) of a field given as (number, base type[, size])."""
    if len(field) > 2:
        return field[0], field[1], field[2]
    return field[0], field[1], struct.calcsize(FIT_BASE_TYPES[field[1]][0])


def _fit_value(value, base_type: int, size: int, order: str) -> bytes:
    if isinstance(value, bytes):
        assert len(value) == size, f"{len(value)} bytes for a field of {size}"
        return value
    fmt, invalid = FIT_BASE_TYPES[base_type]
    if fmt == "s":
        raw = (value or "").encode("utf-8").ljust(size, b"\0")
    else:
        count = size // struct.calcsize(fmt)
        values = value if isinstance(value, tuple) else (value,) * count
        raw = b"".join(struct.pack(order + fmt, invalid if v is None else v) for v in values)
    assert len(raw) == size, f"{len(raw)} bytes for a field of {size}"
    return raw


def fit_file(*messages, header_size: int = 14, cut: int | None = None,
             bad_crc: bool = False) -> bytes:
    """A made-up FIT file of these messages (FitDefinition and FitData, in order; bytes are
    written as they are, e.g. a message no definition explains), its header of 14 or 12 bytes
    and its CRC right. `cut`: only its first `cut` bytes, as a file the device was still
    writing; `bad_crc`: the CRC at its end wrong."""
    from conftest import fit_crc

    defined = {}
    body = b""
    for m in messages:
        if isinstance(m, bytes):
            body += m
        elif isinstance(m, FitDefinition):
            order = ">" if m.big_endian else "<"
            fields = [_fit_size(f) for f in m.fields]
            body += bytes([0x40 | (0x20 if m.developer else 0) | m.local, 0,
                           1 if m.big_endian else 0])
            body += struct.pack(order + "HB", m.message, len(fields))
            body += b"".join(bytes([num, size, base]) for num, base, size in fields)
            if m.developer:
                body += bytes([len(m.developer)])
                body += b"".join(bytes(f) for f in m.developer)
            defined[m.local] = (m, fields)
        else:
            definition, fields = defined[m.local]
            assert len(m.values) == len(fields), "one value for each field"
            if m.time_offset is None:
                body += bytes([m.local])
            else:
                body += bytes([0x80 | (m.local << 5) | m.time_offset])
            order = ">" if definition.big_endian else "<"
            body += b"".join(_fit_value(v, base, size, order)
                             for v, (_num, base, size) in zip(m.values, fields, strict=True))
            dev_size = sum(f[1] for f in definition.developer)
            body += m.developer if m.developer is not None else bytes(dev_size)
    if header_size == 14:
        header = struct.pack("<BBHI4sH", 14, 0x20, 2132, len(body), b".FIT", 0)
    else:
        header = struct.pack("<BBHI4s", 12, 0x10, 2100, len(body), b".FIT")
    crc = fit_crc(header + body) ^ (0xFFFF if bad_crc else 0)
    data = header + body + struct.pack("<H", crc)
    return data if cut is None else data[:cut]


# Made-up GPX files: a course to copy to a device (course_gpx, courses).
GPX_NAMESPACE = "http://www.topografix.com/GPX/1/1"


def gpx_file(body: str, ns: str = GPX_NAMESPACE,
             decl: str = '<?xml version="1.0" encoding="UTF-8"?>\n') -> str:
    return f'{decl}<gpx version="1.1" creator="made up" xmlns="{ns}">\n{body}</gpx>\n'


def gpx_track(name: str | None = "Morning Loop",
              points=((52.0, 13.0, 30.0), (52.0, 13.01, 40.0), (52.0, 13.02, 35.0))) -> str:
    named = f"<name>{name}</name>" if name is not None else ""
    pts = "".join(f'<trkpt lat="{la}" lon="{lo}"><ele>{e}</ele></trkpt>' for la, lo, e in points)
    return f"  <trk>{named}<trkseg>{pts}</trkseg></trk>\n"


def gpx_route(name="To the Lake", points=((52.0, 13.0), (52.01, 13.0), (52.02, 13.01))) -> str:
    pts = "".join(f'<rtept lat="{la}" lon="{lo}"/>' for la, lo in points)
    return f"  <rte><name>{name}</name>{pts}</rte>\n"


def gpx_waypoint(name="Spring", lat=52.0, lon=13.0) -> str:
    return f'  <wpt lat="{lat}" lon="{lon}"><name>{name}</name></wpt>\n'


# Made-up courses: a device that takes GPX files in Garmin/NewFiles, its course files and its
# saved locations (courses, course_gpx).
COURSE_UNIT = 1234


def course_device(gpx_folder="Garmin/NewFiles", extension="GPX", locations="Garmin/Locations"):
    """A made-up device with the declarations of a fēnix 8 and an Edge 1050: courses out of
    Garmin/Courses, GPX files and courses into Garmin/NewFiles, saved locations out of
    `locations` (None: none declared); `gpx_folder` None: no GPX files at all."""
    from openexpress.device.xmldevice import INPUT, OUTPUT, DeviceFile, GarminDevice

    files = [DeviceFile("FIT_TYPE_6", OUTPUT, "Garmin/Courses", extension="FIT"),
             DeviceFile("FIT_TYPE_6", INPUT, "Garmin/NewFiles", extension="FIT")]
    if gpx_folder is not None:
        files.append(DeviceFile("GPSData", INPUT, gpx_folder, extension=extension))
    if locations is not None:
        files.append(DeviceFile("FIT_TYPE_8", OUTPUT, locations, extension="FIT"))
    return GarminDevice(COURSE_UNIT, "006-B0000-00", 100, "Made-up Edge", files)


def course_fit(name: str | None = "Morning Loop", *, made: int, product=20119,
               manufacturer=1, distance_cm: int | None = 1_234_500, ascent: int | None = 321,
               lap_after_points=False, points=3) -> bytes:
    """A made-up course file: file_id (type 6, made at `made`, a Unix time; the product 20119
    is that of a course a device made of a file), its course, one lap and `points` records, the
    lap before them as the devices write it (or after)."""
    at = made - FIT_EPOCH  # the FIT protocol's time
    head = (FitDefinition(0, 0, ((0, 0x00), (1, 0x84), (2, 0x84), (4, 0x86))),
            FitData(0, (6, manufacturer, product, at)),
            FitDefinition(1, 31, ((5, 0x07, 48), (4, 0x00))), FitData(1, (name, 0)))
    lap = (FitDefinition(2, 19, ((9, 0x86), (21, 0x84))), FitData(2, (distance_cm, ascent)))
    records = (FitDefinition(3, 20, ((253, 0x86), (0, 0x85), (1, 0x85))),
               *(FitData(3, (at + n, 0, n * 1000)) for n in range(points)))
    return fit_file(*head, *(records + lap if lap_after_points else lap + records))


def locations_fit(count: int, cut: int | None = None) -> bytes:
    """A made-up locations file (type 8) of `count` saved locations."""
    return fit_file(FitDefinition(0, 0, ((0, 0x00),)), FitData(0, (8,)),
                    FitDefinition(1, 29, ((0, 0x07, 16), (1, 0x85), (2, 0x85))),
                    *(FitData(1, (f"Place {n}", n, n)) for n in range(count)), cut=cut)

# A session of an activity: start, sport, sub-sport, elapsed and timer time, distance,
# calories, average speed and its enhanced field, average and maximum heart rate, ascent.
FIT_SESSION = FitDefinition(2, 18, ((2, 0x86), (5, 0x00), (6, 0x00), (7, 0x86), (8, 0x86),
                                    (9, 0x86), (11, 0x84), (14, 0x84), (124, 0x86), (16, 0x02),
                                    (17, 0x02), (22, 0x84)))
# A record of a time and a heart rate only (a workout).
FIT_HEART_RECORD = FitDefinition(1, 20, ((253, 0x86), (3, 0x02)))


def fit_activity(start: int, *, sport: int = 1, timer_ms: int | None = 3_600_000,
                 distance_cm: int | None = 1_000_000, ascent: int | None = 100,
                 records: int = 1, position: tuple[int, int] | None = None, **options) -> bytes:
    """A made-up activity that starts at `start` (a time of the FIT protocol): its file_id
    created then, `records` records a second apart (at `position`, in semicircles, when given),
    one session of `sport` with these totals (None: the field's invalid value). `options`: as
    fit_file's (cut, bad_crc)."""
    if position is None:
        record_fields, at = ((253, 0x86),), ()
    else:
        record_fields, at = ((253, 0x86), (0, 0x85), (1, 0x85)), position
    return fit_file(
        FitDefinition(0, 0, ((0, 0x00), (4, 0x86))), FitData(0, (4, start)),
        FitDefinition(1, 20, record_fields),
        *(FitData(1, (start + n, *at)) for n in range(records)),
        FitDefinition(2, 18, ((2, 0x86), (5, 0x00), (8, 0x86), (9, 0x86), (22, 0x84))),
        FitData(2, (start, sport, timer_ms, distance_cm, ascent)), **options)


def luminance(colour: str) -> float:
    """The relative luminance of "#rrggbb" (WCAG 2)."""
    def linear(c):
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (linear(int(colour[i:i + 2], 16) / 255) for i in (1, 3, 5))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    """The contrast ratio of two colours "#rrggbb" (WCAG 2): 1 to 21."""
    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


_STYLED: set = set()  # the displays that have the program's own style


def program_style() -> None:
    """The program's own style (gui/start.py: the chips, the tiles, the colours of the sports),
    as the application loads it before its window (App), once for each display: measured or
    drawn without it, a chip would have no padding, an icon of a sport no colour."""
    from gi.repository import Gdk

    from openexpress.gui import start

    display = Gdk.Display.get_default()
    if display not in _STYLED:
        _STYLED.add(display)
        start.load_css(display)


def git(cwd, *args: str) -> str:
    """git in a made-up repository: without the user's configuration, with a made-up author and
    without signing; its output."""
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
           "GIT_AUTHOR_NAME": "A", "GIT_AUTHOR_EMAIL": "a@example.com",
           "GIT_COMMITTER_NAME": "A", "GIT_COMMITTER_EMAIL": "a@example.com"}
    return subprocess.run(["git", "-c", "commit.gpgsign=false", *args], cwd=cwd, env=env,
                          check=True, capture_output=True, text=True).stdout.strip()


def software_update(**fields):
    """A made-up software update (updates.SoftwareUpdate), its fields as given."""
    from openexpress.updates import SoftwareUpdate

    made_up = dict(part_number="006-D0000-00", name="Made-up file", version="1.00", severity="",
                   is_primary=False, is_reinstall=False, restart_required=False,
                   device_path="Garmin/006-D0000-00.bin", url="https://example.com/file.bin",
                   md5="0" * 32, size=1, order=0)
    return SoftwareUpdate(**{**made_up, **fields})


# --- answers of Garmin's servers, GarminDevice.xml and the folders a device hides ------------


def offered_update(part, name, path, filename, data, version="2.18", severity="Recommended",
                   primary=False, installed=True, locale=None, eula=None):
    """One item the update server offers, the keys as it sends them (camelCase): the file
    `filename` with the contents `data`, for the folder `path` on the device."""
    import hashlib

    return {
        "partNumber": part, "productName": name, "dataType": "Firmware",
        "displayVersion": version, "severity": severity, "isPrimaryFirmware": primary,
        "locale": locale, "releaseState": "live", "releaseNotes": [], "eulaUrl": eula,
        "deliverable": {"downloadUrl": "https://example.org/" + filename,
                        "md5": hashlib.md5(data).hexdigest().upper(), "sizeInBytes": len(data)},
        "installation": {"path": path, "fileName": filename, "isReinstall": False,
                         "isProductInstalled": installed, "isRestartRequired": False,
                         "order": 0},
    }


def map_entry(name, path, state=0, part=None, size=4, md5="0123456789ABCDEF0123456789ABCDEF",
              reinstall=False, remove=()):
    """One map the map server offers, its keys in lower case (connect.lower_keys): `name`, of
    one file at `path` on the device, `size` bytes; `state`: its installationstate; `part`: its
    region part number (else its name); `remove`: the files it removes."""
    return {
        "displayname": name, "maptype": "CityNavigator", "installationstate": state,
        "isreinstall": reinstall, "canuninstall": True,
        "release": {"majorversion": 2026, "minorversion": 20},
        "downloadhosts": {"foregroundprimaryhost": "https://example.org/maps/",
                          "failoverhosts": []},
        "filestoremove": [{"filename": p} for p in remove],
        "installoptions": [{"ispreferred": True,
                            "identifier": {"regionpartnumber": name if part is None else part},
                            "files": [{"filename": path, "datatype": "X",
                                       "deliverableoptions": [{
                                           "type": 1, "url": "x.img", "md5": md5,
                                           "sizeinbytes": size}]}]}],
    }


# A component in Garmin/RegionMaps: a device that names it has that folder for its own files.
REGION_COMPONENT = """  <UpdateFile><PartNumber>006-D0005-00</PartNumber><Version><Major>1</Major>
   <Minor>10</Minor></Version><Path>Garmin/RegionMaps</Path><FileName>made_up.rgn</FileName>
  </UpdateFile>
"""


def xml_with(root, block, folder="GARMIN"):
    """`root`, its GarminDevice.xml (in `folder`) with `block` added at the end of what it
    declares (MassStorageMode)."""
    xml = root / folder / "GarminDevice.xml"
    text = xml.read_text()
    assert text.count("</MassStorageMode>") == 1, "a GarminDevice.xml without MassStorageMode"
    xml.write_text(text.replace("</MassStorageMode>", block + "</MassStorageMode>"))
    return root


def hidden_at(folder: str, hidden: list[str]) -> str | None:
    """The folder of `hidden` (the folders a device hides) that `folder` is or is in, in any
    letter case; None when it is seen."""
    key = folder.casefold()
    for h in sorted(hidden, key=len):
        if key == h.casefold() or key.startswith(h.casefold() + "/"):
            return h
    return None


# --- satellite data -----------------------------------------------------------------------------


def made_up_cpe(n: int = 1, blocks: int = 31) -> bytes:
    """A file of the satellite data format known (satellite.known_format): `blocks` of 2,048
    bytes, its first four bytes, the length of its data, zeros after it; the data made up (`n`
    tells two apart)."""
    from openexpress import satellite

    length = 1000 + n
    return (satellite.START + length.to_bytes(4, "little") + bytes([n % 250 + 1]) * (length - 8)
            + bytes(blocks * 2048 - length))


class SatelliteServer:
    """Garmin's address of the satellite data as a test answers it, each answer in turn (an
    exception is raised); `asked`: how many requests came."""

    def __init__(self, *answers):
        self.answers, self.asked = list(answers), 0

    def get(self, url, headers=None, timeout=None):
        from openexpress import satellite

        assert url == satellite.URL and headers == {"Accept": "application/octet-stream"}
        self.asked += 1
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def satellite_answer(content: bytes = b"", status: int = 200,
                     kind: str = "application/octet-stream"):
    """An answer of Garmin's address of the satellite data."""
    import requests

    from openexpress import satellite

    r = requests.Response()
    r.status_code, r._content, r.url = status, content, satellite.URL
    r.reason = "OK" if status == 200 else "Not Found"
    r.headers["content-type"] = kind
    return r


def satellite_server(monkeypatch, *answers) -> SatelliteServer:
    """Garmin's address of the satellite data answered by a SatelliteServer (no network)."""
    from openexpress import useragent

    server = SatelliteServer(*answers)
    monkeypatch.setattr(useragent, "session", lambda *a, **kw: server)
    return server


# --- another openExpress process with a USB session of its own (device.session_mark) -------

_OTHER_SESSION = """
import os, sys, time
sys.path.insert(0, sys.argv[1])
from openexpress.device import session_mark
bus, address, ready, stop, how, quiet = sys.argv[2:8]
mark = session_mark.begin((int(bus), int(address)))
open(ready, "w").write("marked")
end = time.monotonic() + 30
while not os.path.exists(stop) and time.monotonic() < end:
    time.sleep(0.05)
if how == "crash":
    os._exit(1)  # ended in its session: no until, and the kernel lets go of the lock
mark.end(float(quiet))
"""


class OtherProcessSession:
    """Another openExpress process, a real one, marking a session of its own on the device at
    `place` (bus, device number) with the program's session_mark, in the runtime folder
    `runtime` (its XDG_RUNTIME_DIR): marked once it is made; `finish()` ends its session, with a
    quiet of `quiet` seconds after it, or as a process that died in it (`how="crash"`)."""

    def __init__(self, folder, runtime, place=(3, 14), how="end", quiet=15.0):
        import sys
        import time
        from pathlib import Path

        repo = Path(__file__).resolve().parent.parent
        self.ready = Path(folder) / f"marked-{place[0]}-{place[1]}"
        self.stop = Path(folder) / f"stop-{place[0]}-{place[1]}"
        self.process = subprocess.Popen(
            [sys.executable, "-I", "-c", _OTHER_SESSION, str(repo), str(place[0]),
             str(place[1]), str(self.ready), str(self.stop), how, str(quiet)],
            env={**os.environ, "XDG_RUNTIME_DIR": str(runtime)})
        end = time.monotonic() + 20
        while not self.ready.exists() and self.process.poll() is None and time.monotonic() < end:
            time.sleep(0.02)
        if not self.ready.exists():
            self.process.kill()
            raise AssertionError("the other process did not mark its session")

    def finish(self) -> None:
        self.stop.touch()
        self.process.wait(timeout=20)


# --- openExpress's own USB session on a made-up device ---------------------------------------


@contextlib.contextmanager
def ptp_opened(fake):
    """A USB session of openExpress's own on `fake` (ptp_fake.FakeDevice), as exclusive_ptp
    gives one: the device connected anew, a PTP session on it for the block."""
    from openexpress.device import ptp

    fake.reconnect()
    with ptp.Session(fake) as session:
        yield session


def hidden_folder_waits(monkeypatch) -> list:
    """The waits for a USB session of openExpress's own (hidden_folders._sleep), recorded in
    the list returned instead of slept."""
    from openexpress import hidden_folders

    waited = []
    monkeypatch.setattr(hidden_folders, "_sleep", waited.append)
    return waited


# --- several modules: a local HTTP server, the command line's client, an update's answer -----


class Server:
    """A local HTTP server: `routes` maps a path to (status, headers, body) or to a function of
    the request that gives them, `requests` keeps (method, path, headers) of each, `url` is its
    address (conftest's http_server)."""

    def __init__(self):
        self.routes = {}
        self.requests = []
        server = self

        class H(http.server.BaseHTTPRequestHandler):
            def _handle(self):
                n = int(self.headers.get("Content-Length") or 0)
                if n:
                    self.rfile.read(n)
                server.requests.append((self.command, self.path, dict(self.headers)))
                route = server.routes.get(self.path.split("?")[0])
                if route is None:
                    status, headers, body = 404, {}, b""
                elif callable(route):
                    status, headers, body = route(self)
                else:
                    status, headers, body = route
                self.send_response(status)
                for k, v in headers.items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            do_GET = do_POST = _handle

            def log_message(self, *a):
                pass

        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        # A short poll: shutdown() waits for it (the default of 0.5 s took most of the run).
        threading.Thread(target=self.srv.serve_forever, kwargs={"poll_interval": 0.05},
                         daemon=True).start()


# The bytes of a made-up download.
DOWNLOAD_DATA = bytes(range(256)) * 20


def md5hex(b):
    """The MD5 of `b` in hexadecimal, as the servers give it."""
    return hashlib.md5(b).hexdigest()


# The upload settings FakeClient answers: one location, for activities.
CLIENT_UPLOAD_CONFIG = {
    "unittypename": "test",
    "datatypes": [
        {"datatypename": "FIT_TYPE_4", "uploadlocations": [
            {"url": "https://up.example/activity", "requiresauthentication": True}]},
    ],
}


class FakeClient:
    """The Garmin client of the command line (conftest's fake_client): each upload answered
    with `status`, the device's messages `messages`, the upload settings refused with
    `fail_config`."""

    def __init__(self, status=201, messages=None, fail_config=None):
        self.status = status
        self.messages = messages or []
        self.fail_config = fail_config
        self.uploads = []
        self.auth = object()
        self.verified_unit_id = None

    def account_devices(self, fresh=False):
        return [{"unitid": 1234567890}]

    def require_verified(self, unit_id=None):
        assert self.verified_unit_id is not None

    # The account allows uploads, as a normal account does.
    def consent(self, consent_type):
        return {"useroption": "opt-in", "sourcetype": "CUSTOMER",
                "consentenforcementstate": "HARD", "consenttypeid": consent_type}

    def device_sync_preference(self):
        return {"key": "account.deviceSync", "value": "true"}

    def profile_country(self, fresh=False):
        return "PL"

    def upload_settings(self, unit_id):
        if self.fail_config:
            raise self.fail_config
        return CLIENT_UPLOAD_CONFIG

    def device_messages(self, unit_id):
        return self.messages

    def activity_matches(self, unit_id, ids):
        return {i: False for i in ids}

    def upload_file(self, url, data, *, is_zip, last_file, authenticated):
        from openexpress.connect import UploadResult

        self.uploads.append(url)
        return UploadResult(self.status, {}, None, None)

    def poll_location(self, url, delay):
        return 201

    def download_message(self, url, absolute):
        return None

    def ack_message(self, mid):
        pass


class Widget:
    """A stand-in of the widgets the window sets while a task works with the device: what it
    was last told to show (visible, text, icon)."""

    def __init__(self):
        self.visible, self.text, self.icon = None, None, None

    def set_visible(self, visible):
        self.visible = visible

    def set_from_icon_name(self, name):  # the device view's icon of the kind
        self.icon = name

    def set_pixel_size(self, size):
        pass

    def set_size_request(self, width, height):
        pass

    def add_css_class(self, name):
        pass

    def set_text(self, text):
        self.text = text

    def set_tooltip_text(self, text):
        pass

    def set_reveal_child(self, revealed):
        self.visible = revealed

    def set_fraction(self, fraction):
        pass

    def set_sensitive(self, sensitive):
        pass


def update_answer(*entries):
    """An answer of the update server; `entries`: (part, name, datatype, file, data, severity)."""
    import hashlib

    return {"updates": [{
        "partnumber": part, "productname": name, "datatype": datatype, "version": "2.70",
        "severity": severity,
        "installation": {"path": "Garmin\\SID", "filename": file, "isreinstall": False},
        "deliverable": {"downloadurl": "https://example.org/" + file,
                        "md5": hashlib.md5(data).hexdigest().upper(), "sizeinbytes": len(data)},
    } for part, name, datatype, file, data, severity in entries]}


# The bytes of each file the server offers below, by its name.
OFFERED_BYTES = {"DA00100.sid": b"same file", "DA00200.sid": b"new bytes",
                 "DA00300.sid": b"missing", "GUPDATE.GCD": b"fw"}


# --- the view of a remembered device, as the start page opens it -------------------------------


def remembered_tile(known):
    """The tile the start page draws for a remembered device not connected (devicelist.entries),
    which its view takes its picture from."""
    from openexpress import devicelist

    [(tile, _source)] = devicelist.entries([], {known.unit_id: known}, None, logged_in=False)
    return tile


def show_remembered(win, known) -> None:
    """A click on the tile of a remembered device: its view, with that tile."""
    win._show_remembered(known, remembered_tile(known))


def fill_remembered(win, known) -> None:
    """The view of a remembered device filled again, with its tile (_redraw_remembered)."""
    win._fill_remembered(known, remembered_tile(known))


def found_now(monkeypatch, storage) -> None:
    """The device the command line and the window find connected now: `storage`."""
    from openexpress import service

    monkeypatch.setattr(service, "open_device_or_drive", lambda **kw: storage)


# settings.json as the last release before the activity journal wrote it: the fields of its
# Settings (a made-up GUID) and the language chosen in the window; no answer about the journal.
SETTINGS_BEFORE_THE_JOURNAL = {"environment": "production", "client_name": "express",
                   "client_version": "7.29.1.0", "client_platform": "Windows",
                   "client_platform_version": "10.0.19045.0",
                   "client_guid": "00000000-0000-4000-8000-000000000013",
                   "diagnostics_copy": False, "ui_language": "en"}


def settings_before_the_journal() -> None:
    """The program's settings.json as an update from the release before the journal finds it."""
    import json

    from openexpress import paths

    where = paths.settings_file()
    where.parent.mkdir(parents=True, exist_ok=True)
    where.write_text(json.dumps(SETTINGS_BEFORE_THE_JOURNAL, indent=2))
