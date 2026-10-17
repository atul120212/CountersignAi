# Deploy OperateGuard AI on Vercel

Vercel serves the console from its CDN (`web/`) and runs the FastAPI backend as one Python serverless function (`api/index.py`, see `vercel.json`). Serverless instances do not share memory or disk, so **cases, the live trace, the audit chain and uploaded PDFs are stored in Cloud Firestore**. Gemini is called with an API key (or through Vertex AI with the same service account).

Setup takes about 10 minutes. You need a Google account, a GitHub account and a Vercel account (the Hobby plan is enough).

## 1. Firebase / Firestore (2 minutes)
1. Go to <https://console.firebase.google.com> → **Create a project** (or pick your existing GCP project).
2. **Build → Firestore Database → Create database** → *Production mode* → any location.
3. Open the **Rules** tab, paste the contents of [`infra/firestore.rules`](infra/firestore.rules), then **Publish**.
4. **Project settings → Service accounts → Generate new private key**. A JSON file downloads. Keep it secret and never commit it.

## 2. Gemini API key (1 minute)
<https://aistudio.google.com/apikey> → **Create API key**.

## 3. Optional: per-analyst sign-in and live Firestore streaming
Skip this step to run a **shared demo workspace**: no login, and every visitor uses the same queue. The UI then polls for progress.
1. Firebase console → **Project settings → General → Your apps → Add app → Web** and copy the `firebaseConfig` object as JSON (keys `apiKey`, `authDomain`, `projectId`, `appId`, …).
2. **Build → Authentication → Get started → Sign-in method** → enable **Anonymous** (guest access for evaluators) and optionally **Google**.
3. **Authentication → Settings → Authorized domains** → add your Vercel domain (e.g. `operateguard.vercel.app`). This is only needed for Google sign-in.

## 4. Deploy to Vercel
**Dashboard:** push this repo to GitHub → <https://vercel.com/new> → import the repo → keep the defaults (`vercel.json` sets everything) → add the environment variables below → **Deploy**.

**CLI:**
```bash
npm i -g vercel
vercel link
vercel env add GEMINI_API_KEY production
vercel env add GCP_SERVICE_ACCOUNT_JSON production     # paste the base64 string (see below)
vercel env add FIREBASE_WEB_CONFIG production          # optional
vercel --prod
```

### Environment variables
| Variable | Required | Value |
|---|---|---|
| `GEMINI_API_KEY` | yes | Key from step 2 |
| `GCP_SERVICE_ACCOUNT_JSON` | yes | The service-account JSON from step 1.4, raw or **base64** (base64 avoids newline issues) |
| `FIREBASE_WEB_CONFIG` | optional | Web app config JSON from step 3. Turns on Firebase Auth (guest + Google) and live Firestore listeners |
| `GEMINI_FLASH_MODEL` | optional | Default `gemini-3.5-flash` |
| `USE_EMBEDDINGS` | optional | `false` to use keyword retrieval only (saves API quota) |
| `GOOGLE_GENAI_USE_VERTEXAI` | optional | `true` to call Gemini through Vertex AI with the service account instead of an API key (give it the *Vertex AI User* role) |
| `GCS_BUCKET` / `BQ_AUDIT_TABLE` | optional | Store uploads in Cloud Storage / mirror the audit trail to BigQuery |

Base64-encode the key file:
```bash
base64 -w0 service-account.json          # Linux / Cloud Shell
base64 -i service-account.json | tr -d '\n'   # macOS
```
```powershell
[Convert]::ToBase64String([IO.File]::ReadAllBytes("service-account.json"))   # Windows PowerShell
```

## 5. Check it
- `https://<your-app>.vercel.app/healthz` should show `"store": "firestore"` and `"warnings": []`.
- Open the site → (**Continue as guest analyst** if sign-in is enabled) → **Analyze** document **C**.
- Any missing setting appears as a red **Setup needed** banner on the home page.

## Vercel limits to know
| Limit | Effect |
|---|---|
| Request body 4.5 MB | Uploads are capped at 4 MB on Vercel (the bundled demo documents are unaffected) |
| Function duration | `maxDuration` is 60 s; a case usually takes 10-25 s. If Gemini is slow, the request may time out, but the UI keeps showing the stored state |
| Cold starts | The first request after idle takes a few seconds longer |
| Free Gemini API quota | Low requests-per-minute limits can slow back-to-back runs; the client retries 429s with back-off |

> **AI Builder Cup:** the submission form asks for a prototype "deployed on GCP or Cloud Run". Vercel is a convenient extra demo link; also deploy to Cloud Run with `infra/deploy.sh` and submit that URL.
