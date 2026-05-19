#!/bin/bash
# platform/lambda/entities_api/build.sh
#
# Stage shared modules (detectors/base.py + unified_writer.py) from the
# sibling ai_scanner Lambda into this Lambda's directory so they get
# zipped by CDK's Code.fromAsset. Source of truth lives in ai_scanner;
# .gitignore excludes the runtime copies.
#
# CDK's Code.fromAsset zips the directory at deploy time, so this script
# only needs to be run before `cdk deploy` (or as a CDK pre-deploy hook).

set -euo pipefail
cd "$(dirname "$0")"

echo "==> copying shared modules from ../ai_scanner"
rm -rf detectors unified_writer.py
mkdir -p detectors
cp ../ai_scanner/detectors/base.py detectors/base.py
touch                              detectors/__init__.py
cp ../ai_scanner/unified_writer.py unified_writer.py

echo "==> done. Staged detectors/base.py + unified_writer.py."
