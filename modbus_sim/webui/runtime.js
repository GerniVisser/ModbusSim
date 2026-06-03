/* Runtime view (REQUIREMENTS.md section 12): device list, grouped signal table,
 * live value editing, simulate/clear, and a 2s value auto-refresh that never
 * overwrites the field the user is currently editing. */
(function () {
  "use strict";

  const Runtime = {
    devices: [],
    selected: null,
    signals: [],            // signals of the selected device
    trafficInterface: "",
    valueTimer: null,
    enter,
    leave,
    selectDevice,
    reloadSelected,
  };
  window.Runtime = Runtime;

  function leave() {
    if (Runtime.valueTimer) {
      clearInterval(Runtime.valueTimer);
      Runtime.valueTimer = null;
    }
    Runtime.devices = [];
    Runtime.selected = null;
    Runtime.signals = [];
  }

  async function enter() {
    const cfg = await App.getJSON("/api/config");
    if (cfg.ok) {
      document.getElementById("rt-project").textContent = cfg.data.project_name;
      const vlan = cfg.data.vlan_mode ? "enabled" : "disabled";
      document.getElementById("rt-vlan").textContent = `VLAN: ${vlan} / ${cfg.data.traffic_interface}`;
      Runtime.trafficInterface = cfg.data.traffic_interface;
    }
    wireToolbar();
    await loadDevices();
    if (!Runtime.valueTimer) Runtime.valueTimer = setInterval(refreshValues, 2000);
  }

  function wireToolbar() {
    document.getElementById("rt-simulate-all").onclick = async () => {
      await App.postJSON("/api/simulate"); App.toast("Simulated all devices"); refreshValues();
    };
    document.getElementById("rt-clear-all").onclick = async () => {
      await App.postJSON("/api/clear"); App.toast("Cleared all devices"); refreshValues();
    };
    document.getElementById("rt-stop").onclick = async () => {
      if (!confirm("Stop the engine? It will exit (and restart in SETUP under systemd).")) return;
      await App.postJSON("/api/stop"); App.toast("Stopping engine…", "warning");
    };
    document.getElementById("rt-reset").onclick = async () => {
      if (!confirm(
        "Reset the engine to SETUP?\n\n" +
        "This will stop all Modbus servers, remove network interfaces, " +
        "and delete the current configuration so a new one can be uploaded.\n\n" +
        "The process will NOT restart."
      )) return;
      const r = await App.postJSON("/api/reset");
      if (r.ok) {
        App.toast("Engine reset to SETUP — upload a new config to continue", "warning");
      } else {
        App.toast((r.data && r.data.error) || "Reset failed", "danger");
      }
    };
    document.getElementById("rt-dev-simulate").onclick = async () => {
      await App.postJSON(`/api/devices/${enc(Runtime.selected)}/simulate`);
      App.toast("Loaded defaults"); refreshValues();
    };
    document.getElementById("rt-dev-clear").onclick = async () => {
      await App.postJSON(`/api/devices/${enc(Runtime.selected)}/clear`);
      App.toast("Cleared"); refreshValues();
    };
    document.getElementById("rt-dev-edit").onclick = () =>
      window.Editor.open(Runtime.selected, Runtime.signals);
    document.getElementById("rt-filter").oninput = applyFilter;
  }

  async function loadDevices() {
    const r = await App.getJSON("/api/devices");
    if (!r.ok) return;
    Runtime.devices = r.data;
    const list = document.getElementById("rt-devices");
    list.innerHTML = Runtime.devices.map((d) => `
      <a class="list-group-item list-group-item-action" data-id="${d.id}">
        <div class="d-flex align-items-center gap-2">
          <span class="status-dot"></span>
          <div><div class="fw-semibold">${App.escapeHtml(d.name)}</div>
          <div class="text-muted small mono">${d.ip} ${d.vlan ? "&middot; VLAN " + d.vlan : ""}</div></div>
        </div>
      </a>`).join("");
    list.querySelectorAll(".list-group-item").forEach((el) =>
      (el.onclick = () => selectDevice(el.dataset.id)));
    if (Runtime.devices.length) selectDevice(Runtime.selected || Runtime.devices[0].id);
  }

  async function selectDevice(id) {
    Runtime.selected = id;
    document.querySelectorAll("#rt-devices .list-group-item").forEach((el) =>
      el.classList.toggle("active", el.dataset.id === id));
    const r = await App.getJSON(`/api/devices/${enc(id)}/signals`);
    Runtime.signals = r.ok ? r.data : [];
    renderHeader();
    renderSignals();
    refreshValues();
  }

  async function reloadSelected() { await selectDevice(Runtime.selected); }

  function renderHeader() {
    const d = Runtime.devices.find((x) => x.id === Runtime.selected);
    if (!d) return;
    const iface = d.vlan ? `${Runtime.trafficInterface}.${d.vlan}` : Runtime.trafficInterface;
    document.getElementById("rt-device-header").innerHTML = `
      <div>
        <h5 class="mb-1">${App.escapeHtml(d.name)}</h5>
        <div class="text-muted mono">${d.ip}:${d.port} &middot; Unit ${d.unit_id}
          ${d.vlan ? "&middot; VLAN " + d.vlan + " &middot; " + iface : ""}</div>
      </div>
      <span class="badge text-bg-light">${d.signal_count} signals</span>`;
  }

  function renderSignals() {
    const bySection = {};
    Runtime.signals.forEach((s) => (bySection[s.section || "General"] ||= []).push(s));
    const container = document.getElementById("rt-signals");
    container.innerHTML = Object.entries(bySection).map(([section, sigs]) => `
      <table class="table table-sm signal-table mb-3">
        <thead><tr class="section-header"><td colspan="5">${App.escapeHtml(section)}</td></tr>
        <tr class="text-muted small"><th>Name</th><th>Raw</th><th>Scaled</th><th>Unit</th><th>Type</th></tr></thead>
        <tbody>${sigs.map(rowHtml).join("")}</tbody>
      </table>`).join("") || '<p class="text-muted">No signals.</p>';

    container.querySelectorAll("input.raw").forEach((inp) => {
      inp.onchange = () => commitValue(inp);
      inp.onkeydown = (e) => { if (e.key === "Enter") inp.blur(); };
    });
    container.querySelectorAll("input.bit").forEach((cb) =>
      (cb.onchange = () => setValue(cb.dataset.name, cb.checked)));
    applyFilter();
  }

  function rowHtml(s) {
    const nameCell = `<td data-name="${App.escapeHtml(s.name)}" data-desc="${App.escapeHtml(s.description || "")}">
      ${App.escapeHtml(s.name)}</td>`;
    if (s.data_type === "bool") {
      return `<tr>${nameCell}
        <td><div class="form-check form-switch"><input class="form-check-input bit" type="checkbox" data-name="${App.escapeHtml(s.name)}"></div></td>
        <td>&mdash;</td><td>${App.escapeHtml(s.unit || "")}</td><td><span class="badge text-bg-secondary">bool</span></td></tr>`;
    }
    return `<tr>${nameCell}
      <td><input class="form-control form-control-sm raw" type="number" step="any"
           data-name="${App.escapeHtml(s.name)}" data-scale="${s.scale}"></td>
      <td><span class="scaled" data-scaled="${App.escapeHtml(s.name)}"></span></td>
      <td>${App.escapeHtml(s.unit || "")}</td>
      <td><span class="badge text-bg-secondary">${s.data_type}</span></td></tr>`;
  }

  async function refreshValues() {
    if (!Runtime.selected) return;
    const r = await App.getJSON(`/api/devices/${enc(Runtime.selected)}/values`);
    if (!r.ok) return;
    const active = document.activeElement;
    for (const [name, value] of Object.entries(r.data)) {
      const sel = `[data-name="${CSS.escape(name)}"]`;
      const num = document.querySelector(`input.raw${sel}`);
      if (num) {
        if (num !== active) num.value = value;            // don't clobber a focused field
        updateScaled(name, num);
        continue;
      }
      const cb = document.querySelector(`input.bit${sel}`);
      if (cb && cb !== active) cb.checked = value === true || value === 1;
    }
  }

  function updateScaled(name, input) {
    const span = document.querySelector(`[data-scaled="${CSS.escape(name)}"]`);
    if (!span) return;
    const scale = Number(input.dataset.scale) || 1;
    const raw = Number(input.value);
    span.textContent = isNaN(raw) ? "" : +(raw * scale).toPrecision(8);
  }

  function commitValue(input) {
    const raw = Number(input.value);
    if (input.value === "" || isNaN(raw)) {
      input.classList.add("is-invalid");
      return;
    }
    input.classList.remove("is-invalid");
    updateScaled(input.dataset.name, input);
    setValue(input.dataset.name, raw);
  }

  async function setValue(name, value) {
    const r = await App.postJSON(`/api/devices/${enc(Runtime.selected)}/set`, { name, value });
    if (!r.ok) App.toast((r.data.errors || [r.data.error]).join("; "), "danger");
  }

  function applyFilter() {
    const q = document.getElementById("rt-filter").value.toLowerCase();
    document.querySelectorAll("#rt-signals tbody tr").forEach((tr) => {
      const cell = tr.querySelector("[data-name]");
      if (!cell) return;
      const hay = (cell.dataset.name + " " + cell.dataset.desc).toLowerCase();
      tr.style.display = !q || hay.includes(q) ? "" : "none";
    });
  }

  const enc = encodeURIComponent;
})();
