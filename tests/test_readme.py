"""The documents and the help of the command line say what the program has: the command table
(docs/cli.md) names every command and the options of sync, install-desktop, devices, report and
cache; the help of `cache` names its parts, that of `--path` is not for a drive; Development
(CONTRIBUTING.md) names the variables of the tests, and the Flatpak permissions
(docs/privacy.md) every folder the manifest opens."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
README = (ROOT / "README.md").read_text(encoding="utf-8")
MANIFEST = (ROOT / "packaging" / "flatpak"
            / "io.github.tomaszbojanowski.OpenExpress.yml").read_text(encoding="utf-8")


def _section(title: str, document: str = "README.md") -> str:
    """The text of a section of a document, up to the next heading of any level."""
    text = "\n" + (ROOT / document).read_text(encoding="utf-8")
    start = text.index(f"\n{title}\n")
    end = text.find("\n#", start + len(title) + 2)
    return text[start:end if end != -1 else None]


def _commands() -> dict:
    from openexpress import cli

    parser = cli.build_parser()
    sub = next(a for a in parser._actions if a.__class__.__name__ == "_SubParsersAction")
    return dict(sub.choices)


def test_the_command_table_names_every_command():
    table = _section("# Command line", "docs/cli.md")
    missing = [name for name in _commands()
               if not re.search(rf"`(openexpress )?{re.escape(name)}\b", table)]
    assert missing == []


def test_the_command_table_names_the_main_options():
    table = _section("# Command line", "docs/cli.md")
    wanted = {"sync": ["--all", "--upload-only", "--download-only", "--type",
                       "--include-diagnostics"],
              "install-desktop": ["--remove"], "devices": ["--forget"], "report": ["--log"],
              "cache": ["--clear", "--only"]}
    commands = _commands()
    missing = []
    for name, options in wanted.items():
        known = {o for a in commands[name]._actions for o in a.option_strings}
        rows = [line for line in table.splitlines() if f"openexpress {name}" in line]
        for option in options:
            assert option in known  # the test asks only for what the command has
            if not any(option in row for row in rows):
                missing.append(f"{name} {option}")
    assert missing == []


def _help_of(option: str) -> str:
    from openexpress import cli

    return next(a.help for a in cli.build_parser()._actions if option in a.option_strings)


def test_the_help_of_cache_names_every_part():
    from openexpress import cli
    from openexpress.cache import CATEGORIES

    parser = cli.build_parser()
    sub = next(a for a in parser._actions if a.__class__.__name__ == "_SubParsersAction")
    line = next(c.help for c in sub._choices_actions if c.dest == "cache")
    assert [part for part in CATEGORIES if part not in line] == []


def test_the_help_of_path_is_for_a_folder_not_for_a_drive():
    words = _help_of("--path")
    assert "copy" in words
    # A device in mass storage mode is found by the program itself.
    assert "UMS" not in words and "mass storage" not in words


def test_the_development_section_names_the_variables_of_the_tests():
    variables = set(re.findall(r"OPENEXPRESS_TESTS_[A-Z_]+",
                               (ROOT / "tests" / "conftest.py").read_text(encoding="utf-8")))
    assert variables  # the tests have such variables at all
    development = _section("## Development", "CONTRIBUTING.md")
    assert sorted(v for v in variables if v not in development) == []
    assert "Xvfb" in development


def test_the_flatpak_permissions_name_every_folder_of_the_manifest():
    folders = re.findall(r"^\s*- --filesystem=(\S+)", MANIFEST, flags=re.MULTILINE)
    assert "xdg-documents/Garmin:create" in folders
    permissions = _section("## Flatpak permissions", "docs/privacy.md")
    assert [f for f in folders if f"`{f}`" not in permissions
            and f"`--filesystem={f}`" not in permissions] == []
