"""The package (code audit of 25.10.2026): the version is the same in its three places, the
build needs a setuptools that knows the licence as it is written (an SPDX expression and
license-files: setuptools 77 or newer), and PyGObject is what every command needs, so it is a
dependency, and its absence is said in one line, not as a traceback."""

import sys
import tomllib
import xml.etree.ElementTree as ET
from pathlib import Path

import openexpress

ROOT = Path(openexpress.__file__).parent.parent
PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
METAINFO = ROOT / "openexpress" / "data" / "io.github.tomaszbojanowski.OpenExpress.metainfo.xml"


def test_the_version_is_the_same_in_its_three_places():
    newest = ET.parse(METAINFO).getroot().find("releases/release").get("version")
    assert PYPROJECT["project"]["version"] == openexpress.__version__ == newest


def test_the_build_needs_a_setuptools_that_reads_the_licence_as_written():
    assert isinstance(PYPROJECT["project"]["license"], str)  # an SPDX expression (PEP 639)
    (setuptools,) = [r for r in PYPROJECT["build-system"]["requires"]
                     if r.startswith("setuptools")]
    minimum = tuple(int(n) for n in setuptools.split(">=")[1].split("."))
    assert minimum >= (77,)


def test_pygobject_is_a_dependency():
    names = [d.split(">")[0].split("=")[0].strip() for d in PYPROJECT["project"]["dependencies"]]
    assert "PyGObject" in names


def test_the_command_line_without_pygobject_says_what_to_install(monkeypatch, capsys):
    from openexpress.cli import main

    monkeypatch.setitem(sys.modules, "gi", None)  # as if PyGObject were not installed
    assert main(["cache"]) == 1
    err = capsys.readouterr().err
    assert err.startswith("openExpress needs PyGObject with GTK 4 and libadwaita.")
    assert "python3-gobject" in err and "python3-gi" in err and "Traceback" not in err
