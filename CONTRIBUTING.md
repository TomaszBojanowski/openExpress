# Contributing

## Development
```bash
python3 -m pytest
```

The tests need Python 3.11+, `pytest`, `requests` and PyGObject 3.46+ with GTK 4 and
libadwaita (even without a display, because the window modules are imported); the tests with
a display also need WebKitGTK 6.0. They start their own hidden X server (Xvfb:
`xorg-x11-server-Xvfb` on Fedora, `xvfb` on Debian/Ubuntu), so no window shows on the desktop
and `xvfb-run` is not needed; without Xvfb they use the display of the session.
`OPENEXPRESS_TESTS_NO_DISPLAY=1` runs them without any display (the tests that need one are
skipped), `OPENEXPRESS_TESTS_ON_SCREEN=1` on the display of the session.
`OPENEXPRESS_TESTS_STRESS=1` also runs the long stress tests (a short version of each is in
the ordinary run). Some tests are skipped
quietly when something is missing: the translation tests without gettext, the PTP test without
libusb-1.0 and the window-width tests without the Adwaita Sans font (the widths are measured in
it). The tests never reach the network (one that tries fails) and need no device.


## Adding a translation

Translations use GNU gettext (`xgettext`, `msginit`, `msgmerge`, `msgattrib`, `msgfmt` from the
`gettext` package).

```bash
tools/update-translations.sh de      # creates po/de.po from the template
# translate po/de.po (e.g. with Poedit, GNOME Translation Editor or a text editor)
tools/update-translations.sh         # updates all languages and compiles .mo files
python3 -m pytest tests/test_i18n.py # checks placeholders and compiled catalogs
```

Keep `{placeholders}` unchanged and fill all plural forms. Add the language name to
`LANGUAGE_NAMES` in `openexpress/i18n.py`.
