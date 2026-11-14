# Translating openExpress

openExpress uses GNU gettext. The texts are in the template `po/openexpress.pot`, each language
has its `po/<language>.po`, and `po/LINGUAS` lists the languages. Every language is translated
in the browser on Hosted Weblate (next section), and only there: a translation is its
translators', so the project never changes their files. Polish alone is kept by the project
itself, with the code: a correction of Polish goes in an issue or a pull request.

## Translating on Hosted Weblate

Translate in your browser on
[Hosted Weblate](https://hosted.weblate.org/projects/openexpress/), with no files, no git and no
GitHub account:

- **Account**: sign in to Hosted Weblate, or sign up there first. Anyone signed in can
  translate.
- **Your language**: open the component openexpress and choose your language. If it is not
  listed, start a new translation of it there: Weblate makes its file from the template.
- **Suggestions**: "Save and continue" puts your translation in at once. "Suggest" leaves it for
  another translator of the language to accept, when you are unsure of a word or would like a
  correction checked first.
- **Comments and contexts**: Weblate shows them beside each text, and the rules of this page
  apply there too. "translator-credits" and the language name are texts like any other
  ([Credits and sending a translation](#credits-and-sending-a-translation)).
- **Sending**: Weblate sends the translations to this repository in a pull request, merged
  before each release: the tests are run then, and your name goes to `TRANSLATORS`. Weblate also
  brings each new text of the template into your language.
- **In a release**: as a rule a language goes into the program once at least 90% of its texts
  are translated; the project then adds it to `po/LINGUAS` (Weblate does not). French went in
  earlier, so that its translator sees the work in a release: its texts not translated yet
  stay in English.

## What not to translate

- Garmin, Garmin Connect, Garmin Express, Connect IQ, TrueUp, and the names of Garmin's
  features that Garmin leaves in English in your language (Body Battery, ClimbPro…).
- The names of devices and models: fēnix, Edge, Venu, Forerunner, Varia…
- The names of maps: TopoActive, CourseView…
- Codes and identifiers: commands and options (`openexpress sync --apply`), file names
  (`GarminExpress.exe`, `GarminDevice.xml`), paths, environment variables, `MTP`, `FIT`, `GPX`.

## Garmin's words

For Garmin's concepts, use the term Garmin Connect uses in your language. That covers
workouts, courses, segments, training status, HRV status, the
primary training device and the like. Garmin Connect on the web (connect.garmin.com, with your
account's language set to your language) comes first. Use its phone app only for a term the web
does not show.

The most frequent terms, with the Polish of openExpress (taken from Garmin Connect) as an
example:

| English | Where | Polish |
|---|---|---|
| workouts, courses, segments | data that goes to the device | treningi, kursy, segmenty |
| training status data, HRV status | data that goes to Garmin Connect | dane stanu wytrenowania, status HRV |
| Primary Training Device | the device page | Podstawowe urządzenie treningowe |
| Primary Wearable | the device page | Podstawowe urządzenie typu wearable |
| Heart Rate Zones | an activity's page | Strefy tętna |
| Training Effect, Training Load | an activity's page | Efekt treningu, Obciążenie wysiłkowe |
| Total Ascent | an activity's tiles | Całkowity wznios |
| watch face, data field | types of Connect IQ apps (use the Connect IQ Store's names) | tarcza zegarka, pole danych |

## Where the context is

- **Comments for translators**: the lines starting with `#.` in the `.po` file. Poedit and
  GNOME Translation Editor show them next to the text. They say where a text appears and what
  fills its placeholders.
- **Contexts** (`msgctxt`): the same English text in two roles is two entries, each with its
  context ("tab", "app type", "sport", "activity tile"…). Translate each one for its own
  role.
- **Capitalization**: English labels use GNOME's header capitalization ("Install All", "Device
  Not Ready"). That rule is for English only: write by the conventions of your language. Polish,
  for example, capitalizes only the first word everywhere and names a switch with a noun
  ("Synchronizacja w tle").

## Plurals, placeholders and punctuation

- **Plurals**: fill every plural form your language has. Weblate sets the `Plural-Forms` line
  of the header when it starts the language.
- **Placeholders** in braces (`{count}`, `{name}`, `{size}`, `{date}`…) stay exactly as they are.
  You may move them within the sentence. A few texts are date formats (`%Y-%m-%d`); their
  comment says so, and they must keep valid format codes.
- **Ellipsis**: the single character `…` (U+2026), wherever the English has it: a button that
  asks for more before it acts, or a task under way.
- **Hard spaces**: where your language's typography wants a hard space (after a one-letter
  word, before `; : ! ?`…), type it in your translation in Weblate: the typography of your
  language is yours. The command line prints plain spaces in their place. The project puts
  those of Polish itself.

## Trying a translation in the window

From [source](install.md#from-source), with GNU gettext installed, and your translation
downloaded from Weblate into `po/<language>.po`:

```bash
python3 -m openexpress.catalogs        # builds the catalogs of po/LINGUAS
python3 -m openexpress --lang de gui   # the window in that language
```

A language not yet in `po/LINGUAS` needs its code added there first, on your computer only (the
project adds it for a release, below).

`--lang` (or `OPENEXPRESS_LANG=de`) wins over the language chosen in the Preferences.
`LANGUAGE=de` works only while the Preferences say "System default". The window opens a
connected device as usual.

## Credits and sending a translation

- Translate "translator-credits" into your name, one translator per line: the About dialog
  shows it. Add your language and name to [TRANSLATORS](../TRANSLATORS) too.
- Translate "English" in the context "language name" into the name of your language in your
  language ("Deutsch", "Français"). The list of languages in the Preferences shows it.
- Weblate sends your translation; the project adds your language to `po/LINGUAS`, as a rule
  once at least 90% of its texts are translated, and your name to `TRANSLATORS`.
