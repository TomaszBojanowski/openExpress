import pytest

from openexpress import report
from openexpress.device.storage import LocalStorage
from openexpress.device.xmldevice import parse


class FakeMtpStorage(LocalStorage):
    label = "mtp://091e_51b4_0000abcdef12/Internal%20Storage"
    usb_id = "091e:51b4"


def test_report_has_no_identifiers(device_root):
    st = FakeMtpStorage(device_root)
    st.label = FakeMtpStorage.label
    dev = parse(st.read("GARMIN/GarminDevice.xml"))
    text = report.build(dev, st, logged_in=True)
    assert "Test watch" in text and "006-B0000-00" in text and "23.39" in text
    assert "USB ID: 091e:51b4" in text
    assert str(dev.unit_id) not in text           # unit ID
    assert "0000abcdef12" not in text             # serial number from the MTP address
    assert "mtp://" not in text
    assert "- [x] Activities" in text and "- [ ] Music" in text


def test_report_has_no_user_name_and_no_home_folder(device_root):
    from pathlib import Path

    from openexpress import applog

    st = FakeMtpStorage(device_root)
    text = report.build(parse(st.read("GARMIN/GarminDevice.xml")), st, logged_in=True)
    for private in (applog._system_user(), str(Path.home()), str(device_root)):
        assert not private or private not in text


def test_usb_id_comes_from_the_storage_not_from_the_address(device_root):
    st = LocalStorage(device_root)
    dev = parse(st.read("GARMIN/GarminDevice.xml"))
    assert "- USB ID: unknown" in report.build(dev, st)  # e.g. --path

    # An address made of the USB strings carries no IDs, only the serial number.
    st.label = "mtp://Garmin_Edge_1050_0000abcdef12/Internal%20Storage"
    assert "- USB ID: unknown" in report.build(dev, st)
    st.usb_id = "091e:5158"
    text = report.build(dev, st)
    assert "- USB ID: 091e:5158" in text
    assert "0000abcdef12" not in text and "mtp://" not in text and "Garmin_Edge" not in text
    st.usb_id = "091e:unknown"
    assert "- USB ID: 091e:unknown" in report.build(dev, st)


# --- an address that opens the GitHub form filled in -------------------------------------------


def _query(url):
    from urllib.parse import parse_qs, urlsplit

    parts = urlsplit(url)
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == report.ISSUE_FORM
    return {key: values for key, values in parse_qs(parts.query, keep_blank_values=True).items()}


def test_issue_address_fills_in_the_title_and_the_report():
    text = "### Device\n- Model: fēnix 8\na & b # c + d = 100% ? / ;\n\n[x] ok\n"
    url = report.issue_url(text, "fēnix 8")
    assert _query(url) == {"template": ["device_report.yml"], "title": ["[Device] fēnix 8"],
                           "report": [text]}  # exactly as given, special characters too
    assert "\n" not in url and " " not in url and "#" not in url.split("?", 1)[1]
    assert report.issue_url("x", None).endswith("&report=x")
    assert _query(report.issue_url("x", None))["title"] == ["[Device] unknown"]
    # The address of the empty form is the same as before.
    assert report.ISSUE_URL == ("https://github.com/TomaszBojanowski/openExpress/issues/new"
                                "?template=device_report.yml")


def test_issue_address_has_a_length_limit():
    fits = report.issue_url("x", "M")
    room = report.MAX_URL - len(fits)
    assert len(report.issue_url("x" * (1 + room), "M")) == report.MAX_URL  # just fits
    assert report.issue_url("x" * (2 + room), "M") is None
    assert report.issue_url("ę" * 1500, "M") is None  # 6 characters each once encoded


