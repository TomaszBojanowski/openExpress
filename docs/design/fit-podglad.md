# Design: a look into FIT files (activities, GPX, the sync queue, `fit inspect`)

Status: implemented, each decision of section 4 with its recommended option (marked "chosen");
tried on a device over MTP, the preview of the sync queue included, not yet on one connected as
a USB drive. The scope in section 1 is closed: what it does not list is not added.

## 1. Scope

In scope, four parts:

1. Activities: an Activities page in the window and `openexpress activities` on the command
   line: the activities of the connected device (MTP or USB drive) and of its backups, with
   date, sport, time, distance, ascent and source.
2. GPX export of an activity, in the window and on the command line.
3. A preview of the sync queue: what a sync would write to the device from Garmin Connect
   (workouts, courses, Garmin Coach plans, schedules): which file, its sport and dates, and the
   result of the check the sync and the backup already make (`fit.not_whole`: header, declared
   size, CRC).
4. `openexpress fit inspect FILE`: type, manufacturer, product, creation time, the messages and
   how many of each, the result of the check, and where a file is cut short.

Not in scope (a closed list; each would be a project of its own):

- charts, maps, laps, heart rate zones, splits, records; sub-sports; statute units;
- other exports (TCX, CSV, a copy of the FIT file); GPX of courses or of queue files;
- changing or deleting anything on the device;
- activities from the Garmin Connect account; multi-day activities (`FIT_TYPE_88`);
- activities of a device that is not connected (the read-only page of a remembered device);
- summaries kept on disk (D2c, not chosen); reading only the end of a file over MTP;
- a change to what the sync writes or acknowledges (D7b, not chosen);
- handing files to other programs ("Open in …").

