"use strict";
// AgentTable SPA — vanilla JS, no build step. Talks to /api + two WebSockets.

const qs = (s) => document.querySelector(s);
const tokenFromUrl = new URLSearchParams(location.search).get("t");

// --- tiny markdown: escape, bold/italic, and pipe tables ------------------
function esc(s) {
  return s.replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
}
function renderMarkdown(text) {
  const lines = text.split("\n");
  let html = "";
  let i = 0;
  while (i < lines.length) {
    if (lines[i].includes("|") && lines[i + 1] && /^\s*\|?[-\s|:]+\|/.test(lines[i + 1])) {
      const rows = [];
      while (i < lines.length && lines[i].includes("|")) rows.push(lines[i++]);
      html += renderTable(rows);
      continue;
    }
    html += inline(lines[i]) + "<br/>";
    i++;
  }
  return html.replace(/(<br\/>)+$/, "");
}
function escAttr(s) {
  return esc(s).replace(/"/g, "&quot;");
}
function inline(s) {
  // Turn markdown links AND bare URLs into clickable source chips. Both are
  // stashed behind ASCII sentinels (URLs may contain _ or *, and the sentinel
  // must not collide with real numbers) before bold/italic runs, then restored.
  const links = [];
  const stash = (html) => { links.push(html); return "LINKMARK" + (links.length - 1) + "ENDMARK"; };
  s = s.replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, (_m, text, url) => {
    const host = url.replace(/^https?:\/\/(www\.)?/, "").split("/")[0];
    return stash('<a class="src" href="' + escAttr(url) + '" target="_blank" rel="noopener noreferrer" title="' + escAttr(url) + '">' + (esc(text) || esc(host)) + '</a>');
  });
  s = s.replace(/https?:\/\/[^\s<>()\[\]"']+/g, (url) => {
    const clean = url.replace(/[.,;:!?]+$/, "");
    const trail = url.slice(clean.length);
    const host = clean.replace(/^https?:\/\/(www\.)?/, "").split("/")[0];
    return stash('<a class="src" href="' + escAttr(clean) + '" target="_blank" rel="noopener noreferrer" title="' + escAttr(clean) + '">' + esc(host) + '</a>') + trail;
  });
  s = esc(s)
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/_(.+?)_/g, "<em>$1</em>");
  return s.replace(/LINKMARK(\d+)ENDMARK/g, (_m, i) => links[+i]);
}
function renderTable(rows) {
  const cells = (r) => r.split("|").map((c) => c.trim()).filter((c, idx, a) => !(idx === 0 && c === "") && !(idx === a.length - 1 && c === ""));
  const head = cells(rows[0]);
  const body = rows.slice(2).map(cells);
  let h = "<table><thead><tr>" + head.map((c) => `<th>${inline(c)}</th>`).join("") + "</tr></thead><tbody>";
  h += body.map((r) => "<tr>" + r.map((c) => `<td>${inline(c)}</td>`).join("") + "</tr>").join("");
  return h + "</tbody></table>";
}

// --- message rendering ----------------------------------------------------
// Track the highest message id per channel + a seen-set, so a reconnect can
// resync only the missed messages (via ?since=) without duplicating.
let lastPrivateId = 0, lastRoomId = 0;
const seenPrivate = new Set(), seenRoom = new Set();

function appendPrivate(m) {
  if (m.id) {
    if (seenPrivate.has(m.id)) return;
    seenPrivate.add(m.id);
    lastPrivateId = Math.max(lastPrivateId, m.id);
  }
  const log = qs("#private-log");
  const div = document.createElement("div");
  div.className = "msg " + (m.role === "user" ? "user" : m.role === "system" ? "system" : "agent");
  div.innerHTML = renderMarkdown(m.content);
  log.appendChild(div);
  log.scrollTop = log.scrollHeight;
}
function appendRoom(m) {
  if (m.id) {
    if (seenRoom.has(m.id)) return;
    seenRoom.add(m.id);
    lastRoomId = Math.max(lastRoomId, m.id);
  }
  const log = qs("#room-log");
  const div = document.createElement("div");
  div.className = "msg " + (m.kind === "system" ? "system" : "agent");
  if (m.kind !== "system") {
    div.style.setProperty("--who", m.color);
    const who = document.createElement("div");
    who.className = "who";
    who.textContent = m.agent_name;
    who.style.color = m.color;
    div.appendChild(who);
  }
  const body = document.createElement("div");
  body.innerHTML = renderMarkdown(m.content);
  div.appendChild(body);
  log.appendChild(div);
  log.scrollTop = log.scrollHeight;
}

