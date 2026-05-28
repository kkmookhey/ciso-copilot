#!/usr/bin/env bash
# Build the tools Lambda zip.
# - Vendors _shared/ so the Lambda can import _shared.{speakable,mcp_client,push}.
# - Installs deps targeting manylinux2014_x86_64 (matches Lambda runtime) and
#   falls back to the host Python wheels if the targeted install fails — pattern
#   borrowed from soc_enrichment/build.sh.
# CDK's lambda.Code.fromAsset points at this directory; the zip output below is
# for manual inspection / out-of-band uploads. Asset-mode deploys still need
# this script run first so build/ contains vendored deps + _shared/.
set -euo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
BUILD=$HERE/build
DIST=$HERE/dist
rm -rf "$BUILD" "$DIST"
mkdir -p "$BUILD" "$DIST"
pip3 install --target "$BUILD" -r "$HERE/requirements.txt" --quiet \
  --platform manylinux2014_x86_64 --only-binary=:all: --python-version 3.12 2>/dev/null \
  || pip3 install --target "$BUILD" -r "$HERE/requirements.txt" --quiet
cp "$HERE"/*.py "$BUILD"
cp -r "$HERE/.."/_shared "$BUILD/_shared"
(cd "$BUILD" && zip -qr "$DIST/tools.zip" .)
echo "built: $DIST/tools.zip"
