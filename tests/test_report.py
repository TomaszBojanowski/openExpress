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
