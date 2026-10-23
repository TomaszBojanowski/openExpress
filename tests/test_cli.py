from openexpress.cli import main


def test_info_and_files(device_root, xdg, capsys):
    assert main(["--path", str(device_root), "info"]) == 0
    out = capsys.readouterr().out
    assert "Test watch" in out and "23.39" in out
    assert main(["--path", str(device_root), "files", "--type", "FIT_TYPE_4"]) == 0
    out = capsys.readouterr().out
    assert "2026-10-02-10-00-00.fit" in out and "notes.txt" not in out


def test_files_total_counts_every_file_once(device_root, capsys):
    # Like maps on a real device: two more data types that share folders with others.
    xml = device_root / "GARMIN" / "GarminDevice.xml"
    extra = """<DataType><Name>EverythingFit</Name>
   <File><Location><Path>Garmin</Path><FileExtension>FIT</FileExtension></Location>
    <TransferDirection>OutputFromUnit</TransferDirection></File></DataType>
  <DataType><Name>ActivitiesAgain</Name>
   <File><Location><Path>GARMIN/ACTIVITY</Path><FileExtension>FIT</FileExtension></Location>
    <TransferDirection>OutputFromUnit</TransferDirection></File></DataType>
  <UpdateFile>"""
    xml.write_text(xml.read_text().replace("<UpdateFile>", extra, 1))
    assert main(["--path", str(device_root), "files"]) == 0
    out = capsys.readouterr().out
    rows = {line.split()[0]: int(line.split()[1]) for line in out.splitlines()
            if line and not line.startswith("Total")}
    assert rows["FIT_TYPE_4"] == 2 and rows["ActivitiesAgain"] == 2 and rows["EverythingFit"] == 4
    assert sum(rows.values()) > 4  # the rows overlap…
    assert out.splitlines()[-1] == "Total: 4 files"  # …the total does not

    assert main(["--path", str(device_root), "files", "--type", "FIT_TYPE_4"]) == 0
    assert capsys.readouterr().out.splitlines()[-1] == "Total: 2 files"


def test_backup_keeps_n(device_root, xdg, tmp_path, capsys, monkeypatch):
    from datetime import datetime as real_dt

    import openexpress.backup as b

    dest = tmp_path / "backups"
    for i in range(4):
        class FakeDT(real_dt):
            @classmethod
            def now(cls, tz=None, _i=i):
                return real_dt(2026, 1, 1, 10, 0, _i)
        monkeypatch.setattr(b, "datetime", FakeDT)
        assert main(["--path", str(device_root), "backup", "--dest", str(dest), "--apply"]) == 0
    dirs = sorted(p.name for p in (dest / "1234567890").iterdir())
    assert len(dirs) == 3 and dirs[0].endswith("10.00.01)")
    latest = dest / "1234567890" / dirs[-1]
    assert (latest / "GarminDevice.xml").exists()
    assert (latest / "FIT_TYPE_4" / "2026-10-01-10-00-00.fit").exists()


def test_sync_requires_setup(device_root, xdg, capsys):
    assert main(["--path", str(device_root), "sync"]) == 1
    assert "openexpress setup" in capsys.readouterr().err


def test_no_command_opens_the_window(monkeypatch):
    import openexpress.cli as cli

    called = []
    monkeypatch.setattr(cli, "cmd_gui", lambda args: called.append(args) or 0)
    assert cli.main([]) == 0
    assert cli.main(["--lang", "en"]) == 0
    assert len(called) == 2


def test_last_sync_is_remembered(xdg):
    from openexpress import service

    summary = service.SyncSummary(uploaded=2, downloaded=1, errors=[], lines=["Uploaded 2."])
    service.save_last_sync(42, summary)
    data = service.load_last_sync(42)
    assert data["uploaded"] == 2 and data["lines"] == ["Uploaded 2."]
    assert service.load_last_sync(7) is None  # another watch


def test_format_when():
    import time

    from openexpress.i18n import format_when

    assert format_when(time.time()).startswith("Today at ")
    assert format_when(time.time() - 86400).startswith("Yesterday at ")
