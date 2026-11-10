# Design: the page Device Status

Status: partly decided, nothing implemented. Decided: D1 (the name), D4 (layout A), D5's threshold
of little space, D8 (the log always kept), D9 (removal at step e, both candidates, always
confirmed); D13's remembered device is decided at step f. The other decisions are proposed, each
with the option recommended. Mockups of layout A (step c, read only, and step e, with removal and
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
- **a report:** the openExpress report with the device's diagnostics, and a local archive for
  Garmin's support.

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
crashes merged from all sources, storage by category in the window, freeing space, the archive for
Garmin's support and diagnostic lines in the report.

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
| Reading | the counts are not known yet | a spinner | Reading the Device… | Odczyt urządzenia… |

The line under it: "Last one {when} · the same error each time · {free} free" / "Ostatnia {when}
· za każdym razem ten sam błąd · {free} wolne" (parts left out when they do not apply). Both
crashes and little space: the crashes in the title, the space in the line. The error colour is
not used: a device's crash is a fact to know, not an alarm.

### D6. Storage by category

- **Counted** at each connection, in the background after the device page is shown: one listing of
  the device's folders over the ordinary connection (gvfs, or the drive's folder), sizes and counts
  only; nothing read from the files, no name kept. On a large device it takes a moment, so the
  card shows "Counting…" until then.
- **Categories:** Maps, Activities, Music and Audio, Apps, Courses and Locations, Updates and
  Satellite Data, Logs and Memory Dumps, System (the capacity minus the free space minus
  the files listed: the device's hidden files and what its file system keeps), and the free part.
  Which folder belongs where comes from the data types the device declares, as the sync and the
  pages already sort them.
- **The bar:** one `Gtk.DrawingArea`, the categories from the largest to the smallest in GNOME's
  palette (the same in light and dark), the free part last, as the track. No category is red: in libadwaita red means an error, so Logs
  and Memory Dumps are slate and System light grey.
- **The legend:** in the bar's order, the free part last, as a ring; each name with its size at
  the end of its line. Two columns only where both names fit on one line (the flow box lays them
  out at their natural width): at the page's width the longest names (Updates and Satellite Data,
  Logs and Memory Dumps) do not, so it is one column. A name wraps only in a window too narrow
  for it, and only its line is the taller for it: every line keeps its natural height, with no
  room left under the last one.
- **A category of 0 B** is left out of the legend and of Category Details (a USB drive holds
  none of most); the free part stays always, at 0 B too.
- **Category Details** (a row that expands): each category with its count and size, in the bar's
  order; those with a page (Maps, Activities, Music and Audio, Apps, Courses and Locations) lead
  to it.

### D7. Crashes

- Only crashes that restarted the device ("RTL Trap"): the kinds an Edge writes at an ordinary
  start ("Assert") and "Abnormal Powerdown" are counted in the log only.
- **Sources, merged:** `err_log.txt` and `err_log.bak` on the device, the boot log (copied by Copy
  Crash Logs), and every copy kept on this computer (the device keeps only its last entries). One
  crash = the same error and program counter within a minute, whatever the sources.
- **Shown** newest first with a relative date and the software version; "the same error as the {n}
  before" when it repeats. The codes (error number, address, screens) only in Error Details.
- **Show All:** beside the group's description on a wide window; on a narrow one the last row of
  the list, where the description leaves it no room.
- **Example (made up):** "Crash · 2 days ago · software 21.18 · the same error as the 2 before".

### D8. The device log (history)

