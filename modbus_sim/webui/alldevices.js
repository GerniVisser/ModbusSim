/* All Devices view — cross-device fuzzy search + bulk apply.
 *
 * Opened from the pinned "⊞ All Devices" entry in the device list. The user
 * fuzzy-searches variable NAMES across every device (server-side, so the browser
 * never holds all signals) and bulk-applies a simulation motion or a static value
 * to ALL matches at once. Both the live search and the apply send the same query
 * string; the server re-matches on apply, so the applied set == what was searched. */
(function () {
  "use strict";

  // Motion options for the bulk panel. A matched set can mix bools and numerics,
  // so the list is the union (toggle is bool-only, the rest are numeric).
  const MOTIONS = [
    ["", "(inherit default)"], ["static", "Static"], ["oscillate", "Oscillate"],
    ["sawtooth", "Ramp & reset"], ["triangle", "Sweep up/down"],
    ["step", "Step (staircase)"], ["toggle", "Toggle (bool)"],
  ];

  let _vl = null;            // VList over #rt-all-results
  let _wired = false;        // search bar / apply button wired once
  let _modal = null;         // bulk-apply Bootstrap modal
  let _debounce = null;

  let _query = "";           // last searched query
  let _matches = [];         // rows returned (capped at server limit)
  let _total = 0;            // true match count (may exceed _matches.length)
  let _truncated = false;

  const AllDevices = { enter, leave };
  window.AllDevices = AllDevices;

  function enter() {
    const panel = document.getElementById("rt-all-panel");
    if (panel) panel.style.display = "flex";
    if (!_vl) _vl = new VList(document.getElementById("rt-all-results"), 34, {});
    _wire();
    const inp = document.getElementById("rt-all-search");
    if (inp) {
      inp.value = _query;
      _query ? _search(_query) : _reset();
      inp.focus();
    }
  }

  function leave() {
    const panel = document.getElementById("rt-all-panel");
    if (panel) panel.style.display = "none";
  }

  function _wire() {
    if (_wired) return;
    _wired = true;
    const inp = document.getElementById("rt-all-search");
    inp.oninput = () => {
      clearTimeout(_debounce);
      const q = inp.value;
      _debounce = setTimeout(() => _search(q), 250);
    };
    document.getElementById("rt-all-apply").onclick = _openBulk;
  }

  // ── search ──────────────────────────────────────────────────────────────────
  function _reset() {
    _query = ""; _matches = []; _total = 0; _truncated = false;
    _vl.set([], _renderRow);
    _setCount("Type a variable name to search across all devices…");
    document.getElementById("rt-all-apply").disabled = true;
  }

  async function _search(q) {
    _query = q;
    if (!q.trim()) { _reset(); return; }
    const r = await App.getJSON("/api/signals/search?q=" + encodeURIComponent(q));
    if (!r.ok) { _setCount("Search failed"); return; }
    _matches = r.data.matches || [];
    _total = r.data.total || 0;
    _truncated = !!r.data.truncated;
    _vl.set(_matches.map((m) => ({ type: "row", ...m })), _renderRow);
    _updateCount();
    document.getElementById("rt-all-apply").disabled = _total === 0;
    document.getElementById("rt-all-apply").textContent =
      _total ? `Bulk apply to ${_total}…` : "Bulk apply…";
  }

  function _setCount(txt) { document.getElementById("rt-all-count").textContent = txt; }

  function _updateCount() {
    if (_total === 0) { _setCount("No variables match."); return; }
    const devs = new Set(_matches.map((m) => m.device_id)).size;
    _setCount(_truncated
      ? `Showing ${_matches.length} of ${_total} matches on ${devs}+ devices — bulk apply affects all ${_total}`
      : `${_total} variable${_total === 1 ? "" : "s"} on ${devs} device${devs === 1 ? "" : "s"}`);
  }

  function _renderRow(item) {
    const el = document.createElement("div");
    el.className = "all-row";
    const cell = (v) => `<div>${App.escapeHtml(v === null || v === undefined || v === "" ? "—" : String(v))}</div>`;
    el.innerHTML =
      `<div class="all-dev" title="${App.escapeHtml(item.device_id)}">${App.escapeHtml(item.device_name)}</div>` +
      `<div class="all-name" title="${App.escapeHtml(item.name)}">${App.escapeHtml(item.name)}</div>` +
      `<div class="all-type">${App.escapeHtml((item.register_type || "").replace("_", " "))}</div>` +
      cell(item.sim_mode) + cell(item.sim_min) + cell(item.sim_max) + cell(item.sim_period);
    return el;
  }

  // ── bulk apply modal ────────────────────────────────────────────────────────
  function _openBulk() {
    if (!_total) return;
    _ensureModal();
    document.getElementById("bk-count").textContent = String(_total);
    document.getElementById("bk-query").textContent = _query;
    _modal.show();
    _syncMode();
  }

  function _ensureModal() {
    if (document.getElementById("bulk-modal")) {
      _modal = _modal || new bootstrap.Modal(document.getElementById("bulk-modal"));
      return;
    }
    const w = document.createElement("div");
    w.innerHTML = `
<div class="modal fade" id="bulk-modal" tabindex="-1">
  <div class="modal-dialog modal-lg">
    <div class="modal-content">
      <div class="modal-header">
        <h5 class="modal-title">Bulk apply</h5>
        <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
      </div>
      <div class="modal-body">
        <p class="small mb-3">Applies to <strong id="bk-count" class="text-info"></strong> variables matching
          “<span id="bk-query" class="text-info"></span>” across all devices.</p>
        <div class="btn-group btn-group-sm w-100 mb-3" role="group">
          <input type="radio" class="btn-check" name="bk-op" id="bk-op-sim" value="sim" checked>
          <label class="btn btn-outline-secondary" for="bk-op-sim">Simulation / motion</label>
          <input type="radio" class="btn-check" name="bk-op" id="bk-op-val" value="val">
          <label class="btn btn-outline-secondary" for="bk-op-val">Static value</label>
        </div>

        <div id="bk-sim">
          <div class="row g-2 align-items-end">
            <div class="col-6 col-md-3"><label class="form-label small mb-1">Motion</label><select class="form-select form-select-sm" id="bk-mode"></select></div>
            <div class="col-6 col-md-2" id="bk-wrap-low"><label class="form-label small mb-1">Low</label><input class="form-control form-control-sm" type="number" step="any" id="bk-low"></div>
            <div class="col-6 col-md-2" id="bk-wrap-high"><label class="form-label small mb-1">High</label><input class="form-control form-control-sm" type="number" step="any" id="bk-high"></div>
            <div class="col-6 col-md-2" id="bk-wrap-period"><label class="form-label small mb-1" id="bk-lbl-period">Period (s)</label><input class="form-control form-control-sm" type="number" step="any" id="bk-period"></div>
            <div class="col-6 col-md-2" id="bk-wrap-step"><label class="form-label small mb-1">Step</label><input class="form-control form-control-sm" type="number" step="any" id="bk-step"></div>
          </div>
          <p class="text-muted small mb-0 mt-2">Numeric variables fluctuate only when both Low and High are set; bools use Toggle. Blank Low/High/Period/Step fields are cleared on every match. “(inherit default)” resets each variable's motion to the project default.</p>
        </div>

        <div id="bk-val" style="display:none">
          <label class="form-label small mb-1">Value</label>
          <input class="form-control form-control-sm" type="number" step="any" id="bk-value" style="max-width:220px">
          <p class="text-muted small mb-0 mt-2">Written to every matching variable. Variables whose type can't hold this value (e.g. a bool when you enter 2.5) are skipped. For bools use 0 or 1.</p>
        </div>
      </div>
      <div class="modal-footer">
        <div class="me-auto small" id="bk-validation"></div>
        <button class="btn btn-secondary" data-bs-dismiss="modal">Cancel</button>
        <button class="btn btn-primary" id="bk-apply">Apply</button>
      </div>
    </div>
  </div>
</div>`;
    document.body.appendChild(w.firstElementChild);
    document.getElementById("bk-mode").innerHTML =
      MOTIONS.map(([v, l]) => `<option value="${v}">${l}</option>`).join("");
    _modal = new bootstrap.Modal(document.getElementById("bulk-modal"));

    const body = document.querySelector("#bulk-modal .modal-body");
    body.addEventListener("input", _syncMode);
    body.addEventListener("change", _syncMode);
    document.getElementById("bk-apply").onclick = _apply;
  }

  function _op() {
    return document.querySelector('input[name="bk-op"]:checked').value;
  }

  // Show/hide fields based on operation + chosen motion, and validate.
  function _syncMode() {
    const op = _op();
    document.getElementById("bk-sim").style.display = op === "sim" ? "" : "none";
    document.getElementById("bk-val").style.display = op === "val" ? "" : "none";

    const mode = document.getElementById("bk-mode").value;
    const showRange = !["", "static", "toggle"].includes(mode);
    const showStep = mode === "step";
    document.getElementById("bk-wrap-low").style.display = showRange ? "" : "none";
    document.getElementById("bk-wrap-high").style.display = showRange ? "" : "none";
    document.getElementById("bk-wrap-step").style.display = showStep ? "" : "none";
    document.getElementById("bk-wrap-period").style.display = mode === "static" ? "none" : "";
    document.getElementById("bk-lbl-period").textContent = showStep ? "Every (s)" : "Period (s)";

    _validate();
  }

  function _numOrNull(id) { const v = document.getElementById(id).value; return v === "" ? null : Number(v); }

  function _validate() {
    const errs = [];
    if (_op() === "sim") {
      const mode = document.getElementById("bk-mode").value;
      const lo = _numOrNull("bk-low"), hi = _numOrNull("bk-high");
      if (!["", "static", "toggle"].includes(mode) && (lo == null) !== (hi == null))
        errs.push("set both Low and High (or neither)");
      if (mode === "step" && !(_numOrNull("bk-step") > 0)) errs.push("step needs a positive step size");
      const per = _numOrNull("bk-period");
      if (per != null && !(per > 0)) errs.push("period must be > 0");
    } else {
      if (_numOrNull("bk-value") == null || isNaN(_numOrNull("bk-value"))) errs.push("enter a value");
    }
    const box = document.getElementById("bk-validation");
    box.className = "me-auto small " + (errs.length ? "text-danger" : "text-success");
    box.textContent = errs.length ? errs.join(" · ") : "✓ ready";
    document.getElementById("bk-apply").disabled = errs.length > 0;
    return errs.length === 0;
  }

  async function _apply() {
    if (!_validate()) return;
    const op = _op();
    if (!confirm(`Apply to ${_total} variable${_total === 1 ? "" : "s"} across all matching devices?`)) return;

    const btn = document.getElementById("bk-apply");
    btn.disabled = true;
    let r;
    if (op === "sim") {
      const mode = document.getElementById("bk-mode").value;
      // Only send fields relevant to the chosen motion so we don't clear values the
      // user can't see. Blank Low/High/Period/Step under an active motion clear them.
      const sim = { sim_mode: mode };
      if (!["", "static", "toggle"].includes(mode)) {
        sim.sim_min = _numOrNull("bk-low");
        sim.sim_max = _numOrNull("bk-high");
      }
      if (mode !== "static") sim.sim_period = _numOrNull("bk-period");
      if (mode === "step") sim.sim_step = _numOrNull("bk-step");
      r = await App.postJSON("/api/signals/sim/bulk", { query: _query, sim });
    } else {
      r = await App.postJSON("/api/signals/value/bulk",
        { query: _query, value: _numOrNull("bk-value") });
    }
    btn.disabled = false;

    if (r.ok) {
      const d = r.data;
      const msg = op === "sim"
        ? `Applied to ${d.applied} variable${d.applied === 1 ? "" : "s"} on ${d.devices} device${d.devices === 1 ? "" : "s"}`
        : `Set ${d.applied} variable${d.applied === 1 ? "" : "s"}` + (d.skipped ? `, skipped ${d.skipped}` : "");
      App.toast(msg);
      _modal.hide();
      _search(_query);   // refresh the rows to show the new motion/values
    } else {
      const errs = (r.data.errors || [r.data.error]).map((e) => App.escapeHtml(String(e)));
      const box = document.getElementById("bk-validation");
      box.className = "me-auto small text-danger";
      box.innerHTML = `<strong>Rejected:</strong> ${errs.slice(0, 3).join(" · ")}`;
    }
  }
})();
