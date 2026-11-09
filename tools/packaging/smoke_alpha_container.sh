#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-or-later
# Test-only frozen alpha GUI acceptance in Python-free network-isolated Linux.
# The only writable workspace is disposable tmpfs. No network calls required.
set -eu
for command in python python3 pip; do
    if command -v "$command" >/dev/null 2>&1; then
        echo "Unexpected Python/pip in offline container" >&2
        exit 1
    fi
done
test -x /bundles/RevisionWorx/RevisionWorx
test -x /bundles/RevisionWorxCLI/RevisionWorxCLI

root=$(mktemp -d /tmp/rw-alpha-fixture-XXXXXXXX)
install="$root/Installed alpha Ω with spaces"
mkdir -p "$install/gui" "$install/cli" "$root/profile" /tmp/runtime
chmod 700 /tmp/runtime
cp -R /bundles/RevisionWorx/. "$install/gui/"
cp -R /bundles/RevisionWorxCLI/. "$install/cli/"
gui="$install/gui/RevisionWorx"
cli="$install/cli/RevisionWorxCLI"
test -x "$gui"
test -x "$cli"
ws="$root/Projects with spaces Ω"

if ! "$gui" --alpha-probe create "$root" "$root/create.json"; then
    cat "$root/create.json" >&2 || :
    echo "Frozen alpha Qt create/import/checkout/checkin failed" >&2
    exit 1
fi
test -d "$ws/.revisionworx"
doc=$(jq -er '.document_id | select(type=="string" and length>0)' "$root/create.json")
jq -e '.gui_import=="passed" and .gui_checkout_checkin=="passed" and .history_rows==2' "$root/create.json" >/dev/null

"$cli" --workspace "$ws" --state-dir "$root/profile/state" \
    --cache-dir "$root/profile/cache" --json doc show "$doc" --hash >"$root/cli-history.json"
jq -e '(.history | length)==2 and .checkout==null' "$root/cli-history.json" >/dev/null
for i in 0 1; do
    id=$(jq -er ".history[$i].id" "$root/cli-history.json")
    wanted=$(jq -er ".history[$i].sha256" "$root/cli-history.json")
    "$cli" --workspace "$ws" --state-dir "$root/profile/state" \
        --cache-dir "$root/profile/cache" --json rev export "$id" "$root/export-$i" >/dev/null
    got=$(sha256sum "$root/export-$i" | cut -d ' ' -f1)
    test "$got" = "$wanted"
done

# Fresh GUI process must rediscover the committed history without altering the
# journal files. These are command process restarts, not unexpected kill tests.
before=$(find "$ws/.revisionworx" -type f -print0 | sort -z | xargs -0 sha256sum | sha256sum)
if ! "$gui" --alpha-probe reopen "$root" "$root/reopen.json"; then
    cat "$root/reopen.json" >&2 || :
    echo "Frozen alpha Qt restart/reopen failed" >&2
    exit 1
fi
jq -e '.history_rows==2 and .gui_reopen=="passed"' "$root/reopen.json" >/dev/null
test "$(jq -c '.revision_ids | sort' "$root/create.json")" = \
     "$(jq -c '.revision_ids | sort' "$root/reopen.json")"
after=$(find "$ws/.revisionworx" -type f -print0 | sort -z | xargs -0 sha256sum | sha256sum)
test "$before" = "$after"

# Normal startup path, not only test mode, must survive offline Qt startup.
"$gui" "$ws" >"$root/normal-gui.log" 2>&1 &
pid=$!
sleep 3
if ! kill -0 "$pid" 2>/dev/null; then
    cat "$root/normal-gui.log" >&2
    wait "$pid" || :
    echo "Normal packaged alpha GUI exited during offline startup" >&2
    exit 1
fi
kill "$pid" 2>/dev/null || :
wait "$pid" || :
echo "OFFLINE_ALPHA_GUI_EVIDENCE python=absent network=none copied_bundle=pass qt_create_import_checkout_checkin=pass qt_reopen=pass cli_sha_exports=pass normal_gui_start=pass"
echo "LIMITATION: Qt widget automation under offscreen; NOT user-visible interactive testing, Windows disconnected VM, native installer, or core safety certification."
