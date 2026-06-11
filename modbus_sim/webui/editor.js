/* Per-variable editor modal — opened by clicking a signal name in the runtime grid.
 * Edits one variable's full definition (Modbus + simulation) and saves it via
 * POST /api/devices/<id>/signals/<name>/update, which sends only this one signal
 * (the server merges + hot-reloads). No bulk table, so it never freezes the browser. */
(function () {
  "use strict";

  const REG_TYPES   = ["holding", "input", "coil", "discrete_input"];
  const DATA_TYPES  = ["uint16", "int16", "uint32", "int32", "float32", "bool"];
  const WORD_ORDERS = ["", "big_endian", "little_endian"];
  const WIDE        = ["uint32", "int32", "float32"];
  // Motion options (value -> label). "" inherits the project default.
  const NUM_MOTIONS = [
    ["", "(inherit default)"], ["static", "Static"], ["oscillate", "Oscillate"],
    ["sawtooth", "Ramp & reset"], ["triangle", "Sweep up/down"], ["step", "Step (staircase)"],
  ];
  const BOOL_MOTIONS = [["", "(inherit default)"], ["static", "Static"], ["toggle", "Toggle"]];

  let _modal = null;
  let _deviceId = null;
  let _origName = null;

  const Editor = { open };
  window.Editor = Editor;

  function open(deviceId, sig) {
    _deviceId = deviceId;
    _origName = sig.name;
    _ensureModal();
    _fill(sig);
    _validate();
    _modal.show();
  }

  function _ensureModal() {
    if (document.getElementById("editor-modal")) {
      _modal = _modal || new bootstrap.Modal(document.getElementById("editor-modal"));
      return;
    }
    const w = document.createElement("div");
    w.innerHTML = `
<div class="modal fade" id="editor-modal" tabindex="-1">
  <div class="modal-dialog modal-lg modal-dialog-scrollable">
    <div class="modal-content">
      <div class="modal-header">
        <h5 class="modal-title">Edit Variable — <span id="ed-title" class="text-info"></span></h5>
        <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
      </div>
      <div class="modal-body">
        <div class="text-uppercase fw-semibold mb-2" style="font-size:.7rem;letter-spacing:.06em;color:var(--muted)">Definition</div>
        <div class="row g-2">
          <div class="col-12"><label class="form-label small mb-1">Name</label><input class="form-control form-control-sm" id="f-name"></div>
          <div class="col-6 col-md-3"><label class="form-label small mb-1">Register type</label><select class="form-select form-select-sm" id="f-rtype"></select></div>
          <div class="col-6 col-md-3"><label class="form-label small mb-1">Data type</label><select class="form-select form-select-sm" id="f-dtype"></select></div>
          <div class="col-6 col-md-3"><label class="form-label small mb-1">Address</label><input class="form-control form-control-sm" type="number" id="f-addr"></div>
          <div class="col-6 col-md-3" id="wrap-bit"><label class="form-label small mb-1">Bit index</label><input class="form-control form-control-sm" type="number" id="f-bit"></div>
          <div class="col-6 col-md-3" id="wrap-word"><label class="form-label small mb-1">Word order</label><select class="form-select form-select-sm" id="f-word"></select></div>
          <div class="col-6 col-md-3"><label class="form-label small mb-1">Scale</label><input class="form-control form-control-sm" type="number" step="any" id="f-scale"></div>
          <div class="col-6 col-md-3"><label class="form-label small mb-1">Unit</label><input class="form-control form-control-sm" id="f-unit"></div>
          <div class="col-6 col-md-3"><label class="form-label small mb-1">Section</label><input class="form-control form-control-sm" id="f-section"></div>
          <div class="col-12 col-md-8"><label class="form-label small mb-1">Description</label><input class="form-control form-control-sm" id="f-desc"></div>
          <div class="col-6 col-md-2"><label class="form-label small mb-1">Default</label><input class="form-control form-control-sm" type="number" step="any" id="f-default"></div>
          <div class="col-6 col-md-2 d-flex align-items-end"><div class="form-check"><input class="form-check-input" type="checkbox" id="f-writable"><label class="form-check-label small" for="f-writable">Writable</label></div></div>
        </div>

        <hr class="my-3">
        <div class="text-uppercase fw-semibold mb-2" style="font-size:.7rem;letter-spacing:.06em;color:var(--muted)">Simulation</div>
        <p class="text-muted small mb-2" id="ed-sim-hint"></p>
        <div class="row g-2 align-items-end">
          <div class="col-6 col-md-3"><label class="form-label small mb-1">Motion</label><select class="form-select form-select-sm" id="f-simmode"></select></div>
          <div class="col-6 col-md-2" id="wrap-low"><label class="form-label small mb-1">Low</label><input class="form-control form-control-sm" type="number" step="any" id="f-low"></div>
          <div class="col-6 col-md-2" id="wrap-high"><label class="form-label small mb-1">High</label><input class="form-control form-control-sm" type="number" step="any" id="f-high"></div>
          <div class="col-6 col-md-2" id="wrap-period"><label class="form-label small mb-1" id="lbl-period">Period (s)</label><input class="form-control form-control-sm" type="number" step="any" id="f-period"></div>
          <div class="col-6 col-md-2" id="wrap-step"><label class="form-label small mb-1">Step</label><input class="form-control form-control-sm" type="number" step="any" id="f-step"></div>
        </div>
        <div class="mt-2" id="ed-preview" style="font-family:monospace;font-size:.74rem;color:var(--muted);background:#0d1117;border-radius:6px;padding:6px 8px"></div>
      </div>
      <div class="modal-footer">
        <div class="me-auto small" id="ed-validation"></div>
        <button class="btn btn-secondary" data-bs-dismiss="modal">Cancel</button>
        <button class="btn btn-primary" id="ed-save">Save</button>
      </div>
    </div>
  </div>
</div>`;
    document.body.appendChild(w.firstElementChild);
    _opts("f-rtype", REG_TYPES.map((v) => [v, v]));
    _opts("f-dtype", DATA_TYPES.map((v) => [v, v]));
    _opts("f-word", WORD_ORDERS.map((v) => [v, v || "—"]));
    _modal = new bootstrap.Modal(document.getElementById("editor-modal"));

    // Re-validate / reshape on any input; data-type & register-type reshape the form.
    const body = document.querySelector("#editor-modal .modal-body");
    body.addEventListener("input", () => { _sync(); _validate(); });
    body.addEventListener("change", () => { _sync(); _validate(); });
    document.getElementById("ed-save").onclick = _save;
  }

  function _opts(id, pairs) {
    document.getElementById(id).innerHTML =
      pairs.map(([v, l]) => `<option value="${v}">${l}</option>`).join("");
  }
  function _set(id, v) { document.getElementById(id).value = v == null ? "" : v; }
  function _get(id) { return document.getElementById(id).value; }
  function _numOrNull(id) { const v = _get(id); return v === "" ? null : Number(v); }

  function _fill(s) {
    document.getElementById("ed-title").textContent = s.name || "(unnamed)";
    _set("f-name", s.name);
    _set("f-rtype", s.register_type || "holding");
    _set("f-dtype", s.data_type || "uint16");
    _set("f-addr", s.address);
    _set("f-bit", s.bit_index);
    _set("f-word", s.word_order || "");
    _set("f-scale", s.scale == null ? 1 : s.scale);
    _set("f-unit", s.unit);
    _set("f-section", s.section);
    _set("f-desc", s.description);
    _set("f-default", s.default_value == null ? 0 : s.default_value);
    document.getElementById("f-writable").checked = !!s.writable;
    _set("f-low", s.sim_min);
    _set("f-high", s.sim_max);
    _set("f-period", s.sim_period);
    _set("f-step", s.sim_step);
    const isBool = (s.data_type || "uint16") === "bool";
    _opts("f-simmode", isBool ? BOOL_MOTIONS : NUM_MOTIONS);
    _set("f-simmode", s.sim_mode || "");
    _sync();
  }

  // Show/hide fields that only apply to certain types, and rebuild the motion list
  // when the data type flips between bool and numeric.
  function _sync() {
    const dtype = _get("f-dtype");
    const rtype = _get("f-rtype");
    const isBool = dtype === "bool";
    const boolInWord = isBool && (rtype === "holding" || rtype === "input");

    document.getElementById("wrap-bit").style.display = boolInWord ? "" : "none";
    document.getElementById("wrap-word").style.display = WIDE.includes(dtype) ? "" : "none";

    const sel = document.getElementById("f-simmode");
    const haveBool = sel.options.length === BOOL_MOTIONS.length;
    if (isBool !== haveBool) {
      const cur = sel.value;
      _opts("f-simmode", isBool ? BOOL_MOTIONS : NUM_MOTIONS);
      sel.value = [...sel.options].some((o) => o.value === cur) ? cur : "";
    }
    const motion = sel.value;
    const showRange = !isBool && motion !== "static";
    const showStep = !isBool && motion === "step";
    document.getElementById("wrap-low").style.display = showRange ? "" : "none";
    document.getElementById("wrap-high").style.display = showRange ? "" : "none";
    document.getElementById("wrap-step").style.display = showStep ? "" : "none";
    document.getElementById("wrap-period").style.display = motion === "static" ? "none" : "";
    document.getElementById("lbl-period").textContent = showStep ? "Every (s)" : "Period (s)";

    document.getElementById("ed-sim-hint").textContent = isBool
      ? "Bools toggle on/off; set a period or leave blank to inherit the project default."
      : "A numeric variable fluctuates only when both Low and High are set. Leave motion blank to inherit the project default.";
    document.getElementById("ed-preview").textContent = _preview(isBool);
  }

  function _preview(isBool) {
    const motion = _get("f-simmode");
    if (motion === "static") return "static — no motion";
    if (isBool) return `toggle on/off${_get("f-period") ? ` every ${_get("f-period")}s` : ""}`;
    const lo = _numOrNull("f-low"), hi = _numOrNull("f-high");
    if (lo == null || hi == null) return "set a Low and High to make it move";
    const per = _get("f-period") ? ` · ~${_get("f-period")}s` : "";
    if (motion === "step") {
      const s = _numOrNull("f-step");
      if (!s || s <= 0) return "enter a step size";
      const levels = Math.max(1, Math.round(Math.abs(hi - lo) / s));
      const iv = _numOrNull("f-period");
      const sweep = iv ? ` (~${(levels * iv).toLocaleString()}s/sweep)` : "";
      return `${lo} ▸ ${lo + (hi >= lo ? s : -s)} ▸ … ▸ ${hi} ↻${sweep}`;
    }
    if (motion === "sawtooth") return `${lo} → ${hi} ramp, reset${per}`;
    if (motion === "triangle") return `${lo} → ${hi} → ${lo}${per}`;
    return `${lo} ↕ ${hi}${per}`;  // oscillate / inherit
  }

  function _collect() {
    const n = (id) => _numOrNull(id);
    return {
      name: _get("f-name").trim(),
      register_type: _get("f-rtype"),
      address: n("f-addr"),
      data_type: _get("f-dtype"),
      bit_index: n("f-bit"),
      word_order: _get("f-word") || null,
      scale: n("f-scale") ?? 1,
      unit: _get("f-unit"),
      section: _get("f-section") || "General",
      description: _get("f-desc"),
      default_value: n("f-default") ?? 0,
      writable: document.getElementById("f-writable").checked,
      sim_mode: _get("f-simmode") || "",
      sim_min: n("f-low"),
      sim_max: n("f-high"),
      sim_period: n("f-period"),
      sim_step: n("f-step"),
    };
  }

  function _validate() {
    const s = _collect();
    const errs = [];
    if (!s.name) errs.push("name required");
    if (s.address == null || s.address < 0) errs.push("address ≥ 0");
    const boolInWord = s.data_type === "bool" && ["holding", "input"].includes(s.register_type);
    if (boolInWord && s.bit_index == null) errs.push("bit required");
    if (!boolInWord && s.data_type === "bool" && s.bit_index != null) errs.push("bit must be empty for coil/discrete bool");
    if (WIDE.includes(s.data_type) && !s.word_order) errs.push("word_order required");
    if (s.data_type !== "bool" && (s.sim_min == null) !== (s.sim_max == null)) errs.push("set both Low and High (or neither)");
    if (s.sim_mode === "step" && !(s.sim_step > 0)) errs.push("step needs a positive step size");

    const box = document.getElementById("ed-validation");
    box.className = "me-auto small " + (errs.length ? "text-danger" : "text-success");
    box.textContent = errs.length ? errs.join(" · ") : "✓ valid";
    document.getElementById("ed-save").disabled = errs.length > 0;
    return errs.length === 0;
  }

  async function _save() {
    if (!_validate()) return;
    const btn = document.getElementById("ed-save");
    btn.disabled = true;
    const r = await App.postJSON(
      `/api/devices/${encodeURIComponent(_deviceId)}/signals/${encodeURIComponent(_origName)}/update`,
      _collect()
    );
    btn.disabled = false;
    if (r.ok) {
      App.toast("Variable updated");
      _modal.hide();
      window.Runtime?.reloadSelected();
    } else {
      const errs = (r.data.errors || [r.data.error]).map((e) =>
        e && typeof e === "object" && "message" in e
          ? `${App.escapeHtml(e.column)}: ${App.escapeHtml(e.message)}`
          : App.escapeHtml(String(e)));
      const box = document.getElementById("ed-validation");
      box.className = "me-auto small text-danger";
      box.innerHTML = `<strong>Rejected:</strong> ${errs.slice(0, 3).join(" · ")}`;
    }
  }
})();
