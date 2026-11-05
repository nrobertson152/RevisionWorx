#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-or-later
# Add RevisionWorx to your desktop's application menu (for this user only; no root needed):
#   ./install-desktop-entry.sh            install or update (run it again after moving this folder)
#   ./install-desktop-entry.sh --remove   take it out again
# Writes ~/.local/share/applications/revisionworx.desktop and the icon in ~/.local/share/icons/hicolor.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
data=${XDG_DATA_HOME:-"$HOME/.local/share"}
apps="$data/applications"
icons="$data/icons/hicolor"
sizes="16 24 32 48 64 128 256 512"
if [ "${1:-}" = "--remove" ]; then
    rm -f "$apps/revisionworx.desktop"
    for s in $sizes; do rm -f "$icons/${s}x${s}/apps/revisionworx.png"; done
    echo "Removed the RevisionWorx menu entry."
    exit 0
fi
[ -x "$here/revisionworx-gui" ] || { echo "revisionworx-gui not found next to this script" >&2; exit 1; }
for s in $sizes; do
    mkdir -p "$icons/${s}x${s}/apps"
    cp "$here/icons/revisionworx-$s.png" "$icons/${s}x${s}/apps/revisionworx.png"
done
mkdir -p "$apps"
sed "s|@EXEC@|$here/revisionworx-gui|" "$here/revisionworx.desktop.in" > "$apps/revisionworx.desktop"
chmod 0644 "$apps/revisionworx.desktop"
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$apps" >/dev/null 2>&1 || true
command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -q -t "$icons" >/dev/null 2>&1 || true
echo "RevisionWorx is in your application menu ($apps/revisionworx.desktop)."
