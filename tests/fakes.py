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

    def account_devices(self):
        self.asked.append("account")
        return [{"unitid": self.UNIT}]

    def consent(self, consent_type):
        self.asked.append("consent")
        return OPT_IN if self.consent_given else {**OPT_IN, "useroption": "opt-out"}

    def device_sync_preference(self):
        self.asked.append("device sync")
        return {"key": "account.deviceSync", "value": "true"}

    def profile_country(self):
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
