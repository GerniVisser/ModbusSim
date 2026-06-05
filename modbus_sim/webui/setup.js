/* Setup wizard (REQUIREMENTS.md section 12).
 * 3 steps: upload sim_config.yaml -> upload a signal CSV per device -> start.
 * Reads /api/setup/status so the wizard rebuilds correctly after a page reload. */
(function () {
  "use strict";
  const body = () => document.getElementById("setup-body");

  const Setup = { enter, refresh };
  window.Setup = Setup;

  function setStep(n, status) {
    document.querySelectorAll(".wizard-steps .step").forEach((el) => {
      const s = Number(el.dataset.step);
      el.classList.toggle("active", s === n);
      el.classList.toggle("done", s < n);
    });
  }

  async function enter() { await refresh(); }

  // Decide which step to render based on current engine setup status.
  async function refresh() {
    const r = await App.getJSON("/api/setup/status");
    if (!r.ok) return;
    const st = r.data;
    if (!st.config_uploaded) return renderStep1();
    if (!st.can_start) return renderStep2(st);
    return renderStep3(st);
  }

  // ---- Step 1: config --------------------------------------------------------
  function renderStep1(errors) {
    setStep(1);
    body().innerHTML = `
      <h5 class="mb-3">Step 1 &mdash; Upload Configuration</h5>
      <p class="text-muted">Select your <code>sim_config.yaml</code>. Once accepted it is
        <strong>locked</strong> for this VM session.</p>
      <div class="input-group">
        <input type="file" class="form-control" id="cfg-file" accept=".yaml,.yml">
        <button class="btn btn-primary" id="cfg-upload">Upload</button>
      </div>
      <div id="cfg-errors" class="mt-3"></div>
      <hr class="my-3">
      <p class="text-muted small mb-2">Have a Zenon 15 Engineering Studio variable export?
        Generate the config and signal files automatically.</p>
      <button class="btn btn-outline-secondary btn-sm" id="zenon-import-btn">
        &#8593; Import from Zenon CSV
      </button>`;
    if (errors) renderErrors("cfg-errors", errors);
    document.getElementById("cfg-upload").onclick = uploadConfig;
    document.getElementById("zenon-import-btn").onclick = () => {
      if (window.ZenonImport) ZenonImport.open();
    };
  }

  async function uploadConfig() {
    const f = document.getElementById("cfg-file").files[0];
    if (!f) return App.toast("Choose a file first", "warning");
    const r = await App.postForm("/api/setup/config", f);
    if (r.ok) { App.toast(`Config accepted: ${r.data.project_name}`); refresh(); }
    else if (r.status === 409) refresh();
    else renderErrors("cfg-errors", r.data.errors || [r.data.error]);
  }

  // ---- Step 2: per-device signal files --------------------------------------
  function renderStep2(st) {
    setStep(2);
    const rows = st.devices.map((d) => `
      <div class="list-group-item d-flex align-items-center gap-2">
        <span class="me-2">${d.signals_uploaded ? "&#9989;" : "&#9711;"}</span>
        <div class="flex-grow-1">
          <div class="fw-semibold">${App.escapeHtml(d.id)}</div>
          <div class="text-muted small">${App.escapeHtml(d.name)}</div>
        </div>
        ${d.signals_uploaded
          ? '<span class="badge text-bg-success">uploaded</span>'
          : `<input type="file" accept=".csv" class="form-control form-control-sm sig-file" data-id="${d.id}" style="max-width:14rem;">
             <button class="btn btn-sm btn-primary sig-upload" data-id="${d.id}">Upload</button>`}
      </div>
      <div class="px-3 sig-errors" data-id="${d.id}"></div>`).join("");

    body().innerHTML = `
      <h5 class="mb-3">Step 2 &mdash; Upload Signal Files</h5>
      <p class="text-muted">Each device needs a signal CSV.</p>
      <div class="list-group mb-3">${rows}</div>
      <button class="btn btn-primary" id="to-step3" ${st.can_start ? "" : "disabled"}>Continue to Start</button>`;

    document.querySelectorAll(".sig-upload").forEach((b) => (b.onclick = () => uploadSignals(b.dataset.id)));
    document.getElementById("to-step3").onclick = refresh;
  }

  async function uploadSignals(id) {
    const input = document.querySelector(`.sig-file[data-id="${CSS.escape(id)}"]`);
    const f = input && input.files[0];
    if (!f) return App.toast("Choose a CSV first", "warning");
    const r = await App.postForm(`/api/setup/signals/${encodeURIComponent(id)}`, f);
    const box = document.querySelector(`.sig-errors[data-id="${CSS.escape(id)}"]`);
    if (r.ok) { App.toast(`${id}: ${r.data.signal_count} signals loaded`); refresh(); }
    else renderErrors(box, r.data.errors || [r.data.error]);
  }

  // ---- Step 3: start ---------------------------------------------------------
  function renderStep3(st) {
    setStep(3);
    body().innerHTML = `
      <h5 class="mb-3">Step 3 &mdash; Start Simulation</h5>
      <p>All <strong>${st.devices_total}</strong> device(s) have signal files. Starting will
        configure the network (VLAN interfaces + IPs) and launch the Modbus servers.</p>
      <div id="start-error" class="mb-3"></div>
      <button class="btn btn-success btn-lg" id="start-btn">&#9654; Start Simulation</button>`;
    document.getElementById("start-btn").onclick = start;
  }

  async function start() {
    const btn = document.getElementById("start-btn");
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>Starting&hellip;';
    const r = await App.postJSON("/api/setup/start");
    if (r.ok) { App.toast("Simulation started"); /* state poll switches view */ }
    else {
      btn.disabled = false; btn.innerHTML = "&#9654; Start Simulation";
      renderErrors("start-error", r.data.errors || [r.data.error]);
    }
  }

  // ---- shared error rendering ------------------------------------------------
  function renderErrors(target, errors) {
    const box = typeof target === "string" ? document.getElementById(target) : target;
    if (!box) return;
    const items = (errors || []).map((e) => {
      if (e && typeof e === "object" && "message" in e)
        return `Row ${e.row}, ${App.escapeHtml(e.column)}: ${App.escapeHtml(e.message)}`;
      return App.escapeHtml(e);
    });
    box.innerHTML = `<div class="alert alert-danger mb-0"><strong>Validation errors:</strong>
      <ul class="mb-0">${items.map((i) => `<li>${i}</li>`).join("")}</ul></div>`;
  }
})();