def test_issue_address_of_a_real_report_has_no_identifiers(device_root):
    st = FakeMtpStorage(device_root)
    st.label = FakeMtpStorage.label
    dev = parse(st.read("GARMIN/GarminDevice.xml"))
    text = report.build(dev, st, logged_in=True)
    url = report.issue_url(text, dev.description)
    assert url is not None and len(url) < report.MAX_URL / 2  # the report fits with room
    assert _query(url)["report"] == [text] and _query(url)["title"] == ["[Device] Test watch"]
    for private in (str(dev.unit_id), "0000abcdef12", "mtp"):
        assert private not in url


# --- the end of the log in a report: only when asked for ---------------------------------------


def _write_log(log_file, lines):
    log_file.parent.mkdir(parents=True, exist_ok=True)
    log_file.write_text("".join(line + "\n" for line in lines), encoding="utf-8")


def test_cli_report_says_where_the_log_is_and_leaves_it_out(device_root, xdg, log_file, capsys,
                                                            monkeypatch, tmp_path):
    from openexpress.cli import main

    monkeypatch.setenv("HOME", str(tmp_path))  # the state folder is inside the home folder
    monkeypatch.delenv("XDG_STATE_HOME")
    assert main(["--path", str(device_root), "report"]) == 0
    out = capsys.readouterr()
    assert "### Log" not in out.out and out.out.rstrip().endswith("Logged in to Garmin Connect: no")
    assert out.err.rstrip().endswith(
        "Log file: ~/.local/state/openexpress/openexpress.log (add --log to print its last "
        "lines for the \u201cLog\u201d field of the form or a comment)")
    assert str(tmp_path) not in out.err


def test_cli_report_with_log_prints_the_filtered_end(device_root, xdg, log_file, capsys):
    """The log alone, to paste into the "Log" field of the form or into a comment (the
    report is in the address): no heading, between two lines of tildes."""
    from openexpress.cli import main

    assert main(["--path", str(device_root), "info"]) == 0  # something to show
    capsys.readouterr()
    with open(log_file, "a", encoding="utf-8") as f:
        # As if written before a rule existed: the report filters again.
        f.write("old line: device 3987654321 at mtp://091e_51b4_0000abcdef12/ for a@example.org\n")
    assert main(["--path", str(device_root), "report", "--log"]) == 0
    out = capsys.readouterr()
    log_part = out.out
    assert "### " not in log_part and "```" not in log_part
    lines = log_part.splitlines()
    assert len(lines) == 6 and lines[0] == lines[-1] == "~~~" and log_part.endswith("\n")
    assert "start: info --path" in log_part and "start: report --path --log" in log_part
    assert "old line: device <number> at mtp://<device>/ for <email>" in log_part
    for secret in ("3987654321", "0000abcdef12", "a@example.org", str(device_root), "1234567890"):
        assert secret not in out.out
    assert "read it before you send it" in out.err


def test_cli_report_with_log_prints_what_the_window_puts_in_the_clipboard(
        device_root, xdg, log_file, capsys, monkeypatch):
    """`report --log` prints the same text as "Open on GitHub" puts in the clipboard
    (report.form_log): ready for the "Log" field of the form, the end of crash.log after it."""
    from openexpress import applog
    from openexpress.cli import main

    monkeypatch.setattr(applog, "tail", lambda count=200: ["one", "device <number>"])
    monkeypatch.setattr(applog, "crash_tail", lambda count=50: ["Fatal Python error"])
    assert main(["--path", str(device_root), "report", "--log"]) == 0
    out = capsys.readouterr()
    assert out.out == ("~~~\none\ndevice <number>\n\n# Crash log (last 1 lines of crash.log)\n"
                       "Fatal Python error\n~~~\n")
    assert out.out == report.form_log(["one", "device <number>"], ["Fatal Python error"])
    assert ("The end of the log is printed above. It is filtered, but read it before you "
            "send it.") in out.err.splitlines()


