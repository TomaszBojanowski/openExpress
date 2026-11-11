# Design: opening openExpress to translators

Status: implemented, with the recommended option of each decision of section 4; openExpress is
translated on Hosted Weblate.

## 1. Before this change

- GNU gettext: `po/openexpress.pot` (about 1,900 texts, 257 of them with a `TRANSLATORS` comment,
  contexts by `pgettext`, plurals by `ngettext`), `po/pl.po`, and the compiled
  `openexpress/locale/pl/LC_MESSAGES/openexpress.mo`, which was kept in the repository.
- `tools/update-translations.sh` did it all: `xgettext` over the code, `msgmerge` and `msgattrib
  --no-obsolete` on every `.po`, `msgfmt --check` into `.mo`; `tools/po-hard-spaces.py` put hard
  spaces after Polish one-letter words; `tools/po-fill.py` fills texts in bulk.
- A new language needed a line in `LANGUAGE_NAMES` (`openexpress/i18n.py`).
- The metainfo carried its Polish inline (`xml:lang`, 19 elements); the `.desktop` entry is
  written by `openexpress/desktop.py` from the catalogs.
- `tests/test_i18n.py` checked placeholders, plural forms, the compiled catalogs and Polish hard
  spaces.
- The project pushes to GitHub only at a release, then as now: translators see new texts only
  then (D3).

## 2. Hosted Weblate or pull requests

| | Hosted Weblate (Libre plan) | Pull requests with `.po` files |
|---|---|---|
| Work at each release | merge one pull request of Weblate; compile and test | `msgmerge` and `msgfmt --check` for each pull request; review of languages one cannot read; conflicts in `.po` and in the compiled `.mo` |
| Setting up | the GitHub App, a gettext component, the `msgmerge` add-on, an application for the Libre plan (a trial meanwhile; approval takes days to weeks) | a section in CONTRIBUTING |
| For translators | a web editor, the glossary, suggestions and votes, no git | git, GitHub, Poedit |
| Risks | the approval queue; Weblate writes to the repository (a fork and pull requests only, D2) | stale pull requests; every pull request waits for its review in the project |

The Libre plan is free for public projects under a free licence, with the limits of the 160k
plan (strings counted in all languages, the source included: room for about 80 languages of
openExpress). Codeberg Translate expects a Codeberg repository and its terms exclude projects
mostly written with generative AI tools; Fedora's Weblate and GNOME's Damned Lies are for
software of their own projects; Crowdin and Transifex are proprietary services.

Less work at each release: Hosted Weblate (D1).

## 3. What to prepare in the repository

1. **The template and its refresh.** The template on its own (`tools/update-template.sh`):
   `xgettext` writes the `.pot`, committed with the code that changes the texts. The `msgmerge`
   of the languages runs in the repository (`tools/update-translations.sh`); Weblate's msgmerge
   add-on is not used, as both doing it makes conflicts. Polish keeps its rule (a new text and its Polish in one commit): Polish
   is edited in the repository, and Weblate's pending changes of Polish are merged first (D5).
2. **`po/LINGUAS`**, kept by the project, not by Weblate's add-on: a language is added by hand
   once it translates at least 90% of the template of a release, together with its
   `po/<language>.po` (a test holds that the two agree and that Polish is listed). The add-on
   would list every language Weblate has, also one just started, and none that Weblate leaves
   out, such as Polish (D5). The languages are read from it, not from `LANGUAGE_NAMES`.
3. **The name of a language in that language**: a text `pgettext("language name", "English")`
   each language translates into its own name ("Polski", "Deutsch"), instead of a line of code
   per language.
