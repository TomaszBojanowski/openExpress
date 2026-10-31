# Command line

The command-line tool is available as
`flatpak run io.github.tomaszbojanowski.OpenExpress <command>` (for example `sync` or `maps`);
in the version from source it is `openexpress <command>`.

| Command | Description |
|---|---|
| `openexpress info [--json]` | model, part number, software version, unit ID, storage and free space, data types and components, Connect IQ limits; `--json` as JSON |
| `openexpress files [--type FIT_TYPE_4] [--limit N]` | files on the watch by data type from `GarminDevice.xml`; with `--type` (or `-v`) each file, `--limit N` only the last N |
| `openexpress report [--log]` | device report for GitHub, without serial numbers, unit IDs or e-mail; `--log` prints the last 200 lines of the [log](troubleshooting.md#log-file) instead, filtered, for the form's “Log” field |
| `openexpress diagnostics [--crash-logs] [--dumps] [--apply]` | what the device logged as errors, its memory dumps and the copies of its diagnostic files kept on this computer; `--apply` copies the files it shows now; with it, `--crash-logs` also reads its hidden crash logs (the device leaves the file manager for a moment) and `--dumps` also copies its memory dumps; what is read, copied and kept, and that nothing is sent: [Privacy and security](privacy.md#privacy-and-security) |
| `openexpress status [--online]` | the login, the Garmin devices connected (name, USB mode, whether they are on the account), the last sync of each and what the window's last check of its updates found; `--online` also asks Garmin Connect and Garmin's update server now (nothing of the answers is kept); without it nothing is asked of a server |
| `openexpress devices [--forget N] [--discard-pending] [--backups] [--apply]` | the devices this installation has seen; `--forget N` forgets device number N of the list and what this installation keeps of it, as **Forget Device…** does ([what that is](guide.md#the-start-page-and-the-device-page)); files not uploaded yet go only with `--discard-pending`, its backups only with `--backups`; a connected device is not forgotten |
| `openexpress setup --from-installer GarminExpress.exe` | one-time setup from the Garmin Express installer for Windows (only read, nothing installed); instead of it `--from-dll Networking.dll [--utilities-dll Utilities.dll]` or `--key KEY --secret SECRET` |
| `openexpress setup [--ui-language en\|pl\|system] [--diagnostics-copy on\|off] [--locale LOCALE] [--environment production\|china]` | settings: the program's language, the copy of the device's diagnostic files, the language asked of Garmin's servers (by default the program's), Garmin's servers; without options it shows them |
| `openexpress login [--manual]` | sign in on the Garmin SSO page (WebKitGTK window) |
| `openexpress whoami [--check]`, `logout` | account, log out (removes the local token) |
| `openexpress upload-config [--json]` | what data is uploaded for this watch and where (Garmin server) |
| `openexpress sync [--all] [--upload-only] [--download-only] [--type TYPE]… [--include-diagnostics] [--apply]` | uploads new FIT files to Connect, courses and workouts from Connect to the watch; `--all` uploads everything on the first sync, `--upload-only` and `--download-only` do one direction, `--type` (repeatable) uploads only the data types named (files from Garmin Connect are still written), `--include-diagnostics` also the device's diagnostics (see [Privacy and security](privacy.md#privacy-and-security)) |
| `openexpress baseline [--apply]` | marks current files as already uploaded (first run) |
| `openexpress backup [--dest DIR] [--keep N] [--apply]` | local backup of watch data (default `Garmin/Backups` in your Documents folder, or `--dest`; the newest 3 kept, or `--keep N`); a FIT file the device was still writing when it was connected is left out, and the summary says so |
| `openexpress cache [--clear] [--only maps,updates] [--apply]` | size of the cache by part (maps, updates, pictures, answers, temp) and clearing it (see [Cache](guide.md#cache)) |
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
| `openexpress primary [--wearable]`, `primary set [--wearable] [--apply]` | the account's primary training device, with `--wearable` its primary wearable (steps and sleep); `set` makes the connected device the primary training device, `set --wearable` the primary wearable |
| `openexpress music` | music, podcasts and audiobooks on the device, playlists, free space |
| `openexpress music add PATH… [--as track\|podcast\|audiobook] [--playlist NAME] [--apply]` | copy files (music as Artist/Album, podcasts and audiobooks by album); `--playlist` also makes a playlist of them |
| `openexpress music playlist FILE.m3u [--name NAME] [--apply]`, `music remove TEXT… [--apply]` | write a playlist as `.m3u8`; remove files whose name, artist or album folder matches TEXT (at least 3 characters) |
| `openexpress gui` | application window (GTK 4 + libadwaita): watch, sync, updates, maps, apps, music |
| `openexpress watch` | background service: sync when the watch is connected, with a notification |
| `openexpress install-desktop [--remove] [--force] [--apply]` | application menu entry and systemd user service (version from source; in the Flatpak it does nothing); `--remove` removes them, `--force` installs them also when the Flatpak is installed |

Before the command: `--lang LANG` (the language of this run), `-v` (more detail), `--no-wait`
(see below), `--device-uri URI` (one MTP device when several are connected), `--path DIR` (a
folder with a copy of a device's files instead of a device) and `--version`. `openexpress`
without a command opens the window.

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
| 1 | an error or a refusal: the device, the account, a server or the network, or a value the command checks itself (e.g. an unknown part in `cache --only`); the message says why |
| 2 | a wrong command line: an unknown command or option, a value that is not one of the choices or not a number (e.g. `setup --environment`, `devices --forget`), `--key` without `--secret`, or a missing argument |
| 3 | the first sync with this program waits for a choice: `openexpress baseline --apply` or `openexpress sync --apply --all` |
| 130 | interrupted (Ctrl+C); not while `sync` waits for TrueUp, which Ctrl+C only stops (the sync then ends as usual) |
