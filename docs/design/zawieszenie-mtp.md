# Design: a device that stops answering over MTP

Status: implemented. The decisions of section 4 are made (D1 1a, D2 2a, D3 3a, D4 4a; D5 5a,
D6 6a, D7 7a when step 1 began; D8 8a when step 3 began; D9 9a, D10 10a, D11 11a, D12 12a when
step 5 began; marked "chosen"). Sections 2 and 3 describe the program before this work. The
steps of section 7, one commit each, and how they went on a fēnix 8 over MTP (the code from
source; a stall cannot be made on purpose, so the device showed the usual work going on as
before):

| Step | On the device |
|---|---|
| 1. Listings and queries under the limit, the error | the newest 30 activities read in 7.9 s (7.6–8.0 s before); the window as usual |
| 2. Reads in chunks | every activity of the device in 31.4 s (32.6 s before); a dry sync of 9 files in 1.8 s |
| 3. Fast failure, the sync and the backup, the handlers (D8) | a dry sync in 1.7 s; `status`, `backup`, `maps`, `apps`, `updates` as before |
| 4. Log lines | the newest 30 activities in 7.4 s, every one in 31.5 s; no step over 5 s logged |
| 5. The window (D4, D9–D12) | the banner seen in pictures of the tests' hidden display; unplugged and plugged in again, the device detected as before |
| 6. Documentation | – |

