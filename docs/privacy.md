# Privacy and security

Garmin Express exists only for Windows and macOS. openExpress is an independent implementation for
Linux with its own code: how the Garmin services and the device's files work was learned by
analysing the official application, solely to interoperate with them. It contains no Garmin code;
the application key, its secret and the value that signs download addresses are read from **your
own copy** of the official installer during setup.

openExpress does contain the names by which Garmin Express introduces itself to Garmin's servers:
its client identifiers and the values of its client headers (see
[How openExpress identifies itself](#how-openexpress-identifies-itself)).

## Signing in

- The password is entered only on Garmin's page; openExpress never sees or stores it.
- After signing in, openExpress stores an OAuth1 token and, next to it, your user name, e-mail
  address, profile number and account identifier. They are in one file readable only by you
  (permissions 0600): `~/.config/openexpress/connect_auth.json`, in the Flatpak
  `~/.var/app/io.github.tomaszbojanowski.OpenExpress/config/openexpress/connect_auth.json`.
- `openexpress logout` removes that file.

## What is kept of each device

Besides the login, this installation keeps what it knows of each device in
`~/.local/share/openexpress` (in the Flatpak
`~/.var/app/io.github.tomaszbojanowski.OpenExpress/data/openexpress`), readable only by you:

- the devices it has seen: name, part number, Unit ID, software version, what the last update
  check found, and the updates copied to the device that Garmin's server still offers (part
  number, version and when);
- the names of the files already uploaded, and the last sync;
- copies of files whose upload failed, until they are uploaded;
- the list of your own maps;
- a short log of each connection (`history.jsonl`): the software version, the storage by kind of
  file, the number and size of memory dumps, the crashes the error logs held (kind, time, error,
  where the software stopped, screen, which of the two logs), how many error logs it has and how
  many entries they held, and whether the device has hidden crash logs, without file names,
  kept whatever **Keep Diagnostic Files** says.

**Forget Device** removes what it keeps of a device. The Flatpak and the version from source
each keep their own files; what each sees of the other is described in
[The Flatpak and the version from source](install.md#the-flatpak-and-the-version-from-source).

## Adding a device to the account

**Add to Account** (experimental) sends Garmin Connect the device's `GarminDevice.xml` (its
model, software and Unit ID), only after you confirm, as Garmin Express does. Garmin's answer
carries the device's own Garmin Connect credentials, for its uploads over Wi-Fi. openExpress
keeps only whether the device was added and its status, never those credentials: not in a file,
the log or the device.

## Diagnostics

openExpress has no telemetry: unlike Garmin Express, it sends nothing to Microsoft Application
Insights. The device's diagnostics (error logs, KPIs, memory dumps, behaviour log) are uploaded
only with `openexpress sync --include-diagnostics`, which takes them from the device itself.
Otherwise the device's diagnostic files stay on this computer:

- Once per connection, when a device is opened, its error logs (`Garmin/Debug/err_log.*`) are
  read: by the window, to show a recent error, and by whatever opened the device first (the
  window, the command line or the background sync), for the log of each connection (above). Of
  each entry only the kind, the time, the software version, the error number, where the software
  stopped and the screen are used, never the Unit ID or the backtrace in it.
- The device report (`openexpress report`, **Report a Device or Problem…**) lists the device's
  last crashes (date, software version and error number) and its memory dumps; never where the
  software stopped, the screen, the Unit ID or a serial number.
- The window remembers where it found the error logs and the device's other diagnostic files, so
  that the next openings read them there without looking through the device's folders; they list
  only the folder of the error logs and that of the memory dumps. It looks through them again when
  one of the files is gone, an error log or a memory dump was written since, or the device has
  other software; only then does it look again for what was not there. This is kept in
  `diagnostic-places.json` in the device's folder (below): the paths, the device's software
  version and whether its event logs are visible, nothing of the files themselves.
- With Preferences → **Diagnostics** → **Keep Diagnostic Files** turned on (off by default;
  `openexpress setup --diagnostics-copy on`), the window also copies the diagnostic files the
  device shows: `Garmin/Debug`, the folders of its diagnostic data types and its event logs. It
  copies them once per connection, before anything is written to the device, through the
  ordinary connection. Health metrics (`MetricsBackup`), settings (`Backup`), memory dumps and
  files of more than 4 MB are only listed.
- Only when you ask: the hidden crash logs of a fēnix 8 (its boot log and event logs) are read
  with **Copy Crash Logs** or `openexpress diagnostics --crash-logs --apply`, in a session of
  openExpress's own; the memory dumps (encrypted by Garmin) are copied with **Copy Memory Dumps**
  or `openexpress diagnostics --dumps --apply`.

- Before older memory dumps are removed from the device (**Free Up Space** → **Remove** on the
  Device Status page, `openexpress diagnostics --free-space --apply`), those
  that fit are copied to this computer, whatever **Keep Diagnostic Files** says, and each copy is
  checked first: at most 100 MB per device, apart from the copies above, the oldest removal
  dropped first. A dump too large for that is removed without a copy, and the question says so.

The copies are in the device's folder of openExpress's data (`~/.local/share/openexpress/devices/`,
in the Flatpak `~/.var/app/io.github.tomaszbojanowski.OpenExpress/data/openexpress/devices/`),
readable only by you: at most 30 copies and 50 MB per device for the error and crash logs, as
many for the memory dumps of Copy Memory Dumps, the oldest removed first, and the
copies of removed dumps in `diagnostics/removed/` (those of a test drive, as all its copies, in
`test-drive/diagnostics/`, with limits of their own). **Forget Device** removes them. These copies
are never sent.

## Activities

- The **Activities** page and `openexpress activities` read the activity files of the device and
  of its backups only when you open the page or run the command. What they say (sport, start,
  time, distance, ascent) is kept in memory while the device is open, never on disk.
- A GPX file is written only where you save it. It holds the positions, elevation and time of
  the activity and nothing of the device.
- The dry run of `openexpress sync` downloads the files waiting in Garmin Connect into memory to
  describe them and keeps nothing.
- The log says how many files were read and how large they were, never a file name, a date, a
  sport or a place.

## Activity maps from OpenStreetMap

The page of an activity with positions draws its track on a plain background. Streets and
terrain under it come from the tile server of OpenStreetMap (`tile.openstreetmap.org`) only after
you turn them on, with **Show the Map** on such a page or in Preferences → **Activity Maps** (off
until then; it can be turned off there at any time). From then on:

- Each map you look at asks that server for the tiles of the area it shows, and for more as you
  move or zoom it.
- The server, and the network on the way, learn that area (often near where you live or were),
  when it was asked and your IP address.
- Each request names the program, its version and its address
  (`User-Agent: openExpress/<version> (+https://github.com/TomaszBojanowski/openExpress)`). It
  never carries the activity, its track, the device or your account.
- The tiles are kept in the cache for as long as the server allows, at most 100 MB
  (`~/.cache/openexpress/tiles`, in the Flatpak
  `~/.var/app/io.github.tomaszbojanowski.OpenExpress/cache/openexpress/tiles`). The row
  **Activity Map Tiles** in Preferences → **Storage**, or
  `openexpress cache --clear --only tiles --apply`, deletes them.

The map data is © OpenStreetMap contributors, under the Open Database License.

## Files on the device

- Sync never deletes files from the device.
- Files are deleted only when you ask for it: by `maps install/update/remove` (old map files
  indicated by the server), `maps remove --own`, `music remove` and `apps remove`. On the command
  line this happens only with `--apply`; without it, a dry run lists the files.
- Before writing a software update, openExpress deletes a file of the same name in another letter
  case and, for a system update, a leftover `GUPDATE.TMP` in its folder. It removes a copy that
  failed its check.
- Files shared by several maps (e.g. the Europe terrain model) are protected.
- When Garmin's map service asks for old files to be cleaned up (`directoriesToRemove`), the
  confirmation dialog before a map is installed, updated or removed and
  `openexpress maps install/update/remove` list the files that would match (whole names, never a
  map of the answer or a map of your own). openExpress does not remove them yet.
- A write to the device that fails leaves no half-written file: what was written of it is
  deleted, and when the copy did not start, the file that was there stays. On a device in mass
  storage mode the new file is written beside the old one and only then takes its place.
- After such a failure the list of apps waiting for the device (`IN.BIN`) is put back as it was,
  or, if even that fails, deleted, and the message says that the changes are lost.
- Software updates and maps: the downloaded files are verified (size, MD5) and copied to the
  device only after all files have been downloaded. The RGN format (older devices) is rejected.

## Uploads

- Uploads work as in Garmin Express: activities that Garmin Connect already has (e.g. from the
  phone) are detected and skipped; duplicates are rejected by the server.
- Nothing is uploaded unless the Garmin account has given the consent to upload data from devices
  and has device sync turned on; both are checked before every sync. Files from Garmin Connect are
  still written to the device.
- A data type one of whose upload locations needs a further consent is not uploaded at all, to
  any of its locations, until that consent is given (as in Garmin Express).

## What Garmin is asked about a device

Garmin's update server describes a device in one request. Like the update check, that request
names the device by its Unit ID. Your Garmin Connect account is asked about a device that is not
on it only when you click **Get Picture From Account**; nothing else asks the account about such
a device.

| Request | When | What it sends | What is kept |
|---|---|---|---|
| Garmin's update server: which map service serves the device, and its picture | once each time a device is opened, and again when its maps are checked again by hand | the device's Unit ID | the answer's fields listed below |
| The same request, for what the server says of an open device in mass storage mode whose kind Garmin has not told | once each time openExpress runs | the device's Unit ID | the answer's fields listed below |
| The same request, for whether Garmin Connect takes the data of a watch connected as a USB drive | once each time openExpress runs, before the watch is synced | the device's Unit ID | the answer's fields listed below |
| Your Garmin Connect account: **Get Picture From Account**, offered for a device not on your account when the update server gives no picture of it | only when you click it | the device's Unit ID, to your account | the picture in the cache, its address with the device |

Of the update server's answer:

- The picture comes from it for a device that is not on the signed-in account, or for any device
  when nobody is signed in.
- Only what the program reads is kept, for seven days, in memory and in the cache (`answers`,
  see [How long Garmin's answers are kept](#how-long-garmins-answers-are-kept)): the map service,
  the address of the model's picture, the market segment, the model's name and the connect
  support. Never the Unit ID or the serial number.
- The picture itself is cached.
- Three things are kept with the devices this installation remembers: the address of the model's
  picture (so that the picture can be downloaded again after the cache is cleared), Garmin's name
  of the model when the device gives it in capitals only, and whether the device is a bike
  accessory.
- The market segment also tells whether an update erases the device's data.
- The log names only the map update type (which map service Garmin uses for the model) and, for
  a device whose kind Garmin does not tell otherwise, the market segment and the connect support,
  always with the part number only, as it names the account's categories of such a device.

## How long Garmin's answers are kept

What Garmin's servers answered is kept for a while, so that the program does not ask them more
often than Garmin Express does. It is kept in memory and in the cache (`answers`, a folder and
files only you can read), each answer for its own time:

| Answer | Kept for | Where | What is kept |
|---|---|---|---|
| The account's device list | 5 minutes | memory and cache | each device's Unit ID and Garmin Connect ID (they tell whether a device is on the account), its model, part number, software version, picture address and what the device can be |
| The account's primary training device | 5 minutes | memory and cache | only its ID |
| The software and map offers for a device | one hour | memory and cache | the offers, by the device's `GarminDevice.xml`, as Garmin Express keeps them |
| What Garmin's update server says of a device (unit-info) | seven days | memory and cache | see [What Garmin is asked about a device](#what-garmin-is-asked-about-a-device) |
| The account's country | one hour | memory only | |
| The Connect IQ Store's list of the device's apps | two minutes | memory only | |

- Of the device list and the primary training device, nothing else is kept: never a name you gave
  a device, a serial number, a date or anything of your profile.
- **Refresh**, **Check Again**, an install, a change of a primary device and another login make
  the program ask again.
- Uploads, the consents, the queue of Garmin Connect and every change to the account are always
  asked anew.
- Signing out deletes what is kept of the account, on disk too.
- Answers out of date leave the disk when the program starts.
- `openexpress cache --clear --only answers --apply`, or the row **Garmin Server Responses** in
  Preferences → **Storage**, deletes what is kept. Nothing of it is ever sent anywhere.
- The window and the command line name each device of the account by its product name, never by
  a name you gave it in Garmin Connect.

## How openExpress identifies itself

- Every request to Garmin's servers carries the header `User-Agent: openexpress/<version> (Linux)`;
  the sign-in window is a web browser with its own.
- openExpress uses the Garmin Express client headers so that the servers answer, but it does not
  hide that it is a different program. The `Garmin-Client-*` headers have the values of Garmin
  Express 7.29.1.0 for Windows (name, version, platform and its version), with a random session
  ID and the language of the program.
- The request for the upload configuration also carries a random ID made once for this
  installation (`client_guid` in `settings.json`).
- The values can be changed in `~/.config/openexpress/settings.json` (in the Flatpak
  `~/.var/app/io.github.tomaszbojanowski.OpenExpress/config/openexpress/settings.json`).

## Flatpak permissions

The Flatpak has network access, the usual permissions of a window (Wayland, X11 as a fallback,
the GPU) and access to:

- devices, through GNOME's gvfs (`--talk-name=org.gtk.vfs.*`, `xdg-run/gvfsd`);
- your Downloads and Music folders, read-only (`xdg-download:ro`, `xdg-music:ro`): the installer
  and music on the command line;
- the folder Documents/Garmin, for backups and the GPX files of `activities export`
  (`xdg-documents/Garmin:create`, created if missing).

Besides these, it has three permissions that weaken the sandbox:

1. It can run commands on the host (`org.freedesktop.Flatpak`).
   - It uses this to copy files to the device with `gio copy`, because gvfs does not let
     sandboxed apps write to MTP devices, and to read which repository the Flatpak was installed
     from (`flatpak info --show-origin`, for the device report and the log; it changes nothing).
   - If you prefer not to grant it, use the [version from source](install.md#from-source):
     writing to the device then works without it.
2. It can open USB devices (`--device=usb`: the nodes in `/dev/bus/usb`, nothing else of `/dev`).
   - It uses this only for two things that need a direct USB connection: Connect IQ apps on
     devices that hide their app folder (e.g. the fēnix 8: installing, updating and removing
     them), and reading the hidden crash logs of such a device when you ask. The device
     disappears from the file manager for a moment while it is used.
   - To turn it off: `flatpak override --user --nodevice=usb io.github.tomaszbojanowski.OpenExpress`.
     A Flatpak older than 1.16 does not know this permission and runs as without it.
   - Without it, on devices that hide their app folder, installs and updates are only queued in
     Garmin Connect, from where the phone or Garmin Express installs them; apps are removed on the
     phone; nothing is written to the device; and the hidden crash logs cannot be read.
3. It can read and write the drives the system mounts under `/run/media` and `/media`
   (`--filesystem=/run/media`, `--filesystem=/media`): every removable drive mounted there (USB
   sticks, memory cards, external disks), not only Garmin devices.
   - It uses this only for Garmin devices in mass storage mode (e.g. the Varia RTL515), which
     connect as a drive.
   - To turn it off:
     `flatpak override --user --nofilesystem=/run/media --nofilesystem=/media io.github.tomaszbojanowski.OpenExpress`.
     The window then says that it has no access to the drive, and devices in mass storage mode
     are left to the version from source.
