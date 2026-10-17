"""Progress messages. A plain message is a step; `Waiting` is a step in which the program
waits for Garmin's servers for a time nobody knows in advance."""

from __future__ import annotations


class Waiting(str):
    """A progress message for a wait on a server. It reads as the message itself (the command
    line prints it as it is); the window keeps its bar moving until the next message."""
