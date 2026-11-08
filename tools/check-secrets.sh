#!/usr/bin/env bash
# Stops a release if Garmin keys or personal identifiers would be published.
# The patterns are kept in a local, untracked file (one regex per line), because
# they are themselves secret: tools/secret-patterns.txt
# Checks the commit given (the pre-push hook gives the one pushed; default HEAD): its files
# and its history. A find is told by where it is only – file and line, or commit – never by
# the line or the commit's message, which would print the secret.
set -euo pipefail
cd "$(dirname "$0")/.."
patterns=tools/secret-patterns.txt
if [[ ! -f $patterns ]]; then
    echo "Missing $patterns – cannot check for secrets." >&2
    exit 2
fi
regex=$(grep -v '^\s*#' "$patterns" | grep -v '^\s*$' | paste -sd'|')
commit=${1:-HEAD}
in_files=$(git grep -z -n -I -i -E "$regex" "$commit" -- . \
    | awk -F'\0' -v rev="$commit:" 'index($1, rev) == 1 { print substr($1, length(rev) + 1) ":" $2 }' \
    || true)
in_history=$(git log --format=%h -i -E -G "$regex" "$commit" -- . ':!*.png' ':!*.mo')
if [[ -n $in_files || -n $in_history ]]; then
    [[ -n $in_files ]] && printf '%s\n' "$in_files"
    [[ -n $in_history ]] && printf 'commit %s\n' $in_history
    echo "STOP: secrets or personal identifiers found (see above). Nothing was published." >&2
    exit 1
fi
echo "No secrets found."
