"use strict";
// AgentViz — 8-bit table scene (spec §8/§9 Phase 4). Purely client-side, a
// consumer of room_message events: each agent is a procedural pixel sprite at a
// shared table; a message makes that agent "speak" (bob + mouth + speech bubble).

const AgentViz = (() => {
  const TALK_MS = 2600; // how long the speaking animation lasts after a message
  let canvas, ctx, dpr = 1;
  let agents = [];
  const lastSpoke = {}; // agent_id -> performance.now() timestamp
  const blinkSched = {}; // agent_id -> {until, next} for irregular, per-sprite blinking
  let running = false;

  // Each sprite blinks on its own randomised schedule (not a linear wave).
  function eyesClosed(key, now) {
    let b = blinkSched[key];
    if (!b) { b = blinkSched[key] = { until: 0, next: now + Math.random() * 4000 }; }
    if (now >= b.next) {
      b.until = now + 100 + Math.random() * 70;          // blink lasts ~100-170ms
      b.next = now + 2200 + Math.random() * 4200;         // next blink in ~2.2-6.4s
      if (Math.random() < 0.15) b.next = now + 220;       // occasional double-blink
    }
    return now < b.until;
  }

  // --- colour helpers -----------------------------------------------------
  function shade(hex, amt) {
    const n = parseInt(hex.replace("#", ""), 16);
    const clamp = (v) => Math.max(0, Math.min(255, v));
    const r = clamp(((n >> 16) & 255) + amt);
    const g = clamp(((n >> 8) & 255) + amt);
    const b = clamp((n & 255) + amt);
    return `rgb(${r},${g},${b})`;
  }

  function resize() {
    const rect = canvas.getBoundingClientRect();
    dpr = window.devicePixelRatio || 1;
    canvas.width = Math.round(rect.width * dpr);
    canvas.height = Math.round(rect.height * dpr);
  }

  // --- one pixel character ------------------------------------------------
  function drawChar(cx, baseY, agent, speaking, now, seed) {
    const H = canvas.height / dpr;
    const U = Math.max(3, Math.floor(H / 34));
    const bob = speaking ? -Math.round((Math.sin(now / 90) + 1) * 0.5) * U : 0;
    const oy = baseY - 18 * U + bob;
    const ox = cx - 6 * U;
    const body = agent.color;
    const legs = shade(body, -40);
    const skin = "#f1c9a5";
    const dark = "#20202f";

    const blk = (gx, gy, gw, gh, c) => {
      ctx.fillStyle = c;
      ctx.fillRect(Math.round(ox + gx * U), Math.round(oy + gy * U), gw * U, gh * U);
    };

    // speaking halo behind the sprite
    if (speaking) {
      ctx.fillStyle = body;
      ctx.globalAlpha = 0.18 + 0.10 * (Math.sin(now / 120) + 1) / 2;
      ctx.fillRect(Math.round(ox - U), Math.round(oy + U), 14 * U, 17 * U);
      ctx.globalAlpha = 1;
    }

    // legs
    blk(3, 14, 2, 4, legs);
    blk(7, 14, 2, 4, legs);
    // torso + arms
    blk(2, 8, 8, 6, body);
    blk(1, 8, 1, 5, shade(body, -20)); // left arm
    blk(10, 8, 1, 5, shade(body, -20)); // right arm
    // neck + head
    blk(5, 7, 2, 1, shade(skin, -25));
    blk(3, 2, 6, 5, skin);

    // eyes (with irregular, per-sprite blink)
    const blink = eyesClosed(agent.id != null ? agent.id : seed, now);
    if (blink) {
      blk(4, 4, 1, 1, skin); blk(7, 4, 1, 1, skin);
    } else {
      blk(4, 4, 1, 1, dark); blk(7, 4, 1, 1, dark);
    }
    // mouth: animates open/closed while speaking
    const open = speaking && Math.floor(now / 170) % 2 === 0;
    blk(5, open ? 6 : 6, 2, open ? 1 : 1, dark);
    if (open) blk(5, 6, 2, 1, dark);

    // per-kind headgear / props
    if (agent.kind === "admin") {
      // golden crown
      const gold = "#ffd23f";
      blk(3, 1, 6, 1, gold);
      blk(3, 0, 1, 1, gold); blk(5, 0, 1, 1, gold); blk(8, 0, 1, 1, gold);
    } else if (agent.kind === "search") {
      // little cap + magnifying glass in hand
      blk(3, 1, 6, 1, shade(body, -30));
      const lens = "#cfe8ff";
      blk(11, 6, 2, 2, lens);
      blk(10, 5, 1, 1, "#8899aa");
      blk(11, 5, 2, 1, "#8899aa"); blk(11, 8, 2, 1, "#8899aa");
      blk(10, 6, 1, 2, "#8899aa"); blk(13, 6, 1, 2, "#8899aa");
    } else {
      // hair tuft
      blk(3, 1, 6, 1, shade(skin, -70));
    }

    // speech bubble above head
    if (speaking) drawBubble(cx, oy - 1 * U, U, now);
  }

  function drawBubble(cx, y, U, now) {
    const w = 8 * U, h = 4 * U;
    const x = Math.round(cx - w / 2);
    const top = Math.round(y - h);
    ctx.fillStyle = "#fdfdf5";
    ctx.fillRect(x, top, w, h);
    ctx.fillRect(Math.round(cx - U), Math.round(top + h), 2 * U, U); // tail
    // animated dots
    const dots = (Math.floor(now / 250) % 3) + 1;
    ctx.fillStyle = "#33303a";
    for (let i = 0; i < dots; i++) {
      ctx.fillRect(Math.round(x + w / 2 - 3 * U + i * 2 * U), Math.round(top + h / 2 - U / 2), U, U);
    }
  }

  function drawNameplate(cx, y, agent, U) {
    const label = agent.name;
    ctx.font = `${Math.max(9, U * 2)}px "Courier New", monospace`;
    ctx.textAlign = "center";
    const w = ctx.measureText(label).width + 2 * U;
    ctx.fillStyle = shade(agent.color, -70);
    ctx.fillRect(Math.round(cx - w / 2), Math.round(y), Math.round(w), Math.round(U * 3));
    ctx.fillStyle = agent.color;
    ctx.fillRect(Math.round(cx - w / 2), Math.round(y), Math.round(w), Math.round(U / 2));
    ctx.fillStyle = "#fdfdf5";
    ctx.textBaseline = "middle";
    ctx.fillText(label, Math.round(cx), Math.round(y + U * 1.7));
  }

  // --- scene --------------------------------------------------------------
  function frame() {
    if (!running) return;
    const now = performance.now();
    const W = canvas.width / dpr, H = canvas.height / dpr;
    const U = Math.max(3, Math.floor(H / 34));
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    // background (dark pixel room with a subtle checker floor)
    ctx.fillStyle = "#14121c";
    ctx.fillRect(0, 0, W, H);
    ctx.fillStyle = "#191727";
    for (let gx = 0; gx * U * 4 < W; gx++) {
      for (let gy = Math.floor(H * 0.55 / (U * 4)); gy * U * 4 < H; gy++) {
        if ((gx + gy) % 2 === 0) ctx.fillRect(gx * U * 4, gy * U * 4, U * 4, U * 4);
      }
    }

    const roster = agents.length ? agents : [];
    const tableY = H - U * 6;
    const baseY = tableY + U * 3;
    const slot = W / Math.max(1, roster.length);

    // characters (behind the table)
    roster.forEach((a, i) => {
      const cx = slot * (i + 0.5);
      const speaking = now - (lastSpoke[a.id] || -1e9) < TALK_MS;
      drawChar(cx, baseY, a, speaking, now, i + 1);
    });

    // table (surface + front) drawn over the legs
    ctx.fillStyle = "#6b4a2b";
    ctx.fillRect(0, Math.round(tableY), W, Math.round(U * 1.5));
    ctx.fillStyle = "#4a3320";
    ctx.fillRect(0, Math.round(tableY + U * 1.5), W, H - tableY);

    // nameplates on the table front
    roster.forEach((a, i) => {
      const cx = slot * (i + 0.5);
      drawNameplate(cx, tableY + U * 2.4, a, U);
    });

    if (!roster.length) {
      ctx.fillStyle = "#6a6480";
      ctx.font = `${U * 3}px "Courier New", monospace`;
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
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
  };
})();
