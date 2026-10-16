# Pitch deck content, mapped to the official template

Copy each section into the matching slide of the AI Builder Cup template (Google Slides → File → Download → PPTX, edit, export to PDF under 5 MB). Screenshots are in `docs/screens/`; re-capture them from the deployed URL with `python scripts/capture_screenshots.py https://<PROJECT_ID>.web.app`.

---

## 1. Team details
- **Team name:** `<team name>`
- **Team leader:** `<leader name>`
- **Problem statement:** Future of Work & Enterprise Productivity: autonomous operational compliance and back-office workflow copilot.

## 2. Brief about the idea
Operations, procurement and legal teams spend hours moving information from vendor documents into systems: reading SOWs, contracts and invoices, checking each term against policy, then keying entries into ERP and ticketing tools. The costliest mistakes, such as an unlimited-liability clause on page 9, hide in long documents.

**OperateGuard AI** reads the document with Gemini, checks every term against the company's own policy text, and drafts the ERP entry, exception tickets and approval route. Nothing is written until a human approves, and every step is recorded in a tamper-evident audit trail.

## 3. Opportunities
**How is it different?**
- RPA breaks when layouts change and cannot read meaning. Chatbots answer fluently but are not grounded in policy and should not take actions. OperateGuard combines LLM reading with code-enforced controls.
- *Grounded or silent:* a finding survives only if it quotes, verbatim, a policy snippet retrieved in that run. Fabricated citations are dropped automatically.
- *Deterministic where it matters:* risk score, routing and tasks are computed by code. The model explains; it does not decide system state.

**How does it solve the problem?**
- Upload to a drafted, policy-cited action plan in seconds, with the exact clause highlighted on its page.
- One click to execute approved actions; exceptions are routed to Treasury, Legal or a VP automatically by spend band and severity.

**USP**
- **Agents propose, humans dispose.** Agents have no code path to enterprise systems (enforced by tests); the approval gate is idempotent.
- **Tamper-evident audit:** SHA-256 hash chain with one-click verification and a visible tamper demonstration.
- **Prompt-injection resistant:** text telling the AI to "approve" is reported, cited against policy and escalated, never obeyed.

## 4. Features
- Upload PDF, or one-click synthetic demo documents (3 scenarios + 7 edge cases)
- Gemini multimodal extraction of 12 key terms with page-cited quotes; quotes verified on the PDF text layer; missing terms reported, never guessed
- Hybrid policy retrieval (Gemini embeddings + BM25) with a citation verifier
- Side-by-side view: contract clause ↔ company policy snippet, source file, section, severity
- Deterministic guards that override a model which misses a hard-limit breach
- Risk score (0-100), recommended action, approval route by spend band (USD-converted)
- Live agent trace streamed through Firestore listeners
- PDF viewer with highlighted evidence, colour-coded by severity
- Human gate: Approve and execute / Edit (negotiated terms, severity overrides; re-scored by rules) / Reject
- Mock ERP purchase order (READY_TO_POST or BLOCKED_ON_EXCEPTION), mock Jira exception tickets
- Hash-chained audit trail: verify, plus simulate tampering; mirrored to BigQuery
- Guest sign-in via Firebase Auth; each analyst sees only their own cases (Firestore rules)

## 5. Process flow
```
Upload PDF → [Extraction agent: Gemini reads PDF → quotes verified in code]
           → [Policy Audit agent: retrieve policy per clause → Gemini compares → verifier drops ungrounded findings → guards]
           → [Rules engine: severity, risk score, action, approval route, task list]
           → [Action Dispatch + Supervisor: Gemini writes rationale, ticket text, summary]
           → PROPOSED (uncommitted) → Human: Approve | Edit | Reject
           → Approve: gate executes tasks on mock ERP/Jira → EXECUTED → audit rows (hash chain)
```
State machine: UPLOADED → PROCESSING → PROPOSED → APPROVED → EXECUTED; PROPOSED → REJECTED; edits return to PROPOSED; PROCESSING → FAILED → retry.

## 6. Wireframes / mock diagrams
Use the real screenshots: `03_proposal_findings.png` (three-panel workspace) and `01_home.png` (queue + demo documents).

## 7. Architecture
Use the Mermaid diagram from README.md (render it on GitHub, or redraw it): Browser (Firebase Hosting + Auth) → Cloud Run (FastAPI: API, agent pipeline, rules, approval gate, mock ERP/Jira) → Gemini on Vertex AI; Cloud Firestore (cases, live trace, drafts, audit chain) → browser `onSnapshot`; Cloud Storage (uploads); BigQuery (audit mirror).

## 8. Technologies
| Layer | Technology |
|---|---|
| AI | Gemini 3.5 Flash on Vertex AI (structured output), Gemini embeddings |
| Runtime | Cloud Run (Python 3.11, FastAPI, Pydantic v2) |
| Data | Cloud Firestore (state, live trace, audit chain), Cloud Storage, BigQuery |
| Identity + web | Firebase Authentication, Firebase Hosting, pdf.js, Firebase JS SDK |
| Quality | pytest safety suite (32 tests), Playwright end-to-end, eval/run_eval.py |

## 9. Estimated implementation cost
Fill in from Cloud Billing after the evaluation run. Main drivers: one always-on Cloud Run instance (1 vCPU, 1 GiB, min-instances=1), about 4 Gemini Flash calls per document, and Firestore reads and writes (about 40 per document). Firebase Auth, Hosting and BigQuery stay within free tiers at demo volume. Production would add Vertex AI Search, real ERP connectors and KMS signing.

## 10. Snapshots of the prototype
`docs/screens/`: 01_home, 02_processing_trace, 03_proposal_findings, 04_clause_highlight_page9, 05_executed_audit_verified, 06_tamper_detected, 07_queue.

## 11. Prototype performance / benchmarking
Use **only** `eval/report.md`, generated by `eval/run_eval.py` against the deployed URL: latency p50/p95, extraction accuracy across 12 entities on 10 documents, routing accuracy, citation fidelity, unapproved writes (target 0) and audit-chain verification.

## 12. Future development
Real SAP/Oracle and Jira connectors · Vertex AI Search over the live policy library · Document AI for 100+ page contracts and batch processing · Cloud KMS signing of audit heads · multilingual contracts · policy-change monitoring that re-audits open contracts · learning from analyst overrides.

## 13. Links
- GitHub: `https://github.com/<you>/operateguard`
- Demo video: `<YouTube/Drive link, 3-4 min>`
- Prototype: `https://<PROJECT_ID>.web.app`