4. **The metainfo through gettext**: a template `….metainfo.xml.in` (summary, description, the
   screenshots' captions; `<releases>` marked `translate="no"`, so that translators are not
   asked to translate each release's notes), read by `xgettext` with AppStream's ITS rules into
   the same `.pot`, merged at the build by `msgfmt --xml --template … -d po` (bulk mode, reads
   `LINGUAS`). The `.desktop` entry stays written from the catalogs by `desktop.py`. The
   Flatpak's manifest and the build from source both run the merge.
5. **The compiled catalogs** built at the build (`python3 -m openexpress.catalogs`: the
   Flatpak's manifest, `install-desktop` from source, the tests), no longer kept in the
   repository (D4).
6. **`TRANSLATORS` comments** for every text that is ambiguous alone (a short label, a chip, a
   unit, a text with placeholders whose meaning is not in the text): a pass over the `.pot`
   for texts of up to three words without a comment or a context.
7. **Hard spaces in other languages**: `po-hard-spaces.py` with rules per language – Polish,
   Czech and Slovak (their one-letter prepositions and conjunctions), French (the narrow
   no-break space before `; : ! ?` and inside « »); run at a release for the languages that
   have rules; the test for every language with rules.
8. **The tests** for any language: placeholders and plural forms checked in every `.po`;
   untranslated texts allowed (English shows); the completeness and hard spaces checked for
   Polish only.
9. **The glossary** (section 5) and the help for translators (the words of Garmin Connect, the
   hard spaces, the placeholders, what not to translate: Garmin, Connect IQ, TrueUp, Body
   Battery and other names of Garmin).

## 4. Decisions

### D1: where translators work — recommended: 1a

- **1a. Hosted Weblate, Libre plan (recommended).** + One pull request per release; a glossary;
  no git for translators. − An application and its wait; another service.
- **1b. Pull requests with `.po` files only.** + Nothing new. − Each pull request needs its own
  merge, checks and review in the project.

### D2: how Weblate writes — recommended: 2a

- **2a. Pull requests from Weblate's fork (the GitHub App with an empty "push branch"),
  merged locally with a signed merge commit, then the usual checks and push at the release
  (recommended).** + Weblate never writes to `main`; the release's checks run over the
  translations too.
- **2b. Merged on GitHub.** − Unsigned merges, and the checks after the fact.

### D3: when translators see new texts — recommended: 3a

- **3a. At the release, as everything is pushed; a text added in a release is translated for
  the next one (until then English shows) (recommended).** + No change to how releases go.
- **3b. A freeze before a release: the `.pot` pushed to a branch some days ahead, an
  announcement in Weblate.** − Each release waits for it.

### D4: the compiled catalogs — recommended: 4a

- **4a. Built at the build, not kept in the repository (recommended).** + No binary file in
  Weblate's pull requests; no conflicts. − The build from source needs `msgfmt` (gettext).
- **4b. Kept, compiled at each release.** − Each merge also rebuilds a binary.

### D5: Polish — recommended: 5a

- **5a. Polish stays in the repository, written with the code, with its rule; in Weblate it is
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
  can be; to be decided for each language on its own.

## 5. Garmin Connect's words for translators

Garmin Connect's words matter most on the activity page (summary tiles, details, charts and
their axes, laps, heart rate zones, training effect, pace and speed, elevation, temperature,
cadence, steps, strength sets) and for sport names. Garmin Connect on the web
(connect.garmin.com) comes first; its app for phones only for a word the web does not show.
A table of openExpress's English and Polish against Garmin Connect's English and Polish, terms
and short labels only, is kept outside the repository, with a proposal for each difference; its
English column is the glossary of 6a.

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

- README, a section **Help translate** after Support: Weblate's badge, where to translate (in
  the browser on Hosted Weblate, with no GitHub account, or a `.po` file in a pull request) and
  a link to the page for translators, `docs/translating.md`.
- `docs/translating.md` holds the help of section 3, item 9: Weblate and the `.po` route side by
  side. CONTRIBUTING, "Adding a translation", links to it.
- The website: the same line and badge in its footer, and "help translate" after the languages
  in the features.

## 7. The work, and each release

1. The repository: section 3, items 1–8. It went out in a release of its own before Weblate was
   asked: the metainfo and the catalogs change how the Flatpak is built, and that was checked
   once on its own.
2. The glossary (6a): the English terms with explanations, a CSV for Weblate.
3. Hosted Weblate: an account, the GitHub App (fork mode), the project and its gettext component
   (`po/*.po`, the template), the glossary, the application for
   the Libre plan.
4. README, CONTRIBUTING, the website (section 6), published with a release.
5. Each release: Weblate's pull request fetched, merged locally (signed merge commit), the
   checks, the hard-space tool; the release as usual.
