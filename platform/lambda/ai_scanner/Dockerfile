# platform/lambda/ai_scanner/Dockerfile
# ai-scanner — Lambda container that clones a customer's GitHub repo and
# runs 8 deterministic AI-discovery detectors over the source.
#
# Build with build.sh; pushed to the ai-scanner ECR repo.

FROM public.ecr.aws/lambda/python:3.12

# System deps: git for cloning.
# Note: ripgrep is not in the Amazon Linux 2023 dnf repos; installed via pip
# (the 'ripgrep' PyPI package ships a static rg binary at bin/rg).
RUN dnf install -y git && dnf clean all

# Python deps. Pinned for reproducibility.
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt && rm /tmp/requirements.txt

# Source.
COPY . ${LAMBDA_TASK_ROOT}/

CMD ["main.handler"]
