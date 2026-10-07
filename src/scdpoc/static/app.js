/* Structured Clinical Document PoC - demonstrator UI (vanilla JS, no build chain). */
"use strict";

const STEPS = ["Patient", "Compose IPS", "Validate", "Render", "Package", "PDF/A validation",
  "Publish (XDS)", "Discover & retrieve", "Exchange projection", "EHDS preview", "Replace & tamper"];
const S = { key: null, patient: null, draft: null, pkg: null, pub: null };

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const h = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const chip = (text, kind) => `<span class="chip ${kind}">${h(text)}</span>`;
const okChip = (ok, yes = "pass", no = "fail") => chip(ok ? yes : no, ok ? "ok" : "bad");
const short = (s, n = 12) => (s && s.length > n * 2 ? `${s.slice(0, n)}…${s.slice(-6)}` : s);
const pre = (title, text, open = false) => `<details${open ? " open" : ""}><summary>${h(title)}</summary><pre>${h(text)}</pre></details>`;
const kv = (rows) => `<dl class="kv">${rows.map(([k, v]) => `<dt>${h(k)}</dt><dd>${v}</dd>`).join("")}</dl>`;

function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(toast.t);
  toast.t = setTimeout(() => t.classList.remove("show"), 3500);
}

async function api(method, path) {
  const r = await fetch(path, { method, headers: { Accept: "application/json" } });
  const text = await r.text();
  let body;
  try { body = JSON.parse(text); } catch { body = text; }
  if (!r.ok) throw new Error((body && body.detail) || `${r.status} ${r.statusText}`);
  return body;
}

function mark(step, state) {
  const sec = $(`#s${step}`);
  const li = $(`#rail li[data-step="${step}"]`);
  for (const el of [sec, li]) {
    if (!el) continue;
    el.classList.remove("done", "failed");
    if (state) el.classList.add(state);
  }
}

function enable(acts, on = true) {
  for (const a of acts) $$(`[data-act="${a}"]`).forEach((b) => (b.disabled = !on));
}

async function busy(btn, fn) {
  if (btn) { btn.classList.add("busy"); btn.disabled = true; }
  try { return await fn(); }
  catch (e) { toast(e.message); console.error(e); return null; }
  finally { if (btn) { btn.classList.remove("busy"); btn.disabled = false; } }
}

/* ------------------------------------------------------------------ init */
async function init() {
  $("#rail").innerHTML = STEPS.map((s, i) => `<li data-step="${i + 1}">${i + 1 < 11 ? i + 1 + ". " : "+ "}${h(s)}</li>`).join("");
  $$("#rail li").forEach((li) => li.addEventListener("click", () => $(`#s${li.dataset.step}`).scrollIntoView({ behavior: "smooth", block: "start" })));
  const info = await api("GET", "/api/info");
  $("#version").textContent = `v${info.version}`;
  const v = info.validators;
  $("#validators").innerHTML = [
    ["HL7 FHIR validator", v.hl7FhirValidator ? chip("configured", "ok") : chip("not configured - pre-flight only", "warn")],
    ["veraPDF", v.verapdf ? chip("configured", "ok") : chip("not configured - conformance not claimed", "warn")],
    ["IPS package", `<code>${h(info.standards.ips.package)}#${h(info.standards.ips.version)}</code>`],
  ].map(([k, d]) => `<div><dt>${h(k)}</dt><dd>${d}</dd></div>`).join("");
  await loadPatients();
  $$("[data-act]").forEach((b) => b.addEventListener("click", () => ACTIONS[b.dataset.act](b)));
  $("#run-all").addEventListener("click", (e) => runAll(e.currentTarget));
}

async function loadPatients() {
  const pts = await api("GET", "/api/demo/patients");
  $("#patients").innerHTML = pts.map((p) => `
    <button class="patient" data-key="${h(p.key)}" aria-pressed="${p.key === S.key}">
      <strong>${h(p.name)}</strong><span class="pid">${h(p.identifier)} · born ${h(p.birthDate)}</span>
      <p>${h(p.scenario)}</p>
      ${p.issuedVersions ? chip(`issued v${p.currentVersion}`, "info") : chip("not yet issued", "muted")}
      ${p.hasRevision ? chip("has revision scenario", "muted") : ""}
    </button>`).join("");
  $$(".patient").forEach((b) => b.addEventListener("click", () => selectPatient(b.dataset.key)));
}

