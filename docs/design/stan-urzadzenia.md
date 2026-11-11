# Design: the page Device Status

Status: steps a to c and e implemented, step d dropped, step f (the remembered device) decided.
Decided: D1 (the name), D4 (layout A), D5's threshold of little space, D8 (the log always kept),
D9 (removal at step e: both candidates, always confirmed, a copy first where it fits, an update in
the main folder only shown, the command line too), D13's remembered device (A, from the records,
the device's actions left out). The other decisions are proposed, each with the option
recommended. Mockups of layout A (step c, read only, and step e, with removal and
its confirmations), in English and Polish, at 1000 × 740 (the size of the program's screenshots)
and 360 × 740, light and dark, were drawn with libadwaita widgets on made-up data; every number and
date below is made up.

## 1. What the page is for

One place for the state of the connected device that today is spread over the device page, the
Preferences and the command line, or not shown at all:

- **at a glance:** whether anything needs attention (crashes, little free space);
- **storage:** what takes the space, by category;
- **crashes and updates:** the device's own crashes (only those that restarted it) and its software
  versions over time;
- **freeing space:** the few files that can go, and only after a copy;
- **a report:** the openExpress report with the device's diagnostics.

Out of scope: the battery (no cheap way to read it), anything sent anywhere (the rule "no
telemetry" stays: nothing leaves the computer unless the user sends it), editing the device's
settings.

## 2. What the program has today

| Where | What | Notes |
|---|---|---|
| Device page, group Storage | **Device Storage**: "{free} free of {total}" with a level bar | not clickable |
| Device page, group Tools | **Backup** (Create), **Device Error Log** (only after an "RTL Trap" in the last 30 days: Error Details, Crash Logs with Copy Crash Logs, Memory Dumps with Copy Memory Dumps), **Part Number** | the copy buttons are reachable only through the error row |
| Preferences, group Diagnostics | **Keep Diagnostic Files** (switch, `diagnostics_copy`, off by default) | the same as `openexpress setup --diagnostics-copy on/off` |
| Main menu | **Report a Device or Problem…** (dialog: report, "Attach the last log", Copy, Open on GitHub) | the report has no line from the device's diagnostics |
| Maps, Music, Apps pages | "Maps use {size}", free space, app slots | each counted on its own page only |
| Command line | `openexpress diagnostics [--apply] [--crash-logs] [--dumps]`: error log entries, memory dumps, crashes in the copied boot log, the copies kept | the window shows little of it |
| `diagnostics.py` | at each connection (window only): reads `err_log.txt`/`.bak` (kind, time, error, PC, screens; never `unit_id` or `backtrace`), lists memory dumps and the places of the diagnostic files; with Keep Diagnostic Files, copies the visible diagnostic files into `devices/<Unit ID>/diagnostics/` (content by hash, snapshots with the time and software version, at most 30 snapshots and 50 MB); on request: memory dumps (newest first, 50 MB a time), hidden crash logs over a short PTP session | the boot log is read only from the newest copy; no history over time |

What is missing (section 6 lists it for each step): a log of the device over time, a history of
crashes merged from all sources, storage by category in the window, freeing space and diagnostic
lines in the report.

## 3. Decisions

### D1. The name

"Device Health" reads as the user's health: Garmin's devices and Garmin Connect use "health" for
heart rate, sleep and stress, the very data the device holds.

| Option | EN | PL | |
|---|---|---|---|
| **A (decided)** | Device Status | Stan urządzenia | says what the page shows (the state of the device, not its data); the summary at the top answers it; next to "Device" in the side panel it reads as its companion |
| B | Diagnostics | Diagnostyka | exact for crashes and reports, but technical and too narrow for storage and history; "Diagnostics" is also the group in Preferences |
| C | Status | Stan | short like the other pages, but alone it can mean the sync's status |
| D | Device Health | Kondycja urządzenia | the confusion above |

### D2. The place in the side panel

| Option | |
|---|---|
| **A (recommended)** | last, after Activities, where Activity Profiles were: it is opened now and then, not at every connection |
| B | second, after Device | puts a rarely used page above Updates and Maps |

### D3. The icon

By the rules of the window's icons: one of the theme if one says it, else the GNOME kit, else our
own, chosen by a blind test.

| Candidate | |
|---|---|
| Adwaita `help-about-symbolic` (an "i" in a circle, drawn as an outline) | GNOME's "About"; may read as "information" (the stand-in in the mockups) |
| our own: the small device outline with a check mark | "the device is all right"; the device icons of the panel exist already for 16 px |
| Adwaita `preferences-system-details-symbolic` (an "i" in a filled box) | too heavy where the status page draws the page's icon large (D14): not proposed |
| our own: the small device outline with a pulse line | read as health (D1): not proposed |

The icon is an outline, as the other pages' icons are, since the status page of D14 shows it large
and dimmed. Recommendation: the blind test between the first two.

### D4. The layout

| Option | |
|---|---|
| **A (decided)** | the summary card on top, then groups one under another: Storage (a card with the bar and its legend, below it Category Details), Crashes and Updates, Space to Free (Free Up Space from step e, D9), Diagnostic Files, Report a Problem. The pattern of every other page (an `Adw.PreferencesPage` of groups and rows); one column at any width; rows read well with the keyboard and a screen reader |
| B | the summary card on top, then cards in a grid of two columns that folds to one on a narrow window (`Gtk.FlowBox`): Storage, Crashes and Updates, Free Up Space, Diagnostic Files, Report a Problem. Looks like a dashboard and shows more on a wide window, but no other page has cards with buttons, cards of uneven heights leave gaps, and a narrow window gets the same one column as A |

Both use only libadwaita's widgets and style classes (`card`, `heading`, `dim-label`, `warning`,
`success`), and one drawing of their own: the storage bar.

### D5. The summary on top

A card with a round icon in the colour of the state, a title and one line.

| State | When | Icon and colour | EN title | PL title |
|---|---|---|---|---|
| Good | no crash in the last 30 days and not little space | `object-select-symbolic`, success | No Problems Found | Nie znaleziono problemów |
| Crashes | a crash in the last 30 days | `dialog-warning-symbolic`, warning | {count} Crash in the Last 30 Days / {count} Crashes in the Last 30 Days | {count} awaria / awarie / awarii w ostatnich 30 dniach |
| Little space (decided) | free space below the smaller of 10 % of the capacity and 500 MB: devices hold from some megabytes to tens of gigabytes, so a share alone would warn a large device too late and a size alone a small one always | `dialog-warning-symbolic`, warning | Little Free Space | Mało wolnego miejsca |
| Error logs not read | none of the above, and the device's error logs could not all be read (also when their folder cannot be listed): whether it crashed is not known | `dialog-question-symbolic`, no colour | Error Logs Not Read | Nie odczytano dzienników błędów |
| Reading | the counts are not known yet | a spinner | Reading the Device… | Odczyt urządzenia… |

The line under it: "Last one {when} · the same error each time · {free} free" / "Ostatnia {when}
· za każdym razem ten sam błąd · {free} wolne" (parts left out when they do not apply). Both
crashes and little space: the crashes in the title, the space in the line. The error colour is
not used: a device's crash is a fact to know, not an alarm. The row Device Status of the device
page says the summary's title, from the same facts (also when the reading of the error logs
failed); for a device without diagnostic data (D14), as its page: "No Diagnostic Data".

