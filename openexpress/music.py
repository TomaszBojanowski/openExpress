"""Music, podcasts and audiobooks on the watch (MTP).

Layout follows Garmin Express:
  track:     <MEDIA_TRACK path>/<Artist | "Various Artists" | "Unknown">/<Album | "Unknown">/<file>
  podcast:   <MEDIA_PODCAST path>/<Album>/<file>
  audiobook: <MEDIA_AUDIOBOOK path>/<Album>/<file>
  playlist:  <MEDIA_PLAYLIST path>/<name>.m3u8 (UTF-8, one device path per line, no header)
"""

from __future__ import annotations

import logging
import os
import posixpath
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from openexpress import applog
from openexpress.device.storage import Storage
from openexpress.device.xmldevice import GarminDevice
from openexpress.errors import UserFacingError
from openexpress.i18n import N_, _, human_size, ngettext
from openexpress.progress import Step

log = logging.getLogger(__name__)

KINDS = {"track": "MEDIA_TRACK", "podcast": "MEDIA_PODCAST", "audiobook": "MEDIA_AUDIOBOOK"}
KIND_NAMES = {"track": N_("Music"), "podcast": N_("Podcasts"), "audiobook": N_("Audiobooks")}
PLAYLIST = "MEDIA_PLAYLIST"
PODCAST_WORDS = ("podcast", "подкаст")
AUDIOBOOK_WORDS = ("audiobook", "audio book", "hörbuch", "hoerbuch", "spoken word", "speech",
                   "audiobooki", "książka", "livre audio", "audiolibro")
_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
SPACE_MARGIN = 20 * 1024 * 1024


class MusicError(UserFacingError):
    pass


@dataclass
class Track:
    local: Path
    title: str
    artist: str
    album: str
    genre: str
    compilation: bool
    kind: str
    size: int
    disc: int | None = None

    def device_path(self, device: GarminDevice) -> str:
        base = media_dir(device, self.kind)
        if self.kind == "track":
            artist = "Various Artists" if self.compilation else (self.artist or "Unknown")
            parts = [base, safe(artist), safe(self.album or "Unknown"), safe(self.local.name)]
        else:
            parts = [base, safe(self.album or self.artist or "Unknown"), safe(self.local.name)]
        return posixpath.join(*[p for p in parts if p])


def safe(name: str) -> str:
    return _BAD.sub("_", name).strip().rstrip(".") or "_"


def media_dir(device: GarminDevice, kind: str) -> str:
    files = device.targets(KINDS[kind])
    if not files:
        raise MusicError({
            "track": _("This device does not accept music"),
            "podcast": _("This device does not accept podcasts"),
            "audiobook": _("This device does not accept audiobooks"),
        }.get(kind) or _("This device does not accept {kind}").format(kind=kind))
    return files[0].path.strip("/")


def supported_kinds(device: GarminDevice) -> list[str]:
    """Kinds of media the device declares (e.g. a bike computer declares none)."""
    return [kind for kind, datatype in KINDS.items() if device.targets(datatype)]


def supports_playlists(device: GarminDevice) -> bool:
    return bool(device.targets(PLAYLIST))


def no_media_message() -> str:
    return _("This device does not play music, podcasts or audiobooks.")


def supported_extensions(device: GarminDevice, kind: str) -> set[str]:
    return {f.extension.lower() for f in device.targets(KINDS[kind]) if f.extension}


def classify(genre: str, forced: str | None) -> str:
    if forced:
        return forced
    g = genre.lower()
    if any(w in g for w in PODCAST_WORDS):
        return "podcast"
    if any(w in g for w in AUDIOBOOK_WORDS):
        return "audiobook"
    return "track"


def read_tags(path: Path) -> dict:
    try:
        import mutagen
    except ImportError:
        return {}
    try:
        f = mutagen.File(path, easy=True)
    except Exception:  # noqa: BLE001
        return {}
    if not f or not f.tags:
        return {}

    def tag(*names):
        for n in names:
            v = f.tags.get(n)
            if v:
                return str(v[0])
        return ""

    return {
        "title": tag("title"),
        "artist": tag("albumartist", "artist"),
        "album": tag("album"),
        "genre": tag("genre"),
        "compilation": tag("compilation") in ("1", "true", "True"),
        "disc": _disc_number(tag("discnumber")),
    }


def _disc_number(value: str) -> int | None:
    try:
        n = int(value.split("/")[0].strip())
    except ValueError:
        return None
    return n if n > 0 else None


