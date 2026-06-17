/* RIGForge Cockpit — vanilla SPA (no build step).
 * Talks to the FastAPI REST API and renders phases, gate/proof/signature
 * state, a verification summary, and a live SSE ledger feed. */
(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const tokenInput = $("token");

  // Persist the bearer token locally so a reload keeps the session.
  tokenInput.value = localStorage.getItem("rigforge_token") || "";
  tokenInput.addEventListener("change", () => {
    localStorage.setItem("rigforge_token", tokenInput.value.trim());
    boot();
  });

  function authHeaders() {
    const t = tokenInput.value.trim();
    return t ? { Authorization: "Bearer " + t } : {};
  }

  async function api(path, opts = {}) {
    const res = await fetch(path, {
      ...opts,
      headers: { "Content-Type": "application/json", ...authHeaders(), ...(opts.headers || {}) },
    });
    if (res.status === 401) {
      toast("Unauthorized — enter a valid bearer token", "bad");
      throw new Error("unauthorized");
    }
    if (!res.ok) {
      let detail = res.statusText;
      try { detail = (await res.json()).detail || detail; } catch (_e) {}
      throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    }
    return res.json();
  }

  let toastTimer = null;
  function toast(msg, kind) {
    const el = $("toast");
    el.textContent = msg;
    el.className = "toast show " + (kind || "");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { el.className = "toast"; }, 3200);
  }

  function badge(label, state) {
    return `<span class="badge ${state || ""}">${label}</span>`;
  }

  function sigBadge(p) {
    if (!p.sealed) return "";
    if (!p.signed) return badge("unsigned", "warn");
    if (p.signature_ok === true) return badge("signed ✓", "ok");
    if (p.signature_ok === false) return badge("signature FAIL", "bad");
    return badge("signed (no key)", "");
  }

  function renderPhases(phases) {
    const root = $("phases");
    root.innerHTML = "";
    phases.forEach((p) => {
      const row = document.createElement("div");
      row.className = "phase-row";
      const statusBadge = p.sealed
        ? badge("sealed", "ok")
        : badge("open", "");
      const integrity = p.sealed
        ? (p.integrity_ok === false ? badge("integrity FAIL", "bad") : badge("integrity ✓", "ok"))
        : "";
      row.innerHTML = `
        <div class="phase-num">${p.phase}</div>
        <div class="phase-main">
          <div class="phase-name">${escapeHtml(p.name)}</div>
          <div class="phase-meta">verifier: ${escapeHtml(p.verifier || "—")} · artifacts: ${p.artifact_count || 0}</div>
          <div class="badges">${statusBadge}${integrity}${sigBadge(p)}</div>
        </div>
        <div class="phase-actions">
          <button class="mini alt" data-run="${p.phase}">run</button>
          <button class="mini" data-seal="${p.phase}">seal</button>
          ${p.sealed ? `<button class="mini alt" data-proof="${p.phase}">proof</button>` : ""}
        </div>`;
      root.appendChild(row);
    });

    root.querySelectorAll("[data-run]").forEach((b) =>
      b.addEventListener("click", () => runPhase(b.getAttribute("data-run"))));
    root.querySelectorAll("[data-seal]").forEach((b) =>
      b.addEventListener("click", () => sealPhase(b.getAttribute("data-seal"))));
    root.querySelectorAll("[data-proof]").forEach((b) =>
      b.addEventListener("click", () => showProof(b.getAttribute("data-proof"))));
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  async function runPhase(n) {
    try {
      const r = await api(`/api/phases/${n}/run`, { method: "POST", body: "{}" });
      const blockers = (r.blockers || []).length;
      toast(blockers ? `Phase ${n}: ${blockers} blocking gate(s)` : `Phase ${n} ran clean ✓`,
        blockers ? "bad" : "ok");
      await boot();
    } catch (e) { toast("Run failed: " + e.message, "bad"); }
  }

  async function sealPhase(n) {
    try {
      const r = await api(`/api/phases/${n}/seal`, {
        method: "POST",
        body: JSON.stringify({ evidence: "sealed from cockpit" }),
      });
      toast(`Phase ${n} sealed · ${r.signed ? "signed" : "unsigned"} · ${r.packet_sha256.slice(0, 10)}…`, "ok");
      await boot();
    } catch (e) { toast("Seal blocked: " + e.message, "bad"); }
  }

  async function showProof(n) {
    try {
      const proof = await api(`/api/proof/${n}`);
      $("proof-phase").textContent = `#${n}`;
      $("proof-json").textContent = JSON.stringify(proof, null, 2);
      $("proof-panel").hidden = false;
      $("proof-panel").scrollIntoView({ behavior: "smooth" });
    } catch (e) { toast("Proof unavailable: " + e.message, "bad"); }
  }
  $("proof-close").addEventListener("click", () => { $("proof-panel").hidden = true; });

  async function renderSummary() {
    const [verify, contracts, phasesResp] = await Promise.all([
      api("/api/verify"), api("/api/contracts"), api("/api/phases"),
    ]);
    const phases = phasesResp.phases;
    const sealed = phases.filter((p) => p.sealed);
    const integrityOk = sealed.every((p) => p.integrity_ok !== false);
    const signed = sealed.filter((p) => p.signed).length;
    $("stat-sealed").textContent = `${sealed.length}/${phases.length}`;
    $("stat-integrity").textContent = sealed.length ? (integrityOk ? "✓" : "FAIL") : "—";
    $("stat-integrity").style.color = sealed.length && !integrityOk ? "var(--red)" : "";
    $("stat-signed").textContent = `${signed}/${sealed.length || 0}`;
    $("stat-verify").textContent = verify.ok ? "PASS" : "FAIL";
    $("stat-verify").style.color = verify.ok ? "var(--green)" : "var(--red)";
    $("stat-contracts").textContent = contracts.count;
    return phases;
  }

  async function boot() {
    try {
      const health = await api("/health");
      $("root-path").textContent = health.root;
      $("conn-dot").className = "dot live";
      const phases = await renderSummary();
      renderPhases(phases);
    } catch (e) {
      $("conn-dot").className = "dot down";
      if (e.message !== "unauthorized") toast("Cannot reach API: " + e.message, "bad");
    }
  }

  // ── Live ledger via SSE ──────────────────────────────────────────────
  let evtSource = null;
  function startLedger() {
    if (evtSource) evtSource.close();
    // EventSource cannot set headers; when a token is required the server
    // accepts it as a ?token= query param on this endpoint only.
    const t = tokenInput.value.trim();
    const url = "/api/ledger/stream?limit=20" + (t ? "&token=" + encodeURIComponent(t) : "");
    try {
      evtSource = new EventSource(url);
    } catch (_e) { $("ledger-status").textContent = "unavailable"; return; }
    const feed = $("ledger-feed");
    evtSource.onopen = () => { $("ledger-status").textContent = "live"; };
    evtSource.onmessage = (msg) => {
      let ev; try { ev = JSON.parse(msg.data); } catch (_e) { return; }
      if (!ev || !ev.kind) return;
      const li = document.createElement("li");
      const meta = Object.entries(ev)
        .filter(([k]) => !["ts", "kind", "actor", "event_id"].includes(k))
        .map(([k, v]) => `${k}=${v}`).join("  ");
      li.innerHTML = `<span class="ev-kind">${escapeHtml(ev.kind)}</span>` +
        `<span class="ev-meta">${escapeHtml((ev.actor || "?") + "  " + meta)}</span>`;
      feed.insertBefore(li, feed.firstChild);
      while (feed.children.length > 60) feed.removeChild(feed.lastChild);
    };
    evtSource.onerror = () => { $("ledger-status").textContent = "reconnecting…"; };
  }

  $("refresh").addEventListener("click", boot);
  boot();
  startLedger();
})();
