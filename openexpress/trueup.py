"""TrueUp: aligning daily totals (steps, calories, …) across several Garmin devices.

The flow in openExpress:
  1. Connect queues a FIT_TYPE_39 `check_back` file containing the time at which
     the client should ask again. It is downloaded and acknowledged.
  2. openExpress waits until that time, but never longer than 90 s, for up to 3 rounds.
  3. Connect then queues a `true_up` file, which is downloaded, acknowledged
     and written to the watch.
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
from openexpress.i18n import _
from openexpress.progress import Waiting

log = logging.getLogger(__name__)

DATATYPE = "FIT_TYPE_39"
CHECK_BACK = "check_back"
TRUE_UP = "true_up"
MAX_ROUNDS = 3
MAX_WAIT_S = 90
LONG_DELAY_S = 300


def is_trueup_message(m: dict) -> bool:
    return (m.get("devicexmldatatype") or "") == DATATYPE


def _find(messages: list[dict], kind: str) -> dict | None:
    return next((m for m in messages
                 if is_trueup_message(m) and m.get("messagetype") == kind and m.get("metadata")),
                None)


def _fetch(client: GarminClient, m: dict) -> bytes | None:
    meta = m["metadata"]
    data = client.download_message(meta.get("messageurl") or "", bool(meta.get("absolute")))
    client.ack_message(m.get("messageid"))
    return data


@dataclass
class TrueUpResult:
    written: str | None = None
    waited_s: float = 0.0
    note: str = ""


def run(
    device: GarminDevice,
    storage: Storage,
    client: GarminClient,
    messages: list[dict],
    progress: Callable[[str], None] = lambda s: None,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.time,
) -> TrueUpResult:
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
        wait = MAX_WAIT_S if delay >= LONG_DELAY_S else max(0.0, min(delay, MAX_WAIT_S))
        log.info("TrueUp: check back, waiting %.0f s", wait)
        if wait:
            sleep(wait)
            result.waited_s += wait
        messages = client.device_messages(device.unit_id)
    m = _find(messages, TRUE_UP)
    if m is None:
        result.note = _("Connect has not prepared TrueUp data yet")
        log.info("TrueUp: nothing prepared yet (waited %.0f s)", result.waited_s)
        return result
    progress(_("TrueUp: writing daily totals to the device"))
    data = _fetch(client, m)
    if not data:
        result.note = _("empty TrueUp file")
        log.info("TrueUp: empty file")
        return result
    t = targets[0]
    name = f"{t.basename + '_' if t.basename else ''}{uuid.uuid4()}.{t.extension or 'FIT'}"
    result.written = storage.write(posixpath.join(t.path, name), data)
    log.info("TrueUp: wrote %s to %s (waited %.0f s)", applog.size(len(data)), t.path,
             result.waited_s)
    return result
