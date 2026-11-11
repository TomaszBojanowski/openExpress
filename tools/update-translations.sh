#!/usr/bin/env bash
# Polish, the maintainer's translation, merged with the template (msgmerge; texts no code uses any
# more dropped), then the compiled catalogs of every language of po/LINGUAS and the translated
# metainfo built for running from source (msgfmt; they are never committed). The template itself:
# tools/update-template.sh.
#
# Every other language is its translators', in Weblate: this script never changes its file.
# Weblate's msgmerge add-on keeps it in line with the template, and a language is added there.
#
#   tools/update-translations.sh
set -euo pipefail
cd "$(dirname "$0")/.."

POT=po/openexpress.pot
po=po/pl.po
# Written always, with the template's POT-Creation-Date in the header.
# The references as the template has them: files without lines (update-template.sh).
msgmerge --quiet --add-location=file --output-file="$po" "$po" "$POT"
# Texts no code uses any more are dropped, not kept as "#~" (msgmerge has no option for it)
msgattrib --no-obsolete --output-file="$po" "$po"
# The flags of Weblate (po/weblate-flags.txt), which msgmerge and msgattrib drop
python3 -m openexpress.catalogs --flags "$po"
echo "pl: $(msgfmt --statistics --output-file=/dev/null "$po" 2>&1)"
python3 -m openexpress.catalogs
