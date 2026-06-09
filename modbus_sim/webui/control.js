/* Engine control strip — polls /control/status every 3s, reflects state in the
 * top bar, and lets the user start/stop/restart the engine without touching Linux.
 * Only wired up when the GUI is running through gui_server.py (the /control/* routes
 * don't exist when running main.py directly). Falls back gracefully if missing. */
(function () {
  "use strict";

  const POLL_MS = 3000;
  const COLORS = {
    running:  "#2ecc71",
    starting: "#f39c12",
    stopped:  "#e74c3c",
    crashed:  "#c0392b",
  };

  let _logsOpen = false;
  let _lastState = null;

  function dot()      { return document.getElementById("ctrl-dot"); }
  function txt()      { return document.getElementById("ctrl-txt"); }
  function logPanel() { return document.getElementById("ctrl-logs"); }

  async function poll() {
    try {
      const r = await fetch("/control/status");
      if (!r.ok) return;         // not running through gui_server — silent skip
      const data = await r.json();
      update(data);
    } catch (_) {
      // gui_server unreachable — don't disturb existing conn-banner logic
    }
  }

  function update(data) {
    const { state, exit_code, log } = data;
    dot().style.background = COLORS[state] || "#555";

    let label = "Engine: " + state;
    if (state === "crashed" && exit_code != null) label += " (exit " + exit_code + ")";
    txt().textContent = label;

    // When engine goes from running → stopped/crashed, prompt in case user
    // is looking at a stale runtime view.
    if (_lastState === "running" && (state === "stopped" || state === "crashed")) {
      App.toast("Engine stopped" + (state === "crashed" ? " (crashed)" : ""), "warning");
    }
    _lastState = state;

    if (_logsOpen && log && log.length) {
      const panel = logPanel();
      const atBottom = panel.scrollTop + panel.clientHeight >= panel.scrollHeight - 20;
      panel.textContent = log.join("\n");
      if (atBottom) panel.scrollTop = panel.scrollHeight;
    }
  }

  function wire() {
    document.getElementById("ctrl-start").onclick = async () => {
      const r = await fetch("/control/start", { method: "POST" });
      const data = await r.json();
      if (data.ok) {
        App.toast("Engine starting" + (data.restore ? " (restoring from disk)…" : "…"), "info");
      } else {
        App.toast(data.error || "Start failed", "danger");
      }
    };

    document.getElementById("ctrl-stop").onclick = async () => {
      if (!confirm("Stop the Modbus engine?")) return;
      const r = await fetch("/control/stop", { method: "POST" });
      const data = await r.json();
      App.toast(data.ok ? "Engine stopping…" : (data.error || "Stop failed"),
                data.ok ? "warning" : "danger");
    };

    document.getElementById("ctrl-restart").onclick = async () => {
      if (!confirm("Restart the Modbus engine? There will be a brief service outage.")) return;
      App.toast("Restarting engine…", "info");
      fetch("/control/restart", { method: "POST" });  // fire-and-forget; poll reflects result
    };

    document.getElementById("ctrl-logs-btn").onclick = () => {
      _logsOpen = !_logsOpen;
      logPanel().style.display = _logsOpen ? "block" : "none";
      document.getElementById("ctrl-logs-btn").textContent = _logsOpen ? "Hide Logs" : "Logs";
      if (_logsOpen) poll();  // immediate refresh
    };
  }

  document.addEventListener("DOMContentLoaded", () => {
    wire();
    poll();
    setInterval(poll, POLL_MS);
  });
})();