In none of them did the log show a step that reached the limit. D3 waits for the log lines of
step 4 to show a stall after a long time without a request. D13 (where the error sits among the
program's errors): 13b for now, the code as it is; 13a is planned for a later version.

## 1. Scope

A device connected over MTP can stop answering in the middle of a read. Today the program then
waits for ever: the bar stands still, the window answers but the task never ends, and only
killing `gvfsd-mtp` and unplugging the device end it. This design puts a limit on every read
from the device through gvfs: after 30 s without any progress the read is given up, the user is
told that the device stopped responding and to unplug it and plug it in again, and nothing else
is tried on that device until it is.

In scope, for a device over MTP through gvfs (`GioStorage`):

1. **Every reading operation**: listing a folder, the information of a file, its contents, the
   start of a file, the free space and capacity (D1).
2. **What follows a stall**: the error and its words, the operations after it in the same
   session, the window (D4), the log lines that tell how long the device had been idle.

Not in scope:

- Writes to the device (copying, deleting, making a folder): no time limit of their own yet;
  after a stall they fail at once like any other operation (section 5).
- USB drives and folders: they read the file system.
- A periodic request that keeps the device busy (D3): left for later.
- PTP sessions of our own (`device/ptp.py`): they have the time limits of libusb.

## 2. The problem

Seen once on a fēnix 8 (software 23.39) over MTP through gvfs, Fedora 44, in the Flatpak (from
the program's log):

- 30 activities read (10.1 MB in 8.0 s) and the music listed; the device answered as usual.
- Eleven seconds later a sync starts. Its plan reads `GARMIN/Backup/Backups/settings_backup.bak`, a file
  of a single-file type, for its MD5 (`sync.plan_uploads`); the read never answers. The bar
  stands on "Looking for new files on the device…".
- `gio list` and `gio mount` on the device's address hang too; `lsusb` shows the device on USB;
  `dmesg` shows no error and no disconnect: not USB nor the driver, but the MTP session in the
  device.
- Eight minutes later, after `gvfsd-mtp` was killed: the sync stops after 480.7 s with "Error in
  stream protocol: End of stream" for that file.
- For three minutes after, the window tries to mount the device every 32 s or so; each mount
  gives up at its limit of 30 s. Only unplugging the device and plugging it in again helps; the syncs
  after it run as usual.

Why everything hung, not only that file (gvfs-mtp 1.60.3, `daemon/gvfsbackendmtp.c`):

- The backend keeps one mutex for the whole mount and holds it across each libmtp call. A read
  (`do_read`: `LIBMTP_GetPartialObject`) that does not return blocks every later operation on
  that mount: listings, queries, the unmount, a new mount of the same backend.
- `do_read` does not look at the job's cancellable, and a libmtp call cannot be interrupted. A
  client that cancels stops waiting; the backend stays blocked until the device is unplugged
  (or the process is killed).
- A read is served by `LIBMTP_GetPartialObject` for as many bytes as the client asks, so a
  file read in chunks gives one answer a chunk.
- The backend already asks the device something every 900 s (`mtp_heartbeat`:
  `LIBMTP_Get_Friendlyname`, when the mutex is free), so a mounted device is never quite idle.

The free space reaches the device: `do_query_fs_info` calls `LIBMTP_Get_Storage` on each call,
and libmtp 1.1.14 (the version read) then sends `GetStorageIDs` and `GetStorageInfo` anew, with
nothing kept. The
information of a file does not: libmtp keeps it from the first time it is asked.

## 3. What exists today

### The reads of `GioStorage`

Each is synchronous and given no cancellable (`None`), so none has a time limit:

| Operation | Gio call | Used for |
|---|---|---|
| `_list` | `enumerate_children`, then `next_file` | `listdir`, `walk_files`, `resolve` (the case of a path), `exists` |
| `info`, `_size_now` | `query_info` | a file's size and time, the check after a write |
| `_read` | `load_contents` | `read`: every file read whole |
| `_read_start` | `read`, then `read_bytes` | `read_start`: the start of a file (headers) |
| `_fs_attr` | `query_filesystem_info` | `free_space`, `capacity` |

About a hundred places in fifteen modules read through them (the sync, the backup, activities,
diagnostics, Connect IQ, maps, own maps, music, updates, the command line); the limit belongs in
these operations, not in their callers. The window reads the device only in its tasks, off the
GTK thread.

### Waiting on Gio

`gio_storage._wait(start, timeout_s, what, name)` runs one asynchronous Gio operation to its end:
in a worker thread on a main context of its own, in the main thread on the default one. At
`timeout_s` it cancels it and waits `CANCEL_WAIT_S` (5 s) more for its answer, then raises
`GioTimedOut` ("…: the device did not answer in time") and logs what happened. Only the mount
(30 s), the unmount (30 s) and the eject (60 s) use it today.

### Measured

From three weeks of the program's log:

- Reads of activities at 1.26–1.37 MB/s each; none slower.
- The long syncs (up to 40 s) mostly wait for TrueUp. No "End of stream" and no time limit
  reached outside a stress test, but the stall above.
- The time of a single read is not logged, so a stall of a few seconds would not show.

## 4. Decisions

### D1: which operations have a limit — chosen: 1a

- **1a. Every reading operation of `GioStorage` (recommended; chosen):** `_list`, `info` and
  `_size_now`, `_read`, `_read_start`, `_fs_attr`.
  - \+ Every caller is covered at once, also those added later.
  - \+ The stall seen was in the plan of a sync, which a limit on file contents only would
    cover by chance; the listings and queries go through the same blocked mutex.
  - − Six operations change at once (section 7 splits them in two steps).
- **1b. Only reads of file contents** (`_read`, `_read_start`).
  - − A listing or a query blocked behind the same mutex still waits for ever.
- **1c. Reads and writes.**
  - − A write cut off leaves a part of a file, which the clean-up after a failed write
    (`_discard_partial`) must then handle with no answer from the device; in the Flatpak writes
    go through `gio copy` on the host. Left for a design of its own.

### D2: the limit — chosen: 2a

- **2a. 30 s without progress a step; files read in chunks of 256 KiB (recommended; chosen).** A
  step is one answer of Gio: opening a file, a chunk of it, closing it; opening a listing, a
  portion of it (`next_files_async`), closing it; one `query_info`; one
  `query_filesystem_info`. Each goes under `_wait` with the limit.
  - \+ No false alarm for a large file: the time counts from the last answer, not from the
    start. A chunk of 256 KiB takes about 0.2 s at the speed measured, so 30 s is a margin of
    more than a hundred times.
  - \+ The same limit as the mount and the unmount.
  - − Up to 35 s (with `CANCEL_WAIT_S`) before the user is told.
- **2b. 15 s a step.**
  - \+ Told sooner.
  - − Half the margin, with nothing measured of a slow device but its mount (up to 20 s until
    `GarminDevice.xml` can be seen).
- **2c. A limit on the whole file, from its size.**
  - − The size gvfs gives can be old (a file written while connected), and a whole-file wait
    needs a guess of the speed.

### D3: a request that keeps the device busy — chosen: 3a

- **3a. Not now (recommended; chosen).** Come back when the log lines of section 5 show stalls
  after a long time without a request.
  - \+ The one stall seen came a few seconds after the device had answered many reads, not
    after idleness; gvfs-mtp already asks the device something every 900 s; three weeks of
    logs show no shorter stalls.
- **3b. Now, in the window only.** While the device view is open and no task runs, ask the free
  space (`GetStorageInfo`, which reaches the device) every 5 minutes, under the same limit.
  - \+ A stall would be found before the user starts a sync.
  - − Not shown to prevent anything; another request queued on the same mutex as the tasks.

### D4: the window after a stall — chosen: 4a

- **4a. A lasting banner, and no more mounts at that place on USB (recommended; chosen).** The
  window's banner (`Adw.Banner`, as for the setup and the sign-in) says "The device stopped
  responding. Unplug it and plug it in again." until the device is unplugged. While the same
  device stays at the same place on USB (`usb_still_there`), the window does not try to mount
  it: each try would wait 30 s for the same blocked mutex. Once the place is gone (unplugged,
  restarted: a new device number) the banner goes and detection works as today.
- **4b. As today.** A try to mount every 32 s or so, each giving up after 30 s, without saying
  why.

### D5: when the error comes in — chosen: 5a

- **5a. In step 1, with its words (recommended; chosen):** `DeviceNotResponding` with its
  English and Polish text and `says_replug`, and the tests of its words, come with the first
  step that raises it.
  - \+ A step raises the error it is tested with; no text in between to change later.
  - − Step 3 no longer brings the error, only what follows it.
- **5b. In step 1 with the words of today's time limits** ("…: the device did not answer in
  time"), its own words in step 3.
  - − A text shown for two steps, then changed.

### D6: the handlers of the device layer — chosen: 6a

- **6a. `DeviceNotResponding` goes past the handlers of the device layer (recommended;
  chosen).** `Storage.resolve` and `Storage.sizes` take a listing that failed for a missing
  folder; `_remount` looks for `GarminDevice.xml` up to 20 times, a second apart, whatever
  fails; `_device_state` takes a failure for "between"; `refresh` takes one for "not
  refreshed". Each lets the error through and keeps its answer for any other failure.
  - \+ Without it a stall while a path is looked for reads as a missing file after 35 s ("File
    not found on the device", an empty listing), and a sync goes on, waiting 35 s at every next
    path; `_remount` waits up to 20 times 35 s.
  - − Five handlers more than section 5 had.
- **6b. Leave them to step 3.**
  - − Step 1 alone would turn a stall into missing files.

### D7: a write that meets a stall — chosen: 7a

- **7a. No clean-up after it (recommended; chosen):** `write` and `write_file` let
  `DeviceNotResponding` through as they let `CopyNotStarted`, without `_discard_partial`. A copy
  has no time limit (D1), so the error comes only before it: from the size of the file there
  (`_size_now`) or from finding its folder.
  - \+ Nothing was touched, and a clean-up would ask the same blocked device again.
- **7b. Clean up as after any failure.**
  - − Waits another 35 s for the same device, then fails the same way.

### D8: the handlers in other modules — chosen: 8a

Besides the 25 handlers of `DeviceError` left from step 1 (section 5), a few broad handlers
(`except Exception`) on the way of the device catch the error too: the loops of a sync (files
uploaded, files of the queue written, TrueUp, apps of the queue) and the look at a device for a
list (`gio_storage._probe`, for `status` and the start page).

- **8a. It goes past every handler that can meet it, outside the window (recommended;
  chosen).** Each handler of `DeviceError` outside `gui/` has one of `DeviceNotResponding`
  before it, which raises it on; the broad handlers on the way of the device too. A test of the
  syntax tree finds any handler of `DeviceError` without it.
  - \+ The first stall always reaches the top: the command line says it once with exit code 1,
    a sync stops before TrueUp, and nothing keeps a false fact.
  - \+ TrueUp is told received before it is written (as Garmin Express does it): a sync that
    went on after a stall in the apps of its queue would download it, tell Garmin Connect it was
    received, and lose it when its write failed at once.
  - \+ A stall when the app folder is made would be remembered as the device refusing apps over
    USB (`_remember_refusal`), for the whole software version: the device would not be asked
    again until it is updated.
  - − About 30 small changes in 10 modules; in the window a stall while a device opens (the
    count of its activities) is told to that task, not taken for "not known".
- **8b. Only handlers that let one item fail and go on, or try again.** Summaries (the line of
  `status`, the overview of own maps, the comparison of updates) answer "not known".
  - − `status`, `maps` and `updates` would end with exit code 0, the stall hidden in a line.
- **8c. Only the sync and the backup, as section 5 had it.**
  - − Both losses of 8a stay; the apps, maps and diagnostics go through all their items, each
    failing with the same words.

### D9: the banner — chosen: 9a

- **9a. An `Adw.Banner` as for the setup and the sign-in, with the device's name, no button
  (recommended; chosen).** "The device fēnix 8 stopped responding. Unplug it and plug it in
  again." ("Urządzenie fēnix 8 przestało odpowiadać. Odłącz je i podłącz ponownie."); without
  the name when it is not known, and for several devices all of them ("The devices fēnix 8,
  Edge 1050 stopped responding…"). The window's own words, not the command line's. It comes
  before the banners of the setup, the sign-in and the account. No error dialog besides: while
  the failure of a task that met a stall is told, `error()` shows none. The device view has the
  banner on top already; the start page, where the window goes once the device is closed
  (D10), gets one too, with the same words.
  - \+ One message that stays while the device is to be unplugged; with several devices it says
    which one.
  - \+ No button: every try would wait for the same blocked device.
- **9b. Always without the name, as D4 had it.**
  - − With several devices it is not known which one.
- **9c. An error dialog instead of the banner, or both.**
  - − It goes once clicked away, while the device stays blocked.

### D10: the device open after a stall — chosen: 10a

- **10a. Closed as if it were unplugged (recommended; chosen):** the session goes, the pages are
  empty, the start page shows what is left; the banner says why.
  - \+ Nothing can be done with the device (fast failure): its pages from before the stall
    would only look usable.
- **10b. Kept, its pages read only, the buttons off until it is unplugged.**
  - − Changes in every page, and the window looks as if the device worked.

### D11: several devices — chosen: 11a

- **11a. Only the place of the stalled device is left out (recommended; chosen):** of the
  detection, the list of the devices connected and the mount. The other devices work as they
  do; the banner, with the name, stays while the stalled device is at its place, also with
  another device open.
- **11b. Nothing detected while the stalled device is plugged in.**
  - − Devices that work are kept out too.

### D12: the place of the device and its unplugging — chosen: 12a

- **12a. The place on USB, looked at every 2 s (recommended; chosen).** The error carries the
  place on USB of its device (`GioStorage.usb`; `_mtp_storage` gives its storages the place from
  the start, so that a stall while a device is opened has it too), and gio_storage keeps it
  (`stalled_places`). The USB poll of the window (every 2 s, `/sys` only) looks whether the
  device is still there (`usb_still_there`); a new device number (unplugged and plugged in
  again, or restarted) means it is gone: the banner goes and the detection is as usual, the
  device coming back as any device plugged in. A stall whose place is not known (an address of
  gvfs without a place on USB) gets the banner but keeps nothing out; its banner goes when the
  Garmin devices on USB change.
  - \+ `/sys` tells it whatever gvfs does.
- **12b. The signal of gvfs that the mount is gone.**
  - − A blocked gvfs may never send it, and a killed `gvfsd-mtp` sends it without the device
    being unplugged (the incident of section 2: the mount went when `gvfsd-mtp` was killed,
    and the device still had to be unplugged).

### D13: where `DeviceNotResponding` sits among the errors — 13b for now, 13a later

Today `DeviceNotResponding` is a `DeviceError` (D5), so every handler of `DeviceError` that a
device over MTP can reach has a clause before it that raises the stall on (D6, D8). Counted in
the syntax tree of the package, there are 56 such clauses in 11 modules:

| Before | Clauses | Where |
|---|---|---|
| a handler of `DeviceError` (in 3 of them `(DeviceError, OSError)`) | 37 (4 of them a tuple: with `RemountFailed`, `SeveralDevices` or `MassStorageNotice`) | `ciq.py` 10, `device/gio_storage.py` 10, `diagnostics.py` 4, `ownmaps.py` 4, `device/storage.py` 3, `maps.py` 2, `cli.py`, `service.py`, `updates.py`, `watch.py` 1 each |
| a broad handler (`Exception`, `BaseException`) | 19 (6 of them a tuple) | `ciq.py` 5, `sync.py` 4, `device/gio_storage.py` 3, `device/storage.py` 3, `maps.py` 3, `service.py` 1 |

Some of the first kind cannot be reached at all: the handlers inside a PTP session (`ciq.py`)
see only `PtpError` and `CiqError`, and `_remount` asks the device only through calls that
already let the stall through.

- **13a. `DeviceNotResponding(UserFacingError)`, no longer a `DeviceError` (recommended; planned
  for a later version).** It
  keeps `says_replug`, `usb` and `during()`; the command line (`_run` catches
  `UserFacingError`: its words, exit code 1) and the window (`run_task`: one line of the log,
  `stalled.telling`) say it as today.
  - The 37 clauses before a handler of `DeviceError` go (a stall is no `OSError` either): 33
    whole, and the 4 tuples lose its name. The 19 before a broad handler stay, as long as those
    handlers do.
  - A stall would no longer reach the handlers of `DeviceError` that have no clause: the three
    that only ever see a drive's folder (`find_ums_devices`, `open_ums_storage`,
    `_eject_drive`, which cannot stall), and `ciq._noting`, which notes the errors of a PTP
    session (a stall comes before the session opens, from `exclusive_ptp`). Nothing that
    catches `UserFacingError` changes: `cli._run` as above, and `service.read_account_country`,
    which asks a server only.
  - The guard of the syntax tree (`tests/test_mtp_stall.py`) changes with it: it looks for
    handlers of `UserFacingError` and broad handlers on the way of the device without the
    clause, and for a clause before a handler that cannot catch the stall (dead). The tests
    that wait for a stall as a `DeviceError` are read one by one.
  - \+ About 70 lines less, and a new handler of `DeviceError` cannot swallow a stall by
    forgetting the clause.
  - − A change of type that every `except DeviceError` of the device's way depends on: one
    commit, the tests as the net, and the stall's path on the command line and in the window
    tried again in the tests (a sync, a backup, maps, apps, the opening of a device).
- **13b. As it is (chosen for now).**
  - \+ Nothing to change; the guard of the syntax tree keeps the clauses complete.
  - − 56 clauses, some dead, and one more for every new handler.

Size of 13a: about 70 lines removed and 10 changed in 11 modules, the guard rewritten (about 40
lines), one commit.

## 5. What changes

| Module | Change |
|---|---|
| `device/gio_storage.py` | The six reading operations asynchronous, each step under `_wait` with `STALL_S = 30`: `_list` with `enumerate_children_async` and `next_files_async` (portions of 100), all its steps on one main context (`_wait(context=…)`, section 6), `info` and `_size_now` with `query_info_async`, `_read` reading a stream in chunks of 256 KiB (`read_async`, `read_bytes_async`, `close_async`), all its steps on one main context too, `_read_start` the same up to its count, `_fs_attr` with `query_filesystem_info_async` (its action: "Cannot read the space on the device"). A step that reaches its limit raises `DeviceNotResponding`. |
| `device/storage.py` | `DeviceNotResponding(DeviceError)`, made by `DeviceNotResponding.during(action)`, with `says_replug = True` as `replug_text` reads it: "{action}: the device stopped responding. Unplug it and plug it in again." (Polish: "{action}: urządzenie przestało odpowiadać. Odłącz je i podłącz ponownie."). In step 1 (D5). |
| `device/storage.py` | `Storage.resolve` and `Storage.sizes` let it through (D6); `write` and `write_file` let it through without a clean-up (D7). |
| `device/gio_storage.py` | `_remount`, `_device_state` and `refresh` let it through (D6). |
| `device/gio_storage.py` | Fast failure: after a `DeviceNotResponding` the storage keeps it, and every later operation of it (reads, writes, a delete, a new folder, a refresh, a PTP session) raises it again at once, with the same words, instead of waiting 30 s each. A device opened again while it stays at its place on USB (its tile chosen before the list left it out) is refused at once too ("Cannot open the device"), as a new storage would wait for it again. A device plugged in again is a new mount and a new storage. |
| `device/gio_storage.py` | `open_device_storage` lets it through at once, also while it waits for a device (the command line, unless `--no-wait`): no try after it and no advice about another program. |
| `sync.py`, `backup.py` | `DeviceNotResponding` stops the sync or the backup: it goes past the handlers that let one unreadable file not stop them, as a device not mounted again does (the files uploaded, the files of the queue written). The backup stopped at any error and leaves no partial folder already. |
| other modules | D8: every handler of `DeviceError` lets it through, and the broad ones on the way of the device: TrueUp in a sync (`service._trueup`), an app of the queue and the app list (`ciq._install_pass`, `ciq.write_app_list`), a file other maps use (`maps._write_shared`), the count of activities to sync, the look at a device for a list (`gio_storage._probe`: not one more device that cannot be read), the quiet clean-ups after a failure (`Storage.delete_quietly`, `GioStorage._discard_partial`). The guard of the syntax tree counts in a storage's own class the calls of its `self` too. |
| `device/gio_storage.py` | Log lines, never with a path, a file name or the device's address. A step that reached its limit, in one warning: what it was (a read, a listing, a query, a query of the free space), how long before the step the device last answered the storage ("none yet"), how long before it this process made the storage, and who mounted the device: this process (`mount`, mounting again after a PTP session or a refresh), or not this process, as far as it knows from when it first found it mounted (such a mount may be older than the process), e.g. `device: no answer to a read in 30 s, the device stopped responding; its last answer: 2.3 s before this step; the storage opened 41.0 s before this step; mounted already when this process found it 41.2 s before`. What gvfs answered to the step's cancel is a line of information before it (`gio: read: no answer in 30 s, cancelled; …`). A step answered after more than 5 s (`SLOW_STEP_S`): `device: a listing took 6.2 s`. Quick steps log nothing, also at DEBUG. |
| `device/gio_storage.py` | D11, D12: the places on USB of the devices that stopped responding (`note_stalled`, `stalled_places`), left out of `find_devices` and of the devices counted on USB (`usb_state`, `garmin_products_on_usb`) until they are gone from there. |
| `gui/` | D4, D9–D12 (`gui/stalled.py`): after a task met a stall (`run_task`), or the USB poll found one that a work in the background met, the window keeps the device (its place and name), shows the banner on the start page and on the device view (`words.stalled_text`), closes that device if it is the one open, and shows no error dialog for it; the USB poll drops it once it is gone from its place. |
| `docs/guide.md` | What the message means and what to do. |

The 25 handlers of `DeviceError` in other modules, left as they were in step 1, let it through
since step 3 (D8):

- `ciq.py` (10): `Operation.hides_app_files`, `prepare_app_dir`, `_install_hidden` (3),
  `_ptp_delete`,
  `_remove_hidden` (3), `save_app_list_copy`;
- `ownmaps.py` (4): `list_own`, `overview`, `add`, `_failed_text`;
- `diagnostics.py` (4): `read_errors` (2), `at_open` (2);
- `maps.py` (2): `cleanup_plan`, `_write_unlock`;
- `cli.py` (1): `_status_updates_online`;
- `watch.py` (1): `_open_session`; `updates.py` (1): `presence`; `service.py` (1):
  `open_device_or_drive`.

Exempt, as they only ever see a drive's folder: `cli._eject_drive`, `gio_storage.find_ums_devices`
and `gio_storage.open_ums_storage`. The handlers inside our own PTP sessions cannot meet it
(libusb, not gvfs) and let it through all the same, so that the rule has no other exception.
Clean-ups after a failure that is said already keep only logging theirs (`delete_quietly`,
`_discard_partial`). The window (`gui/`) is step 5.

The command line says the error once and ends with exit code 1, as for other errors of the
device. The background service logs it and tells it in its notification of a sync that failed;
its next sync comes with the next mount of the device.

## 6. Risks

1. **A device that is slow but not stalled.** A step of more than 30 s ends as a stall. Nothing
   of the kind has been seen in reads; the slowest waits measured are mounts, which keep their
   own limits.
2. **The speed of reads in chunks.** `load_contents` reads in its own way; chunks of 256 KiB
   under `_wait` (a main context and a timer a chunk) may be faster or slower. To measure on a
   device against today's 30 activities in 7.6–8.0 s; the chunk size can change. gvfs reads at
   most 256 KiB for one request of a client (`modify_read_size` in
   `daemon/gvfsreadchannel.c`), so a chunk is one read of the device; should fewer bytes come,
   the read goes on to an empty answer.
3. **gvfs stays blocked.** A cancel ends only our wait; the backend of gvfs-mtp holds its mutex
   until the device is unplugged. That is why the advice is to unplug it, and why nothing is
   tried automatically: mounting the device again, as for a file read cut short, would wait on
   the same mutex (seen: a try every 32 s or so, each giving up after 30 s, until the device was
   unplugged).
4. **Writes after a stall** fail at once (fast failure). A write that stalls before any read has
   no limit yet (D1).
5. **The GTK thread.** `_wait` on the window's main thread would run a nested loop of the
   default context. The window reads the device only in its tasks today (its four reads in
   `gui/` run in background tasks); a new read in the window must stay so.
6. **The fake and the real gvfs.** Portions of a listing and chunks of a stream come from a fake
   of Gio in the tests; a listing of a large folder and a read of a large file are to be
   checked on a device.
7. **One main context for a listing.** gvfs gives the entries of a listing to its enumerator in
   the main context where the listing began (`client/gdaemonfileenumerator.c`: its D-Bus object
   is exported there, and `next_files_async` gets the entries through it). In a worker thread
   `_wait` makes a context of its own for each wait, so a portion waited for on a new context
   would never come and every listing would reach the limit. The steps of one listing therefore
   wait on one context (`_steps_context`); the fake gives portions only in that context, so a
   test fails if they do not. A listing that stalled is not closed: closing it would wait on the
   same blocked device; the enumerator is dropped. A file read does the same (one context, not
   closed after a stall); whether gvfs needs one context for a stream was not looked into, and
   it costs nothing.
8. **The window started again while the device is still stalled.** It knows nothing of the
   stall (the places are kept in memory only): its first read of the device waits 35 s (the
   limit and the wait for the answer to the cancel), then the banner comes. A stall whose place
   is not known, met only by a work in the background, is told by the next task, which fails at
   once with it.

## 7. Order of work

Each step starts with a test that fails on the code before it, keeps its texts and their Polish
in the same commit, and passes the usual gate before its commit: ruff without findings, the tests
with and without a display, and the program's folders unchanged by the tests. Each step waits for
acceptance. The fake of Gio (`tests/gio_fake.py`: `GioOp`, `answer_as_gio`) gets asynchronous
listings in portions, streams read in chunks, queries and filesystem queries, each able to
answer late or never.

1. **Listings and queries under `_wait`, and the error.** Failing tests:
   - a listing in several portions gives every entry, as today, in the main thread and in a
     worker (every portion waited for on the context the listing began in);
   - a listing slower in all than the limit, each portion in time, ends well;
   - a step of a listing (opening it, a portion, closing it) with no answer:
     `DeviceNotResponding` at the limit (a short limit in the test), whatever the answer to the
     cancel; a listing that stalled is not closed;
   - `query_info` with no answer: the same; a file that is not there still gives None;
   - the free space with no answer: the same;
   - another error keeps today's words ("Cannot list …", "Cannot read …");
   - its words in English and Polish (`replug_text` does not add the advice twice) (D5);
   - it goes past `resolve`, `sizes`, `_remount`, `_device_state` and `refresh` (D6), and a
     write cleans nothing up after it (D7).

   A guard that passes before and after: a folder on the disk read through the real Gio (the
   calls as Gio takes them).
2. **Reads in chunks under `_wait`.** Failing tests:
   - a file of 0 B, of less than a chunk, of exactly two chunks and of several reads whole, in
     the main thread and in a worker; a file given in smaller pieces than asked for too;
   - a read slow but steady, longer in all than the limit, ends well;
   - a step of a read (opening the file, a chunk, closing it) with no answer:
     `DeviceNotResponding`, whatever the answer to the cancel; a file whose read stalled is not
     closed;
   - another error keeps today's words ("Cannot read …"), and the file is closed after it once
     open;
   - `read_start` reads its count only (asking for what is still missing), gives all of a
     shorter file, closes the file after an error, and stops at a stall.

   The guard of step 1 also reads a file whole and in part through the real Gio.
3. **Fast failure, the sync and the backup, the handlers (D8).** Failing tests:
   - after a stall every later operation of the same storage fails at once with the same words:
     a listing, a query, a read, the space, a write, a delete, a new folder, a refresh, a PTP
     session; nothing more is asked of gvfs;
   - a sync stops with the message, once, exit code 1 on the command line; what it uploaded
     before stays done and the queue is not asked; a stall while a file of the queue is written
     stops it too, with nothing acknowledged; TrueUp lets it through;
   - `open_device_storage` with waiting stops at the first stall, without the advice about
     another program (each try would wait 35 s for the same blocked device; then, the device
     being on USB, the advice was added);
   - the background service does not open a stalled device again; an unlock file and a file
     other maps use are not written again; a stall is not remembered as a refusal to make the
     app folder; an app of the queue and the app list stop the install, nothing acknowledged
     and nothing put right on the device; the count of activities to sync and the look at
     a device for a list let it through;
   - a test of the syntax tree: every handler of `DeviceError` outside `gui/` has one of
     `DeviceNotResponding` before it, but for those that only see a drive.

   Guards that pass before and after: a backup stops at a stall and leaves no partial folder
   (it stopped at any error already), with the error said once and exit code 1; a new storage
   of the same device reads as usual.
4. **Log lines.** Failing tests:
   - a stall logs what it was, the times since the last answer and since opening, and who
     mounted the device (this process, before this process found it, not known), without a
     path, a name or the device's address; also when the device had not answered yet;
   - a step of more than 5 s logs one line.

   A guard that passes before and after: quick steps log nothing, also at DEBUG (quiet since
   step 1).
5. **The window (D4, D9–D12).** Failing tests:
   - a stall keeps its device out (not found, not counted on USB) until it is gone from its
     place; only that device of several; a stall while a device is opened knows its place;
   - after a stall in a task the banner says it with the device's name, the device open is
     closed, and no error dialog says it, also from a page that tells its failure itself;
   - no detection starts while the stalled device stays at its place on USB; once it is back
     at a new device number the banner goes and it is detected as any device plugged in;
   - a stall that only a work in the background met gets the banner from the USB poll (the
     device closed then, or by the running task);
   - a stall whose place is not known is told and keeps nothing out, its banner going when the
     devices on USB change; with another device open, that one stays open and the banner names
     the stalled one;
   - the banner comes before those of the setup and the sign-in, with no button; its words in
     English and Polish (one device, two, a name not known);
   - the banner wraps in the narrowest window, on the start page and on the device view.

   Guards that pass before and after: another failure, and a mount that reaches its limit with
   no stall before it, keep their error dialog and no banner. The window opens a device through
   `open_device_storage` without waiting (one try each time, no advice about another program);
   with the place left out (D11), no detection tries the stalled device again.
6. **Documentation**: `docs/guide.md` (a section of its own: what the message means and what to
   do), a known problem in `docs/troubleshooting.md` pointing to it, the status of this design.

On a device: the speed of reads in chunks against today's, and a listing of a large folder. A
stall cannot be made on purpose; the log lines of step 4 show when it has happened.

## 8. Size

About 250–350 lines of code (gio_storage about 180 for the six operations, fast failure and the
log lines; storage about 15; sync and backup about 10; the window about 60), about 450–550 lines
of tests with the fake of Gio, three texts with their Polish (the error, the action of the free
space, the banner), and 6 commits, one a step of section 7.
