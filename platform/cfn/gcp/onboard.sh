#!/usr/bin/env bash
# CISO Copilot — GCP onboarding script (Workload Identity Federation).
#
# Run in Google Cloud Shell (preferred) or any terminal with gcloud signed in:
#
#   curl -fsSL https://cdn.settlingforless.com/gcp/onboard.sh | bash -s -- <EXTERNAL_ID>
#
# What it does:
#   1. Picks up the currently-selected GCP project.
#   2. Enables required APIs (Cloud Resource Manager, IAM, Compute, Storage,
#      Cloud Asset, Logging, Cloud Run, KMS).
#   3. Creates a Workload Identity Pool ('ciso-copilot-pool') trusting our
#      AWS account (470226123496) via an AWS provider.
#   4. Creates a Service Account 'ciso-copilot-reader' with read-only
#      security roles: securityReviewer + cloudasset.viewer + logging.viewer.
#   5. Allows our scanner role (arn:aws:sts::470226123496:assumed-role/
#      ciso-copilot-gcp-scanner) to impersonate the SA via WIF.
#   6. POSTs project ID, project number, SA email, pool/provider IDs to
#      our /onboarding/gcp/complete webhook.
#
# We use WIF (not SA keys) so the customer never hands over a long-lived
# credential. Our Lambda's IAM role is the only "key".

set -euo pipefail

# ---------- args & config ----------

EXTERNAL_ID="${1:-}"
if [[ -z "$EXTERNAL_ID" ]]; then
  echo "ERROR: external ID required. Run as:" >&2
  echo "  curl -fsSL https://cdn.settlingforless.com/gcp/onboard.sh | bash -s -- <EXTERNAL_ID>" >&2
  exit 1
fi

COMPLETE_URL="${CISO_COMPLETE_URL:-https://xoljryrb7i.execute-api.us-east-1.amazonaws.com/v1/onboarding/gcp/complete}"
AWS_ACCOUNT_ID="470226123496"
AWS_SCANNER_ROLE="ciso-copilot-gcp-scanner"
POOL_ID="ciso-copilot-pool"
PROVIDER_ID="ciso-copilot-aws-provider"
SA_NAME="ciso-copilot-reader"

# ---------- preflight ----------

command -v gcloud >/dev/null 2>&1 || { echo "ERROR: gcloud CLI required."; exit 1; }
command -v jq     >/dev/null 2>&1 || { echo "ERROR: jq required.";          exit 1; }

PROJECT_ID="$(gcloud config get-value project 2>/dev/null)"
[[ -z "$PROJECT_ID" || "$PROJECT_ID" == "(unset)" ]] && {
  echo "ERROR: no project selected. Set one with 'gcloud config set project <id>'."; exit 1;
}
PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"

echo "Project:        $PROJECT_ID ($PROJECT_NUMBER)"
echo "Pool / Provider: $POOL_ID / $PROVIDER_ID"
echo "Service account: $SA_NAME@$PROJECT_ID.iam.gserviceaccount.com"
echo

# ---------- enable APIs ----------

echo "==> enabling required APIs (this may take a minute on first run)"
gcloud services enable \
  cloudresourcemanager.googleapis.com \
  iam.googleapis.com \
  iamcredentials.googleapis.com \
  sts.googleapis.com \
  compute.googleapis.com \
  storage.googleapis.com \
  cloudasset.googleapis.com \
  logging.googleapis.com \
  run.googleapis.com \
  cloudkms.googleapis.com \
  --project="$PROJECT_ID" >/dev/null

# ---------- workload identity pool + AWS provider ----------

if ! gcloud iam workload-identity-pools describe "$POOL_ID" --location=global --project="$PROJECT_ID" >/dev/null 2>&1; then
  echo "==> creating WIF pool"
  gcloud iam workload-identity-pools create "$POOL_ID" \
    --location=global --project="$PROJECT_ID" \
    --display-name="CISO Copilot" >/dev/null
else
  echo "==> WIF pool already exists, reusing"
fi

if ! gcloud iam workload-identity-pools providers describe "$PROVIDER_ID" \
     --workload-identity-pool="$POOL_ID" --location=global --project="$PROJECT_ID" >/dev/null 2>&1; then
  echo "==> creating AWS provider on the pool"
  gcloud iam workload-identity-pools providers create-aws "$PROVIDER_ID" \
    --workload-identity-pool="$POOL_ID" --location=global --project="$PROJECT_ID" \
    --account-id="$AWS_ACCOUNT_ID" \
    --attribute-mapping="google.subject=assertion.arn,attribute.aws_role=assertion.arn.contains('assumed-role') ? assertion.arn.extract('{account_arn}assumed-role/') + 'assumed-role/' + assertion.arn.extract('assumed-role/{role_name}/') : assertion.arn" \
    >/dev/null
else
  echo "==> AWS provider already exists, reusing"
fi

# ---------- service account + roles ----------

SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

if ! gcloud iam service-accounts describe "$SA_EMAIL" --project="$PROJECT_ID" >/dev/null 2>&1; then
  echo "==> creating service account"
  gcloud iam service-accounts create "$SA_NAME" \
    --project="$PROJECT_ID" \
    --display-name="CISO Copilot Reader" >/dev/null
else
  echo "==> service account already exists, reusing"
fi

echo "==> granting read-only roles"
for ROLE in roles/iam.securityReviewer roles/cloudasset.viewer roles/logging.viewer; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:$SA_EMAIL" --role="$ROLE" \
    --condition=None --quiet >/dev/null 2>&1 || \
    echo "  warn: failed to bind $ROLE (proceeding)"
done

# ---------- allow our AWS role to impersonate the SA via WIF ----------

PRINCIPAL_SET="principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL_ID}/attribute.aws_role/arn:aws:sts::${AWS_ACCOUNT_ID}:assumed-role/${AWS_SCANNER_ROLE}"

echo "==> binding our AWS role to the SA via WIF"
gcloud iam service-accounts add-iam-policy-binding "$SA_EMAIL" \
  --project="$PROJECT_ID" \
  --role="roles/iam.workloadIdentityUser" \
  --member="$PRINCIPAL_SET" >/dev/null

# ---------- post back ----------

POST_BODY="$(jq -nc \
  --arg eid     "$EXTERNAL_ID" \
  --arg pid     "$PROJECT_ID" \
  --arg pnum    "$PROJECT_NUMBER" \
  --arg sa      "$SA_EMAIL" \
  --arg pool    "$POOL_ID" \
  --arg provider "$PROVIDER_ID" \
  '{external_id:$eid, project_id:$pid, project_number:$pnum, sa_email:$sa, wif_pool:$pool, wif_provider:$provider}')"

echo "==> notifying CISO Copilot platform"
HTTP="$(curl -s -o /tmp/ciso-gcp-resp.json -w '%{http_code}' \
  -X POST "$COMPLETE_URL" \
  -H 'content-type: application/json' \
  -d "$POST_BODY")"

if [[ "$HTTP" =~ ^2 ]]; then
  echo
  echo "✓ GCP project $PROJECT_ID connected to CISO Copilot."
  echo "  Open the app — your first scan starts now."
else
  echo
  echo "ERROR: complete-webhook returned HTTP $HTTP" >&2
  cat /tmp/ciso-gcp-resp.json >&2; echo >&2
  echo "Cleanup (optional):" >&2
  echo "  gcloud iam service-accounts delete $SA_EMAIL --project=$PROJECT_ID --quiet" >&2
  echo "  gcloud iam workload-identity-pools delete $POOL_ID --location=global --project=$PROJECT_ID --quiet" >&2
  exit 1
fi
