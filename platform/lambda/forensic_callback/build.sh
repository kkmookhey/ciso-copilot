#!/usr/bin/env bash
# Build the forensic_callback Lambda zip.
# Vendors _shared/push.py so the Lambda can `from _shared import push` at runtime.
# No third-party deps — uses only boto3 (already in the Lambda runtime).
set -euo pipefail
cd "$(dirname "$0")"
rm -rf build dist && mkdir -p build dist
cp main.py __init__.py build/
# Vendor _shared/ as a package so `from _shared import push as push_mod` works.
mkdir -p build/_shared
cp ../_shared/__init__.py build/_shared/__init__.py 2>/dev/null || touch build/_shared/__init__.py
cp ../_shared/push.py build/_shared/push.py
cd build && zip -qr ../dist/forensic_callback.zip . && cd ..
echo "Built $(pwd)/dist/forensic_callback.zip"
