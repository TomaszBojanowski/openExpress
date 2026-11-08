#!/usr/bin/env bash
# Every language of po/LINGUAS merged with the template (msgmerge; texts no code uses any more
# dropped), then the compiled catalogs and the translated metainfo built for running from
# source (msgfmt; they are never committed). The template itself: tools/update-template.sh.
#
#   tools/update-translations.sh          merge and build every language
#   tools/update-translations.sh de       also start a new language (here: German)
set -euo pipefail
cd "$(dirname "$0")/.."

POT=po/openexpress.pot

for lang in "$@"; do
    if [[ ! -f po/$lang.po ]]; then
        msginit --no-translator --locale="$lang" --input="$POT" --output-file="po/$lang.po"
    fi
    grep -qw "$lang" po/LINGUAS || echo "$lang" >> po/LINGUAS
done

for lang in $(sed 's/#.*//' po/LINGUAS); do
    po=po/$lang.po
    # Written always, with the template's POT-Creation-Date in the header, as Weblate's msgmerge
    # add-on writes it: --update leaves a file whose only difference is that date, and Weblate
    # would open a pull request of that one line.
    # The references as the template has them: files without lines (update-template.sh).
    msgmerge --quiet --add-location=file --output-file="$po" "$po" "$POT"
    # Texts no code uses any more are dropped, not kept as "#~" (msgmerge has no option for it)
    msgattrib --no-obsolete --output-file="$po" "$po"
    # The flags of Weblate (po/weblate-flags.txt), which msgmerge and msgattrib drop
    python3 -m openexpress.catalogs --flags "$po"
    echo "$lang: $(msgfmt --statistics --output-file=/dev/null "$po" 2>&1)"
done
python3 -m openexpress.catalogs
