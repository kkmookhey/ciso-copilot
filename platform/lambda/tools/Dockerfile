# platform/lambda/tools/Dockerfile
# tools dispatcher Lambda — container-based so it can shell out to npx for
# the Anthropic-reference Slack and GitHub MCP servers.
#
# Build with build.sh; pushed to the tools-lambda ECR repo.
# build.sh stages _shared/ into the build context before calling docker build.

FROM public.ecr.aws/lambda/python:3.12

# Node.js for upstream MCP servers (Slack, GitHub).
# Base is Amazon Linux 2023 — use dnf, not yum.
RUN dnf install -y nodejs npm && dnf clean all

# Pre-install the two MCP server packages globally so npx can find them
# without a network fetch at Lambda runtime (air-gap safe, faster cold start).
RUN npm install -g \
    @modelcontextprotocol/server-slack \
    @modelcontextprotocol/server-github

# Atlassian MCP — pip-installable Python MCP server.
RUN pip install --no-cache-dir mcp-atlassian

# App deps.
COPY requirements.txt ${LAMBDA_TASK_ROOT}/
RUN pip install --no-cache-dir -r ${LAMBDA_TASK_ROOT}/requirements.txt

# App code + vendored _shared/ (staged by build.sh before docker build).
# Preserve the tools/ package structure so `from tools.main import register`
# resolves at runtime (matches the test-time import path).
COPY *.py ${LAMBDA_TASK_ROOT}/tools/
COPY _shared/ ${LAMBDA_TASK_ROOT}/_shared/

CMD ["tools.main.handler"]
