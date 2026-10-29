"""The window, MainWindow with its mixins (MapsPage, MusicPage, …): a name defined in two of its
classes (one would hide the other) or a method that hides one of GTK's fails here. What the
window has besides its methods, its attributes, is in tests/test_window_state.py."""

import inspect


def _classes():
    """The window's own classes, in the order Python looks them up (MainWindow first)."""
    from openexpress.gui.app import MainWindow

    return [c for c in MainWindow.__mro__ if c.__module__.startswith("openexpress.")]


def _methods(cls) -> set[str]:
    return {name for name, value in vars(cls).items()
            if inspect.isfunction(value) or isinstance(value, (staticmethod, classmethod,
                                                                property))}


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
