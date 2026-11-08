# Design: a device that stops answering over MTP

Status: implemented. D3 (a request that keeps the device busy) is put off; D13 is 13b for now,
13a planned for a later version.

## 1. Scope

A device connected over MTP can stop answering in the middle of a read. Before this change the
program then waited for ever: the bar stood still, the window answered but the task never ended,
and only killing `gvfsd-mtp` and unplugging the device ended it. This design puts a limit on
every read from the device through gvfs: after 30 s without any progress the read is given up,
the user is told that the device stopped responding and to unplug it and plug it in again, and
nothing else is tried on that device until it is.

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
- Eleven seconds later a sync starts. Its plan reads
  `GARMIN/Backup/Backups/settings_backup.bak`, a file of a single-file type, for its MD5
  (`sync.plan_uploads`); the read never answers. The bar stands on "Looking for new files on the
  device…".
- `gio list` and `gio mount` on the device's address hang too; `lsusb` shows the device on USB;
  `dmesg` shows no error and no disconnect: not USB nor the driver, but the MTP session in the
  device.
- Eight minutes later, after `gvfsd-mtp` was killed: the sync stops after 480.7 s with "Error in
  stream protocol: End of stream" for that file.
- For three minutes after, the window tries to mount the device every 32 s or so; each mount
  gives up at its limit of 30 s. Only unplugging the device and plugging it in again helps; the
  syncs after it run as usual.

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
nothing kept. The information of a file does not: libmtp keeps it from the first time it is
asked.

## 3. Before this change

### The reads of `GioStorage`

Each was synchronous and given no cancellable (`None`), so none had a time limit:

| Operation | Gio call | Used for |
|---|---|---|
| listing a folder | `enumerate_children`, then `next_file` | `listdir`, `walk_files`, `resolve` (the case of a path), `exists` |
| the information of a file | `query_info` | a file's size and time, the check after a write |
| reading a file whole | `load_contents` | `read`: every file read whole |
| reading the start of a file | `read`, then `read_bytes` | `read_start`: the start of a file (headers) |
| the free space and capacity | `query_filesystem_info` | `free_space`, `capacity` |

About a hundred places in fifteen modules read through them (the sync, the backup, activities,
diagnostics, Connect IQ, maps, own maps, music, updates, the command line); the limit belongs in
these operations, not in their callers. The window reads the device only in its tasks, off the
GTK thread.

### Waiting on Gio

`gio_storage` has one wait for Gio: it runs one asynchronous Gio operation to its end, in a
worker thread on a main context of its own, in the main thread on the default one. At its time
limit it cancels the operation and waits `CANCEL_WAIT_S` (5 s) more for its answer, then raises
`GioTimedOut` ("…: the device did not answer in time") and logs what happened. Only the mount
(30 s), the unmount (30 s) and the eject (60 s) used it.

### Measured

From three weeks of the program's log:

- Reads of activities at 1.26–1.37 MB/s each; none slower.
- The long syncs (up to 40 s) mostly wait for TrueUp. No "End of stream" and no time limit
  reached outside a stress test, but the stall above.
- The time of a single read is not logged, so a stall of a few seconds would not show.

## 4. Decisions

### D1: which operations have a limit — chosen: 1a

- **1a. Every reading operation of `GioStorage` (recommended; chosen):** listing a folder, the
  information of a file (also its size after a write), reading a file whole or its start, the
  free space and capacity.
  - \+ Every caller is covered at once, also those added later.
  - \+ The stall seen was in the plan of a sync, which a limit on file contents only would
    cover by chance; the listings and queries go through the same blocked mutex.
  - − Six operations change at once.
- **1b. Only reads of file contents** (a file read whole or its start).
  - − A listing or a query blocked behind the same mutex still waits for ever.
- **1c. Reads and writes.**
  - − A write cut off leaves a part of a file, which the clean-up after a failed write must then
    handle with no answer from the device; in the Flatpak writes go through `gio copy` on the
    host. Left for a design of its own.

### D2: the limit — chosen: 2a

- **2a. 30 s without progress a step; files read in chunks of 256 KiB (recommended; chosen).** A
  step is one answer of Gio: opening a file, a chunk of it, closing it; opening a listing, a
  portion of it (`next_files_async`), closing it; one `query_info`; one
  `query_filesystem_info`. Each is waited for, with the limit, by the wait for Gio.
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
  restarted: a new device number) the banner goes and detection works as usual.
- **4b. As before this change.** A try to mount every 32 s or so, each giving up after 30 s,
  without saying why.

