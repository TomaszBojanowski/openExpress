#!/usr/bin/env bash
# Extracts translatable strings, updates every .po file and compiles .mo files.
#
#   tools/update-translations.sh          update the template and all languages
#   tools/update-translations.sh de       also start a new language (here: German)
set -euo pipefail
cd "$(dirname "$0")/.."

DOMAIN=openexpress
LOCALE=openexpress/locale
POT=po/$DOMAIN.pot
VERSION=$(python3 -c "import openexpress; print(openexpress.__version__)")

mkdir -p po
find openexpress -name '*.py' | sort | xargs xgettext \
    --language=Python --from-code=UTF-8 \
    --keyword=_ --keyword=N_ --keyword=ngettext:1,2 --keyword=pgettext:1c,2 \
    --add-comments=TRANSLATORS \
    --package-name=openexpress --package-version="$VERSION" \
    --output="$POT"

if [[ $# -gt 0 ]]; then
    for lang in "$@"; do
        if [[ ! -f po/$lang.po ]]; then
            msginit --no-translator --locale="$lang" --input="$POT" --output-file="po/$lang.po"
        fi
    done
fi

for po in po/*.po; do
    lang=$(basename "$po" .po)
    msgmerge --quiet --update --backup=none "$po" "$POT"
    # Texts no code uses any more are dropped, not kept as "#~" (msgmerge has no option for it)
    msgattrib --no-obsolete --output-file="$po" "$po"
    mkdir -p "$LOCALE/$lang/LC_MESSAGES"
    msgfmt --check --output-file="$LOCALE/$lang/LC_MESSAGES/$DOMAIN.mo" "$po"
    echo "$lang: $(msgfmt --statistics --output-file=/dev/null "$po" 2>&1)"
done
