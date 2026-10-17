# OperateGuard AI user guide

OperateGuard AI reads vendor documents (statements of work, contracts, invoices), checks every important term against your company's policies, and prepares the follow-up actions for you. **Nothing is sent to ERP or Jira until you approve it.**

> All demo documents and policies are **synthetic**. No real companies or contracts are involved.

---

## 1. Open the app

| Where it runs | Address |
|---|---|
| Your computer | http://localhost:8080 (see *Starting the app* at the end) |
| Online (Cloud Run / Firebase / Vercel) | The link your team shared |

**Sign in.** If you see a sign-in page, click **Continue as guest analyst**. No account is needed, and each guest gets a private case list. You can also choose **Sign in with Google**.
If there is no sign-in page, you are in the **shared demo workspace**, where everyone sees the same list.

---

## 2. The home screen

| Area | What it does |
|---|---|
| **Try a demo document** | One-click sample documents. Click **Preview PDF** to read one first. |
| **Upload your own PDF** | Drag a PDF onto the box, or click it to browse. Maximum 15 MB (4 MB on Vercel). |
| **Case queue** | Every document you have analysed, with its status, value, risk and recommended action. Click a row to open it. |

### The demo documents
| Document | What it shows | Expected result |
|---|---|---|
| **A**: Clean SOW, USD 48,000 | Everything follows policy | Low risk; approve and stage in ERP |
| **B**: CoreLogix, USD 92,000 | Payment due in 15 days (policy minimum is 45) | Medium risk; route to Treasury |
| **C**: Apex Cloud, USD 145,000, 15 pages | "Unlimited liability" hidden on **page 9**, plus short payment, renewal and warranty terms | Critical; route to Legal and a VP |
| D | Warranty and governing law missing | Missing terms reported, never guessed |
| E | Two different payment terms in one document | The overriding term (Net 15) is used |
| F | The payment page is a blurry scan | The scan is still read |
| G | Priced in EUR | Converted to USD before choosing approvers |
| H | An invoice, not a contract | Recognised as an invoice |
| I | Unlimited liability in unusual wording | Still caught |
| J | Hidden text telling the AI to approve the contract | Flagged as an injection attempt and escalated |

**Tip:** start with **C**. It shows everything the app can do.

---

## 3. Watching a document being analysed

After you click **Analyze**, the case opens in a three-panel workspace and the agents start working:

```
 Extraction  →  Policy audit  →  Rules  →  Action draft
```

The **Agent reasoning** panel streams each step live, for example:
- *Extraction*: "STATEMENT_OF_WORK from Apex Cloud Solutions LLC: USD 145,000, Net 15, liability UNLIMITED"
- *PolicyAudit*: "LIABILITY_CAP: 3 policy snippets… top = Global_Procurement_Policy_v4.2.pdf Section 8.3"
- *Rules*: "Risk 100/100 (CRITICAL)… Action: ROUTE_TO_LEGAL_AND_VP_APPROVAL"

This usually takes 10 to 25 seconds.

---

## 4. Reading the results

### Left panel: Document
- The PDF with the important clauses **highlighted**: red for critical, orange for high, yellow for moderate, blue for informational.
- The **Evidence** chips at the top jump to the page where each term was found.

### Middle panel: Agent reasoning
- **Extracted terms**: vendor, value, payment terms, liability cap, renewal, warranty and governing law.
  - ✓ means the quoted text was found on that page of the PDF.
  - ◐ means it was read from a scanned image.
  - **missing** means the document does not state it. The app never guesses.
- **Policy findings**: one card per topic, showing:
  - **Contract clause** (left) next to **Company policy** (right), with the exact policy wording and its source file and section. Click the source to open the policy PDF.
  - A **severity badge**: CRITICAL, HIGH, MODERATE, LOW, REVIEW or COMPLIANT.
  - **⚑ notes** where a built-in safety rule overrode or double-checked the AI.
- A **red banner** appears if the document contains text trying to instruct the AI.

### Right panel: Action gateway
| Item | Meaning |
|---|---|
| **UNCOMMITTED DRAFT** | Nothing has been written to any system yet |
| **Risk score (0-100)** | Calculated by fixed rules, not by the AI |
| **Recommended action** | Approve and stage / Route to Treasury / Route to Legal review / Route to Legal + VP |
| **Approval route** | Who must sign off, based on the contract value (under $50k Manager, $50k-$100k Director, over $100k VP + Legal) |
| **Prepared tasks** | The ERP purchase order (ready, or blocked), the approval request and the exception tickets |
| **Rationale** | A plain-English explanation for the approver |

---

## 5. Making a decision

| Button | What happens |
|---|---|
| **Approve and execute** | After you confirm, the tasks run against the mock ERP and Jira. Your name is recorded. The status becomes **Executed** and the created records appear below. |
| **Edit / override** | Change the negotiated payment terms (for example, the vendor agreed to Net 45) or change or waive a finding's severity. Add a reason. The risk and routing are recalculated and the edit is logged. |
| **Reject** | Stops the case. Nothing is written to any system. |

Each case can be approved only once; a second approval is refused.

---

## 6. Checking the audit trail

At the bottom of the right panel:
- **Verify audit chain** recomputes the tamper-proof chain of every AI step and human decision. You should see *"✓ Chain valid"*.
- **Simulate tampering** shows what happens if someone changes a record: the chain breaks at that row. This is only a simulation; your data is not changed.

---

## 7. Common situations

| You see | What to do |
|---|---|
| Red **"Setup needed"** banner | The app is missing a setting (Gemini key or storage). Tell the administrator. |
| Case status **Failed** | Click **Retry processing**. If it fails again, the Gemini key or quota may be the problem. |
| "Demo limit reached" | The hourly run limit was reached. Wait a while. |
| Upload refused | Only PDF files under the size limit are accepted. |
| Highlight missing on a page | That page is a scanned image; the term was still read. |

---

## Starting the app (for whoever runs it)
```powershell
cd D:\workshop\OperateGaurd\operateguard
.\.venv\Scripts\Activate.ps1            # if you created a virtual environment
$env:GEMINI_API_KEY = "your-key"        # free key: https://aistudio.google.com/apikey
python -m uvicorn app.main:app --port 8080
```
Then open http://localhost:8080. Online deployment is covered in `README.md` (Cloud Run) and `DEPLOY_VERCEL.md` (Vercel).