### D5: the error's words — chosen: 5a

- **5a. Its own words from the start (recommended; chosen):** `DeviceNotResponding`, with its
  English and Polish text and `says_replug`, and the tests of its words came with the first
  change that raises it.
  - \+ The error is tested with the words it is shown with; no text in between to change later.
- **5b. First the words of the other time limits** ("…: the device did not answer in time"), its
  own words later.
  - − A text shown for a while, then changed.

### D6: the handlers of the device layer — chosen: 6a

- **6a. `DeviceNotResponding` goes past the handlers of the device layer (recommended;
  chosen).** `Storage.resolve` and `Storage.sizes` take a listing that failed for a missing
  folder; mounting the device again after a PTP session looks for `GarminDevice.xml` up to 20
  times, a second apart, whatever fails; the check of the device's state takes a failure for
  "between"; `refresh` takes one for "not refreshed". Each lets the error through and keeps its
  answer for any other failure.
  - \+ Without it a stall while a path is looked for reads as a missing file after 35 s ("File
    not found on the device", an empty listing), and a sync goes on, waiting 35 s at every next
    path; mounting again waits up to 20 times 35 s.
  - − Five more handlers to change.
- **6b. Leave them as they are.**
  - − A stall would read as missing files.

### D7: a write that meets a stall — chosen: 7a

- **7a. No clean-up after it (recommended; chosen):** `write` and `write_file` let
  `DeviceNotResponding` through as they let `CopyNotStarted`, without the clean-up of a partial
  file. A copy has no time limit (D1), so the error comes only before it: from the query of the
  file's size there or from finding its folder.
  - \+ Nothing was touched, and a clean-up would ask the same blocked device again.
- **7b. Clean up as after any failure.**
  - − Waits another 35 s for the same device, then fails the same way.

### D8: the handlers in other modules — chosen: 8a

Besides the handlers of `DeviceError` in the modules outside the device layer (section 5), a few
broad handlers (`except Exception`) on the way of the device catch the error too: the loops of
a sync (files uploaded, files of the queue written, TrueUp, apps of the queue) and the look at a
device for a list (for `status` and the start page).

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
    USB, for the whole software version: the device would not be asked again until it is
    updated.
  - − About 30 small changes in 10 modules; in the window a stall while a device opens (the
    count of its activities) is told to that task, not taken for "not known".
- **8b. Only handlers that let one item fail and go on, or try again.** Summaries (the line of
  `status`, the overview of own maps, the comparison of updates) answer "not known".
  - − `status`, `maps` and `updates` would end with exit code 0, the stall hidden in a line.
- **8c. Only the sync and the backup.**
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
  place on USB of its device (`GioStorage.usb`; a storage over MTP has the place from the
  start, so that a stall while a device is opened has it too), and gio_storage keeps it
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

`DeviceNotResponding` is a `DeviceError` (D5), so every handler of `DeviceError` that a device
over MTP can reach has a clause before it that raises the stall on (D6, D8), and so has every
broad handler (`Exception`, `BaseException`) on the way of the device: about sixty clauses in a
dozen modules, two thirds of them before a handler of `DeviceError`, some in a tuple with
another error. Some of the first kind cannot be reached at all: the handlers inside a PTP
session see only `PtpError` and `CiqError`, and mounting the device again after a PTP session
asks it only through calls that already let the stall through.

- **13a. `DeviceNotResponding(UserFacingError)`, no longer a `DeviceError` (recommended; planned
  for a later version).** It keeps `says_replug`, `usb` and `during()`; the command line (which
  catches `UserFacingError`: its words, exit code 1) and the window (`run_task`: one line of the
  log, `stalled.telling`) say it as now.
  - The clauses before a handler of `DeviceError` go (a stall is no `OSError` either); in a
    tuple only its name goes. Those before a broad handler stay, as long as those handlers do.
  - A stall would no longer reach the handlers of `DeviceError` that have no clause: those that
    only ever see a drive's folder (`find_ums_devices`, `open_ums_storage` and the eject of a
    drive on the command line, which cannot stall), and the one of Connect IQ that notes the
    errors of a PTP session (a stall comes before the session opens, from `exclusive_ptp`).
    Nothing that catches `UserFacingError` changes: the command line as above, and
    `service.read_account_country`, which asks a server only.
  - The guard of the syntax tree (`tests/test_mtp_stall.py`) changes with it: it looks for
    handlers of `UserFacingError` and broad handlers on the way of the device without the
    clause, and for a clause before a handler that cannot catch the stall (dead). The tests
    that wait for a stall as a `DeviceError` are read one by one.
  - \+ The clauses before the handlers of `DeviceError` go, and a new handler of `DeviceError`
    cannot swallow a stall by forgetting the clause.
  - − A change of type that every `except DeviceError` on the device's way depends on: the tests
    are the net, and the stall's path on the command line and in the window is tried again in
    them (a sync, a backup, maps, apps, the opening of a device).
