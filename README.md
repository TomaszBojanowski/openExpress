<p align="center">
  <img src="openexpress/data/io.github.tomaszbojanowski.OpenExpress.svg" width="128" alt="openExpress icon">
</p>

<h1 align="center">openExpress</h1>

<p align="center">
  An unofficial Linux app for Garmin watches: sync with Garmin Connect, software updates,
  maps, Connect IQ apps and music over USB.
</p>

> **openExpress is not affiliated with, endorsed by or supported by Garmin Ltd.**
> Garmin, fēnix, Garmin Connect and Garmin Express are trademarks of Garmin Ltd. or its
> subsidiaries. openExpress talks to the same Garmin services as the official Windows/macOS
> app; Garmin may change them at any time. Use at your own risk.

<p align="center">
  <img src="docs/screenshots/maps.png" width="32%" alt="Maps">
  <img src="docs/screenshots/apps.png" width="32%" alt="Connect IQ apps">
  <img src="docs/screenshots/music.png" width="32%" alt="Music">
</p>

Garmin Express exists only for Windows and macOS. openExpress is an independent
implementation with its own code, so Linux users can manage their watch without a
virtual machine. How the Garmin services and the watch's file layout work was learned by
analysing the official application, solely to make openExpress interoperate with them.
It contains no Garmin code or data: the few values it needs to talk to
Garmin's servers are read from **your own copy** of the official installer during setup.

**Status:** alpha. Developed and tested with a **fēnix 8 Solar** on Fedora/GNOME. Other
Garmin watches that connect over USB (MTP) should work, but are untested; reports are welcome.

**Highlights**

- Garmin Connect sync: new activities, sleep, HRV, metrics… are uploaded; courses, workouts
  and Connect IQ apps queued in Connect are written to the watch; TrueUp for multiple devices.
- Watch software updates and free maps (TopoActive, CourseView, Ski…), with MD5 verification.
- Connect IQ apps: install, update, remove, enable/disable built-in activities – also on
  watches that hide their app folder (e.g. the fēnix 8), without reordering your widgets.
- Music, podcasts and audiobooks with playlists.
- GTK 4 / libadwaita window, command-line tool and background sync with notifications.
- No telemetry. English and Polish, translatable with gettext.

## What works

| Command | Description |
|---|---|
| `openexpress info` | model, software version, free space |
| `openexpress files [--type FIT_TYPE_4]` | files on the watch by data type from `GarminDevice.xml` |
| `openexpress setup --from-installer GarminExpress.exe` | one-time setup from your own copy of the official installer |
| `openexpress login [--manual]` | sign in on the Garmin SSO page (WebKitGTK window) |
| `openexpress whoami [--check]`, `logout` | account, log out (removes the local token) |
| `openexpress upload-config` | what data is uploaded for this watch and where (Garmin server) |
| `openexpress sync [--apply]` | uploads new FIT files to Connect, courses and workouts from Connect to the watch |
| `openexpress baseline [--apply]` | marks current files as already uploaded (first run) |
| `openexpress backup [--apply]` | local backup of watch data (default `~/Documents/Garmin/Backups`, last 3 kept) |
| `openexpress updates [--reinstalls]` | watch software updates (no login needed) |
| `openexpress updates --install PART… [--apply]` | download with MD5 check and copy to the watch; then restart the watch |
| `openexpress maps [--all]` | installed and available maps (TopoActive, base maps, CourseView, Ski) |
| `openexpress maps install NAME [--apply]` | install a map assigned to the watch (download, activation, write) |
| `openexpress maps update [--apply]`, `maps remove NAME [--apply]` | update and remove maps |
| `openexpress apps [--builtin]` | Connect IQ apps on the watch and in your Garmin account, available updates |
| `openexpress apps install/update/remove/enable/disable … [--apply]` | install, update, remove, enable or disable apps and built-in activities |
| `openexpress apps order [--from COPY] [--apply]` | put apps and widgets back in the order of a saved copy of the app list |
| `openexpress primary [set --apply]` | show the account's primary training device, or make the connected device the primary one |
| `openexpress music [add PATH… \| playlist FILE.m3u \| remove TEXT…] [--apply]` | music, podcasts and audiobooks (Artist/Album layout, `.m3u8` playlists) |
| `openexpress gui` | application window (GTK 4 + libadwaita): watch, sync, updates, maps, apps, music |
| `openexpress watch` | background service: sync when the watch is connected, with a notification |
| `openexpress install-desktop [--apply]` | application menu entry and systemd user service |

