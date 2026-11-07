"""How Garmin's servers answered the requests the program really made.

Nothing here asks a server: every request reports how it ended, and the window shows the
state that follows from the last answers (the button with a coloured dot at the bottom of the
side panel, and its popover with each service). Kept in memory for this process only; shown,
never acted upon.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

from openexpress.i18n import N_, _, pgettext, relative_time

log = logging.getLogger(__name__)

# The services, in the order the popover lists them.
CONNECT, OMT, SIGN_IN = "connect", "omt", "sign-in"
SERVICES = (CONNECT, OMT, SIGN_IN)
SIGNED = (CONNECT, SIGN_IN)  # requests signed with the sign-in; those to OMT are not

# How a request ended.
OK, NETWORK, SERVER, AUTH, CLOCK = "ok", "network", "server", "auth", "clock"

# The state of the dot.
UNKNOWN, ONLINE, OFFLINE, SERVER_PROBLEM, SIGN_IN_EXPIRED, CLOCK_OFF = (
    "unknown", "online", "offline", "server-problem", "sign-in-expired", "clock-off")

# Beyond this, a refused request is put down to the computer's clock, not the sign-in.
MAX_CLOCK_SKEW_S = 59 * 60


@dataclass(frozen=True)
class Result:
    kind: str
    at: float  # time.time() of the answer (or of the failure)
    host: str
    status: int | None = None
    minutes: int | None = None  # clock difference, for CLOCK


def clock_skew(resp) -> float:
    """Seconds between this computer's clock and the Date of a server's answer; 0 when the
    answer has no usable Date."""
    date = (getattr(resp, "headers", None) or {}).get("Date")
    if not date:
        return 0.0
    try:
        return abs(parsedate_to_datetime(date).timestamp() - time.time())
    except (TypeError, ValueError):
        return 0.0


def service_of(url: str, hosts: dict[str, str]) -> str | None:
    """The service a request goes to, from the hosts of the environment; None for others.
    The ticket exchange goes to Garmin Connect's host but is part of signing in."""
    parts = urlsplit(url)
    host = parts.netloc.casefold()

    def same(key: str) -> bool:
        return bool(hosts.get(key)) and urlsplit(hosts[key]).netloc.casefold() == host

    if same("sso") or (same("connectapi") and parts.path.startswith("/oauth-service/")):
        return SIGN_IN
    if same("connectapi"):
        return CONNECT
    if same("omt"):
        return OMT
    return None


def kind_of(status: int, skew: float = 0.0, signed: bool = True) -> str:
    """How an answer with this HTTP status ended. A refusal of an unsigned request says
    nothing of the sign-in or the clock: the server answered."""
    if status in (401, 403):
        if not signed:
            return OK
        return CLOCK if skew > MAX_CLOCK_SKEW_S else AUTH
    if status >= 500:
        return SERVER
    return OK  # also 404 and the like: the server answered


