"""The XDG base folders of the program (paths.xdg): the variable when it names a folder, else
the usual folder below the home folder; an empty variable counts as not set."""

import pytest

from openexpress import paths


@pytest.mark.parametrize("value", [None, ""], ids=["not set", "empty"])
def test_without_a_folder_in_the_variable_the_usual_one(monkeypatch, tmp_path, value):
    monkeypatch.setenv("HOME", str(tmp_path))
    if value is None:
        monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    else:
        monkeypatch.setenv("XDG_DATA_HOME", value)
    assert paths.xdg("XDG_DATA_HOME", ".local/share") == tmp_path / ".local/share"
    assert paths.data_dir() == tmp_path / ".local/share/openexpress"


def test_the_folder_of_the_variable(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    assert paths.xdg("XDG_DATA_HOME", ".local/share") == tmp_path / "data"
    assert paths.data_dir() == tmp_path / "data/openexpress"
