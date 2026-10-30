<p align="center">
  <img src="openexpress/data/io.github.tomaszbojanowski.OpenExpress.svg" width="128" alt="openExpress icon">
</p>

<h1 align="center">openExpress</h1>

<p align="center">
  An unofficial Linux app for Garmin watches and bike computers: sync with Garmin Connect,
  software updates, maps, Connect IQ apps and music over USB.
</p>

<p align="center">
  Featured in <a href="https://the5krunner.com/2026/10/17/openexpress-garmin-express-linux/">the5krunner's Indie Spotlight</a>
</p>

<p align="center">
  <a href="https://github.com/TomaszBojanowski/openExpress/releases/latest"><img alt="Latest release" src="https://img.shields.io/github/v/release/TomaszBojanowski/openExpress?label=release&cacheSeconds=3600"></a>
  <a href="LICENSE"><img alt="License: GPL-3.0-or-later" src="https://img.shields.io/badge/license-GPL--3.0--or--later-blue"></a>
  <a href="https://the5krunner.com/2026/10/17/openexpress-garmin-express-linux/"><img alt="Featured in the5krunner" src="https://img.shields.io/badge/featured%20in-the5krunner-orange"></a>
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

## What it is

Garmin Express exists only for Windows and macOS. openExpress is an independent
implementation for Linux with its own code: how the Garmin services and the watch's files
work was learned by analysing the official application, solely to interoperate with them.
It contains no Garmin code; the application key and the value that signs download addresses
are read from **your own copy** of the official installer during setup.

**Status:** beta. The main features work and are tested on the devices below; other Garmin
devices that connect over USB (MTP) should work too – reports are welcome.

- Sync with Garmin Connect: new activities, sleep, HRV and other data go up; courses,
  workouts and Connect IQ apps queued in Connect come down to the watch; TrueUp.
- Software updates and free maps (TopoActive, CourseView, Ski…), checked by MD5, and maps
  of your own.
- Connect IQ apps: install, update, remove, turn on or off, put back in order – also on
  watches that hide their app folder (e.g. the fēnix 8).
- Music, podcasts and audiobooks, with playlists.
- Backups, copies of the device's diagnostic files on this computer, the primary training
  device and the primary wearable.
- A GTK 4 / libadwaita window, a command-line tool and sync in the background.
- No telemetry. English and Polish.

## Supported devices

