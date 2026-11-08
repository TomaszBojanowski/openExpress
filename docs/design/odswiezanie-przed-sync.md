# Design: reading a cut-short file again after a fresh mount

Status: implemented (D1 1a, D2 2a, D3 3b, D4 4a). Not seen on a device yet: a cut-short file
cannot be made on purpose, and the log tells when it has happened ("upload: a … file read again
after the device was mounted again", "backup: …").

## 1. Scope

A FIT file that the device writes while it is connected over MTP can be read cut short: gvfs
gives the size the file had when it was first looked at. Before this change the sync and the
backup found such a file and left it out; the sync read it again at the next sync, which in
practice meant after the device had been unplugged and plugged in again. This design lets them
get the whole file without unplugging: gvfs mounts the device again, which gives fresh sizes,
and the file is read once more.

In scope, for a device over MTP through gvfs:

1. The sync, in all three places that run it (the window, the command line, the background
   service; all through `service.run_sync`).
2. The backup, in the window and on the command line (`backup.run_backup`).

Not in scope:

- USB drives and folders: they read the file system, whose sizes are current.
- The diagnostic files and the app list, which read device files the same way.
- Files other than FIT: there is no check of their wholeness to start from.

## 2. The problem

- gvfs-mtp opens one libmtp session when it mounts the device and keeps it. libmtp keeps what it
  learnt of each object, its size among it, from the first time it is asked: `ptp_object_want`
  (libmtp 1.1.14, `src/ptp.c:6659`) returns the kept object info without asking the device, and
  `LIBMTP_Get_Filemetadata` goes that way.
- The device announces a changed object with the PTP event `ObjectInfoChanged`. libmtp ignores
  it ("TODO: rescan object cache", `src/libmtp.c:2224`) and has no `LIBMTP_EVENT_*` for it, so
  gvfs never hears of it. libmtp 1.1.22 with gvfs-mtp 1.60.3 behaves the same.
- So `query_info` through Gio gives the old size, and a read ends at it. Asking Gio again
  changes nothing: the same kept info answers.
- Seen once: a fēnix 8 rewrote `Garmin/Coach/schedule.fit` while it was connected. The sync
  read it cut short, and Garmin's server refused the file (415). After the device was unplugged
  and plugged in again, the same file was whole, with a right CRC. When the device wrote it is
  not known.
- Fresh sizes without unplugging come from a new libmtp session, that is gvfs unmounting the
  device and mounting it again; or from a PTP session of our own (`device/ptp.py`, a fresh
  `GetObjectInfo`), which needs the unmount too. This design uses the first: in the Flatpak it
  needs no USB permission.

## 3. Before this change

### The sync

1. The device is found: in the window and the background service by the mounts of
   `Gio.VolumeMonitor` (the background service starts a sync 5 s after a mount appears); on the
   command line by `gio_storage.open_device_storage`. A `GioStorage` on its `mtp://` address,
   then a `service.DeviceSession`.
2. `service.run_sync`, the one entry of all three places, takes `sync_lock` (one sync at a
   time across processes), plans (`plan_sync`) and applies (`apply_sync`).
3. The plan (`sync.plan_uploads`) lists the device's folders with the sizes gvfs gives. A file
   of a single-file type (rewritten in place, as `schedule.fit`) is read already here, for its
   MD5 against the one kept from its last upload; its data stays on the candidate
   (`UploadCandidate.data`).
4. The uploads read each file: the data the plan kept, or a read from the device.
   `sync.still_being_written` checks each FIT file that is not a ZIP with `fit.incompleteness`;
   a cut-short one was left out, in both passes: the match of activities Garmin Connect already
   has, and the upload. For the types that must arrive in order (monitoring, sleep) the later
   files of the type wait. The summary gets the step `still_written`.
5. Then Garmin Connect's queue, TrueUp and Connect IQ.

### The backup

The backup lists every file first, then reads them one by one. A FIT file is checked with
`fit.not_whole`, the check behind `still_being_written`: one cut short was left out (counted,
`backup.incomplete_text`); one not whole otherwise is copied as it is. The bytes of the backup
were counted from the listed size.

### Mounting again

It existed already, for the PTP sessions that reach files gvfs does not show:

- The unmount: `Gio.Mount.unmount_with_operation`. gvfs closes its libmtp session; the device
  stays on USB.
- The mount again: `mount_enclosing_volume` on the same `mtp://` address, then a wait until
  `GARMIN/GarminDevice.xml` can be seen (up to 20 s, once a second).
- Time limits: 30 s for each. An unmount cancelled at its limit is followed by watching how the
  device settles (the same state for 2 s, at most 15 s) and mounting it again if it is
  unmounted, up to 3 tries.
- `own_remount`: for 15 s after our own unmount and mount, the window and the background service
  do not take them for a device unplugged and plugged in; only within the process that did them.
- The address stays the same, so the `GioStorage` goes on working afterwards.

Measured on a fēnix 8 over the PTP sessions in ordinary use: the unmount, the session and the
mount until `GarminDevice.xml` is seen take 0.2–0.5 s, in the Flatpak too, and no unmount went
over its time limit and no mount failed. An Edge 1050 went through a stress test of unmounts and
mounts without an error. How long the first listing of the folders takes after a mount was not
measured.

## 4. Decisions

### D1: when the device is mounted again — chosen: 1a

- **1a. After a cut-short FIT file is found, at most once a sync or a backup (recommended;
  chosen).**
  - \+ Costs nothing in a sync without such a file, that is nearly every sync; 0.2–0.5 s when it
    happens.
  - \+ Catches a file the device wrote at any time before it was read, also during the sync.
  - \+ The new risks (section 6) come only where the file would wait anyway until the device is
    unplugged and plugged in again.
  - − More code than 1b: the data the plan kept of a single-file type must be read again, and the
    order of monitoring and sleep must hold.
- **1b. Before every sync.**
  - \+ Least code: one call in `run_sync`, under the lock, before the plan.
  - − 0.2–0.5 s and a new listing of every folder in every sync; a new point of failure in every
    sync; the file manager loses the device at every sync; another process (section 6, risk 3)
    sees an unplug at every sync of the command line or the background service.
  - − Catches only what the device wrote before the sync began.
- **1c. Before a sync, only when the device was connected less than X seconds ago.**
  - − The command line does not know when the device was connected (another process, no
    signals).
  - − The background service syncs 5 s after the device appears, so for it this is always 1b.
  - − Nothing tells X: when the device writes is not known (a phone that syncs the device over
    Bluetooth while the cable is in could write at any time; not checked).

### D2: when in the uploads — chosen: 2a

- **2a. At the first cut-short file, then that file read again (recommended; chosen).**
  - \+ Files of a type that must arrive in order keep their order; later cut-short files of the
    same sync are read again without another mount.
  - − A mount that fails stops the rest of the sync (the device is not there to read), with the
    message to unplug the device and plug it in again. In ordinary use no mount has failed
    (section 3).
- **2b. After all uploads, the cut-short files read again in one pass.**
  - \+ A failed mount cannot touch the uploads before it.
  - − Monitoring and sleep files held back behind a cut-short one must join the pass in order;
    Garmin Connect's queue, which follows, needs the device mounted all the same.

The backup does as 2a: at its first cut-short FIT file, then that file read again; its files
have no order to keep.

### D3: what else reads again — chosen: 3b

- **3a. Only the sync**: the sync is where a cut-short file matters most, since Garmin Connect
  gets it only after the device is unplugged and plugged in again.
- **3b. Also the backup (chosen)**, which has the same check: the same mechanism, at most once
  a backup.
  - \+ A backup taken while the device writes a file holds the whole file instead of leaving it
    out.
  - − More to test; the diagnostic files and the app list still do not have it.

### D4: what the user is told — chosen: 4a

- **4a. A step of the progress and a line of the log (recommended; chosen).** The step:
  "Refreshing the device's files…" (Polish: "Odświeżanie plików urządzenia…"). A file read
  whole after the refresh is uploaded or copied as any other; one still cut short gives the step
  `still_written` in the sync and the count of files left out in the backup, as before this
  change. The row "Last Sync" does not change, so no new pattern in `tests/data/last_sync/`.
- **4b. Also a line of the summary**, e.g. "1 file read again after the device was mounted
  again".
  - − A new pattern of the row, with its words in English and Polish.

## 5. What the sync and the backup do

- **The refresh.** On a folder or a USB drive it does nothing (`Storage.refresh` returns
  False). On a device over MTP (`GioStorage.refresh`) it says its step of the progress, then
  unmounts the device from gvfs and mounts it again, without a PTP session, in the same way as
  the PTP sessions: the quiet time in which `own_remount` hides our own unmount and mount, the
  settling and mounting back after an unmount that did not finish in time or after Ctrl+C, the
  mount again also after an error. It returns True once the device is mounted again. It returns
  False and leaves the device as it is when the device's place on USB is not known (our own
  unmount would be taken for an unplug), and when the unmount is refused or does not finish in
  time (then whether gvfs has fresh sizes is not known).
