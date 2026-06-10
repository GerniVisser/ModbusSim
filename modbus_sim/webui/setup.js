/* Setup wizard: 3 steps + interface confirmation.
 * Drag-and-drop file upload, live device list, step track. */
(function () {
  "use strict";

  let _ifaceConfirmed = false;

  const Setup = { enter, refresh };
  window.Setup = Setup;

  const body = () => document.getElementById("setup-body");

  // ── step track ────────────────────────────────────────────────────────────
  function setStep(n) {
    const steps = ["Config", "Signals", "Start"];
    const track = document.getElementById("step-track");
    if (!track) return;
    track.innerHTML = steps
      .map((label, i) => {
        const num = i + 1;
        const done = num < n, active = num === n;
        return (
          (i > 0 ? `<div class="step-line${done ? " done" : ""}"></div>` : "") +
          `<div class="step-node${active ? " active" : ""}${done ? " done" : ""}">
            <div class="sn-circle">${done ? "✓" : num}</div>
            <div class="sn-label">${label}</div>
          </div>`
        );
      })
      .join("");
  }

  async function enter() { await refresh(); }

  async function refresh() {
    const r = await App.getJSON("/api/setup/status");
    if (!r.ok) return;
    const st = r.data;
    if (!st.config_uploaded) { _ifaceConfirmed = false; return renderStep1(); }
    if (!_ifaceConfirmed) return renderStepInterface(st);
    if (!st.can_start) return renderStep2(st);
    return renderStep3(st);
  }

  // ── Step 1 : upload config ─────────────────────────────────────────────────
  function renderStep1(errors) {
    setStep(1);
    body().innerHTML = "";
    const wrap = document.createElement("div");
    wrap.innerHTML = `
      <h5 class="mb-1">Upload Configuration</h5>
      <p class="mb-3" style="color:var(--muted);font-size:.875rem">
        Select your <code>sim_config.yaml</code>. Once uploaded it is
        <strong>locked</strong> for this VM session.
      </p>
      <div id="cfg-drop-wrap"></div>
      <div id="cfg-errors" class="mt-3"></div>
      <hr style="border-color:var(--border)" class="my-4">
      <p class="mb-2" style="color:var(--muted);font-size:.82rem">
        Have a Zenon 15 variable export? Generate the config automatically.
      </p>
      <button class="btn btn-outline-secondary btn-sm" id="zenon-import-btn">
        ↑ Import from Zenon 15 CSV
      </button>`;
    body().appendChild(wrap);

    const zone = makeDropZone(".yaml,.yml", "Drop sim_config.yaml here", "or click to browse", uploadConfig);
    document.getElementById("cfg-drop-wrap").appendChild(zone);
    if (errors) renderErrors("cfg-errors", errors);
    document.getElementById("zenon-import-btn").onclick = () => window.ZenonImport?.open();
  }

  async function uploadConfig(file) {
    const zone = document.querySelector(".drop-zone");
    zone?.classList.add("drag-over");
    const r = await App.postForm("/api/setup/config", file);
    zone?.classList.remove("drag-over");
    if (r.ok) { App.toast("Config accepted: " + r.data.project_name); refresh(); }
    else if (r.status === 409) refresh();
    else renderErrors("cfg-errors", r.data.errors || [r.data.error]);
  }

  // ── Step 1b : confirm traffic interface ───────────────────────────────────
  function renderStepInterface(st) {
    setStep(1);
    body().innerHTML = `
      <h5 class="mb-1">Confirm Traffic Interface</h5>
      <p class="mb-3" style="color:var(--muted);font-size:.875rem">
        Select the network adapter that carries the Modbus traffic
        (usually the USB-to-Ethernet NIC).
      </p>
      <div class="mb-3">
        <label class="form-label fw-semibold small">Network Interface</label>
        <select class="form-select" id="iface-select">
          <option value="">Loading interfaces…</option>
        </select>
        <div class="form-text" style="color:var(--muted)">
          Plug in your USB-to-Ethernet adapter first, then select it here.
        </div>
      </div>
      <div id="iface-msg" class="mb-3"></div>
      <button class="btn btn-primary" id="iface-confirm">Confirm &amp; Continue →</button>`;

    App.getJSON("/api/interfaces").then((r) => {
      const sel = document.getElementById("iface-select");
      if (!sel) return;
      if (!r.ok || !r.data?.length) {
        sel.innerHTML = '<option value="">No interfaces found</option>';
        return;
      }
      const cur = st.traffic_interface || "";
      sel.innerHTML = r.data
        .map(
          (i) =>
            `<option value="${App.escapeHtml(i.name)}"${i.name === cur ? " selected" : ""}>` +
            `${App.escapeHtml(i.name)} — ${App.escapeHtml(i.mac)} (${i.state})</option>`
        )
        .join("");
    });

    document.getElementById("iface-confirm").onclick = () => {
      const iface = document.getElementById("iface-select")?.value;
      if (!iface) {
        document.getElementById("iface-msg").innerHTML =
          '<div class="alert alert-warning py-2 small mb-0">Please select an interface to continue.</div>';
        return;
      }
      _ifaceConfirmed = true;
      refresh();
    };
  }

  // ── Step 2 : upload per-device signal files ───────────────────────────────
  function renderStep2(st) {
    setStep(2);
    const rows = st.devices
      .map(
        (d) => `
      <div class="d-flex align-items-center gap-2 py-2" style="border-bottom:1px solid var(--border)">
        <span style="color:${d.signals_uploaded ? "var(--green)" : "var(--muted)"}">
          ${d.signals_uploaded ? "✓" : "○"}
        </span>
        <div class="flex-grow-1">
          <div class="fw-semibold" style="font-size:.84rem">${App.escapeHtml(d.id)}</div>
          <div style="font-size:.72rem;color:var(--muted)">${App.escapeHtml(d.name)}</div>
        </div>
        ${
          d.signals_uploaded
            ? '<span class="badge bg-success">ready</span>'
            : `<label class="btn btn-sm btn-outline-primary mb-0" style="font-size:.75rem">
                Upload CSV
                <input type="file" accept=".csv" class="sig-file" data-id="${App.escapeHtml(d.id)}" hidden>
               </label>`
        }
      </div>
      <div class="sig-err" data-id="${App.escapeHtml(d.id)}" style="display:none"></div>`
      )
      .join("");

    body().innerHTML = `
      <h5 class="mb-1">Upload Signal Files</h5>
      <p class="mb-3" style="color:var(--muted);font-size:.875rem">
        Each device needs a CSV file defining its Modbus register map.
      </p>
      <div class="mb-3">${rows}</div>
      <div class="d-flex justify-content-between align-items-center">
        <span style="color:var(--muted);font-size:.82rem">${st.devices_ready} / ${st.devices_total} ready</span>
        <button class="btn btn-primary btn-sm" id="to-step3" ${st.can_start ? "" : "disabled"}>
          Continue →
        </button>
      </div>`;

    document.querySelectorAll(".sig-file").forEach((inp) => {
      inp.onchange = () => { if (inp.files[0]) uploadSignals(inp.dataset.id, inp.files[0]); };
    });
    document.getElementById("to-step3").onclick = refresh;
  }

  async function uploadSignals(id, file) {
    const r = await App.postForm(`/api/setup/signals/${encodeURIComponent(id)}`, file);
    if (r.ok) { App.toast(`${id}: ${r.data.signal_count} signals loaded`); refresh(); }
    else {
      const errDiv = document.querySelector(`.sig-err[data-id="${CSS.escape(id)}"]`);
      if (errDiv) {
        errDiv.style.display = "";
        renderErrors(errDiv, r.data.errors || [r.data.error]);
      }
    }
  }

  // ── Step 3 : start simulation ─────────────────────────────────────────────
  function renderStep3(st) {
    setStep(3);
    body().innerHTML = `
      <h5 class="mb-1">Start Simulation</h5>
      <p class="mb-3" style="color:var(--muted);font-size:.875rem">
        All <strong>${st.devices_total}</strong> device(s) have signal files. Starting will
        configure the network interfaces and launch the Modbus TCP servers.
      </p>
      <div id="start-err" class="mb-3"></div>
      <button class="btn btn-success btn-lg" id="start-btn">▶ Start Simulation</button>`;
    document.getElementById("start-btn").onclick = doStart;
    // A previous start/auto-restore attempt failed (e.g. NIC unplugged) — show why,
    // so the user understands what to fix before pressing Start again.
    if (st.start_error) renderErrors("start-err", [st.start_error]);
  }

  async function doStart() {
    const btn = document.getElementById("start-btn");
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>Starting…';
    const r = await App.postJSON("/api/setup/start");
    if (r.ok) {
      App.toast("Simulation started");
    } else {
      btn.disabled = false;
      btn.innerHTML = "▶ Start Simulation";
      renderErrors("start-err", r.data.errors || [r.data.error]);
    }
  }

  // ── helpers ───────────────────────────────────────────────────────────────
  function makeDropZone(accept, label, hint, onFile) {
    const inp = document.createElement("input");
    inp.type = "file";
    inp.accept = accept;
    inp.style.display = "none";

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
      e.preventDefault();
      div.classList.remove("drag-over");
      if (e.dataTransfer.files[0]) onFile(e.dataTransfer.files[0]);
    });
    return div;
  }

  function renderErrors(target, errors) {
    const box = typeof target === "string" ? document.getElementById(target) : target;
    if (!box || !errors?.length) return;
    const items = (errors || []).map((e) => {
      if (e && typeof e === "object" && "message" in e)
        return `Row ${e.row}, ${App.escapeHtml(e.column)}: ${App.escapeHtml(e.message)}`;
      return App.escapeHtml(String(e ?? "unknown error"));
    });
    box.innerHTML =
      `<div class="alert alert-danger py-2 mb-0 small">` +
      `<strong>Errors:</strong><ul class="mb-0 mt-1">${items.map((i) => `<li>${i}</li>`).join("")}</ul></div>`;
  }
})();