| Device | Connection | Checked by |
|---|---|---|
| fēnix 8 (47 mm, Solar) | MTP | the developer; [#13](https://github.com/TomaszBojanowski/openExpress/issues/13) |
| Edge 1050 | MTP | the developer |
| Varia RTL515 | USB drive | the developer; [#9](https://github.com/TomaszBojanowski/openExpress/issues/9) |
| epix (Gen 2) | MTP | the developer (read-only: another account) |
| Venu 2 | MTP | the developer (read-only: another account) |
| fēnix 8 (47 mm, AMOLED) | MTP | [#4](https://github.com/TomaszBojanowski/openExpress/issues/4) |
| fēnix 8 (51 mm, AMOLED) | MTP | [#14](https://github.com/TomaszBojanowski/openExpress/issues/14) |
| fēnix 7 (Sapphire Dual Power) | MTP | [#6](https://github.com/TomaszBojanowski/openExpress/issues/6) |
| fēnix 7X (Sapphire Solar), fēnix 7S (Sapphire) | MTP | users on Reddit |
| Instinct 3 – 45mm Tactical | MTP | [#21](https://github.com/TomaszBojanowski/openExpress/issues/21) |
| Edge 1040 | MTP | [#8](https://github.com/TomaszBojanowski/openExpress/issues/8), [#10](https://github.com/TomaszBojanowski/openExpress/issues/10) |
| Edge 540 | MTP | [#18](https://github.com/TomaszBojanowski/openExpress/issues/18) |
| inReach Mini 2 | USB drive | [#11](https://github.com/TomaszBojanowski/openExpress/issues/11) |
| Forerunner 55 | USB drive | [#19](https://github.com/TomaszBojanowski/openExpress/issues/19) |
| Forerunner 920XT | USB drive | [#20](https://github.com/TomaszBojanowski/openExpress/issues/20) |
| zūmo 595LM | – | [#15](https://github.com/TomaszBojanowski/openExpress/issues/15) |
| Other Garmin watches and bike computers over USB (MTP) | MTP | expected to work, **untested** |

What works on each, and the reports in full: [Supported devices](docs/devices.md). A device
that connects as a USB drive gets its information and software updates; sync, apps, maps and
music are for MTP devices only.

## Install

```bash
flatpak remote-add --user --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo
flatpak remote-add --user --if-not-exists openexpress https://flatpak.bojanowski.me/openexpress.flatpakrepo
flatpak install --user openexpress io.github.tomaszbojanowski.OpenExpress
```

Updates then come with `flatpak update`. The GNOME runtime comes from Flathub, hence the
first line. Not using Flatpak, or installing offline: [Other ways to install](docs/install.md).

### System requirements

openExpress reads devices that connect over MTP (most watches and bike computers) through
gvfs, as GNOME's file manager does; the Flatpak uses the gvfs of your system. gvfs needs its
MTP backend:

- Fedora: `sudo dnf install gvfs-mtp`
- Arch and distributions based on it (e.g. CachyOS, Manjaro): `sudo pacman -S gvfs-mtp`
- Debian, Ubuntu: `sudo apt install gvfs-backends`

GNOME comes with it; KDE Plasma and other desktops often do not. Log out and back in after
installing it. Without it, the window says **No MTP support in the system (gvfs-mtp)** as soon
as a device is plugged in, and the command line says the same. Devices in mass storage mode
(such as the Varia RTL515) need no MTP.

**KDE Plasma and Dolphin:** Dolphin opens MTP devices through KDE's own MTP service (kmtpd
from kio-extras, in the `kiod6` process), which keeps the device, so the window says
**The device is not ready**. Close Dolphin, run `kquitapp6 kiod6`, then unplug the cable and
plug it back in ([more](docs/troubleshooting.md#kde-plasma-and-dolphin)).

## Getting started

1. Open openExpress from your application menu.
2. Download the Garmin Express installer **for Windows** (`GarminExpress.exe`, free from
   [garmin.com](https://www.garmin.com/en-US/software/express/windows/)) – also without
   Windows: openExpress only reads it. The installer for macOS does not work.
3. Connect the watch. On its page, click **Choose Installer…** in the banner and select the
   installer, then **Log In**.
4. Sync. The first sync asks whether to upload everything on the watch or new data only.
5. To sync whenever the watch is connected, turn on **Sync automatically when connected** on
   its page.

How the window works: [Usage](docs/guide.md).

## Command line

The same as commands: `flatpak run io.github.tomaszbojanowski.OpenExpress <command>`, or
`openexpress <command>` from source – for example `sync`, `updates` or `maps`. A command that
changes anything only shows its plan until you add `--apply`. Every command:
[Command line](docs/cli.md).

## Limitations

- Connect IQ app settings (their form is rendered by apps.garmin.com).
- Sync, apps, maps and music for devices that connect as a USB drive.
- Map updates for devices whose maps Garmin serves another way than its universal map
  service, and maps on a memory card.
- GPS satellite data (ephemerides) and ECG recordings (they need Garmin Health's own
  sign-in).
- Software updates are installed when you choose to, never as part of a sync.

## Privacy

- The password is typed only on Garmin's page; openExpress keeps a login token in a file only
  you can read.
- No telemetry. The watch's diagnostics are uploaded only with `--include-diagnostics`, and
  the copies of its diagnostic files stay on this computer.
- Sync never deletes files from the watch, and every file written to it is checked.
- The log never has serial numbers, unit IDs, tokens or e-mail addresses.

More: [Privacy and security](docs/privacy.md).

### Flatpak permissions

Besides a window and the network, the Flatpak may run `gio copy` on the host (gvfs lets no
sandboxed app write to MTP devices), open USB devices (`--device=usb`: Connect IQ apps on
watches that hide their app folder) and use the drives mounted under `/run/media` and `/media`
(devices in mass storage mode). What each is for and how to turn it off:
[Flatpak permissions](docs/privacy.md#flatpak-permissions).

## Support

Tried openExpress with another device, or something does not work? Please send a
[device report](https://github.com/TomaszBojanowski/openExpress/issues/new?template=device_report.yml) –
also when everything works. In the window: main menu (☰) → **Report a Device or Problem…**;
on the command line: `openexpress report`, and `openexpress report --log` for the log. The
report has no serial number, unit ID or e-mail. Do not post the output of `openexpress info`,
your `GarminDevice.xml` or the files in `~/.config/openexpress`. When something goes wrong:
[Troubleshooting](docs/troubleshooting.md).

## Documentation

- [Supported devices](docs/devices.md) – every device and what was tested, USB modes
- [Usage](docs/guide.md) – the window, software updates, maps, apps, music, sync, settings
- [Command line](docs/cli.md) – every command and option, exit codes
- [Troubleshooting](docs/troubleshooting.md) – known problems, the log, reporting a problem
- [Privacy and security](docs/privacy.md) – what is kept and sent, the Flatpak's permissions
- [Other ways to install](docs/install.md) – a bundle from a release, from source, building the Flatpak
- [Contributing](CONTRIBUTING.md) – tests and translations

## License

openExpress is free software under the [GNU General Public License v3.0 or later](LICENSE).