async function selectPatient(key) {
  S.key = key; S.draft = S.pkg = S.pub = null;
  $$(".patient").forEach((b) => b.setAttribute("aria-pressed", b.dataset.key === key));
  for (let i = 2; i <= 11; i++) { $(`#o${i}`).innerHTML = ""; mark(i, null); }
  const p = await api("GET", `/api/demo/patients/${key}`);
  S.patient = p;
  const src = p.source;
  const list = (title, rows, cols) => rows && rows.length
    ? `<h3>${h(title)}</h3><div class="scroll"><table class="t"><thead><tr>${cols.map((c) => `<th>${h(c[0])}</th>`).join("")}</tr></thead><tbody>${rows.map((r) => `<tr>${cols.map((c) => `<td>${h(c[1](r))}</td>`).join("")}</tr>`).join("")}</tbody></table></div>` : "";
  $("#o1").innerHTML = `
    <div class="callout warn"><strong>Synthetic record.</strong> Loaded from <code>fixtures/synthetic-patients/${h(key)}.json</code> and checked by the synthetic-data guard (prefix, namespace, and scans for real-identifier patterns).</div>
    ${kv([["Name", h(`${src.patient.given.join(" ")} ${src.patient.family}`)], ["Identifier", `<code>${h(src.patient.identifier)}</code> in <code>urn:oid:2.999.1.1</code>`], ["Born", h(src.patient.birthDate)], ["Issued versions", h(p.issuances.length)]])}
    ${list("Problems (SNOMED CT)", src.problems, [["Problem", (r) => r.display], ["Code", (r) => r.snomed], ["Onset", (r) => r.onset]])}
    ${src.allergies_none_known ? `<h3>Allergies</h3><p>No known allergies (recorded explicitly).</p>` : list("Allergies (SNOMED CT)", src.allergies, [["Substance", (r) => r.display], ["Code", (r) => r.snomed], ["Criticality", (r) => r.criticality]])}
    ${list("Medications (ATC)", src.medications, [["Medicine", (r) => r.display], ["ATC", (r) => r.atc], ["Dosage", (r) => r.dosage]])}
    ${pre("Source fixture JSON", JSON.stringify(src, null, 2))}`;
  mark(1, "done");
  enable(["compose"]);
  enable(["package", "publish"], false);
  const issued = p.issuances.length > 0;
  enable(["discover", "ehds", "tamper-embedded", "tamper-outer", "ondemand"], issued);
  enable(["replace"], issued && !!src.revision);
  $("#run-all").disabled = false;
  if (issued) await renderHistory();
}

