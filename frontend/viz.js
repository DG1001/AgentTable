"use strict";
// AgentViz — 8-bit table scene (spec §8/§9 Phase 4). Purely client-side, a
// consumer of room_message events: each agent is a procedural pixel sprite at a
// shared table. A message makes that agent "speak" (bob + mouth + speech bubble);
// emojis in a message float up as reactions (emotes). All drawn procedurally —
// no external assets, no build step (matches the project's self-contained ethos).

const AgentViz = (() => {
  const TALK_MS = 2600;
  let canvas, ctx, dpr = 1;
  let agents = [];
  const lastSpoke = {};   // agent_id -> performance.now() timestamp
  const blinkSched = {};  // agent_id -> {until, next} irregular per-sprite blinking
  const emotes = [];      // {key, emoji, t0, dx}
  const pos = {};         // agent_id -> {cx, headTop, U} (updated each frame)
  let running = false;

  // --- deterministic per-agent looks -------------------------------------
  const SKIN = ["#f1c9a5", "#e7b58a", "#d29b6e", "#c0875a", "#f6d3b0"];
  const HAIRC = ["#2a211b", "#4a3728", "#7a5230", "#141414", "#9a3b1f", "#c8a24a", "#8f8f8f"];
  function features(agent) {
    const id = agent.id != null ? agent.id : 1;
    return {
      skin: SKIN[id % SKIN.length],
      hair: HAIRC[(id * 5) % HAIRC.length],
      style: id % 4,               // 0 short, 1 side-part, 2 long, 3 buzz
      beard: id % 3 === 0,
      glasses: id % 5 === 2,
    };
  }

  function eyesClosed(key, now) {
    let b = blinkSched[key];
    if (!b) { b = blinkSched[key] = { until: 0, next: now + Math.random() * 4000 }; }
    if (now >= b.next) {
      b.until = now + 100 + Math.random() * 70;
      b.next = now + 2200 + Math.random() * 4200;
      if (Math.random() < 0.15) b.next = now + 220;
    }
    return now < b.until;
  }

  function shade(hex, amt) {
    if (hex[0] !== "#") return hex;
    const n = parseInt(hex.slice(1), 16);
    const c = (v) => Math.max(0, Math.min(255, v));
    return `rgb(${c(((n >> 16) & 255) + amt)},${c(((n >> 8) & 255) + amt)},${c((n & 255) + amt)})`;
  }

  function resize() {
    const rect = canvas.getBoundingClientRect();
    dpr = window.devicePixelRatio || 1;
    canvas.width = Math.round(rect.width * dpr);
    canvas.height = Math.round(rect.height * dpr);
  }

  // --- one pixel character ------------------------------------------------
  function drawChar(cx, baseY, agent, speaking, now, U) {
    const f = features(agent);
    const bob = speaking ? -Math.round((Math.sin(now / 90) + 1) * 0.5) * U : 0;
    const oy = baseY - 18 * U + bob;
    const ox = cx - 6 * U;
    const body = agent.color;
    const legs = shade(body, -40);
    const skin = f.skin;
    const dark = "#20202f";
    const blk = (gx, gy, gw, gh, c) => {
      ctx.fillStyle = c;
      ctx.fillRect(Math.round(ox + gx * U), Math.round(oy + gy * U), gw * U, gh * U);
    };

    if (speaking) {  // soft halo
      ctx.fillStyle = body;
      ctx.globalAlpha = 0.16 + 0.10 * (Math.sin(now / 120) + 1) / 2;
      ctx.fillRect(Math.round(ox - U), Math.round(oy + U), 14 * U, 17 * U);
      ctx.globalAlpha = 1;
    }

    // legs, torso, arms
    blk(3, 14, 2, 4, legs);
    blk(7, 14, 2, 4, legs);
    blk(2, 8, 8, 6, body);
    blk(2, 8, 8, 1, shade(body, 25));      // collar highlight
    blk(1, 8, 1, 5, shade(body, -20));
    blk(10, 8, 1, 5, shade(body, -20));
    // neck + head
    blk(5, 7, 2, 1, shade(skin, -25));
    blk(3, 2, 6, 5, skin);
    blk(8, 3, 1, 3, shade(skin, -25));     // cheek shading

    // hair (skipped for the crowned admin; searcher keeps a cap below)
    if (agent.kind !== "admin") {
      if (f.style === 0) { blk(3, 1, 6, 1, f.hair); blk(3, 2, 1, 1, f.hair); blk(8, 2, 1, 1, f.hair); }
      else if (f.style === 1) { blk(3, 1, 6, 1, f.hair); blk(3, 2, 3, 1, f.hair); }
      else if (f.style === 2) { blk(3, 1, 6, 1, f.hair); blk(3, 2, 1, 4, f.hair); blk(8, 2, 1, 4, f.hair); }
      else { blk(4, 1, 4, 1, shade(f.hair, 10)); }
    }

    // eyes / blink / glasses
    const blink = eyesClosed(agent.id != null ? agent.id : cx, now);
    if (f.glasses) {
      blk(3, 4, 3, 1, dark); blk(6, 4, 3, 1, dark); blk(6, 4, 1, 1, dark);
      if (!blink) { blk(4, 4, 1, 1, "#1a1a2a"); blk(7, 4, 1, 1, "#1a1a2a"); }
    } else {
      blk(4, 4, 1, 1, blink ? skin : dark);
      blk(7, 4, 1, 1, blink ? skin : dark);
    }
    // mouth (animates while speaking) + optional beard
    if (f.beard) { blk(4, 6, 4, 1, shade(f.hair, -10)); blk(4, 5, 1, 1, shade(f.hair, -10)); blk(7, 5, 1, 1, shade(f.hair, -10)); }
    const open = speaking && Math.floor(now / 170) % 2 === 0;
    blk(5, 6, 2, 1, dark);
    if (open) blk(5, 6, 2, 1, "#7a1f1f");

    // per-kind headgear / props
    if (agent.kind === "admin") {
      const gold = "#ffd23f";
      blk(3, 1, 6, 1, gold);
      blk(3, 0, 1, 1, gold); blk(5, 0, 1, 1, gold); blk(8, 0, 1, 1, gold);
      blk(4, 0, 1, 1, "#ff5d73"); blk(7, 0, 1, 1, "#5db0ff"); // jewels
    } else if (agent.kind === "search") {
      const lens = "#cfe8ff";
      blk(11, 6, 2, 2, lens);
      blk(10, 5, 1, 1, "#8899aa"); blk(11, 5, 2, 1, "#8899aa"); blk(11, 8, 2, 1, "#8899aa");
      blk(10, 6, 1, 2, "#8899aa"); blk(13, 6, 1, 2, "#8899aa");
    }

    if (speaking) drawBubble(cx, oy - 1 * U, U, now);
  }

  function drawBubble(cx, y, U, now) {
    const w = 8 * U, h = 4 * U;
    const x = Math.round(cx - w / 2), top = Math.round(y - h);
    ctx.fillStyle = "#fdfdf5";
    ctx.fillRect(x, top, w, h);
    ctx.fillRect(Math.round(cx - U), Math.round(top + h), 2 * U, U);
    const dots = (Math.floor(now / 250) % 3) + 1;
    ctx.fillStyle = "#33303a";
    for (let i = 0; i < dots; i++) {
      ctx.fillRect(Math.round(x + w / 2 - 3 * U + i * 2 * U), Math.round(top + h / 2 - U / 2), U, U);
    }
  }

  function drawNameplate(cx, y, agent, U) {
    ctx.font = `${Math.max(9, U * 2)}px "Courier New", monospace`;
    ctx.textAlign = "center";
    const w = ctx.measureText(agent.name).width + 2 * U;
    ctx.fillStyle = shade(agent.color, -70);
    ctx.fillRect(Math.round(cx - w / 2), Math.round(y), Math.round(w), Math.round(U * 3));
    ctx.fillStyle = agent.color;
    ctx.fillRect(Math.round(cx - w / 2), Math.round(y), Math.round(w), Math.round(U / 2));
    ctx.fillStyle = "#fdfdf5";
    ctx.textBaseline = "middle";
    ctx.fillText(agent.name, Math.round(cx), Math.round(y + U * 1.7));
  }

  function drawEmotes(now) {
    const LIFE = 1700;
    for (let i = emotes.length - 1; i >= 0; i--) {
      const e = emotes[i];
      const age = now - e.t0;
      if (age > LIFE) { emotes.splice(i, 1); continue; }
      const p = pos[e.key];
      if (!p) continue;
      const t = age / LIFE;
      const alpha = t < 0.12 ? t / 0.12 : 1 - (t - 0.12) / 0.88;
      const scale = 0.6 + Math.min(1, t * 5) * 0.55;
      ctx.globalAlpha = Math.max(0, alpha);
      ctx.font = `${Math.round(p.U * 4.2 * scale)}px "Segoe UI Emoji","Apple Color Emoji",serif`;
      ctx.textAlign = "center"; ctx.textBaseline = "middle";
      ctx.fillText(e.emoji, p.cx + e.dx, p.headTop - t * 24 * p.U / 6);
      ctx.globalAlpha = 1;
    }
  }

  // --- scene --------------------------------------------------------------
  function frame() {
    if (!running) return;
    const now = performance.now();
    const W = canvas.width / dpr, H = canvas.height / dpr;
    const U = Math.max(3, Math.floor(H / 34));
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    // wall + warm hanging lamp glow
    ctx.fillStyle = "#14121c";
    ctx.fillRect(0, 0, W, H);
    const g = ctx.createRadialGradient(W / 2, 0, U, W / 2, 0, H * 0.9);
    g.addColorStop(0, "rgba(255,210,120,0.16)");
    g.addColorStop(1, "rgba(255,210,120,0)");
    ctx.fillStyle = g; ctx.fillRect(0, 0, W, H);
    ctx.fillStyle = "#2a2733"; ctx.fillRect(Math.round(W / 2 - U / 2), 0, U, Math.round(U * 2));
    ctx.fillStyle = "#ffd98a"; ctx.fillRect(Math.round(W / 2 - U), Math.round(U * 2), U * 2, U); // bulb

    // checker floor
    ctx.fillStyle = "#191727";
    for (let gx = 0; gx * U * 4 < W; gx++)
      for (let gy = Math.floor(H * 0.55 / (U * 4)); gy * U * 4 < H; gy++)
        if ((gx + gy) % 2 === 0) ctx.fillRect(gx * U * 4, gy * U * 4, U * 4, U * 4);

    const roster = agents.length ? agents : [];
    const tableY = H - U * 6;
    const baseY = tableY + U * 3;
    const slot = W / Math.max(1, roster.length);

    // chair backs behind each sprite
    ctx.fillStyle = "#3a2c1e";
    roster.forEach((a, i) => {
      const cx = slot * (i + 0.5);
      ctx.fillRect(Math.round(cx - 5 * U), Math.round(baseY - 17 * U), U, Math.round(15 * U));
      ctx.fillRect(Math.round(cx + 4 * U), Math.round(baseY - 17 * U), U, Math.round(15 * U));
      ctx.fillRect(Math.round(cx - 5 * U), Math.round(baseY - 17 * U), 10 * U, U);
    });

    // characters + record positions for emotes
    roster.forEach((a, i) => {
      const cx = slot * (i + 0.5);
      const speaking = now - (lastSpoke[a.id] || -1e9) < TALK_MS;
      drawChar(cx, baseY, a, speaking, now, U);
      pos[a.id] = { cx, headTop: baseY - 19 * U, U };
    });

    // table (surface + front) over the legs
    ctx.fillStyle = "#6b4a2b"; ctx.fillRect(0, Math.round(tableY), W, Math.round(U * 1.5));
    ctx.fillStyle = "#7d5836"; ctx.fillRect(0, Math.round(tableY), W, Math.round(U * 0.4));
    ctx.fillStyle = "#4a3320"; ctx.fillRect(0, Math.round(tableY + U * 1.5), W, H - tableY);

    // a couple of mugs on the table for cosiness
    roster.forEach((a, i) => {
      if (i % 2 === 0) return;
      const cx = slot * (i + 0.5) + 3 * U;
      const my = Math.round(tableY - U * 1.6);
      ctx.fillStyle = "#d9d2c4"; ctx.fillRect(Math.round(cx), my, Math.round(U * 1.6), Math.round(U * 1.8));
      ctx.fillStyle = "#c69a3f"; ctx.fillRect(Math.round(cx), my, Math.round(U * 1.6), Math.round(U * 0.6)); // beer
      ctx.fillStyle = "#d9d2c4"; ctx.fillRect(Math.round(cx + U * 1.6), Math.round(my + U * 0.3), Math.round(U * 0.5), Math.round(U));
    });

    // nameplates
    roster.forEach((a, i) => drawNameplate(slot * (i + 0.5), tableY + U * 2.4, a, U));

    drawEmotes(now);

    if (!roster.length) {
      ctx.fillStyle = "#6a6480";
      ctx.font = `${U * 3}px "Courier New", monospace`;
      ctx.textAlign = "center"; ctx.textBaseline = "middle";
      ctx.fillText("… noch niemand am Tisch …", W / 2, H / 2);
    }
    requestAnimationFrame(frame);
  }

  // --- public API ---------------------------------------------------------
  return {
    init(el, roster) {
      canvas = el;
      ctx = canvas.getContext("2d");
      ctx.imageSmoothingEnabled = false;
      agents = (roster || []).filter((a) => a.kind !== undefined);
      resize();
      new ResizeObserver(resize).observe(canvas);
      if (!running) { running = true; requestAnimationFrame(frame); }
    },
    setAgents(roster) { agents = roster || []; },
    speak(agentId) { if (agentId != null) lastSpoke[agentId] = performance.now(); },
    react(agentId, emoji) {
      if (agentId == null || !emoji) return;
      emotes.push({ key: agentId, emoji, t0: performance.now(), dx: (Math.random() - 0.5) * 18 });
      if (emotes.length > 40) emotes.shift();
    },
    celebrate(emoji) { agents.forEach((a, i) => setTimeout(() => this.react(a.id, emoji || "🎉"), i * 120)); },
  };
})();
