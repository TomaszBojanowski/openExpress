#!/usr/bin/env bash
# Stops a release if Garmin keys or personal identifiers would be published.
# The patterns are kept in a local, untracked file (one regex per line), because
# they are themselves secret: tools/secret-patterns.txt
# Checks the commit given (the pre-push hook gives the one pushed; default HEAD): its files
# and its history. A find is told by where it is only – file and line, or commit – never by
# the line or the commit's message, which would print the secret.
# With --path PATH: the files of a folder or one file instead (a Flatpak build, a bundle),
# binary ones too; a find is told by the file's name only.
set -euo pipefail
path=
if [[ ${1:-} == --path ]]; then
    path=$(realpath -- "${2:?--path needs a folder or a file}")
fi
cd "$(dirname "$0")/.."
patterns=tools/secret-patterns.txt
if [[ ! -f $patterns ]]; then
    echo "Missing $patterns – cannot check for secrets." >&2
    exit 2
fi
regex=$(grep -v '^\s*#' "$patterns" | grep -v '^\s*$' | paste -sd'|')
if [[ -n $path ]]; then
    found=$(grep -r -l -a -i -E -- "$regex" "$path" || true)
    if [[ -n $found ]]; then
        printf '%s\n' "$found"
        echo "STOP: secrets or personal identifiers found in the files above." >&2
        exit 1
    fi
    echo "No secrets found ($(find "$path" -type f | wc -l) files)."
    exit 0
fi
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