/* --------------------------------------------------------------- steps */
async function compose(btn, revise = false) {
  const d = await busy(btn, () => api("POST", `/api/demo/compose/${S.key}${revise ? "?revise=true" : ""}`));
  if (!d) return null;
  S.draft = d; S.pkg = S.pub = null;
  for (let i = 5; i <= 10; i++) { $(`#o${i}`).innerHTML = ""; mark(i, null); }
  $("#o2").innerHTML = `
    ${kv([["Document", `<code>${h(d.documentUrn)}</code>`], ["Version", `${h(d.version)}${d.replaces ? ` - replaces <code>${h(short(d.replaces, 18))}</code>` : ""}`], ["Issued", h(d.issued)], ["Series", `<code>${h(d.seriesId)}</code>`], ["IPS bytes", `${h(d.ips.bytes)} · SHA-256 <span class="hash">${h(d.ips.sha256)}</span>`]])}
    <h3>Composition sections</h3>
    <div class="scroll"><table class="t"><thead><tr><th>Section</th><th>LOINC</th><th>Entries</th><th>Empty reason</th></tr></thead><tbody>
    ${d.sections.map((s) => `<tr><td>${h(s.title)}</td><td><code>${h(s.code)}</code></td><td>${h(s.entries)}</td><td>${h(s.emptyReason || "")}</td></tr>`).join("")}</tbody></table></div>
    <div class="grid2"><div><h3>Profiles claimed</h3>${d.profiles.map((p) => `<div><code>${h(p.split("/").pop())}</code></div>`).join("")}</div>
    <div><h3>Terminology systems</h3>${d.terminology.map((t) => `<div><code>${h(t)}</code></div>`).join("")}</div></div>
    <div class="links"><a href="/api/demo/drafts/${h(d.draftId)}/ips.json" target="_blank" rel="noopener">Open exact IPS bytes</a></div>
    ${pre("FHIR IPS document Bundle (exact bytes to be validated, embedded and exchanged)", d.bundle)}`;
  mark(2, "done");

  const v = d.validation;
  $("#o3").innerHTML = `
    <div class="callout ${v.publishable ? "ok" : "bad"}"><strong>${v.publishable ? "Publication gate open" : "Publication blocked"}.</strong> ${h(v.gate)}</div>
    ${kv([["Pinned package", `<code>${h(v.package)}</code>`], ["Pre-flight digest", h(v.preflight_digest_source)]])}
    <h3>Engines</h3>
    <div class="scroll"><table class="t"><thead><tr><th>Engine</th><th>Status</th><th>Errors</th><th>Warnings</th><th>Detail</th></tr></thead><tbody>
    ${v.engines.map((e) => `<tr><td>${h(e.engine)}</td><td>${chip(e.status, e.status === "passed" ? "ok" : e.status === "not-run" ? "warn" : "bad")}</td><td>${h(e.errors)}</td><td>${h(e.warnings)}</td><td>${h(e.detail)}</td></tr>`).join("")}</tbody></table></div>
    ${issueTable(v.engines.flatMap((e) => e.issues))}`;
  mark(3, v.publishable ? "done" : "failed");

  $("#o4").innerHTML = `<p class="links"><span>Deterministic HTML rendition from the validated Bundle (renderer <code>${h(d.rendererVersion)}</code>).</span><a href="/api/demo/drafts/${h(d.draftId)}/summary.html" target="_blank" rel="noopener">Open in new tab</a></p>
    <iframe class="frame" title="Rendered patient summary" sandbox src="/api/demo/drafts/${h(d.draftId)}/summary.html"></iframe>`;
  mark(4, "done");
  enable(["package"], v.publishable);
  $("#s2").scrollIntoView({ behavior: "smooth", block: "start" });
  return d;
}

function issueTable(issues) {
  const shown = issues.filter((i) => i.severity !== "information");
  const info = issues.length - shown.length;
  if (!issues.length) return `<p>No issues reported.</p>`;
  return `<h3>Issues</h3>${shown.length ? `<div class="scroll"><table class="t"><thead><tr><th>Severity</th><th>Rule</th><th>Location</th><th>Message</th></tr></thead><tbody>
    ${shown.map((i) => `<tr><td>${chip(i.severity, i.severity === "warning" ? "warn" : "bad")}</td><td><code>${h(i.rule)}</code></td><td><code>${h(i.location)}</code></td><td>${h(i.message)}</td></tr>`).join("")}</tbody></table></div>` : "<p>No errors or warnings.</p>"}
    ${info ? `<p style="font-size:13px;color:var(--muted)">${info} informational message(s) (e.g. profiles not claimed in meta.profile on resources validated through Bundle slicing).</p>` : ""}`;
}