class Status:
    """The last result of each service and the time of its last good answer. Thread-safe:
    requests run in worker threads; listeners are called in the thread of the request."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last: dict[str, Result] = {}
        self._answered: dict[str, float] = {}
        self._listeners: list[Callable[[], None]] = []
        # The last result of each service that was an answer (not a lost connection).
        self._replied: dict[str, Result] = {}
        self._changed: float | None = None  # when the computer's network last changed

    def note(self, service: str | None, result: Result) -> None:
        if service not in SERVICES:
            return
        with self._lock:
            self._last[service] = result
            if result.kind != NETWORK:
                self._replied[service] = result
            if result.kind == OK:
                self._answered[service] = result.at
            listeners = list(self._listeners)
        for fn in listeners:
            fn()

    def note_answer(self, service: str | None, url: str, status: int, skew: float = 0.0) -> None:
        kind = kind_of(status, skew, signed=service in SIGNED)
        self.note(service, Result(kind, time.time(), urlsplit(url).netloc, status,
                                  int(skew // 60) if kind == CLOCK else None))

    def note_failure(self, service: str | None, url: str) -> None:
        self.note(service, Result(NETWORK, time.time(), urlsplit(url).netloc))

    def snapshot(self) -> tuple[dict[str, Result], dict[str, float]]:
        """Every service's last result and last good answer, as the rows show them."""
        with self._lock:
            return dict(self._last), dict(self._answered)

    def network_changed(self, at: float | None = None) -> None:
        """The computer's network changed (Gio.NetworkMonitor). Nothing is asked and the state
        stays as it is until a request ends; from then on (counted) a request that got no
        answer before the change no longer counts."""
        with self._lock:
            self._changed = time.time() if at is None else at

    def counted(self) -> tuple[dict[str, Result], dict[str, float]]:
        """What the state and the rows of the window come from: the last results, but once a
        request ended after the network last changed, a service whose last request before it
        got no answer (that tells of the network then, not now) counts with its last answer
        before that, or not at all when it never answered. A refusal of the sign-in, a wrong
        clock or a server error before it still count."""
        with self._lock:
            last, answered, changed = dict(self._last), dict(self._answered), self._changed
            replied = dict(self._replied)
        if changed is None or not any(r.at > changed for r in last.values()):
            return last, answered
        counted = {}
        for s, r in last.items():
            if r.kind == NETWORK and r.at <= changed:
                if s in replied:
                    counted[s] = replied[s]
            else:
                counted[s] = r
        return counted, answered

    def state(self) -> str:
        return state_of(*self.counted())

    def add_listener(self, fn: Callable[[], None]) -> None:
        with self._lock:
            self._listeners.append(fn)

    def remove_listener(self, fn: Callable[[], None]) -> None:
        with self._lock:
            if fn in self._listeners:
                self._listeners.remove(fn)

    def clear(self) -> None:
        with self._lock:
            self._last.clear()
            self._answered.clear()
            self._replied.clear()
            self._changed = None


def state_of(last: dict[str, Result], answered: dict[str, float]) -> str:
    """The state of the dot from the last result of each service, worst first."""
    if not last:
        return UNKNOWN
    # A rejected sign-in counts until a request that needs it works again.
    signed_ok = max((answered.get(s, 0.0) for s in (CONNECT, SIGN_IN)), default=0.0)
    if any(r.kind == AUTH and r.at > signed_ok for r in last.values()):
        return SIGN_IN_EXPIRED
    if any(r.kind == CLOCK for r in last.values()):
        return CLOCK_OFF
    # Only what came after the last good answer tells whether the connection is lost.
    newest_ok = max(answered.values(), default=0.0)
    since = [r for r in last.values() if r.at > newest_ok]
    if since and all(r.kind == NETWORK for r in since):
        return OFFLINE  # nothing answered since: this computer has no connection
    if any(r.kind in (NETWORK, SERVER) for r in last.values()):
        return SERVER_PROBLEM  # a server error, or only some of Garmin's servers answer
    return ONLINE


STATUS = Status()


def record_response(hosts: dict[str, str], url: str, resp) -> None:
    """Called after every answer of a request; never fails the request."""
    try:
        STATUS.note_answer(service_of(url, hosts), url, resp.status_code, clock_skew(resp))
    except Exception as e:  # noqa: BLE001 - the state is only shown
        log.debug("network state not recorded: %s", e)


def record_failure(hosts: dict[str, str], url: str) -> None:
    """Called when a request got no answer (network error); never fails the request."""
    try:
        STATUS.note_failure(service_of(url, hosts), url)
    except Exception as e:  # noqa: BLE001 - the state is only shown
        log.debug("network state not recorded: %s", e)


def record(service: str, url: str, status: int | None, resp=None) -> None:
    """For requests outside the client (file downloads, the Connect IQ store): the service
    is known; `status` None means no answer. `resp`, when given, tells the clock difference."""
    try:
        if status is None:
            STATUS.note_failure(service, url)
        else:
            STATUS.note_answer(service, url, status, clock_skew(resp))
    except Exception as e:  # noqa: BLE001 - the state is only shown
        log.debug("network state not recorded: %s", e)


