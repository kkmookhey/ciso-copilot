#!/bin/bash
# Build + push shasta-runner-azure Lambda container image to ECR.
#
# Usage:
#   ./build.sh           # tags 'latest'
#   ./build.sh v0.1.0    # tags 'v0.1.0' + 'latest'

set -euo pipefail

cd "$(dirname "$0")"

REGION="${AWS_REGION:-us-east-1}"
ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
REPO="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/shasta-runner-azure"
TAG="${1:-latest}"

SHASTA_SRC="${SHASTA_SRC:-$HOME/Projects/Shasta}"
if [[ ! -d "$SHASTA_SRC" ]]; then
  echo "ERROR: Shasta source not found at $SHASTA_SRC" >&2
  exit 1
fi

echo "==> staging Shasta from $SHASTA_SRC"
rm -rf .build
mkdir -p .build
rsync -a \
  --exclude='.git' --exclude='.venv' --exclude='__pycache__' \
  --exclude='*.pyc' --exclude='data' --exclude='tests' \
  --exclude='.pytest_cache' --exclude='.ruff_cache' \
  "$SHASTA_SRC/" .build/shasta/

# Copy shared modules from sibling packages. Source of truth lives in
# ai_scanner/ and scanner_core/; .gitignore excludes the runtime copies.
echo "==> copying shared modules from ../ai_scanner"
rm -rf app/detectors app/unified_writer.py
mkdir -p app/detectors
cp ../ai_scanner/detectors/base.py app/detectors/base.py
touch                              app/detectors/__init__.py
cp ../ai_scanner/unified_writer.py app/unified_writer.py

echo "==> copying shared modules from ../scanner_core"
rm -f app/scan_pipeline.py app/scan_state.py app/framework_registry.py app/ai_framework_registry.json
cp ../scanner_core/scan_pipeline.py           app/scan_pipeline.py
cp ../scanner_core/scan_state.py              app/scan_state.py
cp ../scanner_core/framework_registry.py      app/framework_registry.py
cp ../scanner_core/ai_framework_registry.json app/ai_framework_registry.json

# Stage _shared/broadcast_fanout.py inside app/ so the Dockerfile's
# COPY app/ ${LAMBDA_TASK_ROOT}/ makes it importable as _shared.broadcast_fanout
# at Lambda runtime. .gitignore excludes the runtime copy; source of truth
# is platform/lambda/_shared/.
echo "==> staging _shared/broadcast_fanout.py into app/_shared/"
mkdir -p app/_shared
cp ../_shared/__init__.py app/_shared/__init__.py 2>/dev/null || touch app/_shared/__init__.py
cp ../_shared/broadcast_fanout.py app/_shared/broadcast_fanout.py

echo "==> ECR auth"
aws ecr get-login-password --region "$REGION" \
  | docker login --username AWS --password-stdin "$REPO" >/dev/null

echo "==> docker build (linux/amd64) → $REPO:$TAG"
docker build \
  --platform linux/amd64 \
  --provenance=false \
  ${NO_CACHE:+--no-cache} \
  -t "shasta-runner-azure:$TAG" \
  -t "$REPO:$TAG" \
  -t "$REPO:latest" \
  .

echo "==> docker push $REPO:$TAG"
docker push "$REPO:$TAG"
if [[ "$TAG" != "latest" ]]; then
  docker push "$REPO:latest"
fi

rm -rf .build

echo "==> done. Image URI: $REPO:$TAG"