async function pkg(btn) {
  if (!S.draft) return null;
  const p = await busy(btn, () => api("POST", `/api/demo/package/${S.draft.draftId}`));
  if (!p) return null;
  S.pkg = p;
  const c = p.checks;
  $("#o5").innerHTML = `
    ${kv([["Envelope", `<code>${h(p.envelopeName)}</code> · ${h(p.envelope.bytes)} bytes`], ["SHA-256 (envelope)", `<span class="hash">${h(p.envelope.sha256)}</span>`], ["SHA-1 (XDS hash slot)", `<span class="hash">${h(p.envelope.sha1)}</span>`], ["SHA-256 (embedded IPS)", `<span class="hash">${h(p.ipsSha256)}</span>`]])}
    <h3>Associated Files inventory</h3>
    <div class="scroll"><table class="t"><thead><tr><th>File</th><th>MIME</th><th>AFRelationship</th><th>Bytes</th><th>In /AF</th><th>In name tree</th></tr></thead><tbody>
    ${p.associatedFiles.map((f) => `<tr><td><code>${h(f.filename)}</code></td><td>${h(f.mime_type)}</td><td>${h(f.relationship)}</td><td>${h(f.size)}</td><td>${okChip(f.listed_in_af, "yes", "no")}</td><td>${okChip(f.listed_in_name_tree, "yes", "no")}</td></tr>`).join("")}</tbody></table></div>
    <div class="callout">Embedded content is part of the disclosure: authorisation applies to the whole container, not just the visible pages.</div>
    <h3>Fidelity checks</h3>
    ${kv([["Embedded IPS byte-identical to validated IPS", okChip(c.embeddedPayloadIdentical)], ["Every clinical fact visible on the PDF pages", okChip(!c.renditionMissingFacts.length) + (c.renditionMissingFacts.length ? ` ${h(c.renditionMissingFacts.join(", "))}` : "")]])}
    <div class="links"><a href="/api/demo/package/${h(p.packageId)}/envelope.pdf" target="_blank" rel="noopener">Open envelope PDF</a><a href="/api/demo/package/${h(p.packageId)}/envelope.pdf?download=true">Download</a></div>
    <iframe class="frame" title="PDF/A envelope" src="/api/demo/package/${h(p.packageId)}/envelope.pdf"></iframe>`;
  mark(5, c.embeddedPayloadIdentical && !c.renditionMissingFacts.length ? "done" : "failed");
  const vp = c.verapdf, pf = c.preflight;
  $("#o6").innerHTML = `
    <div class="callout ${p.publishable ? (vp.status === "passed" ? "ok" : "warn") : "bad"}"><strong>${p.publishable ? "Package gate open" : "Package gate closed"}.</strong> ${h(p.gate)}</div>
    <div class="grid2">
      <div><h3>veraPDF (independent validator)</h3>
        ${kv([["Status", chip(vp.status, vp.status === "passed" ? "ok" : vp.status === "not-run" ? "warn" : "bad")], ["Profile", h(vp.profile || "PDF/A-3B")], ["Detail", h(vp.detail)]])}
        ${vp.failed_rules.length ? `<div class="scroll"><table class="t"><thead><tr><th>Clause</th><th>Description</th></tr></thead><tbody>${vp.failed_rules.map((r) => `<tr><td>${h(r.clause)}-${h(r.test)}</td><td>${h(r.description)}</td></tr>`).join("")}</tbody></table></div>` : ""}
        ${vp.report_available ? `<div class="links"><a href="/api/demo/package/${h(p.packageId)}/verapdf-report.xml" target="_blank" rel="noopener">Machine-readable veraPDF report</a></div>` : ""}
      </div>
      <div><h3>Built-in pre-flight (subset, not conformance)</h3>
        <div class="scroll"><table class="t"><tbody>${pf.checks.map((x) => `<tr><td>${okChip(x.passed)}</td><td>${h(x.message)}</td></tr>`).join("")}</tbody></table></div>
      </div>
    </div>`;
  mark(6, p.publishable ? "done" : "failed");
  enable(["publish"], p.publishable);
  return p;
}