def test_the_log_to_paste_is_fenced_with_tildes():
    """Pasted into a comment on GitHub, the log is a block of code too, not one paragraph
    with its <path> and <user> eaten as tags (#19). The fence is of tildes: in the form's
    "Log" field, which GitHub puts between backticks itself, it does not close that block
    and shows only as two lines more."""
    assert report.form_log(["one", "two"]) == "~~~\none\ntwo\n~~~\n"
    assert report.form_log([]) == "~~~\n(no log file)\n~~~\n"


def test_no_line_of_the_log_closes_its_fence():
    """A line that begins with tildes (also after spaces) could close a fence as long as
    them: the fence is longer than any."""
    text = report.form_log(["~~~ made up", "a"], ["  ~~~~~ also made up"])
    assert text.startswith("~~~~~~\n~~~ made up\na\n") and text.endswith("~~~~~~\n")


def test_the_words_about_pasting_the_log_in_polish():
    from openexpress import i18n
    from openexpress.i18n import _

    i18n.setup("pl")
    try:
        window = _("The log is in your clipboard. Paste it into the \u201cLog\u201d field of the "
                   "form or into a comment.")
        cli = _("The log is not in the address: paste it into the \u201cLog\u201d field of the "
                "form or into a comment.")
    finally:
        i18n.setup("en")
    # Hard spaces after the one-letter words (tools/po-hard-spaces.py).
    assert window == ("Log jest w\u00a0schowku. Wklej go w\u00a0pole \u201eLog\u201d formularza albo "
                      "w\u00a0komentarz.")
    assert cli == ("Logu nie ma w\u00a0adresie: wklej go w\u00a0pole \u201eLog\u201d formularza "
                   "albo w\u00a0komentarz.")


def test_the_report_has_plain_spaces(device_root, log_file):
    """Text for GitHub: no hard space, also in a Polish session and in a line of the log
    written with one (by a version from before 22.10.2026)."""
    from openexpress import applog, i18n

    i18n.setup("pl")
    try:
        st = LocalStorage(device_root)
        text = report.build(parse(st.read("GARMIN/GarminDevice.xml")), st, logged_in=True)
    finally:
        i18n.setup("en")
    _write_log(log_file, ["old: plik z\u00a0urządzenia, 2,5\u00a0MB"])
    tail = applog.tail(report.LOG_LINES)
    assert tail == ["old: plik z urządzenia, 2,5 MB"]
    assert "\u00a0" not in report.with_log(text, tail)


def test_only_the_last_200_lines_are_attached(log_file):
    from openexpress import applog

    _write_log(log_file.with_name(log_file.name + ".1"), [f"older {n}" for n in range(150)])
    _write_log(log_file, [f"line {n}" for n in range(120)])
    tail = applog.tail(report.LOG_LINES)
    assert len(tail) == 200 and tail[0] == "older 70" and tail[-1] == "line 119"
    assert applog.tail(3) == ["line 117", "line 118", "line 119"]
    text = report.with_log("### System\n", tail)
    assert text.startswith("### System\n\n### Log (last 200 lines)\n```\nolder 70\n")


def test_report_without_a_log_file_says_so(log_file):
    from openexpress import applog

    assert applog.tail() == []
    assert report.with_log("### System\n", []) == "### System\n\n### Log\n(no log file)\n"


def test_window_copies_exactly_what_it_shows(log_file):
    from openexpress.gui.app import MainWindow

    _write_log(log_file, ["one", "device 3987654321"])
    assert MainWindow._report_and_log("### Device\n", False)[0] == "### Device\n"  # the default
    assert MainWindow._report_and_log("### Device\n", True)[0] == (
        "### Device\n\n### Log (last 2 lines)\n```\none\ndevice <number>\n```\n")


# --- the window: Open on GitHub fills in the form ----------------------------------------------


