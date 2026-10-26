"""The last sync in words, one way for all who tell it: a record of a sync (last_sync.json, as
service.save_last_sync writes it, and as older versions wrote it) is put into words by
describe() alone, and what it gives is shown as it is: the row "Last sync" of the device page
(its subtitle, the full date of its tooltip, its details), the tiles of the start page, the
numbers at the end of `openexpress sync` and the notification of the background sync. No
caller puts a record into words itself.

tests/test_last_sync_patterns.py checks it from the file to the screen on records of real
syncs (tests/data/last_sync/): every change of the row comes with a new one (AGENTS.md)."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime

from openexpress import i18n, service
from openexpress.i18n import N_, _

# TRANSLATORS: kept to recognise the records of older versions, which said it after every
# sync whose device had no new file (the row says it already: "nothing to upload")
NOTHING_NEW = N_("No new data to upload")


def _says_nothing(line) -> bool:
    """A line of an older record that only says what the row says: "No new data to upload",
    in the language of that moment, with or without its full stop."""
    return (isinstance(line, str)
            and line.strip().rstrip(".") in i18n.in_every_language(NOTHING_NEW))


@dataclass(frozen=True)
class Told:
    """A sync in words. `counts`: the numbers ("16 new files, 12 uploaded · 2 received");
    `steps`: what the sync did, in order, each with whether it is a warning; `errors`: what
    went wrong, as the sync told it."""

    when: float
    counts: str
    steps: tuple[tuple[str, bool], ...]
    errors: tuple[str, ...]

    @property
    def details(self) -> list[tuple[str, bool]]:
        """The details under the row: the steps, then the errors (warnings)."""
        return [*self.steps, *((error, True) for error in self.errors)]

    def row(self, now: datetime | None = None) -> str:
        """The row under "Last sync" of the window: when, told against now, then the
        numbers (a hard space before each "·")."""
        when = datetime.fromtimestamp(self.when)
        return i18n.caption(i18n.relative_time(when, now or datetime.now()), self.counts)

    @property
    def tooltip(self) -> str:
        """The full date of the sync, for the tooltip of the row."""
        return i18n.format_when(self.when)

    @property
    def sentence(self) -> str:
        """The numbers as a sentence of their own, for the end of `openexpress sync
        --apply`: a capital letter first, a full stop at the end ("Nothing to upload.").
        The row under "Last sync" keeps them as a part of it."""
        counts = self.counts
        return counts[:1].upper() + counts[1:] + ("" if counts.endswith(".") else ".")

    @property
    def text(self) -> str:
        """The numbers, then the steps, a line each: the words of the command line."""
        return "\n".join([self.counts, *(text for text, _warning in self.steps)])

    @property
    def notification(self) -> str:
        """The body of the notification after a sync in the background: the text, then the
        first errors."""
        if not self.errors:
            return self.text
        return self.text + "\n" + _("Errors: {errors}").format(
            errors="; ".join(self.errors[:3]))


def describe(record: dict) -> Told:
    """A record of a sync in words, in the language of the moment (steps kept as numbers are
    put into words now; those of older versions stay in the words they were written in)."""
    lines = [line for line in record.get("lines") or [] if not _says_nothing(line)]
    return Told(when=record.get("time", time.time()), counts=service.describe_counts(record),
                steps=tuple(service.describe_steps(lines)),
                errors=tuple(record.get("errors") or []))
