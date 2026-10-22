// Hellhound Derby overlay effects: particles hung on the anchor points the renderer records per frame.
//
// Everything works on an "effect host" L (one per hound on screen):
//   L.parts   the hound's particles (this module fills it)
//   L.hue     the hound's hue shift (the fire palette is turned by it)
//   L.pt(k)   screen position [x, y] of anchor k ("tail", "mouth", "LH", ...) in the frame now shown
//   L.speed   how fast the track slides past on screen (px/s): track-fixed particles drift with it
//   L.dx      how far the hound moved on screen since the last draw: hound-fixed particles follow it
//   L.ground  the screen y of the track under the hound (drips land there)
//   L.size    particle scale for this hound's size on screen (1 = a 240 px wide hound)
// FX[name].tick(L, dt) runs every frame and FX[name].step(L, paws) on paw touchdowns; drawFX(L, ctx, dt)
// moves and draws the particles.
const HoundFX = (() => {
  const rnd = (a, b) => a + Math.random() * (b - a);
  // the fire palette turned by a hue (white-hot -> yellow -> orange -> red)
  const fire = (t, hue, a = 1) => `hsla(${(48 - 46 * t + hue + 720) % 360},100%,${95 - 52 * t}%,${a})`;

  // world: true = stays on the track; false = rides with the hound
  function spawn(L, p) {
    if (L.parts.length >= 900) return;
    const k = L.size || 1;
    p = Object.assign({ age: 0, world: true, vx: 0, vy: 0 }, p);
    p.r *= k; p.vx *= k; p.vy *= k;
    L.parts.push(p);
  }
  const flame = (L, x, y, o = {}) => spawn(L, { kind: "flame", x: x + rnd(-2, 2), y: y + rnd(-2, 2), vx: rnd(-15, 15) + (o.vx || 0),
    vy: rnd(-110, -55) + (o.vy || 0), life: rnd(.28, .55) * (o.life || 1), r: rnd(4, 9) * (o.size || 1), hue: o.hue ?? L.hue });
  const spark = (L, x, y) => spawn(L, { kind: "spark", x, y, vx: rnd(-25, 25), vy: rnd(-90, -35), life: rnd(.7, 1.5), r: rnd(1, 2.2), hue: L.hue });
  const smoke = (L, x, y, tint, o = {}) => spawn(L, { kind: "smoke", x, y, vx: rnd(-10, 10), vy: rnd(-35, -15), life: rnd(.8, 1.4), r: rnd(4, 7) * (o.size || 1), tint });
  const print = (L, x, y) => spawn(L, { kind: "print", x, y: y + 2, life: 1.3, r: 6, hue: L.hue });
  const dust = (L, x, y, o = {}) => spawn(L, { kind: "smoke", x: x + rnd(-4, 4), y, vx: rnd(-30, 10), vy: rnd(-25, -8), life: rnd(.5, .9), r: rnd(3, 5) * (o.size || 1), tint: o.tint || "120,90,70" });
  // a jagged electric bolt between two points
  function bolt(L, a, b, life, body = false, jag = 9, width = 6) {
    const pts = [], n = 7, k = L.size || 1;
    for (let i = 0; i <= n; i++) {
      const f = i / n, j = (i === 0 || i === n) ? 0 : jag * k;
      pts.push([a[0] + (b[0] - a[0]) * f + rnd(-j, j), a[1] + (b[1] - a[1]) * f + rnd(-j, j)]);
    }
    L.parts.push({ kind: "bolt", age: 0, x: 0, y: 0, vx: 0, vy: 0, r: 1, life, pts, width: width * k, world: !body });
  }
  const along = (L, a, b) => { const p = L.pt(a), q = L.pt(b), k = Math.random(); return [p[0] + (q[0] - p[0]) * k, p[1] + (q[1] - p[1]) * k]; };

  const FX = {
    paw_fire: { step(L, downs) { for (const p of downs) { for (let i = 0; i < 8; i++) flame(L, p[0], p[1], { size: .8 }); print(L, p[0], p[1]); } } },
    dust: { step(L, downs) { for (const p of downs) for (let i = 0; i < 4; i++) dust(L, p[0], p[1]); } },
    embers: { tick(L, dt) { if (Math.random() < 22 * dt) { const p = along(L, "back", "hips"); spark(L, p[0], p[1]); } } },
    // the tail itself burns: tongues of flame lick up all along it and ride with the hound, only lagging
    // a little (a track-fixed stream off the tip reads as a rocket exhaust)
    tail_flame: { tick(L, dt) {
      const pts = [L.pt("tail_base"), L.pt("tail_mid"), L.pt("tail")];
      for (let n = Math.round(rnd(0, 1.5) + 70 * dt); n > 0; n--) {
        const k = Math.random() * 2, i = Math.min(1, Math.floor(k)), f = k - i, a = pts[i], b = pts[i + 1];
        spawn(L, { kind: "flame", world: false, x: a[0] + (b[0] - a[0]) * f + rnd(-2, 2), y: a[1] + (b[1] - a[1]) * f - rnd(0, 3),
          vx: -L.speed * .18 / (L.size || 1) + rnd(-10, 10), vy: rnd(-70, -35), life: rnd(.18, .34), r: rnd(3, 6) * (1.15 - .2 * k), hue: L.hue });
      } } },
    mouth_fire: { tick(L, dt) {
      L.cool = (L.cool || 0) - dt;
      if (L.cool <= 0) { L.burst = .22; L.cool = rnd(.6, 1.1); }
      if (L.burst > 0) { L.burst -= dt; const m = L.pt("mouth"); for (let i = 0; i < 3; i++) flame(L, m[0], m[1], { vx: rnd(150, 260), vy: rnd(10, 60), life: .9, size: 1.2 }); } } },
    mouth_smoke: { tick(L, dt) { if (Math.random() < 9 * dt) { const m = L.pt("mouth"); smoke(L, m[0], m[1], "150,140,140"); } } },
    // brimstone: each paw strike kicks up glowing grit and a little yellow puff; thin wisps smoulder off the back
    sulfur: {
      step(L, downs) { for (const p of downs) {
        for (let i = 0; i < 5; i++) spawn(L, { kind: "grit", x: p[0] + rnd(-3, 3), y: p[1], vx: rnd(-70, 30), vy: rnd(-150, -60), life: rnd(.4, .8), r: rnd(1, 1.8), hue: L.hue });
        smoke(L, p[0], p[1] - 2, "210,200,70"); } },
      tick(L, dt) { if (Math.random() < 7 * dt) { const a = L.pt("back"), b = L.pt("head"), k = Math.random() * .7;
        spawn(L, { kind: "wisp", world: false, x: a[0] + (b[0] - a[0]) * k + rnd(-4, 4), y: a[1] + (b[1] - a[1]) * k - 4, vx: -L.speed * .15 / (L.size || 1), vy: rnd(-30, -18), life: rnd(.7, 1.1), r: rnd(2, 3.5) }); } } },
    drool: { tick(L, dt) {
      L.cool = (L.cool || 0) - dt;
      if (L.cool <= 0) { L.cool = rnd(.25, .7); const m = L.pt("mouth"); spawn(L, { kind: "drop", x: m[0], y: m[1] + 3, vx: rnd(-20, 10), vy: rnd(0, 40), life: 3, r: rnd(1.6, 2.6), hue: L.hue }); } } },
    flies: { tick(L, dt) {
      if (!L.flies) L.flies = Array.from({ length: 5 }, () => ({ a: rnd(0, 6.28), w: rnd(5, 9) * (Math.random() < .5 ? -1 : 1), rx: rnd(22, 40), ry: rnd(10, 22), j: rnd(0, 6) }));
      for (const f of L.flies) f.a += f.w * dt; } },
    slime: {
      step(L, downs) { for (const p of downs) { spawn(L, { kind: "puddle", x: p[0] + rnd(-3, 3), y: p[1] + 2, life: 2.4, r: rnd(7, 10) });
        for (let i = 0; i < 2; i++) spawn(L, { kind: "drip", x: p[0], y: p[1], vx: rnd(-50, 30), vy: rnd(-120, -60), life: 2, r: rnd(1.5, 2.5) }); } },
      tick(L, dt) {
        L.cool = (L.cool || 0) - dt;
        if (L.cool <= 0) { L.cool = rnd(.2, .5); const m = L.pt("mouth"); spawn(L, { kind: "drip", x: m[0], y: m[1] + 2, vx: rnd(-15, 10), vy: rnd(0, 30), life: 3, r: rnd(1.8, 3) }); }
        for (const p of L.parts) if (p.kind === "puddle" && Math.random() < 1.2 * dt)
          L.parts.push({ kind: "bubble", age: 0, world: true, x: p.x + rnd(-p.r * .6, p.r * .6), y: p.y - 1, vx: 0, vy: rnd(-22, -10) * (L.size || 1), life: rnd(.4, .8), r: rnd(1.2, 2.6) * (L.size || 1) }); } },
    // lightning: each paw leaves a crackling electric print, and a faint charge runs along the ground behind
    lightning: {
      step(L, downs) { for (const p of downs) {
        spawn(L, { kind: "eprint", x: p[0], y: p[1] + 2, life: 1.4, r: rnd(6, 8), seed: Math.random() });
        for (let i = 0; i < 3; i++) spawn(L, { kind: "zap", x: p[0], y: p[1], vx: rnd(-60, 60), vy: rnd(-70, -20), life: rnd(.12, .25), r: 1.2 }); } },
      tick(L, dt) {
        L.cool = (L.cool || 0) - dt;
        if (L.cool <= 0) { L.cool = rnd(.05, .12);
          const a = L.pt("RH"), b = L.pt("LF"), k = L.size || 1, y = Math.max(a[1], b[1]) + 2, x = Math.min(a[0], b[0]) - rnd(0, 30) * k;
          bolt(L, [x, y], [x - rnd(20, 45) * k, y + rnd(-1, 1)], rnd(.1, .2), false, 3, 2.5); } } },
    frost: {
      step(L, downs) { for (const p of downs) { spawn(L, { kind: "frost", x: p[0], y: p[1] + 2, life: 2.6, r: rnd(6, 8), rot: rnd(0, 1) });
        for (let i = 0; i < 4; i++) spawn(L, { kind: "shard", x: p[0], y: p[1], vx: rnd(-80, 50), vy: rnd(-140, -50), life: rnd(.4, .8), r: rnd(1.5, 3), rot: rnd(0, 6) }); } },
      tick(L, dt) { if (Math.random() < 30 * dt) { const p = along(L, "back", "tail");
        spawn(L, { kind: "flake", x: p[0] + rnd(-4, 4), y: p[1] + rnd(-6, 6), vx: rnd(-20, 20), vy: rnd(-8, 25), life: rnd(.8, 1.6), r: rnd(.8, 1.8) }); } } },
    soul_fire: { tick(L, dt) {
      const e = L.pt("eye"); for (let n = Math.round(rnd(0, 1) + 45 * dt); n > 0; n--) flame(L, e[0], e[1], { size: .7, hue: 150 });
      if (Math.random() < 25 * dt) { const p = along(L, "back", "hips"); flame(L, p[0], p[1], { size: .8, hue: 150 }); } } },
  };

  function drawFX(L, ctx, dt) {
    const parts = L.parts, G = L.ground, dx = L.dx || 0, drift = (L.speed || 0) * dt;
    for (let i = parts.length - 1; i >= 0; i--) {
      const p = parts[i]; p.age += dt;
      if (p.age >= p.life) { parts.splice(i, 1); continue; }
      if (p.kind === "drop" || p.kind === "drip" || p.kind === "shard" || p.kind === "zap" || p.kind === "grit") {
        p.vy += (p.kind === "zap" ? 300 : p.kind === "drop" ? 900 : 800) * dt * (L.size || 1);
        if (p.y + p.vy * dt >= G) {
          parts.splice(i, 1);
          if (p.kind === "drip") spawn(L, { kind: "puddle", x: p.x, y: G, life: 1.6, r: rnd(3, 5) });
          if (p.kind === "drop") { for (let k = 0; k < 3; k++) spawn(L, { kind: "spark", x: p.x, y: G, vx: rnd(-40, 40), vy: rnd(-80, -30), life: .4, r: 1.2, hue: p.hue }); print(L, p.x, G - 2); }
          continue;
        }
      }
      if (p.kind === "bolt") {
        for (const q of p.pts) q[0] += p.world ? -drift : dx;
      } else {
        p.x += p.vx * dt + (p.world ? -drift : dx); p.y += p.vy * dt;
      }
      const t = p.age / p.life;
      switch (p.kind) {
        case "puddle": {
          ctx.globalCompositeOperation = "source-over";
          const a = Math.min(1, (1 - t) * 1.6);
          ctx.fillStyle = `rgba(70,200,40,${.75 * a})`;
          ctx.beginPath(); ctx.ellipse(p.x, p.y, p.r, p.r * .32, 0, 0, 6.283); ctx.fill();
          ctx.globalCompositeOperation = "lighter";
          ctx.fillStyle = `rgba(140,255,90,${.45 * a})`;
          ctx.beginPath(); ctx.ellipse(p.x - p.r * .25, p.y - p.r * .08, p.r * .35, p.r * .1, 0, 0, 6.283); ctx.fill();
          break;
        }
        case "drip":
          ctx.globalCompositeOperation = "lighter";
          ctx.fillStyle = "rgba(120,255,70,.9)";
          ctx.beginPath(); ctx.ellipse(p.x, p.y, p.r * .8, p.r * (1.2 + Math.min(1.5, p.vy / 300)), 0, 0, 6.283); ctx.fill();
          break;
        case "bubble":
          ctx.globalCompositeOperation = "lighter";
          ctx.strokeStyle = `rgba(160,255,110,${.8 * (1 - t)})`; ctx.lineWidth = 1;
          ctx.beginPath(); ctx.arc(p.x, p.y, p.r * (1 + t * .6), 0, 6.283); ctx.stroke();
          break;
        case "bolt": {
          ctx.globalCompositeOperation = "lighter";
          const flick = Math.random() < .25 ? .3 : 1, fade = 1 - t * .6, W = p.width || 6;
          for (const [w, col] of [[W, `rgba(70,160,255,${.25 * flick * fade})`], [W * .4, `rgba(140,210,255,${.7 * flick * fade})`], [Math.max(.8, W / 6), `rgba(255,255,255,${flick * fade})`]]) {
            ctx.strokeStyle = col; ctx.lineWidth = w; ctx.lineJoin = "round";
            ctx.beginPath(); p.pts.forEach((q, k) => k ? ctx.lineTo(q[0], q[1]) : ctx.moveTo(q[0], q[1])); ctx.stroke();
          }
          break;
        }
        case "zap":
          ctx.globalCompositeOperation = "lighter";
          ctx.fillStyle = `rgba(190,230,255,${1 - t})`;
          ctx.fillRect(p.x - 1, p.y - 1, 2.2, 2.2);
          break;
        case "eprint": {
          // a crackling print: jagged rays from where the paw struck, flickering as it fades
          ctx.globalCompositeOperation = "lighter";
          const a = (1 - t) * (Math.random() < .2 ? .35 : 1);
          ctx.fillStyle = `rgba(90,170,255,${.45 * a})`;
          ctx.beginPath(); ctx.ellipse(p.x, p.y, p.r, p.r * .3, 0, 0, 6.283); ctx.fill();
          ctx.strokeStyle = `rgba(200,235,255,${a})`; ctx.lineWidth = 1;
          ctx.beginPath();
          for (let k = 0; k < 5; k++) {
            const ang = p.seed * 6.283 + k * 1.2566, len = p.r * (1.1 + .5 * Math.sin(p.seed * 50 + k));
            ctx.moveTo(p.x, p.y);
            for (let s = 1; s <= 3; s++) ctx.lineTo(p.x + Math.cos(ang) * len * s / 3 + rnd(-1.5, 1.5), p.y + Math.sin(ang) * len * .35 * s / 3 + rnd(-1, 1));
          }
          ctx.stroke();
          break;
        }
        case "grit":
          ctx.globalCompositeOperation = "lighter";
          ctx.fillStyle = fire(.15 + t * .5, p.hue, 1 - t * .7);
          ctx.fillRect(p.x - p.r / 2, p.y - p.r / 2, p.r, p.r);
          break;
        case "wisp":
          ctx.globalCompositeOperation = "source-over";
          ctx.fillStyle = `rgba(205,195,95,${.16 * Math.sin(Math.PI * t)})`;
          ctx.beginPath(); ctx.ellipse(p.x, p.y, p.r * (1 + t), p.r * (1.6 + t * 2), 0, 0, 6.283); ctx.fill();
          break;
        case "frost": {
          ctx.globalCompositeOperation = "lighter";
          const a = (1 - t) * .8;
          ctx.fillStyle = `rgba(150,210,255,${a * .5})`;
          ctx.beginPath(); ctx.ellipse(p.x, p.y, p.r, p.r * .3, 0, 0, 6.283); ctx.fill();
          ctx.strokeStyle = `rgba(220,240,255,${a})`; ctx.lineWidth = 1;
          ctx.beginPath();
          for (let k = 0; k < 6; k++) { const ang = p.rot + k * 1.047; ctx.moveTo(p.x, p.y); ctx.lineTo(p.x + Math.cos(ang) * p.r * 1.3, p.y + Math.sin(ang) * p.r * .45); }
          ctx.stroke();
          break;
        }
        case "shard":
          ctx.globalCompositeOperation = "lighter";
          ctx.fillStyle = `rgba(200,235,255,${1 - t})`;
          p.rot += 12 * dt; ctx.save(); ctx.translate(p.x, p.y); ctx.rotate(p.rot);
          ctx.beginPath(); ctx.moveTo(0, -p.r * 1.6); ctx.lineTo(p.r * .6, 0); ctx.lineTo(0, p.r * 1.6); ctx.lineTo(-p.r * .6, 0); ctx.fill(); ctx.restore();
          break;
        case "flake":
          ctx.globalCompositeOperation = "lighter";
          ctx.fillStyle = `rgba(230,245,255,${.8 * (1 - t)})`;
          ctx.beginPath(); ctx.arc(p.x, p.y, p.r, 0, 6.283); ctx.fill();
          break;
        case "flame": {
          p.vx *= .97; const r = p.r * (1 - t * .6);
          ctx.globalCompositeOperation = "lighter";
          const g = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, r);
          g.addColorStop(0, fire(t, p.hue, .9 * (1 - t))); g.addColorStop(1, fire(Math.min(1, t + .3), p.hue, 0));
          ctx.fillStyle = g; ctx.beginPath(); ctx.arc(p.x, p.y, r, 0, 6.283); ctx.fill();
          break;
        }
        case "spark":
          ctx.globalCompositeOperation = "lighter"; p.vx += rnd(-80, 80) * dt;
          ctx.fillStyle = fire(t * .7, p.hue, (1 - t) * (.6 + .4 * Math.random()));
          ctx.beginPath(); ctx.arc(p.x, p.y, p.r, 0, 6.283); ctx.fill();
          break;
        case "print":
          ctx.globalCompositeOperation = "lighter";
          ctx.fillStyle = fire(.35 + t * .6, p.hue, .7 * (1 - t));
          ctx.beginPath(); ctx.ellipse(p.x, p.y, p.r, p.r * .35, 0, 0, 6.283); ctx.fill();
          break;
        case "smoke":
          ctx.globalCompositeOperation = "source-over";
          ctx.fillStyle = `rgba(${p.tint},${.35 * (1 - t)})`;
          ctx.beginPath(); ctx.arc(p.x, p.y, p.r * (1 + t * 2.2), 0, 6.283); ctx.fill();
          break;
        case "drop":
          ctx.globalCompositeOperation = "lighter";
          ctx.fillStyle = fire(.25, p.hue, .9);
          ctx.beginPath(); ctx.ellipse(p.x, p.y, p.r * .8, p.r * 1.5, 0, 0, 6.283); ctx.fill();
          break;
      }
    }
    ctx.globalCompositeOperation = "source-over";
    if (L.flies) {
      const b = L.pt("back"), k = L.size || 1;
      for (const f of L.flies) {
        const x = b[0] + (Math.cos(f.a) * f.rx + Math.sin(f.a * 2.7 + f.j) * 4) * k, y = b[1] + (-14 + Math.sin(f.a) * f.ry) * k;
        ctx.fillStyle = "rgba(20,16,12,.95)"; ctx.beginPath(); ctx.arc(x, y, 1.8 * k, 0, 6.283); ctx.fill();
        ctx.fillStyle = "rgba(200,200,220,.35)"; ctx.beginPath(); ctx.ellipse(x - k, y - 2 * k, 2 * k, k, -.5, 0, 6.283); ctx.fill();
      }
    }
  }

  return { FX, drawFX, spawn, flame, spark, smoke, dust, bolt, fire, rnd };
})();