All commands that change anything run **in dry-run mode by default**:
they show the plan and do nothing without `--apply`.

A Garmin device needs a moment to start after it is plugged in. Commands wait for it
(up to 45 seconds) and the window shows that the device is starting; `--no-wait` makes a
command fail at once instead.

While a sync, a backup or a write is running, the window shows **Do not unplug the device**
and the command-line tool prints the same warning when it starts such an operation.

## Supported devices

| Device | Status |
|---|---|
| fēnix 8 (47 mm, Solar) | tested by the developer: sign-in, sync (upload, TrueUp), software update download, map install, copying music and playlists, Connect IQ apps in the version from source (install, remove, restoring the order; command line and window). **Not yet tested on the watch:** updating a Connect IQ app, installing a software update |
| epix (part 006-B3943) | read-only commands and backup tested |
| Edge 1050 | detected automatically; read-only commands, backup and sync plan tested |
| Other Garmin watches and bike computers connecting over USB (MTP) | expected to work, **untested** |

Tried openExpress with another device? Please send a
[device report](https://github.com/TomaszBojanowski/openExpress/issues/new?template=device_report.yml) –
also when everything works. In the window: main menu (☰) → **Report a Device or Problem…**,
or run `openexpress report`. The report contains no serial number, unit ID or e-mail.

## Install

### Flatpak (recommended)

Download `openexpress-<version>.flatpak` from the
[latest release](https://github.com/TomaszBojanowski/openExpress/releases/latest) and run:

```bash
flatpak install --user openexpress-*.flatpak   # needs the Flathub remote for the GNOME runtime
flatpak run io.github.tomaszbojanowski.OpenExpress
```

openExpress then appears in your application menu. Click **Choose Installer…** in the banner
at the top and select the Garmin Express installer, then **Log In**. Turn on automatic sync on
the Watch page (or in Preferences) to sync whenever the watch is connected.
The command-line tool is available as
`flatpak run io.github.tomaszbojanowski.OpenExpress <command>` (for example `sync` or `maps`).

**About the Flatpak's permissions:** besides network, USB storage access through GNOME's
gvfs and your Downloads/Music folders, the Flatpak can run commands on the host
(`org.freedesktop.Flatpak`). It uses this only to copy files to the watch with `gio copy`,
because gvfs does not let sandboxed apps write to MTP devices. This weakens the sandbox; if
you prefer not to grant it, use the version from source (writing to the watch then works
without it).

**Limitation of the Flatpak:** it has no direct access to USB devices. On devices that hide
their app folder (e.g. the fēnix 8) it therefore cannot install, update or remove Connect IQ
apps itself: installs and updates are only queued in Garmin Connect, from where the phone or
Garmin Express installs them, and apps are removed on the phone. Nothing is written to the
device in that case. The version from source has no such limit.

Build the Flatpak yourself:

```bash
flatpak install --user flathub org.gnome.Sdk//50
flatpak-builder --user --install --force-clean build-dir packaging/flatpak/io.github.tomaszbojanowski.OpenExpress.yml
```

### From source

## Requirements

- Linux with Python 3.11+, `requests`, PyGObject with GTK 4, libadwaita, WebKitGTK 6.0
  (login window) and GIO/gvfs (MTP). Optional: `mutagen` (music tags), `7z` (setup),
  `libusb-1.0` (Connect IQ apps on devices that hide their app folder; usually installed).
  - Fedora: `sudo dnf install python3-requests python3-gobject gtk4 libadwaita webkitgtk6.0 gvfs-mtp python3-mutagen p7zip-plugins libusb1`
  - Debian/Ubuntu: `sudo apt install python3-requests python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 gir1.2-webkit-6.0 gvfs-backends python3-mutagen 7zip libusb-1.0-0`
- Watch connected with a USB cable. GNOME (gvfs-mtp) mounts it automatically, and
  openexpress uses the same connection, so the file manager can stay open.
- The official Garmin Express installer for Windows (`GarminExpress.exe`, free download from
  garmin.com), used once during setup. It is not installed or run.

## First run

```bash
git clone https://github.com/TomaszBojanowski/openExpress.git
cd openExpress
pip install --user .                 # or run without installing: python3 -m openexpress …

# 1. One-time setup from the official installer (downloaded from garmin.com)
openexpress setup --from-installer ~/Downloads/GarminExpress.exe

# 2. Log in
openexpress login                    # window with the official Garmin sign-in page
openexpress whoami --check

# 3. Preview and first sync
openexpress sync                     # plan only
openexpress baseline --apply         # if the watch also syncs via your phone
openexpress sync --apply
```

The easiest way is the window: `openexpress gui`, then click **Choose Installer…** in the
banner at the top and **Log In**.

If the login window does not capture the ticket, use `openexpress login --manual`: you
sign in using a regular browser and paste the final URL containing `ticket=ST-…`.

## Application window and background sync

```bash
openexpress gui                       # application window
openexpress install-desktop --apply   # "openExpress" menu entry and background service
systemctl --user daemon-reload
systemctl --user enable --now openexpress-watch.service
```

The background service syncs the watch every time it is connected and shows a notification.
It does nothing until you log in and perform the first sync
(in the application window or with `openexpress baseline`/`sync`). The window and the service
never sync at the same time (shared lock). A watch that is not registered to the signed-in
account only gets a notification.

## Connect IQ apps

- Some watches (e.g. the fēnix 8) keep their app folder out of the ordinary file listing.
  On such a device `apps install`, `apps update`, `apps remove` and the buttons in the window
  talk to it over a direct USB connection; the device disappears from the file manager for a
  moment and is mounted again afterwards. The device finishes the change after it is
  unplugged; until then the app is shown as waiting. A sync never does this: it leaves such
  apps in the queue. Not available in the Flatpak (see above).
- The apps already on the device keep their order and their on/off state: the app list is
  written back exactly as the device reported it, with only the requested change.
- Before every change to the apps (install, update, remove, enable/disable, order) a copy of
  the device's app list is saved next to the backups, in a folder named `GARMIN-APPS <date>`.
  Nothing is changed if the copy cannot be made.
- `openexpress apps order` (in the window: **Restore the order from a copy** on the Apps
  page) lists these copies and puts the apps and widgets back in the order of the chosen
  one, for example after another program rearranged the widgets. It only reorders: the same
  apps, the same settings.
- An app that already waits in Garmin's queue for the device is not requested a second time.

## Log file

openExpress keeps a log of what it does, to make problems easier to find:

- **Where:** `~/.local/state/openexpress/openexpress.log`; in the Flatpak
  `~/.var/app/io.github.tomaszbojanowski.OpenExpress/.local/state/openexpress/openexpress.log`.
  The command-line tool, the window and the background sync write to the same file. At most
  three files of about 1 MB are kept; the file is readable only by you.
- **What it contains:** which command or sync ran and how it ended, the steps of a sync
  (numbers of files by type, not their contents), app installs and removals, how the device
  was detected, requests to Garmin's servers as method, address without parameters, status
  and duration, and error messages.
- **What it never contains:** serial numbers, unit IDs, MTP addresses, tokens, passwords,
  cookies, e-mail addresses, local paths, names of your music files, or file contents.
  Every line passes a filter that replaces such values before it is written. The log
  stays on your computer; nothing is sent anywhere.
- **More or less:** `OPENEXPRESS_LOG=debug` (or `-v`) writes more detail,
  `OPENEXPRESS_LOG=off` writes no file.
- **In a bug report:** `openexpress report` says where the log is; `openexpress report --log`
  appends its last 200 lines, filtered once more. In the window, tick **Attach the last log**
  in *Report a Device or Problem…*; the preview shows exactly what will be copied. Read it
  before you send it.

## Privacy and security

- The password is entered only on Garmin's page. An OAuth1 token is stored locally
  (`~/.config/openexpress/connect_auth.json`, permissions 0600).
- **No telemetry.** Unlike Garmin Express, the program sends nothing to
  Microsoft Application Insights. Watch diagnostics (error logs, KPIs, memory
  dumps, behaviour log) are not uploaded without `--include-diagnostics`.
- Sync **never deletes files from the watch**. Files are deleted only by
  `maps update/remove` (old map files indicated by the server), always after showing
  the list in dry-run mode. Files shared by several maps (e.g. the Europe terrain model) are
  protected.
- Software updates and maps: downloaded files are verified (size, MD5);
  they are copied to the watch only after all files have been downloaded. The RGN format
  (older devices) is rejected.
- Upload works as in Express: activities that Connect already has (e.g. from the phone) are
  detected and skipped; duplicates are rejected by the server.
- Nothing is uploaded unless the Garmin account has given the consent to upload data from
  devices and has device sync turned on; both are checked before every sync. Files from
  Garmin Connect are still written to the device. Upload locations that need a further
  consent are used only when that consent is given.
- The `Garmin-Client-*` headers use the same values as Garmin Express (the server returns
  the configuration based on them). They can be changed in `~/.config/openexpress/settings.json`.

## Not yet available (next stages)

- Connect IQ app settings (the settings form is rendered by apps.garmin.com).
- Checking the privacy-policy consents before a sync (the upload consent is checked, see above).
- Connect IQ apps over a direct USB connection in the Flatpak.

## Upgrading from 0.3.x

- `sync --include-consent` was removed. Whether data may be uploaded is now read from the
  Garmin account before every sync (see *Privacy and security*); upload locations that need
  a consent are used exactly when the account has given it.

## Sync details

- The watch must be registered to the Garmin account you are signed in to. Before every sync
  and every Connect IQ operation the account's device list is fetched from Garmin Connect;
  if the watch is not on it, or the list cannot be fetched, nothing is uploaded and nothing
  is written to the watch. `openexpress baseline` therefore needs a login now. Features
  without the account (backup, software updates, maps, music) work with any watch.
- **TrueUp** (`FIT_TYPE_39`): when you own several Garmin devices, Connect asks the client to
  check back after a short delay (at most 90 s) and then sends adjusted daily totals, which
  are written to the watch.
- **Connect IQ** apps queued for the watch (e.g. chosen in the store on your phone) are
  installed during sync, except on devices that hide their app folder: there they stay
  queued until you run `apps install` or use the window (see *Connect IQ apps*). New watch
  faces are installed but not activated, like in Garmin Express.

## Languages

The interface is available in English and Polish. The language follows the system
locale; you can override it:

- in the window: main menu (☰) → Preferences → Language,
- for one command: `openexpress --lang pl …` or `OPENEXPRESS_LANG=pl`,
- permanently: `openexpress setup --ui-language pl` (`system` restores the default).

### Adding a translation

Translations use GNU gettext (`xgettext`, `msgmerge`, `msgfmt` from the `gettext` package).

```bash
tools/update-translations.sh de      # creates po/de.po from the template
# translate po/de.po (e.g. with Poedit, GNOME Translation Editor or a text editor)
tools/update-translations.sh         # updates all languages and compiles .mo files
python3 -m pytest tests/test_i18n.py # checks placeholders and compiled catalogs
```

Keep `{placeholders}` unchanged and fill all plural forms. Add the language name to
`LANGUAGE_NAMES` in `openexpress/i18n.py`.

## Development

```bash
python3 -m pytest
```

Bug reports: please include the watch model, the output of `openexpress info` and the
command you ran. Never post `~/.config/openexpress/*.json` (your login token) or your
`GarminDevice.xml` (it contains the serial number and map unlock codes).

## License

openExpress is free software under the [GNU General Public License v3.0 or later](LICENSE).
