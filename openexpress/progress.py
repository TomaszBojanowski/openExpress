"""Progress messages. A plain message is a step; `Waiting` is a step in which the program
waits for Garmin's servers for a time nobody knows in advance; `TimedWait` a wait whose length
is known (TrueUp: Garmin Connect says when to ask again)."""

from __future__ import annotations


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
