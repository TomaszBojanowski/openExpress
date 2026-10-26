"""Garmin Connect token storage (file with 0600 permissions)."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

from openexpress import paths
from openexpress.errors import UserFacingError
from openexpress.i18n import _


class NotLoggedIn(UserFacingError):
    pass


@dataclass
class ConnectAuth:
    # Out of repr(): an object that ends up in a log line or an error shows no secret.
    token: str = field(repr=False)
    token_secret: str = field(repr=False)
    environment: str = "production"
    account: dict = field(default_factory=dict)

    @property
    def display(self) -> str:
        a = self.account
        return a.get("username") or a.get("emailaddress") or _("(unknown account)")


def _path():
    return paths.config_dir() / "connect_auth.json"


def stamp() -> str | None:
    """The version of the login saved (when its file was written, and its size), nothing of
    what is in it; None: none saved. What was read of the account with one login holds only
    while that login stays saved (tile_facts)."""
    try:
        st = _path().stat()
    except OSError:
        return None
    return f"{st.st_mtime_ns}-{st.st_size}"


def load() -> ConnectAuth:
    p = _path()
    if not p.exists():
        raise NotLoggedIn(_("Not logged in. Run: openexpress login"))
    try:
        data = json.loads(p.read_text())
        auth = ConnectAuth(**data)
    except (OSError, ValueError, TypeError) as e:
        raise NotLoggedIn(_("The saved login is damaged; please log in again.")) from e
    if not (isinstance(auth.token, str) and isinstance(auth.token_secret, str)
            and isinstance(auth.account, dict)):
        raise NotLoggedIn(_("The saved login is damaged; please log in again."))
    return auth


def save(auth: ConnectAuth) -> None:
    paths.write_private(_path(), json.dumps(asdict(auth), indent=2))


def delete() -> bool:
    p = _path()
    if p.exists():
        p.unlink()
        return True
    return False
