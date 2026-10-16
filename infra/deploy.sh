#!/usr/bin/env bash
# One-command deploy of OperateGuard AI. Designed for Google Cloud Shell (gcloud, firebase, bq, curl preinstalled).
#
#   export PROJECT_ID=my-project      # billing enabled
#   export REGION=us-central1         # optional
#   firebase login --no-localhost     # once, for Firestore rules + Hosting
#   bash infra/deploy.sh
#
# Idempotent: safe to re-run after a code change.
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-$(gcloud config get-value project 2>/dev/null)}"
REGION="${REGION:-us-central1}"
SERVICE="${SERVICE:-operateguard}"
BUCKET="${BUCKET:-${PROJECT_ID}-operateguard-docs}"
FIRESTORE_LOCATION="${FIRESTORE_LOCATION:-nam5}"
BQ_LOCATION="${BQ_LOCATION:-US}"
GEMINI_FLASH_MODEL="${GEMINI_FLASH_MODEL:-gemini-3.5-flash}"
SA_NAME="operateguard-run"
SA="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

[ -n "$PROJECT_ID" ] || { echo "Set PROJECT_ID"; exit 1; }
echo "==> Project $PROJECT_ID, region $REGION"
gcloud config set project "$PROJECT_ID" >/dev/null
TOKEN() { gcloud auth print-access-token; }

echo "==> Enabling APIs"
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
  aiplatform.googleapis.com firestore.googleapis.com storage.googleapis.com bigquery.googleapis.com \
  firebase.googleapis.com identitytoolkit.googleapis.com firebasehosting.googleapis.com firebaserules.googleapis.com

echo "==> Firebase on the same project"
curl -s -X POST -H "Authorization: Bearer $(TOKEN)" -H "Content-Type: application/json" \
  "https://firebase.googleapis.com/v1beta1/projects/${PROJECT_ID}:addFirebase" -d '{}' >/dev/null || true
sleep 3

echo "==> Firestore (Native mode)"
if ! gcloud firestore databases describe --database="(default)" >/dev/null 2>&1; then
  gcloud firestore databases create --database="(default)" --location="$FIRESTORE_LOCATION" --type=firestore-native
fi

echo "==> Cloud Storage bucket gs://$BUCKET"
gcloud storage buckets describe "gs://$BUCKET" >/dev/null 2>&1 || \
  gcloud storage buckets create "gs://$BUCKET" --location="$REGION" --uniform-bucket-level-access

echo "==> BigQuery audit mirror"
bq --location="$BQ_LOCATION" show "${PROJECT_ID}:operateguard" >/dev/null 2>&1 || bq --location="$BQ_LOCATION" mk -d "${PROJECT_ID}:operateguard"
bq show "${PROJECT_ID}:operateguard.audit_log" >/dev/null 2>&1 || bq mk --table "${PROJECT_ID}:operateguard.audit_log" infra/bq_schema.json

echo "==> Service account (least privilege)"
gcloud iam service-accounts describe "$SA" >/dev/null 2>&1 || \
  gcloud iam service-accounts create "$SA_NAME" --display-name="OperateGuard Cloud Run"
for role in roles/aiplatform.user roles/datastore.user roles/bigquery.dataEditor roles/logging.logWriter; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" --member="serviceAccount:$SA" --role="$role" --condition=None >/dev/null
done
gcloud storage buckets add-iam-policy-binding "gs://$BUCKET" --member="serviceAccount:$SA" --role=roles/storage.objectAdmin >/dev/null
# Source deploys build with the Compute default service account on newer projects; it needs the builder role.
PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
gcloud projects add-iam-policy-binding "$PROJECT_ID" --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
  --role=roles/run.builder --condition=None >/dev/null 2>&1 || true

echo "==> Firebase web app config"
APP_ID="$(curl -s -H "Authorization: Bearer $(TOKEN)" "https://firebase.googleapis.com/v1beta1/projects/${PROJECT_ID}/webApps" \
  | python3 -c 'import sys,json; a=json.load(sys.stdin).get("apps",[]); print(a[0]["appId"] if a else "")')"
if [ -z "$APP_ID" ]; then
  curl -s -X POST -H "Authorization: Bearer $(TOKEN)" -H "Content-Type: application/json" \
    "https://firebase.googleapis.com/v1beta1/projects/${PROJECT_ID}/webApps" -d '{"displayName":"OperateGuard Console"}' >/dev/null
  for i in $(seq 1 20); do
    sleep 3
    APP_ID="$(curl -s -H "Authorization: Bearer $(TOKEN)" "https://firebase.googleapis.com/v1beta1/projects/${PROJECT_ID}/webApps" \
      | python3 -c 'import sys,json; a=json.load(sys.stdin).get("apps",[]); print(a[0]["appId"] if a else "")')"
    [ -n "$APP_ID" ] && break
  done
