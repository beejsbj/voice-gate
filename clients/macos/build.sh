#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
output="${1:-$PWD/build/Ambient.app}"
mkdir -p "$output/Contents/MacOS"
swiftc -target "$(uname -m)-apple-macosx14.0" -swift-version 5 -O -framework AppKit -framework AVFoundation -framework Speech Ambient.swift -o "$output/Contents/MacOS/Ambient"
cp Info.plist "$output/Contents/Info.plist"
codesign --force --sign - "$output"
"$output/Contents/MacOS/Ambient" --self-test
printf 'Built %s\n' "$output"
