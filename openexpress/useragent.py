"""How openExpress names itself to the servers it asks: one User-Agent header for every
request, the Garmin Connect API as well as the upload of an activity, the check of its status,
product pictures and the files of updates and maps. The Garmin Express client headers are
sent too, so that the servers answer, but the program does not hide that it is another one.
Only the sign-in window is a web browser, with its own header."""

from __future__ import annotations

import platform

import requests

from openexpress import __version__

USER_AGENT = f"openexpress/{__version__} ({platform.system()})"


def session() -> requests.Session:
    """A session whose requests carry USER_AGENT instead of the library's own header. The
    requests of the program name it themselves too, so a session made elsewhere does not
    bring the library's header back."""
    http = requests.Session()
    http.headers["User-Agent"] = USER_AGENT
    return http
