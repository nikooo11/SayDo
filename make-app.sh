#!/bin/zsh
# Rebuild the dev /Applications/SayDo.app bundle around the native launcher.
# The launcher embeds Python in-process, so macOS attributes the app (Dock
# label, permissions, mic panel) to SayDo instead of Python.
set -euo pipefail
cd "$(dirname "$0")"

SAYDO_DIR="$PWD"
PYTHON_LIB="$(brew --prefix python@3.13)/Frameworks/Python.framework/Versions/3.13/Python"
APP=/Applications/SayDo.app

[[ -f "$PYTHON_LIB" ]] || { echo "python@3.13 framework not found"; exit 1; }

mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

clang -O2 -o "$APP/Contents/MacOS/SayDo" launcher.c \
  -DSAYDO_DIR="\"$SAYDO_DIR\"" -DPYTHON_LIB="\"$PYTHON_LIB\""

cp assets/SayDo.icns "$APP/Contents/Resources/SayDo.icns"

/usr/libexec/PlistBuddy "$APP/Contents/Info.plist" \
  -c "Clear dict" \
  -c "Add :CFBundleName string SayDo" \
  -c "Add :CFBundleDisplayName string SayDo" \
  -c "Add :CFBundleIdentifier string com.nikooo11.saydo" \
  -c "Add :CFBundleExecutable string SayDo" \
  -c "Add :CFBundleIconFile string SayDo" \
  -c "Add :CFBundlePackageType string APPL" \
  -c "Add :CFBundleShortVersionString string 2.4" \
  -c "Add :LSMinimumSystemVersion string 13.0" \
  -c "Add :NSHighResolutionCapable bool true" \
  -c "Add :NSMicrophoneUsageDescription string 'SayDo records your voice to transcribe it into text.'"

codesign --force --sign - "$APP"
touch "$APP"
echo "built $APP (launcher -> $SAYDO_DIR/main.py)"
