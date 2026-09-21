/* Meridian Freight — minimal coordinator review UI (localStorage only; no send). */

const STORAGE_KEY = "meridian_claims_review_v1";
const API_KEY_SESSION = "meridian_claims_review_api_key";

function loadReviewState() {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}");
  } catch {
    return {};
  }
}

function saveReviewState(state) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
}

function getClaimState(emailId) {
  const all = loadReviewState();
  return all[emailId] || { status: "pending", draftOverride: null, note: null };
}

function setClaimState(emailId, patch) {
  const all = loadReviewState();
  all[emailId] = { ...getClaimState(emailId), ...patch, updatedAt: new Date().toISOString() };
  saveReviewState(all);
}

function esc(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function badge(text, kind) {
  return `<span class="badge ${kind || "neutral"}">${esc(text)}</span>`;
}

function kv(label, value) {
  return `<div class="kv"><dt>${esc(label)}</dt><dd>${value == null || value === "" ? "—" : esc(value)}</dd></div>`;
}

function listOrNone(items) {
  if (!items || !items.length) return `<p class="muted">None</p>`;
  return `<ul class="clean">${items.map((x) => `<li>${esc(typeof x === "string" ? x : JSON.stringify(x))}</li>`).join("")}</ul>`;
}

function fmtMs(ms) {
  if (ms == null || ms === "") return null;
  const n = Number(ms);
  if (!Number.isFinite(n)) return null;
  if (n >= 1000) return `${(n / 1000).toFixed(n >= 10000 ? 1 : 2)}s`;
  return `${Math.round(n)}ms`;
}

function fmtTokens(n) {
  if (n == null || n === "") return null;
  const v = Number(n);
  if (!Number.isFinite(v)) return null;
  return v.toLocaleString();
}

function fmtUsd(n) {
  if (n == null || n === "") return null;
  const v = Number(n);
  if (!Number.isFinite(v)) return null;
  if (v === 0) return "$0";
  if (v < 0.01) return `$${v.toFixed(4)}`;
  return `$${v.toFixed(3)}`;
}

/** Pull latency / tokens / cost from packet usage + decision (either may be present). */
function evalMetrics(p) {
  const usage = p.usage || {};
  const decision = p.decision || {};
  const input =
    usage.input_tokens != null ? usage.input_tokens : decision.model_input_tokens;
  const output =
    usage.output_tokens != null ? usage.output_tokens : decision.model_output_tokens;
  const modelLatency =
    usage.latency_ms != null ? usage.latency_ms : decision.model_latency_ms;
  const processing =
    decision.processing_duration_ms != null
      ? decision.processing_duration_ms
      : p.processing_duration_ms;
  const cost =
    usage.estimated_total_cost_usd != null
      ? usage.estimated_total_cost_usd
      : decision.estimated_total_cost_usd;
  const modelCalled =
    decision.model_called === true ||
    (p.llm_status === "ok" && input != null) ||
    p.model_called === true;
  return {
    processing_ms: processing,
    model_latency_ms: modelLatency,
    input_tokens: input,
    output_tokens: output,
    total_tokens:
      input != null || output != null ? (Number(input) || 0) + (Number(output) || 0) : null,
    estimated_total_cost_usd: cost,
    estimated_input_cost_usd:
      usage.estimated_input_cost_usd != null
        ? usage.estimated_input_cost_usd
        : decision.estimated_input_cost_usd,
    estimated_output_cost_usd:
      usage.estimated_output_cost_usd != null
        ? usage.estimated_output_cost_usd
        : decision.estimated_output_cost_usd,
    model: usage.model || decision.model_name || null,
    model_called: modelCalled,
    llm_status: p.llm_status || decision.model_call_status || null,
    pricing_note: usage.pricing_note || null,
  };
}

function queueEvalLine(p) {
  const m = evalMetrics(p);
  const parts = [];
  const wall = fmtMs(m.processing_ms);
  if (wall) parts.push(wall);
  if (m.model_called && m.input_tokens != null) {
    const inT = fmtTokens(m.input_tokens);
    const outT = fmtTokens(m.output_tokens);
    parts.push(`${inT}→${outT} tok`);
  } else if (m.llm_status && m.llm_status !== "ok") {
    parts.push(String(m.llm_status).replace(/_/g, " "));
  }
  const usd = fmtUsd(m.estimated_total_cost_usd);
  if (usd && m.model_called) parts.push(`~${usd}`);
  return parts.length ? parts.join(" · ") : "no model metrics";
}

function renderEvalSection(p) {
  const m = evalMetrics(p);
  const note =
    m.pricing_note ||
    "Estimated model cost from token usage × configured USD/MTok rates — not an invoice.";
  return `
    <section class="section eval">
      <h3>7 · Latency &amp; tokens</h3>
      <div class="source-tag">OBSERVABILITY · measured this run</div>
      <div class="grid">
        ${kv("Pipeline duration", fmtMs(m.processing_ms) || "—")}
        ${kv("Model latency", fmtMs(m.model_latency_ms) || (m.model_called ? "—" : "no model call"))}
        ${kv("Input tokens", fmtTokens(m.input_tokens) || "—")}
        ${kv("Output tokens", fmtTokens(m.output_tokens) || "—")}
        ${kv("Total tokens", fmtTokens(m.total_tokens) || "—")}
        ${kv("Est. cost (total)", fmtUsd(m.estimated_total_cost_usd) || "—")}
        ${kv("Est. cost (in / out)", `${fmtUsd(m.estimated_input_cost_usd) || "—"} / ${fmtUsd(m.estimated_output_cost_usd) || "—"}`)}
        ${kv("Model", m.model || "—")}
        ${kv("LLM status", m.llm_status || "—")}
        ${kv("Model called", m.model_called ? "yes" : "no")}
      </div>
      <p class="muted small">${esc(note)}</p>
    </section>
  `;
}

let packets = [];
let emails = [];
let selectedId = null;
let processing = false;

function setLiveStatus(message, kind) {
  const el = document.getElementById("liveStatus");
  const banner = document.getElementById("liveBanner");
  el.textContent = message || "";
  if (!message) {
    banner.classList.add("hidden");
    banner.textContent = "";
    return;
  }
  banner.classList.remove("hidden", "busy", "ok", "err");
  if (kind) banner.classList.add(kind);
  banner.textContent = message;
}

function isDryRun() {
  return document.getElementById("dryRunToggle").checked;
}

function getUiApiKey() {
  const el = document.getElementById("apiKeyInput");
  return (el && el.value ? el.value : "").trim();
}

function persistApiKeyFromInput() {
  const key = getUiApiKey();
  try {
    if (key) sessionStorage.setItem(API_KEY_SESSION, key);
    else sessionStorage.removeItem(API_KEY_SESSION);
  } catch {
    /* ignore quota / private mode */
  }
}

function restoreApiKeyInput() {
  const el = document.getElementById("apiKeyInput");
  if (!el) return;
  try {
    const saved = sessionStorage.getItem(API_KEY_SESSION) || "";
    if (saved && !el.value) el.value = saved;
  } catch {
    /* ignore */
  }
}

async function refreshApiConfig() {
  const hint = document.getElementById("apiKeyHint");
  try {
    const res = await fetch("/api/config");
    const data = await res.json();
    if (data.has_env_api_key) {
      hint.textContent =
        "Server has ANTHROPIC_API_KEY in env/.env. Leave blank to use it, or paste to override for this session.";
    } else {
      hint.textContent =
        "No ANTHROPIC_API_KEY in server env — paste a key here before live Process.";
    }
  } catch (err) {
    hint.textContent = `Could not check env key: ${err.message}`;
  }
}

function setProcessing(busy) {
  processing = busy;
  const processBtn = document.getElementById("processBtn");
  const rerun = document.getElementById("rerunBtn");
  processBtn.disabled = busy;
  if (rerun) rerun.disabled = busy;
}

async function refreshEmails() {
  const select = document.getElementById("emailSelect");
  try {
    const res = await fetch("/api/emails");
    const data = await res.json();
    emails = data.emails || [];
    const prev = select.value;
    select.innerHTML = "";
    if (!emails.length) {
      select.innerHTML = `<option value="">No sample .eml files</option>`;
      return;
    }
    for (const e of emails) {
      const opt = document.createElement("option");
      opt.value = e.email_id;
      opt.textContent = e.has_packet
        ? `${e.email_id} (has packet)`
        : `${e.email_id} (new)`;
      select.appendChild(opt);
    }
    if (prev && emails.some((e) => e.email_id === prev)) select.value = prev;
    else if (selectedId && emails.some((e) => e.email_id === selectedId)) {
      select.value = selectedId;
    }
  } catch (err) {
    select.innerHTML = `<option value="">Failed to load emails</option>`;
    setLiveStatus(`Failed to list emails: ${err.message}`, "err");
  }
}

async function processEmail(emailId, { preferSelect = false } = {}) {
  if (!emailId) {
    setLiveStatus("Pick an email id first.", "err");
    return;
  }
  if (processing) return;

  const dryRun = isDryRun();
  persistApiKeyFromInput();
  const apiKey = getUiApiKey();
  setProcessing(true);
  setLiveStatus(
    dryRun
      ? `Processing ${emailId} (dry-run)…`
      : apiKey
        ? `Processing ${emailId} live (UI API key)…`
        : `Processing ${emailId} live (env API key)…`,
    "busy"
  );

  try {
    const body = { email_id: emailId, dry_run: dryRun };
    if (!dryRun && apiKey) body.api_key = apiKey;
    const res = await fetch("/api/process", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    if (!res.ok || !data.ok) {
      setLiveStatus(data.error || `Process failed (${res.status})`, "err");
      return;
    }
    const m = evalMetrics(data.packet || data);
    const wall = fmtMs(m.processing_ms) || "—";
    const tok =
      m.input_tokens != null
        ? `${fmtTokens(m.input_tokens)}→${fmtTokens(m.output_tokens)} tok`
        : "no model tokens";
    const src = data.api_key_source && data.api_key_source !== "n/a"
      ? ` · key=${data.api_key_source}`
      : "";
    setLiveStatus(
      `Processed ${data.email_id} · ${data.mode}${src} · ${wall} · ${tok}`,
      "ok"
    );
    selectedId = data.email_id;
    if (preferSelect) {
      const select = document.getElementById("emailSelect");
      if (select) select.value = data.email_id;
    }
    await refreshEmails();
    await refreshQueue();
  } catch (err) {
    setLiveStatus(`Process failed: ${err.message}`, "err");
  } finally {
    setProcessing(false);
  }
}

async function refreshQueue() {
  const status = document.getElementById("queueStatus");
  status.textContent = "Loading…";
  try {
    const res = await fetch("/api/packets");
    const data = await res.json();
    packets = data.packets || [];
    status.textContent = `${packets.length} packet(s) in output/`;
    renderQueue();
    if (selectedId) {
      const still = packets.find((p) => p.email_id === selectedId);
      if (still) await selectClaim(selectedId);
      else {
        selectedId = null;
        document.getElementById("emptyState").classList.remove("hidden");
        document.getElementById("detailContent").classList.add("hidden");
      }
    }
  } catch (err) {
    status.textContent = `Failed to load packets: ${err.message}`;
  }
}

function renderQueue() {
  const ul = document.getElementById("queue");
  ul.innerHTML = "";
  if (!packets.length) {
    ul.innerHTML = `<li class="muted small">No ActionPacket JSON found. Run process/eval first.</li>`;
    return;
  }
  for (const p of packets) {
    const review = getClaimState(p.email_id);
    const li = document.createElement("li");
    const load = p.load_number || "unresolved";
    const escalate = p.needs_human ? badge("escalate", "warn") : badge("ok", "ok");
    const statusBadge =
      review.status === "accepted"
        ? badge("accepted", "ok")
        : review.status === "rejected"
          ? badge("rejected", "danger")
          : badge("pending", "neutral");
    li.innerHTML = `
      <button type="button" data-id="${esc(p.email_id)}" class="${p.email_id === selectedId ? "active" : ""}">
        <div class="q-title"><span>${esc(load)}</span><span>${esc(p.email_id)}</span></div>
        <div class="q-meta">
          ${esc(p.claim_type || "—")}
          · conf ${p.confidence == null ? "—" : Number(p.confidence).toFixed(2)}
          · ${esc(p.resolution_status || "—")}
        </div>
        <div class="q-meta q-eval">${esc(queueEvalLine(p))}</div>
        <div class="q-meta" style="margin-top:6px">${statusBadge} ${escalate}</div>
      </button>`;
    li.querySelector("button").addEventListener("click", () => selectClaim(p.email_id));
    ul.appendChild(li);
  }
}

async function selectClaim(emailId) {
  selectedId = emailId;
  renderQueue();
  const empty = document.getElementById("emptyState");
  const content = document.getElementById("detailContent");
  empty.classList.add("hidden");
  content.classList.remove("hidden");
  content.innerHTML = `<p class="muted">Loading ${esc(emailId)}…</p>`;

  try {
    const res = await fetch(`/api/packets/${encodeURIComponent(emailId)}`);
    const packet = await res.json();
    if (packet.error) {
      content.innerHTML = `<div class="section review"><h3>Error</h3><p>${esc(packet.error)}</p></div>`;
      return;
    }
    content.innerHTML = renderDetail(packet);
    wireDetailActions(packet);
  } catch (err) {
    content.innerHTML = `<div class="section review"><h3>Error</h3><p>${esc(err.message)}</p></div>`;
  }
}

function renderDetail(p) {
  const review = getClaimState(p.email_id);
  const analysis = p.analysis || {};
  const fp = analysis.freightpro_facts || {};
  const email = analysis.email_facts || {};
  const pod = analysis.pod_facts || {};
  const comparisons = analysis.what_is_inconsistent || {};
  const unknowns = analysis.what_is_still_unknown || [];
  const aiWrap = (analysis.what_ai_suggests || {}).interpretation || p.interpretation || {};
  const draftBlock = (analysis.what_ai_suggests || {}).draft_response || {};
  const human = analysis.why_human_review || {};
  const resolution = p.resolution || {};
  const load = resolution.load || {};
  const ids = p.identifiers || email.identifiers || {};
  const draftText =
    review.draftOverride != null
      ? review.draftOverride
      : draftBlock.text || p.draft_reply || "";

  const loadNo = load.LoadNumber || ids.load_number || "—";

  return `
    <div class="detail-head">
      <h1>${esc(loadNo)} <span class="muted">· ${esc(p.email_id)}</span></h1>
      <div>
        ${badge(resolution.status || "unknown", resolution.status === "resolved" ? "ok" : "warn")}
        ${p.needs_human ? badge("human review", "warn") : ""}
        ${badge(review.status || "pending", review.status === "accepted" ? "ok" : review.status === "rejected" ? "danger" : "neutral")}
      </div>
    </div>

    <section class="section eval">
      <h3>Live re-run</h3>
      <div class="source-tag">Runs the pipeline now · writes output/${esc(p.email_id)}.json</div>
      <div class="actions">
        <button type="button" class="btn primary" id="rerunBtn">Re-run pipeline</button>
      </div>
      <p class="muted small">Uses the sidebar Dry-run toggle and API key (UI override or server env). Still never sends email. Key is never stored in the ActionPacket.</p>
    </section>

    <section class="section facts">
      <h3>1 · Load</h3>
      <div class="source-tag">FACTS · resolution + FreightPro</div>
      <div class="grid">
        ${kv("Load number", load.LoadNumber || ids.load_number)}
        ${kv("Shipper", load.shipper_name || fp.shipper_name)}
        ${kv("Carrier", load.carrier_legal_name || fp.carrier_legal_name)}
        ${kv("Resolution method", resolution.method)}
        ${kv("Resolution status", resolution.status)}
        ${kv("Confidence", resolution.confidence)}
        ${kv("Shipper corroboration", resolution.shipper_corroboration)}
      </div>
      ${resolution.reason ? `<p class="muted small">${esc(resolution.reason)}</p>` : ""}
      ${(resolution.candidates || []).length ? `<p class="muted small">Candidates: ${esc((resolution.candidates || []).map((c) => c.LoadNumber).join(", "))}</p>` : ""}
    </section>

    <section class="section facts">
      <h3>2 · Claim</h3>
      <div class="source-tag">FACTS · email / classification</div>
      <div class="grid">
        ${kv("Claim type", (p.classification || {}).claim_type || aiWrap.claim_type)}
        ${kv("Confidence", (p.classification || {}).confidence)}
        ${kv("Source", (p.classification || {}).source)}
        ${kv("Subject", p.subject)}
        ${kv("PO", ids.po)}
        ${kv("BOL", ids.bol)}
        ${kv("PRO", ids.pro)}
      </div>
      <p><strong>Alleged facts</strong></p>
      <div class="pre">${esc((p.classification || {}).alleged_facts || "")}</div>
    </section>

    <section class="section facts">
      <h3>3 · FreightPro facts</h3>
      <div class="source-tag">Source: FreightPro snapshot</div>
      <div class="grid">
        ${kv("Available", fp.available)}
        ${kv("Snapshot date", fp.snapshot_date)}
        ${kv("Recorded status", fp.status_recorded || fp.status)}
        ${kv("Lane", fp.lane || load.lane)}
        ${kv("PODReceived", fp.pod_received || load.PODReceived)}
        ${kv("Commodity", fp.commodity || load.Commodity)}
        ${kv("Carrier MC", fp.carrier_mc || load.carrier_mc)}
        ${kv("Carrier SCAC", fp.carrier_scac || load.carrier_scac)}
      </div>
      ${fp.freshness_note ? `<p class="muted small">${esc(fp.freshness_note)}</p>` : ""}
    </section>

    <section class="section facts">
      <h3>4 · POD evidence</h3>
      <div class="source-tag">FACTS · attachment / extraction</div>
      <div class="grid">
        ${kv("Filename", (p.pod || {}).filename || pod.filename)}
        ${kv("Mode", (p.pod || {}).mode || pod.extraction_method)}
        ${kv("Status", (p.pod || {}).status || pod.status)}
        ${kv("Available", pod.available)}
      </div>
      <p class="muted small">${esc((p.pod || {}).findings || pod.unavailable_reason || "")}</p>
      <p><strong>Extracted text (redacted)</strong></p>
      <div class="pre">${esc((p.pod || {}).excerpt || pod.excerpt_redacted || "(none)")}</div>
      ${(p.attachments && (p.attachments.pod_candidates || []).length > 1) ? `<p class="muted small">Multiple POD candidates — escalate.</p>` : ""}
    </section>

    <section class="section disc">
      <h3>5 · Discrepancies</h3>
      <div class="source-tag">DISCREPANCIES · deterministic</div>
      ${listOrNone(p.discrepancies || comparisons.discrepancies || [])}
    </section>

    <section class="section unk">
      <h3>6 · Unknowns</h3>
      <div class="source-tag">UNKNOWNS · not established</div>
      ${
        unknowns.length
          ? `<ul class="clean">${unknowns
              .map(
                (u) =>
                  `<li><code>${esc(u.field)}</code> — ${esc(u.reason || "")}${
                    u.requires_human ? " <em>(requires human)</em>" : ""
                  }</li>`
              )
              .join("")}</ul>`
          : `<p class="muted">None listed</p>`
      }
    </section>

    ${renderEvalSection(p)}

    <section class="section ai">
      <h3>8 · AI interpretation</h3>
      <div class="source-tag">AI-GENERATED · suggestion only</div>
      <div class="grid">
        ${kv("LLM status", p.llm_status || aiWrap.status)}
        ${kv("Claim type", aiWrap.claim_type)}
        ${kv("Confidence", aiWrap.confidence)}
        ${kv("Source", aiWrap.source)}
      </div>
      <p>${esc(aiWrap.summary || p.summary || "—")}</p>
      ${p.llm_error ? `<p class="muted small">Error: ${esc(p.llm_error)}</p>` : ""}
      <p class="muted small">${esc(aiWrap.note || "Interpretation only — not a source of FreightPro/POD facts.")}</p>
    </section>

    <section class="section draft">
      <h3>9 · Draft response</h3>
      <div class="source-tag">DRAFT · AI-generated text requiring human review</div>
      <textarea class="draft" id="draftArea">${esc(draftText)}</textarea>
      <p class="muted small">Edits stay in this browser (localStorage). Original ActionPacket JSON is not modified. Nothing is sent.</p>
    </section>

    <section class="section review">
      <h3>10 · Human review</h3>
      <div class="source-tag">Required reasons</div>
      ${listOrNone(human.reasons || p.needs_human_reasons || [])}
      <div class="actions">
        <button type="button" class="btn primary" id="acceptBtn">Accept Draft</button>
        <button type="button" class="btn danger" id="rejectBtn">Reject Draft</button>
      </div>
      <div id="reviewToast"></div>
      ${
        review.note
          ? `<div class="toast ${review.status === "accepted" ? "ok" : "warn"}">${esc(review.note)}</div>`
          : ""
      }
    </section>
  `;
}

function wireDetailActions(packet) {
  const area = document.getElementById("draftArea");
  const toast = document.getElementById("reviewToast");
  const rerun = document.getElementById("rerunBtn");

  if (rerun) {
    rerun.addEventListener("click", () => processEmail(packet.email_id));
  }

  document.getElementById("acceptBtn").addEventListener("click", () => {
    setClaimState(packet.email_id, {
      status: "accepted",
      draftOverride: area.value,
      note: "Draft accepted by reviewer. No email sent.",
    });
    toast.innerHTML = `<div class="toast ok">Draft accepted by reviewer. No email sent.</div>`;
    renderQueue();
  });

  document.getElementById("rejectBtn").addEventListener("click", () => {
    setClaimState(packet.email_id, {
      status: "rejected",
      draftOverride: area.value,
      note: "Draft rejected by reviewer.",
    });
    toast.innerHTML = `<div class="toast warn">Draft rejected by reviewer.</div>`;
    renderQueue();
  });

  area.addEventListener("change", () => {
    setClaimState(packet.email_id, { draftOverride: area.value });
  });
}

document.getElementById("refreshBtn").addEventListener("click", async () => {
  await refreshApiConfig();
  await refreshEmails();
  await refreshQueue();
});
document.getElementById("processBtn").addEventListener("click", () => {
  const emailId = document.getElementById("emailSelect").value;
  processEmail(emailId, { preferSelect: true });
});
document.getElementById("apiKeyInput").addEventListener("change", persistApiKeyFromInput);
document.getElementById("apiKeyInput").addEventListener("blur", persistApiKeyFromInput);

restoreApiKeyInput();
refreshApiConfig()
  .then(refreshEmails)
  .then(refreshQueue);
