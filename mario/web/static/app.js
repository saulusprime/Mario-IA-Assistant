/* Dashboard LEGO Mario: helper API, barra di stato, flusso eventi SSE, logica per pagina. */
(function () {
  const page = document.body.dataset.page;
  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));
  const hhmmss = (ts) => new Date((ts || Date.now() / 1000) * 1000).toLocaleTimeString("it-IT");
  const fmtDate = (ts) => ts ? new Date(ts * 1000).toLocaleString("it-IT") : "-";

  async function api(path, method, data) {
    const opts = { method: method || "GET", headers: {} };
    if (data !== undefined) { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(data); }
    const res = await fetch(path, opts);
    let json = {};
    try { json = await res.json(); } catch (e) { json = { ok: false, error: "risposta non valida (" + res.status + ")" }; }
    if (!json.ok) throw new Error(json.error || ("errore " + res.status));
    return json;
  }
  function busy(btn, on) { if (btn) { btn.disabled = on; btn.dataset.label = btn.dataset.label || btn.textContent; btn.textContent = on ? "attendi…" : btn.dataset.label; } }
  function flash(el, text, isError) { if (!el) return; el.textContent = text; el.style.color = isError ? "#d7262c" : ""; }

  // ---- barra di stato -------------------------------------------------------------------
  const STATE_LABEL = { disconnected: "scollegato", scanning: "scansione…", connecting: "connessione…", connected: "connesso", discovering: "scoperta…" };
  const STATE_CLASS = { disconnected: "err", scanning: "busy", connecting: "busy", connected: "ok", discovering: "busy" };
  let lastStatus = null;
  function renderStatus(st) {
    lastStatus = st;
    const badge = $("#st-state");
    badge.textContent = STATE_LABEL[st.state] || st.state; badge.className = "badge state " + (STATE_CLASS[st.state] || "");
    $("#st-device").textContent = st.device ? `${st.device.name || "?"} [${st.device.address}]${st.device.bonded ? " · associato" : ""}` : "nessun dispositivo";
    $("#st-battery").textContent = st.battery !== null && st.battery !== undefined ? `🔋 ${st.battery}%` : "";
    $("#st-progress").textContent = st.error ? ("⚠ " + st.error) : (st.progress && st.progress.length ? st.progress[st.progress.length - 1] : "");
    document.dispatchEvent(new CustomEvent("mario:status", { detail: st }));
  }
  async function refreshStatus() { try { renderStatus((await api("/api/status")).status); } catch (e) { $("#st-progress").textContent = "server non raggiungibile"; } }
  refreshStatus(); setInterval(refreshStatus, 2500);

  async function doAction(name, btn, extra) {
    busy(btn, true);
    try {
      if (name === "scan") { const r = await api("/api/scan", "POST", { duration: 8 }); flash($("#st-progress"), `trovati ${r.devices.length} dispositivi LEGO`); if (page !== "scan") location.href = "/scan"; }
      else if (name === "connect") { await api("/api/connect", "POST", { address: extra || "" }); }
      else if (name === "disconnect") { await api("/api/disconnect", "POST", {}); }
      else if (name === "discover") { await api("/api/discover", "POST", { full: 1 }); if (page === "report" || page === "devices") location.reload(); }
      else if (name === "off") { if (confirm("Spegnere Mario?")) await api("/api/hub_action", "POST", { action: "SWITCH_OFF" }); }
      else if (name === "subscribe-defaults") { await api("/api/subscribe_defaults", "POST", {}); }
      else if (name === "unpair") { if (confirm("Rimuovere l'associazione? Al prossimo collegamento verrà rifatto il pairing.")) await api("/api/unpair", "POST", { address: extra || "" }); }
      await refreshStatus();
    } catch (e) { flash($("#st-progress"), e.message, true); alert(e.message); }
    finally { busy(btn, false); }
  }
  document.addEventListener("click", (ev) => {
    const b = ev.target.closest("button"); if (!b) return;
    if (b.dataset.action) doAction(b.dataset.action, b);
    else if (b.dataset.connect !== undefined) doAction("connect", b, b.dataset.connect);
    else if (b.dataset.unpair !== undefined) doAction("unpair", b, b.dataset.unpair);
    else if (b.id === "st-connect") doAction("connect", b);
    else if (b.id === "st-disconnect") doAction("disconnect", b);
  });

  // ---- flusso eventi (SSE) -----------------------------------------------------------
  const listeners = [];
  function onEvent(fn) { listeners.push(fn); }
  function startStream() {
    const es = new EventSource("/api/stream");
    es.onmessage = (m) => { let ev; try { ev = JSON.parse(m.data); } catch (e) { return; } listeners.forEach((fn) => { try { fn(ev); } catch (err) { console.error(err); } }); };
    es.onerror = () => { /* EventSource riprova da solo */ };
  }
  const isBattery = (ev) => ev.io_type === 20 || (ev.prop === "BATTERY_VOLTAGE");
  function eventRow(ev, cols) {
    const tr = document.createElement("tr");
    tr.innerHTML = cols.map((c) => `<td>${c}</td>`).join("");
    return tr;
  }
  const esc = (s) => String(s === undefined || s === null ? "" : s).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));

  // ---- pagine -------------------------------------------------------------------------
  if (page === "index") {
    const tbody = $("#ov-events tbody");
    onEvent((ev) => {
      if (!ev.interesting || ev.type === "STATE" || isBattery(ev)) return;
      tbody.prepend(eventRow(ev, [hhmmss(ev.ts), ev.port !== undefined ? ev.port : "", esc(ev.decoded || ev.text)]));
      while (tbody.children.length > 30) tbody.lastChild.remove();
    });
    document.addEventListener("mario:status", (e) => {
      const st = e.detail;
      $("#ov-state").textContent = STATE_LABEL[st.state] || st.state;
      $("#ov-device").textContent = st.device ? `${st.device.name || "?"} (fw ${st.device.fw || "?"})` : "-";
      $("#ov-address").textContent = st.device ? st.device.address : "-";
      $("#ov-battery").textContent = st.battery !== null && st.battery !== undefined ? st.battery + " %" : "-";
      $("#ov-subs").textContent = Object.keys(st.subscriptions || {}).length ? Object.entries(st.subscriptions).map(([p, s]) => `${p}:${s.name || s.mode}`).join(", ") : "-";
      $("#ov-progress").textContent = (st.progress || []).join("\n");
    });
    startStream();
  }

  if (page === "scan") {
    $("#scan-start").addEventListener("click", async (ev) => {
      const btn = ev.target; busy(btn, true); flash($("#scan-status"), "scansione in corso…");
      try {
        const r = await api("/api/scan", "POST", { duration: Number($("#scan-duration").value), any: $("#scan-any").checked });
        const tbody = $("#scan-results tbody"); tbody.innerHTML = "";
        r.devices.forEach((d) => tbody.appendChild(eventRow(d, [`<code>${d.address}</code>`, esc(d.name), esc(d.type_name), `${d.rssi} dBm`, `<code>${d.manufacturer}</code>`, `<button class="small" data-connect="${d.address}">Connetti</button>`])));
        flash($("#scan-status"), r.devices.length ? `${r.devices.length} dispositivi` : "nessun Mario: premi il tasto Bluetooth e riprova");
      } catch (e) { flash($("#scan-status"), e.message, true); } finally { busy(btn, false); }
    });
  }

  if (page === "devices") {
    const vol = $("#dev-vol"); if (vol) vol.addEventListener("input", () => { $("#dev-vol-val").textContent = vol.value; });
    document.addEventListener("click", async (ev) => {
      const b = ev.target.closest("button"); if (!b) return;
      if (b.dataset.deleteDevice) { if (confirm("Eliminare il dispositivo e i suoi eventi dal database?")) { await api("/api/devices/" + b.dataset.deleteDevice, "DELETE"); location.reload(); } }
      if (b.id === "dev-save-notes") { const ta = $("#dev-notes"); try { await api("/api/devices/" + ta.dataset.deviceId, "POST", { notes: ta.value }); flash($("#dev-msg"), "note salvate"); } catch (e) { flash($("#dev-msg"), e.message, true); } }
      if (b.id === "dev-name-set") { const inp = $("#dev-name-new"); busy(b, true); try { const r = await api("/api/name", "POST", { name: inp.value }); await api("/api/devices/" + inp.dataset.deviceId, "POST", { name: r.name }); flash($("#dev-msg"), `nome impostato: ${r.name}`); } catch (e) { flash($("#dev-msg"), e.message, true); } finally { busy(b, false); } }
      if (b.id === "dev-vol-set") { const inp = $("#dev-name-new"); busy(b, true); try { const r = await api("/api/volume", "POST", { level: vol.value }); await api("/api/devices/" + inp.dataset.deviceId, "POST", { volume: r.volume }); flash($("#dev-msg"), `volume impostato: ${r.volume} %`); } catch (e) { flash($("#dev-msg"), e.message, true); } finally { busy(b, false); } }
    });
  }

  if (page === "events") {
    const msg = $("#evp-msg"), tbody = $("#evp-table tbody");
    const isoToTs = (v) => v ? String(new Date(v).getTime() / 1000) : "";
    function query(extra) {
      const q = new URLSearchParams({ device_id: $("#evp-device").value, port: $("#evp-port").value, type: $("#evp-type").value, q: $("#evp-q").value, limit: $("#evp-limit").value });
      const f = isoToTs($("#evp-from").value), t = isoToTs($("#evp-to").value); if (f) q.set("from", f); if (t) q.set("to", t);
      Object.entries(extra || {}).forEach(([k, v]) => q.set(k, v));
      return q;
    }
    async function loadTable() {
      try { const r = await api("/api/history?" + query()); tbody.innerHTML = "";
        r.events.forEach((e) => tbody.appendChild(eventRow(e, [fmtDate(e.ts), e.device_id ?? "", e.port ?? "", e.mode ?? "", esc(e.type), esc(e.decoded || e.text), `<code>${esc(e.raw)}</code>`])));
        flash(msg, `${r.events.length} eventi mostrati, ${r.stats.total} in archivio`); } catch (e) { flash(msg, e.message, true); }
    }
    function drawBars(el, data, alt) {
      el.innerHTML = ""; const entries = Object.entries(data || {}); const max = Math.max(1, ...entries.map(([, v]) => v));
      if (!entries.length) { el.innerHTML = '<span class="muted">nessun dato</span>'; return; }
      entries.slice(0, 16).forEach(([k, v]) => { el.insertAdjacentHTML("beforeend", `<span>${esc(k)}</span><div class="bar${alt ? " alt" : ""}" style="width:${Math.round(100 * v / max)}%"></div><span>${v}</span>`); });
    }
    function drawBattery(svg, series) {
      const W = 600, H = 220, pad = 28; svg.innerHTML = "";
      const all = [...series.percent, ...series.millivolt]; if (!all.length) { svg.innerHTML = '<text x="20" y="40" fill="#6b6f7a">nessun dato</text>'; return; }
      const t0 = Math.min(...all.map((p) => p.ts)), t1 = Math.max(...all.map((p) => p.ts)) || t0 + 1;
      const x = (ts) => pad + (W - 2 * pad) * (t1 > t0 ? (ts - t0) / (t1 - t0) : 0.5), y = (v) => H - pad - (H - 2 * pad) * Math.max(0, Math.min(100, v)) / 100;
      let g = `<line x1="${pad}" y1="${H - pad}" x2="${W - pad}" y2="${H - pad}" stroke="#ccc"/><line x1="${pad}" y1="${pad}" x2="${pad}" y2="${H - pad}" stroke="#ccc"/>`;
      [0, 50, 100].forEach((v) => { g += `<text x="2" y="${y(v) + 4}" font-size="10" fill="#6b6f7a">${v}</text>`; });
      const line = (pts, style) => pts.length ? `<polyline fill="none" ${style} points="${pts.map((p) => `${x(p.ts).toFixed(1)},${y(p.v).toFixed(1)}`).join(" ")}"/>` : "";
      g += line(series.percent.map((p) => ({ ts: p.ts, v: p.value })), 'stroke="#1f5fbf" stroke-width="2"');
      g += line(series.millivolt.map((p) => ({ ts: p.ts, v: (p.value - 2000) / 13 })), 'stroke="#d7262c" stroke-width="1.5" stroke-dasharray="4 3"');
      svg.innerHTML = g;
      $("#chart-battery-info").textContent = `${series.percent.length} letture %, ${series.millivolt.length} letture mV, ${fmtDate(t0)} → ${fmtDate(t1)}`;
    }
    async function loadCharts() {
      try { const r = await api("/api/charts?device_id=" + encodeURIComponent($("#evp-device").value));
        drawBattery($("#chart-battery"), r.battery); drawBars($("#chart-gestures"), r.gestures); drawBars($("#chart-tiles"), r.tiles, true); drawBars($("#chart-types"), r.types); } catch (e) { flash(msg, e.message, true); }
    }
    $("#evp-load").addEventListener("click", () => { loadTable(); loadCharts(); });
    $("#evp-csv").addEventListener("click", () => { location.href = "/api/history.csv?" + query({ limit: 50000 }); });
    $("#evp-json").addEventListener("click", () => { location.href = "/api/history.json?" + query({ limit: 50000 }); });
    $("#evp-device").addEventListener("change", loadCharts);
    loadTable(); loadCharts();
  }

  if (page === "monitor") {
    const cards = $("#port-cards"), tbody = $("#ev-table tbody"); let count = 0;
    function renderPorts(st) {
      cards.innerHTML = "";
      Object.entries(st.ports || {}).forEach(([p, info]) => {
        const div = document.createElement("div"); div.className = "port" + (info.notify ? " active" : "");
        const last = info.last || {};
        div.innerHTML = `<h3>Porta ${p} · ${esc(info.io_name)}</h3><div class="muted">${esc(info.io_type)} · modo ${info.current_mode ?? "-"} ${esc(info.mode_name || "")} ${info.notify ? "· notifiche ON" : ""}</div><div class="val">${esc(last.text || "—")}</div><div class="raw">${esc(last.raw || "")} ${last.ts ? hhmmss(last.ts) : ""}</div><div class="muted">modi: ${Object.entries(info.modes || {}).map(([m, n]) => `${m}=${n}`).join(" ")}</div>`;
        cards.appendChild(div);
      });
      if (!Object.keys(st.ports || {}).length) cards.innerHTML = '<p class="muted">Nessuna porta: connetti Mario.</p>';
    }
    document.addEventListener("mario:status", (e) => renderPorts(e.detail));
    onEvent((ev) => {
      if (!ev.interesting || ev.type === "STATE" || ev.type === "STATUS") return;
      if ($("#ev-pause").checked) return;
      const pf = $("#ev-port").value; if (pf !== "" && String(ev.port) !== pf) return;
      if ($("#ev-hide-battery").checked && isBattery(ev)) return;
      count++; $("#ev-count").textContent = count;
      tbody.prepend(eventRow(ev, [hhmmss(ev.ts), ev.port ?? "", ev.mode ?? "", esc(ev.decoded || ev.text), `<code>${esc(ev.raw)}</code>`]));
      while (tbody.children.length > 300) tbody.lastChild.remove();
      if (ev.port !== undefined && lastStatus) { const card = cards.children[Object.keys(lastStatus.ports || {}).indexOf(String(ev.port))]; if (card) { card.querySelector(".val").textContent = ev.decoded || ev.text; card.querySelector(".raw").textContent = `${ev.raw} ${hhmmss(ev.ts)}`; } }
    });
    $("#ev-clear").addEventListener("click", () => { tbody.innerHTML = ""; count = 0; $("#ev-count").textContent = 0; });
    const sub = async (notify) => { try { const r = await api("/api/subscribe", "POST", { port: $("#sub-port").value, mode: $("#sub-mode").value, delta: $("#sub-delta").value, notify: notify ? 1 : 0 }); flash($("#sub-msg"), r.reply); refreshStatus(); } catch (e) { flash($("#sub-msg"), e.message, true); } };
    $("#sub-on").addEventListener("click", () => sub(true));
    $("#sub-off").addEventListener("click", () => sub(false));
    $("#sub-value").addEventListener("click", async () => { try { const r = await api("/api/value", "POST", { port: $("#sub-port").value }); flash($("#sub-msg"), "valore grezzo: " + r.raw); } catch (e) { flash($("#sub-msg"), e.message, true); } });
    $("#hist-load").addEventListener("click", async () => {
      const q = new URLSearchParams({ port: $("#hist-port").value, q: $("#hist-q").value, limit: $("#hist-limit").value });
      try { const r = await api("/api/history?" + q); const tb = $("#hist-table tbody"); tb.innerHTML = "";
        r.events.forEach((e) => tb.appendChild(eventRow(e, [fmtDate(e.ts), e.port ?? "", esc(e.type), esc(e.decoded || e.text), `<code>${esc(e.raw)}</code>`])));
        flash($("#hist-stats"), `${r.stats.total} eventi in archivio`); } catch (e) { flash($("#hist-stats"), e.message, true); }
    });
    startStream();
  }

  if (page === "console") {
    const log = $("#con-log"), msg = $("#console-msg");
    const addLog = (text) => { log.textContent += text + "\n"; log.scrollTop = log.scrollHeight; };
    onEvent((ev) => { if (ev.type === "STATE" || ev.type === "ASSISTANT" || isBattery(ev)) return; addLog(`[${hhmmss(ev.ts)}] <- ${ev.text}   [${ev.raw}]`); });
    const run = async (label, fn) => { try { const r = await fn(); addLog(`[${hhmmss()}] -> ${label}` + (r ? `  (${r})` : "")); flash(msg, "ok"); refreshStatus(); } catch (e) { flash(msg, e.message, true); addLog(`[${hhmmss()}] !! ${label}: ${e.message}`); } };
    $("#props-all").addEventListener("click", () => run("leggi tutte le proprietà", async () => { const r = await api("/api/properties"); const tb = $("#props-table tbody"); tb.innerHTML = ""; r.properties.forEach((p) => tb.appendChild(eventRow(p, [`<b>${p.prop}</b> <span class="muted">0x${p.id.toString(16).padStart(2, "0")}</span>`, esc(p.text), `<code>${esc(p.raw)}</code>`]))); return r.properties.length + " proprietà"; }));
    $$("[data-prop-action]").forEach((b) => b.addEventListener("click", () => run(`${b.dataset.propAction} ${$("#prop-name").selectedOptions[0].text}`, async () => { const r = await api("/api/property", "POST", { prop: $("#prop-name").value, action: b.dataset.propAction }); return r.text || ""; })));
    $("#vol").addEventListener("input", () => { $("#vol-val").textContent = $("#vol").value; });
    $("#vol-set").addEventListener("click", () => run("volume " + $("#vol").value, async () => (await api("/api/volume", "POST", { level: $("#vol").value })).volume + " %"));
    $("#name-set").addEventListener("click", () => run("nome " + $("#name-new").value, async () => (await api("/api/name", "POST", { name: $("#name-new").value })).name));
    $("#prop-set-raw").addEventListener("click", () => run("scrivi proprietà", async () => (await api("/api/property", "POST", { prop: $("#prop-name").value, action: "set", value: $("#prop-raw").value })).text || ""));
    $("#msg-send").addEventListener("click", () => run(`msg ${$("#msg-type").value} ${$("#msg-payload").value}`, () => api("/api/message", "POST", { type: $("#msg-type").value, payload: $("#msg-payload").value })));
    $("#raw-send").addEventListener("click", () => run("raw " + $("#raw-hex").value, () => api("/api/raw", "POST", { hex: $("#raw-hex").value })));
    $("#out-send").addEventListener("click", () => run(`out ${$("#out-port").value} ${$("#out-sub").value} ${$("#out-payload").value}`, () => api("/api/port_output", "POST", { port: $("#out-port").value, subcommand: $("#out-sub").value, payload: $("#out-payload").value })));
    $("#action-send").addEventListener("click", () => run("azione " + $("#action-name").selectedOptions[0].text, () => api("/api/hub_action", "POST", { action: $("#action-name").value })));
    $("#alert-send").addEventListener("click", () => run(`allarme ${$("#alert-name").value} ${$("#alert-op").value}`, async () => (await api("/api/alert", "POST", { alert: $("#alert-name").value, operation: $("#alert-op").value })).reply || ""));
    $("#con-clear").addEventListener("click", () => { log.textContent = ""; });
    startStream();
  }

  if (page === "accessories") {
    const msg = $("#acc-msg");
    $$("button.tab").forEach((b) => b.addEventListener("click", () => { $$("button.tab").forEach((x) => x.classList.toggle("active", x === b)); $$(".tabpane").forEach((p) => p.classList.toggle("hidden", p.dataset.pane !== b.dataset.tab)); }));
    const filter = () => { const q = $("#acc-q").value.toLowerCase(), onlyOwned = $("#acc-only-owned").checked; $$(".acc-table tbody tr").forEach((tr) => { const text = tr.textContent.toLowerCase() + " " + $$("input[type=text]", tr).map((i) => i.value.toLowerCase()).join(" "); const owned = $(".acc-owned", tr).checked; tr.classList.toggle("hidden", (q && !text.includes(q)) || (onlyOwned && !owned)); }); };
    $("#acc-q").addEventListener("input", filter); $("#acc-only-owned").addEventListener("change", filter);
    document.addEventListener("click", async (ev) => {
      const b = ev.target.closest("button"); if (!b) return; const tr = b.closest("tr");
      if (b.classList.contains("acc-save") && tr) { try { await api("/api/accessories/" + tr.dataset.id, "POST", { owned: $(".acc-owned", tr).checked ? 1 : 0, quantity: $(".acc-qty", tr).value, set_number: $(".acc-set", tr).value, notes: $(".acc-notes", tr).value }); tr.classList.toggle("owned", $(".acc-owned", tr).checked); flash(msg, "salvato"); } catch (e) { flash(msg, e.message, true); } }
      if (b.classList.contains("acc-del") && tr) { if (confirm("Eliminare questo accessorio?")) { await api("/api/accessories/" + tr.dataset.id, "DELETE"); tr.remove(); } }
      if (b.id === "new-add") { try { await api("/api/accessories", "POST", { kind: $("#new-kind").value, name: $("#new-name").value, code: $("#new-code").value, set_number: $("#new-set").value, notes: $("#new-notes").value, owned: $("#new-owned").checked ? 1 : 0, quantity: $("#new-owned").checked ? 1 : 0 }); location.reload(); } catch (e) { flash(msg, e.message, true); } }
    });
    document.addEventListener("change", (ev) => { const cb = ev.target; if (cb.classList && cb.classList.contains("acc-owned")) { const tr = cb.closest("tr"); api("/api/accessories/" + tr.dataset.id, "POST", { owned: cb.checked ? 1 : 0 }).then(() => tr.classList.toggle("owned", cb.checked)).catch((e) => flash(msg, e.message, true)); } });
  }

  if (page === "assistant") {
    const chat = $("#chat"), msg = $("#as-msg"), EMO = {};
    let current = null;
    const scroll = () => { chat.scrollTop = chat.scrollHeight; };
    function bubble(cls, html) { const d = document.createElement("div"); d.className = "bubble " + cls; d.innerHTML = html; const e = $("#chat-empty"); if (e) e.remove(); chat.appendChild(d); while (chat.children.length > 200) chat.firstChild.remove(); scroll(); return d; }
    function emoColor(key) { return (EMO[key] || {}).color || "#2e9e44"; }
    function setFace(e) { const info = typeof e === "string" ? EMO[e] : e; if (!info) return; $("#face").style.setProperty("--emo", info.color); $("#face-emoji").textContent = info.emoji; $("#face-label").textContent = info.label; }
    function addUser(text, source, sensor) { current = null; bubble("user" + (sensor ? " sensor" : ""), `<span class="src">${sensor ? "sensore · " : ""}${esc(source || "utente")}</span>${esc(text)}`); }
    function ensureCurrent(emotion) { if (!current) { current = bubble("assistant typing", ""); current.style.setProperty("--emo", emoColor(emotion)); } return current; }
    function renderContext(c) { c = c || {}; $("#ctx-pants").textContent = c.pants_name || "-"; $("#ctx-color").textContent = c.color_name || "-"; $("#ctx-tile").textContent = c.tile_name || "-"; $("#ctx-gesture").textContent = c.gesture || "-"; $("#ctx-battery").textContent = c.battery !== null && c.battery !== undefined ? c.battery + " %" : "-"; }
    function renderIntent(ev) { const ul = $("#intent-list"); if (ul.firstElementChild && ul.firstElementChild.classList.contains("muted")) ul.innerHTML = ""; const li = document.createElement("li"); li.textContent = `${hhmmss(ev.ts)} · ${ev.intent.source} → ${ev.outcome}`; ul.prepend(li); while (ul.children.length > 10) ul.lastChild.remove(); }
    function behLog(ev) { const log = $("#beh-log"); log.textContent += `[${hhmmss(ev.ts)}] ${ev.text}\n`; log.scrollTop = log.scrollHeight; }
    onEvent((ev) => {
      if (ev.type !== "ASSISTANT") return;
      switch (ev.kind) {
        case "user": addUser(ev.text, ev.source, ev.sensor); break;
        case "start": $("#as-backend").textContent = ev.backend; $("#face").classList.add("busy"); $("#face-sub").textContent = "sto pensando…"; break;
        case "emotion": setFace(ev.emotion); if (current) current.style.setProperty("--emo", emoColor(ev.emotion)); break;
        case "delta": { const b = ensureCurrent(ev.emotion); b.style.setProperty("--emo", emoColor(ev.emotion)); b.appendChild(document.createTextNode(ev.text)); scroll(); break; }
        case "sound": { const b = ensureCurrent(ev.emotion); b.appendChild(document.createTextNode(` ${ev.text} `)); scroll(); break; }
        case "done": if (current) { current.classList.remove("typing"); current.textContent = ev.display || ev.text; } else if (ev.text) bubble("assistant", esc(ev.display || ev.text)); current = null; $("#face").classList.remove("busy"); $("#face-sub").textContent = `${ev.elapsed} s${ev.first_sentence ? ` (prima frase ${ev.first_sentence} s)` : ""} · ${(ev.emotions || []).join(", ") || "nessun tag"}`; break;
        case "memory": flash(msg, `ricordo salvato: ${ev.text}`); loadMemory(); break;
        case "memory_forgotten": flash(msg, ev.text); loadMemory(); break;
        case "time_up": flash(msg, ev.text, true); $("#as-listen").checked = false; break;
        case "blocked": bubble("error", esc(ev.text)); break;
        case "error": bubble("error", esc(ev.text)); current = null; $("#face").classList.remove("busy"); $("#face-sub").textContent = "errore"; break;
        case "stopped": if (current) { current.classList.remove("typing"); current = null; } $("#face").classList.remove("busy"); break;
        case "reset": chat.innerHTML = ""; current = null; break;
        case "listening": $("#as-listen").checked = !!ev.on; flash(msg, ev.text); break;
        case "intent": renderIntent(ev); break;
        case "context": renderContext(ev.context); break;
        case "behaviour": behLog(ev); break;
      }
    });
    function renderSettings(s, speaker, llm) {
      $("#set-backend").value = s.backend || "auto"; $("#set-model").value = s.model || ""; $("#set-base").value = s.base_url || "";
      $("#set-smodel").value = s.sensor_model ?? "auto"; $("#set-sresolved").textContent = llm ? "in uso: " + (llm.resolved_sensor_model || "lo stesso modello") : "";
      $("#set-max").value = s.max_tokens || 350; $("#set-temp").value = s.temperature || 0.8; $("#set-persona").value = s.persona || "";
      $("#set-key-state").textContent = s.api_key_set ? "chiave salvata nel database" : (s.env_anthropic_key ? "chiave presa da ANTHROPIC_API_KEY" : "nessuna chiave: risponde il backend demo");
      $("#set-resolved").textContent = "in uso: " + (llm ? llm.resolved_backend : s.resolved_backend);
      $("#set-tts").checked = !!s.tts; $("#set-sounds").checked = !!s.sounds; $("#set-voice").value = s.voice || "";
      $("#set-rate").value = s.rate || 0; $("#set-rate-v").textContent = s.rate || 0; $("#set-pitch").value = s.pitch || 0; $("#set-pitch-v").textContent = s.pitch || 0;
      $("#set-engine").textContent = speaker && speaker.engine ? `motore: ${speaker.engine}, suoni: ${speaker.player || "nessun lettore"}` : "nessun motore vocale trovato (installa speech-dispatcher o espeak-ng)";
      $("#set-beh").checked = !!s.behaviours; $("#set-vol").checked = !!s.volume_effects; $("#set-busy").checked = !!s.busy_indication; $("#set-ports").checked = !!s.port_writes;
      $("#set-payloads").value = s.port_payloads && s.port_payloads !== "{}" ? s.port_payloads : "";
      $("#set-soundstyle").value = s.sound_style || "giochi"; $("#set-veffect").value = s.voice_effect || ""; $("#set-exclam").checked = !!s.exclamations;
      $("#set-pack").textContent = speaker && speaker.soundpack ? (speaker.soundpack.files ? `${speaker.soundpack.files} suoni scaricati` : "nessun suono scaricato: ./run.sh fetch-sounds") : "";
      $("#set-ovref").value = s.openvoice_reference || ""; $("#set-ovtau").value = s.openvoice_tau || 0.3; $("#set-ovwm").checked = !!s.openvoice_watermark;
      const ovs = speaker && speaker.openvoice;
      $("#set-ovstate").textContent = !ovs ? "" : ovs.problem ? `non disponibile: ${ovs.problem}` : ovs.state === "spento" ? "" : `${ovs.mode === "piper" ? "processo piper" : "voce IA"}: ${ovs.state}` + (ovs.load_s ? `, caricata in ${ovs.load_s} s` : "") + (ovs.avg_s ? `, ${ovs.avg_s} s per frase` : "") + (ovs.last_error ? ` (${ovs.last_error})` : "");
      $("#set-piper").value = s.piper_model || ""; $("#set-minutes").value = s.session_minutes || 0; $("#set-blocked").value = s.blocked_words || ""; $("#set-memory").checked = !!s.memory;
    }
    function renderMemory(items) {
      const ul = $("#memory-list"); ul.innerHTML = "";
      if (!items.length) { ul.innerHTML = '<li class="muted">nessuno</li>'; return; }
      items.forEach((m) => { const li = document.createElement("li"); li.innerHTML = `${esc(m.text)} <span class="muted small">${fmtDate(m.ts)}</span> <button class="small ghost" data-forget="${m.id}" title="dimentica">×</button>`; ul.appendChild(li); });
    }
    async function loadMemory() { try { renderMemory((await api("/api/assistant/memory")).memory || []); } catch (e) { flash(msg, e.message, true); } }
    const isoToTs = (v) => v ? String(new Date(v).getTime() / 1000) : "";
    function diaryQuery(extra) { const q = new URLSearchParams({ limit: $("#diary-limit").value }); const f = isoToTs($("#diary-from").value), t = isoToTs($("#diary-to").value); if (f) q.set("from", f); if (t) q.set("to", t); Object.entries(extra || {}).forEach(([k, v]) => q.set(k, v)); return q; }
    async function loadDiary() {
      try { const r = await api("/api/assistant/history?" + diaryQuery()); const tb = $("#diary-table tbody"); tb.innerHTML = "";
        r.messages.forEach((m) => tb.appendChild(eventRow(m, [fmtDate(m.ts), m.role === "assistant" ? "Mario" : (m.source && m.source !== "utente" ? "sensore" : "bambino"), esc(m.source || ""), esc(m.emotion || ""), esc(m.content)])));
        flash($("#diary-msg"), `${r.messages.length} messaggi mostrati, ${r.stats.total} in archivio`); } catch (e) { flash($("#diary-msg"), e.message, true); }
    }
    function renderRules(rules) {
      const tb = $("#rules-table tbody"); tb.innerHTML = "";
      rules.forEach((r) => { const tr = document.createElement("tr"); tr.dataset.name = r.name; tr.innerHTML = `<td><input type="checkbox" class="r-on" ${r.enabled ? "checked" : ""}></td><td><b>${esc(r.name)}</b></td><td class="muted">${esc(r.description)}</td><td>${esc(r.kind)}</td><td>${r.kind === "prompt" ? `<input type="text" class="r-text" value="${esc(r.text).replace(/"/g, "&quot;")}" style="width:100%">` : `<code>${esc(r.text)}</code>`}</td><td><input type="number" class="r-cd" value="${r.cooldown}" min="0" step="0.5" style="width:5em"></td>`; tb.appendChild(tr); });
    }
    async function load() {
      const st = (await api("/api/assistant/status")).status;
      (st.emotions || []).forEach((e) => { EMO[e.key] = e; });
      setFace(st.emotion); $("#as-listen").checked = !!st.listening; renderContext(st.context);
      $("#as-backend").textContent = st.backend || (st.llm.resolved_backend + (st.llm.model ? " · " + st.llm.model : ""));
      if (st.busy) $("#face").classList.add("busy");
      renderSettings(st.settings, st.speaker, st.llm); renderRules(st.rules || []); renderMemory(st.memory || []);
      (st.behaviours.last_actions || []).forEach((t) => { $("#beh-log").textContent += t + "\n"; });
      const h = (await api("/api/assistant/history?limit=40")).messages;
      h.forEach((m) => { if (m.role === "user") addUser(m.content, m.source, m.source && m.source !== "utente"); else { const b = bubble("assistant", esc(m.content)); b.style.setProperty("--emo", emoColor(m.emotion)); } });
      current = null;
    }
    async function send() { const text = $("#as-text").value.trim(); if (!text) return; try { await api("/api/assistant/ask", "POST", { text }); $("#as-text").value = ""; flash(msg, ""); } catch (e) { flash(msg, e.message, true); } }
    $("#as-send").addEventListener("click", send);
    $("#as-text").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); send(); } });
    $("#as-stop").addEventListener("click", () => api("/api/assistant/stop", "POST", {}).catch((e) => flash(msg, e.message, true)));
    $("#as-repeat").addEventListener("click", () => api("/api/assistant/repeat", "POST", {}).then((r) => flash(msg, r.repeated ? "ripeto" : "niente da ripetere")).catch((e) => flash(msg, e.message, true)));
    $("#as-reset").addEventListener("click", () => { if (confirm("Nuova conversazione? Il modello dimentica quanto detto finora (lo storico resta nel database).")) api("/api/assistant/reset", "POST", {}).catch((e) => flash(msg, e.message, true)); });
    $("#as-listen").addEventListener("change", () => api("/api/assistant/listen", "POST", { on: $("#as-listen").checked ? 1 : 0 }).catch((e) => flash(msg, e.message, true)));
    $("#say-send").addEventListener("click", () => { const text = $("#say-text").value.trim(); if (text) api("/api/assistant/say", "POST", { text }).catch((e) => flash(msg, e.message, true)); });
    $("#set-rate").addEventListener("input", () => { $("#set-rate-v").textContent = $("#set-rate").value; });
    $("#set-pitch").addEventListener("input", () => { $("#set-pitch-v").textContent = $("#set-pitch").value; });
    $("#memory-add").addEventListener("click", async () => { const t = $("#memory-text").value.trim(); if (!t) return; try { await api("/api/assistant/memory", "POST", { text: t }); $("#memory-text").value = ""; } catch (e) { flash(msg, e.message, true); } });
    $("#memory-clear").addEventListener("click", async () => { if (confirm("Dimenticare tutti i ricordi?")) { try { await api("/api/assistant/memory", "DELETE"); } catch (e) { flash(msg, e.message, true); } } });
    $("#diary-load").addEventListener("click", loadDiary);
    $("#diary-csv").addEventListener("click", () => { location.href = "/api/assistant/history.csv?" + diaryQuery({ limit: 5000 }); });
    document.addEventListener("click", async (ev) => {
      const b = ev.target.closest("button"); if (!b) return;
      if (b.dataset.forget) { try { await api("/api/assistant/memory/" + b.dataset.forget, "DELETE"); } catch (e) { flash(msg, e.message, true); } }
      if (b.dataset.emotion) { try { await api("/api/assistant/emotion", "POST", { emotion: b.dataset.emotion }); } catch (e) { flash(msg, e.message, true); } }
      if (b.dataset.sim) { try { const r = await api("/api/assistant/simulate", "POST", { what: b.dataset.sim }); flash(msg, r.intents.length ? `simulato: ${r.intents.map((i) => i.source).join(", ")}` : "simulato: nessun intento (pausa attiva o regola spenta)"); } catch (e) { flash(msg, e.message, true); } }
    });
    $("#set-save").addEventListener("click", async () => {
      let payloads = {};
      try { payloads = $("#set-payloads").value.trim() ? JSON.parse($("#set-payloads").value) : {}; } catch (e) { flash($("#set-msg"), "JSON dei payload non valido", true); return; }
      const data = { backend: $("#set-backend").value, model: $("#set-model").value, sensor_model: $("#set-smodel").value, api_key: $("#set-key").value, clear_api_key: $("#set-key-clear").checked ? 1 : 0, base_url: $("#set-base").value, max_tokens: $("#set-max").value, temperature: $("#set-temp").value, persona: $("#set-persona").value, tts: $("#set-tts").checked ? 1 : 0, sounds: $("#set-sounds").checked ? 1 : 0, voice: $("#set-voice").value, rate: $("#set-rate").value, pitch: $("#set-pitch").value, behaviours: $("#set-beh").checked ? 1 : 0, volume_effects: $("#set-vol").checked ? 1 : 0, busy_indication: $("#set-busy").checked ? 1 : 0, port_writes: $("#set-ports").checked ? 1 : 0, port_payloads: payloads, piper_model: $("#set-piper").value, openvoice_reference: $("#set-ovref").value, openvoice_tau: $("#set-ovtau").value, openvoice_watermark: $("#set-ovwm").checked ? 1 : 0, sound_style: $("#set-soundstyle").value, voice_effect: $("#set-veffect").value, exclamations: $("#set-exclam").checked ? 1 : 0, session_minutes: $("#set-minutes").value, blocked_words: $("#set-blocked").value, memory: $("#set-memory").checked ? 1 : 0 };
      try { const r = await api("/api/assistant/settings", "POST", data); $("#set-key").value = ""; $("#set-key-clear").checked = false; renderSettings(r.settings, null, null); const st = (await api("/api/assistant/status")).status; renderSettings(st.settings, st.speaker, st.llm); flash($("#set-msg"), "impostazioni salvate"); } catch (e) { flash($("#set-msg"), e.message, true); }
    });
    $("#set-ovfile").addEventListener("change", async (ev) => {
      const file = ev.target.files[0]; if (!file) return;
      const form = new FormData(); form.append("file", file);
      try {
        const r = await fetch("/api/assistant/voice-reference", { method: "POST", body: form, credentials: "same-origin" });
        const j = await r.json(); if (!j.ok) throw new Error(j.error || r.statusText);
        $("#set-ovref").value = j.path; flash($("#set-msg"), `registrazione caricata (${j.seconds} s): premi "Salva impostazioni"`);
      } catch (e) { flash($("#set-msg"), e.message, true); }
      ev.target.value = "";
    });
    $("#rules-save").addEventListener("click", async () => {
      const rules = {};
      $$("#rules-table tbody tr").forEach((tr) => { const t = $(".r-text", tr); rules[tr.dataset.name] = { enabled: $(".r-on", tr).checked, cooldown: Number($(".r-cd", tr).value), text: t ? t.value : undefined }; });
      try { await api("/api/assistant/settings", "POST", { rules }); flash($("#rules-msg"), "regole salvate"); } catch (e) { flash($("#rules-msg"), e.message, true); }
    });
    load().catch((e) => flash(msg, e.message, true));
    startStream();
  }

  if (page === "adventures" && $("#editor")) {
    const msg = $("#adv-msg"), edMsg = $("#ed-msg"), editor = $("#editor");
    let current = { name: "", steps: [] }, LIB = null, selectedRow = null;
    const TRIGGER_PARAM = { barcode: ["code", "family"], color: ["code"], pants: ["code"], gesture: ["name"], game_event: ["code"], timer: ["seconds"], button: [], sequence: [], count: [] };
    function paramOf(t) { if (!t) return ""; if (t.kind === "barcode") return t.code || t.family || ""; if (["color", "pants", "game_event"].includes(t.kind)) return t.code || ""; if (t.kind === "gesture") return t.name || ""; if (t.kind === "timer") return t.seconds || ""; return "(vedi JSON)"; }
    function applyParam(t, v) { v = String(v).trim(); if (t.kind === "barcode") { if (/^0x/i.test(v)) { t.code = v; delete t.family; } else { t.family = v || "any"; delete t.code; } } else if (["color", "pants", "game_event"].includes(t.kind)) t.code = v; else if (t.kind === "gesture") t.name = v; else if (t.kind === "timer") t.seconds = Number(v) || 0; return t; }
    function formToModel() {
      current.name = $("#ed-name").value; current.author = $("#ed-author").value; current.version = Number($("#ed-version").value) || 1; current.description = $("#ed-description").value;
      current.intro_text = $("#ed-intro").value; current.end_text = $("#ed-end").value; current.time_limit = Number($("#ed-time").value) || 0; current.kid_mode = $("#ed-kid").checked;
      current.steps = $$("#steps-table tbody tr").map((tr) => { const s = tr._step || {}; const t = s.trigger && typeof s.trigger === "object" ? s.trigger : { kind: "button" };
        const kind = $(".st-kind", tr).value; if (t.kind !== kind) { Object.keys(t).forEach((k) => delete t[k]); t.kind = kind; }
        if (TRIGGER_PARAM[kind] && TRIGGER_PARAM[kind].length) applyParam(t, $(".st-param", tr).value);
        return { ...s, id: $(".st-id", tr).value.trim() || undefined, title: $(".st-title", tr).value, text: $(".st-text", tr).value, trigger: t, points: Number($(".st-points", tr).value) || 0, timeout: Number($(".st-timeout", tr).value) || 0, success_text: $(".st-ok", tr).value, timeout_text: $(".st-ko", tr).value }; });
      return current;
    }
    function modelToForm() {
      $("#ed-name").value = current.name || ""; $("#ed-author").value = current.author || ""; $("#ed-version").value = current.version || 1; $("#ed-description").value = current.description || "";
      $("#ed-intro").value = current.intro_text || ""; $("#ed-end").value = current.end_text || "Avventura finita! Hai fatto {score} punti."; $("#ed-time").value = current.time_limit || 0; $("#ed-kid").checked = current.kid_mode !== false;
      const tb = $("#steps-table tbody"); tb.innerHTML = "";
      (current.steps || []).forEach((s, i) => tb.appendChild(stepRow(s, i)));
      $("#ed-json").value = JSON.stringify(current, null, 1);
      $("#ed-title").textContent = current.name ? `${current.name}${editor.dataset.adventureId ? " (id " + editor.dataset.adventureId + ")" : " (nuova)"}` : "(nuova avventura)";
    }
    function stepRow(s, i) {
      const tr = document.createElement("tr"); tr._step = s; const t = s.trigger || { kind: "button" };
      const kinds = Object.keys(TRIGGER_PARAM).map((k) => `<option value="${k}" ${t.kind === k ? "selected" : ""}>${k}</option>`).join("");
      tr.innerHTML = `<td>${i + 1}</td><td><input class="st-id" value="${esc(s.id || "")}" style="width:6em"></td><td><input class="st-title" value="${esc(s.title || "")}" style="width:8em"></td><td><input class="st-text" value="${esc(s.text || "")}" style="width:16em"></td><td><select class="st-kind">${kinds}</select></td><td><input class="st-param" value="${esc(paramOf(t))}" style="width:7em"></td><td><input class="st-points" type="number" value="${s.points ?? 10}" style="width:4em"></td><td><input class="st-timeout" type="number" value="${s.timeout || 0}" style="width:4em"></td><td><input class="st-ok" value="${esc(s.success_text || "")}" style="width:12em"></td><td><input class="st-ko" value="${esc(s.timeout_text || "")}" style="width:12em"></td><td><button class="small ghost st-up" title="su">↑</button><button class="small ghost st-down" title="giù">↓</button><button class="small ghost danger st-del">×</button></td>`;
      tr.addEventListener("click", () => { $$("#steps-table tbody tr").forEach((r) => r.classList.remove("owned")); tr.classList.add("owned"); selectedRow = tr; });
      return tr;
    }
    async function loadAdventure(id) {
      try { const r = await api("/api/adventures/" + id); current = r.adventure.data; editor.dataset.adventureId = id; modelToForm(); flash(msg, `caricata: ${current.name}`); editor.scrollIntoView({ behavior: "smooth" }); } catch (e) { flash(msg, e.message, true); }
    }
    async function loadRuns() {
      try { const r = await api("/api/adventure/runs?limit=15"); const tb = $("#runs-table tbody"); tb.innerHTML = "";
        r.runs.forEach((x) => tb.appendChild(eventRow(x, [fmtDate(x.started), esc(x.name), esc(x.outcome), x.score, x.ended && x.started ? Math.round(x.ended - x.started) + " s" : ""]))); } catch (e) { flash(msg, e.message, true); }
    }
    function renderLibrary() {
      if (!LIB) return; const box = $("#library"); box.innerHTML = ""; const onlyOwned = $("#lib-owned").checked;
      const add = (el, label, trig) => { const d = document.createElement("span"); d.className = "element" + (el.owned ? " owned" : ""); d.title = el.effect || el.name || ""; d.innerHTML = `<span class="ico">${el.icon || ""}</span>${esc(label)}`; d.addEventListener("click", () => useTrigger(trig)); box.appendChild(d); };
      LIB.tiles.filter((t) => !onlyOwned || t.owned).forEach((t) => add(t, t.name, { kind: "barcode", code: t.hex }));
      LIB.colors.forEach((c) => add(c, c.name, { kind: "color", code: c.hex }));
      LIB.pants.forEach((p) => add(p, p.name, { kind: "pants", code: p.hex }));
      LIB.gestures.forEach((g) => add(g, g.label, { kind: "gesture", name: g.name }));
      Object.entries(LIB.families).forEach(([k, v]) => { if (k !== "unknown") add({ icon: "🔲" }, "basetta " + v, { kind: "barcode", family: k }); });
    }
    function useTrigger(trig) { if (!selectedRow) { flash(edMsg, "seleziona prima un passo nella tabella", true); return; } selectedRow._step.trigger = trig; $(".st-kind", selectedRow).value = trig.kind; $(".st-param", selectedRow).value = paramOf(trig); flash(edMsg, `trigger impostato: ${trig.kind} ${paramOf(trig)}`); }
    async function save(thenPlay) {
      formToModel();
      try { const id = editor.dataset.adventureId; const r = id ? await api("/api/adventures/" + id, "POST", { data: current }) : await api("/api/adventures", "POST", { data: current });
        const newId = id || r.id; editor.dataset.adventureId = newId; flash(edMsg, "salvata"); if (thenPlay) location.href = "/adventures/play?id=" + newId; else location.reload(); } catch (e) { flash(edMsg, e.message, true); }
    }
    document.addEventListener("click", async (ev) => {
      const b = ev.target.closest("button"); if (!b) return;
      if (b.classList.contains("adv-edit")) loadAdventure(b.dataset.id);
      if (b.classList.contains("adv-del")) { if (confirm("Eliminare questa avventura?")) { await api("/api/adventures/" + b.dataset.id, "DELETE"); location.reload(); } }
      if (b.id === "adv-new") { current = { name: "Nuova avventura", author: "", version: 1, description: "", intro_text: "", end_text: "Avventura finita! Hai fatto {score} punti.", time_limit: 0, kid_mode: true, steps: [{ id: "passo1", title: "Primo passo", text: "Fai saltare Mario!", trigger: { kind: "gesture", name: "salto" }, points: 10, timeout: 60, success_text: "Bravo!" }] }; editor.dataset.adventureId = ""; modelToForm(); editor.scrollIntoView({ behavior: "smooth" }); }
      if (b.id === "adv-import") { const ta = $("#adv-import-text"); if (ta.classList.contains("hidden")) { ta.classList.remove("hidden"); b.textContent = "Importa questo JSON"; return; } try { const r = await api("/api/adventures/import", "POST", { json: ta.value }); flash(msg, `importata: ${r.adventure.name}`); location.reload(); } catch (e) { flash(msg, e.message, true); } }
      if (b.id === "step-add") { formToModel(); current.steps.push({ id: "passo" + (current.steps.length + 1), title: "", text: "", trigger: { kind: "button" }, points: 10, timeout: 0 }); modelToForm(); }
      if (b.classList.contains("st-del") || b.classList.contains("st-up") || b.classList.contains("st-down")) { formToModel(); const tr = b.closest("tr"); const i = Array.from(tr.parentNode.children).indexOf(tr); if (b.classList.contains("st-del")) current.steps.splice(i, 1); else { const j = b.classList.contains("st-up") ? i - 1 : i + 1; if (j >= 0 && j < current.steps.length) { [current.steps[i], current.steps[j]] = [current.steps[j], current.steps[i]]; } } modelToForm(); }
      if (b.id === "ed-apply-json") { try { current = JSON.parse($("#ed-json").value); modelToForm(); flash(edMsg, "JSON applicato"); } catch (e) { flash(edMsg, "JSON non valido: " + e.message, true); } }
      if (b.id === "ed-validate") { formToModel(); try { const r = await api("/api/adventures/validate", "POST", { data: current }); flash(edMsg, r.problems.length ? "problemi: " + r.problems.join("; ") : "tutto a posto", r.problems.length > 0); $("#ed-json").value = JSON.stringify(r.normalized, null, 1); } catch (e) { flash(edMsg, e.message, true); } }
      if (b.id === "ed-save") save(false);
      if (b.id === "ed-play") save(true);
    });
    $("#lib-owned").addEventListener("change", renderLibrary);
    api("/api/adventures/library").then((r) => { LIB = r.library; renderLibrary(); }).catch((e) => flash(msg, e.message, true));
    loadRuns();
    const params = new URLSearchParams(location.search); if (params.get("id")) loadAdventure(params.get("id"));
  }

  if (page === "adventures" && $("#stage")) {
    const msg = $("#play-msg"), stage = $("#stage"), log = $("#pl-log");
    const addLog = (text) => { log.textContent += `[${hhmmss()}] ${text}\n`; log.scrollTop = log.scrollHeight; };
    function render(st) {
      const r = st.runner;
      stage.classList.toggle("finished", !!r && r.state === "finished");
      if (!r) { $("#pl-adventure").textContent = "Scegli un'avventura e premi Inizia"; $("#pl-text").textContent = "—"; return; }
      $("#pl-adventure").textContent = r.adventure + (r.state === "finished" ? ` · ${r.outcome}` : ""); $("#pl-score").textContent = r.score; $("#pl-elapsed").textContent = Math.round(r.elapsed);
      if (r.step) { $("#pl-step-title").textContent = `Passo ${r.step_index + 1} di ${r.steps_total} · ${r.step.title}`; $("#pl-text").textContent = r.step.text || r.step.title; $("#pl-trigger").textContent = "aspetto: " + r.step.trigger; $("#pl-progress").textContent = r.step.progress || "";
        const fill = $("#pl-timebar"); if (r.step.timeout) { const pct = Math.max(0, Math.min(100, 100 * r.step.remaining / r.step.timeout)); fill.style.width = pct + "%"; fill.style.background = pct < 25 ? "#d7262c" : pct < 50 ? "#f5c400" : "#2e9e44"; } else fill.style.width = "0"; }
      else { $("#pl-step-title").textContent = ""; $("#pl-text").textContent = r.state === "finished" ? `Finita: ${r.outcome} · ${r.score} punti` : "…"; $("#pl-trigger").textContent = ""; $("#pl-progress").textContent = ""; $("#pl-timebar").style.width = "0"; }
      const c = r.context || {}; $("#pl-context").textContent = `👖 ${c.pants || "—"} · 🎨 ${c.color || "—"} · 🔲 ${c.tile || "—"}${c.lifted ? " · ✋ in mano" : ""}`;
      $("#pl-path").innerHTML = (r.path || []).map((p) => `<span>${esc(p.name)}</span>`).join("") || '<span class="muted">nessuna basetta ancora</span>';
      $("#pl-results").innerHTML = (r.results || []).map((x) => `<li class="${x.outcome === "ok" ? "ok" : "ko"}">${esc(x.title)} · ${x.outcome} · ${x.points} punti</li>`).join("");
    }
    async function refresh() { try { render((await api("/api/adventure/status")).status); } catch (e) { flash(msg, e.message, true); } }
    onEvent((ev) => { if (ev.type !== "ADVENTURE") return; addLog(`${ev.kind}: ${ev.text}`); if (["start", "step", "success", "timeout", "finish", "progress", "score"].includes(ev.kind)) refresh(); });
    $("#play-start").addEventListener("click", async () => { try { render((await api("/api/adventure/start", "POST", { id: $("#play-select").value })).status); flash(msg, ""); } catch (e) { flash(msg, e.message, true); } });
    $("#play-stop").addEventListener("click", async () => { try { render((await api("/api/adventure/stop", "POST", {})).status); } catch (e) { flash(msg, e.message, true); } });
    $("#play-big").addEventListener("change", () => stage.classList.toggle("big", $("#play-big").checked));
    document.addEventListener("click", async (ev) => { const b = ev.target.closest("button[data-advsim]"); if (!b) return; try { const r = await api("/api/adventure/simulate", "POST", { what: b.dataset.advsim }); flash(msg, r.progressed ? "evento riconosciuto!" : "evento ricevuto, nessun progresso"); render(r.status); } catch (e) { flash(msg, e.message, true); } });
    refresh(); setInterval(refresh, 1000); startStream();
  }

  if (page === "sounds") {
    const msg = $("#snd-msg"), audio = $("#snd-audio");
    const at = (v) => esc(v).replace(/"/g, "&quot;");  // valori dentro attributi HTML
    let ROWS = [], VOLUME = 100;
    const editStyle = () => $("#snd-edit").value;
    const fileUrl = (rel, extra) => "/api/sounds/file/" + rel.split("#")[0].split("/").map(encodeURIComponent).join("/") + (extra ? "?" + new URLSearchParams(extra) : "");
    function listen(rel, browser) {
      const track = rel.includes("#") ? rel.split("#")[1] : "";
      const extra = browser ? null : Object.assign({ seconds: 30 }, track ? { track } : {});
      audio.src = fileUrl(rel, extra); audio.play().catch((e) => flash(msg, "anteprima non riuscita: " + e.message, true));
    }
    async function playOnPc(data) { try { await api("/api/sounds/play", "POST", data); flash(msg, "suonato sul PC"); } catch (e) { flash(msg, e.message, true); } }
    function chip(f, removable) {
      return `<span class="chip" title="${at(f.rel)}"><button class="small ghost" data-listen="${at(f.rel)}" data-browser="${f.browser ? 1 : ""}">▶</button>${esc(f.description)}` +
        (f.game && f.game !== "custom" ? ` <span class="muted small">${esc(f.game)}</span>` : "") +
        (removable ? ` <button class="small ghost danger" data-unassign="${at(f.rel)}" title="togli">×</button>` : "") + "</span>";
    }
    function names() { return ROWS.map((r) => r.name); }
    function fillNameSelects() {
      const current = $("#snd-target").value, up = $("#snd-up-name").value;
      $("#snd-target").innerHTML = names().map((n) => `<option value="${at(n)}">${esc(n)}</option>`).join("");
      $("#snd-up-name").innerHTML = '<option value="">nessuno</option>' + names().map((n) => `<option value="${at(n)}">${esc(n)}</option>`).join("");
      if (current && names().includes(current)) $("#snd-target").value = current;
      if (up && names().includes(up)) $("#snd-up-name").value = up;
    }
    function renderTable() {
      const st = editStyle(), tbody = $("#snd-table tbody"); tbody.innerHTML = "";
      ROWS.forEach((r) => {
        const entry = r[st], own = entry.override;
        const files = own ? own.map((f) => chip(f, true)).join(" ") : (entry.default.length ? '<span class="muted">predefinito:</span> ' + entry.default.map((f) => chip(f, false)).join(" ") : '<span class="muted">sintetizzato</span>');
        const tr = document.createElement("tr"); tr.dataset.name = r.name;
        tr.innerHTML = `<td><code>${esc(r.name)}</code></td><td class="small">${esc(r.uses)}</td><td>${files}</td>` +
          `<td class="nowrap"><button class="small" data-pc="${at(r.name)}">PC</button> <button class="small" data-pick="${at(r.name)}">Scegli</button>` +
          (own ? ` <button class="small ghost" data-reset="${at(r.name)}">Ripristina</button>` : "") + "</td>";
        tbody.appendChild(tr);
      });
    }
    async function loadAssignments() {
      try {
        const r = await api("/api/sounds/assignments"); ROWS = r.rows; VOLUME = r.volume;
        $("#snd-style").value = r.style; $("#snd-volume").value = VOLUME; $("#snd-volume-v").textContent = VOLUME;
        $("#snd-stats").textContent = `${r.status.files} file in archivio` + (r.status.missing.length ? `, ${r.status.missing.length} nomi senza file scaricati` : "");
        renderTable(); fillNameSelects();
      } catch (e) { flash(msg, e.message, true); }
    }
    async function assign(name, files, append) {
      try { const r = await api("/api/sounds/assign", "POST", { style: editStyle(), name, files, append: append ? 1 : 0 }); ROWS = r.rows; renderTable(); fillNameSelects(); flash(msg, `${name}: ${r.files.length ? r.files.length + " file" : "predefinito"}`); }
      catch (e) { flash(msg, e.message, true); }
    }
    let gamesFilled = "";
    async function loadCatalog() {
      const q = new URLSearchParams({ section: $("#snd-sec").value, game: $("#snd-game").value, q: $("#snd-q").value, limit: 400 });
      try {
        const r = await api("/api/sounds/catalog?" + q);
        if (gamesFilled !== $("#snd-sec").value) {
          const sel = $("#snd-game"), keep = sel.value; gamesFilled = $("#snd-sec").value;
          const games = [...new Set(r.games.map((g) => g.game))];
          sel.innerHTML = '<option value="">tutti</option>' + games.map((g) => `<option value="${at(g)}">${esc(g)}</option>`).join("");
          if (games.includes(keep)) sel.value = keep;
        }
        const tbody = $("#snd-list tbody"); tbody.innerHTML = "";
        r.items.forEach((it) => {
          const multi = ["nsf", "nsfe"].includes((it.rel.split(".").pop() || "").toLowerCase());
          const tr = document.createElement("tr"); tr.dataset.rel = it.rel;
          tr.innerHTML = `<td class="nowrap"><button class="small ghost" data-listen="${at(it.rel)}" data-browser="${it.browser ? 1 : ""}" ${it.playable || it.browser ? "" : "disabled title=\"non riproducibile su questo PC\""}>▶</button>` +
            (multi ? ` <input type="number" class="snd-track" min="0" max="99" value="0" title="traccia" style="width:3.5em">` : "") + "</td>" +
            `<td>${esc(it.description)}</td><td class="small">${esc(it.game || "")}${it.category ? " · " + esc(it.category) : ""}</td>` +
            `<td class="small muted">${esc(it.section)}${it.extra ? " · " + esc(it.extra) : ""}${it.size ? " · " + Math.max(1, Math.round(it.size / 1024)) + " KB" : ""}</td>` +
            `<td class="nowrap"><button class="small" data-pcfile="${at(it.rel)}" ${it.playable ? "" : "disabled"}>PC</button> <button class="small primary" data-add="${at(it.rel)}" title="aggiungi al nome scelto in Assegna a">+</button>` +
            (it.section === "custom" ? ` <button class="small ghost danger" data-delete="${at(it.rel)}" title="cancella">×</button>` : "") + "</td>";
          tbody.appendChild(tr);
        });
        $("#snd-matches").textContent = `${r.matches} trovati su ${r.total}` + (r.matches > r.items.length ? `, mostrati ${r.items.length}` : "") +
          " · " + Object.entries(r.sections).map(([k, v]) => `${k} ${v}`).join(", ");
      } catch (e) { flash(msg, e.message, true); }
    }
    const withTrack = (btn, rel) => { const t = btn.closest("tr") && $(".snd-track", btn.closest("tr")); return t && Number(t.value) > 0 ? `${rel}#${Number(t.value)}` : rel; };
    document.addEventListener("click", async (ev) => {
      const b = ev.target.closest("button"); if (!b || !b.closest("main")) return;
      if (b.dataset.listen !== undefined) listen(withTrack(b, b.dataset.listen), !!b.dataset.browser && !withTrack(b, b.dataset.listen).includes("#"));
      else if (b.dataset.pc) playOnPc({ name: b.dataset.pc, style: editStyle() });
      else if (b.dataset.pcfile) { const rel = withTrack(b, b.dataset.pcfile); playOnPc({ file: rel, seconds: /\.(nsfe?|spc|it|xm|mod|s3m|mid|midi|mp3)$/i.test(rel.split("#")[0]) ? 30 : "" }); }
      else if (b.dataset.pick) { $("#snd-target").value = b.dataset.pick; $("#snd-archive").scrollIntoView({ behavior: "smooth" }); flash(msg, `scegli i file per "${b.dataset.pick}" con +`); }
      else if (b.dataset.reset) assign(b.dataset.reset, [], false);
      else if (b.dataset.unassign) { const name = b.closest("tr").dataset.name, row = ROWS.find((r) => r.name === name); assign(name, (row[editStyle()].override || []).map((f) => f.rel).filter((rel) => rel !== b.dataset.unassign), false); }
      else if (b.dataset.add) { const name = $("#snd-target").value; if (!name) { flash(msg, "scegli prima il nome in Assegna a", true); return; } assign(name, [withTrack(b, b.dataset.add)], true); }
      else if (b.dataset.delete) { if (!confirm("Cancellare questo suono?")) return; try { await api("/api/sounds/file/" + b.dataset.delete.split("/").map(encodeURIComponent).join("/"), "DELETE"); loadCatalog(); loadAssignments(); } catch (e) { flash(msg, e.message, true); } }
    });
    $("#snd-edit").addEventListener("change", renderTable);
    $("#snd-style").addEventListener("change", async () => { try { await api("/api/assistant/settings", "POST", { sound_style: $("#snd-style").value }); flash(msg, "stile salvato"); } catch (e) { flash(msg, e.message, true); } });
    $("#snd-volume").addEventListener("input", () => { $("#snd-volume-v").textContent = $("#snd-volume").value; });
    $("#snd-volume").addEventListener("change", async () => { try { const r = await api("/api/sounds/volume", "POST", { volume: Number($("#snd-volume").value) }); flash(msg, `volume degli effetti ${r.volume}%`); } catch (e) { flash(msg, e.message, true); } });
    $("#snd-add").addEventListener("click", () => {
      const name = $("#snd-newname").value.trim().toLowerCase();
      if (!/^[a-z0-9_]{1,32}$/.test(name)) { flash(msg, "nome non valido: lettere minuscole, cifre e _", true); return; }
      if (!names().includes(name)) { ROWS.push({ name, uses: "nuovo: aggiungi i file dall'archivio", giochi: { default: [], override: null }, voce: { default: [], override: null } }); renderTable(); fillNameSelects(); }
      $("#snd-target").value = name; $("#snd-newname").value = ""; $("#snd-archive").scrollIntoView({ behavior: "smooth" });
    });
    $("#snd-search").addEventListener("click", loadCatalog);
    $("#snd-q").addEventListener("keydown", (e) => { if (e.key === "Enter") loadCatalog(); });
    $("#snd-sec").addEventListener("change", () => { $("#snd-game").value = ""; loadCatalog(); });
    $("#snd-game").addEventListener("change", loadCatalog);
    $("#snd-upload").addEventListener("click", async () => {
      const file = $("#snd-file").files[0]; if (!file) { flash($("#snd-up-msg"), "scegli un file", true); return; }
      const form = new FormData(); form.append("file", file); form.append("description", $("#snd-desc").value);
      form.append("name", $("#snd-up-name").value); form.append("style", editStyle());
      try {
        const r = await fetch("/api/sounds/upload", { method: "POST", body: form, credentials: "same-origin" }); const j = await r.json();
        if (!j.ok) throw new Error(j.error || r.statusText);
        flash($("#snd-up-msg"), `caricato: ${j.item.description}`); $("#snd-file").value = ""; $("#snd-desc").value = "";
        $("#snd-sec").value = "custom"; gamesFilled = null; loadCatalog(); loadAssignments();
      } catch (e) { flash($("#snd-up-msg"), e.message, true); }
    });
    loadAssignments(); loadCatalog();
  }

  if (page === "report") {
    $("#report-refresh").addEventListener("click", async () => { try { $("#report-text").textContent = (await api("/api/report")).text; } catch (e) { alert(e.message); } });
  }
})();
