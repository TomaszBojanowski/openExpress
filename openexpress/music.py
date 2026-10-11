"""Music, podcasts and audiobooks on the watch (MTP).

Layout follows Garmin Express (docs/analiza/05, section 4):
  track:     <MEDIA_TRACK path>/<Artist | "Various Artists" | "Unknown">/<Album | "Unknown">/<file>
  podcast:   <MEDIA_PODCAST path>/<Album>/<file>
  audiobook: <MEDIA_AUDIOBOOK path>/<Album>/<file>
  playlist:  <MEDIA_PLAYLIST path>/<name>.m3u8 (UTF-8, one device path per line, no header)
"""

from __future__ import annotations

import os
import posixpath
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from openexpress.device.storage import Storage
from openexpress.device.xmldevice import GarminDevice
from openexpress.i18n import N_, _

KINDS = {"track": "MEDIA_TRACK", "podcast": "MEDIA_PODCAST", "audiobook": "MEDIA_AUDIOBOOK"}
KIND_NAMES = {"track": N_("Music"), "podcast": N_("Podcasts"), "audiobook": N_("Audiobooks")}
PLAYLIST = "MEDIA_PLAYLIST"
PODCAST_WORDS = ("podcast", "подкаст")
AUDIOBOOK_WORDS = ("audiobook", "audio book", "hörbuch", "hoerbuch", "spoken word", "speech",
                   "audiobooki", "książka", "livre audio", "audiolibro")
_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
SPACE_MARGIN = 20 * 1024 * 1024


class MusicError(Exception):
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
            "track": _("This watch does not accept music"),
            "podcast": _("This watch does not accept podcasts"),
            "audiobook": _("This watch does not accept audiobooks"),
        }.get(kind) or _("This watch does not accept {kind}").format(kind=kind))
    return files[0].path.strip("/")


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
    }


def scan(paths: list[Path], device: GarminDevice, forced_kind: str | None = None) -> list[Track]:
    """Finds audio files under `paths` that the watch can play."""
    tracks = []
    files: list[Path] = []
    for p in paths:
        p = Path(p).expanduser()
        if p.is_file():
            files.append(p)
        elif p.is_dir():
            files += sorted(x for x in p.rglob("*") if x.is_file())
        else:
            raise MusicError(_("Not found: {path}").format(path=p))
    for f in files:
        tags = read_tags(f)
        kind = classify(tags.get("genre", ""), forced_kind)
        if f.suffix.lower().lstrip(".") not in supported_extensions(device, kind):
            continue
        tracks.append(Track(
            local=f,
            title=tags.get("title") or f.stem,
            artist=tags.get("artist") or (f.parent.parent.name if forced_kind is None else ""),
            album=tags.get("album") or f.parent.name,
            genre=tags.get("genre", ""),
            compilation=bool(tags.get("compilation")),
            kind=kind,
            size=f.stat().st_size,
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
            if p.lower().endswith((".m3u8", ".m3u")) and "/" not in p[len(files[0].path.strip("/")) + 1:]]


@dataclass
class CopyPlan:
    to_copy: list[tuple[Track, str]] = field(default_factory=list)
    already: list[tuple[Track, str]] = field(default_factory=list)

    @property
    def size(self) -> int:
        return sum(t.size for t, _ in self.to_copy)


def plan_copy(tracks: list[Track], device: GarminDevice, storage: Storage) -> CopyPlan:
    plan = CopyPlan()
    for t in tracks:
        dest = t.device_path(device)
        (plan.already if storage.size(dest) == t.size else plan.to_copy).append((t, dest))
    return plan


def copy(plan: CopyPlan, storage: Storage, progress: Callable[[str], None] = lambda s: None,
         transfer_progress=None) -> list[str]:
    free = storage.free_space()
    if free is not None and plan.size + SPACE_MARGIN > free:
        raise MusicError(_("Not enough space on the watch: need {need} B, free {free} B")
                         .format(need=plan.size, free=free))
    written = []
    for t, dest in plan.to_copy:
        progress(_("Copying {path}").format(path=dest))
        real = storage.write_file(dest, t.local, transfer_progress)
        written.append(real)
    return written


def write_playlist(device: GarminDevice, storage: Storage, name: str, device_paths: list[str]) -> str:
    files = device.targets(PLAYLIST)
    if not files:
        raise MusicError(_("This watch does not support playlists"))
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


def remove(storage: Storage, device_paths: list[str]) -> list[str]:
    removed = []
    for p in device_paths:
        if storage.exists(p):
            storage.delete(p)
            removed.append(p)
    return removed
