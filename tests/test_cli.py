from openexpress.cli import main


def test_info_and_files(device_root, xdg, capsys):
    assert main(["--path", str(device_root), "info"]) == 0
    out = capsys.readouterr().out
    assert "Test watch" in out and "23.39" in out
    assert main(["--path", str(device_root), "files", "--type", "FIT_TYPE_4"]) == 0
    out = capsys.readouterr().out
    assert "2026-10-02-10-00-00.fit" in out and "notes.txt" not in out


def test_backup_keeps_n(device_root, xdg, tmp_path, capsys, monkeypatch):
    import openexpress.backup as b
    from datetime import datetime as real_dt

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