- **The sync**, in both passes of the uploads: at the first cut-short FIT file one refresh; the
  data the plan kept of that file is dropped and the file is read from the device again; a later
  cut-short file of the same sync is read again without another refresh; one still cut short is
  left out.
- **The backup**: the same, at most once a backup. The bytes of the backup are counted from what
  was read rather than from the listed size.
- **A device not mounted again** stops the sync or the backup with its error and what to do
  (unplug the device and plug it in again), unless the error says that itself
  (`storage.replug_text`). The app list says the same words for its own case of a device not
  mounted again.
- **The progress**: the text of the step is `progress.refreshing_text()`, next to the texts of
  downloading and copying, with its Polish. Only a refresh on a device over MTP says it, so a
  folder and a USB drive never show it.
- **The log**: the refresh, each file read again, a refresh not done; the type and the sizes,
  never the name of the file.
- **Unchanged**: the background service, the window and `service.py`, which go through
  `run_sync` and `run_backup`; `own_remount` already hides our own mount.
- **`docs/guide.md`** says it for the sync ("Sync details") and for the backup: a cut-short
  file is read again after the device is mounted again; only one still cut short is left out,
  with the advice to unplug the device and plug it in again.

The dry run of the command line does not refresh: it only plans. A single-file type is read
there for its MD5, so its count may include a cut-short file.