# --- what the window shows ------------------------------------------------------------------

STATE_TEXT = {
    # TRANSLATORS: state of Garmin's servers, at the bottom of the side panel
    UNKNOWN: N_("Status unknown"),
    # TRANSLATORS: state of Garmin's servers at the bottom of the side panel: they answer
    ONLINE: N_("Online"),
    # TRANSLATORS: state of Garmin's servers at the bottom of the side panel: nothing answered,
    # the computer has no connection
    OFFLINE: N_("No connection"),
    # TRANSLATORS: state of Garmin's servers at the bottom of the side panel: a server error, or
    # only some of the servers answer
    SERVER_PROBLEM: N_("Garmin server problem"),
    # TRANSLATORS: state of Garmin's servers at the bottom of the side panel: they refuse the
    # sign-in, which must be renewed
    SIGN_IN_EXPIRED: N_("Sign-in expired"),
    # TRANSLATORS: state of Garmin's servers at the bottom of the side panel: they refuse the
    # sign-in because the computer's clock differs too much from theirs
    CLOCK_OFF: N_("Computer clock is off"),
}
SERVICE_NAME = {
    # TRANSLATORS: the parts of Garmin's servers, in the popover of the server state button
    CONNECT: N_("Garmin Connect"),
    # TRANSLATORS: a part of Garmin's servers, in the popover of the server state button: the
    # one for software updates and maps
    OMT: N_("Updates and maps"),
    # TRANSLATORS: a part of Garmin's servers, in the popover of the server state button: the
    # sign-in server (a noun, not a button)
    SIGN_IN: N_("Sign-in"),
}
_HOST_KEY = {CONNECT: "connectapi", OMT: "omt", SIGN_IN: "sso"}


def _day(ts: float, now: float | None) -> tuple[bool, str, str]:
    """(today?, date, hour) of a moment, the date in the format of the language."""
    t, today = time.localtime(ts), time.localtime(now if now is not None else time.time())
    # TRANSLATORS: strftime format of a date in the popover of the server state button
    return t[:3] == today[:3], time.strftime(_("%Y-%m-%d"), t), time.strftime("%H:%M", t)


def when(ts: float, now: float | None = None) -> str:
    """The time of an answer on its own: the hour today, with the date on another day."""
    today, date, hour = _day(ts, now)
    if today:
        return hour
    # TRANSLATORS: a date and an hour, e.g. 2026-10-14 23:41
    return pgettext("date and hour", "{date} {time}").format(date=date, time=hour)


def at(ts: float, now: float | None = None) -> str:
    """The time of an answer after a word: "at 23:41", on another day with the date."""
    today, date, hour = _day(ts, now)
    if today:
        # TRANSLATORS: the time of an answer today, after a word: "answered at 23:41"
        return _("at {time}").format(time=hour)
    # TRANSLATORS: the time of an answer on another day: "answered at 2026-10-14 23:41"
    return _("at {date} {time}").format(date=date, time=hour)


def ago(ts: float, now: float | None = None) -> str:
    """The time of an answer in the popover: "just now", "5 min ago", "yesterday, 23:41"."""
    return relative_time(datetime.fromtimestamp(ts),
                         datetime.fromtimestamp(now if now is not None else time.time()))


def at_full(ts: float) -> str:
    """ "at 2026-10-14 23:41": always with the date (the tooltip of a row)."""
    t = time.localtime(ts)
    return _("at {date} {time}").format(date=time.strftime(_("%Y-%m-%d"), t),
                                         time=time.strftime("%H:%M", t))


# TRANSLATORS: heading of the window with the state of Garmin's servers, when all is well
CONNECTED_HEADING = N_("Connected to Garmin's servers")
# TRANSLATORS: at the bottom of the window with the state of Garmin's servers
NOTE = N_("The state comes from the program's last requests.")


