"""Garmin Connect token storage (file with 0600 permissions)."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

from openexpress import answers, paths
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
        """The account's name as the window shows it: its user name, else its e-mail
        address, whole."""
        a = self.account
        # TRANSLATORS: shown in place of the signed-in Garmin account's name when it has no user
        # name and no e-mail address
        return a.get("username") or a.get("emailaddress") or _("(unknown account)")

    @property
    def masked_display(self) -> str:
        """The account's name as the command line shows it, masked (masked()): its output is
        pasted into reports."""
        a = self.account
        name = a.get("username") or a.get("emailaddress")
        return masked(name) if name else _("(unknown account)")


def masked(name: str) -> str:
    """An account's name for what is pasted into reports (the command line's output): a user
    name as its first and last letters, "t…b" (only the first of two letters or fewer); an
    e-mail address as the first letters of its two halves, "t…@e…"."""
    local, at, domain = name.partition("@")
    if at:
        return f"{local[:1]}…@{domain[:1]}…"
    return f"{name[:1]}…{name[-1:]}" if len(name) > 2 else f"{name[:1]}…"


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
        raise NotLoggedIn(_("Not signed in. Run: openexpress login"))
    try:
        data = json.loads(p.read_text())
        auth = ConnectAuth(**data)
    except (OSError, ValueError, TypeError) as e:
        raise NotLoggedIn(_("The saved sign-in is damaged; sign in again.")) from e
    if not (isinstance(auth.token, str) and isinstance(auth.token_secret, str)
            and isinstance(auth.account, dict)):
        raise NotLoggedIn(_("The saved sign-in is damaged; sign in again."))
    return auth


def save(auth: ConnectAuth) -> None:
    paths.write_private(_path(), json.dumps(asdict(auth), indent=2))
    _forget_the_account()


def delete() -> bool:
    p = _path()
    _forget_the_account()
    if p.exists():
        p.unlink()
        return True
    return False


def _forget_the_account() -> None:
    """Another login (or none): nothing kept of the account's answers holds (answers)."""
    answers.ANSWERS.forget(answers.ACCOUNT_KINDS)
