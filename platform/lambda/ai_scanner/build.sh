#!/bin/bash
# platform/lambda/ai_scanner/build.sh — build + push ai-scanner image to ECR.

set -euo pipefail
cd "$(dirname "$0")"

REGION="${AWS_REGION:-us-east-1}"
ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
REPO="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/ai-scanner"
TAG="${1:-latest}"

# Stage shared modules from scanner_core into the build context.
# The Dockerfile does COPY . so these flat-copied files are picked up.
# .gitignore excludes the runtime copies; source of truth is scanner_core/.
echo "==> copying shared modules from ../scanner_core"
cp ../scanner_core/framework_registry.py     framework_registry.py
cp ../scanner_core/ai_framework_registry.json ai_framework_registry.json

# Stage _shared/broadcast_fanout.py so `from _shared import broadcast_fanout`
# resolves at Lambda runtime. The Dockerfile does COPY . ${LAMBDA_TASK_ROOT}/
# so the _shared/ sub-directory lands alongside the handler. .gitignore
# excludes the runtime copy; source of truth is platform/lambda/_shared/.
echo "==> staging _shared/broadcast_fanout.py"
mkdir -p _shared
cp ../_shared/__init__.py _shared/__init__.py 2>/dev/null || touch _shared/__init__.py
cp ../_shared/broadcast_fanout.py _shared/broadcast_fanout.py

echo "==> ECR auth"
aws ecr get-login-password --region "$REGION" \
  | docker login --username AWS --password-stdin "$REPO" >/dev/null

echo "==> docker build (linux/amd64) → $REPO:$TAG"
docker build \
  --platform linux/amd64 \
  --provenance=false \
  ${NO_CACHE:+--no-cache} \
  -t "ai-scanner:$TAG" \
  -t "$REPO:$TAG" \
  -t "$REPO:latest" \
  .

echo "==> docker push $REPO:$TAG"
docker push "$REPO:$TAG"
if [[ "$TAG" != "latest" ]]; then
  docker push "$REPO:latest"
fi

echo "==> done. Image URI: $REPO:$TAG"