@dataclass(frozen=True)
class Row:
    """One service, as the window shows it."""
    service: str
    name: str
    host: str
    kind: str | None  # None: the program has not needed the service yet
    detail: str  # how it answered, with the time: "server error (HTTP 503) at 00:15"
    last_answer: str  # "last answer at …" after a failure; "" otherwise
    outcome: str = ""  # the same without the time, short: "server error (HTTP 503)"
    time: str = ""  # "just now", "5 min ago", "yesterday, 23:41"; "" when not asked yet
    answered: str = ""  # "last answer at 2026-10-14 23:41", with the date; "" if none yet


def heading(state: str) -> str:
    return _(CONNECTED_HEADING) if state == ONLINE else _(STATE_TEXT[state])


def rows(last: dict[str, Result], answered: dict[str, float], hosts: dict[str, str],
         now: float | None = None) -> list[Row]:
    """Each service: its name, its host and how it last answered, with the time."""
    out = []
    for s in SERVICES:
        r = last.get(s)
        host = r.host if r else urlsplit(hosts.get(_HOST_KEY[s], "")).netloc
        if r is None:
            # The words of the popover with no service asked yet, for screen readers after the
            # name of a service this window has not asked.
            detail = outcome = _("Not checked yet in this window")
        elif r.kind == OK:
            # TRANSLATORS: a part of Garmin's servers answered well, read by screen readers; {at}:
            # "at 23:41", on another day with the date
            detail = _("answered {at}").format(at=at(r.at, now))
            # TRANSLATORS: how a service of Garmin last answered, beside the time
            outcome = _("answered")
        elif r.kind == NETWORK:
            # TRANSLATORS: a part of Garmin's servers could not be reached, read by screen readers;
            # {at}: "at 23:41", on another day with the date
            detail = _("no connection {at}").format(at=at(r.at, now))
            # TRANSLATORS: a part of Garmin's servers could not be reached: the word beside its time
            # in the popover of the server state button
            outcome = _("no connection")
        elif r.kind == SERVER:
            detail = _("server error (HTTP {status}) {at}").format(
                status=r.status, at=at(r.at, now))
            outcome = _("server error (HTTP {status})").format(status=r.status)
        elif r.kind == AUTH:
            detail = _("sign-in refused (HTTP {status}) {at}").format(
                status=r.status, at=at(r.at, now))
            outcome = _("sign-in refused (HTTP {status})").format(status=r.status)
        else:
            detail = _("computer clock off by {minutes} min at {time}").format(
                minutes=r.minutes, time=when(r.at, now))
            outcome = _("clock off by {minutes} min").format(minutes=r.minutes)
        # TRANSLATORS: when a part of Garmin's servers last answered well (screen readers, tooltip
        # of its row); {at}: "at 23:41" or with the date
        last_answer = (_("last answer {at}").format(at=at(answered[s], now))
                       if r is not None and r.kind != OK and s in answered else "")
        good = r.at if r is not None and r.kind == OK else answered.get(s)
        out.append(Row(s, _(SERVICE_NAME[s]), host or "?", r.kind if r else None, detail,
                       last_answer, outcome, ago(r.at, now) if r else "",
                       _("last answer {at}").format(at=at_full(good)) if good else ""))
    return out


def describe(last: dict[str, Result], answered: dict[str, float], hosts: dict[str, str],
             now: float | None = None) -> tuple[str, str]:
    """(short text, whole text) of the state, the whole text naming each service, its host
    and when it last answered: for screen readers (the window shows the same in rows)."""
    lines = [_(STATE_TEXT[state_of(last, answered)])]
    for r in rows(last, answered, hosts, now):
        detail = r.detail + ("; " + r.last_answer if r.last_answer else "")
        # TRANSLATORS: a line read by screen readers for each part of Garmin's servers; {host}: its
        # address; {detail}: how it last answered
        lines.append(_("{service} ({host}): {detail}").format(
            service=r.name, host=r.host, detail=detail))
    return lines[0], "\n".join(lines)
