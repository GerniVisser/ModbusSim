/* Runtime view: virtual device list + virtual signal table + O(1) value refresh.
 *
 * Performance fix: after rendering, signal DOM elements are cached in _els (Map).
 * refreshValues() iterates _values by direct Map lookup — zero querySelector calls. */
(function () {
  "use strict";

  // ── module state ─────────────────────────────────────────────────────────
  let _devices = [];        // all devices from GET /api/devices
  let _selected = null;     // selected device id
  let _signals = [];        // signals of selected device
  let _values = {};         // latest name→rawValue

  let _devItems = [];       // flat array for device VList
  let _sigItems = [];       // flat array for signal VList (section hdrs + signals)
  let _filtSigItems = [];   // after filter applied

  let _devVL = null;        // VList for device list
  let _sigVL = null;        // VList for signal table
  let _els = new Map();     // signal name → { num, scaled, cb } — rebuilt on each VList render

  let _valueTimer = null;
  let _trafficInterface = "";

  const Runtime = { enter, leave, selectDevice, reloadSelected };
  window.Runtime = Runtime;

  // ── lifecycle ─────────────────────────────────────────────────────────────
  function leave() {
    if (_valueTimer) { clearInterval(_valueTimer); _valueTimer = null; }
    _devices = []; _selected = null; _signals = []; _values = {};
    _devItems = []; _sigItems = []; _filtSigItems = [];
    _els.clear();
    // Reset main panel to "select a device" state
    const hdr = document.getElementById("rt-device-header");
    if (hdr) hdr.innerHTML = '<span style="color:var(--muted);font-size:.875rem">← Select a device</span>';
    const tb = document.getElementById("rt-toolbar");
    if (tb) tb.style.display = "none";
    const sf = document.getElementById("rt-sig-filter-wrap");
    if (sf) sf.style.display = "none";
    const ch = document.getElementById("rt-sig-col-hdr");
    if (ch) ch.style.display = "none";
  }

  async function enter() {
    // Init VLists once; reuse on subsequent enter() calls.
    if (!_devVL) {
      _devVL = new VList(document.getElementById("rt-device-list"), 54, {
        onRender: _syncDevActive,
        onClick: (item) => { if (item.type === "device") selectDevice(item.id); },
      });
    }
    if (!_sigVL) {
      _sigVL = new VList(document.getElementById("rt-signal-area"), 36, {
        onRender: _rebuildElsCache,
      });
    }

    const cfg = await App.getJSON("/api/config");
    if (cfg.ok) {
      const el = document.getElementById("rt-project-name");
      if (el) el.textContent = cfg.data.project_name || "Project";
      _trafficInterface = cfg.data.traffic_interface || "";
    }

    _wireSearch();
    _wireToolbar();
    await _loadDevices();

    if (!_valueTimer) _valueTimer = setInterval(_refreshValues, 2000);
  }

  // ── device list ───────────────────────────────────────────────────────────
  async function _loadDevices() {
    const r = await App.getJSON("/api/devices");
    if (!r.ok) return;
    _devices = r.data;
    _devItems = _buildDevItems(_devices, "");
    _devVL.set(_devItems, _renderDevItem);
    if (_devices.length) {
      const keep = _selected && _devices.find((d) => d.id === _selected);
      await selectDevice(keep ? _selected : _devices[0].id);
    }
  }

  function _buildDevItems(devices, query) {
    const q = (query || "").toLowerCase();
    const filtered = q
      ? devices.filter(
          (d) =>
            d.name.toLowerCase().includes(q) ||
            d.ip.toLowerCase().includes(q) ||
            d.id.toLowerCase().includes(q)
        )
      : devices;

    const groups = new Map();
    for (const d of filtered) {
      const k = d.vlan || 0;
      if (!groups.has(k)) groups.set(k, []);
      groups.get(k).push(d);
    }

    const sortedKeys = [...groups.keys()].sort((a, b) => a - b);
    const items = [];
    for (const vlan of sortedKeys) {
      items.push({ type: "header", label: vlan ? `VLAN ${vlan}` : "No VLAN" });
      const sorted = groups.get(vlan).sort((a, b) => a.name.localeCompare(b.name));
      for (const d of sorted) items.push({ type: "device", ...d });
    }
    return items;
  }

  function _renderDevItem(item) {
    const el = document.createElement("div");
    if (item.type === "header") {
      el.className = "dev-group-hdr";
      el.textContent = item.label;
    } else {
      el.className = "dev-item" + (item.id === _selected ? " active" : "");
      el.dataset.devId = item.id;
      el.innerHTML =
        `<span class="status-dot"></span>` +
        `<div class="dev-info">` +
        `<div class="dev-name">${App.escapeHtml(item.name)}</div>` +
        `<div class="dev-meta">${App.escapeHtml(item.ip)}:${item.port} · Unit ${item.unit_id}</div>` +
        `</div>` +
        `<span class="dev-count">${item.signal_count}</span>`;
    }
    return el;
  }

  function _syncDevActive() {
    document
      .querySelectorAll("#rt-device-list .dev-item")
      .forEach((el) => el.classList.toggle("active", el.dataset.devId === _selected));
  }

  function _wireSearch() {
    const inp = document.getElementById("dev-search");
    if (!inp) return;
    inp.value = "";
    inp.oninput = () => {
      const items = _buildDevItems(_devices, inp.value);
      _devItems = items;
      _devVL.set(items, _renderDevItem);
    };
    // ArrowDown: jump to first device item in the filtered list
    inp.onkeydown = (e) => {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        const first = _devItems.find((x) => x.type === "device");
        if (first) selectDevice(first.id);
      }
    };
  }

  // ── device selection ──────────────────────────────────────────────────────
  async function selectDevice(id) {
    _selected = id;
    _syncDevActive();

    const idx = _devItems.findIndex((x) => x.type === "device" && x.id === id);
    if (idx >= 0) _devVL.scrollTo(idx);

    const d = _devices.find((x) => x.id === id);
    if (d) _renderDeviceHeader(d);

    document.getElementById("rt-toolbar").style.display = "";
    document.getElementById("rt-sig-filter-wrap").style.display = "";
    const colHdr = document.getElementById("rt-sig-col-hdr");
    if (colHdr) colHdr.style.display = "grid";

    const filterEl = document.getElementById("rt-filter");
    if (filterEl) filterEl.value = "";

    const r = await App.getJSON(`/api/devices/${enc(id)}/signals`);
    _signals = r.ok ? r.data : [];
    _sigItems = _buildSigItems(_signals);
    _filtSigItems = _sigItems.slice();
    _sigVL.set(_filtSigItems, _renderSigRow);

    await _refreshValues();
  }

  async function reloadSelected() { if (_selected) await selectDevice(_selected); }

  function _renderDeviceHeader(d) {
    const iface = d.vlan ? `${_trafficInterface}.${d.vlan}` : _trafficInterface;
    document.getElementById("rt-device-header").innerHTML =
      `<div class="flex-grow-1">` +
      `<div class="fw-semibold" style="font-size:.95rem">${App.escapeHtml(d.name)}</div>` +
      `<div style="font-size:.72rem;color:var(--muted);font-family:monospace">` +
      `${App.escapeHtml(d.ip)}:${d.port} · Unit ${d.unit_id}` +
      (d.vlan ? ` · VLAN ${d.vlan} · ${App.escapeHtml(iface)}` : "") +
      `</div></div>` +
      `<div class="d-flex align-items-center gap-2 ms-3">` +
      `<span class="badge bg-secondary">${d.signal_count} signals</span>` +
      `<button class="btn btn-outline-secondary btn-sm py-0 px-2" style="font-size:.72rem" id="rt-change-iface">` +
      `${App.escapeHtml(_trafficInterface)} ↕</button>` +
      `</div>`;
    document.getElementById("rt-change-iface").onclick = _openChangeIfaceModal;
  }

  // ── signal list ───────────────────────────────────────────────────────────
  function _buildSigItems(signals) {
    const sections = new Map();
    for (const s of signals) {
      const sec = s.section || "General";
      if (!sections.has(sec)) sections.set(sec, []);
      sections.get(sec).push(s);
    }
    const items = [];
    for (const [sec, sigs] of sections) {
      items.push({ type: "section", label: sec, count: sigs.length });
      for (const s of sigs) items.push({ type: "signal", ...s });
    }
    return items;
  }

  function _filterSigItems(items, query) {
    if (!query) return items.slice();
    const q = query.toLowerCase();
    const out = [];
    for (let i = 0; i < items.length; i++) {
      if (items[i].type === "section") {
        // Collect matching signals in this section
        const matching = [];
        let j = i + 1;
        while (j < items.length && items[j].type === "signal") {
          const s = items[j];
          if (
            (s.name || "").toLowerCase().includes(q) ||
            (s.description || "").toLowerCase().includes(q)
          )
            matching.push(items[j]);
          j++;
        }
        if (matching.length) { out.push(items[i]); out.push(...matching); }
        i = j - 1;
      }
    }
    return out;
  }

  const _REG_COLOR = {
    holding: "primary", input: "info", coil: "success", discrete_input: "warning",
  };

  function _renderSigRow(item) {
    const el = document.createElement("div");
    if (item.type === "section") {
      el.className = "sig-section-hdr";
      el.innerHTML =
        App.escapeHtml(item.label) +
        `<span class="ms-2" style="font-weight:400;font-size:.65rem;color:var(--muted)">${item.count}</span>`;
      return el;
    }

    el.className = "sig-row";
    el.dataset.sig = item.name;
    if (item.description) el.title = item.description;

    const regColor = _REG_COLOR[item.register_type] || "secondary";
    const regLabel = (item.register_type || "").replace("_", " ");

    if (item.data_type === "bool") {
      el.innerHTML =
        `<div class="sig-name">${App.escapeHtml(item.name)}</div>` +
        `<span class="badge text-bg-${regColor} sig-badge">${App.escapeHtml(regLabel)}</span>` +
        `<div><div class="form-check form-switch mb-0 ms-1">` +
        `<input class="form-check-input sig-bool" type="checkbox" data-sig="${App.escapeHtml(item.name)}">` +
        `</div></div>` +
        `<div class="sig-scaled">—</div>` +
        `<div class="sig-unit">${App.escapeHtml(item.unit || "")}</div>`;
      el.querySelector(".sig-bool").onchange = (e) =>
        _setValue(item.name, e.target.checked);
    } else {
      el.innerHTML =
        `<div class="sig-name">${App.escapeHtml(item.name)}</div>` +
        `<span class="badge text-bg-${regColor} sig-badge">${App.escapeHtml(regLabel)}</span>` +
        `<div><input class="sig-num" type="number" step="any"` +
        ` data-sig="${App.escapeHtml(item.name)}" data-scale="${item.scale || 1}"></div>` +
        `<div class="sig-scaled">—</div>` +
        `<div class="sig-unit">${App.escapeHtml(item.unit || "")}</div>`;
      const inp = el.querySelector(".sig-num");
      inp.onchange = () => _commitValue(inp);
      inp.onkeydown = (e) => { if (e.key === "Enter") inp.blur(); };
    }
    return el;
  }

  // ── element cache (O(1) value refresh) ───────────────────────────────────
  function _rebuildElsCache() {
    _els.clear();
    const area = document.getElementById("rt-signal-area");
    if (!area) return;
    area.querySelectorAll(".sig-row[data-sig]").forEach((row) => {
      const name = row.dataset.sig;
      _els.set(name, {
        num:    row.querySelector(".sig-num")  || null,
        scaled: row.querySelector(".sig-scaled") || null,
        cb:     row.querySelector(".sig-bool")  || null,
      });
    });
    _applyValues();
  }

  // ── value refresh ─────────────────────────────────────────────────────────
  async function _refreshValues() {
    if (!_selected) return;
    const r = await App.getJSON(`/api/devices/${enc(_selected)}/values`);
    if (!r.ok) return;
    _values = r.data;
    _applyValues();
  }

  function _applyValues() {
    const active = document.activeElement;
    for (const [name, value] of Object.entries(_values)) {
      const e = _els.get(name);
      if (!e) continue;  // not in visible range — skip
      if (e.num) {
        if (e.num !== active) {
          const str = String(value);
          if (e.num.value !== str) e.num.value = str;
          if (e.scaled) e.scaled.textContent = _fmtScaled(value, e.num.dataset.scale);
        }
      } else if (e.cb && e.cb !== active) {
        const checked = value === true || value === 1;
        if (e.cb.checked !== checked) e.cb.checked = checked;
      }
    }
  }

  function _fmtScaled(raw, scale) {
    const s = Number(scale) || 1;
    if (s === 1) return "";
    const v = Number(raw) * s;
    return isNaN(v) ? "—" : String(+v.toPrecision(7));
  }

  function _commitValue(input) {
    const raw = Number(input.value);
    if (input.value === "" || isNaN(raw)) { input.classList.add("is-invalid"); return; }
    input.classList.remove("is-invalid");
    _setValue(input.dataset.sig, raw);
    const e = _els.get(input.dataset.sig);
    if (e?.scaled) e.scaled.textContent = _fmtScaled(raw, input.dataset.scale);
  }

  async function _setValue(name, value) {
    const r = await App.postJSON(`/api/devices/${enc(_selected)}/set`, { name, value });
    if (!r.ok) App.toast((r.data.errors || [r.data.error]).join("; "), "danger");
  }

  // ── toolbar wiring ────────────────────────────────────────────────────────
  function _wireToolbar() {
    document.getElementById("rt-simulate-all").onclick = async () => {
      await App.postJSON("/api/simulate"); App.toast("Simulated all devices"); _refreshValues();
    };
    document.getElementById("rt-clear-all").onclick = async () => {
      await App.postJSON("/api/clear"); App.toast("Cleared all devices"); _refreshValues();
    };
    document.getElementById("rt-stop").onclick = async () => {
      if (!confirm("Stop the engine? It will shut down and restart in SETUP state.")) return;
      await App.postJSON("/api/stop"); App.toast("Engine stopping…", "warning");
    };
    document.getElementById("rt-reset").onclick = async () => {
      if (!confirm(
        "Reset to SETUP?\n\nThis stops all Modbus servers, removes network interfaces, " +
        "and deletes the current config so a new one can be uploaded."
      )) return;
      const r = await App.postJSON("/api/reset");
      if (r.ok) App.toast("Reset to SETUP", "warning");
      else App.toast(r.data?.error || "Reset failed", "danger");
    };
    document.getElementById("rt-dev-simulate").onclick = async () => {
      if (!_selected) return;
      await App.postJSON(`/api/devices/${enc(_selected)}/simulate`);
      App.toast("Loaded defaults"); _refreshValues();
    };
    document.getElementById("rt-dev-clear").onclick = async () => {
      if (!_selected) return;
      await App.postJSON(`/api/devices/${enc(_selected)}/clear`);
      App.toast("Cleared"); _refreshValues();
    };
    document.getElementById("rt-dev-edit").onclick = () => {
      if (_selected) window.Editor?.open(_selected, _signals);
    };

    // Signal filter
    const sf = document.getElementById("rt-filter");
    if (sf) {
      sf.oninput = () => {
        _filtSigItems = _filterSigItems(_sigItems, sf.value);
        _sigVL.set(_filtSigItems, _renderSigRow);
      };
    }
  }

  // ── change interface modal ────────────────────────────────────────────────
  function _openChangeIfaceModal() {
    const MID = "change-iface-modal";
    if (!document.getElementById(MID)) {
      const w = document.createElement("div");
      w.innerHTML =
        `<div class="modal fade" id="${MID}" tabindex="-1">` +
        `<div class="modal-dialog"><div class="modal-content">` +
        `<div class="modal-header">` +
        `<h5 class="modal-title">Change Traffic Interface</h5>` +
        `<button type="button" class="btn-close" data-bs-dismiss="modal"></button>` +
        `</div><div class="modal-body">` +
        `<p class="text-muted small mb-3">Brief Modbus outage while the network is reconfigured.</p>` +
        `<label class="form-label fw-semibold small">Interface</label>` +
        `<select class="form-select" id="ci-select"><option>Loading…</option></select>` +
        `<div id="ci-err" class="mt-2"></div>` +
        `</div><div class="modal-footer">` +
        `<button class="btn btn-secondary" data-bs-dismiss="modal">Cancel</button>` +
        `<button class="btn btn-primary" id="ci-apply">Apply</button>` +
        `</div></div></div></div>`;
      document.body.appendChild(w.firstElementChild);
    }

    App.getJSON("/api/interfaces").then((r) => {
      const sel = document.getElementById("ci-select");
      if (!r.ok || !r.data?.length) { sel.innerHTML = '<option value="">None found</option>'; return; }
      sel.innerHTML = r.data
        .map(
          (i) =>
            `<option value="${App.escapeHtml(i.name)}"${i.name === _trafficInterface ? " selected" : ""}>` +
            `${App.escapeHtml(i.name)} — ${App.escapeHtml(i.mac)} (${i.state})</option>`
        )
        .join("");
    });

    document.getElementById("ci-apply").onclick = async () => {
      const iface = document.getElementById("ci-select").value;
      if (!iface) return;
      const btn = document.getElementById("ci-apply");
      btn.disabled = true;
      btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Applying…';
      const r = await App.postJSON("/api/config/interface", { interface: iface });
      btn.disabled = false; btn.innerHTML = "Apply";
      if (r.ok) {
        bootstrap.Modal.getInstance(document.getElementById(MID)).hide();
        App.toast(`Interface → ${iface}`, "success");
        _trafficInterface = iface;
        Runtime.enter();
      } else {
        document.getElementById("ci-err").innerHTML =
          `<div class="alert alert-danger py-2 small mb-0">${App.escapeHtml(r.data?.error || "Failed")}</div>`;
      }
    };

    new bootstrap.Modal(document.getElementById(MID)).show();
  }

  const enc = encodeURIComponent;
})();
