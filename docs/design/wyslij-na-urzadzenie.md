# Design: sending workouts, the calendar and courses to a device

Status: postponed. The design waits for a review before any work; each decision of section 5
has a recommended option, and nothing is implemented. The mockups (section 4) are drawn from libadwaita widgets with a
made-up device, account, workouts and courses.

## 1. Scope

From the Garmin account to the connected device, the way the Garmin Connect app sends them, over
the device's file queue in Garmin Connect that openExpress already empties at every sync:

- **a workout** of the account (run, bike, strength, HIIT, yoga, swim, multisport…);
- **the calendar**: the workouts scheduled from today, those of training plans among them;
- **a course** of the account.

Not in scope: creating or editing workouts, courses or training plans (Garmin Connect does it),
segments, race events, PacePro and Power Guide (the same mechanism, later), files from the
computer without the account (a GPX file, points of interest: a design of their own), and
everything the phone sends over Bluetooth only.

## 2. What Garmin Connect does

Facts of its Android app, read from its files (no request was made):

- **A workout:** `POST /device-service/devicemessage/messages`, a JSON array of one item
  `{deviceId, fileType: "FIT", messageType: "workouts", messageUrl:
  "workout-service/workout/FIT/{workoutId}", messageName, priority: "1"}`; the answer has no
  body. Then the app syncs: the device fetches the FIT file (type 5) from the queue.
- **The calendar:** `POST /mobile-gateway/calendar/schedule-workout/device/{deviceId}` with
  `{"startDate": "yyyy-MM-dd"}` (today); before it, `GET
  /calendar-service/workout/schedule/summary/?startDate=` answers `scheduleStartDate`,
  `scheduleEndDate`, `scheduleWorkoutCount`. The server chooses how many days it sends; the
  device gets queue items of type `schedules` (FIT type 7) and `workouts` (FIT type 5), which
  belong together. For a workout of a Garmin Coach plan the app uses `PUT
  /workout-service/schedule/sendtodevices` (no body) instead. The app warns that a device holds
  a limited number of workouts and that the calendar can replace some.
- **A course:** `POST /course-service/course/{courseId}/sendToDevice/{deviceId}`: the server
  puts it in the queue (FIT type 6).
- **The lists:** `GET /workout-service/workouts?includeAtp=false&orderBy=UPDATE_DATE` (the
  account's own workouts, without those of plans: `workoutId`, `workoutName`, `sportType`,
  `estimatedDurationInSecs`, `estimatedDistanceInMeters`, `updateDate`…); `GET
  course-service/course/owner/{displayName}` (`courseId`, `courseName`, `activityType`,
  `distanceInMeters`, `elevationGainInMeters`, `updatedDate`, `favorite`…).
- Garmin Express puts nothing in the queue: it only empties it, as openExpress does.

## 3. What openExpress has

- The queue read, each file downloaded, written to the folder of its type (`Garmin/NewFiles`
  on the watches), acknowledged (`device_messages`, `download_message`, `ack_message`); only the
  types the device declares `InputToUnit` in its `GarminDevice.xml`; the free space checked
  before each file.
- The device checked against the account before any request about it (`require_verified`).
- The dialogs `confirm()` and `inform()`, the tasks of a button (`run_task`), the writes to
  the device locked (`writes_to_device`, `WRITING`), the answers of the servers kept a while
  (`answers.ANSWERS`), the dry runs of the commands (`--apply`).
- A fēnix 8 declares the workouts (`FIT_TYPE_5`), courses (`FIT_TYPE_6`) and schedules
  (`FIT_TYPE_7`) both ways: in through `Garmin/NewFiles`, out of `Garmin/Workouts`,
  `Garmin/Courses`, `Garmin/Schedule`. It declares no limit of their number.
