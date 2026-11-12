"""The names the help of the command line gives what a command takes, as docs/cli.md writes
them: an argument in capitals, a folder as DIR, an option's value by the name the table of
commands gives it; and a line of help for every command, argument and option. Every parser of
the program is looked at, those of commands added later too."""

import argparse
import re
from pathlib import Path

from openexpress import cli

DOCS = Path(__file__).resolve().parent.parent / "docs" / "cli.md"
# The help of an option that takes a folder starts so ("folder of the backups", "a folder with
# a copy…", "destination directory…").
FOLDER = re.compile(r"(a |the )?(destination )?(folder|directory)\b")
# Values docs/cli.md gives as an example, not as a name.
EXAMPLES = {"FIT_TYPE_4"}


def parsers(parser, words=()):
    """Every parser under `parser`, with the words of its command."""
    yield words, parser
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for name, sub in action.choices.items():
                yield from parsers(sub, (*words, name))


def taking(parser):
    """The arguments and options of `parser` that take a value."""
    return [a for a in parser._actions if a.nargs != 0
            and not isinstance(a, argparse._SubParsersAction | argparse._HelpAction)]


def shown(action) -> str:
    """The name the help gives what `action` takes."""
    if action.metavar:
        return action.metavar
    if action.choices:
        return "{" + ",".join(action.choices) + "}"
    return action.dest.upper() if action.option_strings else action.dest


def test_every_argument_is_named_in_capitals():
    wrong = [f"{' '.join(words)} {shown(a)}" for words, p in parsers(cli.build_parser())
             for a in taking(p) if not a.option_strings and shown(a) != shown(a).upper()]
    assert wrong == []


def test_every_option_that_takes_a_folder_shows_dir():
    wrong = [f"{' '.join(words)} {a.option_strings[0]} {shown(a)}"
             for words, p in parsers(cli.build_parser()) for a in taking(p)
             if a.option_strings and FOLDER.match((a.help or "").casefold())
             and shown(a) != "DIR"]
    assert wrong == []


def described(action) -> bool:
    return bool((action.help or "").strip())


def test_every_command_argument_and_option_has_a_help_text():
    # argparse gives -h and --version their help itself.
    missing = []
    for words, p in parsers(cli.build_parser()):
        for a in p._actions:
            if isinstance(a, argparse._HelpAction | argparse._VersionAction):
                continue
            if isinstance(a, argparse._SubParsersAction):
                missing += [f"{' '.join((*words, c.dest))} (command)"
                            for c in a._choices_actions if not described(c)]
            elif not described(a):
                name = a.option_strings[-1] if a.option_strings else shown(a)
                missing.append(" ".join((*words, name)))
    assert not missing, f"{len(missing)} without a help text:\n" + "\n".join(missing)


def documented():
    """(the words of the command, an option, the name of its value) as docs/cli.md writes them
    in capitals: in a row of its table of commands, for the command of that row; elsewhere, an
    option before the command (words: none)."""
    for line in DOCS.read_text(encoding="utf-8").splitlines():
        spans = [span.replace("\\|", "|") for span in re.findall(r"`([^`]+)`", line)]
        if line.startswith("| `openexpress "):
            words = []
            for word in spans[0].split()[1:]:
                if not re.fullmatch(r"[a-z][a-z-]*(\|[a-z][a-z-]*)*", word):
                    break
                words.append(word.split("|"))
        else:
            words = None  # only an option before the command
        for span in spans:
            for option, value in re.findall(r"(--[a-z][a-z-]*) ([A-Z][A-Za-z0-9_.]*)", span):
                for command in _each(words or []):
                    yield command, option, value, words is None


def _each(words):
    """Each command of words such as [["apps"], ["enable", "disable"]]."""
    if not words:
        yield ()
        return
    for first in words[0]:
        for rest in _each(words[1:]):
            yield (first, *rest)


def test_every_list_of_choices_is_that_of_the_program():
    """`--option a|b|c` in docs/cli.md lists the choices the program takes, all of them (a
    language added to the program and not to the table is found)."""
    found = dict(parsers(cli.build_parser()))
    wrong = []
    for line in DOCS.read_text(encoding="utf-8").splitlines():
        if not line.startswith("| `openexpress "):
            continue
        spans = [span.replace("\\|", "|") for span in re.findall(r"`([^`]+)`", line)]
        words = []
        for word in spans[0].split()[1:]:
            if not re.fullmatch(r"[a-z][a-z-]*(\|[a-z][a-z-]*)*", word):
                break
            words.append(word.split("|"))
        for span in spans:
            for option, choices in re.findall(r"(--[a-z][a-z-]*) ([a-z0-9]+(?:\|[a-z0-9]+)+)",
                                              span):
                for command in _each(words):
                    action = next(a for a in found[command]._actions
                                  if option in a.option_strings)
                    if sorted(choices.split("|")) != sorted(action.choices or ()):
                        wrong.append(f"{' '.join(command)} {option}: {sorted(action.choices or ())}"
                                     f", docs/cli.md {choices}")
    assert wrong == []


def test_every_name_of_a_value_is_that_of_the_documentation():
    found = dict(parsers(cli.build_parser()))
    wrong = []
    for command, option, value, before_the_command in documented():
        parser = found.get(command)
        assert parser is not None, f"docs/cli.md names a command the program has not: {command}"
        action = next((a for a in parser._actions if option in a.option_strings), None)
        if action is None and before_the_command:
            continue  # an option of some command, named in the text
        assert action is not None, f"docs/cli.md names an option {command} has not: {option}"
        if action.nargs == 0 or value in EXAMPLES:
            continue  # a flag: the name after it is an argument of the command
        if shown(action) != value:
            wrong.append(f"{' '.join(command)} {option}: {shown(action)}, docs/cli.md {value}")
    assert wrong == []
