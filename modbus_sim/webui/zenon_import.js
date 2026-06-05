/* Zenon 15 CSV import workflow (called from the Setup wizard Step 1).
 * Opens a modal with three steps:
 *   Step 1 – upload + parse the Zenon variable export
 *   Step 2 – optionally upload a device mapping CSV to pre-populate IPs/units/VLANs
 *   Step 3 – configure each device and generate config + signal files
 */
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

  // ---- Step 1: upload & parse -----------------------------------------------
  function showStep1(errorMsg) {
    setModalStep(1);
    document.getElementById("zi-body").innerHTML = `
      <h6 class="mb-3">Step 1 &mdash; Upload Zenon Variable Export</h6>
      <p class="text-muted small">Select the variable export from
        Zenon 15 Engineering Studio (tab, semicolon, or comma delimited).</p>
      <div class="input-group mb-3">
        <input type="file" class="form-control" id="zi-file" accept=".csv,.txt">
        <button class="btn btn-primary" id="zi-parse-btn">Parse File</button>
      </div>
      <div id="zi-parse-error"></div>`;
    if (errorMsg) showError("zi-parse-error", errorMsg);
    document.getElementById("zi-parse-btn").onclick = doParse;
  }

  async function doParse() {
    const fileInput = document.getElementById("zi-file");
    const f = fileInput && fileInput.files[0];
    if (!f) return App.toast("Choose a file first", "warning");

    const btn = document.getElementById("zi-parse-btn");
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Parsing…';

    const r = await App.postForm("/api/import/zenon/parse", f);
    btn.disabled = false;
    btn.innerHTML = "Parse File";

    if (!r.ok) {
      const msg = r.data.error || (r.data.errors || []).join("; ") || "Parse failed";
      showError("zi-parse-error", msg);
      return;
    }

    _parsedDrivers = r.data.drivers || [];
    if (_parsedDrivers.length === 0) {
      const counts = r.data.driver_type_counts || {};
      const delim = r.data.detected_delimiter || "unknown";
      const cols = (r.data.found_columns || []).slice(0, 8);
      const found = Object.keys(counts).filter(Boolean);
      let hint;
      if (found.length) {
        hint = `Detected delimiter: ${delim}. DriverType values found: ${found.map(k => `"${k}" (${counts[k]})`).join(", ")}.`;
      } else {
        const colList = cols.length ? `First columns seen: ${cols.map(c => `"${c}"`).join(", ")}` : "No columns parsed";
        hint = `Detected delimiter: ${delim}. No DriverType column found. ${colList}.`;
      }
      showError("zi-parse-error",
        `No Modbus TCP signals found. Filter matches any DriverType or DriverName containing "MODBUS". ${hint}`);
      return;
    }

    showStep2(r.data);
  }

  // ---- Step 2: device mapping upload ----------------------------------------
  function showStep2(parseResult) {
    setModalStep(2);
    const total = parseResult.total_signals || 0;
    const skipped = parseResult.skipped_non_modbus || 0;

    document.getElementById("zi-body").innerHTML = `
      <h6 class="mb-3">Step 2 &mdash; Device Mapping (optional)</h6>
      <p class="text-muted small mb-2">
        Found <strong>${_parsedDrivers.length}</strong> Modbus device(s),
        <strong>${total}</strong> total signal(s).
        ${skipped > 0 ? `<span class="text-secondary">${skipped} non-Modbus rows skipped.</span>` : ""}
      </p>
      <p class="text-muted small mb-1">
        Upload a mapping CSV to pre-populate IP addresses, Modbus unit IDs, and VLANs.
        The file must have a header row with these five columns (in any order):
      </p>
      <pre class="bg-light border rounded p-2 small mb-3" style="font-size:.8rem">DriverName,NetAddr,IPAddress,UnitID,VLAN,DisplayName</pre>
      <p class="text-muted small mb-3">
        Rows are matched by <code>DriverName</code> + <code>NetAddr</code>. Unmatched
        devices will have blank fields in Step 3. Extra mapping rows are ignored.
        <code>DisplayName</code> is optional — leave it blank to keep the driver name.
      </p>
      <div class="input-group mb-3">
        <input type="file" class="form-control" id="zi-map-file" accept=".csv,.txt">
        <button class="btn btn-primary" id="zi-map-btn">Load Mapping</button>
      </div>
      <div id="zi-map-error" class="mb-2"></div>
      <div class="d-flex gap-2 mt-3">
        <button class="btn btn-secondary btn-sm" id="zi-back-btn">&#8592; Back</button>
        <button class="btn btn-outline-secondary btn-sm ms-auto" id="zi-skip-btn">
          Skip &rarr; Configure manually
        </button>
      </div>`;

    document.getElementById("zi-back-btn").onclick = () => showStep1();
    document.getElementById("zi-skip-btn").onclick = () => showStep3(null);
    document.getElementById("zi-map-btn").onclick = () => doLoadMapping();
  }

  function doLoadMapping() {
    const fileInput = document.getElementById("zi-map-file");
    const f = fileInput && fileInput.files[0];
    if (!f) return App.toast("Choose a mapping file first", "warning");

    const reader = new FileReader();
    reader.onload = (evt) => {
      const text = evt.target.result || "";
      const result = parseMappingCsv(text);
      if (result.error) {
        showError("zi-map-error", result.error);
        return;
      }
      showStep3(result.mapping);
    };
    reader.readAsText(f);
  }

  function parseMappingCsv(text) {
    // Normalise line endings.
    const lines = text.replace(/\r\n/g, "\n").replace(/\r/g, "\n").split("\n").filter(l => l.trim());
    if (lines.length < 2) return { error: "Mapping file must have a header row and at least one data row." };

    const delim = lines[0].includes(";") ? ";" : ",";
    const header = lines[0].split(delim).map(h => h.trim().toLowerCase());
    const required = ["drivername", "netaddr", "ipaddress", "unitid", "vlan"];
    const missing = required.filter(col => !header.includes(col));
    if (missing.length) {
      return { error: `Missing column(s): ${missing.join(", ")}. Expected: DriverName, NetAddr, IPAddress, UnitID, VLAN, DisplayName` };
    }

    const idx = col => header.indexOf(col);
    const mapping = new Map();
    for (let i = 1; i < lines.length; i++) {
      const cols = lines[i].split(delim).map(c => c.trim());
      const driverName = cols[idx("drivername")] || "";
      const netAddr = cols[idx("netaddr")] || "";
      const ip = cols[idx("ipaddress")] || "";
      const unitId = cols[idx("unitid")] || "";
      const vlan = cols[idx("vlan")] || "";
      const displayName = idx("displayname") >= 0 ? (cols[idx("displayname")] || "") : "";
      if (!driverName && !netAddr) continue;
      mapping.set(`${driverName}|${netAddr}`, { ip, unit_id: unitId, vlan, display_name: displayName });
    }

    return { mapping };
  }

  // ---- Step 3: configure devices --------------------------------------------
  function showStep3(mapping) {
    setModalStep(3);

    const rows = _parsedDrivers.map((d, i) => {
      const key = `${d.driver_name}|${d.net_addr}`;
      const m = mapping && mapping.get(key);
      const ip = m ? m.ip : "";
      const unitId = m ? (m.unit_id || "1") : "1";
      const vlan = m ? (m.vlan || "0") : "0";
      const displayName = (m && m.display_name) ? m.display_name : d.driver_name;
      return `
      <tr>
        <td class="text-muted small">${App.escapeHtml(d.driver_name)}</td>
        <td class="text-center text-muted">${d.net_addr}</td>
        <td class="text-center">${d.signal_count}</td>
        <td><input class="form-control form-control-sm" name="id_${i}"
              value="${App.escapeHtml(d.suggested_id)}" maxlength="40" required></td>
        <td><input class="form-control form-control-sm" name="name_${i}"
              value="${App.escapeHtml(displayName)}" required></td>
        <td><input class="form-control form-control-sm zi-ip" name="ip_${i}"
              placeholder="10.x.x.x" value="${App.escapeHtml(ip)}" required
              pattern="^\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}$"></td>
        <td><input class="form-control form-control-sm" type="number" name="unit_id_${i}"
              value="${App.escapeHtml(unitId)}" min="1" max="255" style="width:4.5rem" required></td>
        <td><input class="form-control form-control-sm" type="number" name="port_${i}"
              value="502" min="1" max="65535" style="width:5rem"></td>
        <td><input class="form-control form-control-sm" type="number" name="vlan_${i}"
              value="${App.escapeHtml(vlan)}" min="0" max="4094" style="width:5rem"></td>
        <td><input class="form-control form-control-sm" type="number" name="prefix_${i}"
              value="24" min="0" max="32" style="width:4.5rem"></td>
        <td>
          <select class="form-select form-select-sm zi-wo" name="wo_${i}" style="min-width:9rem">
            <option value="little_endian" selected>little-endian</option>
            <option value="big_endian">big-endian</option>
          </select>
        </td>
      </tr>`;
    }).join("");

    document.getElementById("zi-body").innerHTML = `
      <h6 class="mb-2">Step 3 &mdash; Configure Devices</h6>

      <div class="row g-2 mb-3">
        <div class="col-md-5">
          <label class="form-label fw-semibold small mb-1">Project Name <span class="text-danger">*</span></label>
          <input class="form-control form-control-sm" id="zi-project-name" placeholder="My Plant" required>
        </div>
        <div class="col-md-4">
          <label class="form-label fw-semibold small mb-1">Traffic Interface <span class="text-danger">*</span></label>
          <input class="form-control form-control-sm" id="zi-traffic-iface"
                 placeholder="eth1" value="eth1" required>
          <div class="form-text">Run <code>ip link</code> in the VM to find the USB-C NIC name.</div>
        </div>
        <div class="col-md-3">
          <label class="form-label fw-semibold small mb-1">Web UI Port</label>
          <input class="form-control form-control-sm" id="zi-webui-port"
                 type="number" value="5000" min="1" max="65535">
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

      <div class="table-responsive" style="max-height:45vh;">
        <table class="table table-sm table-bordered align-middle" style="min-width:900px">
          <thead class="table-light small">
            <tr>
              <th>Driver Name</th>
              <th title="Zenon internal device index within this driver">Net&nbsp;Addr</th>
              <th>Signals</th>
              <th>Device ID&nbsp;<span class="text-danger">*</span></th>
              <th>Display Name&nbsp;<span class="text-danger">*</span></th>
              <th>IP Address&nbsp;<span class="text-danger">*</span></th>
              <th>Modbus&nbsp;Unit&nbsp;<span class="text-danger">*</span></th>
              <th>Port</th><th>VLAN</th><th>Prefix</th>
              <th>Word&nbsp;Order (32-bit)</th>
            </tr>
          </thead>
          <tbody id="zi-device-rows">${rows}</tbody>
        </table>
      </div>

      <div id="zi-gen-error" class="mt-2"></div>

      <div class="d-flex gap-2 mt-3">
        <button class="btn btn-secondary btn-sm" id="zi-back-btn">&#8592; Back</button>
        <button class="btn btn-primary btn-sm ms-auto" id="zi-gen-btn">
          Generate Config &amp; Signals
        </button>
      </div>`;

    document.getElementById("zi-back-btn").onclick = () => showStep2({
      total_signals: _parsedDrivers.reduce((s, d) => s + d.signal_count, 0),
      skipped_non_modbus: 0,
    });
    document.getElementById("zi-gen-btn").onclick = doGenerate;

    document.querySelectorAll(".zi-ip").forEach((inp) => {
      inp.addEventListener("input", () => inp.classList.toggle("is-invalid", !inp.value.trim()));
      if (!inp.value.trim()) inp.classList.add("is-invalid");
    });

    document.getElementById("zi-wo-all").addEventListener("change", (e) => {
      if (!e.target.value) return;
      document.querySelectorAll(".zi-wo").forEach(sel => { sel.value = e.target.value; });
    });
  }

  function collectFormData() {
    const projectName = (document.getElementById("zi-project-name").value || "").trim();
    const trafficIface = (document.getElementById("zi-traffic-iface").value || "").trim();
    const webuiPort = parseInt(document.getElementById("zi-webui-port").value || "5000", 10);

    const drivers = _parsedDrivers.map((d, i) => ({
      driver_name: d.driver_name,
      net_addr: d.net_addr,
      unit_id: parseInt(document.querySelector(`[name="unit_id_${i}"]`)?.value || "1", 10),
      id: (document.querySelector(`[name="id_${i}"]`)?.value || d.suggested_id).trim(),
      name: (document.querySelector(`[name="name_${i}"]`)?.value || d.driver_name).trim(),
      ip: (document.querySelector(`[name="ip_${i}"]`)?.value || "").trim(),
      port: parseInt(document.querySelector(`[name="port_${i}"]`)?.value || "502", 10),
      vlan: parseInt(document.querySelector(`[name="vlan_${i}"]`)?.value || "0", 10),
      prefix_length: parseInt(document.querySelector(`[name="prefix_${i}"]`)?.value || "24", 10),
      word_order: document.querySelector(`[name="wo_${i}"]`)?.value || "little_endian",
    }));

    return { projectName, trafficIface, webuiPort, drivers };
  }

  // ---- generate ------------------------------------------------------
  async function doGenerate() {
    const { projectName, trafficIface, webuiPort, drivers } = collectFormData();

    const clientErrors = [];
    if (!projectName) clientErrors.push("Project Name is required.");
    if (!trafficIface) clientErrors.push("Traffic Interface is required.");
    drivers.forEach((d, i) => {
      if (!d.ip || d.ip === "0.0.0.0") clientErrors.push(`Row ${i + 1}: IP Address is required.`);
      if (!d.id) clientErrors.push(`Row ${i + 1}: Device ID is required.`);
      if (!d.unit_id || d.unit_id < 1 || d.unit_id > 255) clientErrors.push(`Row ${i + 1}: Modbus Unit (1-255) is required.`);
    });
    if (clientErrors.length) {
      showError("zi-gen-error", clientErrors.join(" "));
      return;
    }

    const btn = document.getElementById("zi-gen-btn");
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Generating…';

    const r = await App.postJSON("/api/import/zenon/generate", {
      project_name: projectName,
      traffic_interface: trafficIface,
      web_ui_port: webuiPort,
      drivers,
    });

    btn.disabled = false;
    btn.innerHTML = "Generate Config &amp; Signals";

    if (!r.ok) {
      const errs = r.data.errors || [r.data.error || "Generate failed"];
      showError("zi-gen-error", errs);
      return;
    }

    _modal.hide();
    App.toast(
      `Import complete: ${r.data.devices_generated} device(s), ` +
      `${r.data.total_signals} signal(s). Proceeding to Start…`,
      "success"
    );
    if (window.Setup) Setup.refresh();
  }

  // ---- shared helpers -------------------------------------------------------
  function setModalStep(n) {
    document.querySelectorAll("#zenon-import-modal .zi-step").forEach((el) => {
      const s = Number(el.dataset.step);
      el.classList.toggle("active", s === n);
      el.classList.toggle("done", s < n);
    });
  }

  function showError(targetId, msg) {
    const box = typeof targetId === "string"
      ? document.getElementById(targetId)
      : targetId;
    if (!box) return;
    const items = Array.isArray(msg)
      ? msg.map((m) => `<li>${App.escapeHtml(String(m))}</li>`).join("")
      : `<li>${App.escapeHtml(String(msg))}</li>`;
    box.innerHTML = `<div class="alert alert-danger mb-0 small"><ul class="mb-0">${items}</ul></div>`;
  }
})();
