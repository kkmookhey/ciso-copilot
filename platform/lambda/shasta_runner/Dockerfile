# shasta-runner — Lambda container that bundles Shasta + our wrapper.
# Build with build.sh (stages Shasta source into .build/shasta first).

FROM public.ecr.aws/lambda/python:3.12

# Install ONLY the runtime deps shasta.aws.* + shasta.evidence actually use.
# Shasta's full deps include xhtml2pdf → pycairo which needs gcc to build and
# is unrelated to scanning (used by reports/). Skipping all of that.
RUN pip install --no-cache-dir \
    "boto3>=1.35.0" \
    "pydantic>=2.9.0"

# Install Shasta source WITHOUT its declared deps (we just installed what we need).
COPY .build/shasta /tmp/shasta
RUN cd /tmp/shasta && pip install --no-cache-dir --no-deps . && rm -rf /tmp/shasta

# Our wrapper handler.
COPY app/ ${LAMBDA_TASK_ROOT}/

CMD ["main.handler"]
