# shasta-runner-gcp — Lambda container for GCP scans (Workload Identity Federation).

FROM public.ecr.aws/lambda/python:3.12

RUN pip install --no-cache-dir \
    "boto3>=1.35.0" \
    "pydantic>=2.9.0" \
    "google-auth>=2.29.0" \
    "google-api-python-client>=2.128.0" \
    "google-cloud-storage>=2.16.0" \
    "google-auth-httplib2>=0.2.0"

COPY .build/shasta /tmp/shasta
RUN cd /tmp/shasta && pip install --no-cache-dir --no-deps . && rm -rf /tmp/shasta

COPY app/ ${LAMBDA_TASK_ROOT}/

CMD ["main.handler"]