def scan(paths: list[Path], device: GarminDevice, forced_kind: str | None = None) -> list[Track]:
    """Finds audio files under `paths` that the watch can play."""
    tracks = []
    # (file, folders between the scanned directory and the file); None for files given directly
    files: list[tuple[Path, tuple[str, ...] | None]] = []
    for p in paths:
        p = Path(p).expanduser()
        if p.is_file():
            files.append((p, None))
        elif p.is_dir():
            files += [(x, x.relative_to(p).parent.parts)
                      for x in sorted(x for x in p.rglob("*") if x.is_file())]
        else:
            raise MusicError(_("Not found: {path}").format(path=p))
    for f, folders in files:
        tags = read_tags(f)
        kind = classify(tags.get("genre", ""), forced_kind)
        if f.suffix.lower().lstrip(".") not in supported_extensions(device, kind):
            continue
        # Folder names are a fallback only inside a scanned library (…/Artist/Album/file).
        folders = folders or ()
        folder_album = folders[-1] if len(folders) >= 1 else ""
        folder_artist = folders[-2] if len(folders) >= 2 and forced_kind is None else ""
        tracks.append(Track(
            local=f,
            title=tags.get("title") or f.stem,
            artist=tags.get("artist") or folder_artist,
            album=tags.get("album") or folder_album,
            genre=tags.get("genre", ""),
            compilation=bool(tags.get("compilation")),
            kind=kind,
            size=f.stat().st_size,
            disc=tags.get("disc"),
        ))
    return tracks


@dataclass
class DeviceMedia:
    path: str
    size: int
    kind: str


def list_device(device: GarminDevice, storage: Storage) -> list[DeviceMedia]:
    out = []
    for kind, dt in KINDS.items():
        files = device.targets(dt)
        if not files:
            continue
        exts = supported_extensions(device, kind)
        for path, e in storage.walk_files(files[0].path):
            if path.rsplit(".", 1)[-1].lower() in exts:
                out.append(DeviceMedia(path, e.size, kind))
    return out


def list_playlists(device: GarminDevice, storage: Storage) -> list[str]:
    files = device.targets(PLAYLIST)
    if not files:
        return []
    return [p for p, _ in storage.walk_files(files[0].path)
            if p.lower().endswith((".m3u8", ".m3u"))
            and "/" not in p[len(files[0].path.strip("/")) + 1:]]


@dataclass
class CopyPlan:
    to_copy: list[tuple[Track, str]] = field(default_factory=list)
    already: list[tuple[Track, str]] = field(default_factory=list)
    # Of `to_copy`: the files that are on the device with another size, and that size.
    replaced: dict[str, int] = field(default_factory=dict)

    @property
    def size(self) -> int:
        return sum(t.size for t, _ in self.to_copy)


def replaced_note(plan: CopyPlan, track: Track, dest: str) -> str:
    """For a file of the plan that takes the place of one on the device; "" for a new one."""
    if dest not in plan.replaced:
        return ""
    return _("(replaces the file on the device: {old} → {new})").format(
        old=human_size(plan.replaced[dest]), new=human_size(track.size))


def replaced_text(plan: CopyPlan) -> str:
    """How many files on the device the plan replaces; "" when none."""
    n = len(plan.replaced)
    if not n:
        return ""
    return ngettext("{count} file on the device will be replaced.",
                    "{count} files on the device will be replaced.", n).format(count=n)


def _with_name(dest: str, name: str) -> str:
    parent = posixpath.dirname(dest)
    return posixpath.join(parent, name) if parent else name


def plan_copy(tracks: list[Track], device: GarminDevice, storage: Storage) -> CopyPlan:
    plan = CopyPlan()
    used: set[str] = set()
    for t in tracks:
        dest = t.device_path(device)
        if dest.casefold() in used:
            # e.g. "01 Intro.mp3" on both discs of an album
            name = posixpath.basename(dest)
            stem, ext = posixpath.splitext(name)
            if t.disc:
                dest = _with_name(dest, f"{t.disc}-{name}")
            n = 2
            while dest.casefold() in used:
                dest = _with_name(dest, f"{stem} ({n}){ext}")
                n += 1
        used.add(dest.casefold())
        on_device = storage.size(dest)
        if on_device == t.size:
            plan.already.append((t, dest))
            continue
        plan.to_copy.append((t, dest))
        if on_device is not None:
            plan.replaced[dest] = on_device  # copying writes over it
    return plan


def copied_before_text(count: int) -> str:
    """After a file of the wrong size: how many files were copied before it."""
    return ngettext("{count} file was copied before it.", "{count} files were copied before it.",
                    count).format(count=count)


