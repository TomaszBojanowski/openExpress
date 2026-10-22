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
import locale
import os
from datetime import datetime, timedelta
from pathlib import Path

DOMAIN = "openexpress"
LOCALE_DIR = Path(__file__).parent / "locale"
SOURCE_LANGUAGE = "en"

_translation: gettext.NullTranslations = gettext.NullTranslations()
_language = SOURCE_LANGUAGE
_chosen: str | None = None  # the language chosen for the program; None: the system's
# The languages of the system as they were before ensure_utf8_locale set LC_ALL (None: it
# did not), which GLib reads before LANG.
_names_before: list[str] | None = None
FALLBACK_LOCALE = "C.UTF-8"
_CATEGORIES = ("LC_CTYPE", "LC_NUMERIC", "LC_TIME", "LC_COLLATE", "LC_MONETARY", "LC_MESSAGES")


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
    global _translation, _language, _chosen
    lang = language or _configured_language()
    _chosen = lang or None
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


def relative_time(moment: datetime, now: datetime) -> str:
    """When something happened, told against `now` (both local): "just now" under a minute
    (also a moment a little ahead), "5 min ago", "3 h ago" the same day, "yesterday, 23:41"
    the day before and "3 days ago" two to six days back (by the date, however many hours
    ago); else the date and the hour in the format of the language."""
    seconds = (now - moment).total_seconds()
    if seconds < 60:
        return _("just now")
    if seconds < 3600:
        n = int(seconds // 60)
        # TRANSLATORS: how long ago, in minutes
        return ngettext("{n} min ago", "{n} min ago", n).format(n=n)
    hour = moment.strftime("%H:%M")
    if moment.date() == now.date():
        n = int(seconds // 3600)
        # TRANSLATORS: how long ago, in hours
        return ngettext("{n} h ago", "{n} h ago", n).format(n=n)
    if moment.date() == now.date() - timedelta(days=1):
        # TRANSLATORS: a moment of yesterday, e.g. "yesterday, 23:41"
        return _("yesterday, {time}").format(time=hour)
    days = (now.date() - moment.date()).days
    if 2 <= days <= 6:
        # TRANSLATORS: how long ago, in days (two to six)
        return ngettext("{n} day ago", "{n} days ago", days).format(n=days)
    return full_time(moment)


def full_time(moment: datetime) -> str:
    """A moment in full (local): the date and the hour in the format of the language."""
    # TRANSLATORS: strftime format of a date in the popover of the server state button
    date = moment.strftime(_("%Y-%m-%d"))
    # TRANSLATORS: a date and an hour, e.g. 2026-10-14 23:41
    return pgettext("date and hour", "{date} {time}").format(date=date,
                                                             time=moment.strftime("%H:%M"))


def chosen_language() -> str | None:
    """The language chosen for the program (--lang, OPENEXPRESS_LANG, Preferences); None when
    it follows the system."""
    return _chosen


# The country Garmin gets for a language the system names without one.
_USUAL_COUNTRY = {"en": "en_US", "de": "de_DE", "pl": "pl_PL", "fr": "fr_FR"}


def _glib():
    try:
        from gi.repository import GLib
    except ImportError:
        return None
    return GLib


def system_language_names() -> list[str]:
    """The languages of the system, most wanted first, as GLib lists them (e.g. "de_DE.UTF-8",
    "de_DE", "de", "C"); without GLib the locale of the C library. The one place they are read.
    After ensure_utf8_locale changed LC_ALL: as they were before."""
    if _names_before is not None:
        return list(_names_before)
    glib = _glib()
    if glib is not None:
        return list(glib.get_language_names())
    name = locale.getlocale()[0]
    return [name] if name else []


def codeset() -> str:
    """The character set of the C library's locale now ("UTF-8"; "" when it cannot tell)."""
    try:
        return locale.nl_langinfo(locale.CODESET)
    except (AttributeError, ValueError):
        return ""


def locale_said() -> str:
    """The locale of the C library now and its character set, for the log ("C.UTF-8
    (UTF-8)"): what it was set to, never a variable of the environment."""
    return f"{locale.setlocale(locale.LC_CTYPE)} ({codeset() or '?'})"


def _not_available() -> str:
    """The locale(s) of the environment that the C library cannot set (while it is being
    tried): by category, LC_ALL first, then LC_<category>, then LANG."""
    names = []
    for category in _CATEGORIES:
        name = (os.environ.get("LC_ALL") or os.environ.get(category) or os.environ.get("LANG")
                or "C")
        try:
            locale.setlocale(getattr(locale, category), "")
        except locale.Error:
            if name not in names:
                names.append(name)
    return ", ".join(names) or os.environ.get("LC_ALL") or os.environ.get("LANG") or "?"


def ensure_utf8_locale() -> str | None:
    """Before GTK takes the locale from the environment (setlocale(LC_ALL, "")) and before
    the program starts any process: whether the C library can have that locale, in UTF-8.
    If not (a Flatpak whose runtime has no data for the language of the system), LC_ALL is
    set to C.UTF-8 for the program and the processes it starts: in the "C" locale GLib
    converts no name with a character outside ASCII, so flatpak-spawn failed and the
    sign-in window took the program down (#10). LANGUAGE is left as it is (the translations
    follow it); the languages of the system for Garmin stay as they were (read now, before
    the change); Python's own locale is set back as it was. What to log, or None when
    nothing is changed."""
    global _names_before
    saved = locale.setlocale(locale.LC_ALL)
    try:
        try:
            locale.setlocale(locale.LC_ALL, "")
        except locale.Error:
            problem = f"locale {_not_available()} not available"
        else:
            charset = codeset()
            if charset.replace("-", "").upper() == "UTF8":
                return None
            problem = (f"locale {locale.setlocale(locale.LC_CTYPE)} is not UTF-8 "
                       f"({charset or '?'})")
        try:
            locale.setlocale(locale.LC_ALL, FALLBACK_LOCALE)
        except locale.Error:
            return f"{problem}, nor is {FALLBACK_LOCALE}: the locale is left as it is"
    finally:
        locale.setlocale(locale.LC_ALL, saved)
    _names_before = system_language_names()
    os.environ["LC_ALL"] = FALLBACK_LOCALE
    return f"{problem}, using {FALLBACK_LOCALE}"


def garmin_locale() -> str:
    """The language for Garmin in the form its servers take (e.g. "de_DE"), as Garmin Express
    sends the culture of Windows: the one chosen for the program, else the system's. "en_US"
    when neither names one."""
    chosen = chosen_language()
    names = [chosen] if chosen else system_language_names()
    code = (names[0] if names else "").split(".")[0].split("@")[0].replace("-", "_")
    if code in ("", "C", "POSIX"):
        return "en_US"
    return code if "_" in code else _USUAL_COUNTRY.get(code, code)


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


def human_size(n: int | None, localize: bool = True) -> str:
    """A size as the user reads it, like GNOME Files writes it: powers of 1000, the units
    B, kB, MB and GB, one decimal ("699.4 kB"), with the decimal separator of the program's
    language; "?" when the size is not known. A hard space between the number and the unit,
    so that a text of the window that wraps never breaks a size ("4,7 / GB"); the command
    line and the log write it as a plain one.

    Without `localize` the decimal point and a plain space stay, for text that is always in
    English (the device report)."""
    if n is None:
        return "?"
    value = float(n)
    for unit in ("B", "kB", "MB", "GB"):
        # Compare what would be printed: 999.96 kB is "1.0 MB", not "1000.0 kB".
        if unit == "GB" or round(value, 0 if unit == "B" else 1) < 1000:
            number = f"{value:.0f}" if unit == "B" else f"{value:.1f}"
            return f"{decimal(number)}\u00a0{unit}" if localize else f"{number} {unit}"
        value /= 1000
    return str(n)


def N_(message: str) -> str:
    return message


setup()
