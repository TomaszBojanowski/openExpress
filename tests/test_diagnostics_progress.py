"""The progress of the copies of diagnostic files: what is copied and which of how many
("Copying crash logs · 1 of 2"), never the name of a file. All devices and files are made
up."""
from test_diagnostics import device, hiding  # noqa: F401 - fixtures

from openexpress import diagnostics, i18n
from openexpress.progress import Step

NB = " "


def _no_file_names(seen):
    return not [m for m in seen if "GARMIN/" in m.upper() or ".txt" in m.lower()
                or ".zip" in m.lower()]


def test_the_crash_logs_are_counted(hiding, clock):
    st, dev, _fake = hiding
    seen = []
    diagnostics.copy_crash_logs(dev, st, progress=seen.append)
    assert all(isinstance(m, Step) for m in seen)
    assert [str(m) for m in seen] == [f"Copying crash logs{NB}· 1 of 2",
                                      f"Copying crash logs{NB}· 2 of 2"]


def test_the_memory_dumps_are_counted(device, clock):
    st, dev = device
    seen = []
    diagnostics.copy_dumps(dev, st, progress=seen.append)
    assert [str(m) for m in seen] == [f"Copying memory dumps{NB}· 1 of 2",
                                      f"Copying memory dumps{NB}· 2 of 2"]


def test_a_dump_over_the_limit_is_not_counted(device, clock, monkeypatch):
    st, dev = device
    monkeypatch.setattr(diagnostics, "LIMIT", 400)  # the newer one fits, the other not
    seen = []
    diagnostics.copy_dumps(dev, st, progress=seen.append)
    assert [str(m) for m in seen] == [f"Copying memory dumps{NB}· 1 of 1"]


def test_the_diagnostic_files_read_are_counted(device, clock):
    st, dev = device
    seen = []
    first = diagnostics.copy_files(dev, st, progress=seen.append)
    assert [str(m) for m in seen] == [f"Copying diagnostic files{NB}· {n} of {first.new}"
                                      for n in range(1, first.new + 1)]
    assert _no_file_names(seen)
    seen.clear()
    diagnostics.copy_files(dev, st, progress=seen.append)  # nothing changed: nothing read
    assert seen == []


def test_diagnostics_progress_in_polish(hiding, clock):
    st, dev, _fake = hiding
    seen = []
    i18n.setup("pl")
    try:
        diagnostics.copy_crash_logs(dev, st, progress=seen.append)
        diagnostics.copy_dumps(dev, st, progress=seen.append)
        diagnostics.copy_files(dev, st, progress=seen.append)
    finally:
        i18n.setup("en")
    heads = list(dict.fromkeys(m.head for m in seen))
    assert heads == ["Kopiowanie dzienników awarii", "Kopiowanie zrzutów pamięci",
                     "Kopiowanie plików diagnostycznych"]
    assert seen[0] == f"Kopiowanie dzienników awarii{NB}· 1 z{NB}2"


def test_the_command_line_tells_each_copy_once(hiding, clock, monkeypatch, capsys):
    """Not on a terminal: a line when a copy begins, never a file."""
    from openexpress import cli

    st, dev, _fake = hiding
    monkeypatch.setattr(cli, "open_device", lambda args: (st, dev))
    assert cli.main(["diagnostics", "--apply", "--crash-logs", "--dumps"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert [line for line in out if line.startswith("  Copying")] == [
        "  Copying diagnostic files", "  Copying memory dumps", "  Copying crash logs"]


def test_the_window_tells_both_copies_on_its_bar(monkeypatch):
    from test_diagnostics import _found, _window

    from openexpress.gui.device_page import copy_crash_logs, copy_dumps

    win = _window(_found())
    told = []

    def copied(device, storage, progress=None):
        told.append(progress)
        return diagnostics.Copied(new=1, saved=True)

    monkeypatch.setattr(diagnostics, "copy_dumps", copied)
    monkeypatch.setattr(diagnostics, "copy_crash_logs", copied)
    copy_dumps(win)
    copy_crash_logs(win)
    assert told == [win.progress, win.progress]
