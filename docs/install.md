# Other ways to install

## Flatpak bundle from a release

For example to install offline, download `openexpress-<version>.flatpak` from the
[latest release](https://github.com/TomaszBojanowski/openExpress/releases/latest) and run:

```bash
flatpak install --user openexpress-<version>.flatpak   # needs the Flathub remote for the GNOME runtime
```

An app installed from such a file is not updated by `flatpak update`. To switch it to the
repository, keeping your setup, login and settings, add the repository as in
[Install](../README.md#install) and run
`flatpak install --user --reinstall bojanowski io.github.tomaszbojanowski.OpenExpress`.

## From source

Requirements:

- Linux with Python 3.11+, `requests`, PyGObject 3.46+ with GTK 4, libadwaita 1.5+, WebKitGTK 6.0
  (login window) and GIO/gvfs (MTP). Optional: `7z` (7-Zip or p7zip; setup needs it only for
  a Garmin Express installer older than version 8), `mutagen` (music tags), `libusb-1.0`
  (Connect IQ apps on devices that hide their app folder, and their hidden crash logs;
  usually installed), libshumate 1.1+ (the map on the page of an activity; without
  libshumate the page says so and has no map, and with an older one the track has no
  streets under it). GNU gettext (`msgfmt`)
  builds the translations, which are not kept compiled in the repository; without it
  openExpress runs in English.
  - Fedora: `sudo dnf install python3-requests python3-gobject gtk4 libadwaita webkitgtk6.0 gvfs-mtp python3-mutagen p7zip-plugins libusb1 libshumate gettext`
  - Debian/Ubuntu: `sudo apt install python3-requests python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 gir1.2-webkit-6.0 gvfs-backends python3-mutagen 7zip libusb-1.0-0 gir1.2-shumate-1.0 gettext`
- A device connected with a USB cable. GNOME (gvfs-mtp) mounts it automatically, and
  openExpress uses the same connection, so the file manager can stay open.
- The official Garmin Express installer for Windows (`GarminExpress.exe`, free download from
  [garmin.com](https://www.garmin.com/en-US/software/express/windows/)), used once during
  setup. It is not installed or run; the installer for macOS does not work.

First run:

```bash
git clone https://github.com/TomaszBojanowski/openExpress.git
cd openExpress
python3 -m openexpress.catalogs      # the translations (gettext); install-desktop builds them too
pip install --user .                 # or run without installing: python3 -m openexpress …
                                     # (where pip refuses with "externally-managed-environment")

# 1. One-time setup from the official installer (downloaded from garmin.com)
openexpress setup --from-installer ~/Downloads/GarminExpress.exe

# 2. Sign in
openexpress login                    # window with the official Garmin sign-in page
openexpress whoami --check

# 3. Preview and first sync
openexpress sync                     # plan only
openexpress baseline --apply         # if the device also syncs via your phone
openexpress sync --apply
```

The easiest way is the window: `openexpress gui`, then click **Choose Installer…** in the
banner at the top and **Sign In**.

## The Flatpak and the version from source

They are separate installations. Each keeps its own setup (the key from the Garmin Express
installer), login, settings and sync state: its own last syncs, uploaded files and list of your
own maps.

- After switching from one to the other, choose the installer and sign in again.
- The first sync of each device then starts anew in the Flatpak: the window asks whether to mark
  the files already on it as uploaded, and on the command line `openexpress sync --apply` stops
  and suggests `openexpress baseline --apply` or `--all`.
- A file the version from source uploaded is new to the Flatpak; Garmin Connect recognises it as
  a duplicate.

The version from source also reads (never writes) what the Flatpak keeps of a device:

- A file the Flatpak uploaded is not uploaded again (a settings file only while its content is
  the same).
- A file from Garmin Connect the Flatpak wrote to the device but could not report as received is
  only reported.
- A device the Flatpak synced needs no first-sync choice, and its tile does not say **Never
  synced**.
- While the Flatpak syncs, a sync from source does not start: "openExpress (Flatpak) is syncing a
  device now; try again when it is done."
- When the Flatpak synced the device after it, the sync says so once: in the details of **Last
  Sync** and in the dry run of `openexpress sync`.

The Flatpak cannot see the files of the version from source.

## Building the Flatpak yourself

openExpress is not on Flathub and will not be: it uses Garmin's unofficial interfaces.
Building the Flatpak needs `flatpak-builder` with Flatpak 1.16 or newer; older versions
refuse `--device=usb` with "Unknown device type usb":

```bash
flatpak install --user flathub org.gnome.Platform//50 org.gnome.Sdk//50
flatpak-builder --user --install --force-clean build-dir packaging/flatpak/io.github.tomaszbojanowski.OpenExpress.yml
```