fi
FIREBASE_WEB_CONFIG="$(curl -s -H "Authorization: Bearer $(TOKEN)" "https://firebase.googleapis.com/v1beta1/projects/${PROJECT_ID}/webApps/${APP_ID}/config" \
  | python3 -c 'import sys,json; c=json.load(sys.stdin); print(json.dumps({k:c[k] for k in ("apiKey","authDomain","projectId","appId","storageBucket","messagingSenderId") if k in c}))')"
echo "    app $APP_ID"

echo "==> Enable Anonymous sign-in (guest access for evaluators)"
enable_anon() {
  curl -sf -X PATCH -H "Authorization: Bearer $(TOKEN)" -H "X-Goog-User-Project: ${PROJECT_ID}" -H "Content-Type: application/json" \
    "https://identitytoolkit.googleapis.com/admin/v2/projects/${PROJECT_ID}/config?updateMask=signIn.anonymous.enabled" \
    -d '{"signIn":{"anonymous":{"enabled":true}}}' >/dev/null
}
if ! enable_anon; then
  # Authentication was never initialised on this project: initialise it, then retry.
  curl -s -X POST -H "Authorization: Bearer $(TOKEN)" -H "X-Goog-User-Project: ${PROJECT_ID}" -H "Content-Type: application/json" \
    "https://identitytoolkit.googleapis.com/v2/projects/${PROJECT_ID}/identityPlatform:initializeAuth" -d '{}' >/dev/null || true
  sleep 3
fi
if ! enable_anon; then
  echo "    !! Could not enable automatically. Open Firebase console > Authentication > Get started > Sign-in method"
  echo "       and enable 'Anonymous' (and optionally 'Google'). Then re-run this script."
fi

echo "==> Cloud Run service $SERVICE"
python3 - "$FIREBASE_WEB_CONFIG" > infra/.env.yaml <<PY
import json, sys
env = {
  "GOOGLE_CLOUD_PROJECT": "$PROJECT_ID", "GOOGLE_GENAI_USE_VERTEXAI": "true", "GEMINI_LOCATION": "global",
  "GEMINI_FLASH_MODEL": "$GEMINI_FLASH_MODEL", "STORE_BACKEND": "firestore", "AUTH_MODE": "firebase",
  "GCS_BUCKET": "$BUCKET", "BQ_AUDIT_TABLE": "$PROJECT_ID.operateguard.audit_log", "FIREBASE_WEB_CONFIG": sys.argv[1],
}
for k, v in env.items():
    print(f"{k}: {json.dumps(v)}")
PY
gcloud run deploy "$SERVICE" --source . --region "$REGION" --service-account "$SA" --allow-unauthenticated \
  --env-vars-file infra/.env.yaml --min-instances 1 --max-instances 4 --cpu 1 --memory 1Gi \
  --timeout 300 --concurrency 20 --no-cpu-throttling --quiet
RUN_URL="$(gcloud run services describe "$SERVICE" --region "$REGION" --format='value(status.url)')"

echo "==> Firestore rules + Firebase Hosting"
cat > firebase.json <<JSON
{
  "firestore": { "rules": "infra/firestore.rules" },
  "hosting": {
    "public": "web",
    "ignore": ["**/.*"],
    "headers": [{ "source": "**/*.@(js|css|html)", "headers": [{ "key": "Cache-Control", "value": "no-cache" }] }],
    "rewrites": [
      { "source": "/api/**", "run": { "serviceId": "$SERVICE", "region": "$REGION" } },
      { "source": "/healthz", "run": { "serviceId": "$SERVICE", "region": "$REGION" } }
    ]
  }
}
JSON
if command -v firebase >/dev/null 2>&1; then
  firebase deploy --only firestore:rules,hosting --project "$PROJECT_ID" --non-interactive || \
    echo "    !! firebase deploy failed - run 'firebase login --no-localhost' and re-run (Cloud Run URL already works)."
else
  echo "    !! firebase CLI not found: npm i -g firebase-tools, then re-run."
fi

echo
echo "==> Smoke test"
curl -s "$RUN_URL/healthz"; echo
echo
echo "Cloud Run URL : $RUN_URL"
echo "Hosting URL   : https://${PROJECT_ID}.web.app"
echo "Done. Open either URL, click 'Continue as guest analyst', then Analyze document C."
