# Design: the texts of the interface (English)

Status: accepted. D1–D13 with the recommended option of each (D1 A, D5 A, D12 yes) and the answers
of section 2.1. German stays with Weblate: its texts follow from the template.

The counts are of `po/openexpress.pot` (1,676 texts) as it is now: each text by the files that use
it (window `gui/`, command line `cli.py`, the rest of the program "core"), and in the window by the
part it plays (toast, description, heading, button), read from the code. A text used in several
places counts once.

## 1. Rules that stay

They are in force already and are not reopened here:

- Header case (GNOME HIG) for buttons, their tooltips, dialog headings, group and page titles,
  tabs, row and switch titles; sentences for toasts, body text and explanatory tooltips. Polish:
  sentence case everywhere, switches as nouns.
- A row's caption and lines of details: one sentence without a full stop, several with full stops.
- A text shared by the window and the command line gets a window msgid of its own when the two
  need different forms.
- Polish: hard space after one-letter words and before the en dash; the command line and the log
  with plain spaces.

## 2. Decisions

### 2.1 Answers to the open points

- D7: the command named in the map texts is `openexpress setup --from-installer GarminExpress.exe`,
  as the command line has it.
- D13: the titles of the notifications are sentences in Polish too (below, D13).
- Section 3: a text with a number (`{count} skipped…`, `{count} activity to upload now`) goes
  through ngettext, with the Polish plural forms.
- Background sync: one subtitle in Preferences and on the device page, EN "Syncs each time a device
  is connected, even when the window is closed", PL „Synchronizuje przy każdym podłączeniu
  urządzenia, nawet przy zamkniętym oknie”.

### D1. Unplugging: one verb, one phrase for "unplug and plug in again"

Today: "unplug" 56 texts (window 16, command line 9, core 26, shared 5); "disconnect" 15; "reconnect"
8 (6 of them in "disconnect and reconnect"); "plug it in again" 9 and "plug it back in" 2 (with
"unplug" before them); "connect it again" 1. One page mixes them: the chip "Unplug to install"
beside "Update ready: disconnect the device to install it".

| Option | |
|---|---|
| **A (recommended)** | "unplug" everywhere; "unplug it and plug it in again" (or "…the device…") as the one phrase; "eject" stays for a drive, which is another act |
| B | "disconnect" / "reconnect": shorter phrase, but 56 texts to change instead of about 19 |

