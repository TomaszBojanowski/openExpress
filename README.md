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
  <img src="docs/screenshots/maps.png" width="45%" alt="Maps">
  <img src="docs/screenshots/apps.png" width="45%" alt="Connect IQ apps">
</p>

Garmin Express exists only for Windows and macOS. openExpress is an independent
re-implementation written from scratch, so Linux users can manage their watch without a
virtual machine. It contains no Garmin code or data: the few values it needs to talk to
Garmin's servers are read from **your own copy** of the official installer during setup.

**Status:** alpha. Developed and tested with a **fēnix 8 Solar** on Fedora/GNOME. Other
Garmin watches that connect over USB (MTP) should work, but are untested; reports are welcome.

**Highlights**

- Garmin Connect sync: new activities, sleep, HRV, metrics… are uploaded; courses, workouts
  and Connect IQ apps queued in Connect are written to the watch; TrueUp for multiple devices.
- Watch software updates and free maps (TopoActive, CourseView, Ski…), with MD5 verification.
- Connect IQ apps: install, update, remove, enable/disable built-in activities.
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
| `openexpress apps install/update/remove/enable/disable … [--apply]` | install (via the Connect queue), update, remove, enable or disable apps and built-in activities |
| `openexpress music [add PATH… \| playlist FILE.m3u \| remove TEXT…] [--apply]` | music, podcasts and audiobooks (Artist/Album layout, `.m3u8` playlists) |
| `openexpress gui` | application window (GTK 4 + libadwaita): watch, sync, updates, maps, apps, music |
| `openexpress watch` | background service: sync when the watch is connected, with a notification |
| `openexpress install-desktop [--apply]` | application menu entry and systemd user service |

All commands that change anything run **in dry-run mode by default**:
they show the plan and do nothing without `--apply`.

## Install

### Flatpak (recommended)

Download `openexpress-<version>.flatpak` from the
[latest release](https://github.com/TomaszBojanowski/openExpress/releases/latest) and run:

```bash
flatpak install --user openexpress-*.flatpak   # needs the Flathub remote for the GNOME runtime
flatpak run io.github.tomaszbojanowski.OpenExpress
```

openExpress then appears in your application menu. On the **Watch** tab select the
Garmin Express installer, sign in, and optionally turn on **Sync in the background**.
The command-line tool is available as
`flatpak run io.github.tomaszbojanowski.OpenExpress <command>` (for example `sync` or `maps`).

Build the Flatpak yourself:

```bash
flatpak install --user flathub org.gnome.Sdk//50
flatpak-builder --user --install --force-clean build-dir packaging/flatpak/io.github.tomaszbojanowski.OpenExpress.yml
```

### From source

## Requirements

- Linux with Python 3.11+, `requests`, PyGObject with GTK 4, libadwaita, WebKitGTK 6.0
  (login window) and GIO/gvfs (MTP). Optional: `mutagen` (music tags), `7z` (setup).
  - Fedora: `sudo dnf install python3-requests python3-gobject gtk4 libadwaita webkitgtk6.0 gvfs-mtp python3-mutagen p7zip-plugins`
  - Debian/Ubuntu: `sudo apt install python3-requests python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 gir1.2-webkit-6.0 gvfs-backends python3-mutagen 7zip`
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

The easiest way is the window: `openexpress gui`, then select the installer on the Watch tab
and sign in there.

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
never sync at the same time (shared lock).

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
- The `Garmin-Client-*` headers use the same values as Garmin Express (the server returns
  the configuration based on them). They can be changed in `~/.config/openexpress/settings.json`.

## Not yet available (next stages)

- Connect IQ app settings (the settings form is rendered by apps.garmin.com).
- Checking the state of GDPR consents (locations with required consents are skipped).

## Sync details

- **TrueUp** (`FIT_TYPE_39`): when you own several Garmin devices, Connect asks the client to
  check back after a short delay (at most 90 s) and then sends adjusted daily totals, which
  are written to the watch.
- **Connect IQ** apps queued for the watch (e.g. chosen in the store on your phone) are
  installed during sync. New watch faces are installed but not activated, like in Garmin
  Express.

## Languages

The interface is available in English and Polish. The language follows the system
locale; you can override it:

- in the window: Watch tab → Settings → Language,
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