- The sign-in of openExpress (OAuth1, as Garmin Express's) reads the three services: one read
  of the workouts, of the courses and of the calendar's summary each answers 200 with JSON of
  the shape of section 2. Its writes (the queue item, `mobile-gateway`, `sendToDevice`) are
  tried by the first attempt (section 9).

## 4. The window and the command line

Mockups: wide (1000 px) and narrow (360 px), light and dark, in Polish: the page, the confirmation dialog
for a workout, the confirmation dialog for the calendar, the page after a workout was sent.

### 4.1 Where it is offered

A section of the device view, **Workouts and Courses** ("Treningi i kursy"), after
Activities: for a device of the account (checked as at a sync), while logged in, that
declares at least one of `FIT_TYPE_5`, `FIT_TYPE_6`, `FIT_TYPE_7` as `InputToUnit`. Its groups,
each shown only for what the device declares:

- **Calendar**: the row "Scheduled workouts" – "{count} workouts from {start} to {end}" (from the
  summary), or "No workouts scheduled from today"; the button **Send to Device…**.
- **Workouts**: a search over the list; each row: the name, "{sport} · {duration} ·
  {distance}" (what the account gives), the button **Send to Device…** (an icon with a tooltip
  in a narrow window). The description: "Your own workouts in Garmin Connect; those of training
  plans go with the calendar".
- **Courses**: the same; "{sport} · {distance} · ascent {ascent}".
- **On the device**: "Workouts" and "Courses" with how many files the device holds in its
  folders of them (read from the device, no request), and "Waiting in the queue" with how many
  items of the queue wait for this device.

The lists come when the section is first shown and on Refresh (D8).

### 4.2 The confirmation dialog and the work

**Send to Device…** of a workout asks (`confirm()`, Cancel by default; Escape and closing are
Cancel):

> **Send "6 × 800 m intervals" to fēnix 8?**
> openExpress adds the workout to the file queue of this device in Garmin Connect (account
> {account}) and writes it to the device now. The device shows it after you unplug it.
>
> This is an experimental feature.
>
> Cancel · **Send**

The calendar's confirmation dialog names how many workouts and which days, and the limit:

> **Send the calendar to fēnix 8?**
> 12 scheduled workouts from 5 to 18 November. Garmin Connect adds them to the file queue of
> this device (account {account}) and openExpress writes them to the device now. A device holds
> a limited number of workouts: the calendar can replace older ones on it.

A course's confirmation dialog is the workout's, with "the course". In every confirmation dialog the device's
name stays on one line (its spaces do not break).

Then, as the task of the button (busy meanwhile), the device's writes locked:

1. the queue read fresh; an item with the same `messageUrl` already waiting is not added again
   (D4);
2. the request (D2, D10, D13);
3. the queue read again: the new items must be in it (the answer has no body);
4. the queue emptied onto the device, as at a sync (D3): every item waiting, the confirmation dialog has
   said how many others wait.

### 4.3 The outcomes

- **Written**: the toast "Written to the device: 1 workout" (the calendar: "Written to the
  device: the calendar and 12 workouts"); "On the device" read again.
- **Already waiting** (step 1): nothing added; the queue emptied onto the device all the same.
- **Accepted, not in the queue yet** (step 3): "Garmin Connect accepted it, but the queue does
  not show it yet. A sync writes it when it comes." Nothing written.
- **Refused (401, 403)**: "Garmin Connect does not let openExpress send this" (the computer's
  clock first, as everywhere); the section keeps the lists.
- **No room on the device**: the sync's own message for a file that does not fit; the item
  stays in the queue for the next sync.
- **Network error or no answer**: whether the item was added is not known; the queue read on
  Refresh says.

### 4.4 Texts (English, Polish)

| English | Polish |
|---|---|
| Workouts and Courses | Treningi i kursy |
| Calendar · Workouts · Courses · On the device | Kalendarz · Treningi · Kursy · Na urządzeniu |
| Scheduled workouts | Zaplanowane treningi |
| {count} workouts from {start} to {end} | {count} treningów od {start} do {end} (ngettext) |
| No workouts scheduled from today | Brak zaplanowanych treningów od dziś |
| Send to Device… | Wyślij do urządzenia… |
| Your own workouts in Garmin Connect; those of training plans go with the calendar | Twoje treningi w Garmin Connect; treningi z planów idą z kalendarzem |
| Waiting in the queue | Czeka w kolejce |
| Send "{name}" to {device}? | Wysłać „{name}” do urządzenia {device}? |
| Send the calendar to {device}? | Wysłać kalendarz do urządzenia {device}? |
| Send | Wyślij |
| Written to the device: {count} workout | Zapisano na urządzeniu: {count} trening (ngettext) |

The words are Garmin Connect's own in both languages ("Treningi", "Kursy", "Kalendarz",
"Wyślij do urządzenia").

