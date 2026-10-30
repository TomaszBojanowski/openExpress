"""Fakes shared by the tests (imported as `from fakes import …`, like ptp_fake). Only made-up
data; nothing here reaches a device, the network or the disk of this computer."""

import threading
from collections import namedtuple

Call = namedtuple("Call", "title fn done failed device in_page button")


class RunNow:
    """The window's run_task (gui/window_tasks.py), run at once: the work (in a thread,
    waited for, with `thread=True`), then done or failed; an error `failed` does not take goes
    to `error`: "raise" (default), "record" (into `errors`) or a function (title, words).
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
                 button=None):
        # Unlike the window: no check of `busy`, no bar, no pending refresh afterwards.
        call = Call(title, fn, done, failed, device, in_page, button)
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
                self._failed(title, raised[0], failed)
            elif done is not None:
                done(result[0])  # an error of done is not caught, as in the window
        finally:
            if getattr(fn, "writes_to_device", False):
                self.written.append(title)

    def _failed(self, title, error, failed) -> None:
        if failed is not None and failed(error):
            return
        if self.error == "raise":
            raise error
        words = str(error) or type(error).__name__
        if self.error == "record":
            self.errors.append(words)
        else:
            self.error(title, words)


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


def window(**changes):
    """A stand-in for the window without GTK: every attribute the real window has, with the
    value it has before anything is shown (MainWindow._init_state), then `changes`. None of
    the window's methods: a test gives those it needs, or binds them with window_methods."""
    import types

    from openexpress.gui.app import MainWindow

    win = types.SimpleNamespace()
    MainWindow._init_state(win)
    for name, value in changes.items():
        setattr(win, name, value)
    return win


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

    pushed = []
    win = window(
        remembered=RememberedPage(), navigation=types.SimpleNamespace(push=pushed.append),
        pushed=pushed, _device_textures=dict(textures or {}), _pictures_asked=set(),
        _account=None, _on_start_page=lambda: True,
        in_background=lambda fn, on_done, on_error=None: on_done(fn()))
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
        self.handlers = {}  # url part -> function(method, request kwargs) -> response
        self.sent = []

    def request(self, method, url, **kw):
        self.sent.append({"method": method, "url": url, "headers": kw.get("headers") or {},
                          "body": repr((kw.get("data"), kw.get("json"), kw.get("files")))})
        for part, handler in self.handlers.items():
            if part in url:
                return handler(method, kw)
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


def make_client(devices=None, auth=True, **routes):
    """A client of a Garmin Connect of its own: what another one was told is not kept for it
    (answers; in the program another account is another login, so another key)."""
    from openexpress import answers
    from openexpress.auth.store import ConnectAuth
    from openexpress.connect import GarminClient
    from openexpress.settings import Consumer, Settings

    answers.ANSWERS.clear()
    return GarminClient(Settings(), Consumer("ck", "cs"),
                        ConnectAuth("t", "ts") if auth else None,
                        RecordingSession(devices, **routes))
