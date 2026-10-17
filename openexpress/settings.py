"""Configuration: Garmin environments, client identification, OAuth1 consumer key.

The OAuth1 consumer key belongs to Garmin and is not bundled with the program.
The user imports it from their own copy of Garmin Express (`openexpress setup`).
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from openexpress import i18n, paths
from openexpress.i18n import _

log = logging.getLogger(__name__)

ENVIRONMENTS: dict[str, dict[str, str]] = {
    "production": {
        "sso": "https://sso.garmin.com/",
        "connectapi": "https://connectapi.garmin.com/",
        "omt": "https://omt.garmin.com/",
        "gcs": "https://api.gcs.garmin.com/",
    },
    "china": {
        "sso": "https://sso.garmin.cn/",
        "connectapi": "https://connectapi.garmin.cn/",
        "omt": "https://omt.garmin.cn/",
        "gcs": "https://api.gcs.garmin.cn/",
    },
}

SSO_CLIENT_ID = "GarminExpress_WIN_Default"
UPLOAD_CONFIG_CLIENT_ID = "express-windows"

# Up to 0.6.1 settings.json got "locale": "pl_PL" on the first start, chosen or not: now ignored.
_OLD_LOCALE_KEY = "locale"


class SetupError(Exception):
    pass


@dataclass
class Consumer:
    key: str
    secret: str
    url_salt: str | None = None  # map download URL signing (garmindlm parameter)


@dataclass
class Settings:
    environment: str = "production"
    chosen_locale: str = ""  # from `setup --locale`; empty: the language of the system
    client_name: str = "express"
    client_version: str = "7.29.1.0"
    client_platform: str = "Windows"
    client_platform_version: str = "10.0.19045.0"
    client_guid: str = ""

    def __post_init__(self) -> None:
        self._extra: dict = {}

    @property
    def hosts(self) -> dict[str, str]:
        return ENVIRONMENTS[self.environment]

    @property
    def locale(self) -> str:
        """The language for Garmin's pages and answers, e.g. "de_DE"."""
        return self.chosen_locale or i18n.garmin_locale()

    @classmethod
    def load(cls) -> Settings:
        path = paths.config_dir() / "settings.json"
        data: object = {}
        if path.exists():
            try:
                data = json.loads(path.read_text())
            except (OSError, ValueError) as e:
                raise SetupError(_("The settings file {path} is damaged ({error}); "
                                   "fix or delete it.").format(path=path, error=e)) from e
            if not isinstance(data, dict):
                raise SetupError(_("The settings file {path} is damaged; fix or delete it.")
                                 .format(path=path))
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        s = cls(**known)
        # Keys owned by other parts of the program (e.g. "ui_language") survive save().
        s._extra = {k: v for k, v in data.items()
                    if k not in cls.__dataclass_fields__ and k != _OLD_LOCALE_KEY}
        if s.environment not in ENVIRONMENTS:
            raise SetupError(_("Unknown environment: {name}").format(name=s.environment))
        old_locale = _OLD_LOCALE_KEY in data
        if old_locale:
            log.info("settings: the locale saved by an older version (%r) is no longer used; "
                     "now %s (%s)", data[_OLD_LOCALE_KEY], s.locale,
                     "chosen" if s.chosen_locale
                     else "as the program" if i18n.chosen_language() else "as the system")
        if not s.client_guid or old_locale:
            s.client_guid = s.client_guid or str(uuid.uuid4())
            s.save()  # without the old key, so that it is told once
        return s

    def save(self) -> None:
        path = paths.config_dir() / "settings.json"
        data = dict(self._extra)
        try:
            # Keys written by others since load() (e.g. the GUI language choice) win.
            current = json.loads(path.read_text()) if path.exists() else {}
            data.update({k: v for k, v in current.items()
                         if k not in self.__dataclass_fields__ and k != _OLD_LOCALE_KEY})
        except (OSError, ValueError):
            pass
        data.update(asdict(self))
        if not self.chosen_locale:
            del data["chosen_locale"]  # no choice: the system decides at every start
        paths.write_private(
            path,
            json.dumps(data, indent=2),
        )


def consumer_path() -> Path:
    return paths.config_dir() / "consumer.json"


def load_consumer() -> Consumer:
    path = consumer_path()
    if not path.exists():
        raise SetupError(_(
            "openExpress is not set up yet. First run: "
            "openexpress setup --from-installer /path/to/GarminExpress.exe"
        ))
    try:
        data = json.loads(path.read_text())
        return Consumer(str(data["key"]), str(data["secret"]), data.get("url_salt"))
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise SetupError(_(
            "The saved client key in {path} is damaged. Run again: "
            "openexpress setup --from-installer /path/to/GarminExpress.exe"
        ).format(path=path)) from e


def save_consumer(consumer: Consumer) -> None:
    paths.write_private(
        consumer_path(),
        json.dumps({"key": consumer.key, "secret": consumer.secret,
                    "url_salt": consumer.url_salt}, indent=2),
    )


_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_SECRET_RE = re.compile(r"^[A-Za-z0-9]{20,64}$")


def _utf16_strings(blob: bytes, min_len: int = 8) -> list[str]:
    out: list[str] = []
    for m in re.finditer(rb"(?:[\x20-\x7e]\x00){%d,}" % min_len, blob):
        out.append(m.group().decode("utf-16-le"))
    return out


def extract_consumer_from_dll(dll: Path) -> Consumer:
    """Read the consumer key from Garmin Express's Networking.dll.

    In the library the key (UUID) and secret appear as consecutive string
    literals, right after the Connect host URLs.
    """
    strings = _utf16_strings(dll.read_bytes())
    for i, s in enumerate(strings[:-1]):
        if (_UUID_RE.match(s) and _SECRET_RE.match(strings[i + 1])
                and i > 0 and strings[i - 1].startswith("https://")):
            return Consumer(s, strings[i + 1])
    raise SetupError(_("Consumer key not found in {path}").format(path=dll))


def extract_url_salt_from_dll(dll: Path) -> str:
    """Read the download URL signing salt from Garmin Express's Utilities.dll.

    In the library it appears as a literal right before "{0}_{1}" and "garmindlm".
    """
    strings = _utf16_strings(dll.read_bytes(), min_len=5)
    for i, s in enumerate(strings[:-2]):
        if strings[i + 1] == "{0}_{1}" and strings[i + 2] == "garmindlm" and s.isalnum():
            return s
    raise SetupError(_("URL signing data not found in {path}").format(path=dll))
