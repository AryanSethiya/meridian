/* Meridian Freight — minimal coordinator review UI (localStorage only; no send). */

const STORAGE_KEY = "meridian_claims_review_v1";

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

let packets = [];
let selectedId = null;

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

    <section class="section ai">
      <h3>7 · AI interpretation</h3>
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
      <h3>8 · Draft response</h3>
      <div class="source-tag">DRAFT · AI-generated text requiring human review</div>
      <textarea class="draft" id="draftArea">${esc(draftText)}</textarea>
      <p class="muted small">Edits stay in this browser (localStorage). Original ActionPacket JSON is not modified. Nothing is sent.</p>
    </section>

    <section class="section review">
      <h3>9 · Human review</h3>
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

document.getElementById("refreshBtn").addEventListener("click", refreshQueue);
refreshQueue();
