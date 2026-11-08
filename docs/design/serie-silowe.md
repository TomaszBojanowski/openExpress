# Design: the sets of a strength workout, seen and corrected

Status: put off; nothing is implemented. Its direction is accepted (D2 chosen, D11 as proposed);
each other decision of section 6 has a recommended option. Known before it was put off:
openExpress's sign-in reads the sets of an activity, and Garmin Connect's exercise catalogue and
its Polish names are read without a sign-in. Open: how Garmin Connect keeps a correction (the
reading before and after one exercise is corrected, section 8), which D11 waits for. The mockups
(section 5) are drawn from libadwaita widgets with a made-up activity, account, sets and exercise
names.

## 1. Scope

- **Stage 1, read only:** the sets of an activity on its page and on the command line: each
  exercise with its sets (repetitions × weight, or the set's time), from Garmin Connect once the
  activity is on the account, the sets corrected there marked; from the device's file only before
  it is uploaded. A correction in Garmin Connect changes the activity on the account only: the
  file on the device keeps the watch's names.
- **Stage 2, experimental, writes to the account:** correcting the exercise of one set or of
  several at once, their repetitions and their weight, saved to Garmin Connect after a confirmation dialog;
  a copy of the sets kept before each save, the previous version restorable.

Not in scope: adding, deleting or reordering sets, their time and type; the file on the device
(Garmin Connect keeps the corrected sets, the device's file stays as the watch wrote it); the
other edits of an activity (name, description, privacy: a design of their own); workouts.

## 2. What the device's file holds

The FIT message `set` (225), one for each set and each rest: duration (milliseconds),
repetitions, weight (kilograms, scale 16), `set_type` (rest 0, active 1), start time, category
(`exercise_category`, 53 values in the public FIT profile), category subtype (a number within
the category; the profile names 1,846 exercises) and workout step. openExpress reads today the
duration and the type (the rest time of the activity page). The category and subtype are the
watch's guess; the file keeps it when the exercise is corrected in Garmin Connect.

## 3. What Garmin Connect does

Facts of its interface, not yet checked with a request:

- **The sets of an activity:** `GET activity-service/activity/{activityId}/exerciseSets` answers
  `{activityId, exerciseSets: [...]}`; a set has `exercises` (a list of `{category, name,
  probability}`, the name shown is the one of the highest probability), `exerciseTypeCandidates`,
  `duration` (seconds), `messageIndex`, `repetitionCount`, `startTime`
  (`yyyy-MM-dd'T'HH:mm:ss.SSS`), `setType` (`ACTIVE`, `REST`), `weight` (grams; -1 body weight;
  null none), `wktStepIndex`.
- **Saving:** `PUT` to the same address with the **whole list**: an exercise chosen becomes
  the one entry `{category, name, probability: 100.0}`; the sets not touched keep what they had;
  `messageIndex` and `exerciseTypeCandidates` are not sent; a set's `startTime` is its identity
  (a rest goes back after its set by it). The answer has no body. There is no undo.
- **An exercise:** two keys in capitals, a category and a name (`BENCH_PRESS`,
  `BARBELL_BENCH_PRESS`); a category without an exercise of its own name is chosen as itself
  (name null).
- **The catalogue:** `https://connect.garmin.com/web-data/exercises/Exercises.json` (categories,
  their exercises, `isBodyWeight`, muscles; other files for yoga, pilates, mobility) and the names
  in a language: `/web-translations/exercise_types/exercise_types_pl.properties` (keys
  `category_type_<CATEGORY>`, `exercise_type_<CATEGORY>_<NAME>`); both are read without a
  sign-in.
- **The activity's number on the account:** the duplicate check openExpress makes before
  uploading (`matchesByDeviceId`) answers `match.activityId` for an activity Garmin Connect has.

## 4. What openExpress has

- The activity page, from the device's files and its backups (`activity_page.py`), and the
  reading of `set` (duration and type).
- The duplicate check (`activity_matches`), today only whether Garmin Connect has each activity.
- Its sign-in (OAuth1, as Garmin Express's) reads the activities and their sets.
- The answers of the servers kept a while (`answers.ANSWERS`), asked fresh before a write; the
  dialogs `confirm()` and `inform()`; the account's writes of Add to Account (asked, logged,
  experimental); the device's backup folder (the copies of app lists).

## 5. The window and the command line

Mockups: wide (1000 px) and narrow (360 px), light and dark, in Polish: the section of stage 1,
the dialog of stage 2, the choice of an exercise, the confirmation dialog.

### 5.1 Stage 1: the section Serie

On an activity's page, after the chart, for an activity with sets (D1):

- A row for each exercise and its sets in a row: the exercise's name, under it "3 serie ·
  10 × 60 kg, 10 × 60 kg, 8 × 65 kg"; a set of body weight "10 × waga ciała"; a set without
  repetitions by its time ("1:00"); the rests left out (D4).
- Under the title the source: "Z Garmin Connect" or, before the activity is uploaded, "Z pliku
  zegarka (jeszcze niewysłane): ćwiczenia tak, jak rozpoznał je zegarek" (D2). From the file only
  the categories have names ("Przysiad"); sets, repetitions and weights are exact.
- From Garmin Connect, an exercise whose sets differ from the file's says so under its sets:
  "Poprawione w Garmin Connect" (D11).
- At the end "Razem serii: 9 · Razem powtórzeń: 84".
- Garmin Connect is asked when the page opens, if signed in and the activity is on the account;
  until the answer the section says "Wczytywanie serii z Garmin Connect…"; without an answer the
  file's sets are shown, said so ("Garmin Connect nie odpowiedział: serie z pliku zegarka").

### 5.2 Stage 2: Edytuj serie

- The section's button **Edytuj serie** (only for the sets from Garmin Connect) opens a dialog:
  every set, "Seria 1, 2…", with its exercise (a button), repetitions and weight (kilograms,
  steps of 0.5; "waga ciała" for a set of body weight).
- **Wybierz ćwiczenie**: a search "Szukaj ćwiczeń" and the catalogue by category, in the
  window's language; the exercise of the set marked.
- Several sets: "Zaznacz", then **Zmień ćwiczenie…** for all of them (the usual case: three sets
  of one exercise the watch named wrongly).
- What changed is marked; **Zapisz** is enabled once something changed.
- Saving: the confirmation dialog (5.3), then a task: the sets asked fresh (when they changed on the account
  since the dialog opened, nothing is written and the dialog says so), a copy kept (D7), the whole
  list written (D6), asked again and compared, the section shown anew. A toast "Zapisano
  w Garmin Connect".
- **Przywróć poprzednią wersję** in the section's menu: the copies of this activity, newest
  first, by their time; the one chosen is written back the same way, after a confirmation dialog.

### 5.3 The confirmation dialog

> **Zapisać zmiany serii w Garmin Connect?**
>
> „Ćwiczenia siłowe”, czwartek 10:30: zmienione 3 serie. openExpress zapisze listę serii tej
> aktywności na koncie tomek_b; przedtem zachowa jej kopię na tym komputerze, którą można
> przywrócić.
>
> To funkcja eksperymentalna.

Anuluj / Zapisz (`confirm()`; closing and Escape mean Anuluj).

### 5.4 Texts (English, Polish)

Garmin Connect's words where it has them (to be checked on connect.garmin.com):

| English | Polish |
|---|---|
| Sets | Serie |
| Set {number} | Seria {number} |
| {count} set / sets | {count} seria / serie / serii |
| {reps} × {weight} kg; {reps} × body weight | {reps} × {weight} kg; {reps} × waga ciała |
| Reps | Powtórzenia |
| Weight | Ciężar |
| Total sets: {sets} · Total reps: {reps} | Razem serii: {sets} · Razem powtórzeń: {reps} |
| From Garmin Connect | Z Garmin Connect |
| From the watch's file (not uploaded yet): exercises as the watch recognised them | Z pliku zegarka (jeszcze niewysłane): ćwiczenia tak, jak rozpoznał je zegarek |
| Corrected in Garmin Connect | Poprawione w Garmin Connect |
| Loading the sets from Garmin Connect… | Wczytywanie serii z Garmin Connect… |
| Garmin Connect did not answer: the sets of the watch's file | Garmin Connect nie odpowiedział: serie z pliku zegarka |
| Unknown | Nieznane |
| Edit Sets | Edytuj serie |
| Select Exercise | Wybierz ćwiczenie |
| Search exercises | Szukaj ćwiczeń |
| Select; Change Exercise… | Zaznacz; Zmień ćwiczenie… |
| Save | Zapisz |
| Save the changes to the sets in Garmin Connect? | Zapisać zmiany serii w Garmin Connect? |
| Saved in Garmin Connect | Zapisano w Garmin Connect |
| Restore Previous Version | Przywróć poprzednią wersję |
| The sets changed in Garmin Connect since they were shown; nothing was saved | Serie zmieniły się w Garmin Connect, odkąd zostały pokazane; niczego nie zapisano |

The names of the exercises are Garmin Connect's own, read by the program from Garmin's server in
the window's language (D3); none of them is kept in the repository.

### 5.5 The command line

- `openexpress activities sets NAME`: the sets of an activity (a name as `activities` lists it),
  as the section shows them, and their source.
- `openexpress activities sets NAME --set 1-3 --exercise TEXT [--reps N] [--weight KG]
  [--apply]`: TEXT a name or a part of it (one match; several are listed); without `--apply` a
  dry run that prints the changes.
- `openexpress activities sets NAME --restore [WHEN] [--apply]`: the newest copy, or the one of
  WHEN.
- `openexpress exercises [TEXT]`: the catalogue's names matching TEXT, with their category.

### 5.6 Experimental

The confirmation dialog, the dialog and the commands' help say "experimental" until two saves succeed, the
second by another user (D8).

## 6. Decisions

### D1: where — recommended: 1a

- **1a. A section Serie on the activity's page, after the chart (recommended).** + Where the
  workout is looked at; the put-off D19a of the activity page. − The page grows.
- **1b. A page of its own from the activity's page.** − One more step to the sets.

### D2: the source of stage 1 — chosen: 2a

- **2a. Garmin Connect once the activity is on the account, the file only before it is uploaded
  (recommended; chosen).** + The names as corrected (the file keeps the watch's); works before
  the upload too. − Two sources, said under the title.
- **2b. The file only.** + No account, no request. − The watch's guesses, and only the names of
  the 53 categories (the 1,846 exercises of the profile are not in openExpress).
- **2c. Garmin Connect only.** − Nothing without the account.

### D3: the names of the exercises — recommended: 3a

- **3a. Garmin Connect's catalogue and names, read from Garmin's server in the window's language
  and kept a week in `answers` (recommended).** + The very words of Garmin Connect; nothing of
  Garmin's in the repository. − A request; without it the keys are made readable ("Barbell
  bench press").
- **3b. Our own names for the 53 categories.** − No exercise names; translations to keep.

### D4: the rows — recommended: 4a

- **4a. The sets of one exercise one after another in a row; the rests left out (recommended).**
- **4b. Every set a row.** − Long; the rests in between.

### D5: what stage 2 changes — recommended: 5a

- **5a. The exercise (of one set or several), repetitions and weight (recommended).** + What the
  watch gets wrong. − Adding or deleting a set stays in Garmin Connect.
- **5b. Also adding, deleting and reordering sets.** − The order and the rests are tied to the
  sets' times; more to get wrong.

### D6: what is written — recommended: 6a

- **6a. The whole list, as a saving sends it (section 3): the sets not changed as they came, a
  set changed with only its changed fields different (recommended).** + The server gets what it
  knows. − Whether `messageIndex` survives a write is checked live first (section 8).
- **6b. Everything the reading answered, changed where wanted.** − Fields a saving does not
  send (section 3).

### D7: the copy and the restore — recommended: 7a

- **7a. Before each save the answer of the reading kept in the device's backup folder, the last
  ten of an activity; Przywróć poprzednią wersję writes one back, after a confirmation dialog
  (recommended).** + No undo on the server: this is it. − Files on the computer.
- **7b. No copy.** − A wrong save is for good.

### D8: experimental — recommended: 8a

- **8a. Experimental until two saves succeed, the second by another user (recommended).**

### D9: the commands — recommended: 9a

- **9a. `activities sets` and `exercises` (section 5.5) (recommended).** + Next to `activities`
  and `activities export`.
- **9b. A command `sets` of its own.**

### D10: which activities — recommended: 10a

- **10a. Every activity whose file or account has sets (strength, HIIT, yoga, pilates)
  (recommended).** − Yoga and pilates have times rather than repetitions: shown as such.
- **10b. Strength workouts only.**

### D11: what "corrected" means — recommended: 11a

- **11a. A set of the account differs from the file's set of the same start: its repetitions,
  its weight or its exercise's category (the file's category number named by the public FIT
  profile, whose names in capitals are Garmin Connect's category keys); a change of exercise
  within a category too when Garmin Connect tells a pick of the user apart (one entry of
  probability 100.0), which the reading of section 8 shows (recommended).** + Said only where
  something was changed. − A change within a category may go unsaid until that is known.
- **11b. No mark.** − The difference from the watch is not seen.

## 7. What can go wrong, and how it is found

| Case | What happens |
|---|---|
| Not signed in, the activity not on the account, no answer | the file's sets, said so; Edytuj serie not offered |
| The catalogue or its names not reachable | the keys made readable; Wybierz ćwiczenie offers the categories |
| The sets changed on the account since the dialog opened | nothing written, the dialog says so and shows them anew |
| The write answers an error (401, 403, 4xx) | nothing kept as saved; the copy stays; the error said, logged |
| The write succeeds but the reading after it differs | said ("Garmin Connect has other sets than were saved"), the copy offered |
| A set lost by the server | the copy: Przywróć poprzednią wersję |

The log says the requests and their answers' codes, never the names of exercises or weights.

## 8. Checked live first (each run by the maintainer)

Reading only:
1. `GET exerciseSets` of one strength activity before and after one exercise is corrected on
   connect.garmin.com: which set changed and how (its entries and their probabilities), the
   fields, `startTime`'s shape, grams, seconds, -1, `messageIndex`, `exerciseTypeCandidates`.
2. `matchesByDeviceId` for that activity: `match.activityId`; the file's sets against the
   account's (categories as keys, starts, repetitions, weights).

Writing, on a test activity only, each run after the maintainer's own ok:
3. The sets written back unchanged: the answer's code, nothing changed on the account.
4. One exercise changed; then restored from the copy.

## 9. Steps

1. **Stage 1, the file** (`fit.py`: the set's repetitions, weight, category; the section and its
   rows; the command): tests on made-up files.
2. **Stage 1, the account** (`connect.py`: the activity's number from the duplicate check, the
   sets, the catalogue and names; `answers`): after checks 1–2.
3. **Stage 2** (the dialog, the choice of an exercise, the confirmation dialog, the copy and the restore,
   the commands): after checks 3–4, each write of the first attempt with the maintainer's ok.

About 900 lines of code (stage 1: 400, stage 2: 500), about 1,200 lines of tests, about 45 texts
with their Polish; 7 commits.

## 10. The first save on the account

One test strength workout of the maintainer's (two sets of one exercise, recorded for it), its
sets read and kept; one exercise changed with `openexpress activities sets NAME --set 1-2
--exercise TEXT` (the dry run first, then `--apply`); the change seen on connect.garmin.com;
then `--restore --apply` and the old name seen again. What says it worked: the write's answer
2xx, the reading after it equal to what was written, Garmin Connect in the browser showing the
change, and after the restore the sets as before.
