#!/usr/bin/env bash
# Build the ti_feed_kev Lambda zip with vendored _shared/ code.
# No third-party deps — uses stdlib urllib + boto3 (already in Lambda runtime).
set -euo pipefail
cd "$(dirname "$0")"
rm -rf build dist && mkdir -p build dist
cp main.py __init__.py build/
cp ../_shared/ti_lookup.py build/
cd build && zip -qr ../dist/ti_feed_kev.zip . && cd ..
echo "Built $(pwd)/dist/ti_feed_kev.zip"