// --- task panel -----------------------------------------------------------
let lastTask = null;
function renderTask(task) {
  lastTask = task;
  const el = qs("#task-status");
  if (!task) {
    el.className = "task-status muted";
    el.textContent = "Keine Terminfindung aktiv.";
    return;
  }
  const labels = { collecting: "Sammle Verfügbarkeiten", negotiating: "Verhandlung läuft", decided: "Entschieden", failed: "Gescheitert" };
  let html = `<span class="badge ${task.status}">${labels[task.status] || task.status}</span> `;
  html += `<strong>${esc(task.params.description || "Treffen")}</strong> — ${task.params.range_start} bis ${task.params.range_end}`;
  if (task.result && task.result.slot) {
    html += `<br/>✅ <strong>${esc(task.result.slot.label)}</strong>`;
    if (task.result.location) html += ` @ ${esc(task.result.location)}`;
    if (task.status === "decided" && task.share_token) {
      html += `<div class="result-actions">`
        + `<a class="mini" href="${apiUrl(`task/${task.id}/ics`)}">📅 Zum Kalender</a>`
        + `<button class="mini" type="button" onclick="shareResult(event,'${task.share_token}')">🔗 Ergebnis teilen</button>`
        + `</div>`;
    }
  }
  el.className = "task-status active";
  el.innerHTML = html;
  qs("#start-btn").style.display = ["collecting", "negotiating"].includes(task.status) ? "none" : "";
}

// Copy the public share-card link to the clipboard (button lives in the task panel).
function shareResult(ev, token) {
  const url = location.origin + "/share/" + token;
  const btn = ev.target, orig = btn.textContent;
  const ok = () => { btn.textContent = "✓ kopiert"; setTimeout(() => (btn.textContent = orig), 1500); };
  if (navigator.clipboard) navigator.clipboard.writeText(url).then(ok).catch(() => prompt("Link zum Teilen:", url));
  else prompt("Link zum Teilen:", url);
}

let myReady = false;
function renderReadyBar(task, ready) {
  const bar = qs("#ready-bar"), btn = qs("#ready-btn");
  if (task && task.status === "collecting") {
    bar.classList.remove("hidden");
    btn.disabled = !!ready;
    btn.textContent = ready ? "✓ Bereit gemeldet" : "✓ Ich bin bereit";
  } else {
    bar.classList.add("hidden");
  }
}

async function refreshMe() {
  try {
    const me = await (await fetch("api/me" + (tokenFromUrl ? `?t=${tokenFromUrl}` : ""))).json();
    myReady = !!me.ready;
    renderTask(me.task);
    renderReadyBar(me.task, myReady);
  } catch {}
}

// --- websockets -----------------------------------------------------------
function wsUrl(path) {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  const base = location.pathname.replace(/\/$/, "");
  const t = tokenFromUrl ? `?t=${encodeURIComponent(tokenFromUrl)}` : "";
  return `${proto}//${location.host}${base}/${path}${t}`;
}
function apiUrl(path, params = {}) {
  const q = new URLSearchParams(params);
  if (tokenFromUrl) q.set("t", tokenFromUrl);
  const s = q.toString();
  return "api/" + path + (s ? "?" + s : "");
}
function connect(path, onMsg, onReopen) {
  let ws, first = true;
  const open = () => {
    ws = new WebSocket(wsUrl(path));
    ws.onopen = () => { if (!first && onReopen) onReopen(); first = false; };
    ws.onmessage = (e) => onMsg(JSON.parse(e.data), ws);
    ws.onclose = () => setTimeout(open, 1500);
  };
  open();
  return () => ws;
}
// On reconnect: pull only the messages missed during the outage (deduped by id).
async function resyncPrivate() {
  try {
    const d = await (await fetch(apiUrl("private/history", { since: lastPrivateId }))).json();
    d.messages.forEach(appendPrivate);
  } catch {}
}
async function resyncRoom() {
  try {
    const d = await (await fetch(apiUrl("room/history", { since: lastRoomId }))).json();
    d.messages.forEach(appendRoom);
  } catch {}
  refreshMe();
}