- **13b. As it is (chosen for now).**
  - \+ Nothing to change; the guard of the syntax tree keeps the clauses complete.
  - − About sixty clauses, some dead, and one more for every new handler.

## 5. What changed

### The reads (`device/gio_storage.py`)

- The six reading operations are asynchronous, each step waited for at most `STALL_S = 30`:
  - a listing with `enumerate_children_async` and `next_files_async` (portions of 100), all its
    steps on one main context (section 6);
  - the information of a file with `query_info_async`;
  - a file read whole as a stream in chunks of 256 KiB (`read_async`, `read_bytes_async`,
    `close_async`), all its steps on one main context too;
  - the start of a file the same, up to its count, asking each time for what is still missing;
  - the free space and capacity with `query_filesystem_info_async` (its action: "Cannot read the
    space on the device").
- A step that reaches its limit raises `DeviceNotResponding`. Another error keeps its words
  ("Cannot list …", "Cannot read …"), and a file once open is closed after it; a file that is
  not there still has no information, as before.
- Mounting again after a PTP session, the check of the device's state and `refresh` let the
  stall through (D6).
- Fast failure: after a `DeviceNotResponding` the storage keeps it, and every later operation of
  it (reads, writes, a delete, a new folder, a refresh, a PTP session) raises it again at once,
  with the same words, instead of waiting 30 s each; nothing more is asked of gvfs. A device
  opened again while it stays at its place on USB (its tile chosen before the list left it out)
  is refused at once too ("Cannot open the device"), as a new storage would wait for it again.
  A device plugged in again is a new mount and a new storage, and reads as usual.
- `open_device_storage` lets it through at once, also while it waits for a device (the command
  line, unless `--no-wait`): no try after it, since each would wait 35 s for the same blocked
  device, and no advice about another program.
- D11, D12: the places on USB of the devices that stopped responding (`note_stalled`,
  `stalled_places`), left out of `find_devices` and of the devices counted on USB (`usb_state`,
  `garmin_products_on_usb`) until they are gone from there.

### The error (`device/storage.py`)

- `DeviceNotResponding(DeviceError)`, made by `DeviceNotResponding.during(action)`, with
  `says_replug = True`, so that `replug_text` does not add the advice twice: "{action}: the
  device stopped responding. Unplug it and plug it in again." (Polish: "{action}: urządzenie
  przestało odpowiadać. Odłącz je i podłącz ponownie.") (D5).
- `Storage.resolve` and `Storage.sizes` let it through (D6); `write` and `write_file` let it
  through without a clean-up (D7).

### The log lines (`device/gio_storage.py`)

Never with a path, a file name or the device's address.

- A step that reached its limit, in one warning: what it was (a read, a listing, a query, a
  query of the free space), how long before the step the device last answered the storage
  ("none yet"), how long before it this process made the storage, and who mounted the device:
  this process (`mount`, mounting again after a PTP session or a refresh), or not this process,
  as far as it knows from when it first found it mounted (such a mount may be older than the
  process). For example: `device: no answer to a read in 30 s, the device stopped responding;
  its last answer: 2.3 s before this step; the storage opened 41.0 s before this step; mounted
  already when this process found it 41.2 s before`.
- What gvfs answered to the step's cancel is a line of information before it (`gio: read: no
  answer in 30 s, cancelled; …`).
- A step answered after more than 5 s (`SLOW_STEP_S`): `device: a listing took 6.2 s`.
- Quick steps log nothing, also at DEBUG.

### The sync, the backup and the other modules

- `sync.py`, `backup.py`: `DeviceNotResponding` stops the sync or the backup: it goes past the
  handlers that let one unreadable file not stop them, as a device not mounted again does (the
  files uploaded, the files of the queue written). What a sync uploaded before the stall stays
  done, and the queue is not asked; a stall while a file of the queue is written stops it with
  nothing acknowledged. The backup stopped at any error and leaves no partial folder already.
- Other modules (D8): every handler of `DeviceError` lets it through, and the broad ones on the
  way of the device:
  - TrueUp in a sync;
  - an app of the queue and the app list (`ciq.write_app_list`), which stop the install with
    nothing acknowledged and nothing put right on the device;
  - a file other maps use and an unlock file, not written again;
  - the count of activities to sync;
  - the look at a device for a list (not one more device that cannot be read);
  - the quiet clean-ups after a failure (`Storage.delete_quietly`, the clean-up of a partial
    file).
- The background service does not open a stalled device again.
- The guard of the syntax tree counts in a storage's own class the calls of its `self` too.

### The window (`gui/`)

D4, D9–D12 (`gui/stalled.py`):

- After a task met a stall (`run_task`), or the USB poll found one that a work in the background
  met, the window keeps the device (its place and name), shows the banner on the start page and
  on the device view (`words.stalled_text`), closes that device if it is the one open, and shows
  no error dialog for it; the USB poll drops it once it is gone from its place.
- Another failure, and a mount that reaches its own limit with no stall before it, keep their
  error dialog and no banner.
- The window opens a device through `open_device_storage` without waiting (one try each time,
  no advice about another program).
- The banner wraps in the narrowest window, on the start page and on the device view.

### The documents

`docs/guide.md` says what the message means and what to do; `docs/troubleshooting.md` has a
known problem pointing to it.

### Exceptions and the command line

Exempt from D8, as they only ever see a drive's folder: `find_ums_devices`, `open_ums_storage`
and the eject of a drive on the command line. The handlers inside our own PTP sessions cannot
meet it (libusb, not gvfs) and let it through all the same, so that the rule has no other
exception. Clean-ups after a failure that is said already keep only logging theirs
(`delete_quietly`, the clean-up of a partial file).

The command line says the error once and ends with exit code 1, as for other errors of the
device. The background service logs it and tells it in its notification of a sync that failed;
its next sync comes with the next mount of the device.

## 6. Risks

1. **A device that is slow but not stalled.** A step of more than 30 s ends as a stall. Nothing
   of the kind has been seen in reads; the slowest waits measured are mounts, which keep their
   own limits.
2. **The speed of reads in chunks.** A whole read (`load_contents`) reads in its own way; chunks
   of 256 KiB, each waited for (a main context and a timer a chunk), could be faster or slower.
   Measured on a fēnix 8, they are as fast as the whole reads were; the chunk size can change.
   gvfs reads at most 256 KiB for one request of a client (`modify_read_size` in
   `daemon/gvfsreadchannel.c`), so a chunk is one read of the device; should fewer bytes come,
   the read goes on to an empty answer.
3. **gvfs stays blocked.** A cancel ends only our wait; the backend of gvfs-mtp holds its mutex
   until the device is unplugged. That is why the advice is to unplug it, and why nothing is
   tried automatically: mounting the device again, as for a file read cut short, would wait on
   the same mutex (seen: a try every 32 s or so, each giving up after 30 s, until the device was
   unplugged).
4. **Writes after a stall** fail at once (fast failure). A write that stalls before any read has
   no limit yet (D1).
5. **The GTK thread.** The wait for Gio on the window's main thread would run a nested loop of
   the default context. The window reads the device only in its tasks (its reads in `gui/` run
   in background tasks); a new read in the window must stay so.
6. **The fake and the real gvfs.** Portions of a listing and chunks of a stream come from a fake
   of Gio in the tests (`tests/gio_fake.py`), each able to answer late or never; a listing of a
   large folder and a read of a large file are to be checked on a device. A guard reads a folder
   on the disk, and a file whole and in part, through the real Gio, so that the calls are as Gio
   takes them.
7. **One main context for a listing.** gvfs gives the entries of a listing to its enumerator in
   the main context where the listing began (`client/gdaemonfileenumerator.c`: its D-Bus object
   is exported there, and `next_files_async` gets the entries through it). In a worker thread
   the wait for Gio makes a context of its own for each wait, so a portion waited for on a new
   context would never come and every listing would reach the limit. The steps of one listing
   therefore wait on one context; the fake gives portions only in that context, so a test fails
   if they do not. A listing that stalled is not closed: closing it would wait on the same
   blocked device; the enumerator is dropped. A file read does the same (one context, not
   closed after a stall); whether gvfs needs one context for a stream was not looked into, and
   it costs nothing.
8. **The window started again while the device is still stalled.** It knows nothing of the
   stall (the places are kept in memory only): its first read of the device waits 35 s (the
   limit and the wait for the answer to the cancel), then the banner comes. A stall whose place
   is not known, met only by a work in the background, is told by the next task, which fails at
   once with it. A stall cannot be made on purpose; the log lines of section 5 show when one
   has happened.
