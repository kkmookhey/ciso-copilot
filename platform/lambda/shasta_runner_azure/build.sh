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
