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
            return json.loads(path.read_text()).get("ui_language") or None
    except (OSError, ValueError):
        pass
    return None


def setup(language: str | None = None) -> str:
    """Activates a language (None: configured or system language). Returns the language used."""
    global _translation, _language
    lang = language or _configured_language()
    languages = [lang] if lang else None  # None: gettext reads the system locale variables
    if lang == SOURCE_LANGUAGE:
        _translation = gettext.NullTranslations()
        _language = SOURCE_LANGUAGE
        return _language
    _translation = gettext.translation(DOMAIN, LOCALE_DIR, languages=languages, fallback=True)
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


def N_(message: str) -> str:
    return message


setup()
