// Countersign AI - analyst console (vanilla JS, no build step).
// Auth: Firebase Authentication. Live updates: Cloud Firestore onSnapshot (falls back to polling the API).
const FB = "10.12.2";
const PDFJS_WORKER = "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js";
const $app = document.getElementById("app");
let CFG = null, fb = null, USER = null, SAMPLES = null;
const S = { caseId: null, case: null, trace: [], unsub: [], poll: null, pdfFor: null, pages: {}, lastSig: "", seen: new Set(), verify: null, mock: null, audit: null };
const running = new Set();

// ------------------------------------------------------------------ utils
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const norm = (s) => String(s || "").toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
const money = (v, c) => v == null ? "—" : new Intl.NumberFormat("en-US", { style: "currency", currency: (c || "USD").slice(0, 3), maximumFractionDigits: 0 }).format(v);
const label = (s) => String(s || "").replace(/_/g, " ").toLowerCase().replace(/^\w/, (m) => m.toUpperCase());
const ago = (ms) => { if (!ms) return ""; const s = Math.round((Date.now() - ms) / 1000); return s < 60 ? `${s}s ago` : s < 3600 ? `${Math.round(s / 60)}m ago` : new Date(ms).toLocaleString(); };
const SEV_RANK = { CRITICAL: 4, HIGH: 3, MODERATE: 2, LOW: 1, NONE: 0 };
const FIELD_CAT = { liability_cap: "LIABILITY_CAP", payment_terms_days: "PAYMENT_TERMS", auto_renewal: "AUTO_RENEWAL", auto_renewal_notice_days: "AUTO_RENEWAL", warranty_period_days: "WARRANTY", governing_law: "GOVERNING_LAW", total_value: "SPEND_APPROVAL" };
const ACTION_TEXT = { APPROVE_AND_STAGE: "Approve and stage in ERP", ROUTE_TO_TREASURY: "Route to Treasury exception", ROUTE_TO_LEGAL_REVIEW: "Route to Legal review", ROUTE_TO_LEGAL_AND_VP_APPROVAL: "Route to Legal + VP approval" };
const riskColor = (lvl) => ({ CRITICAL: "var(--crit)", HIGH: "var(--high)", MEDIUM: "var(--mod)", LOW: "var(--ok)" }[lvl] || "var(--text-3)");

function toast(msg, ms = 4200) {
  const t = document.getElementById("toast");
  t.textContent = msg; t.hidden = false;
  clearTimeout(toast._t); toast._t = setTimeout(() => (t.hidden = true), ms);
}

async function token() {
  if (fb && fb.auth.currentUser) return fb.auth.currentUser.getIdToken();
  return null;
}
async function api(path, opts = {}) {
  const headers = { ...(opts.headers || {}) };
  const tk = await token();
  if (tk) headers.Authorization = `Bearer ${tk}`;
  if (opts.json !== undefined) { headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(opts.json); }
  const r = await fetch(path, { ...opts, headers });
  if (!r.ok) {
    let d = r.statusText;
    try { d = (await r.json()).detail || d; } catch {}
    throw new Error(typeof d === "string" ? d : JSON.stringify(d));
  }
  return opts.raw ? r : r.json();
}

function modal(title, body, actions) {
  const root = document.getElementById("modal-root");
  root.innerHTML = `<div class="modal-bg"><div class="card modal"><div class="card-h"><h2>${esc(title)}</h2></div>
    <div class="card-b">${body}<div style="display:flex;gap:8px;justify-content:flex-end;margin-top:16px" id="m-actions"></div></div></div></div>`;
  const box = root.querySelector("#m-actions");
  const close = () => (root.innerHTML = "");
  root.querySelector(".modal-bg").addEventListener("click", (e) => { if (e.target.classList.contains("modal-bg")) close(); });
  for (const a of [{ text: "Cancel", cls: "", fn: close }, ...actions]) {
    const b = document.createElement("button");
    b.className = `btn ${a.cls || ""}`; b.textContent = a.text;
    b.onclick = async () => { if (a.fn === close) return close(); b.disabled = true; try { if ((await a.fn(root)) !== false) close(); } catch (e) { toast(e.message); } finally { b.disabled = false; } };
    box.appendChild(b);
  }
  return root;
}

// ------------------------------------------------------------------ boot + auth
async function boot() {
  CFG = await (await fetch("/api/config")).json();
  if (CFG.authMode === "firebase" && CFG.firebase) {
    const [appM, authM, fsM] = await Promise.all([
      import(`https://www.gstatic.com/firebasejs/${FB}/firebase-app.js`),
      import(`https://www.gstatic.com/firebasejs/${FB}/firebase-auth.js`),
      import(`https://www.gstatic.com/firebasejs/${FB}/firebase-firestore.js`),
    ]);
    const app = appM.initializeApp(CFG.firebase);
    fb = { auth: authM.getAuth(app), authM, db: fsM.getFirestore(app), fsM };
    authM.onAuthStateChanged(fb.auth, (u) => { USER = u; renderUser(); route(); });
  } else {
    USER = { local: true, displayName: CFG.demoUser || "Demo Analyst", uid: "demo-analyst" };
    renderUser(); route();
  }
  window.addEventListener("hashchange", route);
}

