"""Background service: sync when the watch is connected.

Runs as a systemd user service (`openexpress install-desktop`).
Syncs only when the user is logged in and the first sync has already
been done (so as not to make that decision for them).
"""

from __future__ import annotations

import logging
import threading

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from openexpress import service  # noqa: E402
from openexpress.device.gio_storage import _is_garmin_uri  # noqa: E402
from openexpress.i18n import _  # noqa: E402

log = logging.getLogger(__name__)
APP_ID = "io.github.tomaszbojanowski.OpenExpress"


class Watcher(Gio.Application):
    def __init__(self, delay_s: int = 5):
        # Same ID as the GUI: notifications get the app icon and name.
        # Unique: a second start (autostart + GUI switch) just exits.
        super().__init__(application_id=APP_ID + ".Watch",
                         flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.delay_s = delay_s
        self.running = False

    def do_startup(self):
        Gio.Application.do_startup(self)
        self.hold()
        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", lambda *_a: self.release())
        self.add_action(quit_action)
        self.monitor = Gio.VolumeMonitor.get()
        self.monitor.connect("mount-added", self.on_mount)
        log.info("Waiting for the watch to be connected…")
        # The watch may have been connected before the service started.
        for m in self.monitor.get_mounts():
            if _is_garmin_uri(m.get_root().get_uri()):
                GLib.timeout_add_seconds(self.delay_s, self.start_sync)

    def do_activate(self):
        pass

    def notify(self, title: str, body: str) -> None:
        n = Gio.Notification.new(title)
        n.set_body(body)
        self.send_notification("sync", n)

    def on_mount(self, _monitor, mount):
        if _is_garmin_uri(mount.get_root().get_uri()):
            GLib.timeout_add_seconds(self.delay_s, self.start_sync)

    def start_sync(self):
        if not self.running:
            self.running = True
            threading.Thread(target=self._sync, daemon=True).start()
        return False

    def _sync(self):
        try:
            s = service.open_session()
            if not s.logged_in:
                log.info("Not logged in: skipping sync")
                return
            if s.first_sync:
                GLib.idle_add(self.notify, _("Garmin: first sync required"),
                              _("Open openExpress and choose how to sync the watch."))
                return
            GLib.idle_add(self.notify,
                          _("Syncing {device}").format(device=s.device.description),
                          # TRANSLATORS: notification body while the watch is syncing
                          _("In progress…"))
            summary = service.run_sync(s, progress=log.info)
            body = summary.text
            if summary.errors:
                body += "\n" + _("Errors: {errors}").format(errors="; ".join(summary.errors[:3]))
            GLib.idle_add(self.notify, _("Sync finished"), body)
        except service.SyncBusy:
            log.info("A sync is running in another process")
        except Exception as e:  # noqa: BLE001
            log.exception("Sync failed")
            GLib.idle_add(self.notify, _("Sync failed"), str(e))
        finally:
            self.running = False


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s", force=True)
    return Watcher().run([])