### D6. Storage by category

- **Counted** at each connection, in the background after the device page is shown: one listing of
  the device's folders over the ordinary connection (gvfs, or the drive's folder), sizes and counts
  only; nothing read from the files, no name kept. On a large device it takes a moment: the page
  says it reads the device until then.
- **Categories:** Maps, Activities, Music and Audio, Apps, Courses and Locations, Updates and
  Satellite Data, Logs and Memory Dumps, System (the capacity minus the free space minus
  the files listed: the device's hidden files and what its file system keeps), and the free part.
  Which folder belongs where comes from the data types the device declares, as the sync and the
  pages already sort them.
- **The bar:** one `Gtk.DrawingArea`, the categories from the largest to the smallest in GNOME's
  palette (the same in light and dark), the free part last, as the track. No category is red: in libadwaita red means an error, so Logs
  and Memory Dumps are slate and System light grey.
- **The legend:** in the bar's order, the free part last, as a ring; each name with its size at
  the end of its line, one column at any width: a layout of two columns where the names happen
  to fit would change by itself when the sizes change (after a removal). A name wraps only in a
  window too narrow for it, and only its line is the taller for it: every line keeps its natural
  height, with no room left under the last one.
- **A category of 0 B** is left out of the legend and of Category Details (a USB drive holds
  none of most); the free part stays always, at 0 B too.
- **Category Details** (a row that expands): each category with its size, in the bar's
  order; those with a page (Maps, Activities, Music and Audio, Apps, Courses and Locations) lead
  to it.

### D7. Crashes

- Only crashes that restarted the device ("RTL Trap"): the kinds an Edge writes at an ordinary
  start ("Assert") and "Abnormal Powerdown" are counted in the log only.
- **Sources, merged:** `err_log.txt` and `err_log.bak` on the device, the boot log (copied by Copy
  Crash Logs), every copy kept on this computer (the device keeps only its last entries), and
  every crash the device log (D8) wrote at each connection (with the number of its error log
  and the software version then), so that a device whose diagnostic
  files are not kept still has its crashes, also when it is not connected. One crash = the same
  entry seen again (the same time and codes, in each copy of an error log and in the device log),
  or one crash in two sources (the error log and the boot log) within a minute with codes that
  agree: a boot log line joins one crash, the nearest before it, and never two crashes. Two entries of one file are two crashes, however close, even of the same time and codes:
  a device restarting again and again writes each.
- **"The same error as the {n} before":** the crashes just before it, one after another, that had
  its error; a crash of another error between them ends the count.
- **Shown** newest first with a relative date and the software version; "the same error as the {n}
  before" when it repeats. The codes (error number, address, screens) only in Error Details.
- **Show All:** beside the group's description on a wide window; on a narrow one the last row of
  the list, where the description leaves it no room.
- **Example (made up):** "Crash · 2 days ago · software 21.18 · the same error as the 2 before".

### D8. The device log (history)

| Option | |
|---|---|
| **A (decided)** | `devices/<Unit ID>/history.jsonl` (0600), one line at each connection, written by the window and the background service, once a connection (as the diagnostics' reading): time, connection kind (MTP, drive), software version, capacity, free space, category sizes, number and size of memory dumps, every crash the error logs hold and the newest of them (kind, time, error, program counter, screens), how many error logs there are and how many entries they hold, and whether the device has crash logs only Copy Crash Logs reads. No file names, no Unit ID, no serial number. Lines older than two years (counted from the middle of the times of the newest 100 lines, so that a clock set wrong at fewer than half of them drops nothing) are dropped, but never the newest 100; a line that is not the program's (damaged, not UTF-8, a size no device has) is skipped by the readers and kept as it is by the writers. Kept whatever Keep Diagnostic Files says: it holds only counts, like `devices.json`. Forget Device deletes it with the rest of the device's folder |
| B | the same, but only with Keep Diagnostic Files on | less history for most users (the switch is off by default) |

Crashes and Updates show from it: crashes and software updates (a new version between two lines).
The first connection is when this computer first saw the device (`first_seen` in `devices.json`),
not the log's first line, which may come long after; its version only when the log began with that
connection; without `first_seen` (a test drive) no such row. Storage over time is kept, not
shown (a later chart, if wanted).

After a removal (D9, the window and the command line alike), and after anything else the window
wrote to the device, the line of that connection is put right in place: the kinds of file, the
space and the memory dumps counted again, and when ("counted"); a new line would count the
connection twice. So the page of the device not connected
(D13) and `openexpress diagnostics` tell the state after the removal without connecting it again.
A log whose last line is of another connection is left as it is, and so is a line counted again
after the count began (a count begun before a removal never undoes the removal's): a task of the
window waits for a count still going, which is in the session before the task goes on. The page,
its row, the row Device Storage and the report take one free space, the one the window read last
(at the opening, at Refresh, after a write, and each time the page is read), and the kinds of
file of this connection's line, which every count puts right, the command line's too (the
window's own count only for a device the log does not keep); never another connection's line. A capacity not told by a count is the
one known (it does not change); free space and memory dumps not told are not known. A folder
behind a link is not counted. A write begun before Forget Device (the reading at the opening,
the line of the connection, a count, a removal put right), in this process or another, writes
nothing: the device's folder was there when it began and is gone, or this process forgot it.
A connection is told from the next one by the device's place on USB (a new one at each plugging
in; where /sys cannot be read, the one the volume tells, kept for this alone), a drive's by its
mount, else by its software version. The writers of the log, the removals of D9 and Forget Device take one lock of
the records, in any process; a write begun before Forget Device does not make the device's
folder again.

Step a adds one sentence to "What is kept of each device" in `docs/privacy.md`: a short log of
each connection: the software version, the storage by category, the number and size of memory
dumps and the crashes the error logs held (kind, time, error, where the software stopped, screen,
which of the two logs), without file names, kept whatever Keep Diagnostic Files says.

### D9. Freeing space

| Candidate | What is done | Never |
|---|---|---|
| Older memory dumps: all but the newest, by the time of each file; a dump is a file named as the devices name them (`RAM.zip`, `RAM_bak1.zip`, `ramdump*.zip` in the folder of dumps, `ramdump*.log` beside the error logs), never another file there, a hidden one or a link | copied to this computer first, each copy checked, then removed from the device; the newest dump always stays (the only one of the last crash). No dump is offered when the times cannot be trusted: one not known, before 2010 or ahead of this computer by more than a day, or the two newest at the same time (the newest cannot be told) | – |
| Files of an update the device did not install, outside the device's main folder (a row titled by the update's name and version: "The device took the file in but did not install it") | the file is removed; the Updates page offers the update again | a map support file (`Garmin/SID`, `.sid`), satellite data (`Garmin/GPS`), a hidden file, a file behind a link (a drive or `--path` on this computer): never offered |
| An update file in the device's main folder (`GUPDATE.GCD`, `GUPDATE.GSP`) the device did not install | shown as waiting to install, without a button, and not counted as space to free | removed: that cancels the update |
| – | – | activities, maps, music, courses, settings and profiles, `.sid` files, satellite data, the device's logs (also the `ramdump_err*.txt` texts beside an Edge's dumps), hidden files, anything not on this list |

Decided: both candidates, removed from step e on; the page of step c only reads.

- **The copy before a removal** is made always, also with Keep Diagnostic Files off: it is what
  makes a removal of the device's only diagnostic material safe. It goes to
  `devices/<Unit ID>/diagnostics/removed/`, one folder a removal, numbered (the oldest goes first
  whatever this computer's clock said), its folders for their owner alone as the other copies (a
  test drive's, as all its copies and its records, in `devices/<Unit ID>/test-drive/`, with limits
  of their own: they never push out the device's), apart from the 50 MB of the diagnostic copies, so that the dumps never push out
  the copies of the error logs the crash history reads. At most 100 MB a device: the newest dumps
  first while they fit; older removals are dropped first to make room. A dump whose copy would not
  fit in what is left (the `ramdump*.log` an Edge 1050 wrote with its older software, about
  200 MB each, or one of several that together exceed 100 MB) is removed without a copy, and the
  question says so plainly: "as the copies would not fit in 100 MB". A dump whose copy failed or does not match is not removed. The copy of a
  dump that stays on the device (refused, or not reached when the removal stopped) is dropped:
  it is not its only copy; that of a dump something else removed meanwhile stays, the only one.
  A device without a unit ID has no removal of its dumps: its copies would have no folder of the
  device (the command line says so). One dump alone is the newest, whatever its time: nothing
  older to remove. A removal stopped while copying (Ctrl+C, the device stopping) keeps no copy
  of a dump still on the device. `diagnostics --path` (a copy of a device's files) keeps its
  copies apart from the device's, as a test drive: they never push out the device's. The copies of Copy Memory Dumps share neither this limit nor that of
  the error logs' copies (each kind its own 30 copies and 50 MB), and never push those out; a Copy
  Memory Dumps that finds the same dumps makes no new copy. The copies
  count in "Copies on This Computer" and go with Forget Device.
- **Step c (read only):** the group **Space to Free**, "Files the device no longer needs"; each
  candidate with what it is and its size at the end of its row, no buttons.
- **Step e:** the group becomes **Free Up Space**, "Activities, maps, music, courses and settings are
  never removed"; the rows get **Remove** in the destructive style (as Forget Device); for the
  dumps its tooltip and its name for screen readers say **Copy and Remove** when at least one is
  copied first (the longer words would leave the subtitle no room in a narrow window). Each asks
  first through `confirm()`: Cancel, then the answer in the destructive style, **Copy and
  Remove** (dumps, at least one copied first) or **Remove**; Cancel is the default (Enter), and closing the question cancels it. The question names
  what goes, the space, and what is not copied:
  - "Remove Older Memory Dumps?" – "5 memory dumps (44.9 MB) will be copied to this computer and
    then removed from the device. The newest dump stays on the device." – Cancel, Copy and Remove;
  - some not copied: "5 memory dumps (682.8 MB) will be removed from the device: 2 (75.3 MB) after
    a copy on this computer, 3 (607.5 MB) without a copy, as the copies would not fit in 100 MB.
    The newest dump stays on the device." – Cancel, Copy and Remove;
  - none can be copied: "3 memory dumps (607.5 MB) will be removed from the device without a copy,
    as the copies would not fit in 100 MB. The newest dump stays on the device." – Cancel, Remove;
  - "Remove the Update File?" – "The device took in GPS software 6.20 (4.1 MB) but did not install
    it. The file will be removed from the device; the Updates page will offer the update again." –
    Cancel, Remove.
- **The removal** goes exactly as the question named it: under the lock of the records (another
  removal, from the window or the command line, waits), the device is listed again first, and
  only when it holds the same dumps (the same newest, the same older ones with their sizes and
  times) are they removed, with the question's split into copied and not copied; else nothing is
  removed and the window or the command line says the files changed ("Look at the list again"),
  the page read again. An update file is looked at again too (the same size and time) and the
  update server asked anew; what was freed is the size of the file removed. Each removal is
  checked by listing again: a file the device keeps is not counted as removed. What a removal did
  is always told, in the window or on the command line and in the program's log: also when the
  device stops half-way, is unplugged, or cannot be counted after it ("Removal Not Finished",
  with the files the device refused, and those not removed as their copy failed, said apart), on
  the command line also after Ctrl+C; the device log is then put right by what was removed (also
  when the count after it tells nothing, the device unplugged meanwhile). A file
  gone by the time it is removed (something else removed it) is not counted as freed; when none
  was left to remove, the window and the command line say "Nothing removed: the files were no
  longer on the device". A file whose folder cannot be listed then stays, told as not removed
  with why (exit code 1 on the command line). An answer
  given after the device was unplugged or opened again (a restart, plugged in again) removes
  nothing: "Nothing Removed", and the page is read again.
- **One list:** the page and the command line list what can be freed with the same function: the
  dumps listed again, and the update files from the update server's answer (the one the device
  page and the Updates page share), never from what the Updates page happens to hold.
- **After it:** "5 memory dumps removed, 44.9 MB freed" (or "Update file removed, 4.1 MB freed");
  the kinds of file and the free space are counted again, and Category Details says so ("Counted
  again after a change, {when}": also after another file was written), then the page is read
  again. When the device refused a file, or kept it, the
  window says so ("The device did not remove 2 of the files; the others are removed.", with the
  device's answer); what was removed stays removed, the page shows what is left. A device that
  stops responding gets the usual banner.
- **Command line:** `openexpress diagnostics --free-space` is an action of its own (not with
  `--dumps` or `--crash-logs`): it shows only the space to free, the same list as the page, in
  the future tense ("to copy before the removal", "to remove without a copy, as the copies would
  not fit in 100 MB") and one
  sentence of the dry run; with `--apply` it copies and removes them as the window does, and
  nothing else (not the copy of the diagnostic files). The update files need the update
  server's answer: without it only the dumps, and it says so.

### D10. Diagnostic files

- **Keep Diagnostic Files** also on this page, the same setting as in Preferences (as Background
  Sync is on the device page and in Preferences).
- **Copies on This Computer:** "{count} copies · {size} · the last {when}", with Open Folder.
- **Crash Logs** (Copy Crash Logs, only where the device hides them) and **Memory Dumps** (Copy
  Memory Dumps): always here, not only after a crash.

### D11. Report a Problem

- **Report to openExpress:** the existing dialog (also in the main menu). The report gains a section
  "Device diagnostics": the last crashes (kind, time, software version, error, program counter,
  screens), the crashes of the last 30 days in the boot log, the number and size of memory dumps,
  whether copies are kept. Masked as the report and the log are today; `unit_id` and `backtrace`
  are never read.
- No archive of files for Garmin's support is made (the step that would have built one, d, was
  dropped).

### D12. What moves, what stays

| Element | Decision | Why |
|---|---|---|
| Device page, Tools: **Device Error Log** (with Error Details, Copy Crash Logs, Copy Memory Dumps) | moves to the page (Crashes and Updates, Diagnostic Files); in its place a row **Device Status** with the summary as subtitle and an arrow, as Device Software leads to Updates | the crash is one item of the history, and the copies belong with the other diagnostic files |
| Device page, Storage: **Device Storage** | stays, gets the arrow to the page | free space at a glance is what most users look for |
| Preferences: **Keep Diagnostic Files** | stays, and shows on the page too (D10) | a setting of all devices; the page is where its effect is seen |
| Main menu: **Report a Device or Problem…** | stays, and is on the page too (D11) | the only way to report without a device (not supported, not detected) |
| Device page, Tools: **Backup**, **Part Number** | stay | the user's data and the device's identity, not its diagnostics |
| Maps, Music, Apps: their sizes and free space | stay | they serve their page's own choices |
| Command line `openexpress diagnostics` | stays, lists the merged crashes and the device log, and frees space with `--free-space` (D9) | the same data and the same actions in both |

Nothing disappears but the row Device Error Log, whose content moves.

### D13. Kinds of device and no device

| Case | The page |
|---|---|
| Device over MTP | everything |
| Device as a USB drive | storage (the drive's size and free space, the same categories), crashes from its error log where it keeps one, memory dumps, freeing space; no hidden crash logs (no PTP on a drive) |
| Device without diagnostic files (e.g. a bike accessory) | D14 |
| Remembered device, not connected | **A (decided):** its view gets a row Device Status in Tools, its subtitle the summary and "as of {when}" (the last connection); it leads to the page read from the records of this computer only: the device log (storage of the last connection, software updates, its crashes), the copies of the diagnostic files and the crashes in them. On top a banner: "Device not connected – as of {when}; connect it to free up space or copy its logs". What needs the device is left out, not greyed: Free Up Space, Copy Crash Logs and Copy Memory Dumps, and the report (the main menu has it). The memory dumps as the last connection counted them, without a button. What the records do not tell is "Not known", never none. A device with no records (only on the account, or never logged) gets no row. Connected meanwhile, its view gives way to the device view, as the remembered view does |
| Device that openExpress cannot use (HID) | not offered: there is no device view; the start page's notice and the main menu's report stay |
| No device, or the device unplugged while the page is open | the side panel is not there (start page); unplugged: the usual "Connect Your Device" |

### D14. A device without diagnostic data

`Adw.StatusPage` with the page's icon (an outline, dimmed by the status page as on the other
pages), "No Diagnostic Data" / "Brak danych diagnostycznych" and one
sentence: "This device keeps no error logs or memory dumps that openExpress can read" / "To
urządzenie nie zapisuje dzienników błędów ani zrzutów pamięci, które openExpress potrafi
odczytać"; under it the storage card. A device that gives neither its storage nor diagnostic files:
the status page alone. Error logs not read and memory dumps not listed are not known, never
none: such a device has the page, not this one.

Only a device that keeps no error logs and no memory dumps (a bike accessory, a drive) gets this
page. A device whose error logs are there and were read but hold no entries, with no memory
dumps, gets the summary like any device without crashes: "No Problems Found" with its free space,
and the row Device Status says the same. The sentence above would be untrue of it: it keeps
error logs, and the storage card shows their space under "Logs and Memory Dumps".

### D15. Steps

Each step is checked on its own (tests first, then a live trial) and committed on its own:

a. **The device log** (D8), no window: written at each connection by the window and the background
   service; the category count (D6); the sentence in `docs/privacy.md`. Trial: a test drive with XDG
   folders of its own (as CONTRIBUTING describes; a drive: storage, categories, the log's lines),
   then a watch and a bike computer over MTP.
b. **Crash history and the report** (D7, D11): the merge, the command line, the report's
   new section. Trial: the test drive with a made-up error log (written there only with the
   maintainer's consent), then a watch with its boot log.
c. **The page, read only** (D4–D7, D10, D12–D14): summary, storage, crashes and updates, Space to
   Free without buttons (D9), diagnostic files, report; the row on the device page; the Device
   Error Log row removed. Trial: the test drive (a drive without diagnostic data: D14), the watch,
   the bike computer, narrow and wide windows, both themes, both languages.
d. Dropped: no archive for Garmin's support (D11). The steps after it keep their letters.
e. **Freeing space** (D9): the buttons and their questions. Trial: the test drive with made-up dump
   and update files, then a device with old dumps of its own, after the maintainer's consent.
f. **The remembered device** (D13): the row on the remembered view, the page from the records,
   the device log's crashes in the history of crashes (D7). Trial: the watch and the bike
   computer unplugged, their pages beside those of the last connection (the same crashes and
   storage); plugged in while the page is shown.

## 4. Texts

| Where | EN | PL |
|---|---|---|
| Side panel, page title | Device Status | Stan urządzenia |
| Summary (D5) | see D5 | see D5 |
| Group | Storage | Pamięć |
| Storage card | {free} free of {total} · {used} used | {free} wolne z {total} · Zajęte: {used} |
| Categories | Maps, Activities, Music and Audio, Apps, Courses and Locations, Updates and Satellite Data, Logs and Memory Dumps, System, Free | Mapy, Aktywności, Muzyka i audio, Aplikacje, Kursy i lokalizacje, Aktualizacje i dane satelitarne, Dzienniki i zrzuty pamięci, System, Wolne |
| Row | Category Details – "Counted when the device was connected, {when}"; after a change "Counted again after a change, {when}" | Szczegóły kategorii – „Policzone przy podłączeniu urządzenia, {when}”; po zmianie „Policzone ponownie po zmianie, {when}” |
| Group | Crashes and Updates – "Crashes that restarted the device, and its software updates" | Awarie i aktualizacje – „Awarie, po których urządzenie uruchomiło się ponownie, i aktualizacje jego oprogramowania” |
| Rows | Crash; Software Updated; First Connected Here | Awaria; Zaktualizowano oprogramowanie; Pierwsze podłączenie |
| Button | Show All | Pokaż wszystko |
| Group, step c | Space to Free – "Files the device no longer needs" | Miejsce do zwolnienia – „Pliki, których urządzenie już nie potrzebuje” |
| Group, step e | Free Up Space – "Activities, maps, music, courses and settings are never removed" | Zwolnij miejsce – „Aktywności, mapy, muzyka, kursy i ustawienia nigdy nie są usuwane” |
| Rows | Older Memory Dumps – "{count} files; the newest dump is not counted · {size}"; {name} {version} – "The device took the file in but did not install it · {size}" | Starsze zrzuty pamięci – „{count} pliki; najnowszy zrzut nie jest liczony · {size}”; {name} {version} – „Urządzenie przyjęło plik, ale go nie zainstalowało · {size}” |
| Buttons, step e | Remove on the rows (tooltip: Copy and Remove when copied first); in the questions Copy and Remove or Remove | Usuń na wierszach (podpowiedź: Skopiuj i usuń); w pytaniach Skopiuj i usuń albo Usuń |
| Questions, step e | Remove Older Memory Dumps?; Remove the Update File? (bodies in D9); Cancel | Usunąć starsze zrzuty pamięci?; Usunąć plik aktualizacji?; Anuluj |
| Row, step e | {name} {version} – "Waits to be installed; removing the file would cancel the update" (no button, no size) | {name} {version} – „Czeka na instalację; usunięcie pliku anulowałoby aktualizację” (bez przycisku i rozmiaru) |
| After a removal, step e | "{count} memory dumps removed, {size} freed"; "Update file removed, {size} freed"; "The device did not remove {count} of the files; the others are removed." | „Usunięto {count} zrzutów pamięci, zwolniono {size}”; „Usunięto plik aktualizacji, zwolniono {size}”; „Urządzenie nie usunęło {count} z plików; pozostałe są usunięte.” |
| Group | Diagnostic Files | Pliki diagnostyczne |
| Rows | Keep Diagnostic Files; Copies on This Computer; Crash Logs; Memory Dumps | Przechowywanie plików diagnostycznych; Kopie na tym komputerze; Dzienniki awarii; Zrzuty pamięci |
| Buttons | Open (Copies on This Computer; "Open Folder" in the tooltip and for screen readers); Copy (the rows Crash Logs and Memory Dumps; their full names "Copy Crash Logs", "Copy Memory Dumps" in the tooltip and for screen readers): a narrow window keeps room for the subtitles | Otwórz („Otwórz folder” w podpowiedzi i dla czytników ekranu); Skopiuj („Skopiuj dzienniki awarii”, „Skopiuj zrzuty pamięci”) |
| Group | Report a Problem | Zgłoszenie problemu |
| Row | Report to openExpress | Zgłoszenie do openExpressu |
| Button | Report… | Zgłoś… |
| Device page row | Device Status | Stan urządzenia |
| Remembered view row, step f | Device Status – "{summary} · as of {when}" | Stan urządzenia – „{summary} · stan z {when}” |
| Banner, step f | Device not connected – as of {when}; connect it to free up space or copy its logs | Urządzenie niepodłączone – stan z {when}; podłącz je, aby zwolnić miejsce lub skopiować dzienniki |
| Rows, step f | Category Details – "Counted at the last connection, {when}"; Memory Dumps – "{count} dumps · {size} · at the last connection" | Szczegóły kategorii – „Policzone przy ostatnim podłączeniu, {when}”; Zrzuty pamięci – „{count} zrzutów · {size} · przy ostatnim podłączeniu” |
| Empty page (D14) | No Diagnostic Data | Brak danych diagnostycznych |

English in header case for titles and buttons and in sentences for subtitles and descriptions, as
the texts of the interface say (no full stop under one sentence, "…" only where more input
follows); Polish in sentence case, a hard space after one-letter words and before an en dash.

## 5. Privacy

- Nothing is sent: the report goes where the user pastes it.
- The device log and the category counts hold no file names, no Unit ID and no serial number.
- The report is masked as today.
- The error log's `unit_id` and `backtrace` are never read into the program.

## 6. Missing today, by step

| Step | Missing |
|---|---|
| a | a log over time (today only the last values in `devices.json`, and snapshots of the diagnostic copies when something changed); the category count in the window; writing at a connection outside the window |
| b | merging crashes from the error logs, the boot log and the copies (today: the current `err_log` without removing duplicates, the newest boot log copy, command line only); diagnostic lines in the report |
| c | the page itself; the copy buttons outside the error row |
| e | removing memory dumps; listing update leftovers (today `GUPDATE.TMP` is only removed before an update is written) |

## 7. Open

- The proposed decisions: D2, D3 (the blind test), D5 to D7 but the threshold, D10 to D12, D14.
- A narrow window shortens the Polish title "Stan urządzenia" in the header, as it does "Aktualizacje"
  today: one solution for both.
