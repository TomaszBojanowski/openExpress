"""Garmin Connect token storage (file with 0600 permissions)."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

from openexpress import paths
from openexpress.i18n import _


class NotLoggedIn(Exception):
    pass


@dataclass
class ConnectAuth:
    token: str
    token_secret: str
    environment: str = "production"
    account: dict = field(default_factory=dict)

    @property
    def display(self) -> str:
        a = self.account
        return a.get("username") or a.get("emailaddress") or _("(unknown account)")


def _path():
    return paths.config_dir() / "connect_auth.json"


def load() -> ConnectAuth:
    p = _path()
    if not p.exists():
        raise NotLoggedIn(_("Not logged in. Run: openexpress login"))
    return ConnectAuth(**json.loads(p.read_text()))


def save(auth: ConnectAuth) -> None:
    paths.write_private(_path(), json.dumps(asdict(auth), indent=2))


def delete() -> bool:
    p = _path()
    if p.exists():
        p.unlink()
        return True
    return False