// --- boot -----------------------------------------------------------------
async function main() {
  let me;
  try {
    me = await (await fetch("api/me" + (tokenFromUrl ? `?t=${tokenFromUrl}` : ""))).json();
  } catch {
    qs("#whoami").textContent = "Nicht eingeloggt — Magic-Link nötig.";
    return;
  }
  if (me.detail) {
    qs("#whoami").textContent = me.detail;
    return;
  }
  qs("#whoami").innerHTML = `${esc(me.user.display_name)} · Gruppe „${esc(me.group.name)}"`;
  if (me.user.persona) {
    const p = qs("#persona");
    p.textContent = me.user.persona;
    p.classList.remove("hidden");
  }

  // 8-bit table scene (Phase 4): seat the group's agents (organizer, search, people)
  AgentViz.init(document.getElementById("viz"), me.agents || []);

  (await (await fetch(apiUrl("private/history"))).json()).messages.forEach(appendPrivate);
  (await (await fetch(apiUrl("room/history"))).json()).messages.forEach(appendRoom);
  renderTask(me.task);
  myReady = !!me.ready;
  renderReadyBar(me.task, myReady);

  if (!me.onboarding_done && qs("#private-log").children.length === 0) {
    appendPrivate({ role: "agent", content: "Hi! Ich bin dein persönlicher Agent. Erzähl mir kurz etwas über dich — wie sollen wir dich nennen?" });
  }

  const getPrivate = connect("ws/private", (msg) => {
    if (msg.type === "private_message") appendPrivate(msg.message);
  }, resyncPrivate);
  connect("ws/room", (msg) => {
    if (msg.type === "room_message") {
      appendRoom(msg.message);
      if (msg.message.kind !== "system") AgentViz.speak(msg.message.agent_id);
    }
    if (msg.type === "task_update") { renderTask(msg.task); refreshMe(); }
  }, resyncRoom);

  // deterministic readiness (the LLM often forgets to call mark_ready)
  qs("#ready-btn").addEventListener("click", async () => {
    const r = await fetch("api/ready" + (tokenFromUrl ? `?t=${tokenFromUrl}` : ""), { method: "POST" });
    const data = await r.json();
    if (data.ok) { myReady = true; renderReadyBar(lastTask, true); }
    else if (data.message) appendPrivate({ role: "system", content: data.message });
  });

  qs("#private-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const input = qs("#private-input");
    const text = input.value.trim();
    if (!text) return;
    getPrivate().send(JSON.stringify({ text }));
    input.value = "";
  });

  // start-task dialog
  const dlg = qs("#start-dialog");
  qs("#start-btn").addEventListener("click", () => dlg.showModal());
  qs("#start-form").addEventListener("submit", async (e) => {
    if (e.submitter && e.submitter.value === "cancel") return;
    e.preventDefault();
    const body = {
      description: qs("#f-desc").value,
      range_start: qs("#f-start").value,
      range_end: qs("#f-end").value,
      granularity: qs("#f-gran").value,
    };
    const r = await fetch("api/task/start" + (tokenFromUrl ? `?t=${tokenFromUrl}` : ""), {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    const data = await r.json();
    if (data.task) renderTask(data.task);
    else alert(data.detail || "Fehler beim Start.");
    dlg.close();
  });
}
main();
