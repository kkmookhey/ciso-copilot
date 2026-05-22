# shasta-runner — Lambda container that bundles Shasta + our wrapper.
# Build with build.sh (stages Shasta source into .build/shasta first).

FROM public.ecr.aws/lambda/python:3.12

# Stream stdout live to CloudWatch — the scanner block-buffers otherwise,
# making a running scan unobservable until it exits.
ENV PYTHONUNBUFFERED=1

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

# Lambda mode: the base image ENTRYPOINT runs the runtime bootstrap and
# CMD names the handler. Fargate mode: the ECS task definition overrides
# entryPoint+command to `python run.py` (run.py reads params from env
# vars). Both run the same image with the same code at LAMBDA_TASK_ROOT.
WORKDIR ${LAMBDA_TASK_ROOT}
CMD ["main.handler"]