### 4.5 The command line

- `openexpress workouts` – the account's workouts (name, sport, duration, distance) and the
  calendar's summary; `openexpress workouts send NAME [--apply]` – a name or a part of it, as
  `courses remove` matches names.
- `openexpress courses`, `openexpress courses send NAME [--apply]`.
- `openexpress calendar`, `openexpress calendar send [--apply]`.

Without `--apply` the plan: what would be added to the queue, for which device and account
(masked, as every command masks it), how many items already wait. With `--apply` the steps of
4.2; the outcome as in 4.3. A new exit code only with its line in `docs/cli.md`.

### 4.6 Experimental

The window's confirmation dialog and the commands' help say "experimental" until two attempts on devices
have succeeded (section 9), the second by another user.

## 5. Decisions

### D1: where the window offers it — recommended: 1a

- **1a. A section of the device view, Workouts and Courses (recommended).**
  - \+ One place for what goes from the account to this device; room for the calendar, the
    counts and later segments.
  - − One more section in the sidebar.
- **1b. In the Activities section.** − Activities are what the device recorded; these go the
  other way.
- **1c. A dialog of the Sync button.** − Hidden; no room for the lists.

### D2: how it reaches the device — recommended: 2a

- **2a. The queue, through the requests Garmin Connect makes (recommended).**
  - \+ One mechanism for all three; the device and the account end as after the phone's app;
    the acknowledgement tells Garmin Connect the device has it.
  - − It writes to the account (a queue item): asked first (D5).
- **2b. The FIT file fetched directly (`messageUrl` without a queue item) and written.**
  - \+ Nothing written to the account.
  - − Only for a workout (the calendar's file is made by the server for the queue; a course's
    address is not known); two mechanisms.

### D3: when it is written — recommended: 3a

- **3a. Right away: the queue emptied onto the device as at a sync (recommended).**
  - \+ Done when the confirmation dialog is answered; the device shows it at the unplug.
  - − Other items waiting go too: the confirmation dialog says how many.
- **3b. At the next sync.** − Nothing seen until then; the device may sync by its own Wi-Fi or
  the phone first, which is fine but not what was asked.

### D4: an item already waiting — recommended: 4a

- **4a. Not added again; its `messageUrl` compared (recommended).**
- **4b. Always added.** − Two copies of one workout on the device.

### D5: the confirmation dialog — recommended: 5a

- **5a. What, to which device, for which account, what follows; the calendar with its count, its
  days and the limit (recommended):** the texts of 4.2.
- **5b. No confirmation dialog for a single workout.** − A write to the account without one, unlike
  Add to Account.

### D6: the device's limit of workouts — recommended: 6a

- **6a. The counts on the device shown, the calendar's confirmation dialog warns; nothing deleted
  (recommended).**
  - \+ No guess of a number the device does not declare; a sync never deletes files on the
    device.
  - − The limit itself is learnt only on a device (section 9).
- **6b. A refusal above a number.** − The number is not known.
- **6c. Older workouts offered for deletion.** − Against the rule that a sync deletes nothing.

### D7: which devices — recommended: 7a

- **7a. Devices of the account that declare the types as `InputToUnit`; each group only for
  its type (recommended).**
- **7b. Every device of the account.** − A device that cannot take a workout would be offered it.

### D8: the lists — recommended: 8a

- **8a. Read when the section is first shown, kept a few minutes (`LIFETIMES`), asked fresh on
  Refresh; the queue always fresh before a request (recommended).**
- **8b. Read at every visit.** − Requests for nothing new.