## 6. Risks

1. **The device during the refresh.** Closing the MTP session is not unplugging: changes written
   to the device still take effect only once it is unplugged, as after the PTP sessions. An Edge
   1050 restarts when it is unplugged, not when a session closes. A file still being written when
   it is read again is still cut short and is left out.
2. **A mount that fails.** The sync or the backup stops with the message to unplug the device
   and plug it in again. What the sync uploaded before is kept; a backup stopped this way leaves
   no partial folder, as any stopped backup. In ordinary use no mount has failed (section 3).
3. **Other processes.** `own_remount` knows only its own process's mounts, a risk known before
   this design and the same as for the PTP sessions.
   - The background service, run by another process, takes the refresh for a device plugged in
     and tries a sync 5 s later: `SyncBusy` while the first sync runs, or a second sync
     (harmless, with its notification) when the first has ended.
   - A window open in another process sees it as it sees a PTP session of another process.
   - With 1a this is rare; with 1b it would happen at every sync.
4. **The file manager.** The device leaves GNOME Files for a moment, as with a PTP session.
5. **KDE.** openExpress needs gvfs-mtp there too. KDE's MTP service (kmtpd, in kio-extras) opens
   devices when it starts and when one is plugged in; a mount of gvfs is no USB event, so it
   should not take the device in the gap. Not checked on KDE.
6. **Flatpak.** The unmount and the mount go to gvfs over D-Bus, as for the PTP sessions of the
   Flatpak; no USB permission is needed.
7. **Threads.** `Gio.VolumeMonitor` is used from the thread of a task, as for the PTP sessions
   (GIO documents it for the main thread; it has worked so far).
8. **Other tasks of the window** that read the device during a sync or a backup could meet the
   moment it is unmounted. The window runs one task at a time: another task asked meanwhile
   gets only a toast.
