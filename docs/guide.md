# Usage

## What openExpress does

**Status:** beta. The main features work and are tested on the devices listed in
[Supported devices](../README.md#supported-devices); other Garmin devices that connect over USB (MTP)
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
  <img src="screenshots/updates.png" width="800"
       alt="The Updates page: a component update offered for a fēnix 8">
</p>

## Not yet available

- Connect IQ app settings (the settings form is rendered by apps.garmin.com).
- Map updates for devices that connect as a USB drive
  ([USB modes](devices.md#usb-modes-mtp-and-mass-storage)), and for devices whose maps Garmin
  serves another way than its
  universal map service (the window and `openexpress maps` say so; the Maps page names
  the map update type Garmin gives the device, which helps a report).
- Checking the privacy-policy consents before a sync (the upload consent is checked, see
  [Privacy and security](privacy.md#privacy-and-security)).
- GPS satellite data (ephemerides), which Garmin Express writes to the watch when it syncs
  and which helps the watch find its position faster.
- Maps on a memory card: maps are installed and updated in the device's internal memory only.
- Installing software updates as part of a sync: they are installed only when you choose
  to, on the Updates page or with `openexpress updates --install`.
- Uploading ECG recordings: they go to Garmin Health, which needs a sign-in of its own that
  openExpress does not have. A sync leaves them on the watch and says how many there are.

## Setup

openExpress appears in your application menu. Setup needs the Garmin Express installer
**for Windows** (`GarminExpress.exe`, a free download from
[garmin.com](https://www.garmin.com/en-US/software/express/windows/)), also when you have
no Windows: openExpress only reads it and installs nothing. The installer for macOS does
not work. Connect the watch and open it: its page has a banner at the top. Click **Choose
Installer…** there and select the installer (at any time also in Preferences → Setup →
**Choose File…**), then **Log In** in the same banner (or in Preferences). Turn on automatic sync on the
Device page (or in Preferences) to sync whenever the watch is connected.

## The start page and the device page

With one Garmin device connected, the window opens on its page; otherwise on the start page
(the picture at the top), which is also one step back from a device page: a tile for each
Garmin device connected, for those seen before and, once you are logged in, for the other
devices of your Garmin account; a tile opens its device. Up to two labels on a tile say
what is waiting and what the device is: **Update available**, or **Unplug to install** (an
update copied to the connected device, which installs it once unplugged; **Eject to install**
for a USB drive), **To sync** (activities on the device not uploaded yet) or **Never synced**
(by this installation: [each keeps its own](install.md#the-flatpak-and-the-version-from-source)),
**USB drive**, **Primary** (the account's **Primary wearable**, as Garmin Connect names it:
steps and sleep come from this device), **Not on your account** and,
when nothing else needs saying, **Synced** (synced before, nothing new to upload). **To
sync** and **Synced** are there at once, as the last count found them (at most 7 days ago,
and only while nothing was synced since); the count that runs then confirms or changes them.
Labels of the account come from the account's list as it was last read (at most 7 days ago,
with the same login). While you are not logged in the tiles show neither these labels nor
**To sync**, **Never synced** or **Synced**, and a sentence under the tiles says so; it also
says when the list could not be read (the tiles then keep what was known before). The device page shows **Update available** or
**Update ready** under the software version too, and, under the account's **Primary training
device**, its **Primary wearable** (only for a device that can be one; **Set as Primary**
makes it this device, after a question). Its
**Apps** and **Music** rows lead to their pages, as **Maps** does, and say how many there are
once those pages have read them; **Backup** says when the last backup was made and how many
there are (the folder in its tooltip).

A device seen before and not connected opens read-only: what was known of it when it was
last seen, its last sync with its details, what the last check of its updates found and
when (*Not checked since …* when that check is older than 30 days), and, while the
account's list is known, its **Primary wearable**. **Forget Device…** at the bottom of that
page removes what this installation keeps of it (what was uploaded from it, its last sync, the maps of your own
copied to it and the copies of its diagnostic files); connected again, it is a new device, and its first sync asks again whether to
upload everything or new data only. While files of it are not uploaded yet, it is forgotten
only once you tick that they are discarded; its backups go only when you tick that too; nothing changes on the device or on your Garmin account. From the
command line: `openexpress devices --forget N`.

## Application window and background sync

```bash
openexpress gui                       # application window
openexpress install-desktop --apply   # "openExpress" menu entry and background service
systemctl --user daemon-reload
systemctl --user enable --now openexpress-watch.service
```

This is for the version from source. In the Flatpak, turn on **Sync in the background** in
Preferences, or **Sync automatically when connected** on the device page; `install-desktop`
does nothing there.

The background service syncs the watch every time it is connected and shows a notification.
Until you log in it does nothing, and until the device's first sync (in the window, or with
`openexpress baseline --apply` or `openexpress sync --apply --all`) it only shows a
notification asking for it. The window and the service
never sync at the same time (shared lock). A watch that is not registered to the signed-in
account only gets a notification.

The background service leaves devices in mass storage mode alone (such as the Varia RTL515):
it neither opens nor syncs them, and shows no notification about them. It syncs only when,
apart from those, exactly one Garmin device is connected; with two or more it writes a line
in the [log](troubleshooting.md#log-file) and does nothing. A watch and a Varia: the watch is synced.

<p align="center">
  <img src="screenshots/device.png" width="700"
       alt="The device page of a fēnix 8: an update available under its version, storage, the last sync and the updates">
</p>

## Software updates

The Updates page (and `openexpress updates`) asks Garmin's update server what is new for the
connected device; no login is needed.

- **Status:** the top of the page says what is waiting: the new system version (with the
  installed one beside it), the name of the only update, how many updates there are, an update
  copied already that waits for the device, or that the software is up to date.
- **System updates are recognised by their file** (`GUPDATE.GSP`, `GUPDATE.GCD`) as well as
  by what the server says: the server does not always mark them.
- **Beta versions** that Garmin offers to a device in its beta program are marked *Beta* in
  the window (for a system update at the top of the page and in the question) and `[BETA]`
  on the command line.
- **Before a system update** is copied, the window and `openexpress updates --install` warn
  to charge the device and not to disconnect it while copying. The question in the window
  shows both versions and links to the license (EULA).
- **Devices that lose their data on an update:** before every update of a device that Garmin
  files as one of these kinds (Wellness and Vivomove), the window and `openexpress updates
  --install` warn that the update erases the data stored on it and say to sync it first. The
  update is not held back. No such device has been tried yet.
- **After a system update is copied** the device installs it when it is disconnected. Until
  then the page says *Update ready: disconnect the device to install it* (and how many more
  updates are available, if any), its tile *Unplug to install* (*Eject to install* for a USB
  drive), and `openexpress updates` says that the update waits for the device. A copied
  component is listed as already on the device; the device takes it in when it is
  disconnected too.
- **A device that connects as a USB drive** is ejected first: after the copy the window
  asks to eject it (`openexpress updates --install` ejects it by itself), and says that the
  cable can be unplugged only once that has worked. Some of these devices install an update
  only after a clean eject. Until it is unplugged, the window does not open a drive it
  ejected again.

<p align="center">
  <img src="screenshots/updates-beta.png" width="700"
       alt="The Updates page: a beta system update offered for an Edge 1050">
</p>

Installing a system update was tested on an Edge 1050 (beta 33.12) and
reported working on a fēnix 7 ([#6](https://github.com/TomaszBojanowski/openExpress/issues/6)); component updates were tested on a Venu 2.

<p align="center">
  <img src="screenshots/update-confirm.png" width="700"
       alt="The question before a system update: versions, license link and warning">
</p>

## Maps of your own

Besides the maps Garmin offers, a device with maps also takes map files made by others, for
example from OpenStreetMap data (such as the Garmin `.img` downloads of BBBike.org).
openExpress lists these files on the Maps page and in `openexpress maps`, with the name that
is stored in the file.

- **Adding:** *Add Map…* on the Maps page, or `openexpress maps add FILE.img --apply`. The
  file keeps its name on the device; the name may have letters, digits, spaces and
  `. _ - ( )`, must start with a letter or digit and end in `.img`. `--name` gives it another
  one; in the window, rename the file first.
- **Nothing is overwritten:** a file that is already on the device is never replaced, and
  names that Garmin's own maps use are refused. Garmin's maps cannot be replaced or removed
  this way.
- **Removing:** the remove button on the Maps page, or
  `openexpress maps remove --own NAME --apply` for a map this installation copied. A map
  that was copied by hand or by another installation
  ([each keeps its own list](install.md#the-flatpak-and-the-version-from-source)) can be
  removed in the window after an extra question.
- **Where it works:** on devices that have a folder for maps (e.g. the fēnix 8), not on one
  in mass storage mode; a Venu 2 has none. Tested on a fēnix 8 with a map from BBBike.org,
  and reported working on a fēnix 7 ([#6](https://github.com/TomaszBojanowski/openExpress/issues/6)). openExpress checks that the file
  is a Garmin map file, not what is in the map.

<p align="center">
  <img src="screenshots/maps.png" width="700" alt="Maps, with a map of your own">
</p>

## Connect IQ apps

- Some watches (e.g. the fēnix 8) keep their app folder out of the ordinary file listing.
  On such a device `apps install`, `apps update`, `apps remove` and the buttons in the window
  talk to it over a direct USB connection; the device disappears from the file manager for a
  moment and is mounted again afterwards. The device finishes the change after it is
  unplugged; until then the app is shown as waiting. A sync never does this: it leaves such
  apps in the queue. In the Flatpak this needs its USB permission (see
  [Flatpak permissions](../README.md#flatpak-permissions)).
- The apps already on the device keep their order and their on/off state: the app list is
  written back exactly as the device reported it, with only the requested change.
- Removing an app deletes its file and its settings file, on every device; the data the
  app saved on the device stays.
- Before every change to the apps (install, update, remove, enable/disable, order) a copy of
  the device's app list is saved next to the backups, in a folder named `GARMIN-APPS <date>`.
  Nothing is changed if the copy cannot be made. No new copy is made while the apps and their
  order are those of the newest copy; of copies with the same apps in the same order only the
  newest is kept, and the 10 newest copies of a device are kept: a new one takes the place of
  the oldest (full backups keep their newest 3 the same way).
- `openexpress apps order` (in the window: **Restore the order from a copy** on the Apps
  page, which lists only the copies in another order than now) lists these copies, each with
  how many apps would have to be moved, and puts the apps and widgets back in the order of
  the chosen one, for example after another program rearranged the widgets. It only reorders: the same
  apps, the same settings.
- An app that already waits in Garmin's queue for the device is not requested a second time.
- **Remove and forget** in the window (`apps remove --forget`) also removes the app from your
  Garmin account's app list; **Remove** leaves it there.
- A device that has no app list yet (`GARMIN/APPS/OUT.BIN`, before its first app) shows *No
  Connect IQ apps on the device*; its apps are only queued in Garmin Connect ([#14](https://github.com/TomaszBojanowski/openExpress/issues/14)).
- An app whose version in the store asks for other permissions than before is installed or
  updated only once you allow them: the window lists them and asks, and `openexpress apps
  install` / `apps update` need `--allow-permissions`. A sync installs only what already
  waits in the queue, so it asks nothing.
- When a device refuses to create an app folder, it is not asked again until its software
  changes; the Apps page says so in one line, and apps that go there stay queued in Garmin
  Connect.

<p align="center">
  <img src="screenshots/apps.png" width="700" alt="Connect IQ apps">
</p>

## Music

The Music page (*Files…*, *Folders…*, *Add Playlist…*) and `openexpress music` copy music
(as Artist/Album), podcasts and audiobooks (by album) to the folders the device declares,
and write playlists as `.m3u8`. Files already on the device with the same size are skipped;
before copying, openExpress says which files on the device will be replaced. Files and
playlists can be removed (the trash button, `openexpress music remove`). In the Flatpak, the
tracks of a playlist can be read only from the Music and Downloads folders. Tested on a
fēnix 8.

<p align="center">
  <img src="screenshots/music.png" width="700" alt="Music">
</p>

## Sync details

- The watch must be registered to the Garmin account you are signed in to. Before every sync
  and every Connect IQ operation the account's device list is fetched from Garmin Connect;
  if the watch is not on it, or the list cannot be fetched, nothing is uploaded and nothing
  is written to the watch. `openexpress baseline` needs a login too. Features
  without the account (backup, software updates, maps, music) work with any watch.
- **TrueUp** (`FIT_TYPE_39`): when you own several Garmin devices, Connect asks the client to
  check back after a delay and then sends adjusted daily totals, which are written to the
  watch. As in Garmin Express, openExpress waits the whole delay when it is under five
  minutes and 90 s otherwise, at most three times; the window and the command line show how
  much of the wait has passed. **Stop Waiting** in the window, or Ctrl+C on the command line
  during the wait, stops it: the sync ends as usual,
  nothing of TrueUp is written, and it comes with the next sync.
- **Connect IQ** apps queued for the watch (e.g. chosen in the store on your phone) are
  installed during sync, except on devices that hide their app folder, where they stay
  queued ([Connect IQ apps](#connect-iq-apps)). New watch faces are installed but not
  activated, like
  in Garmin Express.
- **Last sync** on the Device page, `openexpress sync` and the notification of the
  background sync use the same words for how many new files the device had, how many were
  uploaded and how many came from Garmin Connect; the row on the Device page also says when
  it was. On the Device page it ends
  with how many new activities the connected device has to upload, and says *Never synced*
  for a device this installation has not synced yet, as its tile does. The details name the new
  files by their type (activities, sleep, HRV status, device log…; others as "other data"),
  and the files from Garmin Connect too (TrueUp and Connect IQ apps have lines of their own),
  say how many Garmin Connect already had, and warn when Garmin rejected a file, a file could
  not be uploaded, or another step failed (a file from Garmin Connect, a Connect IQ app,
  TrueUp).

## Configuration

### Languages

The interface is available in English and Polish. The language follows the system
locale; you can override it:

- in the window: main menu (☰) → Preferences → Language,
- for one command: `openexpress --lang pl …` or `OPENEXPRESS_LANG=pl`,
- permanently: `openexpress setup --ui-language pl` (`system` restores the default).

Garmin's sign-in page and the answers of its servers use the same language; `openexpress
setup --locale de_DE` sets another one for them.

### Cache

Downloaded maps and software updates, device pictures and app icons, and files on their way to
the device are kept in `~/.cache/openexpress` (the Flatpak: `~/.var/app/io.github.tomaszbojanowski.OpenExpress/cache/openexpress`;
each installation has its own), in four parts: `maps`, `updates`, `pictures` and `temp`.
`openexpress cache` shows how big each part is, `openexpress cache --clear --apply` empties all of
them except the pictures of the devices openExpress remembers (`--only pictures` deletes those
too) and `openexpress cache --clear --only maps,updates --apply` only those named (without
`--apply` it says what it would free). In the window, Preferences → Storage has a row for each
part with a trash button, and **Clear All…**; maps, updates and **Clear All…** ask first. Maps are then downloaded
again when they are installed or updated. Nothing is cleared while a download, an install or a
copy to the device is running.
