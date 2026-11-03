#!/bin/sh
# Disposable CLI lifecycle. Requires jq for assertions, never Python.
# Invoke only against test binaries; no caller-supplied workspace is accepted.
set -eu
test "$(id -u)" != 0 || { echo 'requires non-root permission test' >&2; exit 1; }
cli=$1
scratch=$(mktemp -d "${TMPDIR:-/tmp}/revisionworx-lifecycle.XXXXXX")
cleanup() { chmod u+w "$scratch/Engineering Ω" 2>/dev/null || :; rm -rf "$scratch"; }
trap cleanup EXIT
trap 'exit 1' HUP INT TERM
export HOME="$scratch/profile" XDG_CONFIG_HOME="$scratch/config"
export XDG_CACHE_HOME="$scratch/cache" XDG_STATE_HOME="$scratch/state"
unset REVISIONWORX_WORKSPACE PYTHONPATH PYTHONHOME VIRTUAL_ENV
mkdir -p "$HOME" "$scratch/Engineering Ω"
workspace="$scratch/workspace"
run() { "$cli" --workspace "$workspace" --state-dir "$scratch/tokens" --cache-dir "$scratch/cache" --json "$@"; }
"$cli" --json init "$workspace" --name 'Offline acceptance' > "$scratch/init.json"
run root add engineering "$scratch/Engineering Ω" > "$scratch/root.json"
run project new --name 'Offline project Ω' --path 'Project with spaces Ω' --mode managed > "$scratch/project.json"
project=$(jq -er '.id | select(type == "string" and length > 0)' "$scratch/project.json")
file="$scratch/Engineering Ω/Project with spaces Ω/drawing Ω.txt"
printf 'original engineering fixture\n' > "$file"
original=$(sha256sum "$file" | cut -d ' ' -f1)
run doc register "$project" "$file" --title 'Disposable drawing' > "$scratch/doc.json"
doc=$(jq -er '.document.id | select(type == "string" and length > 0)' "$scratch/doc.json")
jq -e --arg digest "$original" '.revision.sha256 == $digest' "$scratch/doc.json" >/dev/null
# Each command is a fresh process. Checkout token must survive process restart.
run doc checkout "$doc" > "$scratch/checkout.json"
printf 'revised engineering fixture\n' > "$file"
revised=$(sha256sum "$file" | cut -d ' ' -f1)
run doc checkin "$doc" -m 'Offline revision after process restart' > "$scratch/revision.json"
jq -e --arg digest "$revised" '.sha256 == $digest' "$scratch/revision.json" >/dev/null
run doc show "$doc" --hash > "$scratch/reopened.json"
jq -e --arg digest "$revised" '.checkout == null and (.history | length == 2) and (.history | any(.sha256 == $digest))' "$scratch/reopened.json" >/dev/null
first_revision=$(jq -er '.revision.id' "$scratch/doc.json")
run rev export "$first_revision" "$scratch/original-export.txt" > "$scratch/export.json"
test "$(sha256sum "$scratch/original-export.txt" | cut -d ' ' -f1)" = "$original"
run doc verify --id "$doc" > "$scratch/verified.json"
jq -e '. == []' "$scratch/verified.json" >/dev/null
test "$(sha256sum "$file" | cut -d ' ' -f1)" = "$revised"
# Read-only parent denies project creation at the OS boundary (non-root Linux).
chmod a-w "$scratch/Engineering Ω"
if run project new --name Denied --path 'Must not exist' > "$scratch/denied.log" 2>&1; then
    chmod u+w "$scratch/Engineering Ω"
    echo 'permission-denied project creation unexpectedly succeeded' >&2
    exit 1
fi
chmod u+w "$scratch/Engineering Ω"
test ! -e "$scratch/Engineering Ω/Must not exist"
run project list > "$scratch/projects.json"
jq -e 'all(.[]; .name != "Denied")' "$scratch/projects.json" >/dev/null
run doc verify --id "$doc" > "$scratch/after-denial.json"
jq -e '. == []' "$scratch/after-denial.json" >/dev/null
echo 'OFFLINE_LIFECYCLE_EVIDENCE project=create document=register revisions=2 checkout=restart checkin=verified reopen=verified permission_denial=verified'
echo 'LIMITATION: process restarts are not crash injection or interactive GUI acceptance.'
