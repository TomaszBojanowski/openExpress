"""Forgetting a device from the read-only view of a device remembered and not connected (PLAN
T7): its button "Forget Device…", the question and what follows. ForgetDevice is a mixin of
MainWindow (gui/app.py), without __init__ of its own; what it uses of the window
(_remembered_unit, connected, session, navigation, toast, error, in_background) is the
window's. What goes is forget.forget_device's to decide."""
from __future__ import annotations

import logging

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk  # noqa: E402 - imports must follow gi.require_version

from openexpress import forget, known_devices, service  # noqa: E402
from openexpress.gui.window_tasks import Answer, confirm  # noqa: E402
from openexpress.i18n import _, ngettext  # noqa: E402

# The window's logger, as the pages split from it use.
log = logging.getLogger("openexpress.gui.app")


class ForgetDevice:
    def _forget_title(self) -> str:
        # TRANSLATORS: title of a message about forgetting a device (the button "Forget
        # Device…" on the page of a device not connected)
        return _("Forget Device")

    def _connected_now(self, unit_id: int) -> bool:
        """Whether the device is connected (as last listed) or open now."""
        s = self.session
        if s is not None and getattr(s.device, "unit_id", None) == unit_id:
            return True
        return any(getattr(c, "unit_id", None) == unit_id for c in self.connected.devices)

    def ask_forget(self, _button=None) -> None:
        """"Forget Device…" on the view of a remembered device: what goes, and a tick for what
        is lost only when asked (files not uploaded yet: Forget waits for it) or kept unless
        asked (the backups). Nothing for a device not remembered here (any more)."""
        unit = self._remembered_unit
        known = (known_devices.load().get(unit)
                 if unit is not None and not self._remembered_from_account else None)
        if known is None:
            return
        name = known.shown_name or _("Garmin device")
        if self._connected_now(unit):
            self.error(self._forget_title(), _(
                "{device} is connected: unplug it to forget it.").format(device=name))
            return
        kept = forget.kept(unit)
        body = _("openExpress forgets what it keeps of this device on this computer: what was "
                 "uploaded from it, its last sync, the maps of your own copied to it and the "
                 "copies of its diagnostic files. "
                 "Nothing changes on the device or on your Garmin account.\n\n"
                 "Connected again, it is a new device: its first sync asks again whether to "
                 "upload everything or new data only.")
        if kept.pending:
            body += "\n\n" + str(forget.PendingFiles(kept.pending))
        # What is ticked (data, no widget) goes with the ticks and the answer; the window
        # holds the question while it is open (keep_as): a handler that held the question or
        # a tick would keep them after it closed. "closed" comes before the answer.
        choice = {"unit": unit, "name": name, "pending": False, "backups": False}
        ticks = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        if kept.pending:
            tick = Gtk.CheckButton(label=ngettext(
                "Discard the file not uploaded yet", "Discard the {count} files not uploaded yet",
                kept.pending).format(count=kept.pending))
            tick.connect("toggled",
                         lambda button: self._forget_ticked(choice, "pending", button))
            ticks.append(tick)
        if kept.backups:
            tick = Gtk.CheckButton(label=ngettext(
                "Also delete its backup", "Also delete its {count} backups",
                kept.backups).format(count=kept.backups))
            tick.connect("toggled",
                         lambda button: self._forget_ticked(choice, "backups", button))
            ticks.append(tick)
        confirm(self, "forget-device",
                # TRANSLATORS: question before forgetting a device; {name} is its model
                _("Forget {name}?").format(name=name), body,
                # TRANSLATORS: button that forgets a device (in the question "Forget …?")
                Answer("forget", _("Forget"), lambda: self._forget_answered(choice),
                       look="destructive", enabled=not kept.pending),
                default="cancel", extra=ticks if ticks.get_first_child() is not None else None,
                keep_as="_forget_dialog")

    def _forget_ticked(self, choice: dict, which: str, button) -> None:
        """A tick of the question changed (read from the signal's button)."""
        choice[which] = button.get_active()
        dialog = getattr(self, "_forget_dialog", None)
        if which == "pending" and dialog is not None:
            dialog.set_response_enabled("forget", choice["pending"])

    def _forget_answered(self, choice: dict) -> None:
        """Forget, as `choice` says (the unit, its name, what was ticked): in the background
        (backups may be many files); back to the start page once it is done, where its tile
        is gone (_on_popped draws it anew)."""
        unit, name = choice["unit"], choice["name"]

        def work():
            return forget.forget_device(unit, discard_pending=choice["pending"],
                                        backups=choice["backups"])

        def done(forgotten: bool) -> None:
            if not forgotten:
                self.error(self._forget_title(), _("{device} was not forgotten.").format(
                    device=name))
                return
            if self._remembered_unit == unit:
                self._remembered_unit = None
            self.toast(_("Forgot {device}.").format(device=name))
            if self.navigation.get_visible_page() is self.remembered.page:
                self.navigation.pop_to_page(self.start.page)  # drawn by _on_popped

        def failed(e: Exception) -> None:
            if isinstance(e, service.SyncBusy):
                text = _("A sync is running. Forget the device once it has finished.")
            elif isinstance(e, forget.PendingFiles):
                text = str(e)  # more came since the question (not while it is unplugged)
            elif isinstance(e, OSError):
                text = _("{device} was not forgotten: {reason}").format(
                    device=name, reason=e.strerror or type(e).__name__)
            else:
                text = _("{device} was not forgotten.").format(device=name)
            log.info("devices: not forgotten (%s)", type(e).__name__)
            self.error(self._forget_title(), text)

        self.in_background(work, done, failed)
