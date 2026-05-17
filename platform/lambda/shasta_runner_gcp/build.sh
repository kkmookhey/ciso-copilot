#!/bin/bash
# Build + push shasta-runner-gcp Lambda container image to ECR.

set -euo pipefail
cd "$(dirname "$0")"

REGION="${AWS_REGION:-us-east-1}"
ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
REPO="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/shasta-runner-gcp"
TAG="${1:-latest}"

SHASTA_SRC="${SHASTA_SRC:-$HOME/Projects/Shasta}"
[[ -d "$SHASTA_SRC" ]] || { echo "ERROR: Shasta source not found at $SHASTA_SRC" >&2; exit 1; }

echo "==> staging Shasta from $SHASTA_SRC"
rm -rf .build
mkdir -p .build
rsync -a --exclude='.git' --exclude='.venv' --exclude='__pycache__' \
  --exclude='*.pyc' --exclude='data' --exclude='tests' \
  --exclude='.pytest_cache' --exclude='.ruff_cache' \
  "$SHASTA_SRC/" .build/shasta/

aws ecr get-login-password --region "$REGION" \
  | docker login --username AWS --password-stdin "$REPO" >/dev/null

docker build --platform linux/amd64 --provenance=false \
  -t "shasta-runner-gcp:$TAG" -t "$REPO:$TAG" -t "$REPO:latest" .

docker push "$REPO:$TAG"
[[ "$TAG" != "latest" ]] && docker push "$REPO:latest"

rm -rf .build
echo "==> done. Image URI: $REPO:$TAG"
