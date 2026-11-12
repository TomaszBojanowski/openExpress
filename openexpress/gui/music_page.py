"""The Music page of the window: music, podcasts and audiobooks on the device, copied from
files, folders or a playlist, and removed (music.py decides what is copied and where).
MusicPage is a mixin of MainWindow (gui/app.py), without __init__ of its own; what it uses
of the window (session, run_task, toast, sections, …) is the window's."""
from __future__ import annotations

from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402 - imports must follow gi.require_version

from openexpress.gui.tasks import read_again_after_failure, writes_to_device  # noqa: E402
from openexpress.gui.window_tasks import Answer, confirm  # noqa: E402
from openexpress.gui.words import space_text  # noqa: E402
from openexpress.i18n import _, caption, human_size, ngettext  # noqa: E402


def note_music_count(win, count: int | None, size: int | None) -> None:
    """How many media files the Music page found on the open device, and their size (None:
    not read yet), kept for the session and said by the row "Music" of the device page."""
    win.music_count = (count, size) if count is not None else None
    row = win.music_row
    if row is not None:
        row.set_subtitle(GLib.markup_escape_text(caption(ngettext(
            # TRANSLATORS: subtitle of the row "Music" and of the groups of the music page: the
            # files and their total size
            "{count} file · {size}", "{count} files · {size}", count).format(
            count=count, size=human_size(size or 0)))) if count is not None else "")


