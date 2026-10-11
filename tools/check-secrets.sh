#!/usr/bin/env bash
# Stops a release if Garmin keys or personal identifiers would be published.
# The patterns are kept in a local, untracked file (one regex per line), because
# they are themselves secret: tools/secret-patterns.txt
set -euo pipefail
cd "$(dirname "$0")/.."
patterns=tools/secret-patterns.txt
if [[ ! -f $patterns ]]; then
    echo "Missing $patterns – cannot check for secrets." >&2
    exit 2
fi
regex=$(grep -v '^\s*#' "$patterns" | grep -v '^\s*$' | paste -sd'|')
found=0
if git grep -n -I -i -E "$regex" -- . ; then found=1; fi
if git log -p -i -E -G "$regex" --format="commit %h %s" "${1:-HEAD}" -- . ':!*.png' ':!*.mo' | grep -i -E "$regex"; then found=1; fi
if [[ $found == 1 ]]; then
    echo "STOP: secrets or personal identifiers found (see above). Nothing was published." >&2
    exit 1
fi
echo "No secrets found."