| Option | |
|---|---|
| **A (decided)** | `devices/<Unit ID>/history.jsonl` (0600), one line at each connection, written by the window and the background service, once a connection (as the diagnostics' reading): time, connection kind (MTP, drive), software version, capacity, free space, category sizes, number and size of memory dumps, the newest crash (kind, time, error, program counter, screens). No file names, no Unit ID, no serial number. Lines older than two years are dropped. Kept whatever Keep Diagnostic Files says: it holds only counts, like `devices.json`. Forget Device deletes it with the rest of the device's folder |
| B | the same, but only with Keep Diagnostic Files on | less history for most users (the switch is off by default) |

Crashes and Updates show from it: crashes and software updates (a new version between two lines).
The first connection is when this computer first saw the device (`first_seen` in `devices.json`),
not the log's first line, which may come long after; its version only when the log began with that
connection; without `first_seen` (a test drive) no such row. Storage over time is kept, not shown (a later chart, if wanted).

Step a adds one sentence to "What is kept of each device" in `docs/privacy.md`: "a short log of
each connection: the software version, the storage by category, the number and size of memory dumps
and the last crash (kind, time, error, where the software stopped, screen), without file names,
kept whatever Keep Diagnostic Files says;".

### D9. Freeing space

| Candidate | What is done | Never |
|---|---|---|
| Older memory dumps | copied to this computer (with the diagnostic copies), checked, then removed from the device; the newest dump always stays | – |
| Files of an update the device did not install ("Update Not Installed by the Device") | the file is removed; the Updates page offers the update again | a waiting update (`GUPDATE` in the device's main folder), whose removal cancels it |
| – | – | activities, maps, music, courses, settings and profiles, `.sid` files, satellite data, the device's logs, hidden files, anything not on this list |

Decided: both candidates, removed from step e on; the page of step c only reads.

- **Step c (read only):** the group **Space to Free**, "Files the device no longer needs"; each
  candidate with what it is and its size at the end of its row, no buttons.
- **Step e:** the group becomes **Free Up Space**, "Activities, maps, music, courses and settings are
  never removed"; the rows get **Copy and Remove** and **Remove** in the destructive style (as Forget
  Device), and each asks first through `confirm()`: Cancel, then the answer in the destructive
  style; Cancel is the default (Enter), and closing the question cancels it. The question names
  what goes and the space:
  - "Remove Older Memory Dumps?" – "2 memory dumps (18.3 MB) will be copied to this computer and
    then removed from the device. The newest dump stays on the device." – Cancel, Copy and Remove;
  - "Remove the Update File?" – "The device took in GPS software 6.20 (4.1 MB) but did not install
    it. The file will be removed from the device; the Updates page will offer the update again." –
    Cancel, Remove.

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
- **Files for Garmin Support:** a ZIP saved to a folder picked in the save dialog, never sent. Two scopes:
  "Diagnostic files" (default: the error logs, the reports waiting on the device, the boot and event
  logs if copied, the memory dumps if chosen, the Connect IQ error logs, and a `README.txt` with the
  list and the openExpress report) and "The device's Garmin folder without music, pictures and
  maps" (what the support may ask for; it holds activities and health data, said so). Taken from the
  copies on this computer where they are newer than the device's. The dialog says plainly: "It
  holds the device's Unit ID and its logs. Memory dumps are encrypted by Garmin, so nothing in them
  can be masked. Send it only to Garmin's support."

### D12. What moves, what stays

| Element | Decision | Why |
|---|---|---|
| Device page, Tools: **Device Error Log** (with Error Details, Copy Crash Logs, Copy Memory Dumps) | moves to the page (Crashes and Updates, Diagnostic Files); in its place a row **Device Status** with the summary as subtitle and an arrow, as Device Software leads to Updates | the crash is one item of the history, and the copies belong with the other diagnostic files |
| Device page, Storage: **Device Storage** | stays, gets the arrow to the page | free space at a glance is what most users look for |
| Preferences: **Keep Diagnostic Files** | stays, and shows on the page too (D10) | a setting of all devices; the page is where its effect is seen |
| Main menu: **Report a Device or Problem…** | stays, and is on the page too (D11) | the only way to report without a device (not supported, not detected) |
| Device page, Tools: **Backup**, **Part Number** | stay | the user's data and the device's identity, not its diagnostics |
| Maps, Music, Apps: their sizes and free space | stay | they serve their page's own choices |
| Command line `openexpress diagnostics` | stays, and lists the merged crashes and the device log | the same data in both |

Nothing disappears but the row Device Error Log, whose content moves.

### D13. Kinds of device and no device

| Case | The page |
|---|---|
| Device over MTP | everything |
| Device as a USB drive | storage (the drive's size and free space, the same categories), crashes from its error log where it keeps one, memory dumps, freeing space; no hidden crash logs (no PTP on a drive) |
| Device without diagnostic files (e.g. a bike accessory) | D14 |
| Remembered device, not connected | decided at step f, between **A:** its page gets a row Device Status leading to the page read from the records (history, crashes, storage "as of {when}"), every action off with "Connect the device to …"; and **B:** nothing, the page only for a connected device |
| Device that openExpress cannot use (HID) | not offered: there is no device view; the start page's notice and the main menu's report stay |
| No device, or the device unplugged while the page is open | the side panel is not there (start page); unplugged: the usual "Connect Your Device" |

### D14. A device without diagnostic data

`Adw.StatusPage` with the page's icon (an outline, dimmed by the status page as on the other
pages), "No Diagnostic Data" / "Brak danych diagnostycznych" and one
sentence: "This device keeps no error logs or memory dumps that openExpress can read" / "To
urządzenie nie zapisuje dzienników błędów ani zrzutów pamięci, które openExpress potrafi
odczytać"; under it the storage card. A device that gives neither its storage nor diagnostic files:
the status page alone.

### D15. Steps

Each step is checked on its own (tests first, then a live trial) and committed on its own:

a. **The device log** (D8), no window: written at each connection by the window and the background
   service; the category count (D6); the sentence in `docs/privacy.md`. Trial: a test drive with XDG
   folders of its own (as CONTRIBUTING describes; a drive: storage, categories, the log's lines),
   then a watch and a bike computer over MTP.
b. **Crash history and the report** (D7, D11 first part): the merge, the command line, the report's
   new section. Trial: the test drive with a made-up error log (written there only with the
   maintainer's consent), then a watch with its boot log.
c. **The page, read only** (D4–D7, D10, D12–D14): summary, storage, crashes and updates, Space to
   Free without buttons (D9), diagnostic files, report; the row on the device page; the Device
   Error Log row removed. Trial: the test drive (a drive without diagnostic data: D14), the watch,
   the bike computer, narrow and wide windows, both themes, both languages.
d. **Files for Garmin Support** (D11 second part). Trial: an archive of the test drive and of the
   watch, opened and listed.
e. **Freeing space** (D9): the buttons and their questions. Trial: the test drive with made-up dump
   and update files, then a device with old dumps of its own, after the maintainer's consent.
f. **The remembered device** (D13), decided then.

## 4. Texts

| Where | EN | PL |
|---|---|---|
| Side panel, page title | Device Status | Stan urządzenia |
| Summary (D5) | see D5 | see D5 |
| Group | Storage | Pamięć |
| Storage card | {free} free of {total} · {used} used | {free} wolne z {total} · Zajęte: {used} |
| Categories | Maps, Activities, Music and Audio, Apps, Courses and Locations, Updates and Satellite Data, Logs and Memory Dumps, System, Free | Mapy, Aktywności, Muzyka i audio, Aplikacje, Kursy i lokalizacje, Aktualizacje i dane satelitarne, Dzienniki i zrzuty pamięci, System, Wolne |
| Row | Category Details – "Counted when the device was connected, {when}" | Szczegóły kategorii – „Policzone przy podłączeniu urządzenia, {when}” |
| Group | Crashes and Updates – "Crashes that restarted the device, and its software updates" | Awarie i aktualizacje – „Awarie, po których urządzenie uruchomiło się ponownie, i aktualizacje jego oprogramowania” |
| Rows | Crash; Software Updated; First Connected Here | Awaria; Zaktualizowano oprogramowanie; Pierwsze podłączenie |
| Button | Show All | Pokaż wszystko |
| Group, step c | Space to Free – "Files the device no longer needs" | Miejsce do zwolnienia – „Pliki, których urządzenie już nie potrzebuje” |
| Group, step e | Free Up Space – "Activities, maps, music, courses and settings are never removed" | Zwolnij miejsce – „Aktywności, mapy, muzyka, kursy i ustawienia nigdy nie są usuwane” |
| Rows | Older Memory Dumps – "{count} files; the newest dump is not counted"; Update Not Installed by the Device – "{name} {version}; the device took the file in but did not install it" | Starsze zrzuty pamięci – „{count} pliki; najnowszy zrzut nie jest liczony”; Urządzenie nie zainstalowało aktualizacji – „{name} {version}; urządzenie przyjęło plik, ale go nie zainstalowało” |
| Buttons, step e | Copy and Remove; Remove | Skopiuj i usuń; Usuń |
| Questions, step e | Remove Older Memory Dumps?; Remove the Update File? (bodies in D9); Cancel | Usunąć starsze zrzuty pamięci?; Usunąć plik aktualizacji?; Anuluj |
| Group | Diagnostic Files | Pliki diagnostyczne |
| Rows | Keep Diagnostic Files; Copies on This Computer; Crash Logs; Memory Dumps | Przechowywanie plików diagnostycznych; Kopie na tym komputerze; Dzienniki awarii; Zrzuty pamięci |
| Buttons | Open Folder; Copy Crash Logs; Copy Memory Dumps | Otwórz folder; Skopiuj dzienniki awarii; Skopiuj zrzuty pamięci |
| Group | Report a Problem | Zgłoszenie problemu |
| Rows | Report to openExpress; Files for Garmin Support | Zgłoszenie do openExpressu; Pliki dla wsparcia Garmina |
| Buttons | Report…; Save… | Zgłoś…; Zapisz… |
| Device page row | Device Status | Stan urządzenia |
| Empty page (D14) | No Diagnostic Data | Brak danych diagnostycznych |

English in header case for titles and buttons and in sentences for subtitles and descriptions, as
the texts of the interface say (no full stop under one sentence, "…" only where more input
follows); Polish in sentence case, a hard space after one-letter words and before an en dash.

## 5. Privacy

- Nothing is sent: the report goes where the user pastes it, the archive where the user saves it.
- The device log and the category counts hold no file names, no Unit ID and no serial number.
- The report is masked as today; the archive for Garmin cannot be, and says so before it is saved.
- The error log's `unit_id` and `backtrace` are never read into the program.

## 6. Missing today, by step

| Step | Missing |
|---|---|
| a | a log over time (today only the last values in `devices.json`, and snapshots of the diagnostic copies when something changed); the category count in the window; writing at a connection outside the window |
| b | merging crashes from the error logs, the boot log and the copies (today: the current `err_log` without removing duplicates, the newest boot log copy, command line only); diagnostic lines in the report |
| c | the page itself; the copy buttons outside the error row |
| d | building the archive |
| e | removing memory dumps; listing update leftovers (today `GUPDATE.TMP` is only removed before an update is written) |

## 7. Open

- The proposed decisions: D2, D3 (the blind test), D5 to D7 but the threshold, D10 to D12, D14.
- D13's remembered device, at step f.
- Step e: the memory dumps an Edge 1050 wrote beside its error logs (`ramdump*.log`) are about
  200 MB each, over the 50 MB limit of a copy, and removing a dump needs its copy on this computer
  first: a larger limit for Copy and Remove, or another way, decided at step e.
- A narrow window shortens the Polish title "Stan urządzenia" in the header, as it does "Aktualizacje"
  today: one solution for both.
