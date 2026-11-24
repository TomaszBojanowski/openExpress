"""openexpress setup with a key and a secret."""

import pytest

from openexpress.cli import main


@pytest.mark.parametrize("argv", [["setup", "--key", "abc"], ["setup", "--secret", "xyz"]])
def test_setup_key_requires_secret(xdg, capsys, argv):
    with pytest.raises(SystemExit) as e:
        main(argv)
    assert e.value.code == 2
    assert "--key and --secret" in capsys.readouterr().err


def test_setup_does_not_show_any_of_the_key(xdg, capsys):
    """The consumer key comes from the user's own installer: not even its start is printed."""
    assert main(["setup", "--key", "deadbeef-made-up-key", "--secret", "made-up-secret"]) == 0
    said = capsys.readouterr()
    assert "Saved the application key in " in said.out
    assert "deadbeef" not in said.out + said.err and "secret" not in said.out + said.err
