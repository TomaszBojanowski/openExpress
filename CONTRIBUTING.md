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
- What it uploaded and its last sync are kept apart from the device's
  (`devices/<Unit ID>/test-drive/` in the data folder), so its first sync starts anew.
- The rest is shared with the device, as it goes by the Unit ID: the device's entry in
  `devices.json` (how it connects, when it was last seen, its USB IDs, what the last check of
  updates found, the updates copied to it and whether it was unplugged since) and its folder of
  backups. The device's tile, its row "Device Software" and its Updates page may then show what
  was tried with the pendrive.
- The log says so in a warning (on the command line also in the terminal); a value in another
  form is ignored with a warning.
- A connected MTP device still comes first, and a drive that is not mounted is not taken.

The ID is that of your own pendrive: run `lsusb` before and after plugging it in, and take the
`ID VVVV:PPPP` of the line that appeared.

```bash
lsusb                  # with the pendrive plugged in: "Bus … Device …: ID VVVV:PPPP <maker> …"
DRIVE=VVVV:PPPP        # the ID of your own pendrive
OPENEXPRESS_TEST_DRIVE_USB=$DRIVE python3 -m openexpress sync    # dry run
```

To keep the device's own records apart, run the tries with XDG folders of their own, with a
copy of the configuration and the sign-in. openExpress keeps all its files in them; outside them
it only reads the Flatpak's (whether it is syncing, its copies of courses). Backups go to the
Documents folder named in `user-dirs.dirs` of that `XDG_CONFIG_HOME` (`~/Documents` without
one). Close any openExpress window first: there is one per session, and starting another only
brings that one up, with its own folders.

```bash
T=~/openexpress-pendrive
mkdir -p "$T/config/openexpress" "$T/Documents" && chmod 700 "$T"
cp -p ~/.config/openexpress/*.json "$T/config/openexpress/"
echo "XDG_DOCUMENTS_DIR=\"$T/Documents\"" > "$T/config/user-dirs.dirs"
XDG_CONFIG_HOME="$T/config" XDG_DATA_HOME="$T/data" XDG_STATE_HOME="$T/state" \
  XDG_CACHE_HOME="$T/cache" OPENEXPRESS_TEST_DRIVE_USB=$DRIVE python3 -m openexpress gui
```


## Adding a translation

What to translate and how, trying it in the window and sending it:
[Translating openExpress](docs/translating.md).

The template `po/openexpress.pot` is made from the files `po/POTFILES` lists, the metainfo's
template among them, by `tools/update-template.sh`; it is committed with the code that changes
the texts. The compiled catalogs and the translated metainfo are built at the build
(`python3 -m openexpress.catalogs`), never committed.
