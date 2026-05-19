#!/bin/bash
# Build + push the shasta-runner Lambda container image to ECR.
#
# Usage:
#   ./build.sh           # tags 'latest'
#   ./build.sh v0.1.0    # tags + pushes 'v0.1.0' AND 'latest'

set -euo pipefail

cd "$(dirname "$0")"

REGION="${AWS_REGION:-us-east-1}"
ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
REPO="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/shasta-runner"
TAG="${1:-latest}"

# 1. Stage Shasta source into the build context.
SHASTA_SRC="${SHASTA_SRC:-$HOME/Projects/Shasta}"
if [[ ! -d "$SHASTA_SRC" ]]; then
  echo "ERROR: Shasta source not found at $SHASTA_SRC. Set SHASTA_SRC env var." >&2
  exit 1
fi

echo "==> staging Shasta from $SHASTA_SRC into .build/shasta"
rm -rf .build
mkdir -p .build
# Copy without .git, .venv, __pycache__, data/ (large), tests/
rsync -a \
  --exclude='.git' --exclude='.venv' --exclude='__pycache__' \
  --exclude='*.pyc' --exclude='data' --exclude='tests' \
  --exclude='.pytest_cache' --exclude='.ruff_cache' \
  "$SHASTA_SRC/" .build/shasta/

# 1b. Copy shared modules from sibling ai_scanner Lambda (detectors/base.py
#     + unified_writer.py). These are imported by app/main.py at runtime;
#     they live in ai_scanner so they don't fork. .gitignore excludes the
#     copies so they don't get committed.
echo "==> copying shared modules from ../ai_scanner"
rm -rf app/detectors app/unified_writer.py
mkdir -p app/detectors
cp ../ai_scanner/detectors/base.py app/detectors/base.py
touch                              app/detectors/__init__.py
cp ../ai_scanner/unified_writer.py app/unified_writer.py

# 2. Authenticate to ECR.
echo "==> authenticating to ECR"
aws ecr get-login-password --region "$REGION" \
  | docker login --username AWS --password-stdin "$REPO" >/dev/null

# 3. Build for Lambda's x86_64 platform (Mac arm64 default would fail).
#    --provenance=false forces Docker v2.2 manifest format; AWS Lambda
#    rejects the OCI format that Buildx uses by default.
echo "==> docker build (linux/amd64) → $REPO:$TAG"
docker build \
  --platform linux/amd64 \
  --provenance=false \
  -t "shasta-runner:$TAG" \
  -t "$REPO:$TAG" \
  -t "$REPO:latest" \
  .

# 4. Push.
echo "==> docker push $REPO:$TAG"
docker push "$REPO:$TAG"
if [[ "$TAG" != "latest" ]]; then
  docker push "$REPO:latest"
fi

# 5. Cleanup the staged source so it doesn't sit on disk.
rm -rf .build

echo "==> done. Image URI: $REPO:$TAG"
