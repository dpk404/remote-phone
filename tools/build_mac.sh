#!/bin/bash
# Build RemotePhone.app and a drag-to-install DMG for macOS.
# Run inside a venv that has the client deps plus PyInstaller:
#   pip install -e . pyinstaller && tools/build_mac.sh
# Output: dist/RemotePhone.app and dist/RemotePhone-<version>-macos-<arch>.dmg

set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
VERSION=$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml)
ARCH=$(uname -m)

python3 -m PyInstaller --noconfirm --windowed \
    --name RemotePhone \
    --icon "$ROOT/remotephone/assets/icon.icns" \
    --add-data "$ROOT/remotephone/assets:assets" \
    --osx-bundle-identifier com.remotephone.desktop \
    --specpath build \
    remotephone/main.py

# PyInstaller only takes the bundle version from a spec file; patch it in and re-seal the signature
plutil -replace CFBundleShortVersionString -string "$VERSION" dist/RemotePhone.app/Contents/Info.plist
codesign --force --sign - dist/RemotePhone.app

# A staged folder with an /Applications link gives the usual drag-to-install window
STAGE=build/dmg
rm -rf "$STAGE" && mkdir -p "$STAGE"
cp -R dist/RemotePhone.app "$STAGE/"
ln -s /Applications "$STAGE/Applications"
DMG="dist/RemotePhone-${VERSION}-macos-${ARCH}.dmg"
if diskutil image create from -h >/dev/null 2>&1; then
    diskutil image create from --format UDZO --volumeName RemotePhone "$STAGE" "$DMG"
else  # before macOS 26 only hdiutil exists; from 26 on these flags are deprecated
    hdiutil create -volname RemotePhone -srcfolder "$STAGE" -ov -format UDZO "$DMG"
fi
rm -rf "$STAGE"
