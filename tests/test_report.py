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
        "Log file: ~/.local/state/openexpress/openexpress.log (add --log to attach its last "
        "lines to the report)")
    assert str(tmp_path) not in out.err


def test_cli_report_with_log_appends_the_filtered_end(device_root, xdg, log_file, capsys):
    from openexpress.cli import main

    assert main(["--path", str(device_root), "info"]) == 0  # something to show
    capsys.readouterr()
    with open(log_file, "a", encoding="utf-8") as f:
        # As if written before a rule existed: the report filters again.
        f.write("old line: device 3987654321 at mtp://091e_51b4_0000abcdef12/ for a@example.org\n")
    assert main(["--path", str(device_root), "report", "--log"]) == 0
    out = capsys.readouterr()
    report_text, log_part = out.out.split("\n### Log (last ")
    assert "### System" in report_text
    assert log_part.startswith("4 lines)\n```\n") and log_part.endswith("\n```\n")
    assert "start: info --path" in log_part and "start: report --path --log" in log_part
    assert "old line: device <number> at mtp://<device>/ for <email>" in log_part
    for secret in ("3987654321", "0000abcdef12", "a@example.org", str(device_root), "1234567890"):
        assert secret not in out.out
    assert "read it before you send it" in out.err


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
    assert MainWindow._report_text("### Device\n", False) == "### Device\n"  # the default
    assert MainWindow._report_text("### Device\n", True) == (
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

    if not Gtk.init_check():
        pytest.skip("no display")
    st = LocalStorage(device_root)
    session = types.SimpleNamespace(device=parse(st.read("GARMIN/GarminDevice.xml")),
                                    storage=st, logged_in=True)
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


def test_open_on_github_fills_in_the_report(monkeypatch, device_root, log_file):
    dialog, text, opened, copied, said = _report_dialog(monkeypatch, device_root)
    assert "nothing is published until you submit it" in dialog.get_body()
    assert "no serial number, unit ID or e-mail" in dialog.get_body()
    assert dialog.get_response_label("open") == "Open on GitHub"
    assert dialog.get_response_label("copy") == "Copy"  # still there
    dialog.emit("response", "open")
    assert opened == [report.issue_url(text, "Test watch")]
    assert copied == [] and said == []


def test_open_on_github_puts_the_log_in_the_clipboard(monkeypatch, device_root, log_file):
    _write_log(log_file, ["one", "device 3987654321"])
    dialog, text, opened, copied, said = _report_dialog(monkeypatch, device_root)
    _attach_log(dialog)
    dialog.emit("response", "open")
    assert opened == [report.issue_url(text, "Test watch")]  # the report only, never the log
    assert copied == ["### Log (last 2 lines)\n```\none\ndevice <number>\n```\n"]
    assert said == ["The log is in the clipboard: paste it into the issue"]


def test_open_on_github_with_a_report_too_long_copies_it(monkeypatch, device_root, log_file):
    monkeypatch.setattr(report, "MAX_URL", 100)
    _write_log(log_file, ["one"])
    dialog, text, opened, copied, said = _report_dialog(monkeypatch, device_root)
    _attach_log(dialog)
    dialog.emit("response", "open")
    assert opened == [report.ISSUE_URL]  # the empty form, as before
    assert copied == [report.with_log(text, ["one"])]  # everything that is shown
    assert said == ["Report copied: paste it into the form"]


def test_open_on_github_without_a_device_opens_the_empty_form(monkeypatch, device_root):
    dialog, _text, opened, copied, said = _report_dialog(monkeypatch, device_root,
                                                         connected=False)
    dialog.emit("response", "open")
    assert opened == [report.ISSUE_URL] and copied == [] and said == []


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
        "„Otwórz na GitHubie” zaczyna nowe zgłoszenie z wypełnionym raportem; nic nie zostanie "
        "opublikowane, dopóki go nie wyślesz. Raport nie zawiera numeru seryjnego, "
        "identyfikatora urządzenia ani adresu e-mail.")  # the same words as on the command line
    assert said == ["Log jest w schowku: wklej go w zgłoszeniu"]
    assert said_too_long == ["Raport skopiowany: wklej go w formularzu"]


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
    report_text = out.out.split("\n### Log (last ")[0]
    assert report.issue_url(report_text, "Test watch") in out.err
    assert "a line of the log" in out.out and "a%20line%20of%20the%20log" not in out.err
    assert "The log is not in the address: paste it into the issue." in out.err
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
    assert "Logu nie ma w adresie: wklej go w zgłoszeniu." in err
