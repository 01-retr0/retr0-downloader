#!/usr/bin/env bash
# Adds Retr0 Downloader to your Linux app menu.
# Usage: ./install_linux.sh [path/to/Retr0Downloader-linux]
# The binary is copied to ~/.local/share/retr0-downloader/, a folder you own, so the
# in-app "Update app" button can replace it later.
set -e
here="$(cd "$(dirname "$0")" && pwd)"
src="${1:-$here/Retr0Downloader-linux}"
if [ ! -f "$src" ]; then
  echo "Binary not found: $src"
  echo "Download Retr0Downloader-linux from the Releases page, then run:"
  echo "  ./install_linux.sh /path/to/Retr0Downloader-linux"
  exit 1
fi
dest="$HOME/.local/share/retr0-downloader"
mkdir -p "$dest" "$HOME/.local/share/applications"
install -m 755 "$src" "$dest/Retr0Downloader"
[ -f "$here/icon.png" ] && cp "$here/icon.png" "$dest/icon.png"
cat > "$HOME/.local/share/applications/retr0-downloader.desktop" <<DESK
[Desktop Entry]
Type=Application
Name=Retr0 Downloader
Comment=Highest quality video downloader
Exec=$dest/Retr0Downloader
Icon=$dest/icon.png
Terminal=false
Categories=AudioVideo;Network;
DESK
update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true
echo "Installed. Open 'Retr0 Downloader' from your app menu."
