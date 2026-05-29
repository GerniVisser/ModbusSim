/* Tabular signal editor with hot reload (REQUIREMENTS.md section 12).
 * Edit signals inline, validate client-side, then POST the full list to
 * /api/devices/{id}/signals which rebuilds and hot-swaps the RegisterMap. */
(function () {
  "use strict";

  const REG_TYPES = ["holding", "input", "coil", "discrete_input"];
  const DATA_TYPES = ["uint16", "int16", "uint32", "int32", "float32", "bool"];
  const WORD_ORDERS = ["", "big_endian", "little_endian"];
  const WIDE = ["uint32", "int32", "float32"];

  let modal, deviceId;

  const Editor = { open };
  window.Editor = Editor;

  function open(id, signals) {
    deviceId = id;
    document.getElementById("ed-device").textContent = id;
    const tbody = document.getElementById("ed-rows");
    tbody.innerHTML = "";
    signals.forEach((s) => tbody.appendChild(rowEl(s)));
    wire();
    validate();
    modal = modal || new bootstrap.Modal(document.getElementById("editor-modal"));
    modal.show();
  }

  function wire() {
    document.getElementById("ed-add").onclick = () => {
      document.getElementById("ed-rows").appendChild(rowEl({}));
      validate();
    };
    document.getElementById("ed-save").onclick = save;
    document.getElementById("ed-download").onclick = () =>
      (window.location = `/api/devices/${encodeURIComponent(deviceId)}/signals/download`);
    document.getElementById("ed-upload").onchange = uploadCsv;
    document.getElementById("ed-rows").oninput = validate;
  }

  function sel(value, options) {
    return `<select class="form-select form-select-sm">${options
      .map((o) => `<option value="${o}" ${o === (value || "") ? "selected" : ""}>${o || "—"}</option>`)
      .join("")}</select>`;
  }
  function txt(value, type = "text", cls = "") {
    return `<input class="form-control form-control-sm ${cls}" type="${type}" step="any" value="${App.escapeHtml(value ?? "")}">`;
  }

  function rowEl(s) {
    const tr = document.createElement("tr");
    tr.dataset.description = s.description || "";
    tr.innerHTML = `
      <td>${txt(s.name, "text", "f-name")}</td>
      <td>${sel(s.register_type || "holding", REG_TYPES)}</td>
      <td>${txt(s.address, "number", "f-addr")}</td>
      <td>${sel(s.data_type || "uint16", DATA_TYPES)}</td>
      <td>${txt(s.bit_index, "number", "f-bit")}</td>
      <td>${sel(s.word_order, WORD_ORDERS)}</td>
      <td>${txt(s.scale ?? 1, "number")}</td>
      <td>${txt(s.unit)}</td>
      <td>${txt(s.section)}</td>
      <td>${txt(s.default_value ?? 0, "number")}</td>
      <td class="text-center"><input class="form-check-input f-writable" type="checkbox" ${s.writable ? "checked" : ""}></td>
      <td><button class="btn btn-sm btn-outline-danger ed-del">&times;</button></td>`;
    tr.querySelector(".ed-del").onclick = () => { tr.remove(); validate(); };
    return tr;
  }

  // Read the table into a list of signal objects (schema-shaped).
  function collect() {
    return [...document.querySelectorAll("#ed-rows tr")].map((tr) => {
      const inputs = tr.querySelectorAll("input");
      const selects = tr.querySelectorAll("select");
      const num = (v) => (v === "" || v == null ? null : Number(v));
      return {
        name: inputs[0].value.trim(),
        register_type: selects[0].value,
        address: num(inputs[1].value),
        data_type: selects[1].value,
        bit_index: inputs[2].value === "" ? null : Number(inputs[2].value),
        word_order: selects[2].value || null,
        scale: num(inputs[3].value) ?? 1,
        unit: inputs[4].value,
        section: inputs[5].value || "General",
        description: tr.dataset.description || "",
        default_value: num(inputs[6].value) ?? 0,
        writable: tr.querySelector(".f-writable").checked,
      };
    });
  }

  // Light client-side validation; the engine is the authority on save.
  function validate() {
    const rows = collect();
    const errors = [];
    const names = new Set();
    rows.forEach((s, i) => {
      const n = i + 1;
      if (!s.name) errors.push(`Row ${n}: name required`);
      else if (names.has(s.name)) errors.push(`Row ${n}: duplicate name '${s.name}'`);
      names.add(s.name);
      if (s.address == null || s.address < 0) errors.push(`Row ${n}: address must be ≥ 0`);
      if (!DATA_TYPES.includes(s.data_type)) errors.push(`Row ${n}: bad data_type`);
      const boolInWord = s.data_type === "bool" && ["holding", "input"].includes(s.register_type);
      if (boolInWord && s.bit_index == null) errors.push(`Row ${n}: bit required for ${s.register_type} bool`);
      if (!boolInWord && s.data_type === "bool" && s.bit_index != null)
        errors.push(`Row ${n}: bit must be empty for coil/discrete bool`);
      if (WIDE.includes(s.data_type) && !s.word_order) errors.push(`Row ${n}: word_order required for ${s.data_type}`);
    });
    const box = document.getElementById("ed-validation");
    if (errors.length) {
      box.className = "small text-danger";
      box.innerHTML = `<strong>${errors.length} error(s):</strong><ul class="mb-0">` +
        errors.map((e) => `<li>${App.escapeHtml(e)}</li>`).join("") + "</ul>";
    } else {
      box.className = "small text-success";
      box.textContent = `✓ ${rows.length} signals, no client-side errors`;
    }
    document.getElementById("ed-save").disabled = errors.length > 0;
    return errors.length === 0;
  }

  async function save() {
    if (!validate()) return;
    const r = await App.postJSON(`/api/devices/${encodeURIComponent(deviceId)}/signals`,
      { signals: collect() });
    if (r.ok) {
      App.toast(`Hot reloaded ${r.data.signal_count} signals`);
      modal.hide();
      window.Runtime.reloadSelected();
    } else {
      showServerErrors(r.data.errors || [r.data.error]);
    }
  }

  async function uploadCsv(e) {
    const f = e.target.files[0];
    if (!f) return;
    const r = await App.postForm(`/api/devices/${encodeURIComponent(deviceId)}/signals/upload`, f);
    if (r.ok) {
      App.toast(`Hot reloaded ${r.data.signal_count} signals from CSV`);
      modal.hide();
      window.Runtime.reloadSelected();
    } else {
      showServerErrors(r.data.errors || [r.data.error]);
    }
    e.target.value = "";
  }

  function showServerErrors(errors) {
    const box = document.getElementById("ed-validation");
    box.className = "small text-danger";
    const items = (errors || []).map((er) =>
      er && typeof er === "object" && "message" in er
        ? `Row ${er.row}, ${App.escapeHtml(er.column)}: ${App.escapeHtml(er.message)}`
        : App.escapeHtml(er));
    box.innerHTML = `<strong>Engine rejected the changes:</strong><ul class="mb-0">` +
      items.map((i) => `<li>${i}</li>`).join("") + "</ul>";
  }
})();
