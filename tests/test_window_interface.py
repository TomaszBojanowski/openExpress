"""The window, MainWindow with its mixins (MapsPage, MusicPage, …), has the methods listed in
tests/data/window_methods.txt: a method lost while moving code, a name defined in two of its
classes (one would hide the other) or a method that hides one of GTK's fails here. The list
names methods, not classes, so a method moved to another mixin keeps its line."""

import inspect
from pathlib import Path

LIST = Path(__file__).parent / "data" / "window_methods.txt"


def _classes():
    """The window's own classes, in the order Python looks them up (MainWindow first)."""
    from openexpress.gui.app import MainWindow

    return [c for c in MainWindow.__mro__ if c.__module__.startswith("openexpress.")]


def _methods(cls) -> set[str]:
    return {name for name, value in vars(cls).items()
            if inspect.isfunction(value) or isinstance(value, (staticmethod, classmethod,
                                                                property))}


def _listed() -> list[str]:
    return [line.strip() for line in LIST.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")]


def test_the_list_is_sorted_and_names_each_method_once():
    listed = _listed()
    assert listed == sorted(set(listed))


def test_the_window_has_the_methods_of_the_list():
    found = set().union(*(_methods(c) for c in _classes()))
    listed = set(_listed())
    lost, new = sorted(listed - found), sorted(found - listed)
    assert (lost, new) == ([], []), (
        f"lost: {lost}; not in {LIST.name}: {new} (add or remove a name there only for a "
        "method added, removed or renamed on purpose)")


def test_no_method_is_defined_in_two_of_the_window_s_classes():
    classes = _classes()
    assert classes[0].__name__ == "MainWindow" and len(classes) >= 2
    twice = {}
    for cls in classes:
        for name in _methods(cls):
            twice.setdefault(name, []).append(cls.__name__)
    assert {n: where for n, where in twice.items() if len(where) > 1} == {}


def test_no_method_of_ours_hides_one_of_gtk():
    """Only MainWindow.__init__ stands in front of GTK's (it calls it); any other name the
    window shares with Adw.ApplicationWindow and its bases would replace GTK's own method."""
    from openexpress.gui.app import MainWindow

    gtk = [c for c in MainWindow.__mro__ if not c.__module__.startswith("openexpress.")]
    ours = set().union(*(_methods(c) for c in _classes()))
    assert sorted(n for n in ours if any(n in vars(c) for c in gtk)) == ["__init__"]
