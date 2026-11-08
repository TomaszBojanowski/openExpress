# Design: opening openExpress to translators

Status: accepted with the recommended option of each decision of section 4. Of section 3, items
1–5, 7 and 8 are in the code (the template and its merge apart, `po/LINGUAS` and `po/POTFILES`,
the language names, the metainfo through gettext, the catalogs built at the build, hard spaces
per language, the tests); item 6 (the pass over the `TRANSLATORS` comments) comes next.

## 1. Where translations stand

- GNU gettext: `po/openexpress.pot` (about 1,900 texts, 257 of them with a `TRANSLATORS` comment,
  contexts by `pgettext`, plurals by `ngettext`), `po/pl.po`, the compiled
  `openexpress/locale/pl/LC_MESSAGES/openexpress.mo` kept in the repository.
- `tools/update-translations.sh`: `xgettext` over the code, `msgmerge` and `msgattrib
  --no-obsolete` on every `.po`, `msgfmt --check` into `.mo`; `tools/po-hard-spaces.py` puts hard
  spaces after Polish one-letter words; `tools/po-fill.py` fills texts in bulk.
- A new language today needs a line in `LANGUAGE_NAMES` (`openexpress/i18n.py`).
- The metainfo carries its Polish inline (`xml:lang`, 19 elements); the `.desktop` entry is
  written by `openexpress/desktop.py` from the catalogs.
- `tests/test_i18n.py` checks placeholders, plural forms, the compiled catalogs and Polish hard
  spaces.
- The project pushes to GitHub only at a release: translators see new texts only then.

## 2. Hosted Weblate or pull requests

| | Hosted Weblate (Libre plan) | Pull requests with `.po` files |
|---|---|---|
| The maintainer's work at a release | merge one pull request of Weblate; compile and test | `msgmerge` and `msgfmt --check` for each pull request; review of languages one cannot read; conflicts in `.po` and in the compiled `.mo` |
| Setting up | the GitHub App, a gettext component, the `msgmerge` add-on (and `LINGUAS`), an application for the Libre plan (a trial meanwhile; approval takes days to weeks) | a section in CONTRIBUTING |
| For translators | a web editor, the glossary, suggestions and votes, no git | git, GitHub, Poedit |
| Risks | the approval queue; Weblate writes to the repository (a fork and pull requests only, D2) | stale pull requests; the maintainer as a bottleneck |

The Libre plan is free for public projects under a free licence, with the limits of the 160k
plan (strings counted in all languages, the source included: room for about 80 languages of
openExpress). Codeberg Translate expects a Codeberg repository and its terms exclude projects
mostly written with generative AI tools; Fedora's Weblate and GNOME's Damned Lies are for
software of their own projects; Crowdin and Transifex are proprietary services.

**Less work at each release: Hosted Weblate** (D1).

## 3. What to prepare in the repository