def _report_dialog(monkeypatch, device_root, connected=True):
    """The report question of a window that is never shown, with what it would open, copy
    and say recorded."""
    import types

    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gtk

    from openexpress.gui.app import MainWindow

    st = LocalStorage(device_root)
    # The space as the window's session holds it: read when the device was opened.
    session = types.SimpleNamespace(device=parse(st.read("GARMIN/GarminDevice.xml")),
                                    storage=st, logged_in=True,
                                    space=(st.free_space(), st.capacity()))
    opened, copied, said, asked = [], [], [], []

    class Launcher:
        def __init__(self, uri):
            self.uri = uri

        def launch(self, *_a):
            opened.append(self.uri)

    monkeypatch.setattr(Gtk, "UriLauncher", types.SimpleNamespace(new=Launcher))
    monkeypatch.setattr(Adw.AlertDialog, "present", lambda self, parent: asked.append(self))
    win = types.SimpleNamespace(
        session=session if connected else None, toast=said.append,
        get_clipboard=lambda: types.SimpleNamespace(set=copied.append),
        _report_view=MainWindow._report_view)
    win._open_report_form = types.MethodType(MainWindow._open_report_form, win)
    MainWindow.show_report(win)
    (dialog,) = asked
    text = report.build(session.device, st, True)
    return dialog, text, opened, copied, said


def _attach_log(dialog):
    from gi.repository import Gtk

    def find(widget):
        if isinstance(widget, Gtk.CheckButton):
            return widget
        child = widget.get_first_child()
        while child is not None:
            found = find(child)
            if found is not None:
                return found
            child = child.get_next_sibling()
        return None

    find(dialog.get_extra_child()).set_active(True)


@pytest.mark.needs_display
def test_open_on_github_fills_in_the_report(monkeypatch, device_root, log_file):
    dialog, text, opened, copied, said = _report_dialog(monkeypatch, device_root)
    assert "nothing is published until you submit it" in dialog.get_body()
    assert "no serial number, unit ID or e-mail" in dialog.get_body()
    assert dialog.get_response_label("open") == "Open on GitHub"
    assert dialog.get_response_label("copy") == "Copy"  # still there
    dialog.emit("response", "open")
    assert opened == [report.issue_url(text, "Test watch")]
    assert copied == [] and said == []


def _note(dialog):
    """The sentence under "Attach the last log": (shown, its words)."""
    from gi.repository import Gtk

    stack = [dialog.get_extra_child()]
    while stack:
        widget = stack.pop()
        if isinstance(widget, Gtk.Label) and widget.get_name() == "report-log-note":
            return widget.get_visible(), widget.get_label()
        child = widget.get_first_child()
        while child is not None:
            stack.append(child)
            child = child.get_next_sibling()
    return None


@pytest.mark.needs_display
def test_open_on_github_puts_the_log_in_the_clipboard(monkeypatch, device_root, log_file):
    """#18, #19: the log never goes into the address. The form has a field of its own for it
    ("Log": pasting into the field filled in from the address loses what is pasted), so the
    report field gets the report alone; the log goes to the clipboard as that field shows
    it (as code: its lines alone), and the dialog stays to say where to paste it (a toast on
    the window was missed once the browser came up)."""
    from urllib.parse import parse_qs, urlsplit

    _write_log(log_file, ["one", "device 3987654321"])
    dialog, text, opened, copied, said = _report_dialog(monkeypatch, device_root)
    assert _note(dialog) == (False, ("The log is in your clipboard. Paste it into the "
                                     "\u201cLog\u201d field of the form or into a comment."))
    _attach_log(dialog)
    dialog.emit("response", "open")
    (url,) = opened
    assert parse_qs(urlsplit(url).query)["report"][0] == text
    assert "device <number>" not in url and "### Log" not in url
    assert copied == ["~~~\none\ndevice <number>\n~~~\n"]
    assert _note(dialog)[0] is True and said == []
    assert not dialog.get_can_close()  # it stays open with the sentence


