"""Background service: sync when the watch is connected.

Runs as a systemd user service (`openexpress install-desktop`).
Syncs only when the user is logged in and the first sync has already
been done (so as not to make that decision for them).
"""

from __future__ import annotations

import logging
import threading
import time

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from openexpress import service  # noqa: E402
from openexpress.connect import OwnershipError  # noqa: E402
from openexpress.device.gio_storage import garmin_usb, own_remount  # noqa: E402
from openexpress.device.storage import DeviceError  # noqa: E402
from openexpress.i18n import _  # noqa: E402

log = logging.getLogger(__name__)
OPEN_RETRIES = 3
OPEN_RETRY_DELAY_S = 5
APP_ID = "io.github.tomaszbojanowski.OpenExpress"


class Watcher(Gio.Application):
    def __init__(self, delay_s: int = 5):
        # Same ID as the GUI: notifications get the app icon and name.
        # Unique: a second start (autostart + GUI switch) just exits.
        super().__init__(application_id=APP_ID + ".Watch",
                         flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.delay_s = delay_s
        self.running = False
        self.pending = False
        self.quit_requested = False
        self._lock = threading.Lock()

    def do_startup(self):
        Gio.Application.do_startup(self)
        self.hold()
        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", lambda *_a: self.request_quit())
        self.add_action(quit_action)
        self.monitor = Gio.VolumeMonitor.get()
        self.monitor.connect("mount-added", self.on_mount)
        log.info("Waiting for the watch to be connected…")
        # The watch may have been connected before the service started.
        for m in self.monitor.get_mounts():
            if garmin_usb(m, self.monitor):
                GLib.timeout_add_seconds(self.delay_s, self.start_sync)

    def do_activate(self):
        pass

    def notify(self, title: str, body: str) -> None:
        n = Gio.Notification.new(title)
        n.set_body(body)
        self.send_notification("sync", n)

    def on_mount(self, _monitor, mount):
        if own_remount(mount):
            return  # mounted again by our own install, not plugged in
        if garmin_usb(mount):
            GLib.timeout_add_seconds(self.delay_s, self.start_sync)

    def request_quit(self) -> None:
        """Quit now, or after the running sync (never interrupt writes to the watch)."""
        with self._lock:
            if self.quit_requested:
                return
            self.quit_requested = True
            busy = self.running
        if busy:
            log.info("Quitting after the current sync finishes")
        else:
            self.release()

    def start_sync(self):
        with self._lock:
            if self.quit_requested:
                return False
            if self.running:
                # Re-plugged during a sync: run once more afterwards.
                self.pending = True
                return False
            self.running = True
        # Non-daemon: the process waits for a sync to finish instead of killing it mid-write.
        threading.Thread(target=self._worker, daemon=False).start()
        return False

    def _worker(self):
        try:
            while True:
                self._sync()
                with self._lock:
                    if not self.pending or self.quit_requested:
                        break
                    self.pending = False
        finally:
            with self._lock:
                self.running = False
                self.pending = False
                quit_now = self.quit_requested
            if quit_now:
                GLib.idle_add(lambda: (self.release(), False)[1])

    def _open_session(self, sleep=time.sleep):
        for attempt in range(OPEN_RETRIES + 1):
            try:
                return service.open_session()
            except DeviceError:
                # Right after mounting, gvfs may not be ready yet.
                if attempt == OPEN_RETRIES or self.quit_requested:
                    raise
                log.info("Watch not ready, retrying in %d s", OPEN_RETRY_DELAY_S)
                sleep(OPEN_RETRY_DELAY_S)

    def _sync(self):
        try:
            s = self._open_session()
            if not s.logged_in:
                log.info("Not logged in: skipping sync")
                return
            # First, so a watch of another account gets one notification and nothing else.
            service.require_account_device(s.client, s.device.unit_id)
            if s.first_sync:
                GLib.idle_add(self.notify, _("Garmin: first sync required"),
                              _("Open openExpress and choose how to sync the device."))
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
        except OwnershipError as e:
            log.info("Not syncing: %s", e)
            GLib.idle_add(self.notify, _("Garmin: this device was not synced"), str(e))
        except Exception as e:
            log.exception("Sync failed")
            GLib.idle_add(self.notify, _("Sync failed"), str(e))


def main() -> int:
    from openexpress import applog

    applog.setup("watch", stderr_level=logging.INFO, stderr_format="%(message)s")
    return Watcher().run([])
