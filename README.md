<p align="center">
  <img src="openexpress/data/io.github.tomaszbojanowski.OpenExpress.svg" width="128" alt="openExpress icon">
</p>

<h1 align="center">openExpress</h1>

<p align="center">
  An unofficial Linux app for Garmin watches: sync with Garmin Connect, software updates,
  maps, Connect IQ apps and music over USB.
</p>

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/screenshots/start.png">
    <img alt="openExpress – devices page" src="docs/screenshots/start-light.png" width="800">
  </picture>
</p>

> **openExpress is not affiliated with, endorsed by or supported by Garmin Ltd.**
> Garmin, fēnix, Garmin Connect and Garmin Express are trademarks of Garmin Ltd. or its
> subsidiaries. openExpress talks to the same Garmin services as the official Windows/macOS
> app; Garmin may change them at any time. Use at your own risk.

Featured in [the5krunner's Indie Spotlight](https://the5krunner.com/2026/10/17/openexpress-garmin-express-linux/).

## Install

```bash
flatpak remote-add --user --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo
flatpak remote-add --user --if-not-exists openexpress https://flatpak.bojanowski.me/openexpress.flatpakrepo
flatpak install --user openexpress io.github.tomaszbojanowski.OpenExpress
```

Updates then come with `flatpak update`. The GNOME runtime comes from Flathub, hence the
first line; it does nothing when you already have Flathub for your user. openExpress then appears in your application
menu; see [Getting started](#getting-started).

Not using Flatpak, or installing offline? See [Other ways to install](docs/install.md#other-ways-to-install).

### System requirements

openExpress reads devices that connect over MTP (most watches and bike computers) through
gvfs, as GNOME's file manager does; the Flatpak uses the gvfs of your system. gvfs needs its
MTP backend:

- Fedora: `sudo dnf install gvfs-mtp`
- Arch and distributions based on it (e.g. CachyOS, Manjaro): `sudo pacman -S gvfs-mtp`
- Debian, Ubuntu: `sudo apt install gvfs-backends`

GNOME comes with it; KDE Plasma and other desktops often do not. Log out and back in after
installing it. Without it, the window says **No MTP support in the system (gvfs-mtp)** as soon
as a device in its normal mode is plugged in, and the command line says the same. Devices in
mass storage mode (such as the Varia RTL515) need no MTP: they connect as a drive (when the
system has not mounted it, openExpress mounts it once you choose it).

**KDE Plasma and Dolphin:** Dolphin opens MTP devices through KDE's own MTP service (kmtpd
from kio-extras, in the `kiod6` process), not through gvfs. Once started, the service keeps
every MTP device it has opened, also after Dolphin is closed, and takes a device back as soon
as its cable is plugged in again, so gvfs cannot offer it; after 45 seconds the window says
**The device is not ready**. To free the device, close Dolphin, run `kquitapp6 kiod6`, then
unplug the cable and plug it back in. Open the device in openExpress rather than in Dolphin;
if you open it in Dolphin again, repeat these steps.

## Contents

- [System requirements](#system-requirements) · [Features](#features) ([not yet available](#not-yet-available)) ·
  [Supported devices](#supported-devices) ([USB modes](docs/devices.md#usb-modes-mtp-and-mass-storage))
- [Usage](#usage): [getting started](#getting-started), [window and background sync](docs/guide.md#application-window-and-background-sync),
  [command line](docs/cli.md#command-line), [exit codes](docs/cli.md#exit-codes), [software updates](docs/guide.md#software-updates),
  [maps of your own](docs/guide.md#maps-of-your-own), [Connect IQ apps](docs/guide.md#connect-iq-apps), [music](docs/guide.md#music),
  [sync details](docs/guide.md#sync-details), [troubleshooting](docs/troubleshooting.md#troubleshooting)
- [Configuration](docs/guide.md#configuration): [languages](docs/guide.md#languages), [log file](docs/troubleshooting.md#log-file), [cache](docs/guide.md#cache),
  [privacy and security](docs/privacy.md#privacy-and-security), [Flatpak permissions](#flatpak-permissions)
- [Other ways to install](docs/install.md#other-ways-to-install) · [Development](CONTRIBUTING.md#development) · [License](#license)

## Features

Garmin Express exists only for Windows and macOS. openExpress is an independent
implementation with its own code, so Linux users can manage their watch without a
virtual machine. How the Garmin services and the watch's file layout work was learned by
analysing the official application, solely to make openExpress interoperate with them.
It contains no Garmin code. It does contain the names by which Garmin Express introduces
itself to Garmin's servers (its client identifiers and the values of its client headers);
the application key, its secret and the value that signs download addresses are read from
**your own copy** of the official installer during setup.

**Status:** beta. The main features work and are tested on the devices listed in
[Supported devices](#supported-devices); other Garmin devices that connect over USB (MTP)
should work, but are untested – reports are welcome. Developed on Fedora/GNOME.

- Garmin Connect sync: new activities, sleep, HRV, metrics… are uploaded; courses, workouts
  and Connect IQ apps queued in Connect are written to the watch (on watches that hide their
  app folder, apps wait until you install them); TrueUp for multiple devices.
- Watch software updates and free maps (TopoActive, CourseView, Ski…), with MD5 verification.
  A map Garmin does not offer in the country of your Garmin account is not installed or
  updated, and the window and `openexpress maps` say why; the country is read from the
  account's profile (Garmin Express uses the computer's IP address), and without it (not
  signed in, a device of another account) such a map is only marked.
- Connect IQ apps: install, update, remove, enable/disable built-in activities – also on
  watches that hide their app folder (e.g. the fēnix 8), without reordering your widgets.
- Music, podcasts and audiobooks with playlists.
- Backups; showing and setting the primary training device and the primary wearable;
  copies of the device's diagnostic files, kept on this computer.
- GTK 4 / libadwaita window, command-line tool and background sync with notifications.
- No telemetry. English and Polish, translatable with gettext.

<p align="center">
  <img src="docs/screenshots/updates.png" width="800"
       alt="The Updates page: a component update offered for a fēnix 8">
</p>

### Not yet available

- Connect IQ app settings (the settings form is rendered by apps.garmin.com).
- Map updates for devices that connect as a USB drive (mass storage mode), such as older
  nüvi car navigators, and for devices whose maps Garmin serves another way than its
  universal map service (the window and `openexpress maps` say so; the Maps page names
  the map update type Garmin gives the device, which helps a report).
- Checking the privacy-policy consents before a sync (the upload consent is checked, see
  [Privacy and security](docs/privacy.md#privacy-and-security)).
- GPS satellite data (ephemerides), which Garmin Express writes to the watch when it syncs
  and which helps the watch find its position faster.
- Maps on a memory card: maps are installed and updated in the device's internal memory only.
- Installing software updates as part of a sync: they are installed only when you choose
  to, on the Updates page or with `openexpress updates --install`.
- Uploading ECG recordings: they go to Garmin Health, which needs a sign-in of its own that
  openExpress does not have. A sync leaves them on the watch and says how many there are.

## Supported devices

Which devices are tested, and what users report: [Supported devices](docs/devices.md).

## Usage

### Getting started

openExpress appears in your application menu. Setup needs the Garmin Express installer
**for Windows** (`GarminExpress.exe`, a free download from
[garmin.com](https://www.garmin.com/en-US/software/express/windows/)), also when you have
no Windows: openExpress only reads it and installs nothing. The installer for macOS does
not work. Connect the watch and open it: its page has a banner at the top. Click **Choose
Installer…** there and select the installer (at any time also in Preferences → Setup →
**Choose File…**), then **Log In** in the same banner (or in Preferences). Turn on automatic sync on the
Device page (or in Preferences) to sync whenever the watch is connected.

The command-line tool is available as
`flatpak run io.github.tomaszbojanowski.OpenExpress <command>` (for example `sync` or `maps`);
in the version from source it is `openexpress <command>`.

How the window works: [Usage](docs/guide.md); every command: [Command line](docs/cli.md); when something goes wrong: [Troubleshooting](docs/troubleshooting.md).

### Flatpak permissions

What the Flatpak may do beyond a window and the network, and how to turn it off: [Flatpak permissions](docs/privacy.md#flatpak-permissions).

## License

openExpress is free software under the [GNU General Public License v3.0 or later](LICENSE).