async function publish(btn) {
  if (!S.pkg) return null;
  const r = await busy(btn, () => api("POST", `/api/demo/publish/${S.pkg.packageId}`));
  if (!r) return null;
  S.pub = r;
  const i = r.issuance, m = r.metadata;
  const entryRows = (label, e, x) => `<tr><td>${h(label)}</td><td><code>${h(short(e.unique_id, 10))}</code></td><td>${h(e.mime_type)}</td><td><code>${h(e.codes.formatCode.code)}</code></td><td>${h(x)}</td></tr>`;
  $("#o7").innerHTML = `
    <div class="callout ok"><strong>Registered.</strong> Version ${h(i.version)} is now the current issuance${i.replaces ? `; it replaces <code>${h(short(i.replaces, 18))}</code> (RPLC)` : ""}.</div>
    <h3>DocumentEntries</h3>
    <div class="scroll"><table class="t"><thead><tr><th>Object</th><th>uniqueId</th><th>mimeType</th><th>formatCode</th><th>Associations</th></tr></thead><tbody>
      ${entryRows("PDF/A-3b envelope", m.envelope, i.replaces ? "HasMember, RPLC → previous envelope" : "HasMember")}
      ${entryRows("IPS projection", m.ips, "HasMember, XFRM → envelope")}
    </tbody></table></div>
    <div class="callout">Distinct format codes (finding F-006): the envelope uses a locally governed code; only the raw IPS carries <code>urn:ihe:pcc:ips:2020</code>, so a consumer asking for IPS never receives <code>application/pdf</code>.</div>
    ${kv([["Repository", `<code>${h(i.envelope.repositoryUniqueId)}</code>`], ["SubmissionSets", `<code>${h(short(i.envelope.submissionSet, 10))}</code>, <code>${h(short(i.ips.submissionSet, 10))}</code>`], ["Patient (CX)", `<code>${h(m.envelope.patient_id)}</code>`]])}
    ${r.exchanges.map((x, n) => pre(`${x.transaction} #${n + 1} request (SOAP 1.2 / MTOM root part)`, x.request_xml) + pre(`${x.transaction} #${n + 1} response`, x.response_xml)).join("")}`;
  mark(7, "done");
  enable(["discover", "ehds", "tamper-embedded", "tamper-outer", "ondemand"]);
  enable(["replace"], !!S.patient.source.revision);
  $("#o8").innerHTML = ""; $("#o9").innerHTML = ""; $("#o10").innerHTML = "";
  await renderHistory();
  loadPatients();
  return r;
}

async function discover(btn) {
  const [d, r] = await busy(btn, () => Promise.all([api("GET", `/api/demo/discover/${S.key}`), api("GET", `/api/demo/retrieve/${S.key}`)])) || [];
  if (!d) return null;
  const status = (s) => chip(s, /approved|current/i.test(s) ? "ok" : "muted");
  $("#o8").innerHTML = `
    <div class="callout ${d.equivalent ? "ok" : "bad"}"><strong>${d.equivalent ? "Equivalent discovery" : "Discovery mismatch"}.</strong> ITI-18 and ITI-67 return ${d.equivalent ? "the same" : "different"} registry entries for <code>${h(d.patientCX)}</code>.</div>
    <div class="grid2">
      <div><h3>XDS.b · ITI-18 FindDocuments (SOAP)</h3><div class="scroll"><table class="t"><thead><tr><th>Status</th><th>MIME</th><th>formatCode</th></tr></thead><tbody>
        ${d.xds.results.map((x) => `<tr><td>${status(x.status)}</td><td>${h(x.mimeType)}</td><td><code>${h(x.formatCode)}</code></td></tr>`).join("")}</tbody></table></div></div>
      <div><h3>MHD · ITI-67 DocumentReference (FHIR)</h3><div class="scroll"><table class="t"><thead><tr><th>Status</th><th>contentType</th><th>relatesTo</th></tr></thead><tbody>
        ${d.mhd.results.map((x) => `<tr><td>${status(x.status)}</td><td>${h(x.contentType)}</td><td>${h(x.relatesTo.map((r) => r.code).join(", "))}</td></tr>`).join("")}</tbody></table></div></div>
    </div>
    ${pre("ITI-18 request", d.xds.exchange.request_xml)}${pre("ITI-18 response", d.xds.exchange.response_xml)}
    ${pre(`ITI-67 GET ${d.mhd.url}`, JSON.stringify(d.mhd.bundle, null, 2))}
    <h3>Retrieve · ITI-43 and integrity</h3>
    ${kv([["ITI-43 envelope", `${h(r.xds.mimeType)} · ${h(r.xds.bytes)} bytes · ${okChip(r.xds.byteIdenticalToPackage, "byte-identical to issued package", "differs from issued package")}`]])}
    <div class="scroll"><table class="t"><thead><tr><th></th><th>Check</th><th>Expected</th><th>Actual</th></tr></thead><tbody>
      ${r.integrity.checks.map((c) => `<tr><td>${okChip(c.passed)}</td><td>${h(c.id)} ${h(c.label)}</td><td class="hash">${h(short(c.expected, 16))}</td><td class="hash">${h(short(c.actual, 16))}</td></tr>`).join("")}</tbody></table></div>
    ${pre("ITI-43 response (MTOM root part)", r.xds.exchange.response_xml)}`;
  mark(8, d.equivalent && r.integrity.passed ? "done" : "failed");
  $("#o9").innerHTML = `
    ${kv([["Transaction", h(r.mhd.transaction)], ["URL", `<a href="${h(r.mhd.url)}" target="_blank" rel="noopener"><code>${h(r.mhd.url)}</code></a>`], ["HTTP", h(r.mhd.httpStatus)], ["Content-Type", `<code>${h(r.mhd.contentType)}</code>`], ["Bytes", h(r.mhd.bytes)]])}
    <div class="callout ok">The computable IPS was obtained directly as <code>application/fhir+json</code>. Check IC-5 above proves it is byte-identical to the Associated File inside the preserved envelope.</div>
    ${pre("IPS document Bundle retrieved via ITI-68", r.mhd.ips)}`;
  mark(9, r.mhd.contentType.startsWith("application/fhir+json") ? "done" : "failed");
  return d;
}

