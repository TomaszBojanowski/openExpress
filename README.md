<p align="center">
  <img src="openexpress/data/io.github.tomaszbojanowski.OpenExpress.svg" width="128" alt="openExpress icon">
</p>

<h1 align="center">openExpress</h1>

<p align="center">
  An unofficial Linux app for Garmin watches: sync with Garmin Connect, software updates,
  maps, Connect IQ apps and music over USB.
</p>

Featured in [the5krunner's Indie Spotlight](https://the5krunner.com/2026/10/17/openexpress-garmin-express-linux/).

> **openExpress is not affiliated with, endorsed by or supported by Garmin Ltd.**
> Garmin, fēnix, Garmin Connect and Garmin Express are trademarks of Garmin Ltd. or its
> subsidiaries. openExpress talks to the same Garmin services as the official Windows/macOS
> app; Garmin may change them at any time. Use at your own risk.

<p align="center">
  <img src="docs/screenshots/updates.png" width="800"
       alt="The Updates page: a beta software update offered for an Edge 1050">
</p>

**Install:** add the openExpress Flatpak repository and install the app from it:

```bash
flatpak remote-add --user --if-not-exists openexpress https://flatpak.bojanowski.me/openexpress.flatpakrepo
flatpak install --user openexpress io.github.tomaszbojanowski.OpenExpress
```

Updates then come with `flatpak update`. The GNOME runtime comes from Flathub.
Alternatively, for example to install offline, download `openexpress-0.6.2.flatpak` from the
[latest release](https://github.com/TomaszBojanowski/openExpress/releases/latest), then run

```bash
flatpak install --user openexpress-0.6.2.flatpak
```

More in [Install](#install), including the version from source.

**Devices in mass storage mode:** besides devices that connect over USB in MTP mode, such as
the fēnix 8 and the Edge 1050, openExpress 0.6 supports Garmin devices that connect as a USB
drive (mass storage mode: some older Garmin models, and accessories such as the Varia RTL515):
their information and software updates, in the window of the version from source and on the
command line with `--path`. It is tested on the Varia RTL515; reports from other devices in
mass storage mode are welcome. The Flatpak cannot open drives yet and says so. If you are not
sure which mode your device uses, plug it in: if it appears as a USB drive in your file
manager rather than through MTP, it is in mass storage mode.

Garmin Express exists only for Windows and macOS. openExpress is an independent
implementation with its own code, so Linux users can manage their watch without a
virtual machine. How the Garmin services and the watch's file layout work was learned by
analysing the official application, solely to make openExpress interoperate with them.
It contains no Garmin code or data: the few values it needs to talk to
Garmin's servers are read from **your own copy** of the official installer during setup.

**Status:** beta. The main features work and are tested on the devices listed below; other
Garmin devices that connect over USB (MTP) should work, but are untested – reports are
welcome. Developed on Fedora/GNOME and tested with these Garmin devices:

- **fēnix 8 Solar**, on the developer's Garmin account: sync, Connect IQ apps, maps and
  music are tested; software updates are checked and downloaded, but not yet installed on
  this device.
- **Edge 1050**, on the same account: mostly reading is tested; one system software update
  was installed with openExpress (beta 33.12, 14 October 2026).
- **Venu 2**, not on the account: reading is tested, and it was the first device to get
  software updates installed with openExpress (Time Zone Map and Exercise to muscle
  mapping, 13 October 2026).

Details are in [Supported devices](#supported-devices).

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
| `openexpress updates [--reinstalls]` | watch software updates (no login needed); a file the watch already has with the same size and MD5 is not counted as an update, only listed as "already on the device" |
| `openexpress updates --install PART… [--apply]` | download with MD5 check and copy to the watch; after it is unplugged the watch may ask whether to install the update now or later, then restart |
| `openexpress maps [--all]` | installed and available maps (TopoActive, base maps, CourseView, Ski) |
| `openexpress maps install NAME [--apply]` | install a map assigned to the watch (download, activation, write) |
| `openexpress maps update [--apply]`, `maps remove NAME [--apply]` | update and remove maps |
| `openexpress maps add FILE.img [--name NAME] [--apply]` | copy a map of your own (e.g. made from OpenStreetMap data) to the device; never overwrites a file |
| `openexpress maps remove --own NAME [--apply]` | remove a map of your own that was copied with `maps add` |
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
| fēnix 8 (47 mm, Solar) | tested by the developer: sign-in, sync (upload, TrueUp), software update download, map install, maps of your own (added from the command line and the window, removed in the window; also in the Flatpak), copying music and playlists, Connect IQ apps in the version from source (install, remove, restoring the order; command line and window) and in the Flatpak with its USB permission (install and remove in the window, 15 October 2026). **Not yet tested on the watch:** updating a Connect IQ app, installing a software update |
| epix (part 006-B3943) | read-only commands and backup tested |
| Edge 1050 | detected automatically; read-only commands, backup and sync plan tested; one system software update installed in the window (beta 33.12, 14 October 2026) |
| Venu 2 (part 006-B3703) | read-only commands tested; installing software updates tested (two components, 13 October 2026). The watch was not registered to the Garmin Connect account used for testing, so nothing that needs the account (sync, Connect IQ apps) was tested on it |
| Other Garmin watches and bike computers connecting over USB (MTP) | expected to work, **untested** |

Tried openExpress with another device? Please send a
[device report](https://github.com/TomaszBojanowski/openExpress/issues/new?template=device_report.yml) –
also when everything works. In the window: main menu (☰) → **Report a Device or Problem…**
→ **Open on GitHub** opens the form with the report and a title filled in; or run
`openexpress report`, which prints the report and an address that does the same. The report
contains no serial number, unit ID or e-mail. Nothing is published until you submit the form.

## Install

### Flatpak (recommended)

Install from the openExpress Flatpak repository (signed; updates then come with
`flatpak update`):

```bash
flatpak remote-add --user --if-not-exists openexpress https://flatpak.bojanowski.me/openexpress.flatpakrepo
flatpak install --user openexpress io.github.tomaszbojanowski.OpenExpress   # needs the Flathub remote for the GNOME runtime
flatpak run io.github.tomaszbojanowski.OpenExpress
```

Alternatively, for example to install offline, download `openexpress-<version>.flatpak` from
the [latest release](https://github.com/TomaszBojanowski/openExpress/releases/latest) and run:

```bash
flatpak install --user openexpress-*.flatpak   # needs the Flathub remote for the GNOME runtime
```

An app installed from such a file is not updated by `flatpak update`. To switch it to the
repository, keeping your setup, login and settings, add the repository as above and run
`flatpak install --user --reinstall openexpress io.github.tomaszbojanowski.OpenExpress`.

openExpress then appears in your application menu. Click **Choose Installer…** in the banner
at the top and select the Garmin Express installer, then **Log In**. Turn on automatic sync on
the Device page (or in Preferences) to sync whenever the watch is connected.
The command-line tool is available as
`flatpak run io.github.tomaszbojanowski.OpenExpress <command>` (for example `sync` or `maps`).

**About the Flatpak's permissions:** besides network, USB storage access through GNOME's
gvfs and your Downloads/Music folders, the Flatpak has two permissions that weaken the
sandbox:

- It can run commands on the host (`org.freedesktop.Flatpak`). It uses this only to copy
  files to the watch with `gio copy`, because gvfs does not let sandboxed apps write to MTP
  devices.
- It can open USB devices (`--device=usb`: the nodes in `/dev/bus/usb`, nothing else of
  `/dev`). It uses this only for Connect IQ apps on devices that hide their app folder
  (e.g. the fēnix 8): installing, updating and removing such apps needs a direct USB
  connection, and the device disappears from the file manager for a moment while it is
  used.

If you prefer not to grant the first one, use the version from source (writing to the watch
then works without it). The second one can be turned off with
`flatpak override --user --nodevice=usb io.github.tomaszbojanowski.OpenExpress`; Flatpak
older than 1.16 ignores it. Without it, on devices that hide their app folder, installs and
updates are only queued in Garmin Connect, from where the phone or Garmin Express installs
them, apps are removed on the phone, and nothing is written to the device.

**USB modes:** devices that connect over MTP (most watches and bike computers) work in both
versions; devices that connect as a USB drive (mass storage, e.g. the Varia RTL515) are
supported in the version from source – in its window, or on the command line with `--path` –
and the Flatpak does not open them yet. Mass storage is tested on the Varia RTL515; reports
from other such devices are welcome.

**The Flatpak and the version from source are separate installations:** each keeps its own
setup (the key from the Garmin Express installer), login, settings and sync state: its own
last syncs, uploaded files and list of your own maps. After switching from one to the other,
choose the installer and log in again. The first sync of each device then starts anew: the
window asks whether to mark the files already on it as uploaded, and on the command line
`openexpress sync --apply` stops and suggests `openexpress baseline --apply` or `--all`.

openExpress is not on Flathub and will not be: it uses Garmin's unofficial interfaces.

Build the Flatpak yourself (this needs `flatpak-builder` with Flatpak 1.16 or newer; older
versions refuse `--device=usb` with "Unknown device type usb"):

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

<p align="center">
  <img src="docs/screenshots/device.png" width="700"
       alt="The device page: sync, updates, Garmin Connect account and tools">
</p>

## Software updates

The Updates page (and `openexpress updates`) asks Garmin's update server what is new for the
connected device; no login is needed.

- **Status:** the top of the page says whether a system software update is available (with
  the installed and the new version), how many other updates there are, or that the software
  is up to date.
- **System updates are recognised by their file** (`GUPDATE.GSP`, `GUPDATE.GCD`) as well as
  by what the server says: the server does not always mark them.
- **Beta versions** that Garmin offers to a device in its beta program are marked *Beta* in
  the window and `[BETA]` on the command line.
- **Before a system update** is copied, the window and `openexpress updates --install` warn
  to charge the device and not to disconnect it while copying. The question in the window
  shows both versions and links to the license (EULA).
- **After the file is copied** the device installs it when it is disconnected. Until then
  the page says *Update ready to install* and `openexpress updates` says that the update
  waits for the device.

Installing a system update was tested on an Edge 1050 (beta 33.12, 14 October 2026).

<p align="center">
  <img src="docs/screenshots/update-confirm.png" width="700"
       alt="The question before a system update: versions, license link and warning">
</p>

## Maps of your own

Besides the maps Garmin offers, a device with maps also takes map files made by others, for
example from OpenStreetMap data (such as the Garmin `.img` downloads of BBBike.org).
openExpress lists these files on the Maps page and in `openexpress maps`, with the name that
is stored in the file.

- **Adding:** *Add Map…* on the Maps page, or `openexpress maps add FILE.img --apply`. The
  file keeps its name on the device; `--name` gives it another one (letters, digits, spaces
  and `. _ - ( )`, ending in `.img`).
- **Nothing is overwritten:** a file that is already on the device is never replaced, and
  names that Garmin's own maps use are refused. Garmin's maps cannot be replaced or removed
  this way.
- **Removing:** the remove button on the Maps page, or
  `openexpress maps remove --own NAME --apply` for a map this installation copied. A map
  that was copied by hand or by another installation (the Flatpak and the version from
  source keep separate lists) can be removed in the window after an extra question.
- **Where it works:** on devices that have a folder for maps (e.g. the fēnix 8); a Venu 2
  has none. Tested on a fēnix 8 with a map from BBBike.org. openExpress checks that the file
  is a Garmin map file, not what is in the map.

<p align="center">
  <img src="docs/screenshots/maps.png" width="700" alt="Maps, with a map of your own">
</p>

## Connect IQ apps

- Some watches (e.g. the fēnix 8) keep their app folder out of the ordinary file listing.
  On such a device `apps install`, `apps update`, `apps remove` and the buttons in the window
  talk to it over a direct USB connection; the device disappears from the file manager for a
  moment and is mounted again afterwards. The device finishes the change after it is
  unplugged; until then the app is shown as waiting. A sync never does this: it leaves such
  apps in the queue. In the Flatpak this needs its USB permission (see above).
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

<p align="center">
  <img src="docs/screenshots/apps.png" width="700" alt="Connect IQ apps">
</p>

## Music

The Music page (*Files…*, *Folders…*, *Add Playlist…*) and `openexpress music` copy music,
podcasts and audiobooks to the folders the device declares, as Artist/Album, and write
playlists as `.m3u8`. Before copying, openExpress says which files on the device will be
replaced. Tested on a fēnix 8.

<p align="center">
  <img src="docs/screenshots/music.png" width="700" alt="Music">
</p>

## Log file

openExpress keeps a log of what it does, to make problems easier to find:

- **Where:** `~/.local/state/openexpress/openexpress.log`; in the Flatpak
  `~/.var/app/io.github.tomaszbojanowski.OpenExpress/.local/state/openexpress/openexpress.log`.
  The command-line tool, the window and the background sync write to the same file. At most
  three files of about 1 MB are kept; the file is readable only by you.
- **Telling runs apart:** every line carries a short random marker (e.g. `cli[a3f1]`) that is
  the same for one run of the program and means nothing else. The first line of a run says
  the version, whether it is the Flatpak or runs from source, the Python version, the
  language of the app and the process ID; nothing about you or your computer.
- **What it contains:** which command or sync ran and how it ended, the steps of a sync
  (numbers of files by type, not their contents), app installs and removals, how the device
  was detected (including when it was mounted or unmounted), requests to Garmin's servers
  as method, address without parameters, status and duration, and error messages.
- **What it never contains:** serial numbers, unit IDs, MTP addresses, tokens, passwords,
  cookies, e-mail addresses, local paths, addresses of local files and network shares
  (`file://`, `smb://`, `sftp://`…), your user name, names of your music files, or file
  contents.
  Every line passes a filter that replaces such values before it is written. The log
  stays on your computer; nothing is sent anywhere.
- **More or less:** `OPENEXPRESS_LOG=debug` (or `-v`) writes more detail,
  `OPENEXPRESS_LOG=off` writes no file. What `-v` and the background sync print on the
  terminal passes the same filter; messages meant for you (e.g. the addresses to choose
  from with `--device-uri`) and the output of commands such as `openexpress info` do not.
- **In a bug report:** `openexpress report` says where the log is; `openexpress report --log`
  appends its last 200 lines, filtered once more. In the window, tick **Attach the last log**
  in *Report a Device or Problem…*; the preview shows exactly what will be copied. **Open on
  GitHub** puts only the report into the form and the log into the clipboard, to paste into
  the issue: the log never goes into the address. Read it before you send it.

## Cache

Downloaded maps and software updates, device pictures and files on their way to the device
are kept in `~/.cache/openexpress` (the Flatpak: `~/.var/app/io.github.tomaszbojanowski.OpenExpress/cache/openexpress`;
each installation has its own). `openexpress cache` shows how big it is, `openexpress cache --clear --apply`
empties it, and so does Preferences → Storage → Clear… in the window; maps are then downloaded again
when they are installed or updated. It is not cleared while a download or an install is running.

## Privacy and security

- The password is entered only on Garmin's page; openExpress never sees or stores it. What
  is stored locally after signing in is an OAuth1 token and, next to it, your user name,
  e-mail address, profile number and account identifier, in one file readable only by you
  (permissions 0600): `~/.config/openexpress/connect_auth.json`, or in the Flatpak
  `~/.var/app/io.github.tomaszbojanowski.OpenExpress/config/openexpress/connect_auth.json`.
  `openexpress logout` removes that file.
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
- The picture of a device that is not on the signed-in account (or when nobody is signed
  in) is looked up on Garmin's update server; like the update check, that request names the
  device (unit ID). The answer is not stored or logged, only the picture is cached.
- The `Garmin-Client-*` headers use the same values as Garmin Express (the server returns
  the configuration based on them). They can be changed in `~/.config/openexpress/settings.json`.

## Not yet available (next stages)

- Connect IQ app settings (the settings form is rendered by apps.garmin.com).
- Checking the privacy-policy consents before a sync (the upload consent is checked, see above).

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
- **Last sync** on the Device page, `openexpress sync` and the notification of the
  background sync use the same words: how many new files were uploaded, how many Connect
  already had (recognised before sending or, after sending, by Garmin), how many Garmin
  rejected and how many errors there were; the new files are named by their type
  (activities, sleep, HRV status…; others as "other data").

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