function userName() {
  if (!USER) return "";
  if (USER.local) return USER.displayName;
  return USER.displayName || USER.email || `Guest analyst ${USER.uid.slice(0, 6)}`;
}

function renderUser() {
  const box = document.getElementById("userbox");
  if (!USER) { box.innerHTML = ""; return; }
  const n = userName();
  box.innerHTML = `<span class="small muted hide-sm">${fb ? "" : "Shared demo workspace · "}${CFG.realtime ? "Live: Firestore" : "Live: polling"} · ${esc(CFG.models.extraction)}</span>
    <span class="avatar" title="${esc(n)}">${esc(n.slice(0, 1).toUpperCase())}</span><span>${esc(n)}</span>
    ${fb ? `<button class="btn sm ghost" id="signout">Sign out</button>` : ""}`;
  const so = document.getElementById("signout");
  if (so) so.onclick = () => fb.authM.signOut(fb.auth).then(() => (location.hash = "#/"));
}

function renderLogin() {
  $app.innerHTML = `<section class="login">
    <div>
      <h1>Operations compliance copilot for vendor documents</h1>
      <p class="lead">Upload a SOW, contract or invoice. Gemini extracts the terms, every finding is grounded in your own policy text, and nothing reaches ERP or Jira until a human approves.</p>
      <ul>
        <li><span class="dot">1</span><span><b>Extract</b> - Gemini reads the PDF (even scanned pages) and quotes the page for every term.</span></li>
        <li><span class="dot">2</span><span><b>Ground</b> - each term is checked against retrieved policy snippets; uncited findings are dropped.</span></li>
        <li><span class="dot">3</span><span><b>Decide</b> - risk score and routing are computed by code, not by the model.</span></li>
        <li><span class="dot">4</span><span><b>Approve</b> - a human gate executes the draft; every step is hash-chained for audit.</span></li>
      </ul>
    </div>
    <div class="card"><div class="card-b">
      <h2 style="font-size:17px">Sign in to the analyst console</h2>
      <p class="muted small" style="margin:0">Evaluators: use guest access - no account needed. Each guest gets a private case queue.</p>
      <button class="btn primary block" id="guest">Continue as guest analyst</button>
      <button class="btn block" id="google">Sign in with Google</button>
      <p class="muted small" style="margin:0">All documents and policies in this demo are synthetic.</p>
    </div></div></section>`;
  document.getElementById("guest").onclick = () => fb.authM.signInAnonymously(fb.auth).catch((e) => toast("Guest sign-in failed: " + e.message));
  document.getElementById("google").onclick = () => fb.authM.signInWithPopup(fb.auth, new fb.authM.GoogleAuthProvider()).catch((e) => toast("Google sign-in failed: " + e.message));
}

function cleanup() {
  S.unsub.forEach((u) => { try { u(); } catch {} });
  S.unsub = [];
  clearInterval(S.poll); S.poll = null;
}