### D9: the workouts of training plans — recommended: 9a

- **9a. The list shows the account's own workouts (`includeAtp=false`); those of plans go with
  the calendar (recommended)** – as the app's own list.
- **9b. All of them.** − A plan's workout sent alone loses its day.

### D10: the calendar's request — recommended: 10a

- **10a. `POST /mobile-gateway/calendar/schedule-workout/device/{deviceId}` from today
  (recommended)**, if the sign-in of openExpress passes `mobile-gateway` (section 9, the read
  first); otherwise the calendar group is not shown.
- **10b. `PUT /workout-service/schedule/sendtodevices`.** − Names no device: it may send to
  every device of the account.

### D11: the commands' names — recommended: 11a

- **11a. `workouts`, `courses`, `calendar`, each with `send` (recommended)** – as `courses` and
  `courses remove`.
- **11b. One command `send workout|course|calendar`.** − The lists would need another command.

### D12: the course's request — recommended: 12a

- **12a. `sendToDevice` (the server adds the item) (recommended).**
- **12b. A queue item of its own.** − Its `messageUrl` is not known.

### D13: experimental — recommended: 13a

- **13a. Experimental until two attempts succeed, the second by another user (recommended)** –
  as Add to Account.

## 6. What can go wrong, and how it is found

| What | How it is prevented, and found |
|---|---|
| The wrong device or account | the confirmation dialog names both; the device checked against the account first |
| Two copies | the queue read before the request (D4) |
| The device full, or its limit reached | the free space checked before each file, as at a sync; the counts shown; the calendar's warning (D6) |
| A workout of a sport the device lacks | the device's own rule; seen in section 9 |
| Accepted, not in the queue | said (4.3); a sync writes it when it comes |
| The sign-in refused by a service | said per service; the group of that service hidden |
| Other items of the queue written with it | said in the confirmation dialog (D3) |
| No network, or no answer | not known whether it was added: the queue read on Refresh says |
| The tests | no network: recorded answers only; no queue item made by a test |

## 7. Steps

Each step starts with tests that fail on the code before it, keeps its texts and their Polish in
the same commit, and passes the usual checks. Each waits for acceptance.

1. **The requests** (`connect`): the lists, the calendar's summary, the queue item of a workout,
   `sendToDevice`, the calendar's request; tests of each request's form and answers.
2. **Sending** (`service`): the queue before, the request, the queue after, the queue emptied
   onto the device; the log's lines (the kind, how many items; no names of workouts or
   courses, no Unit ID).
3. **The commands** `workouts`, `courses`, `calendar`; `docs/cli.md`.
4. **The window**: the section, its groups, the confirmation dialogs, the task, the outcomes; the narrow
   window.
5. **The documents**: `docs/guide.md`, `docs/privacy.md` (what is sent: a queue item naming the
   workout or course and the device), README.
6. **On a device**: section 9.

## 8. Size

About 450 lines of code (the requests 120, sending 90, the commands 90, the window 150), about
700 lines of tests, about 40 texts with their Polish; 5 commits.

## 9. The first attempt on a device

Made once, by hand, with the log kept, on the maintainer's own fēnix 8 and account:

- **Before:** `openexpress workouts` (the list) and `openexpress workouts send NAME` (the trial
  run): the workout, the device, the account, how many items wait.
- **The attempt:** one workout of the maintainer's own, `openexpress workouts send NAME
  --apply`; then `openexpress report --log`.
- **What says it worked:** "HTTP POST …/devicemessage/messages -> 200"; the queue read after it
  holds one item more, of type `workouts`; the download of that item and its acknowledgement as
  at a sync; after the unplug the workout in the device's list of workouts.
- **What says it did not:** a 401 or 403 (the sign-in does not reach the queue's writes: stop);
  an item that does not come (a sync later writes it, or not: stop and look at the queue in
  Garmin Connect).
- **Then:** the calendar and a course the same way, each its own attempt; the device's limit
  of workouts, if a calendar meets it; `docs/devices.md` says what was checked.
