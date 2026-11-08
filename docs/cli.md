# Command line

The command-line tool is available as
`flatpak run io.github.tomaszbojanowski.OpenExpress <command>` (for example `sync` or `maps`);
in the version from source it is `openexpress <command>`.

| Command | Description |
|---|---|
| `openexpress info [--json]` | model, part number, software version, unit ID, storage and free space, data types and components, Connect IQ limits; `--json` as JSON |
| `openexpress files [--type FIT_TYPE_4] [--limit N]` | files on the watch by data type from `GarminDevice.xml`; with `--type` (or `-v`) each file, `--limit N` only the last N |
| `openexpress activities [--limit N \| --all] [--backups DIR]` | the activities of the connected device (also one connected as a USB drive) and of its backups, the newest first: date, sport, time (pauses left out), distance, ascent, source (the device, or the backup of a date) and file name; those only in a backup come after the device's, and one the device still has is read from it; the newest 30, `--limit N` the newest N, `--all` every one; backups from `Garmin/Backups` in your Documents folder, or `--backups DIR`; a file the device was still writing, a damaged file and one that cannot be read say so; nothing is written; the page of an activity (its map, charts, laps, zones and details) is in the window only |
| `openexpress activities export NAME… [--dest DIR] [--backups DIR] [--apply]` | write activities as GPX 1.1 files: NAME is the file name the list shows (from the device, or from a backup when only a backup has it); each point with its position, elevation and time in UTC, nothing else (no heart rate, no device, no serial number); the file is named `YYYY-MM-DD HH.MM Sport.gpx` (the start in local time) and goes to `Garmin/GPX` in your Documents folder or `--dest DIR`; a dry run names the files, `--apply` writes them; a file that is there is never overwritten; nothing is written unless every activity named can be: one without GPS positions, one the device was still writing, a name not found or a file that is there make the command stop with exit code 1 and say which; in the Flatpak `--dest` must be within `Garmin` in your Documents folder |
| `openexpress fit inspect FILE` | what a FIT file on this computer holds: its data type, manufacturer, product and creation time, its messages and how many of each, whether it has developer data, and whether it is whole (a file cut short says how many bytes were read and how many its header declares); never the device's serial number; exit code 0 only for a whole FIT file; in the Flatpak it reaches only `Garmin` in your Documents folder and, to read, Downloads and Music |
| `openexpress report [--log]` | device report for GitHub, without serial numbers, unit IDs or e-mail; when no device can be opened, a report of what is connected over USB and of the system (exit code 0); `--log` prints the last 200 lines of the [log](troubleshooting.md#log-file) instead, filtered, for the form's “Log” field |
| `openexpress diagnostics [--crash-logs] [--dumps] [--apply]` | what the device logged as errors, its memory dumps and the copies of its diagnostic files kept on this computer; `--apply` copies the files it shows now; with it, `--crash-logs` also reads its hidden crash logs (the device leaves the file manager for a moment) and `--dumps` also copies its memory dumps; what is read, copied and kept, and that nothing is sent: [Privacy and security](privacy.md#privacy-and-security) |
| `openexpress status [--online]` | the sign-in, the Garmin devices connected (name, USB mode, whether they are on the account), the last sync of each with the activities it has to upload now, and what the window's last check of its updates found; `--online` also asks Garmin Connect and Garmin's update server now (nothing of the answers is kept); without it nothing is asked of a server |
| `openexpress devices [--forget N] [--discard-pending] [--backups] [--apply]` | the devices this installation has seen; `--forget N` forgets device number N of the list and what this installation keeps of it, as **Forget Device…** does ([what that is](guide.md#the-start-page-and-the-device-page)); files not uploaded yet go only with `--discard-pending`, its backups only with `--backups`; a connected device is not forgotten |
| `openexpress setup --from-installer GarminExpress.exe` | one-time setup from the Garmin Express installer for Windows (only read, nothing installed); instead of it `--from-dll Networking.dll [--utilities-dll Utilities.dll]` or `--key KEY --secret SECRET` |
| `openexpress setup [--ui-language en\|pl\|system] [--diagnostics-copy on\|off] [--locale LOCALE] [--environment production\|china]` | settings: the program's language, the copy of the device's diagnostic files, the language asked of Garmin's servers (by default the program's), Garmin's servers; without options it shows them |
| `openexpress login [--manual]` | sign in on the Garmin SSO page (WebKitGTK window) |
| `openexpress whoami [--check]`, `logout` | account (its name masked: a user name as `t…b`, an e-mail address as `t…@e…`, as in every command's output), sign out (removes the local token) |
| `openexpress upload-config [--json]` | what data is uploaded for this watch and where (Garmin server) |
| `openexpress sync [--all] [--upload-only] [--download-only] [--type TYPE]… [--include-diagnostics] [--apply]` | uploads new FIT files to Connect, courses and workouts from Connect to the watch; `--all` uploads everything on the first sync, `--upload-only` and `--download-only` do one direction, `--type` (repeatable) uploads only the data types named (files from Garmin Connect are still written), `--include-diagnostics` also the device's diagnostics (see [Privacy and security](privacy.md#privacy-and-security)); a watch connected as a USB drive the same, when it can be synced (else the command says why), its Connect IQ apps left in Garmin Connect's queue, and the drive ejected after a sync that wrote to it |
| `openexpress baseline [--apply]` | marks current files as already uploaded (first run) |
| `openexpress backup [--dest DIR] [--keep N] [--apply]` | local backup of watch data (default `Garmin/Backups` in your Documents folder, or `--dest`; the newest 3 kept, or `--keep N`); a FIT file the device was still writing when it was connected is left out, and the summary says so |
| `openexpress cache [--clear] [--only maps,updates] [--apply]` | size of the cache by part (maps, updates, pictures, answers, tiles, temp) and clearing it (see [Cache](guide.md#cache)) |
| `openexpress updates [--reinstalls]` | watch software updates (no login needed); `--reinstalls` also lists what can be installed again; a file the watch already has with the same size and MD5 is not counted as an update, only listed as "already on the device" |
| `openexpress updates --install PART…\|all [--apply]` | download with MD5 check and copy to the watch; after it is unplugged the watch may ask whether to install the update now or later, then restart |
| `openexpress maps [--all]` | installed maps and maps of your own; `--all` also those available for download (TopoActive, base maps, CourseView, Ski) |
| `openexpress maps install NAME [--apply]` | install a map assigned to the watch (download, activation, write) |
| `openexpress maps update [--apply]`, `maps remove NAME [--apply]` | update and remove maps |
| `openexpress maps add FILE.img [--name NAME] [--apply]` | copy a map of your own (e.g. made from OpenStreetMap data) to the device; never overwrites a file |
| `openexpress maps remove --own NAME [--apply]` | remove a map of your own that was copied with `maps add` |
| `openexpress apps [--builtin]` | Connect IQ apps on the watch and in your Garmin account, available updates |
| `openexpress apps install NAME [--allow-permissions] [--apply]` | install an app from your Garmin account; `--allow-permissions` allows the new permissions it asks for |
| `openexpress apps update [--allow-permissions] [--apply]` | update every installed app that has an update |
| `openexpress apps remove NAME [--forget] [--apply]` | remove an app; `--forget` also from your Garmin account's app list |
| `openexpress apps enable\|disable NAME [--type TYPE] [--apply]` | turn an app or a built-in activity on or off; `--type` (app, widget, watch-face, data-field, music-app, activity) picks the entry when two share a name |
| `openexpress apps order [--from COPY] [--all] [--apply]` | put apps and widgets back in the order of a saved copy of the app list; without `--from` it lists the copies with how many apps each would move, `--all` lists every position that would change |
| `openexpress primary [--wearable]`, `primary set [--wearable] [--apply]` | the account's primary training device, with `--wearable` its primary wearable (steps and sleep); `set` makes the connected device the primary training device (the main source of performance and training data in Garmin Connect, as `set` says first), `set --wearable` the primary wearable |
| `openexpress courses` | **experimental**: the courses on the connected device (each with its name, where it came from – openExpress or Garmin Connect, when that is known –, its distance and ascent, and the day its file was made – for a course from Garmin Connect the day Connect made the file, when it sent the course, not the day the course was created there), the GPX files copied to it that it has not turned into courses yet (it does when it is unplugged), and how many saved locations it keeps; only for a device that takes GPX files in `Garmin/NewFiles`; nothing is written |
| `openexpress courses add FILE.gpx [--name COURSE] [--apply]` | **experimental**: copy a GPX file to `Garmin/NewFiles`, where the device turns each of its tracks and routes into a course and its waypoints into saved locations when it is unplugged; the dry run says the course's name, the file's name on the device, what the file holds, what the device does with it (a route's points are joined with straight lines, not roads) and the space, `--apply` copies it; `--name` names the course (only for a file with one track or route: the name is written into the copy, nothing else of the file changes); a file on the device is never overwritten; in the Flatpak FILE must be in Downloads or in `Garmin` in your Documents folder |
| `openexpress courses remove NAME [--apply]` | **experimental**: remove one course or one GPX file waiting for the device, by its name or its file's, or by a part of it that only one has (several matches are listed and nothing is removed); the dry run says what is lost: the device keeps no other copy of a course, Garmin Connect can send its own again |
| `openexpress register [--apply]` | **experimental**: add the connected device to the Garmin account you are signed in to, as an active device, as Garmin Express adds one (for a device the account does not list, for example one registered only as a product on garmin.com); the trial run says what would be sent (its `GarminDevice.xml`, which holds its unit ID) and to which account (its name masked, as every command shows it); `--apply` sends it, makes active a device Garmin keeps as retired, and reads the account's list again to check; nothing is written to the device, and the device's own Garmin Connect credentials in Garmin's answer are not kept; a device is removed from an account only in Garmin Connect |
| `openexpress music` | music, podcasts and audiobooks on the device, playlists, free space |
| `openexpress music add PATH… [--as track\|podcast\|audiobook] [--playlist NAME] [--apply]` | copy files (music as Artist/Album, podcasts and audiobooks by album); `--playlist` also makes a playlist of them |
| `openexpress music playlist FILE.m3u [--name NAME] [--apply]`, `music remove TEXT… [--apply]` | write a playlist as `.m3u8`; remove files whose name, artist or album folder matches TEXT (at least 3 characters) |
| `openexpress gui` | application window (GTK 4 + libadwaita): watch, sync, updates, maps, apps, music, activities |
| `openexpress watch` | background service: sync when the watch is connected, with a notification |
| `openexpress install-desktop [--remove] [--force] [--apply]` | application menu entry and systemd user service (version from source; in the Flatpak it does nothing); `--remove` removes them, `--force` installs them also when the Flatpak is installed |

Before the command: `--lang LANG` (the language of this run), `-v` (more detail), `--no-wait`
(see below), `--device-uri URI` (one MTP device when several are connected), `--path DIR` (a
folder with a copy of a device's files instead of a device) and `--version`. `--no-wait`,
`--device-uri` and `--path` also go after a command that opens a device
(`openexpress sync --device-uri URI`). `openexpress` without a command opens the window.

All commands that change anything run **in dry-run mode by default**:
they show the plan and do nothing without `--apply`. The exceptions are `setup`, `login` and
`logout`, which change only openExpress's own configuration and login token, at once.

A Garmin device needs a moment to start after it is plugged in. Commands wait for it
(up to 45 seconds) and the window shows that the device is starting; `--no-wait` makes a
command fail at once instead.

While a sync, a backup or a write is running, the window shows **Do not unplug the device**
and the command-line tool prints the same warning when it starts such an operation.

Long tasks (a sync, updates, maps, apps, music, a backup, diagnostic files) say how far they are
in one line that updates in place, and the window in its bar at the bottom: what is done and to
what (an update, a map, an app, a type of data), never a file name or a song, for example
`Copying music · 3 of 25 · 120.0 MB of 1.2 GB`.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | done (also a dry run) |
| 1 | an error or a refusal: the device, the account, a server or the network, or a value the command checks itself (e.g. an unknown part in `cache --only`); the message says why; for `fit inspect`, a file that is not a whole FIT file (cut short, damaged, not FIT) or cannot be read |
| 2 | a wrong command line: an unknown command or option, a value that is not one of the choices or not a number (e.g. `setup --environment`, `devices --forget`), `--key` without `--secret`, or a missing argument |
| 3 | the first sync with this program waits for a choice: `openexpress baseline --apply` or `openexpress sync --apply --all` |
| 130 | interrupted (Ctrl+C); not while `sync` waits for TrueUp, which Ctrl+C only stops (the sync then ends as usual) |
