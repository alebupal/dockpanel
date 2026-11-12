/* Dockpanel · interfaz web de update.sh */
(function () {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const state = {
    services: [],
    info: {},
    checkRunning: false,
    selected: new Set(), // carpetas marcadas para acciones en lote
    terms: new Map(), // id → { term, fit, ws, pane, tab, info, closed }
    active: null,
  };

  // ── Utilidades ─────────────────────────────────────────────
  function esc(s) {
    return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  let toastTimer;
  function toast(msg, isError = false) {
    const el = $("#toast");
    el.textContent = msg;
    el.classList.toggle("error", isError);
    el.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.remove("show"), isError ? 6000 : 3000);
  }

  async function api(path, opts = {}) {
    const headers = { "X-Dockpanel": "1" };
    if (opts.body !== undefined) headers["Content-Type"] = "application/json";
    const res = await fetch(path, {
      method: opts.method || "GET",
      headers,
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
    });
    let data = null;
    try { data = await res.json(); } catch (e) { /* sin cuerpo */ }
    if (!res.ok) throw new Error((data && data.detail) || `Error ${res.status}`);
    return data;
  }

  function timeAgo(ts) {
    if (!ts) return "";
    const s = Math.round(Date.now() / 1000 - ts);
    if (s < 60) return "hace unos segundos";
    if (s < 3600) return `hace ${Math.round(s / 60)} min`;
    if (s < 86400) return `hace ${Math.round(s / 3600)} h`;
    return new Date(ts * 1000).toLocaleString();
  }

  // ── Tema ───────────────────────────────────────────────────
  const themes = ["auto", "light", "dark"];
  const themeIcons = { auto: "🌓", light: "☀️", dark: "🌙" };
  function applyTheme(t) {
    if (t === "auto") delete document.documentElement.dataset.theme;
    else document.documentElement.dataset.theme = t;
    const btn = $("#btnTheme");
    btn.textContent = themeIcons[t];
    btn.title = btn.ariaLabel = `Tema: ${{ auto: "automático", light: "claro", dark: "oscuro" }[t]}`;
    for (const entry of state.terms.values()) entry.term.options.theme = termTheme();
  }
  function currentTheme() {
    try { return localStorage.getItem("dockpanel_theme") || "auto"; } catch (e) { return "auto"; }
  }
  $("#btnTheme").addEventListener("click", () => {
    const next = themes[(themes.indexOf(currentTheme()) + 1) % themes.length];
    try { localStorage.setItem("dockpanel_theme", next); } catch (e) { /* sin almacenamiento */ }
    applyTheme(next);
  });

  function termTheme() {
    return { background: "#0b0e13", foreground: "#e6edf7", cursor: "#93c5fd", selectionBackground: "#334155" };
  }

  // ── Servicios ──────────────────────────────────────────────
  async function loadServices() {
    try {
      const data = await api("/api/services");
      state.services = data.services;
      const existing = new Set(data.services.map((s) => s.folder));
      for (const f of [...state.selected]) if (!existing.has(f)) state.selected.delete(f);
      state.checkRunning = data.check_running;
      renderServices();
      renderCheckInfo(data.check_time);
    } catch (e) {
      $("#svcBody").innerHTML = `<tr><td colspan="6" class="muted">⚠️ ${esc(e.message)}</td></tr>`;
    }
  }

  function statusChip(st) {
    if (st === "RUNNING") return '<span class="chip chip-run">● En marcha</span>';
    if (st === "STOPPED") return '<span class="chip chip-stop">● Parado</span>';
    return '<span class="chip chip-off">○ Sin crear</span>';
  }

  function renderServices() {
    const q = $("#filter").value.trim().toLowerCase();
    const list = state.services.filter((s) => !q || `${s.name} ${s.folder} ${s.image}`.toLowerCase().includes(q));
    const running = state.services.filter((s) => s.status === "RUNNING").length;
    $("#svcSummary").textContent = `· ${running}/${state.services.length} en marcha`;

    const updatable = state.services.filter((s) => s.update === "update");
    $("#updCount").textContent = updatable.length;
    $("#btnUpdateAvailable").disabled = updatable.length === 0;

    state.visible = list.map((s) => s.folder);
    updateSelectionUI();

    if (!list.length) {
      $("#svcBody").innerHTML = `<tr><td colspan="6" class="muted">${state.services.length ? "Ningún servicio coincide con el filtro." : "No hay servicios configurados."}</td></tr>`;
      return;
    }

    $("#svcBody").innerHTML = list.map((s) => {
      const badges = [];
      if (s.update === "update") badges.push('<span class="chip chip-upd" title="Hay una imagen nueva">⬆ actualización</span>');
      if (s.update === "error") badges.push('<span class="chip chip-err" title="No se pudo comprobar">? comprobación</span>');
      if (s.is_self) badges.push('<span class="chip chip-info" title="Este panel">este panel</span>');
      const port = s.port
        ? `<a href="${location.protocol}//${esc(location.hostname)}:${esc(s.port)}" target="_blank" rel="noopener">:${esc(s.port)}</a>`
        : '<span class="muted">-</span>';
      const toggle = s.status === "RUNNING"
        ? `<button type="button" class="btn btn-sm btn-danger keep" data-svc-action="stop" title="Detener">⏹</button>`
        : `<button type="button" class="btn btn-sm keep" data-svc-action="start" title="Iniciar">▶</button>`;
      const sel = state.selected.has(s.folder);
      return `<tr data-folder="${esc(s.folder)}"${sel ? ' class="selected"' : ""}>
        <td class="sel"><input type="checkbox" class="sel-chk" aria-label="Seleccionar ${esc(s.name)}"${sel ? " checked" : ""}></td>
        <td><span class="svc-name">${esc(s.name)}</span><span class="svc-badges">${badges.join("")}</span><br>
            <span class="svc-folder" title="${esc(s.image)}">${esc(s.folder)}${s.image ? " · " + esc(s.image) : ""}</span></td>
        <td><span class="muted">${esc(s.mode)}</span></td>
        <td>${port}</td>
        <td>${statusChip(s.status)}</td>
        <td class="actions">
          ${toggle}
          <button type="button" class="btn btn-sm" data-svc-action="restart" title="Reiniciar">🔄</button>
          <button type="button" class="btn btn-sm" data-svc-action="update" title="Actualizar (pull + despliegue)">⬇️</button>
          <button type="button" class="btn btn-sm keep" data-svc-action="logs" title="Ver logs en vivo">📜</button>
          <span class="more">
            <button type="button" class="btn btn-sm keep" data-more aria-haspopup="true" title="Más acciones">⋯</button>
            <span class="more-menu" role="menu">
              <button type="button" role="menuitem" data-svc-action="start">▶ Iniciar</button>
              <button type="button" role="menuitem" data-svc-action="stop">⏹ Detener</button>
              <button type="button" role="menuitem" data-svc-action="restart">🔄 Reiniciar</button>
              <button type="button" role="menuitem" data-svc-action="recreate">🔨 Recrear (sin descargar)</button>
              <button type="button" role="menuitem" data-svc-action="update">⬇️ Actualizar</button>
              <button type="button" role="menuitem" data-svc-action="update_force">⬇️ Actualizar forzando recreación</button>
              <button type="button" role="menuitem" data-svc-action="backup">💾 Crear backup</button>
              <button type="button" role="menuitem" data-svc-action="policy">🔁 Política de reinicio</button>
              <button type="button" role="menuitem" data-svc-action="show">📜 Comando de arranque</button>
              <button type="button" role="menuitem" data-svc-action="logs">📜 Logs en vivo</button>
            </span>
          </span>
        </td>
      </tr>`;
    }).join("");
  }

  function renderCheckInfo(ts) {
    const el = $("#checkInfo");
    if (state.checkRunning) el.textContent = "🔍 Comprobando actualizaciones…";
    else if (ts) {
      const n = state.services.filter((s) => s.update === "update").length;
      el.textContent = `Última comprobación ${timeAgo(ts)}: ${n ? n + " con actualización disponible" : "todo al día"}.`;
    } else el.textContent = "Pulsa «Comprobar» para ver qué servicios tienen imagen nueva.";
  }

  $("#filter").addEventListener("input", renderServices);
  $("#btnRefresh").addEventListener("click", loadServices);

  $("#btnCheck").addEventListener("click", async () => {
    const btn = $("#btnCheck");
    btn.disabled = true;
    state.checkRunning = true;
    renderCheckInfo();
    try {
      await api("/api/check", { method: "POST", body: {} });
    } catch (e) {
      toast(e.message, true);
    } finally {
      btn.disabled = false;
      state.checkRunning = false;
      await loadServices();
    }
  });

  $("#btnUpdateAvailable").addEventListener("click", () => {
    const list = state.services.filter((s) => s.update === "update");
    if (!list.length) return;
    const names = list.map((s) => s.name).join(", ");
    const self = list.some((s) => s.is_self) ? "\n\nIncluye este panel: se reiniciará y la tarea seguirá en segundo plano." : "";
    if (!confirm(`¿Actualizar ${list.length} servicio(s)?\n${names}${self}`)) return;
    runAction({ action: "update_list", services: list.map((s) => s.folder) });
  });

  // ── Selección y acciones en lote ───────────────────────────
  function selectedServices() {
    return state.services.filter((s) => state.selected.has(s.folder));
  }

  function updateSelectionUI() {
    const n = state.selected.size;
    const title = $("#batchTitle");
    title.textContent = n ? `☑️ Selección (${n})` : "⏯️ Todos";
    title.classList.toggle("has-sel", n > 0);
    const visible = state.visible || [];
    const visSel = visible.filter((f) => state.selected.has(f)).length;
    const chkAll = $("#chkAll");
    chkAll.checked = visible.length > 0 && visSel === visible.length;
    chkAll.indeterminate = visSel > 0 && visSel < visible.length;
    $("#btnSelectAll").textContent = n ? "✖ Quitar selección" : "☑️ Seleccionar todos";
    document.querySelectorAll("[data-batch]").forEach((b) => {
      b.title = n ? `Aplicar a los ${n} servicio(s) seleccionados` : "Aplicar a TODOS los servicios";
    });
  }

  function setSelected(folder, on) {
    if (on) state.selected.add(folder);
    else state.selected.delete(folder);
    const row = document.querySelector(`#svcBody tr[data-folder="${CSS.escape(folder)}"]`);
    if (row) {
      row.classList.toggle("selected", on);
      const chk = $(".sel-chk", row);
      if (chk) chk.checked = on;
    }
  }

  $("#svcBody").addEventListener("change", (ev) => {
    if (!ev.target.classList.contains("sel-chk")) return;
    setSelected(ev.target.closest("tr").dataset.folder, ev.target.checked);
    updateSelectionUI();
  });

  // Casilla de la cabecera: marca o desmarca los servicios visibles (respeta el filtro)
  $("#chkAll").addEventListener("change", (ev) => {
    for (const f of state.visible || []) setSelected(f, ev.target.checked);
    updateSelectionUI();
  });

  $("#btnSelectAll").addEventListener("click", () => {
    if (state.selected.size) for (const f of [...state.selected]) setSelected(f, false);
    else for (const f of state.visible || []) setSelected(f, true);
    updateSelectionUI();
  });

  document.querySelectorAll("[data-batch]").forEach((btn) => btn.addEventListener("click", () => batchAction(btn.dataset.batch)));

  function batchAction(op) {
    const sel = selectedServices();
    // Solo consulta: sin confirmación (sin selección = todos)
    if (op === "show") return runAction({ action: "show", services: sel.map((s) => s.folder) });
    const force = $("#chkForce").checked;
    const verbs = {
      start: "Iniciar", stop: "Detener", restart: "Reiniciar", recreate: "Recrear (en local, sin descargar)",
      update: "Actualizar" + (force ? " forzando recreación" : ""), backup: "Hacer backup de", policy: "Cambiar la política de",
    };

    if (!sel.length) {
      // Sin selección: TODOS, con las opciones globales de update.sh (-a, --start all…)
      const selfNote = op === "stop" ? " Esto incluye este panel si está gestionado por update.sh." : "";
      if (op === "policy") return askPolicy("todos los servicios", (policy) => runAction({ action: "policy", service: "all", policy }));
      if (op !== "start" && op !== "backup" && !confirm(`¿${verbs[op]} TODOS los servicios?${selfNote}`)) return;
      if (op === "update") return runAction({ action: "update_all", force });
      return runAction({ action: op, service: "all" });
    }

    const names = sel.map((s) => s.name).join(", ");
    const self = sel.find((s) => s.is_self);
    let selfNote = "";
    if (self && op === "stop") selfNote = `\n\nIncluye este panel: dejará de estar accesible hasta que lo arranques por SSH (./update.sh --start ${self.folder}).`;
    else if (self && op !== "backup") selfNote = "\n\nIncluye este panel: se reiniciará y la tarea seguirá en segundo plano.";
    const body = { action: "batch", op, services: sel.map((s) => s.folder), force: op === "update" && force };
    if (op === "policy") {
      return askPolicy(`${sel.length} servicio(s)`, (policy) => runAction({ ...body, policy }));
    }
    if (op !== "start" && !confirm(`¿${verbs[op]} ${sel.length} servicio(s)?\n${names}${selfNote}`)) return;
    runAction(body);
  }

  // Acciones por fila
  $("#svcBody").addEventListener("click", (ev) => {
    const moreBtn = ev.target.closest("[data-more]");
    if (moreBtn) {
      const wrap = moreBtn.parentElement;
      const open = !wrap.classList.contains("open");
      closeMenus();
      wrap.classList.toggle("open", open);
      ev.stopPropagation();
      return;
    }
    const btn = ev.target.closest("[data-svc-action]");
    if (!btn) {
      // Clic en la fila (fuera de botones, enlaces y casilla) = marcar/desmarcar
      const row = ev.target.closest("tr[data-folder]");
      if (row && !ev.target.closest("a, input, button, .more")) {
        setSelected(row.dataset.folder, !state.selected.has(row.dataset.folder));
        updateSelectionUI();
      }
      return;
    }
    closeMenus();
    const folder = btn.closest("tr").dataset.folder;
    const svc = state.services.find((s) => s.folder === folder);
    if (svc) serviceAction(svc, btn.dataset.svcAction);
  });
  function closeMenus() { document.querySelectorAll(".more.open").forEach((m) => m.classList.remove("open")); }
  document.addEventListener("click", closeMenus);

  function serviceAction(svc, act) {
    const selfNote = svc.is_self ? "\n\nEs este panel: se reiniciará y la tarea seguirá en segundo plano; recarga la página en unos segundos." : "";
    const ask = {
      stop: `¿Detener ${svc.name}?`,
      restart: svc.is_self ? `¿Reiniciar ${svc.name}?` : null,
      recreate: `¿Recrear ${svc.name} con su configuración actual?`,
      update: svc.is_self ? `¿Actualizar ${svc.name}?` : null,
      update_force: `¿Actualizar ${svc.name} forzando la recreación?`,
    }[act];
    if (svc.is_self && act === "stop") {
      if (!confirm(`¿Detener ${svc.name}? Es este panel: dejará de estar accesible hasta que lo arranques por SSH (./update.sh --start ${svc.folder}).`)) return;
    } else if (ask && !confirm(ask + selfNote)) return;

    if (act === "policy") return askPolicy(svc.name, (policy) => runAction({ action: "policy", service: svc.folder, policy }));
    if (act === "show") return runAction({ action: "show", services: [svc.folder] });
    if (act === "update_force") return runAction({ action: "update", service: svc.folder, force: true });
    runAction({ action: act, service: svc.folder });
  }

  // Botones globales con data-action
  document.addEventListener("click", (ev) => {
    const btn = ev.target.closest("[data-action]");
    if (!btn) return;
    if (btn.dataset.confirm && !confirm(btn.dataset.confirm)) return;
    const body = { action: btn.dataset.action };
    if (btn.hasAttribute("data-all")) body.service = "all";
    if (body.action === "update_all") body.force = $("#chkForce").checked;
    runAction(body);
  });

  // Política de reinicio
  function askPolicy(label, onPick) {
    const dlg = $("#dlgPolicy");
    $("#policyTarget").textContent = `· ${label}`;
    dlg.returnValue = "";
    dlg.onclose = () => {
      if (dlg.returnValue === "ok") onPick($("input[name=policy]:checked", dlg).value);
    };
    dlg.showModal();
  }

  // Nuevo servicio
  $("#btnNew").addEventListener("click", () => {
    const dlg = $("#dlgNew");
    dlg.returnValue = "";
    dlg.onclose = () => {
      if (dlg.returnValue !== "ok") return;
      const cmd = $("#newRunCmd").value.replace(/\\\s*\n/g, " ").replace(/\s*\n\s*/g, " ").trim();
      runAction({ action: "new", run_cmd: cmd });
    };
    dlg.showModal();
  });

  // Ruta base de usuario
  $("#btnUserHome").addEventListener("click", () => {
    const path = prompt("Ruta base de usuario (USER_HOME) para backups y volúmenes:\n(Se guarda en .user_home junto a update.sh)", "");
    if (!path) return;
    runAction({ action: "user_home", path: path.trim() });
  });

  // ── Terminales ─────────────────────────────────────────────
  function termSize() {
    const active = state.active && state.terms.get(state.active);
    if (active) return { cols: active.term.cols, rows: active.term.rows };
    const host = $("#termHost");
    return {
      cols: Math.max(60, Math.floor((host.clientWidth - 16) / 7.8)),
      rows: Math.max(15, Math.floor((host.clientHeight - 12) / 17)),
    };
  }

  async function runAction(body) {
    try {
      const info = await api("/api/sessions", { method: "POST", body: { ...body, ...termSize() } });
      openSession(info, true);
    } catch (e) {
      toast(e.message, true);
    }
  }

  function openSession(info, activate) {
    let entry = state.terms.get(info.id);
    if (!entry) {
      $("#termEmpty").hidden = true;
      const pane = document.createElement("div");
      pane.className = "term-pane";
      $("#termHost").appendChild(pane);

      const tab = document.createElement("div");
      tab.className = "term-tab";
      tab.setAttribute("role", "tab");
      tab.tabIndex = 0;
      tab.innerHTML = '<span class="dot"></span><span class="t-title"></span><button type="button" class="t-close" aria-label="Cerrar">×</button>';
      $(".t-title", tab).textContent = info.title;
      tab.title = info.title;
      $("#termTabs").appendChild(tab);

      const term = new Terminal({
        convertEol: false,
        cursorBlink: true,
        fontFamily: 'ui-monospace, "SFMono-Regular", Menlo, Consolas, "DejaVu Sans Mono", monospace',
        fontSize: 13,
        scrollback: 10000,
        theme: termTheme(),
      });
      const fit = new FitAddon.FitAddon();
      term.loadAddon(fit);
      term.open(pane);

      entry = { term, fit, pane, tab, info, ws: null, closed: false };
      state.terms.set(info.id, entry);

      term.onData((d) => {
        if (entry.ws && entry.ws.readyState === WebSocket.OPEN && entry.info.running) {
          entry.ws.send(JSON.stringify({ t: "in", d }));
        }
      });
      term.onResize(({ cols, rows }) => {
        if (entry.ws && entry.ws.readyState === WebSocket.OPEN) entry.ws.send(JSON.stringify({ t: "resize", c: cols, r: rows }));
      });

      tab.addEventListener("click", (ev) => {
        if (ev.target.closest(".t-close")) closeSession(info.id);
        else activateSession(info.id);
      });
      tab.addEventListener("keydown", (ev) => { if (ev.key === "Enter") activateSession(info.id); });

      connect(entry);
    }
    entry.info = info;
    updateTab(entry);
    if (activate || !state.active) activateSession(info.id);
  }

  function connect(entry) {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/api/sessions/${entry.info.id}/ws`);
    ws.binaryType = "arraybuffer";
    entry.ws = ws;
    let first = true;
    ws.onopen = () => {
      ws.send(JSON.stringify({ t: "resize", c: entry.term.cols, r: entry.term.rows }));
    };
    ws.onmessage = (ev) => {
      if (typeof ev.data === "string") {
        const msg = JSON.parse(ev.data);
        if (msg.t === "exit") {
          entry.info.running = false;
          entry.info.exit_code = msg.code;
          updateTab(entry);
          entry.term.write(`\r\n\x1b[90m── Tarea terminada (código ${msg.code}) ──\x1b[0m\r\n`);
          setTimeout(loadServices, 300);
        }
        return;
      }
      if (first) { entry.term.reset(); first = false; } // al reconectar llega el histórico completo
      entry.term.write(new Uint8Array(ev.data));
    };
    ws.onclose = (ev) => {
      if (entry.closed || !entry.info.running) return;
      if (ev.code === 4404) { entry.info.running = false; updateTab(entry); return; }
      setTimeout(() => { if (!entry.closed) connect(entry); }, ev.code === 4000 ? 200 : 2000);
    };
  }

  function updateTab(entry) {
    const dot = $(".dot", entry.tab);
    const i = entry.info;
    dot.className = "dot " + (i.running ? "run" : i.exit_code === 0 ? "ok" : "err");
    entry.term.options.disableStdin = !i.running;
    if (state.active === i.id) renderStatus(entry);
  }

  function renderStatus(entry) {
    const i = entry.info;
    const where = i.container ? ` · contenedor auxiliar ${i.container}` : "";
    $("#termStatus").textContent = i.running
      ? `En marcha${where}. Escribe en la terminal para responder a las preguntas de update.sh; Ctrl+C interrumpe.`
      : `Terminada con código ${i.exit_code}${i.exit_code === 0 ? " ✅" : " ❌"} · ${new Date((i.finished || i.created) * 1000).toLocaleTimeString()}`;
  }

  function activateSession(id) {
    state.active = id;
    for (const [sid, entry] of state.terms) {
      const on = sid === id;
      entry.pane.classList.toggle("active", on);
      entry.tab.classList.toggle("active", on);
      entry.tab.setAttribute("aria-selected", on ? "true" : "false");
    }
    const entry = state.terms.get(id);
    if (!entry) return;
    requestAnimationFrame(() => {
      try { entry.fit.fit(); } catch (e) { /* panel oculto */ }
      entry.term.focus();
    });
    renderStatus(entry);
    entry.tab.scrollIntoView({ block: "nearest", inline: "nearest" });
  }

  async function closeSession(id) {
    const entry = state.terms.get(id);
    if (!entry) return;
    if (entry.info.running) {
      const risky = !!entry.info.container;
      const msg = risky
        ? `«${entry.info.title}» sigue en marcha. ¿Interrumpirla? Si está actualizando o recreando un servicio, puede quedar a medias.`
        : `¿Cerrar «${entry.info.title}»?`;
      if (!confirm(msg)) return;
      try { await api(`/api/sessions/${id}`, { method: "DELETE" }); } catch (e) { toast(e.message, true); }
      return; // la pestaña queda con el resultado; se cierra después con ×
    }
    entry.closed = true;
    try { entry.ws && entry.ws.close(); } catch (e) { /* ya cerrado */ }
    try { await api(`/api/sessions/${id}`, { method: "DELETE" }); } catch (e) { /* ya no existe */ }
    entry.term.dispose();
    entry.pane.remove();
    entry.tab.remove();
    state.terms.delete(id);
    if (state.active === id) {
      const next = [...state.terms.keys()].pop();
      state.active = null;
      if (next) activateSession(next);
      else { $("#termEmpty").hidden = false; $("#termStatus").textContent = ""; }
    }
  }

  // Sincroniza pestañas con las sesiones del servidor (otras pestañas del navegador, reconexiones…)
  async function syncSessions() {
    try {
      const data = await api("/api/sessions");
      const ids = new Set();
      for (const info of data.sessions) {
        ids.add(info.id);
        const entry = state.terms.get(info.id);
        if (!entry) openSession(info, false);
        else {
          const wasRunning = entry.info.running;
          entry.info = { ...entry.info, ...info };
          updateTab(entry);
          if (wasRunning && !info.running) setTimeout(loadServices, 300);
        }
      }
      for (const [id, entry] of state.terms) {
        if (!ids.has(id) && !entry.info.running) continue;
        if (!ids.has(id)) { entry.info.running = false; updateTab(entry); }
      }
    } catch (e) { /* servidor reiniciándose */ }
  }

  let resizeTimer;
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
      const entry = state.active && state.terms.get(state.active);
      if (entry) try { entry.fit.fit(); } catch (e) { /* oculto */ }
    }, 120);
  });

  // ── Arranque ───────────────────────────────────────────────
  applyTheme(currentTheme());
  api("/api/info").then((info) => {
    state.info = info;
    if (info.docker_dir) $("#subtitle").textContent = `Gestor de servicios Docker · ${info.docker_dir}/update.sh`;
  }).catch(() => {});
  loadServices();
  syncSessions();
  setInterval(syncSessions, 4000);
  setInterval(() => { if (!document.hidden) loadServices(); }, 30000);
})();
