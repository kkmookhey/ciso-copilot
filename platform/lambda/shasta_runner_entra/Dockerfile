# shasta-runner-entra — Lambda container for Entra (Azure AD) scans via Graph.

FROM public.ecr.aws/lambda/python:3.12

# Microsoft Graph SDK + minimal Azure deps. We skip the azure-mgmt-*
# resource manager SDKs (those are for Phase B, Azure subscription scope).
RUN pip install --no-cache-dir \
    "boto3>=1.35.0" \
    "pydantic>=2.9.0" \
    "azure-identity>=1.17.0" \
    "msgraph-sdk>=1.5.0"

COPY .build/shasta /tmp/shasta
RUN cd /tmp/shasta && pip install --no-cache-dir --no-deps . && rm -rf /tmp/shasta

COPY app/ ${LAMBDA_TASK_ROOT}/

CMD ["main.handler"]