def copy(plan: CopyPlan, storage: Storage,
         progress: Callable[[str], None] | None = None) -> list[str]:
    """`progress`: how many files and bytes, never a file nor a track - the names are the
    user's own ("Copying music · 3 of 25 · 120 MB of 1.2 GB")."""
    storage.require_space(plan.size, SPACE_MARGIN, error=MusicError)
    written = []
    say = progress if progress is not None else (lambda _message: None)
    # TRANSLATORS: a step of copying music ("… · 3 of 25 · 120 MB of 1.2 GB")
    head = _("Copying music")
    count, size = len(plan.to_copy), plan.size
    before = 0  # the bytes of the files copied before the current one
    for n, (t, dest) in enumerate(plan.to_copy, 1):
        def copied(done: int, total: int | None, _n=n, _before=before) -> None:
            say(Step(head, _n, count, bytes_done=_before + done, bytes_total=size))

        # Only when someone listens: a copy over MTP told nothing goes another way (gio copy
        # without -p in the Flatpak).
        told = copied if progress is not None else None
        say(Step(head, n, count, bytes_done=before, bytes_total=size))
        try:
            real = storage.write_file_checked(dest, t.local, told, error=MusicError)
        except MusicError as e:
            # Of the wrong size: deleted, and the rest is not copied, as with maps.
            log.info("music: stopped after %d files: one of the wrong size", len(written))
            raise MusicError(f"{e}. {copied_before_text(len(written))}") from e
        written.append(real)
        before += t.size
    if count:
        say(Step(head, count, count, bytes_done=size, bytes_total=size))
    # Counts and sizes only: file names are the user's own.
    log.info("music: %d files copied (%s)", len(written), applog.size(plan.size))
    return written


def write_playlist(device: GarminDevice, storage: Storage, name: str,
                   device_paths: list[str]) -> str:
    files = device.targets(PLAYLIST)
    if not files:
        raise MusicError(_("This device does not support playlists"))
    path = posixpath.join(files[0].path.strip("/"), safe(name) + ".m3u8")
    body = "".join(p + "\n" for p in device_paths).encode("utf-8")
    return storage.write(path, body)


def read_m3u(path: Path) -> list[Path]:
    """Reads a local .m3u/.m3u8 playlist; relative entries are relative to the playlist."""
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        p = Path(line) if os.path.isabs(line) else path.parent / line
        out.append(p)
    return out


@dataclass
class PlaylistPlan:
    """What adding a local playlist would do."""

    name: str
    target: str  # device path of the playlist file
    replaces: bool  # a playlist with this name is already on the device
    copy: CopyPlan
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (entry, reason)
    # Device paths in the order of the playlist (a track listed twice appears twice).
    device_paths: list[str] = field(default_factory=list)


def plan_playlist(m3u: Path, device: GarminDevice, storage: Storage,
                  name: str | None = None) -> PlaylistPlan:
    """Plan adding a local .m3u/.m3u8 playlist: which tracks to copy, which cannot be added.

    Entries that are missing or in a format the device does not play are skipped with a
    reason instead of stopping the whole playlist. Nothing is written.
    """
    files = device.targets(PLAYLIST)
    if not files:
        raise MusicError(_("This device does not support playlists"))
    m3u = Path(m3u)
    name = name or m3u.stem
    tracks: dict[Path, Track] = {}  # one copy per local file, however often it is listed
    order: list[Path] = []
    skipped: list[tuple[str, str]] = []
    for entry in read_m3u(m3u):
        if not entry.is_file():
            skipped.append((entry.name, _("file not found")))
            continue
        key = entry.resolve()
        if key not in tracks:
            found = scan([entry], device)
            if not found:
                skipped.append((entry.name, _("format not supported by the device")))
                continue
            tracks[key] = found[0]
        order.append(key)
    copy_plan = plan_copy(list(tracks.values()), device, storage)
    dest = {t.local.resolve(): d for t, d in copy_plan.to_copy + copy_plan.already}
    target = posixpath.join(files[0].path.strip("/"), safe(name) + ".m3u8")
    return PlaylistPlan(name, target, storage.resolve(target) is not None, copy_plan, skipped,
                        [dest[key] for key in order])


def add_playlist(plan: PlaylistPlan, device: GarminDevice, storage: Storage,
                 progress: Callable[[str], None] | None = None) -> str:
    """Copy the missing tracks, then write the playlist. Returns its device path."""
    if not plan.device_paths:
        raise MusicError(_("The playlist has no tracks that can be added."))
    copy(plan.copy, storage, progress)
    return write_playlist(device, storage, plan.name, plan.device_paths)


def remove(storage: Storage, device_paths: list[str]) -> list[str]:
    removed = []
    for p in device_paths:
        if storage.exists(p):
            storage.delete(p)
            removed.append(p)
    log.info("music: %d of %d files removed", len(removed), len(device_paths))
    return removed