To change under A: 19 texts (15 "disconnect", 2 "Reconnect it", 2 "plug it back in"; "connect it
again" 1 inside one of them). Among them the update instructions ("1. Disconnect the device from the
computer." → "1. Unplug the device from the computer.", "4. Reconnect it and …" → "4. Plug it in
again and …") and the warnings ("do not disconnect it while copying" → "do not unplug it while
copying").

PL: already "odłącz" and mostly "Odłącz je i podłącz ponownie" (10 texts). To align: 4 texts
("odłącz i podłącz kabel ponownie", "odłącz kabel i podłącz go ponownie", "a potem podłącz je
ponownie") → "odłącz kabel i podłącz go ponownie" for the cable, "odłącz je i podłącz ponownie"
for the device.

### D2. Full stops, one rule for each part of the window

| Part | Today | Rule (recommended) | To change |
|---|---|---|---|
| Toast | 7 with a full stop, all of them texts of the command line used in the window; about 30 without | no full stop (a toast is a short status, HIG) | 8: through window msgids (D9) |
| Group description | 4 with ("Changes take effect after you disconnect the device.", the two of the Connect IQ Store, the one of a USB drive's sync), 3 without | the caption rule: one sentence without, several with | 4 |
| Status page description (an empty or refused page) | both ("Sign in to manage apps.", "This device does not keep activities." beside "This takes a moment after the cable is plugged in") | the caption rule | about 10 (the list is made when the change is made: several come from functions the command line shares, which then need window msgids) |
| Title of an empty page | three styles: a sentence with a full stop, a sentence without, header case | header case, no full stop: "No Activities", "No Maps on This Device", "Sign-In Page Not Working"; "No Courses on the Device" stays | 3 |
| Dialog body | sentences, full stops; one exception ("No copy differs from the current order") | unchanged rule | 1 |

PL: the same rule; Polish texts follow the English ones (about 16 texts lose or get a full stop, and
the three titles: „Brak aktywności”, „Brak map na tym urządzeniu”, „Strona logowania nie działa”).

### D3. Header case where a text is a name

| Texts | Today | Proposal | Count |
|---|---|---|---|
| Rows of an activity's details | "Elapsed time", "Moving time", "Average moving pace"… | "Elapsed Time", "Moving Time", "Average Moving Pace"… (Garmin Connect writes them so in English) | 14 |
| Tiles on top of an activity | "Average pace", "Average heart rate", "Total ascent"… | "Average Pace", "Average Heart Rate", "Total Ascent"… | 5 |
| Zones | "Below zone 1" beside "Zone {number}" | "Below Zone 1" | 1 |
| Courses page | two sentences as row titles ("The courses could not be read from the device") | a name as title ("Courses", "Files Waiting") and the sentence as subtitle | 2 (+2 subtitles) |
| Question | "Remove “{name}” from the Device?" | "…From the Device?" (prepositions of four letters and more are capitalised) | 1 |
| Chip | "experimental", the only chip in lower case | "Experimental" | 1 |

24 texts. PL: sentence case, no change, except the two new subtitles of the Courses page.

### D4. Ellipses and the trash button

The rule: no "…" on a button that only opens a confirmation; "…" where more input follows (HIG).

| | Texts | Count |
|---|---|---|
| "…" removed (only a question follows) | "Add to Account…", "Forget Device…", "Clear All…", the trash tooltip "Remove…" of the Courses page | 4 |
| "…" kept (more input follows) | file choosers ("Add Map…", "Add Course…", "Add Playlist…", "Files…", "Folders…", "Choose Installer…", "Choose File…", "Export as GPX…"), "Restore…" (a list of copies), "Report a Device or Problem…" (a dialog to read and send) | 0 |
| Progress | "Loading Connect IQ Apps…" → "Reading Connect IQ Apps…" (every other page says "Reading …"); "Activating the map on the Garmin server" → with "…" as other single progress messages | 2 |

Trash button, one pattern: tooltip "Remove {name}" wherever the row has a name (apps and maps have
it; Courses "Remove…" and Music "Remove" ×2 change; Preferences "Delete These Files" stays: it
deletes the program's own files, not the device's). 3 texts.

PL: „Dodaj do konta”, „Zapomnij urządzenie”, „Wyczyść wszystko”, „Usuń {name}”, „Odczyt aplikacji
Connect IQ…”, „Aktywacja mapy na serwerze Garmin…”.

### D5. Headings of a failure: one pattern

Today: 5 "Cannot …" ("Cannot Add the Course", "Cannot Remove the Course", "Cannot Add the Map",
"Cannot Remove the Map", "Cannot Install the Map"), 1 "Could Not Open the Device", 9 "… Not …"
("Device Not Added", "Update Not Installed", "Cache Not Cleared"…).

| Option | |
|---|---|
| **A (recommended)** | "<Thing> Not <Done>", as most do: "Course Not Added", "Course Not Removed", "Map Not Added", "Map Not Removed", "Map Not Installed", "Device Not Opened" – 6 to change |
| B | "Could Not <Do> the <Thing>" – 10 to change |

PL (A): the pattern of the existing ones („Nie zainstalowano aktualizacji”, „Nie wysunięto
urządzenia”): „Nie dodano kursu”, „Nie usunięto kursu”, „Nie dodano mapy”, „Nie usunięto mapy”,
„Nie zainstalowano mapy”, „Nie otwarto urządzenia”; and „Tego urządzenia nie dodano” → „Nie dodano
urządzenia” (7 Polish texts).

### D6. Command line: errors, warnings, dry runs

- "Error: …" and "Warning: …" only: "ERROR {error}", "ERROR {item}: {error}" and the two "WARNING:"
  change. 4 texts. PL already „Błąd:”/„Uwaga:”: 4 texts follow.
- Dry run: one form, "Dry run: nothing was <done>. Add --apply to <do it>." Today 12 forms in 14
  texts. To change: "Dry run. Add --apply." (one msgid, 19 places: "Dry run: nothing was changed. Add
  --apply."), "Dry run. Add --apply to copy the files the device shows now." ("Dry run: nothing was
  copied. …"), "Would remove it from your account list. Add --apply." ("Dry run: … Add --apply to
  remove it."), and "a trial run without --apply" → "a dry run without --apply". 4 texts; the forms
  with a count ("Dry run: about {count} file would be copied. Add --apply.") stay.

PL: „Tryb próbny: nic nie zmieniono. Dodaj --apply.” and the like; 4 texts.

### D7. Terms

| Term | Today | Proposal | Count |
|---|---|---|---|
| Garmin account | 6 wordings in 23 texts: "On the Account", "In Your Garmin Account", "on your account", "not on your account", "registered to your Garmin account", "your account list" | "your Garmin account" in sentences; the row "Garmin Account" (not "On the Account"); the apps' list "the app list of your Garmin account" | about 8 |
| Connect IQ Store | "Connect IQ store" | "Connect IQ Store" (its name) | 4 |
| Garmin Connect | "Connect" alone ("most files are already in Connect", "Downloading file from Connect", "Connect did not confirm…", "To download from Connect…") | "Garmin Connect" | 4 |
| Application key | "client key", "consumer key", "OAuth1 consumer key" | "application key", as the window says | 4 |
| Primary wearable | "your active tracker in Garmin Connect" | "your primary wearable in Garmin Connect" | 1 |
| Unit ID | "unit ID" 5, "Unit ID" 2 | "Unit ID", as the device page shows it | 5 |
| Device software | "System Software" (group of the Updates page) beside "Device Software" (row of the device page) | "Device Software" for both | 1 |
| Apps | "Remove and Forget" ("forget" is a device's elsewhere) | "Remove From Device and Account" | 1 |
| Cache | "Answers of Garmin's servers" beside "Garmin Server Responses" | "Garmin Server Responses" | 1 |
| Command line | "{device} (software {version})" beside "{device}, software {version}"; "Connect token is missing (openexpress login)." beside "Not signed in. Run: openexpress login" | the second form of each | 2 |
| Files | "Select the Garmin Express Installer…" beside "Choose…" | "Choose the Garmin Express Installer…" | 1 |
| Map downloads | "…requires the Utilities.dll file from Garmin Express. Run: openexpress setup --utilities-dll …" (window and command line) | point to the installer: "Choose Installer…" in the window, "openexpress setup --from-installer GarminExpress.exe" | 2 |
| Background sync | "Preferences, Sync in the background" (no such label) | "Preferences, Background Sync" | 1 |

About 35 texts. PL follows the same terms (konto Garmin, Sklep Connect IQ, klucz aplikacji,
podstawowe urządzenie typu wearable, Unit ID, oprogramowanie urządzenia); Polish already says „Synchronizacja w tle” in the last one.

### D8. Punctuation

| | Today | Proposal | Count |
|---|---|---|---|
| Quotes | '…' around a name ("Map '{name}' cannot be installed…", "No app matches '{query}'") | “…” | 14 |
| Dash | "—" in "Device not connected — connect it to sync or update" and one sentence of the Updates page | en dash with spaces ("–"), as elsewhere | 2 |
| Contractions | "isn't" ×2, "won't", "aren't" ×2 | "is not", "will not", "are not" | 5 |
| "please" | "The saved sign-in is damaged; please sign in again." and two more | without "please"; "unauthorized (HTTP {status})" → "the sign-in has expired" | 3 |

PL: quotes already „…” in most; the en dash with a hard space before it; 4 texts.

### D9. Window msgids where the window and the command line share one

22 texts are shared. 11 need a window form: the 7 toasts with a full stop (D2), "Freed {size}.",
"The device finishes the removal after it is unplugged." (glued to "Removed {name}" with ". " in the
code: one window msgid "Removed {name}. The device finishes the removal after it is unplugged"),
"Software is up to date" (the command line puts it after "Device software:": a command-line msgid
of its own), "{device} was not forgotten.". The others ("{count} file", "Software {version}", "Last
seen {when}", "disabled", the date format, "Garmin device") stay shared.

PL: 11 new texts, the Polish of today without the full stop.

### D10. Comments for translators in the wrong place

- "Unsupported connection": its comment is on "Garmin device" (it moved one text up).
- "Sync With Garmin Connect": carries the comment meant for the description under it.
- "Last Sync": carries the comment meant for `pgettext("sync", "Not available")`.
- "Primary wearable" (the name of a server request): no `msgctxt "server request"` and no comment,
  unlike its twin.
- No comment: "Podcasts", "Audiobooks" (kinds of audio), "Sport", "Distance" (command-line columns).

8 texts; the text itself does not change, only its comment (the Polish stays).

### D11. FIT_TYPE_57 in the window

After a sync of an Edge 1050 the details of "Last Sync" show "other data (FIT_TYPE_57) ×4". What
the type is:

- the Edge declares it with the folder `Garmin/Sports/Backups` (`OutputFromUnit`) in its
  `GarminDevice.xml`;
- the upload address Garmin Connect gives for it is `upload/sport-backup`;
- the files of that folder have the FIT file type 57 in their headers: they are the device's
  backups of its activity profiles (sport files).

Name (the rule of `service.TYPE_NAMES`: from the folder and the upload address): EN "activity
profile backups", PL „kopie profili aktywności” (lower case, as the other names in lists). One
text, a new pattern of "Last Sync" in `tests/data/last_sync/` with its words in Polish and English.
No other type seen in the logs reaches the window as a code (FIT_TYPE_39 is TrueUp's, which TrueUp
handles).

### D12. The template without line numbers (`--add-location=file`)

Today every reference has a line number, so any change of code moves hundreds of them in the
template and in every `.po`, also when no text changes.

| Option | |
|---|---|
| **Yes (recommended)** | `xgettext` and `msgmerge` with `--add-location=file`: references name the file only; a change of code touches only the texts that change |
| No | as now |

What it changes:
- One diff once: every reference of the template and of each `.po` loses its line number.
- Weblate: its "Source string location" shows the file without a line, and its link opens the file,
  not the line. Its msgmerge add-on is off (it would drop the flags of `po/weblate-flags.txt`), so
  only our scripts write the references; Weblate keeps what it finds when it saves a translation.
  Fewer lines touched on both sides: fewer conflicts between Weblate's pull requests and ours.
- Translators lose the line, not the context: comments and `msgctxt` stay.
- The scripts: `tools/update-template.sh` (the template) and `tools/update-translations.sh`
  (msgmerge); `test_catalogs` must keep passing (it compares texts, not lines).

### D13. Desktop notifications

"Garmin: first sync required", "Garmin: this device was not synced" with a prefix; "Sync finished",
"Sync failed" without it and without the device. Decided: no prefix, the device's name in
the title, a sentence:

| When | EN title | PL title |
|---|---|---|
| the first sync waits for a choice | "{device}: choose how to sync it first" | „{device}: najpierw wybierz sposób synchronizacji” |
| a sync finished | "{device} synced" | „Zsynchronizowano urządzenie {device}” |
| a sync refused (another account) or failed | "{device} not synced" | „Nie zsynchronizowano urządzenia {device}” |

A failure before the device is known says "Garmin device" for its name. The bodies stay (the reason,
or "Open openExpress and choose how to sync the device."). 4 texts become 3.

## 3. Smaller texts

| Text today | Proposal | PL |
|---|---|---|
| "{count} not: over the limit of {size}" | "{count} skipped: larger than {size}" (ngettext: "{count} file skipped…" / "{count} files skipped…") | „{count} plik pominięty: większy niż {size}” / „{count} pliki pominięte: większe niż {size}” / „{count} plików pominiętych: większych niż {size}” |
| "now to upload: {count} activity" | "{count} activity to upload now" / "{count} activities to upload now" | „{count} aktywność do wysłania teraz” / „{count} aktywności do wysłania teraz” / „{count} aktywności do wysłania teraz” |
| "Garmin's update server has none. Getting it sends the Unit ID…" | "Garmin's update server has no picture of this device. Getting one sends the Unit ID…" | accordingly |
| "{action}: computer clock is off from the server by {minutes} min" | "{action}: this computer's clock is {minutes} min off; let the system set the time and try again" | „…: zegar tego komputera różni się o {minutes} min; włącz automatyczne ustawianie czasu i spróbuj ponownie” |
| "the Garmin account has no consent…", "device sync is turned off in the Garmin account" | say where: "…; turn it on in Garmin Connect" | accordingly |
| "{what}: unexpected response from the server" (9 places) | "{what}: Garmin's server gave an answer openExpress does not understand; try again later" | „{what}: serwer Garmina odpowiedział w nieznany sposób; spróbuj ponownie później” |
| "The change failed." | "Background sync could not be turned on or off." | „Nie udało się włączyć ani wyłączyć synchronizacji w tle.” |
| "…sign in in your web browser and paste the address you end up on." | "…sign in in your web browser, then paste the address the browser shows at the end." | accordingly |
| "Hidden from the file manager. While they are copied, the device leaves it for a moment" | "Hidden from the file manager: while they are copied, the device disappears from it for a moment" | accordingly |
| "the upload consent could not be checked. Try again later" (put after "Nothing was uploaded:") | "the upload consent could not be checked; try again later" | accordingly |
| "Map '{name}' cannot be installed: the server did not provide all of its files" | with “…” and a full stop, as its neighbours | accordingly |
| "{names}: openExpress cannot install apps on this device over USB yet. The app waits…" (`{names}` may be a list) | with ngettext (one app, several apps) | two forms |
| "…Refresh in a few minutes; if the device is still refused, …" (under "Garmin Accepted the Device") | "…if the device is still missing, …" | accordingly |
| "Set" (in "Change the Primary Wearable?") | "Change" | „Zmień” |
| "New Data Only" beside "Upload Everything" | "Upload New Data Only" | „Wyślij tylko nowe dane” |
| "Background Sync" with two different subtitles (Preferences, the device page) | one subtitle: "Syncs each time a device is connected, even when the window is closed" | „Synchronizuje przy każdym podłączeniu urządzenia, nawet przy zamkniętym oknie” |
| "Choose files stored on this computer" / "Choose a file stored on this computer" | stay (one or several files) | – |

17 texts.

## 4. Screenshots and documents

Step 10 (documents) and the screenshots quote the window. What the decisions change there:

| Decision | Screenshots | Documents |
|---|---|---|
| D1 | `updates.png`, `device.png` when an update waits ("Update ready: …"); `apps.png` (the group description under the apps) | `docs/guide.md` and others: "disconnect" 12×, "reconnect" 5×, "plug it back in" 1× (+ README 1×) |
| D2 | `apps.png` (group description) | – |
| D3 | `activity.png`, `activity-light.png`, the website's four activity pictures (detail rows and tiles) | `docs/guide.md`: "Moving time" and other detail names 5× |
| D4 | none of the pictures shows these buttons (the fēnix is on the account) | `docs/guide.md`: "Add to Account…" 3×, "Forget Device…" 4×, "Clear All…" 2× |
| D5, D6, D8, D9, D10, D12, D13 | none | `docs/cli.md`: "trial run" 3× (D6) |
| D7 | `updates.png`, `update-confirm.png` ("System Software"), `apps.png` ("Connect IQ store") | "System Software" 2×, "Connect IQ store" 2× (+ website 1×), "Remove and Forget" 2×, "unit ID" in prose |
| D11 | none (the details of "Last Sync" are not in a picture) | – |

## 5. Totals

About 176 English texts change (D1 19, D2 18 besides the 8 toasts of D9, D3 24, D4 9, D5 6, D6 8,
D7 35, D8 24, D9 11, D11 1, D13 4, section 3 17), and 8 comments for translators (D10); in Polish
about 120 follow (sentence case and the existing terms keep many as they are).
