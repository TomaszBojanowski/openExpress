# Design: courses from GPX files, without the account

Status: stage 1 implemented (the section Courses of the window and `openexpress courses`),
marked experimental (D12). Stage 2, a FIT course of openExpress's own with the course's type, is
a design of its own, not made yet (section 8). Points of interest (`.gpi`) are not offered
(section 9).

## 1. Scope

The user chooses a GPX file on the computer and openExpress copies it to the connected device,
with no Garmin account and no network. A GPX file holds tracks (`<trk>`), routes (`<rte>`) or
waypoints (`<wpt>`), as route planners, other devices and phones export them; the device makes
courses of its tracks and routes, and saved locations of its waypoints (section 2.3).

openExpress also shows, read only, what the device holds of them (its courses with where each
came from, the GPX files waiting for it, the number of its saved locations), and removes a course
or a waiting file it is asked to. The confirmation dialog before a copy lets the course be
named and shows it on a map.

Not in this stage: a FIT course made by openExpress, with the course's type (stage 2, section
8). Not in scope (section 9): points of interest, a page of a course's details, renaming a
course on the device, sending a course to the Garmin account or from it
(`docs/design/wyslij-na-urzadzenie.md`), FIT and TCX course files from the computer.

## 2. What the devices declare and do

### 2.1 Declared in `GarminDevice.xml`

What a fēnix 8 and an Edge 1050 declare for these files (their own `GarminDevice.xml`; Garmin
Express reads the same file to know where each type goes):

| Type | Direction | fēnix 8 | Edge 1050 |
|---|---|---|---|
| `GPSData` (GPX) | in | `Garmin/NewFiles`, `.gpx` | `Garmin/NewFiles`, `.gpx` |
| `FIT_TYPE_6` (courses) | in / out | `Garmin/NewFiles` / `Garmin/Courses`, `.fit` | the same |
| `FIT_TYPE_8` (locations) | in / out | `Garmin/NewFiles` / `Garmin/Location`, `.fit` | `Garmin/NewFiles` / `Garmin/Locations`, `.fit` |
| `FitnessCourses` (TCX) | in | – | `Garmin/NewFiles`, `.tcx` |

- Neither declares a folder `Garmin/GPX`; other devices may (older Edge models, handhelds), and
  they are not offered the feature (D9). openExpress takes the folder from the declaration of
  each device, never a fixed one.
- A GPX file is declared only inward: the device takes it and keeps what it makes of it (FIT
  courses in `Garmin/Courses`, saved locations in its one locations file), not the file.
- Both declare `CustomPOI` (`Garmin/POI`, `.gpi`); the Edge declares Garmin's own safety cameras
  (`Safety_Cameras_PROM_Provider_1`) in that folder too. openExpress never touches it (section 9).

### 2.2 What Garmin documents, and what users report

The manuals of the fēnix 8 and the Edge 1050 name no folder and no file type for files copied
over USB. The Edge 1050's says only that "most file types have a named folder within the Garmin
folder"; both send courses from Garmin Connect or make them on the device.

Garmin documents it for other devices: on an eTrex SE, GPX files copied to `NewFiles`, "GPX
routes and tracks are converted to FIT courses", waypoints to FIT waypoints; on an eTrex Touch,
"most file types are stored in the GARMIN\NewFiles folder".