The request behind part 1 and 2: users want to see their runs and rides on the computer
(#24, a watch in USB drive mode, so it must work on drives too).

## 2. Before this change

- `openexpress/fit.py`, own code written from the public FIT protocol:
  - `iter_messages(data)` walks the data messages of the first FIT part: definition messages
    in both byte orders, developer fields skipped by their size, compressed-timestamp headers
    recognised (the message is read, its time is not worked out). Values are unsigned
    integers of 1, 2 or 4 bytes only: the base type byte of a field is ignored, so a signed
    field (a position) reads wrong and its "invalid" value is not recognised. Errors only as
    `FitError`.
  - `not_whole(data)`: header of 12 or 14 bytes with ".FIT", the declared size, the CRC,
    chained parts, zero padding; tells "cut short" (a header declares more than was read)
    from other damage. The sync uses it before an upload (`sync.still_being_written`), the
    backup to leave out FIT files cut short.
  - `parse_file_id` (the creation time, the activity's ID for Garmin Connect's duplicate check)
    and `true_up_time` (TrueUp).
- Queue files (the sync's downloads) are written to the device as Garmin Connect sends them,
  without a FIT check.
- `openexpress files --type FIT_TYPE_4` lists activity files (names, sizes); the dry run of
  `sync` lists the queue (type, name, folder on the device) without downloading anything.
- The window only counts activities (their folder listed, no file read:
  `sync.activities_to_sync`).
- Backups: `<Backups>/<Unit ID>/<YYYY-MM-DD (HH.MM.SS)>/<data type>/<path>`, plus
  `GarminDevice.xml`; the window always uses the default folder (`paths.default_backup_dir()`).
- Garmin Express has no activities page, so there is nothing to match.

## 3. Data flow

```
 connected device (MTP or USB drive)        backups of the same device (by Unit ID)
 folders of FIT_TYPE_4 (GarminDevice.xml)   <Backups>/<Unit ID>/<stamp>/FIT_TYPE_4/…
                │ walk_files: names, sizes, times; nothing read
                └──────────────┬──────────────┘
                     one list, by file name: the device's copy first
                               │ the next N files read, one at a time
                               │ (bar: "Reading activities · 3 of 30")
                               ▼
                 fit.summary(bytes) → ActivitySummary; the bytes dropped
                               │
           page / table of `activities`
                               │ Export: that one file read again
                               ▼
                 gpx.track(bytes) → GPX written to the file picked in the dialog

 Garmin Connect queue → (sync dry run) each file downloaded into memory → fit.describe
                      → lines on the screen; never written, never acknowledged
 a local file         → (fit inspect) fit.inspect → lines on the screen
```

### Where the files come from

| Source | Which files | How |
|---|---|---|
| Device, MTP | every file of the data type `FIT_TYPE_4` in the folders `GarminDevice.xml` gives it (as the sync and `files --type FIT_TYPE_4` find them) | `storage.walk_files`, then `storage.read` of one file |
| Device, USB drive | the same, on the mounted drive | the same `Storage` of the drive |
| Backups | `FIT_TYPE_4/…` of every backup of this Unit ID in the backup folder (D3); only folders named as openExpress names them, no symbolic links, every file inside the folder | read as local files |
| `fit inspect` | the file named on the command line | read as a local file |
| Queue preview | each queue file the sync would write (not those it leaves in the queue, refuses, or hands to TrueUp or Connect IQ) | the download the sync uses, without writing or acknowledging |

A file on the device and one in a backup with the same name (letter case aside) are one
activity: the device's copy is read and the source says "Device". A backup copy is read only
for a name the device no longer has; of several backups, the newest that has it.

### When they are read

- Window: when the Activities page is chosen (`load_section`, as Maps and Music), never when a
  device opens; the newest N first (D2). "Show 30 More" reads the next N. Refresh on this page
  lists the folders again; summaries already read are used again.
- `openexpress activities`: when run; the newest N (`--limit`, `--all`).
- Export: the one file, read again whole, when asked.
- Queue preview: at each dry run of `sync` (D6).

### What is kept in memory

- Per activity an `ActivitySummary`: where it is (device path or backup path, the backup's
  date), size, time of change, start time (UTC), sport, timer time, distance, ascent, whether it
  has positions (GPX possible: the session's start position, else the first record that has
  one; the search stops there), the state of the check (whole, still being written, damaged,
  not a FIT file). A file cut short gives its creation time only ("still being written"), and
  so does a file without a session; a file whose CRC does not match gives its summary and
  "damaged".
- Kept for the device that is open, keyed by source, path, size and time of change; forgotten
  when the device closes or another opens. On the command line: as long as the command runs.
- The bytes of one file at a time, dropped once read; the points of one activity while its
  GPX is built; a queue file until it is described.

### What is never stored

- No copy of a FIT file, no position, no summary on disk (D2c, not chosen), no new field in
  `devices.json` or `state.json`.
- The log gets counts, sizes, durations and states only, never a file name, date, sport,
  position, or the name of a workout or course, for example:
  `activities: 30 of 212 read in 4.1 s (5.2 MB); 1 still being written, 0 damaged; 12 only in backups`,
  `activities: GPX written (4312 points, 1.1 MB)`,
  `sync preview: 3 queue files described (workouts ×2, courses ×1), 0 not whole`.
- A GPX file exists only where the user saves it.

## 4. Decisions

### D1: the FIT reader — chosen: 1a

- 1a. Our own, extending `fit.py` (recommended; chosen). Values by base type (signed, the
  invalid value of each type, strings), messages not wanted skipped by their size without being
  decoded, the time of compressed-timestamp headers. About 200 more lines.
  - \+ No new dependency: nothing changes in the Flatpak manifest, `pyproject.toml` or the
    distribution packages; licence questions do not arise.
  - \+ Fast where it matters: a summary decodes only `file_id` and `session` and steps over
    thousands of `record` messages; one reader for the sync, the backup and these pages, with
    the same errors (`FitError`).
  - \+ Clean room by construction (below).
  - − We maintain it; edge cases of the protocol are ours (tested with made-up files of every
    base type, both byte orders, compressed timestamps, developer fields, cut and damaged).
  - − Only the messages and fields of Appendix A have names; `fit inspect` names other
    messages by number.
- 1b. fitdecode (MIT, pure Python, 110 kB, no dependencies; 0.11.0 of 2025 after four years
  without a release).
  - \+ The whole profile: every message and field named.
  - − A new dependency to pin in the Flatpak manifest and to ask of distributions; its profile
    follows Garmin's only when its author regenerates it; decodes every field of every
    message, slower for a list of hundreds; `fit.py` stays for the sync, so two readers.
- 1c. python-fitparse (the maintained fork; MIT, 103 kB, releases in 2026).
  - \+ Active, the whole profile.
  - − As 1b, and its module is named `fitparse` like the unmaintained original.
- Not an option: Garmin's own FIT SDK for Python. Its licence (FIT Protocol License Agreement)
  forbids distributing it so that any part becomes subject to a licence that requires source
  disclosure, which conflicts with GPL-3.0-or-later.

Clean room for 1a: the file structure (headers, definitions, base types, compressed timestamps,
CRC) as the public FIT protocol describes it, as `fit.py` already follows it; message and field
numbers, scales and offsets (Appendix A) are facts of the published FIT profile, checked
against the profile table of an MIT-licensed reader (fitdecode, profile 21.171). No code is
taken from any library, and nothing from Garmin Express: not its program files, not the files it
installs, not anything learned from them. Each number in the code carries a comment naming its
profile message and field, as `service.TYPE_NAMES` does.

### D2: how much is read at a time — chosen: 2b

Reading is what costs: listing 200 activities over MTP took about half a second, while 200
activities of an Edge 1050 are 33 MB. Only one task runs at a time in the window, so a long
read would also hold back a sync.

- 2a. All of them, kept for the session.
  - \+ The simplest page: one list, whole.
  - − Hundreds of files over MTP before anything shows; the Sync button waits as long.
- 2b. The newest 30, then "Show 30 More" (recommended; chosen). Newest by the time of change the
  device gives, then by name; files only in backups come after those on the device (the device
  deletes its oldest first). The command line the same: `--limit N` (default 30), `--all`.
  - \+ The page shows in seconds whatever the device holds; a sync waits seconds at most.
  - \+ Nothing stored; the same rule in the window and the command line.
  - − A second step to older activities; the order before reading is by time of change, so
    the newest 30 may miss one whose time of change is odd (it shows with the next 30).
- 2c. 2a or 2b, plus the summaries kept on disk (cache folder, by Unit ID, file name and size).
  - \+ After the first time, every list is instant.
  - − New stored data of each activity (date, sport, distance): `docs/privacy.md`, Forget
    Device, `cache --clear` and its parts all change; a cache to keep right when files change.

N = 30 is a constant, a number rather than scope: a batch of 30 reads in seconds over MTP.

### D3: which backups — chosen: 3a

- 3a. Every backup of this device in the backup folder, one list by file name (recommended;
  chosen).
  - \+ Activities the device has deleted come back from whichever backup kept them.
  - − Reads one more folder per backup (local, fast).
- 3b. The newest backup only.
  - \+ One folder.
  - − An activity deleted from the device and from the newest backup, but kept by an older one,
    is not shown.
- 3c. No backups in the list.
  - \+ The simplest.
  - − Part 1 of the scope asks for them.

The window uses the default backup folder (as the Backup row); `openexpress activities` the
same, or `--backups DIR` (as `backup --dest`).

### D4: the place in the window — chosen: 4a

- 4a. A section "Activities" last in the sidebar, after Music; no row on the Device page
  (recommended; chosen).
  - \+ Like Maps, Apps and Music: one more page of the same list; existing pages keep their
    place; nothing read until it is chosen.
  - − Not visible on the Device page.
- 4b. As 4a, plus a row "Activities" on the Device page that goes to the section, with the
  count once the page has read it (as Apps and Music).
  - \+ Found from the Device page too.
  - − One more row on a page that is long already; the count says only how many were read
    (with 2b, 30).
- 4c. The section second, after Device.
  - \+ The user's own data first.
  - − Every page after it moves.

Shown for drives too (unlike Maps, Apps and Music); a device that declares no `FIT_TYPE_4`
(e.g. a Varia) gets the page with "This device does not keep activities".

### D5: what the GPX holds — chosen: 5a

- 5a. Position, elevation and time of each point (recommended; chosen). GPX 1.1, one track, one
  segment, points without a position left out.
  - \+ Plain GPX 1.1, read by every program (including those that geotag photos or render a
    route); the least data of the user's body in a file that may be shared.
  - − No heart rate or cadence.
  - Positions in degrees with 7 decimals, the elevation from `enhanced_altitude` before
    `altitude`, times in UTC ending in `Z`; openExpress as the creator, no serial number (D8).
- 5b. As 5a, plus heart rate and cadence in Garmin's TrackPointExtension (v1) namespace.
  - \+ Programs that read it show them.
  - − A second schema to write and test; health data in a file the user may share.

### D6: where the queue preview is shown — chosen: 6a

- 6a. In the dry run of `sync`, always (recommended; chosen). Under each queue line, one more
  line: what the file is, sport, dates, the check.
  - \+ The dry run is the command line's preview of a sync already; nothing new to learn.
  - − Each dry run downloads the queue files (usually a few small files); the real sync
    downloads them again, since nothing is acknowledged.
- 6b. Only with `sync --describe`.
  - \+ A plain dry run asks Garmin's servers for nothing more.
  - − One more option, and the preview is missed by whoever does not know it.
- 6c. As 6a, plus the window: a row "Waiting in Garmin Connect" in the group "Sync with Garmin
  Connect" of the Device page, with a button "Show…" that downloads and describes them (the
  shared bar), and the list in an `inform()` window.
  - \+ Users of the window see it too.
  - − A new request from the window, a new row and window, more texts to fit 360 px.

### D7: the sync's own check of queue files — chosen: 7a

- 7a. The sync stays as it is (recommended; chosen). The preview reports a file that is not
  whole; the sync writes what Garmin Connect sends, unchanged.
  - \+ No change to the sync; no case is known of Garmin Connect sending a damaged file.
  - − A preview may say "damaged" of a file the sync then writes.
- 7b. The sync does not write a FIT file that fails the check: the item counts as failed, stays
  unacknowledged and comes again at the next sync; one line in the log.
  - \+ A damaged file never reaches the device; the preview and the sync agree.
  - − A change to the sync and its summary ("Last Sync" words, a new pattern in
    `tests/data/last_sync/`); a file Garmin Connect keeps sending damaged fails every sync.

### D8: the command line — chosen: 8a

- 8a. `openexpress activities`, `openexpress activities export NAME…`, `openexpress fit inspect
  FILE` (recommended; chosen). `activities` lists (as `maps` and `music` list with no
  subcommand); `export` takes file names as the list prints them.
  - \+ The noun the window uses; `fit` keeps the commands about a file as a file.
  - − Two new top-level commands.
- 8b. Everything under `fit`: `fit activities`, `fit gpx NAME…`, `fit inspect FILE`.
  - \+ One new top-level command.
  - − "fit" means nothing to someone looking for their runs.

Set by the rules of the program, not decisions:

- `activities export` writes only with `--apply` (as every command that changes something),
  never overwrites a file (the dry run and the error say which), and writes by default to
  `Documents/Garmin/GPX/`, which the Flatpak may write already (`--dest DIR` for another
  folder). The window asks where, with the file chooser (a portal in the Flatpak).
- "Time" is the timer time (pauses left out, as Garmin Connect's "Time"); distances in km,
  ascent in m; dates in the computer's time zone.
- No GPX of an activity without positions or still being written; the window's button
  is then insensitive and says why in its tooltip, the command line says why and exits with 1.
- `fit inspect` exits with 0 for a whole FIT file and with 1 for any other (cut short,
  damaged, not FIT, unreadable): code 1 as `docs/cli.md` defines it, no new code.
- The serial number in `file_id` and `device_info` is never shown, logged or written to a GPX;
  a GPX names openExpress as its creator, not the device.

## 5. What changes in existing modules

| Module | Change |
|---|---|
| `fit.py` | values by base type; `wanted` messages decoded, others skipped by size; the time of compressed-timestamp headers; new `summary()`, `inspect()`, `points()`; `iter_messages`, `not_whole`, `parse_file_id`, `true_up_time` keep their behaviour (their tests unchanged) |
| new `activities.py` | the sources (device, backups), one list, the order, reading N at a time with `Step` progress, the summaries kept by key, the names of sports |
| new `gpx.py` | GPX 1.1 writer (`xml.etree`, no dependency) |
| `backup.py` | one function listing the backups of a Unit ID (stamp names, no symbolic links), also used by `gui/device_page.backups_text` instead of its own listing |
| `sync.py` | `describe_queue(items, client)`: downloads and describes the items the sync would write; writes nothing, acknowledges nothing (D7b would add one check to the sync's downloads) |
| `cli.py` | `activities`, `activities export`, `fit inspect`; the dry run of `sync` prints the described lines (D6a) |
| `gui/app.py` | `Section("activities", …)` last, with the page's icon (`activities_page.PAGE_ICON`); `ActivitiesPage` among the bases of `MainWindow`; its attributes made with the window's others |
| new `gui/activities_page.py` | the page, Show 30 More, Export GPX (file chooser, `run_task(…, button=)`); "Has:" line, own logger |
| `gui/device_page.py` | `load_section` knows "activities"; the summaries forgotten when the pages of the device are cleared |
| `gui/words.py` | the words of a row: sport, date, time, distance, ascent, source, state (no GTK, tested without a display) |
| `po/pl.po`, `po/openexpress.pot` | new msgids, Polish in the same commit as each |
| `docs/` | `cli.md` (the commands, code 1 of `fit inspect`), `guide.md` (a section "Activities"), `privacy.md` (what is read, that nothing is kept), README's list of features |
| `tests/` | `fakes.fit_file(…)`: a builder of made-up FIT files (definitions, base types, compressed headers, developer fields, a cut or a wrong CRC on request); `test_window_state`, `test_narrow_window` and `test_window_split` cover the new page as they cover the others |

Unchanged: `device/storage.py` and `device/gio_storage.py` (`walk_files`, `read` and `read_start`
suffice), the sync's uploads and downloads (with D7a), `state.json`, `devices.json`, the Flatpak
manifest (with D1a) and its permissions, the main menu, the keyboard shortcuts. No new task
writes to the device, so none joins the `WRITING` list of `writes_to_device`.

## 6. Risks

1. Large files. Long recordings at one point a second reach tens of MB. One file in memory at a
   time; the summary steps over `record` messages without decoding them; export decodes only
   position, altitude and time. A GPX of 100 000 points is about 10 MB and is written as a task
   with the bar. Nothing is allocated from a size the file declares.
2. Files cut short over MTP. gvfs gives the size a file had when the device was connected, so a
   file the device was writing then reads short (seen on a watch with a Garmin Coach file).
   `not_whole` says "cut short": the row says "Still being written: unplug the device and plug
   it in again", without numbers or export, as the sync and the backup treat such a file.
   Activities are not recorded while the device is connected over USB, so for them it is rare.
3. Hundreds of activities. Listing is cheap, reading is not (D2). With 2b a batch takes
   seconds; its time goes to the log.
4. USB drives. Read as plain files (fast). A drive ejected or unplugged while the page reads:
   the page shows the error, as other pages do; nothing is written, so nothing needs an eject
   afterwards. A drive the window does not mount (KDE) opens as every drive opens.
5. Backups. Only folders whose names are backup stamps, no symbolic links, every file inside
   the folder (`paths.is_inside`); a backup folder that cannot be read is one log line, the list
   goes on without it. The Flatpak sees `Documents/Garmin` already.
6. Damaged or crafted files. The reader raises only `FitError`, always moves forward, never
   loops; a damaged file is one row and never stops the list. A file whose CRC does not match
   but whose summary reads is shown with "Damaged file".
7. One task at a time. While the page reads, Sync and other tasks wait (the window's toast says
   so); the background count of activities (`in_background`) may read the device at the same
   time, as it can with other pages.
8. Time zone. FIT times are UTC; an activity recorded abroad shows the computer's local time
   (the device's own local time is out of scope).
9. Words and width. Sport names and row words, in Polish too, must wrap within 360 px;
   `test_narrow_window` gets the longest Polish sport name and state.
10. The Flatpak on the command line. `fit inspect FILE` and `export --dest` reach only what the
    sandbox sees (`Documents/Garmin`, Downloads and Music read-only); `docs/cli.md` says so. No
    new permission.
11. The queue preview asks more of Garmin's servers (D6): one download per queue file per dry
    run; a failed download is a line of the preview, never an error of the dry run, which ends
    with 0. The files are not kept (no kind of `answers`): the queue changes as soon as a sync
    takes it.
12. Privacy. Positions appear only in a GPX the user saves; workout and course names only on
    the screen (as the dry run of `sync` shows them), never in the log.

## 7. Sketches

### The Activities page

The window as it is: the sidebar with the main menu in its header, the page with Refresh and
Sync in its header, the shared bar at the bottom of the page's column. The chosen options (2b,
3a, 4a); made-up data.

```
┌──────────────────────────┬──────────────────────────────────────────────────┐
│ ‹     openExpress     ≡  │ ⟳               Activities                [Sync] │
├──────────────────────────┼──────────────────────────────────────────────────┤
│  ◷  Device               │   212 activities: 200 on the device, 12 only in  │
│  ⇩  Updates              │   backups. The newest first.                     │
│  ⌖  Maps                 │  ┌────────────────────────────────────────────┐  │
│  ▦  Apps                 │  │ Running                              [⤓]   │  │
│  ♫  Music                │  │ Thu 30 Oct 2026, 07:00 · 1 h 02 min ·      │  │
│ [▤  Activities        ]  │  │ 12.4 km · ↑ 135 m · Device                 │  │
│                          │  ├────────────────────────────────────────────┤  │
│                          │  │ Strength                             [⤓]┄  │  │
│                          │  │ Wed 29 Oct 2026, 18:00 · 45 min · Device   │  │
│                          │  ├────────────────────────────────────────────┤  │
│                          │  │ Activity                                   │  │
│                          │  │ Tue 28 Oct 2026, 06:00 · Still being       │  │
│                          │  │ written: unplug the device and plug it in  │  │
│                          │  │ again                                      │  │
│                          │  ├────────────────────────────────────────────┤  │
│                          │  │ Cycling                              [⤓]   │  │
│                          │  │ Sun 14 Sep 2025, 09:00 · 3 h 41 min ·      │  │
│                          │  │ 88.1 km · ↑ 960 m · Backup of 2 Oct 2025   │  │
│                          │  └────────────────────────────────────────────┘  │
│                          │                [ Show 30 More ]                  │
│  ● (network status)      ├──────────────────────────────────────────────────┤
│                          │ ◌ Reading activities · 12 of 30   ▓▓▓▓░░░░░░░░   │
└──────────────────────────┴──────────────────────────────────────────────────┘
 [⤓]  Export as GPX… (icon document-save-as-symbolic, tooltip and accessible name)
 [⤓]┄ insensitive: "No GPS positions in this activity"
```

- One `Adw.PreferencesGroup` with a description (how many, where), rows `Adw.ActionRow`: title
  the sport, subtitle the details joined by `caption()` (wrapping), suffix the export button.
  A damaged file adds "Damaged file" to its subtitle; a file that is no FIT file is a row
  "Unreadable File" with its source.
- While the first 30 are read: the page's own "Reading Activities…" (as every page loads) and
  the bar with its steps. "Show 30 More" is a task on its button (the bar after half a second);
  the rows stay. Export: the file chooser, then a task on the row's button, then a toast
  "Saved as 2026-10-30 07.00 Running.gpx".
- Empty: "No Activities", "There are none on the device or in its backups"; a device without
  `FIT_TYPE_4`: "This device does not keep activities". An error: the page's error with Try
  Again, as every page.
- Folded (below 620 sp): the sidebar list first, the page after it with the back arrow and the
  Sync icon, as on the other pages. The main menu and its items do not change.

### The command line

```
$ openexpress activities
fēnix 8 (software 23.39)
Date              Sport     Time     Distance  Ascent  Source              File
2026-10-30 07:00  Running   1:02:11  12.40 km  135 m   device              2026-10-30-07-00-00.fit
2026-10-29 18:00  Strength  0:45:02  –         –       device              2026-10-29-18-00-00.fit
2026-10-28 06:00  –         –        –         –       device              2026-10-28-06-00-00.fit
                  still being written: unplug the device and plug it in again
2025-09-14 09:00  Cycling   3:41:20  88.10 km  960 m   backup 2025-10-02   2025-09-14-09-00-00.fit

The newest 30 of 212 (12 only in backups); --all shows every one.

$ openexpress activities export 2026-10-30-07-00-00.fit
Would write ~/Documents/Garmin/GPX/2026-10-30 07.00 Running.gpx (4312 points)
Dry run: nothing was written. Add --apply to write it.

$ openexpress fit inspect ~/Documents/Garmin/course.fit
File: course.fit, 23.4 kB
Check: whole (header 14 B, data 23 380 B, CRC matches)
Type: courses (FIT_TYPE_6)
Manufacturer: Garmin (1)
Product: 65534
Created: 2026-10-30 07:00:00
Messages: 6 kinds, 1213 in all
  record (20)            1204
  course_point (32)         4
  event (21)                2
  file_id (0)               1
  course (31)               1
  lap (19)                  1
Developer data: none

$ openexpress sync                      # the dry run, with D6a
…
To download from Garmin Connect to the device: 2
  FIT_TYPE_5             Tempo 5×1 km -> GARMIN/Workouts
      workout · running · 7 steps · created 2026-10-29 · whole
  FIT_TYPE_7             Plan -> GARMIN/Schedules
      schedules · 14 entries from 3 Nov 2026 to 30 Nov 2026 · whole
```

A cut-short file in `fit inspect`: `Check: cut short: 120 000 B read, 184 016 B declared (still
being written when it was read)`, and the messages counted up to where it ends. A queue file of
a type with no message of Appendix A (a Garmin Coach plan may use a type of its own rather than
workouts and schedules) is described by its type and creation time.

## Appendix A: the FIT facts used

From the published FIT profile, checked against the profile table of fitdecode (MIT, profile
21.171). Times (`date_time`) are seconds since 1989-12-31 00:00 UTC (`fit.FIT_EPOCH_OFFSET`);
`local_date_time` the same in the device's local time. Positions are semicircles: degrees =
value × 180 / 2^31.

| Message (number) | Field (number) | Base type | Scale, offset, unit | Used for |
|---|---|---|---|---|
| file_id (0) | type (0) | enum | | type of the file (`FIT_TYPE_n`) |
| | manufacturer (1) | uint16 | | inspect: 1 Garmin, 255 development, others by number |
| | product (2) | uint16 | | inspect, by number |
| | serial_number (3) | uint32z | | never shown |
| | time_created (4) | date_time | | creation time |
| session (18) | start_time (2) | date_time | | date of the activity |
| | start_position_lat (3), start_position_long (4) | sint32 | semicircles | GPX possible |
| | sport (5) | enum | | sport |
| | total_elapsed_time (7) | uint32 | ÷1000, s | not shown ("Time" is the timer time) |
| | total_timer_time (8) | uint32 | ÷1000, s | time |
| | total_distance (9) | uint32 | ÷100, m | distance |
| | total_ascent (22) | uint16 | m | ascent |
| record (20) | timestamp (253) | date_time | | GPX time |
| | position_lat (0), position_long (1) | sint32 | semicircles | GPX position; GPX possible when the session has no start position |
| | altitude (2) | uint16 | ÷5 −500, m | GPX elevation, when 78 is missing |
| | enhanced_altitude (78) | uint32 | ÷5 −500, m | GPX elevation |
| workout (26) | sport (4), num_valid_steps (6), wkt_name (8, string) | | | queue preview |
| course (31) | sport (4), name (5, string) | | | queue preview |
| schedule (28) | time_created (3), completed (4), type (5), scheduled_time (6, local_date_time) | | | queue preview |

Named only in `fit inspect`: sport (12), goal (15), lap (19), event (21), device_info (23),
workout_step (27), course_point (32), activity (34), file_creator (49), training_file (72),
hrv (78), field_description (206), developer_data_id (207); any other as "message N".

Base types (the byte of a field definition) and their invalid values: enum 0x00 and uint8 0x02
(0xFF), sint8 0x01 (0x7F), sint16 0x83 (0x7FFF), uint16 0x84 (0xFFFF), sint32 0x85
(0x7FFFFFFF), uint32 0x86 (0xFFFFFFFF), string 0x07 (empty, ends at the first zero byte),
float32 0x88 and float64 0x89 (NaN), uint8z 0x0A, uint16z 0x8B and uint32z 0x8C (0), byte 0x0D
(all 0xFF), sint64 0x8E, uint64 0x8F, uint64z 0x90 (as their 32-bit kin). A field whose size is
a multiple of its base type's is an array: read only where a message asks for its arrays, none
of this appendix (the heart rate zones of [podglad-aktywnosci.md](podglad-aktywnosci.md) read
theirs, D14).

Compressed-timestamp header (bit 7 set): local message type in bits 5–6, a time offset in bits
0–4 that replaces the low five bits of the last full timestamp, plus 32 when it is smaller than
them.

Sports with names of their own (others: "Sport N"): 0 generic ("Activity"), 1 running,
2 cycling, 4 fitness equipment, 5 swimming, 10 training, 11 walking, 12 cross-country skiing,
13 alpine skiing, 14 snowboarding, 15 rowing, 16 mountaineering, 17 hiking, 18 multisport,
19 paddling, 21 e-biking, 25 golf, 31 rock climbing, 35 snowshoeing, 37 stand-up paddleboarding,
41 kayaking, 62 HIIT.
