/* Zenon 15 CSV import wizard (3 steps): parse → map devices → configure & generate. */
(function () {
  "use strict";

  let _modal = null;
  let _parsedDrivers = [];

  const ZenonImport = { open };
  window.ZenonImport = ZenonImport;

  function open() {
    _modal = new bootstrap.Modal(document.getElementById("zenon-import-modal"), {
      backdrop: "static",
      keyboard: false,
    });
    _parsedDrivers = [];
    showStep1();
    _modal.show();
  }

  // ── step track ─────────────────────────────────────────────────────────────
  function setModalStep(n) {
    document.querySelectorAll("#zenon-import-modal .zi-step").forEach((el) => {
      const s = Number(el.dataset.step);
      el.classList.toggle("active", s === n);
      el.classList.toggle("done", s < n);
    });
  }

  // ── Step 1: upload & parse ─────────────────────────────────────────────────
  function showStep1(errorMsg) {
    setModalStep(1);
    document.getElementById("zi-body").innerHTML = `
      <h6 class="mb-3">Upload Zenon Variable Export</h6>
      <p class="text-muted small">
        Select the variable export from Zenon 15 Engineering Studio
        (tab, semicolon, or comma delimited).
      </p>
      <div id="zi-drop-wrap" class="mb-3"></div>
      <div id="zi-parse-error"></div>`;

    const zone = _makeDropZone(".csv,.txt", "Drop Zenon variable export here", "or click to browse", (f) => {
      // store and trigger parse
      _parseFile(f);
    });
    document.getElementById("zi-drop-wrap").appendChild(zone);

    if (errorMsg) _showError("zi-parse-error", errorMsg);
  }

  async function _parseFile(file) {
    const btn = document.getElementById("zi-parse-err-btn");
    document.getElementById("zi-parse-error").innerHTML =
      `<div class="d-flex align-items-center gap-2 mt-3">` +
      `<span class="spinner-border spinner-border-sm"></span> Parsing…</div>`;

    const r = await App.postForm("/api/import/zenon/parse", file);

    if (!r.ok) {
      const msg = r.data.error || (r.data.errors || []).join("; ") || "Parse failed";
      _showError("zi-parse-error", msg);
      return;
    }

    _parsedDrivers = r.data.drivers || [];
    if (_parsedDrivers.length === 0) {
      const counts = r.data.driver_type_counts || {};
      const delim = r.data.detected_delimiter || "unknown";
      const found = Object.keys(counts).filter(Boolean);
      let hint;
      if (found.length) {
        hint = `Detected delimiter: ${delim}. DriverType values found: ${found.map((k) => `"${k}" (${counts[k]})`).join(", ")}.`;
      } else {
        const cols = (r.data.found_columns || []).slice(0, 8);
        const colList = cols.length ? `First columns: ${cols.map((c) => `"${c}"`).join(", ")}` : "No columns parsed";
        hint = `Detected delimiter: ${delim}. No DriverType column. ${colList}.`;
      }
      _showError(
        "zi-parse-error",
        `No Modbus TCP signals found. Filter matches any DriverType or DriverName containing "MODBUS". ${hint}`
      );
      return;
    }

    showStep2(r.data);
  }

  // ── Step 2: device mapping upload (optional) ───────────────────────────────
  function showStep2(parseResult) {
    setModalStep(2);
    const total = parseResult.total_signals || 0;
    const skipped = parseResult.skipped_non_modbus || 0;

    document.getElementById("zi-body").innerHTML = `
      <h6 class="mb-3">Device Mapping <span class="text-muted fw-normal">(optional)</span></h6>
      <p class="text-muted small mb-2">
        Found <strong>${_parsedDrivers.length}</strong> Modbus device(s),
        <strong>${total}</strong> total signal(s).
        ${skipped > 0 ? `<span class="text-secondary ms-1">${skipped} non-Modbus rows skipped.</span>` : ""}
      </p>
      <p class="text-muted small mb-2">
        Upload a mapping CSV to pre-populate IPs, unit IDs, and VLANs. Required columns:
      </p>
      <pre class="rounded p-2 small mb-3" style="font-size:.78rem">DriverName,NetAddr,IPAddress,UnitID,VLAN,DisplayName</pre>
      <p class="text-muted small mb-3">
        Rows matched by <code>DriverName + NetAddr</code>. Extra rows ignored.
        <code>DisplayName</code> is optional.
      </p>
      <div id="zi-map-drop-wrap" class="mb-3"></div>
      <div id="zi-map-error" class="mb-2"></div>
      <div class="d-flex gap-2 mt-3">
        <button class="btn btn-secondary btn-sm" id="zi-back-btn">← Back</button>
        <button class="btn btn-outline-secondary btn-sm ms-auto" id="zi-skip-btn">
          Skip → configure manually
        </button>
      </div>`;

    const zone = _makeDropZone(
      ".csv,.txt",
      "Drop device mapping CSV here",
      "or click to browse",
      (f) => _loadMapping(f)
    );
    document.getElementById("zi-map-drop-wrap").appendChild(zone);

    document.getElementById("zi-back-btn").onclick = () => showStep1();
    document.getElementById("zi-skip-btn").onclick = () => showStep3(null);
  }

  function _loadMapping(file) {
    const reader = new FileReader();
    reader.onload = (evt) => {
      const result = _parseMappingCsv(evt.target.result || "");
      if (result.error) { _showError("zi-map-error", result.error); return; }
      showStep3(result.mapping);
    };
    reader.readAsText(file);
  }

  function _parseMappingCsv(text) {
    const lines = text.replace(/\r\n/g, "\n").replace(/\r/g, "\n").split("\n").filter((l) => l.trim());
    if (lines.length < 2)
      return { error: "Mapping file must have a header row and at least one data row." };
    const delim = lines[0].includes(";") ? ";" : ",";
    const header = lines[0].split(delim).map((h) => h.trim().toLowerCase());
    const required = ["drivername", "netaddr", "ipaddress", "unitid", "vlan"];
    const missing = required.filter((c) => !header.includes(c));
    if (missing.length)
      return { error: `Missing column(s): ${missing.join(", ")}. Expected: DriverName, NetAddr, IPAddress, UnitID, VLAN, DisplayName` };

    const idx = (col) => header.indexOf(col);
    const mapping = new Map();
    for (let i = 1; i < lines.length; i++) {
      const cols = lines[i].split(delim).map((c) => c.trim());
      const dn = cols[idx("drivername")] || "";
      const na = cols[idx("netaddr")] || "";
      if (!dn && !na) continue;
      mapping.set(`${dn}|${na}`, {
        ip:           cols[idx("ipaddress")] || "",
        unit_id:      cols[idx("unitid")] || "",
        vlan:         cols[idx("vlan")] || "",
        display_name: idx("displayname") >= 0 ? (cols[idx("displayname")] || "") : "",
      });
    }
    return { mapping };
  }

  // ── Step 3: configure devices ──────────────────────────────────────────────
  function showStep3(mapping) {
    setModalStep(3);

    const rows = _parsedDrivers
      .map((d, i) => {
        const key = `${d.driver_name}|${d.net_addr}`;
        const m = mapping?.get(key);
        const ip = m?.ip || "";
        const unitId = m?.unit_id || "1";
        const vlan = m?.vlan || "0";
        const displayName = m?.display_name || d.driver_name;
        return `<tr>
          <td class="text-muted small">${App.escapeHtml(d.driver_name)}</td>
          <td class="text-center text-muted">${d.net_addr}</td>
          <td class="text-center">${d.signal_count}</td>
          <td><input class="form-control form-control-sm" name="id_${i}" value="${App.escapeHtml(d.suggested_id)}" maxlength="40" required></td>
          <td><input class="form-control form-control-sm" name="name_${i}" value="${App.escapeHtml(displayName)}" required></td>
          <td><input class="form-control form-control-sm zi-ip" name="ip_${i}" placeholder="10.x.x.x" value="${App.escapeHtml(ip)}" required pattern="^\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}$"></td>
          <td><input class="form-control form-control-sm" type="number" name="unit_id_${i}" value="${App.escapeHtml(unitId)}" min="1" max="255" style="width:4.5rem" required></td>
          <td><input class="form-control form-control-sm" type="number" name="port_${i}" value="502" min="1" max="65535" style="width:5rem"></td>
          <td><input class="form-control form-control-sm" type="number" name="vlan_${i}" value="${App.escapeHtml(vlan)}" min="0" max="4094" style="width:5rem"></td>
          <td><input class="form-control form-control-sm" type="number" name="prefix_${i}" value="24" min="0" max="32" style="width:4.5rem"></td>
          <td>
            <select class="form-select form-select-sm zi-wo" name="wo_${i}" style="min-width:9rem">
              <option value="little_endian" selected>little-endian</option>
              <option value="big_endian">big-endian</option>
            </select>
          </td>
        </tr>`;
      })
      .join("");

    document.getElementById("zi-body").innerHTML = `
      <h6 class="mb-3">Configure Devices</h6>

      <div class="row g-2 mb-3">
        <div class="col-md-5">
          <label class="form-label fw-semibold small mb-1">Project Name <span class="text-danger">*</span></label>
          <input class="form-control form-control-sm" id="zi-project-name" placeholder="My Plant" required>
        </div>
        <div class="col-md-4">
          <label class="form-label fw-semibold small mb-1">Traffic Interface <span class="text-danger">*</span></label>
          <select class="form-select form-select-sm" id="zi-traffic-iface" required>
            <option value="">Loading interfaces…</option>
          </select>
        </div>
        <div class="col-md-3">
          <label class="form-label fw-semibold small mb-1">Web UI Port</label>
          <input class="form-control form-control-sm" id="zi-webui-port" type="number" value="5000" min="1" max="65535">
        </div>
      </div>

      <div class="d-flex align-items-center gap-2 mb-2">
        <span class="small fw-semibold">Set all word orders:</span>
        <select class="form-select form-select-sm" id="zi-wo-all" style="width:auto">
          <option value="">-- pick --</option>
          <option value="little_endian">little-endian</option>
          <option value="big_endian">big-endian</option>
        </select>
      </div>

      <div class="table-responsive" style="max-height:42vh">
        <table class="table table-sm table-bordered align-middle" style="min-width:900px;font-size:.82rem">
          <thead style="position:sticky;top:0;z-index:1">
            <tr>
              <th>Driver Name</th>
              <th title="Zenon device index within the driver">Net Addr</th>
              <th>Signals</th>
              <th>Device ID <span class="text-danger">*</span></th>
              <th>Display Name <span class="text-danger">*</span></th>
              <th>IP Address <span class="text-danger">*</span></th>
              <th>Unit <span class="text-danger">*</span></th>
              <th>Port</th><th>VLAN</th><th>Prefix</th>
              <th>Word Order</th>
            </tr>
          </thead>
          <tbody id="zi-device-rows">${rows}</tbody>
        </table>
      </div>

      <div id="zi-gen-error" class="mt-2"></div>

      <div class="d-flex gap-2 mt-3">
        <button class="btn btn-secondary btn-sm" id="zi-back-btn">← Back</button>
        <button class="btn btn-primary btn-sm ms-auto" id="zi-gen-btn">Generate Config &amp; Signals</button>
      </div>`;

    document.getElementById("zi-back-btn").onclick = () =>
      showStep2({
        total_signals: _parsedDrivers.reduce((s, d) => s + d.signal_count, 0),
        skipped_non_modbus: 0,
      });
    document.getElementById("zi-gen-btn").onclick = _doGenerate;

    document.querySelectorAll(".zi-ip").forEach((inp) => {
      inp.addEventListener("input", () => inp.classList.toggle("is-invalid", !inp.value.trim()));
      if (!inp.value.trim()) inp.classList.add("is-invalid");
    });

    document.getElementById("zi-wo-all").addEventListener("change", (e) => {
      if (!e.target.value) return;
      document.querySelectorAll(".zi-wo").forEach((sel) => { sel.value = e.target.value; });
    });

    // Populate interface dropdown
    App.getJSON("/api/interfaces").then((r) => {
      const sel = document.getElementById("zi-traffic-iface");
      if (!sel) return;
      if (!r.ok || !r.data?.length) {
        const inp = document.createElement("input");
        inp.className = "form-control form-control-sm";
        inp.id = "zi-traffic-iface";
        inp.placeholder = "e.g. enxc8a3622c0b97";
        inp.required = true;
        sel.replaceWith(inp);
        return;
      }
      sel.innerHTML = r.data
        .map(
          (i) =>
            `<option value="${App.escapeHtml(i.name)}">` +
            `${App.escapeHtml(i.name)} — ${App.escapeHtml(i.mac)} (${i.state})</option>`
        )
        .join("");
    });
  }

  function _collectFormData() {
    const projectName  = (document.getElementById("zi-project-name")?.value || "").trim();
    const trafficIface = (document.getElementById("zi-traffic-iface")?.value || "").trim();
    const webuiPort    = parseInt(document.getElementById("zi-webui-port")?.value || "5000", 10);
    const drivers = _parsedDrivers.map((d, i) => ({
      driver_name:   d.driver_name,
      net_addr:      d.net_addr,
      id:            (document.querySelector(`[name="id_${i}"]`)?.value || d.suggested_id).trim(),
      name:          (document.querySelector(`[name="name_${i}"]`)?.value || d.driver_name).trim(),
      ip:            (document.querySelector(`[name="ip_${i}"]`)?.value || "").trim(),
      unit_id:       parseInt(document.querySelector(`[name="unit_id_${i}"]`)?.value || "1", 10),
      port:          parseInt(document.querySelector(`[name="port_${i}"]`)?.value || "502", 10),
      vlan:          parseInt(document.querySelector(`[name="vlan_${i}"]`)?.value || "0", 10),
      prefix_length: parseInt(document.querySelector(`[name="prefix_${i}"]`)?.value || "24", 10),
      word_order:    document.querySelector(`[name="wo_${i}"]`)?.value || "little_endian",
    }));
    return { projectName, trafficIface, webuiPort, drivers };
  }

  async function _doGenerate() {
    const { projectName, trafficIface, webuiPort, drivers } = _collectFormData();
    const errs = [];
    if (!projectName) errs.push("Project Name is required.");
    if (!trafficIface) errs.push("Traffic Interface is required.");
    drivers.forEach((d, i) => {
      if (!d.ip || d.ip === "0.0.0.0") errs.push(`Row ${i + 1}: IP Address is required.`);
      if (!d.id) errs.push(`Row ${i + 1}: Device ID is required.`);
      if (!d.unit_id || d.unit_id < 1 || d.unit_id > 255) errs.push(`Row ${i + 1}: Unit (1-255) is required.`);
    });
    if (errs.length) { _showError("zi-gen-error", errs.join(" ")); return; }

    const btn = document.getElementById("zi-gen-btn");
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Generating…';

    const r = await App.postJSON("/api/import/zenon/generate", {
      project_name:      projectName,
      traffic_interface: trafficIface,
      web_ui_port:       webuiPort,
      drivers,
    });
    btn.disabled = false;
    btn.innerHTML = "Generate Config &amp; Signals";

    if (!r.ok) {
      _showError("zi-gen-error", r.data.errors || [r.data.error || "Generate failed"]);
      return;
    }

    _modal.hide();
    App.toast(
      `Import complete: ${r.data.devices_generated} device(s), ${r.data.total_signals} signal(s). Proceeding to Start…`,
      "success"
    );
    window.Setup?.refresh();
  }

  // ── helpers ───────────────────────────────────────────────────────────────
  function _showError(targetId, msg) {
    const box = document.getElementById(targetId);
    if (!box) return;
    const msgs = Array.isArray(msg) ? msg : [msg];
    box.innerHTML =
      `<div class="alert alert-danger py-2 small">` +
      msgs.map((m) => `<div>${App.escapeHtml(String(m))}</div>`).join("") +
      `</div>`;
  }

  function _makeDropZone(accept, label, hint, onFile) {
    const inp = document.createElement("input");
    inp.type = "file"; inp.accept = accept; inp.style.display = "none";
    const div = document.createElement("div");
    div.className = "drop-zone";
    div.innerHTML =
      `<div class="drop-zone-icon">📄</div>` +
      `<div class="drop-zone-text">${label}</div>` +
      `<div class="drop-zone-hint">${hint}</div>`;
    div.appendChild(inp);
    div.addEventListener("click", (e) => { if (e.target !== inp) inp.click(); });
    inp.addEventListener("change", () => { if (inp.files[0]) onFile(inp.files[0]); });
    div.addEventListener("dragover", (e) => { e.preventDefault(); div.classList.add("drag-over"); });
    div.addEventListener("dragleave", () => div.classList.remove("drag-over"));
    div.addEventListener("drop", (e) => {
      e.preventDefault(); div.classList.remove("drag-over");
      if (e.dataTransfer.files[0]) onFile(e.dataTransfer.files[0]);
    });
    return div;
  }
})();
