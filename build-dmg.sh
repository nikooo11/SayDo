#!/bin/zsh
# Build a self-contained SayDo.app and wrap it in a DMG (Apple Silicon).
# Requires: .venv with requirements.txt + pyinstaller installed, and the
# tiny.en model already downloaded once (run the app once, or prime.py).
set -e
cd "$(dirname "$0")"

VERSION=${1:-1.0}
MODEL_CACHE=~/.cache/huggingface/hub/models--Systran--faster-whisper-tiny.en/snapshots

# Stage the model with symlinks resolved (HF cache stores blobs behind symlinks).
rm -rf build/model-staging
mkdir -p build/model-staging
cp -RL "$MODEL_CACHE"/*/ build/model-staging/

.venv/bin/pyinstaller --noconfirm --windowed --name SayDo \
  --osx-bundle-identifier com.nikooo11.saydo \
  --icon assets/SayDo.icns \
  --add-data "config.yaml:." \
  --add-data "build/model-staging:models/faster-whisper-tiny.en" \
  --add-data "ui:ui" \
  --collect-all faster_whisper \
  main.py

PLIST=dist/SayDo.app/Contents/Info.plist
plutil -replace LSUIElement -bool true "$PLIST"
plutil -replace NSMicrophoneUsageDescription \
  -string "SayDo records your voice to transcribe it into text." "$PLIST"
plutil -replace CFBundleShortVersionString -string "$VERSION" "$PLIST"

codesign --force --deep -s - dist/SayDo.app

rm -rf build/dmg-staging
mkdir -p build/dmg-staging
cp -R dist/SayDo.app build/dmg-staging/
ln -s /Applications build/dmg-staging/Applications
hdiutil create -volname SayDo -srcfolder build/dmg-staging -ov -format UDZO \
  "dist/SayDo-$VERSION-arm64.dmg"

echo "Built dist/SayDo-$VERSION-arm64.dmg"
