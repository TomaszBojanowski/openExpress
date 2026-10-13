"""Translations (GNU gettext).

Language selection, first match wins:
  1. the OPENEXPRESS_LANG environment variable (e.g. "pl", "en"),
  2. "ui_language" in ~/.config/openexpress/settings.json,
  3. the system locale (LANGUAGE, LC_ALL, LC_MESSAGES, LANG).

Usage in code:
  from openexpress.i18n import _, N_, ngettext
  _("Text")                         translated at call time
  N_("Text")                        marks a module-level constant; translate with _() when shown
  ngettext("{n} file", "{n} files", n).format(n=n)
  pgettext("tab", "Sync")           same English word, different meaning (tab vs. button)
"""

from __future__ import annotations

import gettext
import json
import os
from pathlib import Path

DOMAIN = "openexpress"
LOCALE_DIR = Path(__file__).parent / "locale"
SOURCE_LANGUAGE = "en"

_translation: gettext.NullTranslations = gettext.NullTranslations()
_language = SOURCE_LANGUAGE


def available_languages() -> list[str]:
    langs = [SOURCE_LANGUAGE]
    if LOCALE_DIR.is_dir():
        langs += sorted(p.name for p in LOCALE_DIR.iterdir()
                        if (p / "LC_MESSAGES" / f"{DOMAIN}.mo").exists())
    return langs


LANGUAGE_NAMES = {"en": "English", "pl": "Polski"}


def _configured_language() -> str | None:
    env = os.environ.get("OPENEXPRESS_LANG")
    if env:
        return env
    try:
        from openexpress import paths

        path = paths.config_dir() / "settings.json"
        if path.exists():
            data = json.loads(path.read_text())
            if isinstance(data, dict):
                value = data.get("ui_language")
                return value if isinstance(value, str) and value else None
    except (OSError, ValueError):
        pass
    return None


def _system_languages() -> list[str]:
    """The user's language preference list, read like gettext does."""
    for var in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(var)
        if value:
            return [v for v in value.split(":") if v] if var == "LANGUAGE" else [value]
    return []


def _system_translation() -> gettext.NullTranslations:
    """First entry of the preference list that is English or has a catalog wins."""
    for entry in _system_languages():
        code = entry.split(".")[0].split("@")[0]
        base = code.split("_")[0]
        if base in (SOURCE_LANGUAGE, "C", "POSIX"):
            return gettext.NullTranslations()
        for candidate in dict.fromkeys((code, base)):
            try:
                return gettext.translation(DOMAIN, LOCALE_DIR, languages=[candidate])
            except OSError:
                continue
    return gettext.NullTranslations()


def setup(language: str | None = None) -> str:
    """Activates a language (None: configured or system language). Returns the language used."""
    global _translation, _language
    lang = language or _configured_language()
    if lang == SOURCE_LANGUAGE:
        _translation = gettext.NullTranslations()
        _language = SOURCE_LANGUAGE
        return _language
    if lang:
        _translation = gettext.translation(DOMAIN, LOCALE_DIR, languages=[lang], fallback=True)
    else:
        _translation = _system_translation()
    if isinstance(_translation, gettext.GNUTranslations):
        _language = _translation.info().get("language") or lang or SOURCE_LANGUAGE
    else:
        _language = SOURCE_LANGUAGE
    return _language


def current_language() -> str:
    return _language


def _(message: str) -> str:
    return _translation.gettext(message)


def ngettext(singular: str, plural: str, n: int) -> str:
    return _translation.ngettext(singular, plural, n)


def pgettext(context: str, message: str) -> str:
    """Translation with a context, for identical English words with different meanings."""
    return _translation.pgettext(context, message)


def decimal(number: str) -> str:
    """A number written with a decimal point (e.g. "6.2 GB") as the language of the program
    writes it ("6,2 GB" in Polish). The separator comes from the translation, not from the
    system locale, so it always matches the language of the messages.

    Only for what the user reads: the log, the device report, JSON output and anything a
    program may read keep the point."""
    # TRANSLATORS: the decimal separator of your language, one character: "." or ","
    separator = pgettext("decimal separator", ".")
    if len(separator) != 1 or separator == "." or separator.isalnum() or separator.isspace():
        return number
    return number.replace(".", separator)


def human_size(n: int | None) -> str:
    """A size as the user reads it, like GNOME Files writes it: powers of 1000, the units
    B, kB, MB and GB, one decimal ("699.4 kB"), with the decimal separator of the program's
    language; "?" when the size is not known."""
    if n is None:
        return "?"
    value = float(n)
    for unit in ("B", "kB", "MB", "GB"):
        # Compare what would be printed: 999.96 kB is "1.0 MB", not "1000.0 kB".
        if unit == "GB" or round(value, 0 if unit == "B" else 1) < 1000:
            return f"{value:.0f} B" if unit == "B" else decimal(f"{value:.1f} {unit}")
        value /= 1000
    return str(n)


def N_(message: str) -> str:
    return message


setup()
