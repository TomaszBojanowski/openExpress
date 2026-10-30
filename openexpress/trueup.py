"""TrueUp: aligning daily totals (steps, calories, …) across several Garmin devices.

Connect queues a FIT_TYPE_39 `check_back` file with the time to ask again; it is downloaded
and acknowledged, and openExpress waits (wait_for) and asks again. Connect then queues a
`true_up` file, which is downloaded, acknowledged and written to the watch. A stopped wait
asks and writes nothing more.
"""

from __future__ import annotations

import logging
import posixpath
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass

from openexpress import applog
from openexpress.connect import GarminClient
from openexpress.device.storage import Storage
from openexpress.device.xmldevice import GarminDevice
from openexpress.fit import FitError, true_up_time
from openexpress.i18n import _, duration_text
from openexpress.progress import Step, TimedWait, Waiting, downloading_text, say_to, writing_text

log = logging.getLogger(__name__)

DATATYPE = "FIT_TYPE_39"
CHECK_BACK = "check_back"
TRUE_UP = "true_up"
# The wait, as Garmin Express does it: the whole delay when it is under LONG_DELAY_S, else
# LONG_DELAY_WAIT_S, for up to MAX_ROUNDS rounds; in WAIT_STEPS steps, each told (a wait of
# known length).
MAX_ROUNDS = 3
LONG_DELAY_S = 300  # from this delay on, Garmin Express waits only…
LONG_DELAY_WAIT_S = 90  # …this long
WAIT_STEPS = 10


def is_trueup_message(m: dict) -> bool:
    return (m.get("devicexmldatatype") or "") == DATATYPE


def _find(messages: list[dict], kind: str) -> dict | None:
    return next((m for m in messages
                 if is_trueup_message(m) and m.get("messagetype") == kind and m.get("metadata")),
                None)


def _fetch(client: GarminClient, m: dict) -> bytes | None:
    """The file of a TrueUp message, acknowledged at once: fetched, acknowledged, then
    written, as Garmin Express does it. A check_back is only a request
    to ask again; a true_up written in vain afterwards is lost for Garmin Connect (run says
    so in the log)."""
    meta = m["metadata"]
    data = client.download_message(meta.get("messageurl") or "", bool(meta.get("absolute")))
    client.ack_message(m.get("messageid"))
    return data


@dataclass
class TrueUpResult:
    written: str | None = None
    waited_s: float = 0.0
    note: str = ""
    stopped: bool = False  # the wait was stopped: nothing more was asked or written


def wait_for(delay: float) -> float:
    """How long to wait for a check_back `delay` s ahead, as Garmin Express does."""
    if delay <= 0:
        return 0.0
    return delay if delay < LONG_DELAY_S else float(LONG_DELAY_WAIT_S)


def stopped_text() -> str:
    return _("Stopped waiting for TrueUp; Garmin Connect sends it with the next sync.")


def _wait(seconds: float, progress, sleep, stop, result: TrueUpResult) -> bool:
    """Wait `seconds` in WAIT_STEPS steps, each told; False when it was stopped (`stop` set,
    or Ctrl+C: only here is it taken for "stop waiting", so that the sync ends as usual)."""
    length = duration_text(seconds)
    # The command line has room to say why; the window's bar says it short, the whole
    # sentence in its tooltip.
    text = _("Waiting {time} for Garmin Connect to prepare TrueUp (the daily totals of all "
             "your devices)…").format(time=length)
    # TRANSLATORS: the bar at the bottom of the window while TrueUp waits {time} for Garmin
    # Connect, beside its filling bar and "Stop Waiting": as short as it can be, it has to fit
    # the narrowest window whole ("TrueUp: 4 min 59 s…"; the whole sentence is its tooltip)
    short = _("TrueUp: {time}…").format(time=length)
    step = seconds / WAIT_STEPS
    progress(TimedWait(text, seconds, 0.0, short=short, about=text))
    for i in range(1, WAIT_STEPS + 1):
        try:
            if stop is not None:
                if stop.wait(step):
                    return False
            else:
                sleep(step)
        except KeyboardInterrupt:
            log.info("TrueUp: Ctrl+C during the wait")
            return False
        result.waited_s += step
        progress(TimedWait(text, seconds, i / WAIT_STEPS, short=short, about=text))
    return True


def run(
    device: GarminDevice,
    storage: Storage,
    client: GarminClient,
    messages: list[dict],
    progress: Callable[[str], None] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.time,
    stop=None,  # set by the window's Stop Waiting, or the background service quitting
) -> TrueUpResult:
    """`stop`: a threading.Event that stops the wait when it is set (its wait() is a step of
    the wait); without it the wait sleeps. Ctrl+C during the wait stops it the same way."""
    progress = say_to(progress)
    result = TrueUpResult()
    targets = device.targets(DATATYPE)
    if not targets:
        result.note = _("the device does not support TrueUp")
        log.info("TrueUp: not supported by the device")
        return result
    for _round in range(MAX_ROUNDS):
        m = _find(messages, CHECK_BACK)
        if m is None:
            break
        progress(Waiting(_("TrueUp: waiting for Garmin Connect…")))
        data = _fetch(client, m)
        try:
            when = true_up_time(data or b"")
        except FitError:
            when = None
        delay = 0.0 if when is None else when - now()
        wait = wait_for(delay)
        log.info("TrueUp: check back (round %d), waiting %.0f s", _round + 1, wait)
        if wait and not _wait(wait, progress, sleep, stop, result):
            result.stopped = True
            result.note = stopped_text()
            log.info("TrueUp: wait stopped after %.0f s, nothing more asked", result.waited_s)
            progress(stopped_text())
            return result
        messages = client.device_messages(device.unit_id)
    m = _find(messages, TRUE_UP)
    if m is None:
        result.note = _("Connect has not prepared TrueUp data yet")
        log.info("TrueUp: nothing prepared yet (waited %.0f s)", result.waited_s)
        return result
    progress(Step(downloading_text("TrueUp")))
    data = _fetch(client, m)
    if not data:
        result.note = _("empty TrueUp file")
        log.info("TrueUp: empty file")
        return result
    t = targets[0]
    name = f"{t.basename + '_' if t.basename else ''}{uuid.uuid4()}.{t.extension or 'FIT'}"
    progress(Step(writing_text("TrueUp")))
    try:
        result.written = storage.write_checked(posixpath.join(t.path, name), data)
    except Exception as e:
        log.warning("TrueUp: Garmin Connect was told it was received before it was written, "
                    "as Garmin Express does, and writing it failed (%s): this TrueUp is lost",
                    type(e).__name__)
        raise
    log.info("TrueUp: wrote %s to %s (waited %.0f s)", applog.size(len(data)), t.path,
             result.waited_s)
    return result
