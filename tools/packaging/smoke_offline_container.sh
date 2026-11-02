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
mkdir -p /tmp/config /tmp/cache /tmp/runtime
chmod 700 /tmp/runtime
/bundles/RevisionWorxCLI/RevisionWorxCLI --help >/tmp/cli.log 2>&1 || {
  cat /tmp/cli.log >&2
  exit 1
}
/bundles/RevisionWorx/RevisionWorx >/tmp/gui.log 2>&1 &
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
echo "OFFLINE_CONTAINER_EVIDENCE network=none python=absent cli_help=pass gui_offscreen_start=pass"
echo "LIMITATION: no socket-attempt tracing; not a native interactive GUI, installer, or Windows offline VM."