function route() {
  cleanup();
  if (!USER) return renderLogin();
  const m = location.hash.match(/^#\/case\/([\w-]+)/);
  if (m) return openCase(m[1]);
  return renderHome();
}

// ------------------------------------------------------------------ home
function configBanner() {
  return (CFG.warnings || []).map((w) => `<div class="alert crit">Setup needed: ${esc(w)}</div>`).join("");
}

async function renderHome() {
  $app.innerHTML = `${configBanner()}<div class="flow"><span class="s">Upload PDF</span><span class="a">→</span><span class="s">Gemini extraction</span><span class="a">→</span>
    <span class="s">Policy retrieval + verified citations</span><span class="a">→</span><span class="s">Deterministic risk + routing</span><span class="a">→</span>
    <span class="s">Human approval gate</span><span class="a">→</span><span class="s">Mock ERP / Jira</span><span class="a">→</span><span class="s">Hash-chained audit</span></div>
    <div class="home">
      <div class="card"><div class="card-h"><h2>Try a demo document</h2><span class="sub">synthetic, one click</span></div>
        <div class="card-b"><div class="samples" id="samples"><div class="muted">Loading…</div></div>
          <label class="drop" id="drop"><input type="file" accept="application/pdf" id="file" />
            <b>Upload your own PDF</b><br><span class="small">Drop a vendor SOW, contract or invoice here, or click to browse (max ${CFG.maxUploadMb} MB)</span></label>
        </div></div>
      <div class="card"><div class="card-h"><h2>Case queue</h2><span class="sub" id="qsub"></span></div><div id="queue"><div class="empty">Loading…</div></div></div>
    </div>`;
  SAMPLES = SAMPLES || (await api("/api/samples"));
  const card = (s) => `<div class="sample"><div class="t">${esc(s.title)}</div><div class="d">${esc(s.blurb)}</div>
    <div class="row"><button class="btn sm primary" data-run="${s.key}">Analyze</button><a class="small" href="/api/samples/${s.key}/pdf" target="_blank" rel="noopener">Preview PDF</a></div></div>`;
  document.getElementById("samples").innerHTML = SAMPLES.filter((s) => s.featured).map(card).join("") +
    `<details class="edge"><summary>${SAMPLES.filter((s) => !s.featured).length} edge-case documents (missing fields, scanned page, injection…)</summary><div class="samples">${SAMPLES.filter((s) => !s.featured).map(card).join("")}</div></details>`;
  document.querySelectorAll("[data-run]").forEach((b) => (b.onclick = () => runSample(b.dataset.run, b)));
  const drop = document.getElementById("drop"), file = document.getElementById("file");
  file.onchange = () => file.files[0] && uploadFile(file.files[0]);
  ["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
  drop.addEventListener("drop", (e) => e.dataTransfer.files[0] && uploadFile(e.dataTransfer.files[0]));
  await loadQueue();
  S.poll = setInterval(loadQueue, 4000);
}

async function loadQueue() {
  const el = document.getElementById("queue");
  if (!el) return;
  let cases;
  try { cases = await api("/api/cases"); } catch (e) { el.innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
  document.getElementById("qsub").textContent = `${cases.length} case${cases.length === 1 ? "" : "s"}`;
  if (!cases.length) { el.innerHTML = `<div class="empty">No cases yet. Pick demo document <b>C</b> to see a critical clause buried on page 9 caught and routed.</div>`; return; }
  el.innerHTML = `<table class="queue"><thead><tr><th>Status</th><th>Document</th><th class="hide-sm">Value</th><th>Risk</th><th class="hide-sm">Recommended action</th><th class="hide-sm">Updated</th></tr></thead><tbody>
    ${cases.map((c) => `<tr class="click" data-id="${c.id}"><td>${statusPill(c.status)}</td>
      <td><b>${esc(c.vendor || c.filename)}</b><div class="sum">${esc(c.summary || c.filename)}</div></td>
      <td class="hide-sm">${money(c.value, c.currency)}</td>
      <td>${c.riskLevel ? `<span class="sev ${c.riskLevel}">${c.riskScore} · ${c.riskLevel}</span>` : "—"}</td>
      <td class="hide-sm small">${esc(ACTION_TEXT[c.recommendedAction] || "—")}</td><td class="hide-sm small muted">${ago(c.updatedAt)}</td></tr>`).join("")}
    </tbody></table>`;
  el.querySelectorAll("tr[data-id]").forEach((tr) => (tr.onclick = () => (location.hash = `#/case/${tr.dataset.id}`)));
}

const statusPill = (s) => `<span class="pill ${s}">${s === "PROCESSING" ? '<i class="spin"></i>' : ""}${esc(label(s))}</span>`;

async function runSample(key, btn) {
  if (btn) btn.disabled = true;
  try {
    const c = await api("/api/cases", { method: "POST", json: { sample: key } });
    location.hash = `#/case/${c.id}`;
    startProcessing(c.id);
  } catch (e) { toast(e.message); if (btn) btn.disabled = false; }
}

async function uploadFile(f) {
  if (f.type && f.type !== "application/pdf") return toast("Please choose a PDF file");
  const fd = new FormData(); fd.append("file", f);
  toast(`Uploading ${f.name}…`);
  try {
    const c = await api("/api/cases/upload", { method: "POST", body: fd });
    location.hash = `#/case/${c.id}`;
    startProcessing(c.id);
  } catch (e) { toast(e.message); }
}

function startProcessing(id) {
  if (running.has(id)) return;
  running.add(id);
  S.seen = new Set();
  api(`/api/cases/${id}/process`, { method: "POST" })
    .then(() => refreshCase(id))
    .catch((e) => { if (!/504|timeout|gateway/i.test(e.message)) toast("Processing error: " + e.message); refreshCase(id); })
    .finally(() => running.delete(id));
}

// ------------------------------------------------------------------ workspace
async function openCase(id) {
  S.caseId = id; S.case = null; S.trace = []; S.lastSig = ""; S.verify = null; S.mock = null; S.audit = null;
  if (S.pdfFor !== id) { S.pdfFor = null; S.pages = {}; }
  $app.innerHTML = `<div class="ws-head" id="ws-head"><a href="#/">← Queue</a></div>
    <div class="ws">
      <section class="card panel" id="p-doc"><div class="card-h"><h2>Document</h2><span class="sub" id="doc-sub"></span></div>
        <div class="card-b" style="padding:8px 12px;border-bottom:1px solid var(--border)" id="ev-chips"></div>
        <div class="pdfwrap" id="pdf"><div class="center-msg">Loading PDF…</div></div></section>
      <section class="card panel flow-panel" id="p-reason"><div class="card-h"><h2>Agent reasoning</h2><span class="sub">live trace and grounded findings</span></div><div class="card-b" id="reason"></div></section>
      <section class="card panel" id="p-gate"><div class="card-h"><h2>Action gateway</h2><span class="sub">human-in-the-loop</span></div><div class="card-b" id="gate"></div></section>
    </div>`;
  await refreshCase(id);
  if (!S.case) return;
  loadPdf(id);
  if (CFG.realtime && fb) subscribe(id);
  else S.poll = setInterval(() => { if (!S.case || ["UPLOADED", "PROCESSING"].includes(S.case.status) || running.has(id)) refreshCase(id); }, 1000);
}

function subscribe(id) {
  const { fsM, db } = fb;
  const fallback = (e) => { console.warn("Firestore listener failed, polling instead", e); if (!S.poll) S.poll = setInterval(() => refreshCase(id), 1200); };
  S.unsub.push(fsM.onSnapshot(fsM.doc(db, "cases", id), (snap) => {
    if (!snap.exists() || S.caseId !== id) return;
    S.case = { ...S.case, ...snap.data() };
    render();
  }, fallback));
  S.unsub.push(fsM.onSnapshot(fsM.query(fsM.collection(db, "cases", id, "trace"), fsM.orderBy("ts")), (qs) => {
    if (S.caseId !== id) return;
    S.trace = qs.docs.map((d) => d.data());
    render();
  }, fallback));
}

async function refreshCase(id) {
  try {
    const c = await api(`/api/cases/${id}`);
    if (S.caseId !== id) return;
    S.trace = c.trace || S.trace;
    S.case = c;
    if (c.status === "EXECUTED" && !S.mock) loadMock(id);
    render();
  } catch (e) {
    $app.innerHTML = `<div class="center-msg">${esc(e.message)}<br><br><a href="#/">Back to queue</a></div>`;
    cleanup(); S.case = null;
  }
}

function render() {
  const c = S.case;
  if (!c) return;
  const sig = JSON.stringify([c.status, c.updatedAt, c.riskScore, S.trace.length, !!c.extraction, !!c.audit, !!c.proposal, c.auditRows, S.verify, S.mock && S.mock.erp.length]);
  if (sig === S.lastSig) return;
  S.lastSig = sig;
  renderHead(c); renderReason(c); renderGate(c); drawHighlights();
}

function renderHead(c) {
  const lat = c.latencyMs && c.latencyMs.total ? ` · end-to-end ${(c.latencyMs.total / 1000).toFixed(1)}s` : "";
  document.getElementById("ws-head").innerHTML = `<a href="#/">← Queue</a>
    <h1>${esc(c.vendor || c.filename)}</h1>${statusPill(c.status)}
    ${c.riskLevel ? `<span class="sev ${c.riskLevel}">RISK ${c.riskScore} · ${c.riskLevel}</span>` : ""}
    <span class="meta">${esc(c.id)} · ${esc(c.filename)}${lat}</span>`;
}

const STAGES = [["Extraction", "Extraction"], ["PolicyAudit", "Policy audit"], ["Rules", "Rules"], ["ActionDispatch", "Action draft"]];
function renderReason(c) {
  const el = document.getElementById("reason");
  if (!el) return;
  const seenAgents = new Set(S.trace.map((t) => t.agent));
  const done = ["PROPOSED", "APPROVED", "EXECUTED", "REJECTED"].includes(c.status);
  let activeIdx = -1;
  STAGES.forEach(([k], i) => { if (seenAgents.has(k)) activeIdx = i; });
  const steps = STAGES.map(([k, n], i) => `<div class="step ${done || i < activeIdx ? "done" : i === activeIdx && c.status === "PROCESSING" ? "on" : ""}">${done || i < activeIdx ? "✓ " : ""}${n}</div>`).join("");
  const t0 = S.trace.length ? S.trace[0].ts : 0;
  const trace = S.trace.map((t) => {
    const key = `${t.id}`; const isNew = !S.seen.has(key); S.seen.add(key);
    return `<li class="${isNew && c.status === "PROCESSING" ? "new" : ""}"><span class="t">+${((t.ts - t0) / 1000).toFixed(1)}s</span><span class="ag ${esc(t.agent)}">${esc(t.agent)}</span><span>${esc(t.message)}</span></li>`;
  }).join("");
  let html = `<div class="steps">${steps}</div><ul class="trace" id="trace">${trace || `<li><span></span><span></span><span class="muted">${c.status === "UPLOADED" ? "Queued - starting agents…" : "Waiting for agents…"}</span></li>`}</ul>`;

  if (c.status === "FAILED") html += `<div class="alert crit" style="margin-top:12px">Case failed: ${esc(c.error)}</div>`;
  const x = c.extraction;
  if (x) {
    if (x.suspicious_instructions && x.suspicious_instructions.length)
      html += `<div class="alert crit" style="margin-top:12px"><b>Prompt-injection attempt detected.</b> The document contains text addressed to AI reviewers; it was treated as data, not instructions:<br><i>"${esc(x.suspicious_instructions[0].slice(0, 240))}"</i></div>`;
    html += `<div class="section-t">Extracted terms <span class="muted" style="text-transform:none;letter-spacing:0;font-weight:500">- confidence ${(x.extraction_confidence * 100).toFixed(0)}% (quotes verified on the PDF text layer)</span></div>`;
    const ev = (f) => (x.evidence || []).find((e) => e.field === f);
    const row = (k, v, f) => { const e = ev(f); return `<span class="k">${k}</span><span class="v">${esc(v ?? "—")}</span><span>${e ? `<a href="#" data-page="${e.page}" class="small">p.${e.page}</a> ${e.verified ? '<span class="ok" title="Quote verified on the PDF text layer">✓</span>' : '<span class="warn" title="Read visually (scanned or not machine-verifiable)">◐</span>'}` : (x.missing_fields || []).includes(f) ? '<span class="small warn">missing</span>' : ""}</span>`; };
    const r = x.risk_clauses, fi = x.financial;
    html += `<div class="kv">${row("Document type", label(x.document_type), "document_type")}${row("Vendor", x.vendor.name, "vendor_name")}
      ${row("Total value", money(fi.total_value, fi.currency), "total_value")}${row("Payment terms", fi.payment_terms_days != null ? `Net ${fi.payment_terms_days}` : null, "payment_terms_days")}
      ${row("Liability cap", r.liability_cap, "liability_cap")}${row("Auto-renewal", r.auto_renewal == null ? null : r.auto_renewal ? `Yes, ${r.auto_renewal_notice_days ?? "?"} days notice` : "No", "auto_renewal")}
      ${row("Warranty", r.warranty_period_days != null ? `${r.warranty_period_days} days` : null, "warranty_period_days")}${row("Governing law", r.governing_law, "governing_law")}</div>`;
    if (x.missing_fields && x.missing_fields.length) html += `<div class="alert warn" style="margin-top:10px">Not stated in the document (left empty, never guessed): ${x.missing_fields.map(esc).join(", ")}</div>`;
  }
  const a = c.audit;
  if (a) {
    const fs = [...a.findings].sort((p, q) => SEV_RANK[q.severity] - SEV_RANK[p.severity]);
    html += `<div class="section-t">Policy findings · contract clause vs company policy <span class="muted" style="text-transform:none;letter-spacing:0;font-weight:500">- retriever: ${esc(a.retriever)}${a.rejected_findings.length ? `, ${a.rejected_findings.length} ungrounded finding(s) dropped` : ""}</span></div>`;
    html += fs.map(findingCard).join("");
  }
  el.innerHTML = html;
  el.querySelectorAll("[data-page]").forEach((l) => (l.onclick = (e) => { e.preventDefault(); gotoPage(+l.dataset.page); }));
  const tr = document.getElementById("trace");
  if (tr && c.status === "PROCESSING") tr.scrollTop = tr.scrollHeight;
}

function findingCard(f) {
  const verdict = f.clause_category === "SPEND_APPROVAL" ? `<span class="sev NONE">APPROVAL BAND</span>` :
    f.violation_detected ? `<span class="sev ${f.severity}">${f.severity}</span>` : f.needs_review ? `<span class="sev LOW">REVIEW</span>` : `<span class="sev NONE">COMPLIANT</span>`;
  const pol = f.policy_source && f.policy_source.split(",")[0];
  return `<div class="finding ${f.clause_category === "SPEND_APPROVAL" ? "NONE" : f.severity}">
    <div class="fh"><span class="cat">${esc(label(f.clause_category))}</span>${f.analyst_override ? `<span class="small muted">edited ${esc(f.analyst_override)}</span>` : ""}${verdict}</div>
    <div class="cols"><div class="col"><div class="lbl">Contract clause</div><div><b>${esc(f.extracted_term)}</b></div>
        ${f.contract_quote ? `<blockquote>"${esc(f.contract_quote)}"</blockquote>` : ""}${f.contract_page ? `<a href="#" data-page="${f.contract_page}" class="small">Show on page ${f.contract_page}</a>` : ""}</div>
      <div class="col"><div class="lbl">Company policy</div><div><b>${esc(f.policy_standard)}</b></div>
        ${f.policy_snippet ? `<blockquote>"${esc(f.policy_snippet)}"</blockquote>` : ""}
        <div class="src">${pol && pol.endsWith(".pdf") ? `<a href="/api/policies/${encodeURIComponent(pol)}" target="_blank" rel="noopener">${esc(f.policy_source)}</a>` : esc(f.policy_source)}${f.retrieval_id ? ` · <code>${esc(f.retrieval_id)}</code>` : ""}</div></div></div>
    <div class="why">${esc(f.reasoning)}${f.guard_note ? `<div class="guard">⚑ ${esc(f.guard_note)}</div>` : ""}</div></div>`;
}

function renderGate(c) {
  const el = document.getElementById("gate");
  if (!el) return;
  if (["UPLOADED", "PROCESSING"].includes(c.status)) {
    el.innerHTML = `<div class="alert info">Agents are drafting a proposal. Nothing can be executed until a human approves it.</div>
      <div class="muted small">The draft appears here as soon as the Action Dispatch agent finishes.</div>`;
    return;
  }
  if (c.status === "FAILED") {
    el.innerHTML = `<div class="alert crit">${esc(c.error || "Processing failed")}</div><button class="btn primary block" id="retry">Retry processing</button>`;
    document.getElementById("retry").onclick = () => startProcessing(c.id);
    return;
  }
  const p = c.proposal, a = c.audit;
  if (!p) { el.innerHTML = `<div class="muted">No proposal.</div>`; return; }
  const committed = c.status === "EXECUTED", rejected = c.status === "REJECTED";
  const banner = committed ? `<div class="uncommitted committed">✓ EXECUTED · approved by ${esc((c.approvedBy || {}).name || "analyst")}</div>`
    : rejected ? `<div class="uncommitted" style="color:var(--crit);background:var(--crit-bg);border-color:var(--crit)">✕ REJECTED · no system was changed</div>`
    : `<div class="uncommitted">● UNCOMMITTED DRAFT · nothing has been written to ERP or Jira</div>`;
  const tasks = p.tasks.map((t) => {
    const pr = t.parameters;
    const body = t.target_system === "MOCK_JIRA" ? `<div><b>${esc(pr.summary)}</b></div><div class="muted small">${esc(pr.assigned_group)} · priority ${esc(pr.priority)}</div><div class="small" style="margin-top:4px">${esc(pr.description)}</div>`
      : t.action_type === "REQUEST_APPROVAL" ? `<div class="small">Approvers: <b>${esc((pr.approvers || []).join(" → "))}</b></div>`
      : `<div class="small">${esc(pr.vendor)} · ${money(pr.amount, pr.currency)} · Net ${esc(pr.payment_terms_days ?? "?")}</div><div class="small">ERP status on execute: <b class="${pr.status === "READY_TO_POST" ? "ok" : "bad"}">${esc(pr.status)}</b></div>`;
    return `<div class="task"><div class="th"><span class="sys">${t.target_system}</span>${esc(label(t.action_type))}</div>${body}</div>`;
  }).join("");
  let html = `${banner}
    <div class="risk"><div class="num" style="color:${riskColor(c.riskLevel)}">${c.riskScore}</div><div style="flex:1"><div class="small muted">Risk score (computed by rules, not the model) · ${esc(a.overall_status.replace(/_/g, " "))}</div>
      <div class="meter"><i style="width:${c.riskScore}%;background:${riskColor(c.riskLevel)}"></i></div></div></div>
    <div class="small muted">Recommended action</div><div class="action-name">${esc(ACTION_TEXT[p.recommended_action] || p.recommended_action)}</div>
    <div class="route"><span class="small muted">Approval route:</span>${p.approval_route.map((r) => `<span class="r">${esc(r)}</span>`).join('<span class="muted">→</span>')}</div>
    ${p.case_summary ? `<div class="small" style="margin-bottom:10px"><b>Supervisor:</b> ${esc(p.case_summary)}</div>` : ""}
    <div class="section-t" style="margin-top:4px">Prepared tasks (${p.tasks.length})</div>${tasks}
    <div class="section-t">Rationale</div><div class="rationale">${esc(p.rationale)}</div>`;
  if (c.status === "PROPOSED") {
    html += `<div class="gate"><div class="who">Signing as <b>${esc(userName())}</b> · your decision is recorded in the audit chain</div>
      <button class="btn primary block" id="approve">Approve and execute</button>
      <div class="row"><button class="btn" id="edit">Edit / override</button><button class="btn danger" id="reject">Reject</button></div></div>`;
  }
  if (committed && S.mock) {
    html += `<div class="section-t">Mock enterprise systems (after approval)</div>` +
      S.mock.erp.map((r) => `<div class="rec"><span class="sys">MOCK_ERP</span> ${esc(label(r.type))} ${r.po_number ? `· <code>${esc(r.po_number)}</code>` : ""}<div>Status: <span class="st ${r.status === "READY_TO_POST" ? "ok" : r.status === "BLOCKED_ON_EXCEPTION" ? "bad" : "warn"}">${esc(r.status)}</span>${r.approvers ? ` · ${esc(r.approvers.join(" → "))}` : ""}</div></div>`).join("") +
      S.mock.jira.map((r) => `<div class="rec"><span class="sys">MOCK_JIRA</span> <code>${esc(r.key)}</code> · ${esc(r.priority)}<div>${esc(r.summary)}</div><div class="muted small">${esc(r.assigned_group)} · ${esc(r.status)}</div></div>`).join("");
  }
  if (p.edits && p.edits.length) html += `<div class="section-t">Analyst edits</div>` + p.edits.map((e) => `<div class="rec small"><b>${esc(e.by)}</b>: ${esc(e.note || "overrides")} · risk ${e.before.risk_score} → ${e.after.risk_score} · ${esc(label(e.before.recommended_action))} → ${esc(label(e.after.recommended_action))}</div>`).join("");
  html += `<div class="section-t">Tamper-evident audit trail</div>
    <div class="small muted">${c.auditRows || 0} hash-chained rows (prompts, citations, decisions)${CFG.realtime ? " stored in Firestore" : ""}.</div>
    <div class="row" style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:8px"><button class="btn sm" id="verify">Verify audit chain</button><button class="btn sm" id="tamper" title="Recomputes the chain over an in-memory copy with one row altered. Stored data is not modified.">Simulate tampering</button></div>`;
  if (S.verify) {
    const v = S.verify;
    html += `<div class="alert ${v.valid ? "info" : "crit"}" style="margin-top:10px">${v.valid ? `✓ Chain valid - ${v.rows} rows recomputed, head matches the case record.` : `✕ Chain broken at row ${v.firstBadRow}: ${esc(v.reason)}${v.simulated ? " (simulation: stored rows untouched)" : ""}`}</div>
      <div class="chain">${v.chain.map((r) => `<div class="${!v.valid && r.seq >= v.firstBadRow ? "bad" : ""}"><span>${r.seq}</span><span>${esc(r.event_type)} · ${esc(r.actor.slice(0, 22))} · ${esc(r.prev_hash)}→${esc(r.row_hash)}</span></div>`).join("")}</div>`;
  }
  el.innerHTML = html;
  const on = (id, fn) => { const b = document.getElementById(id); if (b) b.onclick = fn; };
  on("approve", () => confirmApprove(c));
  on("reject", () => modal("Reject this case?", `<p class="small">No ERP entry or ticket will be created. The rejection is recorded with your identity.</p><label class="small">Reason<textarea id="rnote" rows="3" style="width:100%"></textarea></label>`,
    [{ text: "Reject case", cls: "danger", fn: async (root) => { await api(`/api/cases/${c.id}/reject`, { method: "POST", json: { note: root.querySelector("#rnote").value } }); await refreshCase(c.id); } }]));
  on("edit", () => editModal(c));
  on("verify", () => verify(c.id, null));
  on("tamper", () => verify(c.id, Math.min(2, c.auditRows || 1)));
}

function confirmApprove(c) {
  const n = c.proposal.tasks.length;
  modal("Approve and execute?", `<p class="small">This will execute <b>${n} prepared task${n > 1 ? "s" : ""}</b> against the mock ERP and Jira systems and record your sign-off (<b>${esc(userName())}</b>) in the hash-chained audit log.</p>`,
    [{ text: "Approve and execute", cls: "primary", fn: async () => {
      await api(`/api/cases/${c.id}/approve`, { method: "POST", json: { note: "" } });
      await loadMock(c.id); await refreshCase(c.id); toast("Executed. Mock ERP and Jira updated.");
    } }]);
}

function editModal(c) {
  const fs = c.audit.findings.filter((f) => f.clause_category !== "SPEND_APPROVAL");
  const opts = (cur) => ["NONE", "LOW", "MODERATE", "HIGH", "CRITICAL"].map((s) => `<option ${s === cur ? "selected" : ""} value="${s}">${s === "NONE" ? "NONE (waive)" : s}</option>`).join("");
  const pt = c.extraction.financial.payment_terms_days;
  modal("Edit draft before approval", `<div class="fields">
      <p class="small muted" style="margin:0">Overrides are applied by the deterministic rules engine; risk score and routing are recomputed and the edit is recorded in the audit chain.</p>
      <label>Negotiated payment terms (days)<input type="number" id="e-pt" min="0" max="180" value="${pt ?? ""}" /></label>
      ${fs.map((f) => `<div class="frow"><span>${esc(label(f.clause_category))} <span class="muted">(${esc(f.extracted_term)})</span></span><select data-fid="${f.finding_id}" data-cur="${f.severity}">${opts(f.severity)}</select></div>`).join("")}
      <label>Justification<textarea id="e-note" rows="2" placeholder="e.g. Vendor agreed to Net 45 by email on 16 Oct"></textarea></label></div>`,
    [{ text: "Apply edits", cls: "primary", fn: async (root) => {
      const ov = {};
      root.querySelectorAll("select[data-fid]").forEach((s) => { if (s.value !== s.dataset.cur) ov[s.dataset.fid] = s.value; });
      const v = root.querySelector("#e-pt").value;
      const body = { severity_overrides: ov, note: root.querySelector("#e-note").value };
      if (v !== "" && +v !== pt) body.payment_terms_days = +v;
      if (!Object.keys(ov).length && body.payment_terms_days === undefined) { toast("No changes"); return false; }
      await api(`/api/cases/${c.id}/edit`, { method: "POST", json: body });
      await refreshCase(c.id); toast("Draft updated and re-scored");
    } }]);
}

async function verify(id, tamper) {
  try {
    S.verify = await api(`/api/cases/${id}/audit/verify${tamper ? `?simulate_tamper=${tamper}` : ""}`);
    S.lastSig = ""; render();
  } catch (e) { toast(e.message); }
}

async function loadMock(id) {
  try { S.mock = await api(`/api/cases/${id}/mock-status`); S.lastSig = ""; render(); } catch (e) { console.warn(e); }
}

// ------------------------------------------------------------------ PDF viewer with clause highlights
async function loadPdf(id) {
  const box = document.getElementById("pdf");
  try {
    window.pdfjsLib.GlobalWorkerOptions.workerSrc = PDFJS_WORKER;
    const buf = await (await api(`/api/cases/${id}/document`, { raw: true })).arrayBuffer();
    if (S.caseId !== id) return;
    const pdf = await window.pdfjsLib.getDocument({ data: buf }).promise;
    box.innerHTML = "";
    document.getElementById("doc-sub").textContent = `${pdf.numPages} page${pdf.numPages > 1 ? "s" : ""}`;
    const width = Math.max(320, box.clientWidth - 24);
    S.pages = {}; S.pdfFor = id;
    for (let i = 1; i <= pdf.numPages; i++) {
      const page = await pdf.getPage(i);
      const vp = page.getViewport({ scale: width / page.getViewport({ scale: 1 }).width });
      const dpr = window.devicePixelRatio || 1;
      const wrap = document.createElement("div");
      wrap.className = "pdfpage"; wrap.id = `pg-${i}`; wrap.style.width = `${vp.width}px`; wrap.style.height = `${vp.height}px`;
      const cv = document.createElement("canvas");
      cv.width = vp.width * dpr; cv.height = vp.height * dpr; cv.style.width = `${vp.width}px`; cv.style.height = `${vp.height}px`;
      wrap.appendChild(cv);
      wrap.insertAdjacentHTML("beforeend", `<span class="pno">${i}</span>`);
      box.appendChild(wrap);
      await page.render({ canvasContext: cv.getContext("2d"), viewport: vp, transform: dpr !== 1 ? [dpr, 0, 0, dpr, 0, 0] : null }).promise;
      const tc = await page.getTextContent();
      S.pages[i] = { vp, items: tc.items, el: wrap };
      if (S.caseId !== id) return;
    }
    drawHighlights();
  } catch (e) {
    box.innerHTML = `<div class="center-msg">Could not render PDF: ${esc(e.message)}</div>`;
  }
}

function sevForField(field) {
  const cat = FIELD_CAT[field];
  const f = S.case && S.case.audit && S.case.audit.findings.find((x) => x.clause_category === cat);
  if (!f) return "LOW";
  return f.violation_detected ? f.severity : "NONE";
}

function drawHighlights() {
  const c = S.case;
  if (!c || !c.extraction || !Object.keys(S.pages).length) return;
  document.querySelectorAll(".hl").forEach((h) => h.remove());
  const marks = (c.extraction.evidence || []).map((e) => ({ page: e.page, quote: e.quote, sev: sevForField(e.field), field: e.field }));
  for (const m of marks) {
    const pg = S.pages[m.page];
    if (!pg) continue;
    let joined = ""; const spans = [];
    pg.items.forEach((it, idx) => { const n = norm(it.str); if (!n) return; if (joined) joined += " "; spans.push([joined.length, joined.length + n.length, idx]); joined += n; });
    const q = norm(m.quote);
    let pos = joined.indexOf(q), len = q.length;
    if (pos < 0 && q.length > 40) { const head = q.slice(0, 40); pos = joined.indexOf(head); len = head.length; }
    if (pos < 0) continue;
    for (const [s, e, idx] of spans) {
      if (e <= pos || s >= pos + len) continue;
      const it = pg.items[idx];
      const tx = window.pdfjsLib.Util.transform(pg.vp.transform, it.transform);
      const fh = Math.hypot(tx[2], tx[3]);
      const d = document.createElement("div");
      d.className = `hl ${m.sev}`;
      d.title = `${m.field}`;
      Object.assign(d.style, { left: `${tx[4] - 1}px`, top: `${tx[5] - fh}px`, width: `${it.width * pg.vp.scale + 2}px`, height: `${fh * 1.2}px` });
      pg.el.appendChild(d);
    }
  }
  const chips = document.getElementById("ev-chips");
  if (chips) {
    const ev = [...(c.extraction.evidence || [])].sort((a, b) => SEV_RANK[sevForField(b.field)] - SEV_RANK[sevForField(a.field)]);
    chips.innerHTML = `<span class="small muted">Evidence:</span> ` + ev.map((e) => `<a href="#" class="small" data-pg="${e.page}" style="margin-right:8px;white-space:nowrap"><span class="sev ${sevForField(e.field)}" style="padding:0 5px">${esc(label(e.field))}</span> p.${e.page}</a>`).join("");
    chips.querySelectorAll("[data-pg]").forEach((a) => (a.onclick = (ev2) => { ev2.preventDefault(); gotoPage(+a.dataset.pg); }));
  }
}

function gotoPage(n) {
  const el = document.getElementById(`pg-${n}`);
  const box = document.getElementById("pdf");
  if (!el || !box) return;
  box.scrollTo({ top: el.offsetTop - box.offsetTop - 8, behavior: "smooth" });
  if (window.innerWidth < 1280) el.scrollIntoView({ behavior: "smooth", block: "start" });
  el.classList.remove("flash"); void el.offsetWidth; el.classList.add("flash");
}

boot().catch((e) => { $app.innerHTML = `<div class="center-msg">Failed to start: ${esc(e.message)}</div>`; });