async function ehds(btn) {
  const e = await busy(btn, () => api("GET", `/api/demo/ehds-preview/${S.key}`));
  if (!e) return null;
  const kind = { "demonstrated": "ok", "gap": "bad", "optional-absent": "muted", "unresolved": "warn", "out-of-scope": "info" };
  $("#o10").innerHTML = `
    <div class="callout warn"><strong>Non-normative.</strong> ${h(e.disclaimer)}</div>
    ${kv([["Document", `<code>${h(e.documentUrn)}</code>`], ["Input", h(e.source)], ["Register", `<code>config/ehds-readiness.yml</code> v${h(e.register_version)}`], ["Summary", Object.entries(e.summary).map(([k, n]) => chip(`${n} ${k}`, kind[k] || "muted")).join(" ")]])}
    <div class="scroll"><table class="t"><thead><tr><th>ID</th><th>Area</th><th>Requirement</th><th>Status</th><th>Evidence / note</th></tr></thead><tbody>
      ${e.items.map((i) => `<tr><td><code>${h(i.id)}</code></td><td class="nowrap">${h(i.area)}</td><td>${h(i.requirement)}</td><td>${chip(i.status, kind[i.status] || "muted")}</td><td>${h(i.evidence)}${i.note ? `<br><span style="color:var(--muted)">${h(i.note)}</span>` : ""}</td></tr>`).join("")}</tbody></table></div>`;
  mark(10, "done");
  return e;
}

async function replace(btn) {
  const d = await compose(btn, true);
  if (!d) return;
  const p = await pkg(null);
  if (!p || !p.publishable) return;
  await publish(null);
  toast(`Version ${d.version} issued; previous version deprecated, still retrievable.`);
  $("#s11").scrollIntoView({ behavior: "smooth", block: "start" });
}

async function tamper(btn, mode) {
  const t = await busy(btn, () => api("POST", `/api/demo/tamper/${S.key}?mode=${mode}`));
  if (!t) return;
  const failed = t.tampered.checks.filter((c) => !c.passed);
  $("#o11").innerHTML = `
    <div class="callout ${t.tampered.passed ? "bad" : "ok"}"><strong>${t.tampered.passed ? "Tampering NOT detected" : `Tampering detected by ${failed.length} check(s)`}.</strong> ${h(t.tampering)}. ${h(t.note)}</div>
    <div class="scroll"><table class="t"><thead><tr><th>Tampered copy</th><th>Stored original</th><th>Check</th><th>Actual (tampered)</th></tr></thead><tbody>
      ${t.tampered.checks.map((c, n) => `<tr><td>${okChip(c.passed)}</td><td>${okChip(t.storedOriginal.checks[n]?.passed)}</td><td>${h(c.id)} ${h(c.label)}</td><td class="hash">${h(short(c.actual, 16))}</td></tr>`).join("")}</tbody></table></div>
    <div id="history"></div>`;
  await renderHistory();
}

