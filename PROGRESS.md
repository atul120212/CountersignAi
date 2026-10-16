# Progress

## Done
- Synthetic data: 4 policy PDFs (28 sections, `corpus.json`), 10 vendor documents (A, B, C + 7 edge cases), ground truth for all 10.
- Agent pipeline: Gemini extraction with verified quotes → hybrid policy retrieval → Gemini audit → citation verifier → deterministic guards and rules → Gemini dispatch + supervisor.
- API, state machine, approval gate (idempotent), edit/reject, mock ERP/Jira, hash-chained audit with verify and tamper simulation, BigQuery mirror.
- Console: guest/Google sign-in, demo documents and upload, queue, three-panel workspace, live trace (Firestore onSnapshot with polling fallback), PDF highlights, approve/edit/reject, audit verification.
- Tests: 32 passing (safety suite, verifier, rules for all 10 documents, hash chain, LLM retry).
- UI verified end to end with Playwright (local harness): no console errors.
- Real Gemini client verified to reach the API (invalid-key run fails the case cleanly).
- deploy.sh, firestore.rules, eval/run_eval.py, README, DECISIONS, submission pack drafts.

## Next (needs the human)
1. Push to a public GitHub repo.
2. In Cloud Shell: `export PROJECT_ID=...; firebase login --no-localhost; bash infra/deploy.sh`.
3. Open the Hosting URL, sign in as guest, run A, B, C.
4. `python eval/run_eval.py --base https://PROJECT_ID.web.app --firebase-api-key <apiKey>` → commit `eval/report.md`.
5. `python scripts/capture_screenshots.py https://PROJECT_ID.web.app` → deck screenshots.
6. Record the video (submission/VIDEO_SCRIPT.md), fill the deck (submission/DECK_CONTENT.md), paste submission/DESCRIPTION.txt.
