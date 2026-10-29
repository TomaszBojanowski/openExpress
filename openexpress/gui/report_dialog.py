"""The report of a device or problem ("Report a Device or Problem": a preview, "Attach the last
log", Copy, Open on GitHub) and "About openExpress". ReportDialog is a mixin of MainWindow
(gui/app.py), without __init__ of its own.

Has: no attribute of the window; what a dialog keeps is in its own closures.
Uses: session (the device open, its storage and its space as the session holds them),
toast, get_clipboard; the dialogs are shown over the window (present(self))."""
from __future__ import annotations

import logging

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, GLib, Gtk  # noqa: E402 - imports must follow gi.require_version

from openexpress.i18n import _  # noqa: E402
from openexpress.paths import APP_ID  # noqa: E402

log = logging.getLogger(__name__)


def report_log_note(dialog) -> None:
    """The sentence of the report dialog that the log is in the clipboard, shown; found from
    the dialog of the signal (no widget held)."""
    stack = [dialog.get_extra_child()]
    while stack:
        widget = stack.pop()
        if widget is None:
            continue
        if isinstance(widget, Gtk.Label) and widget.get_name() == "report-log-note":
            widget.set_visible(True)
            return
        child = widget.get_first_child()
        while child is not None:
            stack.append(child)
            child = child.get_next_sibling()


class ReportDialog:
    @staticmethod
    def _report_and_log(report_text: str, attach_log: bool) -> tuple[str, str | None]:
        """The report, with the end of the log when the user asked for it, and that log as
        the "Log" field of the form takes it (None without it): read once, so that both
        say the same."""
        from openexpress import applog, report

        if not attach_log:
            return report_text, None
        lines, crash = applog.tail(report.LOG_LINES), applog.crash_tail()
        return report.with_log(report_text, lines, crash), report.form_log(lines, crash)

    @staticmethod
    def _report_view(report_text: str, kept: dict | None = None):
        """The preview of the report with the "attach the log" switch: (widget, text buffer).
        `kept["log"]`: the log shown, as the form's "Log" field takes it (None: not attached).

        No handler here may refer to a widget of the preview: a widget that keeps itself
        alive through its own handler outlives the dialog, and Python frees it whenever it
        next collects reference cycles. The switch therefore only knows the text buffer."""
        view = Gtk.TextView(editable=False, monospace=True, wrap_mode=Gtk.WrapMode.WORD_CHAR,
                            top_margin=8, bottom_margin=8, left_margin=8, right_margin=8)
        buffer = view.get_buffer()
        buffer.set_text(report_text)
        scroller = Gtk.ScrolledWindow(child=view, min_content_height=260, min_content_width=420)
        scroller.add_css_class("card")
        attach = Gtk.CheckButton(label=_("Attach the last log"))
        attach.set_tooltip_text(_("The log is filtered, but read it before you send it."))

        def toggled(button):
            text, log_for_form = ReportDialog._report_and_log(report_text, button.get_active())
            buffer.set_text(text)
            if kept is not None:
                kept["log"] = log_for_form

        attach.connect("toggled", toggled)
        # Shown once "Open on GitHub" put the log in the clipboard (show_report).
        note = Gtk.Label(label=_("The log is in your clipboard. Paste it into the “Log” "
                                 "field of the form or into a comment."), wrap=True, xalign=0,
                         visible=False, name="report-log-note", css_classes=["dim-label"])
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.append(scroller)
        box.append(attach)
        box.append(note)
        return box, buffer

    def show_report(self) -> None:
        from openexpress import report

        s = self.session
        if s is None:
            text = _("Connect the device first, so that the report can describe it.")
            body_text = ""
        else:
            # The space as the session holds it: nothing is asked of the device here.
            body_text = report.build(s.device, s.storage, s.logged_in,
                                     space=getattr(s, "space", None) or (None, None))
            text = _("Open on GitHub starts a new issue with this report filled in; nothing is "
                     "published until you submit it. The report contains no serial number, "
                     "unit ID or e-mail.")
        log.info("report dialog opened (device connected: %s)", s is not None)
        d = Adw.AlertDialog(heading=_("Report a Device or Problem"), body=text)
        buffer = None
        kept = {"log": None}  # the log shown, for the form's "Log" field (_report_view)
        if body_text:
            box, buffer = self._report_view(body_text, kept)
            d.set_extra_child(box)
            d.add_response("copy", _("Copy"))
        d.add_response("close", _("Close"))
        d.add_response("open", _("Open on GitHub"))
        d.set_response_appearance("open", Adw.ResponseAppearance.SUGGESTED)
        d.set_close_response("close")
        # "Open on GitHub" with the log attached keeps the dialog to say where the log is:
        # the dialog closes only when the answer lets it. A click says "close-attempt"
        # before "response", Escape only "close-attempt": the closing waits for the answer,
        # if any, in the main loop.
        d.set_can_close(False)
        stay = {"open": False}

        def shown() -> str:
            """Exactly what the preview shows."""
            return buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False)

        def settle(dialog) -> bool:
            if not stay.pop("open", False):
                dialog.force_close()
            return False

        def response(dialog, r):
            if r == "copy":
                self.get_clipboard().set(shown())
                self.toast(_("Report copied"))
            elif r == "open":
                attached = self._open_report_form(
                    body_text, shown() if buffer is not None else "", kept["log"],
                    s.device.description if s is not None else None)
                if attached:
                    stay["open"] = True
                    report_log_note(dialog)

        d.connect("close-attempt", lambda dialog: GLib.idle_add(settle, dialog))
        d.connect("response", response)
        d.present(self)

    def _open_report_form(self, report_text: str, shown: str, log_for_form: str | None,
                          model: str | None) -> bool:
        """Open a new device report on GitHub with the report filled in. The log never goes
        into the address (too long, and the address is sent to GitHub when the page opens),
        nor into the report field (what is pasted into a field filled in from the address is
        lost): when it is attached (`log_for_form`), it goes to the clipboard, for the
        form's "Log" field; whether it did. A report too long for an address goes to the
        clipboard whole, with the empty form."""
        from openexpress import report

        attached = bool(report_text) and log_for_form is not None
        url = report.issue_url(report_text, model) if report_text else None
        if report_text and url is None:
            self.get_clipboard().set(shown)
            self.toast(_("Report copied: paste it into the form"))
            attached = False
        elif attached:
            self.get_clipboard().set(log_for_form)
        Gtk.UriLauncher.new(url or report.ISSUE_URL).launch(self, None, None, None)
        return attached

    def show_about(self) -> None:
        from openexpress import __version__

        about = Adw.AboutDialog(
            application_name="openExpress",
            application_icon=APP_ID,
            version=__version__,
            developer_name="Tomasz Bojanowski",
            website="https://github.com/TomaszBojanowski/openExpress",
            issue_url="https://github.com/TomaszBojanowski/openExpress/issues",
            license_type=Gtk.License.GPL_3_0,
            comments=_("Unofficial Linux app for Garmin watches: sync with Garmin Connect, "
                       "software updates, maps, Connect IQ apps and music.\n\n"
                       "openExpress is not affiliated with, endorsed by or supported by "
                       "Garmin Ltd. Garmin, fēnix, Garmin Connect and Garmin Express are "
                       "trademarks of Garmin Ltd. or its subsidiaries."),
        )
        # TRANSLATORS: a link in "About openExpress" to the page where one can support it
        about.add_link(_("Support the project"), "https://buymeacoffee.com/sojer2005")
        # TRANSLATORS: replace with your name(s) in the translated version
        credits = _("translator-credits")
        if credits != "translator-credits":
            about.set_translator_credits(credits)
        about.present(self)