class MusicPage:
    """The Music page and its tasks."""

    def load_music(self) -> None:
        from openexpress import music

        s = self.session
        if not music.supported_kinds(s.device):
            # TRANSLATORS: the Music page of a device without music
            self.sections["music"].show_info(_("This device does not play music, podcasts or "
                                               "audiobooks"))
            return
        # The free space too: asked of the device here, never as the page is drawn.
        self._load("music", _("Reading Music on the Device…"),
                   lambda: (music.list_device(s.device, s.storage),
                            music.list_playlists(s.device, s.storage), s.storage.free_space()),
                   lambda r: self.show_music(r[:2], free=r[2]))

    def show_music(self, result, free: int | None = None) -> None:
        """`free`: the free space of the device, read with its music (None: not known);
        nothing is asked of the device here."""
        from openexpress import music

        if self.session is None:
            return
        items, playlists = result
        sec = self.sections["music"]
        # The row "Music" of the device page says it from now on.
        note_music_count(self, len(items), sum(i.size for i in items))
        kinds = music.supported_kinds(self.session.device)
        g = sec.group(_("Add to the Device"), space_text(free))
        for kind in kinds:
            row = Adw.ActionRow(title=_(music.KIND_NAMES[kind]))
            # TRANSLATORS: button beside Music, Podcasts or Audiobooks: chooses folders to copy to
            # the device
            row.add_suffix(self._button(_("Folders…"), lambda k=kind: self.choose_music(k, True)))
            # TRANSLATORS: button beside Music, Podcasts or Audiobooks: chooses files to copy to the
            # device
            row.add_suffix(self._button(_("Files…"), lambda k=kind: self.choose_music(k, False)))
            g.add(row)
        on_watch = {"track": _("Music on the Device"), "podcast": _("Podcasts on the Device"),
                    "audiobook": _("Audiobooks on the Device")}
        for kind in kinds:
            files = [i for i in items if i.kind == kind]
            n = len(files)
            g = sec.group(on_watch[kind], caption(
                ngettext("{count} file · {size}", "{count} files · {size}", n).format(
                    count=n, size=human_size(sum(f.size for f in files)))))
            if not files:
                continue
            exp = Adw.ExpanderRow(title=ngettext("Show {count} File", "Show {count} Files",
                                                 n).format(count=n))
            g.add(exp)
            for f in files:
                row = Adw.ActionRow(title=GLib.markup_escape_text(f.path.split("/")[-1]),
                                    subtitle=GLib.markup_escape_text(f.path))
                row.add_suffix(self._trash(_("Remove {name}").format(name=f.path.split("/")[-1]),
                                           lambda f=f: self.confirm_remove_music(f.path, f.size)))
                exp.add_row(row)
        if music.supports_playlists(self.session.device):
            g = sec.group(_("Playlists"))
            g.set_header_suffix(self._button(_("Add Playlist…"), self.choose_playlist))
            for p in playlists:
                row = Adw.ActionRow(title=GLib.markup_escape_text(p.split("/")[-1]))
                row.add_suffix(self._trash(_("Remove {name}").format(name=p.split("/")[-1]),
                                           lambda p=p: self.confirm_remove_music(p)))
                g.add(row)

    def choose_music(self, kind: str, folders: bool) -> None:
        dialog = Gtk.FileDialog(title=_("Choose Music to Copy to the Device"))

        def chosen(dlg, res):
            try:
                files = (dlg.select_multiple_folders_finish(res) if folders
                         else dlg.open_multiple_finish(res))
            except GLib.Error:
                return
            local = [files.get_item(i).get_path() for i in range(files.get_n_items())]
            if not local or None in local:
                self.toast(_("Choose files stored on this computer"))
                return
            self.add_music([Path(p) for p in local], kind)

        if folders:
            dialog.select_multiple_folders(self, None, chosen)
        else:
            dialog.open_multiple(self, None, chosen)

    def add_music(self, files, kind: str) -> None:
        from openexpress import music

        s = self.session

        def plan():
            tracks = music.scan(files, s.device, forced_kind=None if kind == "track" else kind)
            return music.plan_copy(tracks, s.device, s.storage)

        def copy(p):
            self.run_task(
                _("Copying Music…"),
                writes_to_device(lambda: music.copy(p, s.storage, progress=self.progress)),
                lambda written: (self.toast(ngettext("Copied {count} file", "Copied {count} files",
                                                     len(written)).format(count=len(written))),
                                 self.load_section("music", force=True)),
                read_again_after_failure(self, "music"), failed_title=_("Music Not Copied"))

        def ask_copy(p):
            if not p.to_copy:
                self.toast(_("Nothing new to copy"))
                return
            n = len(p.to_copy)
            confirm(self, "music-copy",
                    ngettext("Copy {count} File?", "Copy {count} Files?", n).format(count=n),
                    self._copy_summary(p),
                    # TRANSLATORS: button
                    Answer("ok", _("Copy"), lambda: copy(p), look="suggested"), private=True)

        self.run_task(_("Scanning Files…"), plan, ask_copy,
                      # TRANSLATORS: heading of the error when music, podcasts or audiobooks
                      # could not be copied to the device
                      failed_title=_("Music Not Copied"))

    def choose_playlist(self) -> None:
        dialog = Gtk.FileDialog(title=_("Choose a Playlist to Add to the Device"))
        only = Gtk.FileFilter()
        only.set_name(_("Playlists (.m3u, .m3u8)"))
        for pattern in ("*.m3u", "*.m3u8", "*.M3U", "*.M3U8"):
            only.add_pattern(pattern)
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(only)
        dialog.set_filters(filters)

        def chosen(dlg, res):
            try:
                path = dlg.open_finish(res).get_path()
            except GLib.Error:
                return
            if not path:
                self.toast(_("Choose files stored on this computer"))
                return
            self.add_playlist(Path(path))

        dialog.open(self, None, chosen)

    @staticmethod
    def _copy_summary(plan) -> str:
        """Text of the confirmation before music is copied."""
        from openexpress import music

        text = ngettext("{size} will be copied to the device ({count} file already there).",
                        "{size} will be copied to the device ({count} files already there).",
                        len(plan.already)).format(size=human_size(plan.size),
                                                  count=len(plan.already))
        replaced = music.replaced_text(plan)
        return f"{text}\n\n{replaced}" if replaced else text

    @staticmethod
    def _playlist_summary(plan, in_flatpak: bool = False) -> str:
        """Text of the confirmation: what is copied, what is already there, what is left out."""
        tracks = len(plan.device_paths)
        # TRANSLATORS: first line of the confirmation before a playlist is added: how many songs it
        # has (music tracks, not GPS tracks)
        lines = [ngettext("{count} track", "{count} tracks", tracks).format(count=tracks)]
        n = len(plan.copy.to_copy)
        lines.append(ngettext("{count} file to copy ({size}), {already} already on the device",
                              "{count} files to copy ({size}), {already} already on the device",
                              n).format(count=n, size=human_size(plan.copy.size),
                                        already=len(plan.copy.already)))
        if plan.copy.replaced:
            from openexpress import music

            lines.append(music.replaced_text(plan.copy))
        if plan.replaces:
            lines.append(_("A playlist with this name is already on the device and "
                           "will be replaced."))
        if plan.skipped:
            lines.append("")
            lines.append(ngettext("{count} track cannot be added:",
                                  "{count} tracks cannot be added:",
                                  len(plan.skipped)).format(count=len(plan.skipped)))
            lines += [f"• {name} ({reason})" for name, reason in plan.skipped[:10]]
            if len(plan.skipped) > 10:
                more = len(plan.skipped) - 10
                lines.append(ngettext("… and {count} more", "… and {count} more",
                                      more).format(count=more))
            if in_flatpak:
                lines.append(_("openExpress installed as a Flatpak can read music only from "
                               "the Music and Downloads folders."))
        return "\n".join(lines)

    def add_playlist(self, path) -> None:
        from openexpress import music
        from openexpress.device.gio_storage import in_flatpak

        s = self.session

        def add(plan):
            self.run_task(
                _("Copying Music…"),
                writes_to_device(lambda: music.add_playlist(
                    plan, s.device, s.storage, progress=self.progress)),
                # TRANSLATORS: toast after a playlist was added to the device
                lambda _path: (self.toast(_("Added the playlist {name}").format(name=plan.name)),
                               self.load_section("music", force=True)),
                read_again_after_failure(self, "music"), failed_title=_("Playlist Not Added"))

        def ask_add(plan):
            summary = self._playlist_summary(plan, in_flatpak())
            if not plan.device_paths:
                # TRANSLATORS: heading of the message when no track of a playlist can be added
                self.error(_("No Tracks to Add"),
                           _("The playlist has no tracks that can be added.") + "\n\n" + summary,
                           private=True)
                return
            confirm(self, "playlist-add", _("Add the Playlist “{name}”?").format(name=plan.name),
                    summary,
                    # TRANSLATORS: button that adds the playlist to the device
                    Answer("ok", _("Add"), lambda: add(plan), look="suggested"), private=True)

        self.run_task(_("Reading the Playlist…"),
                      lambda: music.plan_playlist(path, s.device, s.storage), ask_add,
                      # TRANSLATORS: heading of the error when a playlist could not be added to
                      # the device
                      failed_title=_("Playlist Not Added"))

    def confirm_remove_music(self, path: str, size: int | None = None) -> None:
        """The trash of a file (`size`: its size) or of a playlist (no size): asked first, as
        every trash of the window."""
        if size is None:
            # TRANSLATORS: the confirmation before a playlist is removed: the music files it lists
            # stay on the device
            body = _("{path} will be removed from the device; its music stays.").format(path=path)
        else:
            body = _("{path} ({size}) will be removed from the device.").format(
                path=path, size=human_size(size))
        # TRANSLATORS: heading of the confirmation before a music file or a playlist is removed
        heading = _("Remove {name}?").format(name=path.split("/")[-1])
        confirm(self, "music-remove", heading, body,
                Answer("ok", _("Remove"), lambda: self.remove_music([path]), look="destructive"),
                private=True)

    def remove_music(self, device_paths) -> None:
        from openexpress import music

        s = self.session
        self.run_task(_("Removing…"),
                      writes_to_device(lambda: music.remove(s.storage, device_paths)),
                      lambda removed: (self.toast(self._removed_files(len(removed))),
                                       self.load_section("music", force=True)),
                      read_again_after_failure(self, "music"),
                      # TRANSLATORS: heading of the error when music, a podcast, an audiobook
                      # or a playlist could not be removed from the device
                      failed_title=_("Music Not Removed"))
