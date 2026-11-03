#!/bin/sh
# Executed INSIDE experimental Ubuntu image: --network none, no Python/pip.
# This proves startup without network reachability, NOT that no socket was attempted.
set -eu
if command -v python >/dev/null 2>&1 || command -v python3 >/dev/null 2>&1 ||
   command -v pip >/dev/null 2>&1; then
  echo "Offline isolation failed: Python or pip exists in runtime" >&2
  exit 1
fi
test -x /bundles/RevisionWorx/RevisionWorx
test -x /bundles/RevisionWorxCLI/RevisionWorxCLI
# Copy the shipped onedir trees to paths containing spaces and Unicode, then
# execute only the relocated copies. This catches dependencies on build-tree
# paths. Both original bind mounts are read-only; all writes stay in /tmp.
mkdir -p '/tmp/installed copy Ω/gui' '/tmp/installed copy Ω/cli'
cp -R /bundles/RevisionWorx/. '/tmp/installed copy Ω/gui/'
cp -R /bundles/RevisionWorxCLI/. '/tmp/installed copy Ω/cli/'
test -x '/tmp/installed copy Ω/gui/RevisionWorx'
test -x '/tmp/installed copy Ω/cli/RevisionWorxCLI'
/bin/sh /harness/offline_lifecycle.sh '/tmp/installed copy Ω/cli/RevisionWorxCLI'
mkdir -p /tmp/config /tmp/cache /tmp/runtime
chmod 700 /tmp/runtime
"/tmp/installed copy Ω/cli/RevisionWorxCLI" --help >/tmp/cli.log 2>&1 || {
  cat /tmp/cli.log >&2
  exit 1
}
"/tmp/installed copy Ω/gui/RevisionWorx" >/tmp/gui.log 2>&1 &
gui_pid=$!
sleep 4
if ! kill -0 "$gui_pid" 2>/dev/null; then
  cat /tmp/gui.log >&2
  wait "$gui_pid" || :
  echo "Frozen Qt GUI terminated inside offline container" >&2
  exit 1
fi
kill "$gui_pid" 2>/dev/null || :
wait "$gui_pid" || :
echo "OFFLINE_CONTAINER_EVIDENCE network=none python=absent relocated_bundle=pass cli_help=pass gui_offscreen_start=pass"
echo "LIMITATION: no socket-attempt tracing; not a native interactive GUI, installer, or Windows offline VM."
