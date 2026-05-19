# shasta-runner-azure — Lambda container for Azure scans.
# Same pattern as shasta-runner (AWS): build context staged by build.sh,
# Shasta installed --no-deps to avoid xhtml2pdf → pycairo compile chain.

FROM public.ecr.aws/lambda/python:3.12

# Azure SDK + scanner deps. msgraph-sdk needed by shasta/azure/iam.py's
# Conditional Access / MFA checks — Shasta's Azure IAM module reaches into
# Entra via Microsoft Graph.
RUN pip install --no-cache-dir \
    "boto3>=1.35.0" \
    "pydantic>=2.9.0" \
    "azure-identity>=1.17.0" \
    "azure-mgmt-resource>=23.0.0" \
    "azure-mgmt-subscription>=3.0.0" \
    "azure-mgmt-compute>=31.0.0" \
    "azure-mgmt-network>=25.0.0" \
    "azure-mgmt-storage>=21.0.0" \
    "azure-mgmt-monitor>=6.0.0" \
    "azure-mgmt-sql>=3.0.0" \
    "azure-mgmt-keyvault>=10.0.0" \
    "azure-mgmt-authorization>=4.0.0" \
    "azure-mgmt-security>=6.0.0" \
    "msgraph-sdk>=1.0.0"

COPY .build/shasta /tmp/shasta
RUN cd /tmp/shasta && pip install --no-cache-dir --no-deps . && rm -rf /tmp/shasta

COPY app/ ${LAMBDA_TASK_ROOT}/

CMD ["main.handler"]
