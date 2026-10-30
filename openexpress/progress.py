"""Progress messages. A plain message is a step; `Waiting` is a step in which the program
waits for Garmin's servers for a time nobody knows in advance; `TimedWait` a wait whose length
is known (TrueUp: Garmin Connect says when to ask again); `Step` a step of a long task with how
far it is ("3 of 8", "412 MB of 1.5 GB"). `Reporter` is the one place that decides how often
they are shown, for the window and the command line alike."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable


class Waiting(str):
    """A progress message for a wait on a server. It reads as the message itself (the command
    line prints it as it is); the window keeps its bar moving until the next message."""


class TimedWait(Waiting):
    """A wait of known length: `seconds` in all, `done` of it gone (0 to 1). It reads as the
    message itself; the window fills its bar with `done`, the command line one line."""

    seconds: float
    done: float

    def __new__(cls, text: str, seconds: float, done: float = 0.0) -> TimedWait:
        message = super().__new__(cls, text)
        message.seconds, message.done = float(seconds), min(1.0, max(0.0, float(done)))
        return message


class Step(str):
    """A step of a long task as a person reads it: what is done and to what (`head`: a name a
    person knows - a type of data, a map - never the name of a file), then how far: "<head> ·
    3 of 8", or in bytes "<head> · 412 MB of 1.5 GB", or both ("<head> · 3 of 25 · 120 MB of
    1.2 GB": `bytes_done` of `bytes_total` besides the count). `fraction`: for the bar, by the
    bytes when they are known (None: not known). Steps of one head follow one another;
    `Reporter` shows a few of them."""

    head: str
    done: int | None
    total: int | None
    in_bytes: bool
    bytes_done: int | None
    bytes_total: int | None

    def __new__(cls, head: str, done: int | None = None, total: int | None = None,
                in_bytes: bool = False, bytes_done: int | None = None,
                bytes_total: int | None = None) -> Step:
        from openexpress.i18n import _, human_size

        def of(a, b) -> str:
            # TRANSLATORS: how far a step of a long task is: "3 of 8", "412.0 MB of 1.5 GB"
            return _("{done} of {total}").format(done=a, total=b)

        parts = []
        if done is not None and total:
            parts.append(of(human_size(done), human_size(total)) if in_bytes
                         else of(done, total))
        if bytes_done is not None and bytes_total:
            parts.append(of(human_size(bytes_done), human_size(bytes_total)))
        step = super().__new__(cls, "".join([head, *(f"\u00a0· {p}" for p in parts)]))
        step.head, step.done, step.total, step.in_bytes = head, done, total, in_bytes
        step.bytes_done, step.bytes_total = bytes_done, bytes_total
        return step

    @property
    def measure(self) -> tuple[int | None, int | None]:
        """How far, as the bar reads it: the bytes when they are known, else the count."""
        if self.bytes_done is not None and self.bytes_total:
            return self.bytes_done, self.bytes_total
        return self.done, self.total

    @property
    def fraction(self) -> float | None:
        done, total = self.measure
        if done is None or not total:
            return None
        return min(1.0, max(0.0, done / total))

    @property
    def last(self) -> bool:
        return self.fraction == 1.0


class Reporter:
    """Tells the progress of a long task to `show` (the window's label, the command line's
    line): a `Step` at most once every `every` seconds - the first one, one of another head
    and the last one at once, one held meanwhile shown when its time comes (through `later`,
    when given: `later(seconds, fn)` runs `fn` once, later) or with the next message; its
    fraction to `bar` at most once every `bar_every` seconds. Any other message at once; it
    drops the step held. May be called from any thread."""

    def __init__(self, show: Callable[[str], None],
                 bar: Callable[[int, int], None] | None = None,
                 later: Callable[[float, Callable[[], None]], None] | None = None,
                 every: float = 1.0, bar_every: float = 0.1,
                 clock: Callable[[], float] = time.monotonic):
        self.show, self.bar, self.later = show, bar, later
        self.every, self.bar_every, self.clock = every, bar_every, clock
        self._lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        """A new task: nothing held, nothing shown yet."""
        with self._lock:
            self._held: Step | None = None
            self._head: str | None = None
            self._shown_at = self._bar_at = float("-inf")
            self._waiting_for_later = False

    def __call__(self, message: str) -> None:
        now = self.clock()
        send = None
        with self._lock:
            if not isinstance(message, Step):
                self._held, self._head = None, None
                send = message
            else:
                at_once = (message.head != self._head or message.last
                           or now - self._shown_at >= self.every)
                if at_once:
                    self._held, self._head, self._shown_at = None, message.head, now
                    send = message
                else:
                    self._held = message
                    self._ask_later(now)
                    if (self.bar is not None and message.fraction is not None
                            and now - self._bar_at >= self.bar_every):
                        self._bar_at = now
                        self.bar(*message.measure)
        if send is not None:
            self.show(send)

    def _ask_later(self, now: float) -> None:
        if self.later is not None and not self._waiting_for_later:
            self._waiting_for_later = True
            self.later(max(0.0, self._shown_at + self.every - now), self.flush)

    def flush(self) -> None:
        """Shows the step held, if any."""
        with self._lock:
            self._waiting_for_later = False
            held, self._held = self._held, None
            if held is not None:
                self._shown_at = self.clock()
        if held is not None:
            self.show(held)