@pytest.mark.needs_display
def test_the_crash_log_goes_to_the_log_field_too(monkeypatch, device_root, log_file):
    from openexpress import applog

    _write_log(log_file, ["one"])
    applog.crash_path().write_text("Fatal Python error\n", encoding="utf-8")
    dialog, text, opened, copied, said = _report_dialog(monkeypatch, device_root)
    _attach_log(dialog)
    dialog.emit("response", "open")
    assert copied == ["~~~\none\n\n# Crash log (last 1 lines of crash.log)\nFatal Python error\n~~~\n"]


@pytest.mark.needs_display
def test_copy_with_the_log_is_as_before(monkeypatch, device_root, log_file):
    _write_log(log_file, ["one"])
    dialog, text, opened, copied, said = _report_dialog(monkeypatch, device_root)
    _attach_log(dialog)
    dialog.emit("response", "copy")
    assert copied == [report.with_log(text, ["one"])] and said == ["Report copied"]
    assert opened == [] and _note(dialog)[0] is False


@pytest.mark.needs_display
def test_the_sentence_about_the_log_in_polish(monkeypatch, device_root, log_file):
    from openexpress import i18n

    _write_log(log_file, ["one"])
    i18n.setup("pl")
    try:
        dialog, _text, _opened, _copied, _said = _report_dialog(monkeypatch, device_root)
    finally:
        i18n.setup("en")
    assert _note(dialog)[1] == ("Log jest w\u00a0schowku. Wklej go w\u00a0pole \u201eLog\u201d "
                                "formularza albo w\u00a0komentarz.")


def test_the_report_alone_fits_an_address_with_room_to_spare(device_root):
    """The report has a fixed number of lines; it stays well under MAX_URL (the empty form
    and the clipboard remain for anything longer)."""
    st = LocalStorage(device_root)
    text = report.build(parse(st.read("GARMIN/GarminDevice.xml")), st, True)
    url = report.issue_url(text, "Test watch")
    assert url is not None and len(url) < report.MAX_URL // 2


@pytest.mark.needs_display
def test_open_on_github_with_a_report_too_long_copies_it(monkeypatch, device_root, log_file):
    monkeypatch.setattr(report, "MAX_URL", 100)
    _write_log(log_file, ["one"])
    dialog, text, opened, copied, said = _report_dialog(monkeypatch, device_root)
    _attach_log(dialog)
    dialog.emit("response", "open")
    assert opened == [report.ISSUE_URL]  # the empty form, as before
    assert copied == [report.with_log(text, ["one"])]  # everything that is shown
    assert said == ["Report copied: paste it into the form"]


@pytest.mark.needs_display
def test_open_on_github_without_a_device_opens_the_empty_form(monkeypatch, device_root):
    dialog, _text, opened, copied, said = _report_dialog(monkeypatch, device_root,
                                                         connected=False)
    dialog.emit("response", "open")
    assert opened == [report.ISSUE_URL] and copied == [] and said == []


@pytest.mark.needs_display
def test_open_on_github_in_polish(monkeypatch, device_root, log_file):
    from openexpress import i18n

    _write_log(log_file, ["one"])
    i18n.setup("pl")
    try:
        dialog, _text, _opened, _copied, said = _report_dialog(monkeypatch, device_root)
        _attach_log(dialog)
        dialog.emit("response", "open")
        monkeypatch.setattr(report, "MAX_URL", 100)
        too_long, _t, _o, _c, said_too_long = _report_dialog(monkeypatch, device_root)
        too_long.emit("response", "open")
    finally:
        i18n.setup("en")
    assert dialog.get_response_label("open") == "Otwórz na GitHubie"
    assert dialog.get_body() == (
        "„Otwórz na GitHubie” zaczyna nowe zgłoszenie z\u00a0wypełnionym raportem; nic nie zostanie "
        "opublikowane, dopóki go nie wyślesz. Raport nie zawiera numeru seryjnego, "
        "identyfikatora urządzenia ani adresu e-mail.")  # the same words as on the command line
    assert said == []  # the dialog says it (test_the_sentence_about_the_log_in_polish)
    assert said_too_long == ["Raport skopiowany: wklej go w\u00a0formularzu"]


