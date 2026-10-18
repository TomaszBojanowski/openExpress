"""Which Flatpak this is: from the openExpress repository or from a bundle, and its commit -
for the device report and the start line of the log. Read from /.flatpak-info and, only in
the Flatpak, from the host's `flatpak info --show-origin`. All values here are made up."""

import subprocess
import types

import pytest

from openexpress import applog, paths

COMMIT = "bd8b1bc1f17fb3f7b2bb0af0d1680562aed420c531cd5e09a95bf8e1533455c7"
INFO = f"""[Application]
name=io.github.tomaszbojanowski.OpenExpress
runtime=runtime/org.gnome.Platform/x86_64/50

[Instance]
instance-id=1234567890
branch=master
arch=x86_64
app-commit={COMMIT}
flatpak-version=1.18.4
"""


@pytest.fixture
def info_file(tmp_path, monkeypatch):
    path = tmp_path / "flatpak-info"
    monkeypatch.setattr(paths, "FLATPAK_INFO", path, raising=False)
    return path


def test_the_branch_and_commit_come_from_flatpak_info(info_file):
    info_file.write_text(INFO)
    assert paths.flatpak_build() == ("master", COMMIT[:12])


@pytest.mark.parametrize("text", [None, "", "not an ini file\n[[[", "[Application]\nname=x\n"])
def test_no_build_without_a_readable_instance(info_file, text):
    if text is not None:
        info_file.write_text(text)
    assert paths.flatpak_build() is None


def test_a_build_without_a_commit(info_file):
    info_file.write_text("[Instance]\nbranch=master\n")
    assert paths.flatpak_build() == ("master", None)


def _host(monkeypatch, answer):
    """flatpak-spawn on the host stands in: records the command, answers or raises."""
    calls = []

    def run(command, **kw):
        calls.append((command, kw.get("timeout")))
        if isinstance(answer, BaseException):
            raise answer
        code, out = answer
        return types.SimpleNamespace(returncode=code, stdout=out, stderr="")

    monkeypatch.setattr(paths.subprocess, "run", run)
    return calls


@pytest.mark.parametrize("answer, origin", [
    ((0, "openexpress\n"), "openexpress"),
    ((0, "openexpress1-origin\n"), "openexpress1-origin"),
    ((1, ""), None),
    ((0, "  \n"), None),
    (FileNotFoundError("flatpak-spawn"), None),
    (subprocess.TimeoutExpired("flatpak-spawn", 3), None),
    (OSError("no"), None),
])
def test_the_origin_is_asked_of_the_host(monkeypatch, answer, origin):
    monkeypatch.setattr(paths, "in_flatpak", lambda: True)
    calls = _host(monkeypatch, answer)
    assert paths.flatpak_origin() == origin
    assert calls == [(["flatpak-spawn", "--host", "flatpak", "info", "--show-origin",
                       "io.github.tomaszbojanowski.OpenExpress"], 3)]


def test_the_origin_is_asked_for_this_branch(monkeypatch, info_file):
    """With two branches installed (master and test) a question without the branch is
    ambiguous and fails: the branch from /.flatpak-info goes with the app ID."""
    info_file.write_text(INFO.replace("branch=master", "branch=test"))
    monkeypatch.setattr(paths, "in_flatpak", lambda: True)
    calls = _host(monkeypatch, (0, "openexpress-test\n"))
    assert paths.flatpak_origin() == "openexpress-test"
    assert calls == [(["flatpak-spawn", "--host", "flatpak", "info", "--show-origin",
                       "io.github.tomaszbojanowski.OpenExpress//test"], 3)]


@pytest.mark.parametrize("text", [None, "[Instance]\napp-commit=abc\n", "not an ini file\n[[["])
def test_without_a_readable_branch_the_origin_is_asked_without_one(monkeypatch, info_file,
                                                                   text):
    if text is not None:
        info_file.write_text(text)
    monkeypatch.setattr(paths, "in_flatpak", lambda: True)
    calls = _host(monkeypatch, (0, "openexpress\n"))
    assert paths.flatpak_origin() == "openexpress"
    assert calls[0][0][-1] == "io.github.tomaszbojanowski.OpenExpress"


def test_the_origin_is_never_asked_outside_the_flatpak(monkeypatch):
    monkeypatch.setattr(paths, "in_flatpak", lambda: False)
    calls = _host(monkeypatch, (0, "openexpress\n"))
    assert paths.flatpak_origin() is None and calls == []


# --- what the report and the log say -----------------------------------------------------------

def _report_line(monkeypatch, device_root, origin, build):
    from openexpress import report
    from openexpress.device.storage import LocalStorage
    from openexpress.device.xmldevice import parse

    monkeypatch.setattr(paths, "in_flatpak", lambda: True)
    monkeypatch.setattr(paths, "flatpak_origin", lambda: origin, raising=False)
    monkeypatch.setattr(paths, "flatpak_build", lambda: build, raising=False)
    st = LocalStorage(device_root)
    text = report.build(parse(st.read("GARMIN/GarminDevice.xml")), st)
    (line,) = [line for line in text.splitlines() if line.startswith("- openExpress: ")]
    return line


@pytest.mark.parametrize("origin, build, words", [
    ("openexpress", ("master", "bd8b1bc1f17f"),
     'Flatpak from the "openexpress" repository, commit bd8b1bc1f17f'),
    ("openexpress1-origin", ("master", "bd8b1bc1f17f"), "Flatpak bundle, commit bd8b1bc1f17f"),
    (None, ("master", "bd8b1bc1f17f"), "Flatpak, origin unknown, commit bd8b1bc1f17f"),
    ("openexpress", ("master", None), 'Flatpak from the "openexpress" repository'),
    ("openexpress1-origin", None, "Flatpak bundle"),
])
def test_the_report_says_where_the_flatpak_came_from(monkeypatch, device_root, origin, build,
                                                     words):
    from openexpress import __version__

    assert _report_line(monkeypatch, device_root, origin, build) == (
        f"- openExpress: {__version__} ({words})")


def test_outside_the_flatpak_the_report_is_as_before(monkeypatch, device_root):
    from openexpress import __version__, report
    from openexpress.device.storage import LocalStorage
    from openexpress.device.xmldevice import parse

    monkeypatch.setattr(paths, "in_flatpak", lambda: False)
    monkeypatch.setattr(paths, "flatpak_origin",
                        lambda: pytest.fail("asked outside the Flatpak"), raising=False)
    st = LocalStorage(device_root)
    text = report.build(parse(st.read("GARMIN/GarminDevice.xml")), st)
    assert f"- openExpress: {__version__} (source)" in text.splitlines()


@pytest.mark.parametrize("origin, build, words", [
    ("openexpress", ("master", "ab12345678cd"), "Flatpak/openexpress ab12_3456_78cd"),
    ("openexpress1-origin", ("master", "bd8b1bc1f17f"), "Flatpak/openexpress1-origin bd8b_1bc1_f17f"),
    (None, None, "Flatpak/unknown"),
])
def test_the_start_line_tells_the_flatpak_and_survives_the_filter(monkeypatch, origin, build,
                                                                  words):
    """The commit in groups of four: never seven digits in a row for the filter to hide."""
    monkeypatch.setattr(paths, "in_flatpak", lambda: True)
    monkeypatch.setattr(paths, "flatpak_origin", lambda: origin, raising=False)
    monkeypatch.setattr(paths, "flatpak_build", lambda: build, raising=False)
    line = applog.environment()
    assert f"openExpress {applog.__version__}, {words}, Python " in line
    assert applog.redact(line) == line
