#!/usr/bin/env bash
# Build the tools Lambda zip. Vendors _shared/ so the Lambda can import
# from _shared.speakable, _shared.mcp_client, _shared.push.
set -euo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
BUILD=$HERE/build
ZIP=$HERE/tools.zip
rm -rf "$BUILD" "$ZIP"
mkdir -p "$BUILD"
pip install -r "$HERE/requirements.txt" -t "$BUILD" --quiet
cp "$HERE"/*.py "$BUILD"
cp -r "$HERE/.."/_shared "$BUILD/_shared"
(cd "$BUILD" && zip -rq "$ZIP" .)
echo "built: $ZIP"
