#!/bin/bash
# platform/lambda/tools/build.sh — build + push tools-lambda image to ECR.
#
# Usage:
#   ./build.sh           # builds + pushes tag "latest"
#   ./build.sh v1.2.3    # builds + pushes tag "v1.2.3" (also tags latest)
#   NO_CACHE=1 ./build.sh
#
# Prereqs: docker, aws CLI, authenticated to ECR.

set -euo pipefail
cd "$(dirname "$0")"

REGION="${AWS_REGION:-us-east-1}"
ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
REPO="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/tools-lambda"
TAG="${1:-latest}"

# Stage _shared/ into the build context so the Dockerfile can COPY it.
# .gitignore excludes the staged copy; source of truth stays in ../_shared/.
echo "==> staging _shared/ from ../_shared"
rm -rf _shared
cp -r ../_shared _shared

cleanup() {
  echo "==> cleaning up staged _shared/"
  rm -rf _shared
}
trap cleanup EXIT

echo "==> ECR auth"
aws ecr get-login-password --region "$REGION" \
  | docker login --username AWS --password-stdin "$REPO" >/dev/null

echo "==> docker build (linux/amd64) → $REPO:$TAG"
docker build \
  --platform linux/amd64 \
  --provenance=false \
  ${NO_CACHE:+--no-cache} \
  -t "tools-lambda:$TAG" \
  -t "$REPO:$TAG" \
  -t "$REPO:latest" \
  .

echo "==> docker push $REPO:$TAG"
docker push "$REPO:$TAG"
if [[ "$TAG" != "latest" ]]; then
  docker push "$REPO:latest"
fi

echo "==> done. Image URI: $REPO:$TAG"
