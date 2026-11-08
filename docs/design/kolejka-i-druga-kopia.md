# Garmin Connect's queue, a second install, the test drive

Status: implemented, with the recommended option of each decision.

Three changes to the sync, designed together because they meet in the same places of the sync
(the queue and TrueUp after the uploads, the records of a device), and one sentence for
`primary set`. Nothing here changes what is written to a device or sent to Garmin Connect for a
device synced by one install; it changes what is said, and what a second install or a test
drive may do.

## 1. Where a file of the queue comes from

A device's queue may carry files another device of the account made: Garmin Connect forwards the
performance measurements of one device to the others (its "Biometrics Data", FIT type 44, keep
the `file_id` of the device that made them). openExpress writes them as Garmin Express does,
which stays so. What it lacked was saying it: before this change the log named only the type
and the count.

- **D1 (recommended: 1a).** What is said of each file written from the queue.
  - **1a.** From its FIT `file_id`: the file type, the maker and when it was made. The maker is
    the account's device of that product (`006-B<product>-..` against the remembered devices and
    the account's list), "this device" for the open one, "Garmin Connect" for product 65534,
    otherwise "Garmin product N". The serial number of `file_id` is never read into it.
  - **1b.** The type and the count only (as before this change).
- **D2 (recommended).** Where it is said: one line in the log per file written
  (`download: FIT_TYPE_44 written, made by fenix 8 on 2026-01-02 03:04`) and the counts by maker
  in the summary line (`downloads: 5 written (fenix 8×4, Garmin Connect×1)`); in the dry run
  of `openexpress sync`, beside each queue file it lists. The window says nothing new (its sync
  summary stays as it is).
- A file that is no FIT file (an app) or whose `file_id` does not read is "maker not given".
- `fit.describe` gets the maker and the product beside the type and the time it reads already.

## 2. A second install of openExpress on the same computer

The version from source and the Flatpak keep their records apart (`devices/<unit>/sync/`;
[The Flatpak and the version from source](../install.md#the-flatpak-and-the-version-from-source)),
and each takes its own lock. Syncing one device from both sends files again that the other sent
(single files whose content changed since this install's own record, files of several-file types
that came since its own last sync), and the two may sync at the same time. Garmin Express keeps
one record per computer. The Flatpak cannot see the files of the version from source (it gets no
new permission for it); the version from source can read the Flatpak's.

- **D3 (recommended: 3a).** What the version from source does with the Flatpak's records of the
  same device (read only, never written):
  - **3a.** A file the Flatpak recorded as sent (by name; a single file by the same MD5) is not
    sent again; the version from source then records it as its own record records any sent file.
    A message of the queue the Flatpak wrote but could not acknowledge (`unacked.json`) is only
    acknowledged, not written again.
  - **3b.** A warning only, the records kept apart.
  - **3c.** No sync from source of a device the Flatpak synced, unless asked with a flag.
- **D4 (recommended).** The lock: the version from source also takes the Flatpak's
  `sync.lock` (without waiting); held, its sync stops before anything is read: "openExpress
  (Flatpak) is syncing a device now; try again when it is done." (the same words when it stops
  `baseline` or forgetting a device, which take the same lock)
- **D5 (recommended).** What is said: when the Flatpak's last sync of the device is newer than
  this install's, the sync says once (the window's summary, the command line):
  "This device was also synced by openExpress (Flatpak), last on {date}; files it sent are not
  sent again" (a line of the details, without a full stop). The row **Last Sync** of the device
  page stays this install's own (a change of it needs its own patterns and words; left for
  later).
- An acknowledgement Garmin Connect answers with 404 (the message is gone) counts as done, in
  either install, so that a message both installs know is not tried for ever.
- The Flatpak's folder: `~/.var/app/io.github.tomaszbojanowski.OpenExpress/data/openexpress`.
  The Flatpak itself changes in nothing of this section.

## 3. The test drive

A USB drive taken for a Garmin drive through `OPENEXPRESS_TEST_DRIVE_USB` carries the
`GarminDevice.xml` of a real device, so Garmin Connect takes it for that device: its sync takes
the device's queue (and acknowledges it, so the device never gets those files), may run TrueUp,
and writes over the device's records in that install.

- **D6 (recommended).** A test drive (a drive whose USB vendor is not Garmin's) syncs without
  Garmin Connect's queue and without TrueUp: nothing is downloaded or acknowledged. The sync says
  so in one line: "A test drive: Garmin Connect's queue and TrueUp are left to the device it
  stands in for" (a line of the details, without a full stop).
- **D7 (recommended: 7a).** Its records:
  - **7a.** Kept apart from the device's (`devices/<unit>/test-drive/`): its uploaded files, its
    last sync and the facts of its tile never touch the device's.
  - **7b.** No uploads either (a dry run only).
- Uploads from a test drive stay possible (trying the way of a drive is what it is for); its
  files are those the tester put on it.

## 4. `primary set`

The command line says, before it changes anything, the sentence the window's confirmation
dialog already says: "The primary training device is the main source of performance and
training data in Garmin Connect." Nothing else changes.

## Texts

| English | Polish |
|---|---|
| made by {maker} (the dry run, before the time the file was made) | twórca: {maker} |
| this device | to urządzenie |
| Garmin product {number} | produkt Garmin {number} |
| openExpress (Flatpak) is syncing a device now; try again when it is done. | openExpress (Flatpak) synchronizuje teraz urządzenie; spróbuj ponownie, gdy skończy. |
| This device was also synced by openExpress (Flatpak), last on {date}; files it sent are not sent again | To urządzenie synchronizował też openExpress (Flatpak), ostatnio {date}; pliki, które wysłał, nie są wysyłane ponownie |
| A test drive: Garmin Connect's queue and TrueUp are left to the device it stands in for | Dysk testowy: kolejka Garmin Connect i TrueUp zostają dla urządzenia, które zastępuje |

The log stays in English, as always ("made by …", "maker not given").

## Tests

- A queue file whose `file_id` names another device of the account: the log line names that
  device and the time; one of the open device: "this device"; product 65534: "Garmin Connect";
  an app: "maker not given"; the summary counts by maker; the serial number is in no line.
- The dry run lists the maker beside each queue file.
- Two data folders, one standing for the Flatpak's: files it recorded are not planned again by
  the version from source; a single file of another MD5 is; a message in its `unacked.json` is
  acknowledged and not written; its lock held stops the sync before anything is read; its newer
  last sync gives the sentence once; nothing in its folder changes (snapshot before and after).
- In the Flatpak (`in_flatpak()`), nothing of section 2 runs.
- An acknowledgement answered 404 counts as done.
- A test drive: no request to the queue, no TrueUp, its records under `test-drive/`, the
  device's records unchanged; a Garmin drive (vendor 091e) syncs with the queue and TrueUp.
- `primary set` without `--apply` prints the sentence.