async function ondemand(btn) {
  const sys = "urn:oid:2.999.1.1";
  const id = S.patient.source.patient.identifier;
  const url = `/fhir/Patient/$summary?identifier=${encodeURIComponent(sys + "|" + id)}`;
  const b = await busy(btn, () => api("GET", url));
  if (!b) return;
  $("#o11").innerHTML = `
    <div class="callout warn"><strong>On-demand, not preserved.</strong> A fresh IPS composed now from current source data (new identifier <code>${h(b.identifier.value)}</code>, timestamp ${h(b.timestamp)}). It is tagged <code>${h(b.meta.tag[0].code)}</code>, is not registered, and must not be confused with the immutable issued snapshots below.</div>
    ${pre(`GET ${url}`, JSON.stringify(b, null, 2))}<div id="history"></div>`;
  await renderHistory();
}

async function renderHistory() {
  if (!S.key) return;
  const hst = await api("GET", `/api/demo/history/${S.key}`);
  let host = $("#history");
  if (!host) { $("#o11").insertAdjacentHTML("beforeend", `<div id="history"></div>`); host = $("#history"); }
  host.innerHTML = `<h3>Issued versions</h3><ul class="timeline">${hst.issuances.slice().reverse().map((i) => `
    <li><span class="v">v${h(i.version)}</span><div>
      ${chip(`envelope ${i.envelopeStatus}`, i.envelopeStatus === "Approved" ? "ok" : "muted")} ${chip(`IPS ${i.ipsStatus}`, i.ipsStatus === "Approved" ? "ok" : "muted")}
      <div>Issued ${h(i.issued)} · <code>${h(short(i.documentUrn, 18))}</code></div>
      <div style="color:var(--muted)">${i.associations.map(assocLabel(i)).map(h).join(" · ") || "no associations"}</div>
      <div class="links"><a href="/api/demo/evidence/${h(i.packageId)}" target="_blank" rel="noopener">Evidence record</a><a href="/api/demo/package/${h(i.packageId)}/envelope.pdf" target="_blank" rel="noopener">Envelope</a></div>
    </div></li>`).join("")}</ul>`;
}

function assocLabel(i) {
  return (a) => {
    const t = a.type.split(":").pop();
    const out = a.source === i.envelope.entryUUID;
    if (t === "RPLC") return out ? `replaces ${short(a.target, 14)} (RPLC)` : `replaced by ${short(a.source, 14)} (RPLC)`;
    if (t === "XFRM") return out ? `transforms ${short(a.target, 14)}` : `IPS projection ${short(a.source, 14)} (XFRM)`;
    return `${t} ${short(out ? a.target : a.source, 14)}`;
  };
}

const ACTIONS = {
  compose: (b) => compose(b),
  package: (b) => pkg(b),
  publish: (b) => publish(b),
  discover: (b) => discover(b),
  ehds: (b) => ehds(b),
  replace: (b) => replace(b),
  "tamper-embedded": (b) => tamper(b, "embedded"),
  "tamper-outer": (b) => tamper(b, "outer"),
  ondemand: (b) => ondemand(b),
};

async function runAll(btn) {
  if (!S.key) { toast("Choose a synthetic patient first"); return; }
  btn.classList.add("busy"); btn.disabled = true;
  try {
    if (!(await compose(null)) || !S.draft.validation.publishable) return;
    const p = await pkg(null);
    if (!p || !p.publishable) return;
    if (!(await publish(null))) return;
    await discover(null);
    await ehds(null);
    toast("Journey complete - every step's evidence is shown below.");
  } finally { btn.classList.remove("busy"); btn.disabled = false; }
}

init().catch((e) => toast(e.message));
