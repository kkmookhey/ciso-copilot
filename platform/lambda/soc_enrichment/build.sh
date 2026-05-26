#!/usr/bin/env bash
# Build the soc_enrichment Lambda zip with litellm + boto3 bundled.
set -euo pipefail
cd "$(dirname "$0")"
rm -rf build dist && mkdir -p build dist
pip3 install --target build -r requirements.txt --quiet --platform manylinux2014_x86_64 --only-binary=:all: --python-version 3.12 2>/dev/null \
  || pip3 install --target build -r requirements.txt --quiet
cp main.py features.py llm.py build/
# spend_cap is shared with event_router — vendor it in
cp ../event_router/spend_cap.py build/
# Slice 1c: vendor the _shared/ TI substrate
cp ../_shared/ti_lookup.py ../_shared/ioc_extract.py ../_shared/greynoise.py build/
cd build && zip -qr ../dist/soc_enrichment.zip . && cd ..
echo "Built $(pwd)/dist/soc_enrichment.zip"
