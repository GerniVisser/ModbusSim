/* Global value-simulation control. Opens a modal to toggle the master switch and
 * set the inherited defaults (default motion / period). Because values are generated
 * on read, applying a change takes effect immediately — no restart. Per-signal
 * low/high + motion are set inline in the runtime grid (⚡ on a row). */
(function () {
  "use strict";

  const MID = "sim-config-modal";
  let _state = null;  // last-known SimDefaults

  const Simulation = { isEnabled: () => !!(_state && _state.enabled), refresh };
  window.Simulation = Simulation;

  function btn() { return document.getElementById("rt-sim-config"); }

  async function refresh() {
    const r = await App.getJSON("/api/simulation");
    if (r.ok) { _state = r.data; _paintButton(); }
  }

  function _paintButton() {
    const b = btn();
    if (!b) return;
    const on = Simulation.isEnabled();
    b.className = "btn btn-sm py-0 " + (on ? "btn-warning" : "btn-outline-secondary");
    b.textContent = on ? "⚡ Simulation: On" : "⚡ Simulation";
  }

  function _ensureModal() {
    if (document.getElementById(MID)) return;
    const opts = (vals, cur) =>
      vals.map((v) => `<option value="${v}"${v === cur ? " selected" : ""}>${v}</option>`).join("");
    const s = _state || {};
    const w = document.createElement("div");
    w.innerHTML =
      `<div class="modal fade" id="${MID}" tabindex="-1"><div class="modal-dialog"><div class="modal-content">` +
      `<div class="modal-header"><h5 class="modal-title">Value Simulation</h5>` +
      `<button type="button" class="btn-close" data-bs-dismiss="modal"></button></div>` +
      `<div class="modal-body">` +
      `<p class="text-muted small mb-3">Make register values fluctuate over time. ` +
      `Set each signal's <strong>low / high</strong> and motion in the runtime grid (⚡ on a row) — ` +
      `signals without a low/high stay static. The settings below are just the defaults a signal ` +
      `inherits when it doesn't specify its own.</p>` +
      `<div class="form-check form-switch mb-3">` +
      `<input class="form-check-input" type="checkbox" id="sim-enabled"${s.enabled ? " checked" : ""}>` +
      `<label class="form-check-label fw-semibold" for="sim-enabled">Simulation enabled</label></div>` +
      `<div class="row g-2">` +
      `<div class="col-6"><label class="form-label small mb-1">Default motion</label>` +
      `<select class="form-select form-select-sm" id="sim-numeric">${opts(["oscillate", "sawtooth", "triangle", "static"], s.numeric_mode)}</select></div>` +
      `<div class="col-6"><label class="form-label small mb-1">Boolean motion</label>` +
      `<select class="form-select form-select-sm" id="sim-bool">${opts(["toggle", "static"], s.bool_mode)}</select></div>` +
      `<div class="col-6"><label class="form-label small mb-1">Default period (seconds)</label>` +
      `<input class="form-control form-control-sm" type="number" step="any" min="0" id="sim-period" value="${s.period_seconds ?? 10}"></div>` +
      `</div>` +
      `<p class="text-muted mt-3 mb-0" style="font-size:.72rem">A simulated signal's generated value ` +
      `overrides client writes on the next read. Motions: oscillate (sine), sawtooth (ramp &amp; reset), ` +
      `triangle (ramp up/down). Per-signal step motion is set from the grid.</p>` +
      `<div id="sim-err" class="mt-2"></div>` +
      `</div><div class="modal-footer">` +
      `<button class="btn btn-secondary" data-bs-dismiss="modal">Cancel</button>` +
      `<button class="btn btn-primary" id="sim-apply">Apply</button>` +
      `</div></div></div></div>`;
    document.body.appendChild(w.firstElementChild);
    document.getElementById("sim-apply").onclick = _apply;
  }

  async function _open() {
    await refresh();
    // Rebuild the modal each open so fields reflect the latest state.
    const existing = document.getElementById(MID);
    if (existing) existing.remove();
    _ensureModal();
    new bootstrap.Modal(document.getElementById(MID)).show();
  }

  async function _apply() {
    const body = {
      enabled: document.getElementById("sim-enabled").checked,
      numeric_mode: document.getElementById("sim-numeric").value,
      bool_mode: document.getElementById("sim-bool").value,
      period_seconds: Number(document.getElementById("sim-period").value),
    };
    const b = document.getElementById("sim-apply");
    b.disabled = true;
    const r = await App.postJSON("/api/simulation", body);
    b.disabled = false;
    if (r.ok) {
      _state = r.data.simulation;
      _paintButton();
      bootstrap.Modal.getInstance(document.getElementById(MID)).hide();
      App.toast(body.enabled ? "Simulation on" : "Simulation off", body.enabled ? "success" : "warning");
    } else {
      document.getElementById("sim-err").innerHTML =
        `<div class="alert alert-danger py-2 small mb-0">${App.escapeHtml((r.data.errors || [r.data.error]).join("; "))}</div>`;
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    const b = btn();
    if (b) b.onclick = _open;
    refresh();
  });
})();
