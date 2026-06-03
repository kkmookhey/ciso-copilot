#!/bin/bash
# Bootstrap Slice 2 admin connector routes directly via aws apigateway.
#
# CFN's 500-resource limit per stack prevents adding these routes via CDK
# (CisoCopilotApi is at 498). The Lambda's internal `_route` dispatcher
# handles each path; this script just opens API Gateway routes that
# forward to it.
#
# Idempotent: re-running is safe — `get-resources` finds existing children
# and `create-resource` is skipped when the path-part already exists.
#
# Routes created (all forward to connectorsFn):
#   GET    /v1/connectors/admin/slack/channels             — Cognito-authed
#   POST   /v1/connectors/admin/slack/broadcast-channel    — Cognito-authed
#   PATCH  /v1/connectors/admin/slack/autonomous-rule      — Cognito-authed
#   DELETE /v1/connectors/admin/slack                       — Cognito-authed
#   GET    /v1/connectors/admin/slack/status                — Cognito-authed
#
# /connect/slack-workspace-bot and /callback/slack-workspace-bot go through
# the existing /connect/{kind} and /callback/{kind} routes (the Lambda's
# regex matches the literal value).
set -euo pipefail

: "${REST_API_ID:?missing REST_API_ID}"
: "${CONNECTORS_RES_ID:?missing CONNECTORS_RES_ID — id of /connectors}"
: "${AUTHORIZER_ID:?missing AUTHORIZER_ID — Cognito user pool authorizer}"
: "${LAMBDA_ARN:?missing LAMBDA_ARN}"
: "${REGION:=us-east-1}"
: "${ACCOUNT:?missing ACCOUNT}"

# Helper: return the resource id for $1=parent_id $2=path_part, creating it
# if it doesn't already exist.
ensure_resource() {
  local parent_id=$1
  local path_part=$2
  local existing
  existing=$(aws apigateway get-resources --rest-api-id "$REST_API_ID" --limit 500 \
    --query "items[?parentId=='$parent_id' && pathPart=='$path_part'].id" --output text)
  if [[ -n "$existing" && "$existing" != "None" ]]; then
    echo "$existing"
    return 0
  fi
  aws apigateway create-resource \
    --rest-api-id "$REST_API_ID" --parent-id "$parent_id" --path-part "$path_part" \
    --query 'id' --output text
}

# Helper: add a method + Lambda integration. $1=resource_id $2=verb
add_method() {
  local res_id=$1
  local verb=$2
  # PUT method (idempotent — overwrites if exists)
  aws apigateway put-method --rest-api-id "$REST_API_ID" --resource-id "$res_id" \
    --http-method "$verb" \
    --authorization-type COGNITO_USER_POOLS --authorizer-id "$AUTHORIZER_ID" \
    --no-api-key-required > /dev/null
  aws apigateway put-integration --rest-api-id "$REST_API_ID" --resource-id "$res_id" \
    --http-method "$verb" \
    --type AWS_PROXY --integration-http-method POST \
    --uri "arn:aws:apigateway:$REGION:lambda:path/2015-03-31/functions/$LAMBDA_ARN/invocations" > /dev/null
  # Lambda permission so API Gateway can invoke. Use a stable, idempotent statement id.
  local sid="apigw-invoke-${verb}-${res_id}"
  aws lambda add-permission --function-name "$LAMBDA_ARN" --statement-id "$sid" \
    --action lambda:InvokeFunction --principal apigateway.amazonaws.com \
    --source-arn "arn:aws:execute-api:$REGION:$ACCOUNT:$REST_API_ID/*/$verb/connectors/admin/*" \
    2>/dev/null || echo "  (permission $sid already exists)"
  echo "  $verb on resource $res_id wired"
}

echo "==> /connectors/admin"
ADMIN_ID=$(ensure_resource "$CONNECTORS_RES_ID" "admin")
echo "  admin resource id: $ADMIN_ID"

echo "==> /connectors/admin/slack"
ADMIN_SLACK_ID=$(ensure_resource "$ADMIN_ID" "slack")
echo "  admin/slack resource id: $ADMIN_SLACK_ID"
add_method "$ADMIN_SLACK_ID" DELETE

echo "==> /connectors/admin/slack/channels (GET)"
CHANNELS_ID=$(ensure_resource "$ADMIN_SLACK_ID" "channels")
add_method "$CHANNELS_ID" GET

echo "==> /connectors/admin/slack/broadcast-channel (POST)"
BCAST_ID=$(ensure_resource "$ADMIN_SLACK_ID" "broadcast-channel")
add_method "$BCAST_ID" POST

echo "==> /connectors/admin/slack/autonomous-rule (PATCH)"
TOGGLE_ID=$(ensure_resource "$ADMIN_SLACK_ID" "autonomous-rule")
add_method "$TOGGLE_ID" PATCH

echo "==> /connectors/admin/slack/status (GET)"
STATUS_ID=$(ensure_resource "$ADMIN_SLACK_ID" "status")
add_method "$STATUS_ID" GET

echo "==> Deploying API to v1 stage"
aws apigateway create-deployment --rest-api-id "$REST_API_ID" --stage-name v1 \
  --description "Bootstrap admin connector routes (Slice 2 out-of-CDK)" \
  --query 'id' --output text

echo "Done."
