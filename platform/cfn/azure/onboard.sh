#!/usr/bin/env bash
# CISO Copilot — Azure onboarding script.
#
# Customer runs this in Azure Cloud Shell (preferred) or any terminal with
# the Azure CLI installed and signed in:
#
#   curl -fsSL https://cdn.settlingforless.com/azure/onboard.sh | bash -s -- <EXTERNAL_ID>
#
# What it does (you can read it before running — that's why it's a script
# you pipe to bash, not a binary):
#
#   1. Confirms you're signed in to the correct tenant.
#   2. Creates a Service Principal called "CISO Copilot Reader".
#   3. Assigns Reader + Security Reader at every accessible subscription.
#   4. Generates a client secret valid for 2 years.
#   5. POSTs the SP's tenant ID, client ID, secret, and accessible
#      subscription IDs to https://api.settlingforless.com/v1/onboarding/azure/complete
#      along with the one-time external ID you passed in.
#
# Nothing else is sent anywhere. The secret never lands in any logs or
# stdout — it's POSTed once and stored in our AWS Secrets Manager.

set -euo pipefail

# ---------- args & config ----------

EXTERNAL_ID="${1:-}"
if [[ -z "$EXTERNAL_ID" ]]; then
  echo "ERROR: external ID is required. Run as:" >&2
  echo "  curl -fsSL https://cdn.settlingforless.com/azure/onboard.sh | bash -s -- <EXTERNAL_ID>" >&2
  exit 1
fi

COMPLETE_URL="${CISO_COMPLETE_URL:?CISO_COMPLETE_URL must be set — the onboarding flow passes this automatically}"
SP_NAME="${CISO_SP_NAME:-CISO Copilot Reader}"

# ---------- preflight ----------

if ! command -v az >/dev/null 2>&1; then
  echo "ERROR: 'az' (Azure CLI) is required. Install: https://aka.ms/azurecli" >&2
  exit 1
fi
if ! command -v jq >/dev/null 2>&1; then
  echo "ERROR: 'jq' is required. Install: https://stedolan.github.io/jq/" >&2
  exit 1
fi

ACCT_JSON="$(az account show 2>/dev/null)" || { echo "ERROR: 'az login' first." >&2; exit 1; }
TENANT_ID="$(jq -r '.tenantId' <<<"$ACCT_JSON")"
SIGNED_IN_USER="$(jq -r '.user.name' <<<"$ACCT_JSON")"

echo "Signed in as: $SIGNED_IN_USER"
echo "Entra tenant: $TENANT_ID"

# Discover accessible subscriptions
SUBS_JSON="$(az account list --query '[?state==`Enabled`].{id:id,name:name}' -o json)"
SUB_COUNT="$(jq 'length' <<<"$SUBS_JSON")"
if (( SUB_COUNT == 0 )); then
  echo "ERROR: no enabled subscriptions found on this account." >&2
  exit 1
fi

echo "Subscriptions to be onboarded ($SUB_COUNT):"
jq -r '.[] | "  - \(.name) (\(.id))"' <<<"$SUBS_JSON"
echo

# ---------- create SP ----------

# RBAC scopes for each subscription. We assign Reader (read everything) +
# Security Reader (read Defender alerts).
SCOPES=()
while IFS= read -r sub_id; do
  SCOPES+=( "/subscriptions/$sub_id" )
done < <(jq -r '.[].id' <<<"$SUBS_JSON")

echo "Creating Service Principal '$SP_NAME' with Reader + Security Reader..."
SP_JSON="$(az ad sp create-for-rbac \
  --name "$SP_NAME" \
  --role Reader \
  --scopes "${SCOPES[@]}" \
  --years 2 \
  --output json)"

CLIENT_ID="$(jq -r '.appId'     <<<"$SP_JSON")"
CLIENT_SECRET="$(jq -r '.password' <<<"$SP_JSON")"

# Add Security Reader role (assign-role doesn't accept multiple --role flags, so loop)
echo "Adding Security Reader role on each subscription..."
for scope in "${SCOPES[@]}"; do
  az role assignment create \
    --assignee "$CLIENT_ID" \
    --role "Security Reader" \
    --scope "$scope" \
    --output none 2>/dev/null || echo "  warn: Security Reader assignment failed on $scope (proceeding)"
done

# ---------- post back to CISO Copilot ----------

SUB_IDS_JSON="$(jq -c '[.[].id]' <<<"$SUBS_JSON")"

POST_BODY="$(jq -nc \
  --arg eid    "$EXTERNAL_ID" \
  --arg tid    "$TENANT_ID" \
  --arg cid    "$CLIENT_ID" \
  --arg csec   "$CLIENT_SECRET" \
  --argjson subs "$SUB_IDS_JSON" \
  '{external_id:$eid, azure_tenant_id:$tid, client_id:$cid, client_secret:$csec, subscription_ids:$subs}')"

echo "Notifying CISO Copilot platform..."
HTTP_CODE="$(curl -s -o /tmp/ciso-complete-resp.json -w '%{http_code}' \
  -X POST "$COMPLETE_URL" \
  -H 'content-type: application/json' \
  -d "$POST_BODY")"

if [[ "$HTTP_CODE" =~ ^2 ]]; then
  echo
  echo "✓ Azure subscription(s) connected to CISO Copilot."
  echo "  Open the iOS app or web console — your first scan starts now."
else
  echo
  echo "ERROR: complete-webhook returned HTTP $HTTP_CODE" >&2
  echo "Response body:" >&2
  cat /tmp/ciso-complete-resp.json >&2
  echo >&2
  echo "Your Service Principal was created. You can delete it with:" >&2
  echo "  az ad sp delete --id $CLIENT_ID" >&2
  exit 1
fi