Users report (Garmin's forums, not Garmin): an Edge 1050 makes a FIT course of a GPX in
`NewFiles` and reads none in `Courses` or a `GPX` folder; a fēnix 7 makes saved locations of the
waypoints; a course has at most 200 course points; the course takes its name from the GPX
`<name>`, cut on the device (about 15 characters on an Edge 1030); an Edge holds 100 courses and
200 locations (shops' specifications); an Edge 840 did not take a course a browser saved straight
to `NewFiles` over MTP.

### 2.3 What was seen on the devices

A GPX file written to `Garmin/NewFiles` over MTP through gvfs (as openExpress writes), each file
followed by unplugging the device:

| | Edge 1050 | fēnix 8 |
|---|---|---|
| a track: a FIT course in `Garmin/Courses` at the unplug, named after the track's `<name>`, the GPX gone from `NewFiles` | checked | checked |
| a course's FIT file removed over USB: gone from the device's list, the other courses kept | checked | checked |
| a track with times: the same course as without | checked | not tried |
| a route of five points: a course of five points joined by straight lines, no roads | checked | not tried |
| waypoints: saved locations in the device's one locations file | checked | not tried |
| two tracks: two courses | checked | not tried |
| a TCX course: a course as from a GPX | checked | not declared |
| a track with `<type>` (each of seven keys of Garmin Connect): ignored, the course's sport generic (0), the file the same as without it but for its name and time | checked | not tried |
| copied by openExpress, as `.GPX` (the case the device declares): the same as above, the course named as the copy names it (letters with accents kept); the file waits in `NewFiles` while the device is connected; the course removed by openExpress | checked (a track, two tracks, a route, waypoints) | checked (a track) |
| the count of saved locations of `openexpress courses` the same as the device's | checked (0, 3, 0) | checked (0) |

A course the device makes of a file has in its FIT `file_id` the manufacturer Garmin, the product
20119 (the FIT profile's "training center"), the device's serial number and the time it made it;
its lap has the distance and the ascent (none for a route). Files Garmin Connect makes carry the
product 65534, a course it sent to a device too (one on a fēnix 8, shown with the name, distance
and ascent Garmin Connect shows; its `course` has the sport, and its course points include
Garmin Connect's turn directions).

Not tried on either: a very long track, the limits of courses and saved locations, a track with
no `<name>`. What the confirmation dialog says of a route and of waypoints is said of Garmin's
devices in general, not promised for a model (4.2).

## 3. What openExpress has

- The device's `GarminDevice.xml` read; for each type the folders it declares in and out
  (`GarminDevice.targets`, `sources`).
- Writes to the device that are checked: the free space before (`require_space`), the size on
  the device after (`write_file_checked`), a partial file removed; removals that never fail a
  task (`delete_quietly`); one write at a time in the window (`writes_to_device`, `WRITING`).
- **Own maps**, the nearest model: the file checked without the device, a plan made with it (the
  name it gets, the folder, the space), a confirmation dialog with the size and the free space
  after, the copy, its size checked; a name already on the device is never overwritten; what
  this installation copied is recorded per device, and removing a map it did not copy is asked
  about more strongly.
- The FIT reader knows `file_id`, `course`, `lap` and `record`: the name, origin, distance,
  ascent and date of each course on the device are read from its file, with no new format.
- The map of the activity page (its track drawn on a map; the streets of OpenStreetMap only once
  the user turns them on).
- `--path DIR`: a folder taken as a device (USB drives, tests).
- The second install of the same computer ([the Flatpak and the version from
  source](../install.md#the-flatpak-and-the-version-from-source)): the sync takes the other's
  lock too (`service.sync_lock`), and the version from source reads the Flatpak's records of a
  device, never writes them.
- In the Flatpak the window gets any file through the file chooser (the portal); the command line
  reads only the Downloads folder and `Documents/Garmin`.

## 4. The window and the command line

### 4.1 The page

A section of the device view, **Courses** (D1), after Maps; its title carries the chip
**Experimental** (D12). For a device that does not declare `GPSData` inward to `Garmin/NewFiles`
(D8, D9) the page says only "This device does not make courses of GPX files". For the others it
is read from the device when it is first shown and after each change:

- **A banner** while files wait in `NewFiles`: "{count} file waiting – unplug the device so it
  can import it" (plural: "… import them"). It is libadwaita's own banner, grey, as the window's
  other banners, not a blue one of its own.
- **On the Device**: the header's button **Add Course…**; one row per FIT file of
  `Garmin/Courses`:
  - its title: the course's name from the file (else the file's name), and a chip of where it
    came from (D13): "openExpress" (accent) or "Garmin Connect"; none when not known;
  - its subtitle: "{distance} · ↑ {ascent} · {date}": the lap's distance and ascent, in the
    words of a row of the Activities page (kilometres with one decimal, whole metres after ↑,
    hard spaces), so that the two pages read alike (↑ as there, not ↗). The date is the day the
    course's file was made: by the device for a GPX file it turned into a course, by Garmin
    Connect for one it sent (when it sent it to the device, not the day the course was created
    in Garmin Connect). Each is left out when the file has none;
  - a trash button with the tooltip "Remove {name}", which a screen reader reads too (D7). No
    chevron: a page of a course's details is for later.
- **Waiting for the Device**, shown only when a file waits: one row per `.gpx` in `NewFiles`:
  its course name, "{kind}, {distance} · {file name}" (the file's name when it differs from the
  course's); the same trash button.
- **Saved Locations**: one row "On the Device", the description "Waypoints from GPX files and
  locations saved on the device", the count on the right ("Could not be counted" for a locations
  file that does not read whole); no list and no removal: one file holds them all, and the
  device's own menu removes one.

A list that cannot be read is a row that says "Could not be read from the device".

**Empty**: with no course and no waiting file, the page shows the section's icon (a trail),
"No Courses on the Device", "Copy a GPX file with a track or a route – the device turns it into a
course.", and the button **Add Course…** (suggested). A dash of a text of the page has a hard space
before it: no line starts with it.

### 4.2 The confirmation dialog before a copy

As for a map of one's own: the file is checked first (D3, D5), then the device asked for its
space, then the confirmation dialog (`confirm()`, wide, Copy by default, as copying destroys
nothing; Escape and closing are Cancel):

- **Heading** "Copy the Course to the Device?" ("Copy the Courses to the Device?" for several),
  the device's name under it; for a file of waypoints only, which makes no course, "Copy the
  Waypoints to the Device?", and no Course Name.
- **The map** of the course (D14), where libshumate is installed: the map of the activity page
  (its tiles only once the user has turned them on there, their attribution, the note without a
  network), the file drawn in the colours of a track there: a track a line, a route a dashed
  line with a dot at each of its points, the start of each line green, the waypoints blue
  markers; every line of the file in view, kept clear of the attribution; it takes no clicks.
  Without the tiles the drawing alone on a plain background; the confirmation dialog never waits
  for them.
- **Course Name**, an entry: the `<name>` of the track or route, else the file's name without
  `.gpx`. Shown only when the file makes one course; a file of several tracks lists their names
  in the contents, unchanged.
- **Contents**: "track, {distance}, ↑ {ascent}, {count} track points" / "route, {count} route
  points" / "{count} tracks, {distance}" / "{count} routes" / "{count} waypoints" (each kind the
  file holds, joined; a track's points, a route's and the file's waypoints each in words of
  their own).
- **File on the Device**: "{file name} · {size}" (D4).
- **Free After Copying**: the maps' figure.
- **What the device does**, one line for each kind the file holds, said of Garmin's devices in
  general:
  - tracks (a plain box): "The device makes the course when you unplug the cable." (several:
    "… makes {count} courses …");
  - a route (a yellow warning box): "Garmin devices join the points of a route with straight
    lines, not roads. For a course along roads, export a track instead of a route.";
  - waypoints (a plain box): "Garmin devices save waypoints as saved locations."
- **Cancel** · **Copy**.

A name changed in the entry is the course's: openExpress copies the file with that name in place
of the track's or route's `<name>` (added when it has none); nothing else of the file changes
(D4). The file's name on the device is made from the course's name.

### 4.3 The outcomes

- **Copied**: the toast "Copied “{name}”. Unplug the device so it can import the file."; the
  page read again, the file under Waiting for the Device and in the banner.
- **Refused before the confirmation dialog** (an error titled "Course Not Added", with the
  reason; nothing written): not a GPX file, too large (D5), nothing in it to navigate, no folder
  for it on this device.
- **The same file already waiting** (the same size and content): said in the confirmation
  dialog ("The same file is already waiting on the device as {name}."), not refused.
- **No room**: the maps' message.
- **The copy failed**: the partial file removed, the reason shown, the page read again.

The trash button of a row asks (`confirm()`, Cancel by default, **Remove** destructive) "Remove
“{name}” From the Device?", in words of courses.py that the command line says too:

- a course openExpress copied: "The device keeps no other copy of it.";
- a course from Garmin Connect: "The device keeps no other copy of it; Garmin Connect can send it
  again.";
- a course of unknown origin: "openExpress did not copy this course and it did not come from
  Garmin Connect. The device keeps no other copy of it.";
- a waiting file: "The device has not made a course of it yet."

Removed: the toast "Removed “{name}” from the device.", the page read again. A file gone or changed
since the list was shown ("Course Not Removed" with the reason), or a device that stopped
answering: the reason said, the page read again.

### 4.4 The command line

- `openexpress courses`: the device's courses (name, origin, distance, ascent, date), the files
  waiting for it and the number of saved locations (D10); the distance and ascent written as
  `activities` writes them (kilometres with two decimals, whole metres), the ascent after ↑.
- `openexpress courses add FILE.gpx [--name COURSE] [--apply]`: without `--apply` what would be
  copied (the course's name, the file's name on the device, what the file holds and what the
  device does with it, the space); with it the copy. `--name` names the course, as the entry;
  for a file that makes no course or several it is refused, with the reason.
- `openexpress courses remove NAME [--apply]`: a course or a waiting file, by its name or a part
  of it (letter case and diacritics aside); several matches list them and remove nothing.
- Every one works with `--path DIR` and `--device-uri URI`, as the other commands that open a
  device. No new exit code: a refusal is 1, with its reason.

### 4.5 Experimental

The section's chip **Experimental**, the tooltip of **Add Course…** ("Copy a GPX file to the
device (experimental)") and the commands' help say so (D12).

### 4.6 Texts (English, Polish)

| English | Polish |
|---|---|
| Courses | Kursy |
| Experimental (chip) | Eksperymentalne |
| {count} file waiting – unplug the device so it can import it | {count} plik czeka – odłącz urządzenie, aby mogło go zaimportować (ngettext) |
| On the Device · Waiting for the Device · Saved Locations | Na urządzeniu · Czeka na urządzenie · Zapisane lokalizacje |
| Add Course… | Dodaj kurs… |
| Copy a GPX file to the device (experimental) | Skopiuj plik GPX na urządzenie (eksperymentalne) |
| Remove {name} (the trash button's tooltip) | Usuń {name} |
| openExpress · Garmin Connect | openExpress · Garmin Connect |
| Waypoints from GPX files and locations saved on the device | Punkty z plików GPX i lokalizacje zapisane na urządzeniu |
| This device does not make courses of GPX files | To urządzenie nie tworzy kursów z plików GPX |
| Could not be read from the device | Nie udało się ich odczytać z urządzenia |
| No Courses on the Device | Brak kursów na urządzeniu |
| Copy a GPX file with a track or a route – the device turns it into a course. | Skopiuj plik GPX ze śladem lub trasą – urządzenie zrobi z niego kurs. |
| Choose a GPX File | Wybierz plik GPX |
| Copy the Course to the Device? / Copy the Courses to the Device? | Skopiować kurs na urządzenie? / Skopiować kursy na urządzenie? (ngettext) |
| Copy the Waypoints to the Device? | Skopiować punkty na urządzenie? |
| Course Name · Contents · File on the Device · Free After Copying | Nazwa kursu · Zawartość · Plik na urządzeniu · Wolne miejsce po skopiowaniu |
| track · route · {count} tracks · {count} routes · {count} waypoints · {count} track points · {count} route points | ślad · trasa · {count} ślady · {count} trasy · {count} punkty · {count} punkty śladu · {count} punkty trasy (ngettext) |
| The device makes the course when you unplug the cable. | Urządzenie utworzy kurs po odłączeniu kabla. |
| The device makes {count} courses when you unplug the cable. | Urządzenie utworzy {count} kursy po odłączeniu kabla. (ngettext) |
| Garmin devices join the points of a route with straight lines, not roads. For a course along roads, export a track instead of a route. | Urządzenia Garmina łączą punkty trasy prostymi odcinkami, nie drogami. Jeśli chcesz kurs po drogach, wyeksportuj ślad zamiast trasy. |
| Garmin devices save waypoints as saved locations. | Urządzenia Garmina zapisują punkty jako zapisane lokalizacje. |
| Copy | Skopiuj |
| Copied “{name}”. Unplug the device so it can import the file. | Skopiowano „{name}”. Odłącz urządzenie, aby mogło zaimportować plik. |
| Course Not Added · Course Not Removed | Nie dodano kursu · Nie usunięto kursu |
| Remove “{name}” From the Device? | Usunąć „{name}” z urządzenia? |
| Remove | Usuń |
| Removed “{name}” from the device. | Usunięto „{name}” z urządzenia. |

English labels in GNOME's header capitalization (buttons, headings, titles of rows and groups),
the rest in sentences; Polish in sentence case, with its hard spaces. The names of Garmin's things
("Kursy", "Zapisane lokalizacje") are the words of Garmin's Polish manuals of these devices.

## 5. Safety

### 5.1 Where it writes and removes

- **Writes** only to the folder the device declares inward for `GPSData`, and only when that
  folder is `Garmin/NewFiles` (compared without case, as the device's storage does) (D9). A
  device that declares another folder is not offered the feature; the log says which folder.
- **Removes** only one file at a time, never a folder: a `.fit` directly in the device's courses
  folder (`FIT_TYPE_6` outward, `Garmin/Courses`) or a `.gpx` directly in `Garmin/NewFiles`.
  Nothing in a subfolder, nothing by a pattern; the locations file never.
- Every path is made from a folder of the declaration and one name checked as below; no part of
  a path comes from the file's content but the course's name, made safe (5.2).

### 5.2 The file

- **Size** (D5): up to 25 MB, read whole only after its size is checked.
- **A GPX file** must be XML with no document type declaration and no entities (a `<!DOCTYPE`
  refuses it: no expansion of entities, no external file read), its root `gpx` in GPX 1.1's or
  1.0's namespace, and at least one track with points, route with points or waypoint; every
  latitude within ±90 and longitude within ±180. What it holds is counted for the confirmation
  dialog.
- **A course name changed** (D4): written in place of the text of the track's or route's
  `<name>` (escaped as XML text; an element added when it has none); the other bytes of the file
  as they were; the copy checked as any GPX before it is written.
- **The name on the device** (D4): made from the course's name; letters with accents written
  without them ("Pętla" → "Petla"), spaces as `_`; only letters, digits and `. _ - ( )`; no name of
  a device of DOS (`CON`, `NUL`, `COM1`…), no dot at the end; up to 64 characters with the
  extension `.gpx` in the case the device declares. A name already in the folder (without case)
  is never overwritten: "_2", "_3"… is added, and the confirmation dialog shows the name.

### 5.3 Two installs, one device

- A copy or a removal takes the sync's lock (`service.sync_lock`), which holds the other
  install's too: a sync and a copy never write to the device at once, in either install (D11).
- What openExpress copied is recorded per device (`devices/<unit>/courses.json`: the course's
  name, the file's name, its size and MD5, when it was copied; no content). The version from
  source also reads the Flatpak's record, never writes it; a course either install copied counts
  as openExpress's (D13).
- The lists of 4.1 are read from the device: both installs show the same courses.

### 5.4 The log and the tests

- The log names the folder and the size (`applog.size`); the user's file names, course names and
  places are hidden (`applog.hide`).
- The tests use made-up GPX and FIT files and the fake storages only; none reaches a device.

## 6. Decisions

### D1: where the window offers it — chosen: 1a

- **1a. A section of its own, Courses, after Maps.** + One place for what is navigated on the
  device; room for later the courses of the account. − One more section in the sidebar.
- **1b. A group of the Maps page.** − Maps is long already; courses are no maps.

### D2: what is copied for a GPX file — chosen: 2a, for both devices

- **2a. The file, to the folder of `GPSData`; the device makes the course.** The Edge 1050 and
  the fēnix 8 both do (2.3).
- **2b. A FIT course made by openExpress.** Not for a plain course: no device needs it. It is
  stage 2 (section 8), the way to give a course its type.

### D3: what a GPX file may hold — chosen: 3a

- **3a. Any valid GPX with something to navigate; the confirmation dialog says what Garmin's
  devices make of it**, of routes and waypoints in general words (section 2.3: checked on the
  Edge only).
- **3b. One track or one route only.** − Refuses files the device takes well.
- **3c. Routes refused.** − A course of straight lines can still be of use; the warning says it.

### D4: the course's name and the file's — chosen: 4a

- **4a. The course's name in an entry (from `<name>`, else the file's name); a changed name
  written into the copy's `<name>`; the file's name on the device made from it, a taken name
  with "_2".** + The name the device shows is chosen before the copy; the waiting file is
  recognised by its name.
  - − openExpress writes one element of the user's file (5.2).
- **4b. The file as it is; its name made safe only.** − A route planner's `<name>` ("Track 001")
  is what the device shows.

### D5: the largest file — chosen: 5a

- **5a. Up to 25 MB.** The check of a GPX in memory stays small.

### D6: the lists — chosen: 6a

- **6a. Courses, waiting files and the count of saved locations, read from the device.** + What
  the device holds, whoever put it there.

### D7: what can be removed — chosen: 7a

- **7a. Any course and any waiting GPX file, each with its confirmation dialog; a course of
  unknown origin with the stronger warning (4.3); saved locations not.** Removing a course's file
  over USB is taken well by both devices (2.3).
- **7b. Only what openExpress copied.** − The courses from Garmin Connect cannot be tidied.

### D8: which devices — chosen: 8a

- **8a. Those that declare `GPSData` inward to `Garmin/NewFiles`.**

### D9: the folders — chosen: 9a

- **9a. Only what the devices declare: writes to `Garmin/NewFiles`; removals in `Garmin/Courses`
  and `Garmin/NewFiles`.** `Garmin/GPX`, which neither declares, is left out: a device that
  declares it for `GPSData` is not offered the feature. `Garmin/POI` is never touched.

### D10: the commands' names — chosen: 10a

- **10a. `courses` (list, `add`, `remove`).** The postponed sending of the account's courses fits
  as `courses send NAME`.

### D11: the lock — chosen: 11a

- **11a. The sync's lock, the other install's with it.** − A copy waits for a sync to end (it is
  refused with the sync's message).

### D12: experimental — chosen: 12a

- **12a. A chip in the section's title, and the tooltips and help, until openExpress itself has
  copied a course to a fēnix 8 and an Edge 1050 and removed it.** Both were done (2.3); the
  chip stays all the same, and when it goes is decided later.

### D13: where a course came from — chosen: 13a

- **13a. From the FIT file and openExpress's record:** "Garmin Connect" for the product 65534 in
  `file_id`; "openExpress" for a course whose name a copy of either install recorded, made by the
  device (product 20119) after that copy; otherwise not known (no chip; the stronger warning).
  The course's name is the recorded one or its start (a device cuts a long name); "after" allows
  the device's clock and the computer's to be ten minutes apart. Removing such a course takes its
  name off this install's record, so a course of that name copied by hand later is not taken for
  openExpress's.
  - \+ No guess: what is not known is said to be so.
  - − A course copied by openExpress on another computer is not known.
- **13b. No origin.** − Every removal warned the same; a course from Garmin Connect looks like
  any.

### D14: the map in the confirmation dialog — chosen: 14a

- **14a. The activity page's map:** a track as a line, a route as a dashed line through its
  points, the start marked; the streets of OpenStreetMap only as that page allows them (once
  turned on), otherwise the line alone.
  - \+ The course is seen before it is copied; no new map.
  - − A larger confirmation dialog: the narrow window shows the map above the fields, smaller.
- **14b. No map.** − A file named "Track 001" says little.

## 7. What can go wrong, and how it is found

| What | How it is prevented, and found |
|---|---|
| A file written outside the allowed folder | the folder only from the declaration and D9; names made safe (5.2); tests of each refusal |
| A file of the device overwritten | a taken name gets "_2"; the folder read just before the copy |
| A GPX that is no GPX, or harmful XML | the checks of 5.2 before the confirmation dialog; no document type, no entities |
| A changed name breaks the file | one element written, the copy checked as any GPX before the write |
| A route expected to follow roads | the yellow warning (4.2) |
| A course removed that was wanted | the confirmation dialog names it, its origin and that the device keeps no other copy |
| The saved locations lost | the locations file is never removed or written |
| A sync and a copy at once | the sync's lock (D11) |
| The streets asked of OpenStreetMap unasked | the map in the confirmation dialog follows the activity page's setting (D14) |
| No room | the free space checked before the confirmation dialog and before the copy |
| The Flatpak cannot read the file | the window through the portal; the command line names the folders it reads |

## 8. Stage 2: a FIT course of openExpress's own, with the course's type

A design of its own, not made yet. Why: the Edge 1050 does not take the activity type from a
GPX `<type>` (2.3), so a course gets its type only from a FIT course file. What stage 2 is to
settle:

- **The FIT course written by openExpress** from the GPX (`file_id` type 6, `course` with its
  name, sport and sub-sport, one `lap`, the `record`s, perhaps course points), copied to the
  folder the device declares inward for `FIT_TYPE_6` (`Garmin/NewFiles`), checked on both
  devices as stage 1 was (2.3).
- **A list "Course Type" in the confirmation dialog**, with the seven types of Garmin Connect's
  course list, named as Garmin Connect on the web names them:

  | Key | English | Polish |
  |---|---|---|
  | `road_biking` | Road Cycling | Kolarstwo szosowe |
  | `gravel_cycling` | Gravel/Unpaved Cycling | Jazda po drodze szutrowej/nieutwardzonej |
  | `mountain_biking` | Mountain Biking | Kolarstwo górskie |
  | `running` | Running | Bieganie |
  | `trail_running` | Trail Running | Bieg przełajowy |
  | `hiking` | Hiking | Piesze wędrówki |
  | `other` | Other | Inny |

  Their FIT sport and sub-sport, and what each device shows of them, are learnt by trying them
  on the devices.
- **Whether the GPX copy of stage 1 stays** for files whose type is not chosen.

## 9. Later, or not at all

- **Points of interest** (`.gpi` in `Garmin/POI`): not at all. Garmin's maps have their own;
  the Edge 1050 declares `Garmin/POI` for such files and for Garmin's safety cameras, and the
  fēnix 8's manual knows only the points of its maps.
- **A page of a course's details** (its map, profile, points), from a row of On the Device.
- **Renaming a course on the device.**
- **The account**: a course imported to Garmin Connect from a file, and courses sent from the
  account (`docs/design/wyslij-na-urzadzenie.md`).
- **FIT and TCX course files** from the computer: the same copy to the folder of `FIT_TYPE_6` or
  `FitnessCourses` (the Edge 1050 took a TCX course, 2.3).
- **Saved locations**: their list by name and the removal of one (the device's menu does it).
- **Editing**: reversing, joining a course.
- **A file dropped on the window.**
- **Named trails** (`NamedTrails`, `Garmin/SQL`): not offered.

## 10. Open questions

1. The limits: how many courses and saved locations each device holds, and what happens past
   them (an Edge: 100 and 200 by shops' specifications); whether the confirmation dialog should
   warn near them.
2. A very long track (several thousand points): taken whole, cut, or refused?
3. A track with no `<name>` copied as it is: what the device names the course (with D4 the copy
   always has one).
4. The Polish of "Saved Locations" and of the course list in Garmin's manuals of these devices.
