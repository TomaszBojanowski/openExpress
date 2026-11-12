#!/usr/bin/env bash
# The template of the texts: po/POTFILES (the program's files with texts and the metainfo's
# template) and po/openexpress.pot made from them (xgettext; the metainfo by AppStream's
# rules; each text by the files that use it, without lines). The maintainer's step, committed
# with the code that changes the texts. Then:
#   tools/update-translations.sh     merges Polish with it (every other language: Weblate)
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m openexpress.catalogs --template
