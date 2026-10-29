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

import functools
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
    return saved_ui_language() or None


def _settings_path() -> Path:
    from openexpress import paths  # it imports this module

    return paths.config_dir() / "settings.json"


def saved_ui_language() -> str:
    """The language of the interface chosen in the program ("ui_language" in
    settings.json): its code, or "" for the system's (also when none is kept, or the file
    cannot be read). The one reader of it, for the start, the window and the command line."""
    try:
        data = json.loads(_settings_path().read_text())
    except (OSError, ValueError):
        return ""
    value = data.get("ui_language") if isinstance(data, dict) else None
    return value if isinstance(value, str) else ""


def save_ui_language(code: str) -> None:
    """Keep the language of the interface ("" for the system's) in settings.json, read again
    first so that its other keys stay; a file that cannot be read is not written over
    (ValueError, OSError). The one writer of it."""
    from openexpress import paths

    path = _settings_path()
    with paths.settings_locked():  # not over a client GUID written in the meantime
        data = json.loads(path.read_text()) if path.exists() else {}
        if not isinstance(data, dict):
            raise ValueError("settings.json is not an object")
        data["ui_language"] = code
        paths.write_private(path, json.dumps(data, indent=2))


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
    """When `moment` happened, told against `now` (both local): "just now" under a minute
    (also a moment a little ahead), "5 min ago", "3 h ago" the same day, "yesterday, 23:41",
    "3 days ago" two to six days back by the date; else the date and the hour."""
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


def format_when(ts: float) -> str:
    """A moment for the full date in a tooltip: "Today at 23:41", "Yesterday at 23:41", or
    the date of the locale and the hour."""
    when = datetime.fromtimestamp(ts)
    now = datetime.now()
    clock = when.strftime("%H:%M")
    if when.date() == now.date():
        return _("Today at {time}").format(time=clock)
    if when.date() == (now - timedelta(days=1)).date():
        return _("Yesterday at {time}").format(time=clock)
    return when.strftime("%x %H:%M")


# Between the parts of a caption of the window: a hard space before "·", a plain one after
# it, so that a caption that wraps in a narrow window never starts a line with "·".
SEPARATOR = "\u00a0· "


def caption(*parts: str) -> str:
    """The parts of a caption of the window, the empty ones left out, SEPARATOR between
    them; a separator inside a part (a translation, the words of the command line) is made
    the same."""
    return SEPARATOR.join(part.replace(" · ", SEPARATOR) for part in parts if part)


@functools.cache
def in_every_language(message: str) -> frozenset[str]:
    """The message as it is (English) and as each catalogue of the program has it: to know
    words written in the language of an earlier moment (e.g. in a record of a sync)."""
    said = {message}
    for language in available_languages():
        if language != SOURCE_LANGUAGE:
            said.add(gettext.translation(DOMAIN, LOCALE_DIR, languages=[language],
                                         fallback=True).gettext(message))
    return frozenset(said)


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
    """Whether the C library can have the locale of the environment in UTF-8; if not, LC_ALL
    becomes C.UTF-8 for the program and the processes it starts. Returns what to log, or None
    when nothing is changed. Call it before GTK takes the locale from the environment and
    before any process is started; Python's own locale is set back."""
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
    _names_before = system_language_names()  # read before the change: Garmin still gets them
    # E.g. a Flatpak whose runtime has no data for the language of the system: in the "C"
    # locale GLib converts no name with a character outside ASCII, so flatpak-spawn fails and
    # the sign-in window takes the program down. LANGUAGE stays: the translations follow it.
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
    writes it ("6,2 GB" in Polish). Only for what the user reads: the log, the device report,
    JSON output and anything a program may read keep the point."""
    # TRANSLATORS: the decimal separator of your language, one character: "." or ","
    separator = pgettext("decimal separator", ".")
    # From the translation, not from the system locale: it always matches the language of the
    # messages.
    if len(separator) != 1 or separator == "." or separator.isalnum() or separator.isspace():
        return number
    return number.replace(".", separator)


# The ISO 3166 list of countries of the system's iso-codes (also in the Flatpak's GNOME
# runtime), with its translations in the "iso_3166-1" domain.
ISO_3166_1 = Path("/usr/share/iso-codes/json/iso_3166-1.json")


@functools.cache
def _countries() -> dict[str, str]:
    try:
        data = json.loads(ISO_3166_1.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    entries = data.get("3166-1") if isinstance(data, dict) else None
    return {e["alpha_2"].upper(): e.get("common_name") or e["name"]
            for e in entries or [] if isinstance(e, dict)
            and isinstance(e.get("alpha_2"), str) and isinstance(e.get("name"), str)}


@functools.cache
def _country_translation(language: str):
    try:
        return gettext.translation("iso_3166-1", languages=[language])
    except OSError:
        return None


def country_name(code: str) -> str:
    """The name of a country (its ISO 3166 code) in the program's language; the code itself
    when the system has no list of countries or no such country."""
    name = _countries().get(code.upper())
    if name is None:
        return code
    language = current_language()
    if language == SOURCE_LANGUAGE:
        return name
    translation = _country_translation(language)
    return translation.gettext(name) if translation is not None else name


def duration_text(seconds: float) -> str:
    """A length of time as the user reads it: "45 s", "3 min", "3 min 20 s" (whole seconds;
    a hard space between a number and its unit, as in human_size)."""
    total = max(0, round(seconds))
    minutes, rest = divmod(total, 60)
    if not minutes:
        # TRANSLATORS: a length of time in seconds
        return _("{seconds}\u00a0s").format(seconds=rest)
    if not rest:
        # TRANSLATORS: a length of time in minutes
        return _("{minutes}\u00a0min").format(minutes=minutes)
    # TRANSLATORS: a length of time in minutes and seconds
    return _("{minutes}\u00a0min {seconds}\u00a0s").format(minutes=minutes, seconds=rest)


def human_size(n: int | None, localize: bool = True) -> str:
    """A size as the user reads it, like GNOME Files writes it: powers of 1000, B to GB, one
    decimal ("699.4 kB") with the decimal separator of the program's language and a hard space
    before the unit; "?" when not known. Without `localize`: the decimal point and a plain
    space, for text always in English (the device report)."""
    if n is None:
        return "?"
    value = float(n)
    for unit in ("B", "kB", "MB", "GB"):
        # Compare what would be printed: 999.96 kB is "1.0 MB", not "1000.0 kB".
        if unit == "GB" or round(value, 0 if unit == "B" else 1) < 1000:
            number = f"{value:.0f}" if unit == "B" else f"{value:.1f}"
            # A hard space: a text of the window that wraps never breaks a size ("4,7 / GB");
            # the command line and the log write it as a plain one.
            return f"{decimal(number)}\u00a0{unit}" if localize else f"{number} {unit}"
        value /= 1000
    return str(n)


def N_(message: str) -> str:
    return message


setup()