# --- the command line: an address that fills in the form --------------------------------------


def test_cli_report_prints_an_address_with_the_report(device_root, xdg, log_file, capsys):
    from openexpress.cli import main

    assert main(["--path", str(device_root), "report"]) == 0
    out = capsys.readouterr()
    url = report.issue_url(out.out, "Test watch")
    assert url and url in out.err  # the report on stdout is the one in the address
    assert ("This report contains no serial number, unit ID or e-mail. Open this address to "
            f"start a device report with it filled in: {url}") in out.err
    assert "1234567890" not in out.err and "mtp" not in out.err
    assert "The log is not in the address" not in out.err


def test_cli_report_with_log_keeps_the_log_out_of_the_address(device_root, xdg, log_file,
                                                              capsys):
    from openexpress.cli import main

    _write_log(log_file, ["a line of the log"])
    assert main(["--path", str(device_root), "report", "--log"]) == 0
    out = capsys.readouterr()
    st = LocalStorage(device_root)
    report_text = report.build(parse(st.read("GARMIN/GarminDevice.xml")), st, False)
    assert report.issue_url(report_text, "Test watch") in out.err
    assert "a line of the log" in out.out and "a%20line%20of%20the%20log" not in out.err
    assert ("The log is not in the address: paste it into the \u201cLog\u201d field of the "
            "form or into a comment.") in out.err
    assert "read it before you send it" in out.err


def test_cli_report_too_long_for_an_address_gives_the_empty_form(device_root, xdg, log_file,
                                                                 capsys, monkeypatch):
    from openexpress.cli import main

    monkeypatch.setattr(report, "MAX_URL", 100)
    assert main(["--path", str(device_root), "report", "--log"]) == 0
    err = capsys.readouterr().err
    assert (f"Paste it into a device report: {report.ISSUE_URL}\n") in err
    assert "Open this address" not in err and "not in the address" not in err


def test_cli_report_address_in_polish(device_root, xdg, log_file, capsys):
    from openexpress import i18n
    from openexpress.cli import main

    try:
        assert main(["--path", str(device_root), "--lang", "pl", "report", "--log"]) == 0
    finally:
        i18n.setup("en")
    err = capsys.readouterr().err
    assert ("Ten raport nie zawiera numeru seryjnego, identyfikatora urządzenia ani adresu "
            "e-mail. Otwórz ten adres, aby zacząć zgłoszenie z wypełnionym raportem: "
            "https://github.com/") in err
    assert ("Logu nie ma w adresie: wklej go w pole \u201eLog\u201d formularza albo "
            "w komentarz.") in err


# --- the forms on GitHub -----------------------------------------------------------------------


@pytest.mark.parametrize("name", ["device_report.yml", "bug_report.yml"])
def test_the_forms_have_a_field_of_their_own_for_the_log(name):
    """Pasting into the "Device report" field filled in from the address loses what is
    pasted (26.10.2026): the log goes into a field of its own, which shows it as code."""
    from pathlib import Path

    yaml = pytest.importorskip("yaml")
    path = Path(__file__).parent.parent / ".github" / "ISSUE_TEMPLATE" / name
    form = yaml.safe_load(path.read_text(encoding="utf-8"))
    fields = [item for item in form["body"] if item.get("id")]
    log = fields[-1]  # the last field
    assert (log["type"], log["id"]) == ("textarea", "log")
    assert log["attributes"] == {
        "label": "Log", "render": "shell",
        "description": "Paste the output of report --log here if something didn't work."}
    assert not log.get("validations", {}).get("required", False)
    (field,) = [item for item in fields if item["id"] == "report"]
    assert "log" not in field["attributes"]["description"].lower()