1. **The template and its refresh.** `update-translations.sh` split: `xgettext` writes the `.pot`
   (the maintainer's step, committed with the code as today), the `msgmerge` of the languages
   is Weblate's (its add-on, after each pull of the repository) – both doing it makes
   conflicts. Polish keeps its rule (a new text and its Polish in one commit): Polish is
   edited in the repository, and Weblate's pending changes of Polish are merged first (D5).
2. **`po/LINGUAS`**, kept by Weblate's add-on; the languages read from it, not from
   `LANGUAGE_NAMES`.
3. **The name of a language in that language**: a text `pgettext("language name", "English")`
   each language translates into its own name ("Polski", "Deutsch"), instead of a line of code
   per language.
4. **The metainfo and the `.desktop` entry through gettext**: a template
   `….metainfo.xml.in` (summary, description, the screenshots' captions; `<releases>` marked
   `translate="no"`, so that translators are not asked to translate each release's notes),
   read by `xgettext` with AppStream's ITS rules into the same `.pot`, merged at the build by
   `msgfmt --xml --template … -d po` (bulk mode, reads `LINGUAS`); the `.desktop` the same way
   (`msgfmt --desktop`) or, as today, from the catalogs by `desktop.py`. The Flatpak's manifest
   and the build from source both run the merge.
5. **The compiled catalogs** built at the build (the manifest, `pyproject` build step), no
   longer kept in the repository (D4).
6. **`TRANSLATORS` comments** for every text that is ambiguous alone (a short label, a chip, a
   unit, a text with placeholders whose meaning is not in the text): a pass over the `.pot`
   for texts of up to three words without a comment or a context.
7. **Hard spaces in other languages**: `po-hard-spaces.py` with rules per language – Polish
   (today), Czech and Slovak (their one-letter prepositions and conjunctions), French (the
   narrow no-break space before `; : ! ?` and inside « »); run at a release for the languages
   that have rules; the test for every language with rules.
8. **The tests** for any language: placeholders and plural forms checked in every `.po`;
   untranslated texts allowed (English shows); the completeness and hard spaces checked for
   Polish only.
9. **The glossary** (section 5) and the help for translators (CONTRIBUTING: the words of Garmin
   Connect, the hard spaces, the placeholders, what not to translate: Garmin, Connect IQ,
   TrueUp, Body Battery and other names of Garmin).

## 4. Decisions

### D1: where translators work — recommended: 1a

- **1a. Hosted Weblate, Libre plan (recommended).** + One pull request per release; a glossary;
  no git for translators. − An application and its wait; another service.
- **1b. Pull requests with `.po` files only.** + Nothing new. − The work of each pull request is
  the maintainer's.

### D2: how Weblate writes — recommended: 2a

- **2a. Pull requests from Weblate's fork (the GitHub App with an empty "push branch"),
  merged by the maintainer locally with a signed merge commit, then the usual checks and push
  at the release (recommended).** + Weblate never writes to `main`; the release's checks run
  over the translations too.
- **2b. Merged on GitHub.** − Unsigned merges, and the checks after the fact.

### D3: when translators see new texts — recommended: 3a

- **3a. At the release, as everything is pushed; a text added in a release is translated for
  the next one (until then English shows) (recommended).** + No change to how releases go.
- **3b. A freeze before a release: the `.pot` pushed to a branch some days ahead, an
  announcement in Weblate.** − Each release waits for it.

### D4: the compiled catalogs — recommended: 4a

- **4a. Built at the build, not kept in the repository (recommended).** + No binary file in
  Weblate's pull requests; no conflicts. − The build from source needs `msgfmt` (gettext).
- **4b. Kept, compiled by the maintainer at each release.** − Each merge also rebuilds a binary.

### D5: Polish — recommended: 5a

- **5a. Polish stays the maintainer's, in the repository, with its rule; in Weblate it is
  visible, its suggestions taken by hand (recommended).** + The one language with a complete
  translation and rules keeps them.
- **5b. Polish in Weblate as any language.** − New texts need their Polish in the same commit;
  with Weblate, that commit and Weblate's would collide.

### D6: the glossary of Garmin's words — recommended: 6a

- **6a. English terms only, each with an explanation ("the metric's name as Garmin Connect
  shows it") and Garmin's names marked untranslatable; translators take the word Garmin
  Connect uses in their language (recommended).** + No text of Garmin's is published.
  − Each translator looks the words up.
- **6b. Prefilled per language with Garmin Connect's own short terms** (section 5). + The
  translator only checks. − Garmin's translations of a few hundred terms, published: short names
  are not protected by copyright in the US; in the EU a substantial extraction from a database
  can be; the maintainer's decision, for languages one by one.

## 5. Garmin Connect's words for translators

Garmin Connect's words matter most on the activity page (summary tiles, details, charts and
their axes, laps, heart rate zones, training effect, pace and speed, elevation, temperature,
cadence, steps, strength sets) and for sport names. Garmin Connect on the web
(connect.garmin.com) comes first; its app for phones only for a word the web does not show.
A table of openExpress's
English and Polish against Garmin Connect's English and Polish, terms and short labels only,
is kept outside the repository, with a proposal for each difference; its English column is the
glossary of 6a.

Where Garmin Connect's words in another language come from (for 6b, or to check a translator's
term):

- **Garmin Connect on the web**, with the account's language set to that language: the activity
  page, the lists and the settings, read on the screen.
- **The Garmin Connect app on a phone**, with the phone's language set to that language: a word
  the web does not show, read on the screen.
- **Garmin Express** for Windows and macOS carries its own texts in its languages: few terms of
  activities.

Short terms only, never sentences, and nothing of Garmin's code.

## 6. README and the website

- README, a section **Help translate** after Support: "openExpress is translated on Hosted
  Weblate: [the project's page](https://hosted.weblate.org/engage/openexpress/). No account on
  GitHub is needed. Translations are welcome as pull requests with a `.po` file too (see
  Contributing)." With the badge `https://hosted.weblate.org/widget/openexpress/svg-badge.svg`.
- CONTRIBUTING, "Adding a translation": Weblate first; the `.po` route kept; the rules of
  section 3, item 9.
- The website: the same line and badge in its footer, and "English and Polish" in the features
  becomes "English, Polish – help translate".

## 7. Steps and the work

| Step | Who | Work |
|---|---|---|
| 1. The repository (section 3, items 1–8): template split, `LINGUAS`, language names, metainfo and `.desktop` through gettext, catalogs at the build, the `TRANSLATORS` pass, hard spaces per language, tests | the agent, the maintainer reviews | 2–3 days; 5–6 commits |
| 2. The glossary (6a): the English terms with explanations, a CSV for Weblate | the agent | 2–3 hours |
| 3. Hosted Weblate: an account, the GitHub App (fork mode), the project and its gettext component (`po/*.po`, the template), add-ons (`msgmerge`, `LINGUAS`), the glossary, the application for the Libre plan | the maintainer | 1–2 hours, then a wait of days to weeks |
| 4. README, CONTRIBUTING, the website (section 6) | the agent; published with a release | 1 hour |
| 5. Each release: Weblate's pull request fetched, merged locally (signed merge commit), the checks, the hard-space tool; the release as usual | the maintainer with the agent | 15–30 minutes |

Step 1 lands in a release of its own before Weblate is asked: the metainfo and the catalogs
change how the Flatpak is built, and that is checked once on its own.
