/* Shared helpers + state-driven router (REQUIREMENTS.md section 12).
 * On load and every 2s, GET /api/state and show the SETUP wizard or the RUNTIME
 * view, switching automatically when the engine state changes. */
(function () {
  "use strict";

  const App = {
    view: null,         // currently shown top-level view: 'SETUP' | 'RUNNING' | 'STOPPING'
  };
  window.App = App;

  // ---- fetch helpers: always resolve to { ok, status, data } -----------------
  async function request(method, url, opts = {}) {
    try {
      const res = await fetch(url, { method, ...opts });
      let data = null;
      const ct = res.headers.get("content-type") || "";
      if (ct.includes("application/json")) data = await res.json();
      return { ok: res.ok, status: res.status, data };
    } catch (e) {
      return { ok: false, status: 0, data: { error: String(e) } };
    }
  }
  App.getJSON = (url) => request("GET", url);
  App.postJSON = (url, body) =>
    request("POST", url, {
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
  App.postForm = (url, file, field = "file") => {
    const fd = new FormData();
    fd.append(field, file, file.name);
    return request("POST", url, { body: fd });
  };

  // ---- toast notifications ---------------------------------------------------
  App.toast = function (message, type = "success") {
    const el = document.createElement("div");
    el.className = `toast align-items-center text-bg-${type} border-0 show`;
    el.innerHTML =
      `<div class="d-flex"><div class="toast-body">${escapeHtml(message)}</div>` +
      `<button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast"></button></div>`;
    document.getElementById("toasts").appendChild(el);
    setTimeout(() => el.remove(), 4000);
  };

  App.escapeHtml = escapeHtml;
  function escapeHtml(s) {
    return String(s ?? "").replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  function showView(name) {
    document.getElementById("setup-view").style.display = name === "setup" ? "" : "none";
    document.getElementById("runtime-view").style.display = name === "runtime" ? "" : "none";
  }

  // ---- 2s state poll / router ------------------------------------------------
  async function tick() {
    const r = await App.getJSON("/api/state");
    const banner = document.getElementById("conn-banner");
    if (!r.ok) { banner.style.display = "block"; return; }
    banner.style.display = "none";

    const state = r.data.state;
    if (state === App.view) return;          // no transition; views self-refresh
    if (App.view === "RUNNING" && window.Runtime) window.Runtime.leave();
    App.view = state;
    if (state === "SETUP") { showView("setup"); window.Setup.enter(); }
    else if (state === "RUNNING") { showView("runtime"); window.Runtime.enter(); }
    else if (state === "STOPPING") {
      showView("setup");
      document.getElementById("setup-body").innerHTML =
        '<div class="text-center py-5"><div class="spinner-border mb-3"></div>' +
        "<p>Engine is shutting down. It will restart in SETUP state.</p></div>";
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    tick();
    setInterval(tick, 2000);
  });
})();
