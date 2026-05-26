#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
rm -rf build dist && mkdir -p build dist
cp main.py __init__.py build/
cp ../_shared/ti_lookup.py build/
cd build && zip -qr ../dist/ti_feed_tor.zip . && cd ..
echo "Built $(pwd)/dist/ti_feed_tor.zip"
