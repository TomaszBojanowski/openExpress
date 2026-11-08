# Contributing

## Development
```bash
python3 -m pytest
```

What the tests need:

- Python 3.11+, `pytest`, `requests` and PyGObject 3.46+ with GTK 4 and libadwaita, even without
  a display, because the window modules are imported; the tests with a display also need
  WebKitGTK 6.0.
- GNU gettext (`msgfmt`): the tests build the translations for themselves.
- Optional: without libusb-1.0 the PTP test is skipped quietly, and without the Adwaita Sans font
  so are the window-width tests (the widths are measured in it).

Where they run:

- They start their own hidden X server (Xvfb: `xorg-x11-server-Xvfb` on Fedora, `xvfb` on
  Debian/Ubuntu), so no window shows on the desktop and `xvfb-run` is not needed; without Xvfb
  they use the display of the session.
- `OPENEXPRESS_TESTS_NO_DISPLAY=1` runs them without any display; the tests that need one are
  skipped.
- `OPENEXPRESS_TESTS_ON_SCREEN=1` runs them on the display of the session.
- `OPENEXPRESS_TESTS_STRESS=1` also runs the long stress tests (a short version of each is in
  the ordinary run).
- They never reach the network (a test that tries fails) and need no device.

## Testing USB-drive sync with a pendrive

For testing only, not for ordinary use. `OPENEXPRESS_TEST_DRIVE_USB=VVVV:PPPP` (the vendor
and product ID that `lsusb` shows) makes openExpress take the mounted USB drive with these IDs
for a Garmin device connected as a USB drive:

- A pendrive with a copy of a device's `GARMIN/GarminDevice.xml` stands in for that device: the
  same Unit ID and account, so `--apply` uploads to Garmin Connect as from that device.
- Its sync leaves Garmin Connect's queue and TrueUp to the device: nothing is downloaded or
  acknowledged, and the sync says so in one line.
- Its records are its own: what it uploaded and its last sync are kept apart from the device's
  (`devices/<Unit ID>/test-drive/` in the data folder), so its first sync starts anew and the
  device's tile and records stay as they were.
- The log says so in a warning (on the command line also in the terminal); a value in another
  form is ignored with a warning.
- A connected MTP device still comes first, and a drive that is not mounted is not taken.

```bash
OPENEXPRESS_TEST_DRIVE_USB=0781:5581 python3 -m openexpress sync    # dry run
```


## Adding a translation

What to translate and how, trying it in the window and sending it:
[Translating openExpress](docs/translating.md).

The template `po/openexpress.pot` is made from the files `po/POTFILES` lists, the metainfo's
template among them, by `tools/update-template.sh`; it is committed with the code that changes
the texts. The compiled catalogs and the translated metainfo are built at the build
(`python3 -m openexpress.catalogs`), never committed.
