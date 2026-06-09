/* Signal editor modal — inline editing of the register map with hot-reload.
 * Sticky table header, row numbers, client-side validation, CSV import/export. */
(function () {
  "use strict";

  const REG_TYPES  = ["holding", "input", "coil", "discrete_input"];
  const DATA_TYPES = ["uint16", "int16", "uint32", "int32", "float32", "bool"];
  const WORD_ORDERS = ["", "big_endian", "little_endian"];
  const SIM_MODES = ["", "static", "oscillate", "sawtooth", "toggle"];
  const WIDE = ["uint32", "int32", "float32"];

  let _modal = null;
  let _deviceId = null;

  const Editor = { open };
  window.Editor = Editor;

  // ── open ──────────────────────────────────────────────────────────────────
  function open(id, signals) {
    _deviceId = id;
    _ensureModal();
    document.getElementById("ed-device").textContent = id;
    const tbody = document.getElementById("ed-rows");
    tbody.innerHTML = "";
    signals.forEach((s) => tbody.appendChild(_rowEl(s)));
    _wire();
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
  <div class="modal-dialog modal-xl modal-dialog-scrollable">
    <div class="modal-content">
      <div class="modal-header">
        <h5 class="modal-title">Edit Signals — <span id="ed-device"></span></h5>
        <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
      </div>
      <div class="modal-body p-0">
        <div class="d-flex gap-2 p-2" style="border-bottom:1px solid var(--border);flex-wrap:wrap">
          <button class="btn btn-sm btn-outline-primary" id="ed-add">+ Add Row</button>
          <button class="btn btn-sm btn-outline-secondary" id="ed-download">↓ Download CSV</button>
          <label class="btn btn-sm btn-outline-secondary mb-0">↑ Upload CSV
            <input type="file" id="ed-upload" accept=".csv" hidden>
          </label>
          <div class="ms-auto" id="ed-validation" style="font-size:.8rem;display:flex;align-items:center"></div>
        </div>
        <div id="ed-table-wrap">
          <table class="table table-sm table-bordered align-middle mb-0" id="ed-table">
            <thead class="table-dark">
              <tr>
                <th class="row-num">#</th>
                <th>name</th><th>register_type</th><th>address</th><th>data_type</th>
                <th>bit</th><th>word_order</th><th>scale</th><th>unit</th>
                <th>section</th><th>default</th><th>writable</th>
                <th title="empty = inherit project default">sim</th>
                <th title="override min (engineering)">sim min</th>
                <th title="override max (engineering)">sim max</th>
                <th title="override cycle seconds">sim period</th><th></th>
              </tr>
            </thead>
            <tbody id="ed-rows"></tbody>
          </table>
        </div>
      </div>
      <div class="modal-footer">
        <button class="btn btn-secondary" data-bs-dismiss="modal">Cancel</button>
        <button class="btn btn-primary" id="ed-save">Save &amp; Hot Reload</button>
      </div>
    </div>
  </div>
</div>`;
    document.body.appendChild(w.firstElementChild);
    _modal = new bootstrap.Modal(document.getElementById("editor-modal"));
  }

  // ── wiring ────────────────────────────────────────────────────────────────
  function _wire() {
    document.getElementById("ed-add").onclick = () => {
      document.getElementById("ed-rows").appendChild(_rowEl({}));
      _validate();
    };
    document.getElementById("ed-save").onclick = _save;
    document.getElementById("ed-download").onclick = () =>
      (window.location = `/api/devices/${encodeURIComponent(_deviceId)}/signals/download`);
    const up = document.getElementById("ed-upload");
    up.onchange = _uploadCsv;
    up.value = "";
    document.getElementById("ed-rows").oninput = _validate;
  }

  // ── row rendering ─────────────────────────────────────────────────────────
  function _sel(value, options) {
    return (
      `<select class="form-select form-select-sm">` +
      options
        .map((o) => `<option value="${o}"${o === (value ?? "") ? " selected" : ""}>${o || "—"}</option>`)
        .join("") +
      `</select>`
    );
  }
  function _num(value, cls = "") {
    return `<input class="form-control form-control-sm ${cls}" type="number" step="any" value="${App.escapeHtml(String(value ?? ""))}">`;
  }
  function _txt(value, cls = "") {
    return `<input class="form-control form-control-sm ${cls}" type="text" value="${App.escapeHtml(String(value ?? ""))}">`;
  }

  function _rowEl(s) {
    const tr = document.createElement("tr");
    tr.dataset.description = s.description || "";
    tr.innerHTML =
      `<td class="row-num"></td>` +
      `<td>${_txt(s.name, "f-name")}</td>` +
      `<td>${_sel(s.register_type || "holding", REG_TYPES)}</td>` +
      `<td>${_num(s.address, "f-addr")}</td>` +
      `<td>${_sel(s.data_type || "uint16", DATA_TYPES)}</td>` +
      `<td>${_num(s.bit_index, "f-bit")}</td>` +
      `<td>${_sel(s.word_order, WORD_ORDERS)}</td>` +
      `<td>${_num(s.scale ?? 1)}</td>` +
      `<td>${_txt(s.unit)}</td>` +
      `<td>${_txt(s.section)}</td>` +
      `<td>${_num(s.default_value ?? 0)}</td>` +
      `<td class="text-center"><input class="form-check-input f-writable" type="checkbox"${s.writable ? " checked" : ""}></td>` +
      `<td>${_sel(s.sim_mode, SIM_MODES)}</td>` +
      `<td>${_num(s.sim_min, "f-sim")}</td>` +
      `<td>${_num(s.sim_max, "f-sim")}</td>` +
      `<td>${_num(s.sim_period, "f-sim")}</td>` +
      `<td><button class="btn btn-sm btn-outline-danger py-0 ed-del">×</button></td>`;
    tr.querySelector(".ed-del").onclick = () => { tr.remove(); _validate(); };
    return tr;
  }

  // Update row number cells
  function _renumber() {
    document.querySelectorAll("#ed-rows tr").forEach((tr, i) => {
      const cell = tr.querySelector(".row-num");
      if (cell) cell.textContent = i + 1;
    });
  }

  // ── collect + validate ────────────────────────────────────────────────────
  function _collect() {
    return [...document.querySelectorAll("#ed-rows tr")].map((tr) => {
      const inputs  = tr.querySelectorAll("input:not(.f-writable):not(.ed-del)");
      const selects = tr.querySelectorAll("select");
      const n = (v) => (v === "" || v == null ? null : Number(v));
      return {
        name:         inputs[0].value.trim(),
        register_type: selects[0].value,
        address:      n(inputs[1].value),
        data_type:    selects[1].value,
        bit_index:    inputs[2].value === "" ? null : Number(inputs[2].value),
        word_order:   selects[2].value || null,
        scale:        n(inputs[3].value) ?? 1,
        unit:         inputs[4].value,
        section:      inputs[5].value || "General",
        description:  tr.dataset.description || "",
        default_value: n(inputs[6].value) ?? 0,
        writable:     tr.querySelector(".f-writable").checked,
        sim_mode:     selects[3].value || "",
        sim_min:      n(inputs[7].value),
        sim_max:      n(inputs[8].value),
        sim_period:   n(inputs[9].value),
      };
    });
  }

  function _validate() {
    _renumber();
    const rows = _collect();
    const errors = [];
    const names = new Set();
    rows.forEach((s, i) => {
      const n = i + 1;
      if (!s.name) errors.push(`Row ${n}: name required`);
      else if (names.has(s.name)) errors.push(`Row ${n}: duplicate name "${s.name}"`);
      names.add(s.name);
      if (s.address == null || s.address < 0) errors.push(`Row ${n}: address ≥ 0`);
      const boolInWord = s.data_type === "bool" && ["holding", "input"].includes(s.register_type);
      if (boolInWord && s.bit_index == null) errors.push(`Row ${n}: bit required`);
      if (!boolInWord && s.data_type === "bool" && s.bit_index != null)
        errors.push(`Row ${n}: bit must be empty for coil/discrete bool`);
      if (WIDE.includes(s.data_type) && !s.word_order) errors.push(`Row ${n}: word_order required`);
    });

    const box = document.getElementById("ed-validation");
    if (errors.length) {
      box.className = "text-danger";
      box.innerHTML = `<strong>${errors.length} error(s)</strong>`;
    } else {
      box.className = "text-success";
      box.textContent = `✓ ${rows.length} signal(s), no errors`;
    }
    document.getElementById("ed-save").disabled = errors.length > 0;
    return errors.length === 0;
  }

  // ── save / upload ─────────────────────────────────────────────────────────
  async function _save() {
    if (!_validate()) return;
    const r = await App.postJSON(
      `/api/devices/${encodeURIComponent(_deviceId)}/signals`,
      { signals: _collect() }
    );
    if (r.ok) {
      App.toast(`Hot reloaded ${r.data.signal_count} signals`);
      _modal.hide();
      window.Runtime?.reloadSelected();
    } else {
      _showServerErrors(r.data.errors || [r.data.error]);
    }
  }

  async function _uploadCsv(e) {
    const f = e.target.files[0];
    if (!f) return;
    const r = await App.postForm(
      `/api/devices/${encodeURIComponent(_deviceId)}/signals/upload`,
      f
    );
    if (r.ok) {
      App.toast(`Hot reloaded ${r.data.signal_count} signals from CSV`);
      _modal.hide();
      window.Runtime?.reloadSelected();
    } else {
      _showServerErrors(r.data.errors || [r.data.error]);
    }
    e.target.value = "";
  }

  function _showServerErrors(errors) {
    const box = document.getElementById("ed-validation");
    box.className = "text-danger";
    const items = (errors || []).map((er) =>
      er && typeof er === "object" && "message" in er
        ? `Row ${er.row}, ${App.escapeHtml(er.column)}: ${App.escapeHtml(er.message)}`
        : App.escapeHtml(String(er))
    );
    box.innerHTML =
      `<strong>Rejected:</strong> <span style="font-size:.75rem">${items.slice(0, 3).join(" · ")}${items.length > 3 ? "…" : ""}</span>`;
  }
})();
