/*
 * Hexcast Games — Hellhound Derby renderer                                   derby.js
 *
 * The overlay of the Hellhound Derby (window.HexGames.derby): hellhounds race on one track, chat bets on the
 * winner by its race number. The server decides everything (derby.py); this draws it:
 *
 *   betting   the lineup: the odds board (number, name, form, multiplier, the money on it), a spotlight on
 *             each runner in turn, "BETS CLOSE IN n" and the bet command
 *   racing    the gate countdown, then the race: the server's script replayed (every hound's speed wobble and
 *             events, stretched to cross the line at its drawn time - the same maths as derby.py simulate /
 *             replay), the camera on the pack, hazards, the crowd's junk, trash talk, the commentary, and a
 *             photo finish when the script has one
 *   result    the results card: the finishing order, the winner's multiplier, who got paid
 *   idle      (when shown) the form book
 *
 * Every frame is a function of the STATE and the time since its phase started (elapsed_ms), and every
 * cosmetic choice comes from the race's seed, so OBS, a late-joining overlay and the panel's mirror all show
 * the same race. Only the particles (fire, slime, sparks) are free to differ.
 *
 * Assets (next to this file): derby_hounds.json (the roster: names, colours, effects, sprite sheets and their
 * anchor points), derby_lines.json (what they say), hounds/<id>.webp (each hound's 16-frame gallop). They
 * are made by art/derby/scripts/export_assets.py.
 *
 * Plain browser script; also loads under Node (module.exports) for the tests.
 */
(function (root) {
  'use strict';

  var HG = root.HexGames = root.HexGames || {};
  var doc = root.document;
  var BASE_W = 1920, BASE_H = 1080;
  var DEFAULTS = {
    x: 50, y: 50, scale: 1, theme: 'inferno', title: 'Hellhound Derby', show_rules: true, show_players: true, players_max: 8,
    sfx: true, sfx_volume: 0.6, show_talk: true, clean_language: false, show_crowd: true,
    hide_when_idle: true, commands_text: '!derby <number> <coins>', field_size: 6, bet_seconds: 22, race_seconds: 45,
    result_seconds: 12, house_edge_pct: 5, currency: 'coins'
  };
  var APPEARANCE = ['x', 'y', 'scale', 'theme', 'title', 'show_rules', 'show_players', 'players_max', 'sfx', 'sfx_volume',
    'show_talk', 'clean_language', 'show_crowd'];
  // the themes: the sky, the lava falls, the stands, the track, the board colours
  var THEMES = {
    inferno: { sky: ['#0b0204', '#2a0705', '#5a1406'], glow: ['rgba(255,110,30,.55)', 'rgba(200,40,10,.25)', 'rgba(80,0,0,0)'], peaks: '#170405',
      fall: [255, 115, 20], stands: '#1c0807', crowd: ['#2c0c0a', '#3a100c'], eyes: 'rgba(255,90,20,.8)', posts: '#3c1410',
      track: ['#3a170e', '#5a2412', '#6e2c14'], lines: 'rgba(255,120,60,.18)', marks: 'rgba(255,170,120,.55)', banner: '#7a0e08',
      accent: '#ff8a3d', hot: '#ff5a1a', gold: '#ffd27a', ink: '#e8d6c8', dim: '#b8a49a', panel: 'rgba(20,6,4,.9)', dark: 'rgba(10,2,2,.7)' },
    abyss: { sky: ['#020409', '#06122a', '#0b2a52'], glow: ['rgba(70,160,255,.5)', 'rgba(20,60,200,.22)', 'rgba(0,10,60,0)'], peaks: '#050a18',
      fall: [70, 170, 255], stands: '#070c1c', crowd: ['#0b1430', '#0f1a3c'], eyes: 'rgba(110,210,255,.85)', posts: '#13203a',
      track: ['#121a2c', '#1a2640', '#22304e'], lines: 'rgba(110,180,255,.18)', marks: 'rgba(170,210,255,.55)', banner: '#0c3a7a',
      accent: '#6ec3ff', hot: '#2a8aff', gold: '#bfe6ff', ink: '#d2deee', dim: '#93a6c0', panel: 'rgba(4,8,20,.9)', dark: 'rgba(2,4,12,.72)' },
    sulfur: { sky: ['#090802', '#2a2205', '#57440a'], glow: ['rgba(255,220,60,.5)', 'rgba(200,160,10,.22)', 'rgba(60,40,0,0)'], peaks: '#141003',
      fall: [255, 215, 40], stands: '#1c1806', crowd: ['#2c260a', '#3a320c'], eyes: 'rgba(255,230,60,.85)', posts: '#3a3210',
      track: ['#3a3210', '#544614', '#6a5818'], lines: 'rgba(255,230,90,.18)', marks: 'rgba(255,235,150,.55)', banner: '#6a5206',
      accent: '#ffd84a', hot: '#e6b800', gold: '#fff0a0', ink: '#ece4c4', dim: '#bdb38f', panel: 'rgba(18,14,3,.9)', dark: 'rgba(10,8,2,.72)' }
  };
  var TH = THEMES.inferno;     // the theme being drawn (set per frame from the config)

  // ---------------------------------------------------------------- little helpers
  function has(o, k) { return Object.prototype.hasOwnProperty.call(o, k); }
  function merge(a, b) { var r = {}, k; for (k in a) if (has(a, k)) r[k] = a[k]; if (b) for (k in b) if (has(b, k) && b[k] != null) r[k] = b[k]; return r; }
  function clamp(v, a, b) { return Math.max(a, Math.min(b, v)); }
  function ease(u) { return u * u * (3 - 2 * u); }
  function now() { return root.performance && root.performance.now ? root.performance.now() : Date.now(); }
  var raf = root.requestAnimationFrame ? root.requestAnimationFrame.bind(root) : function (f) { return setTimeout(function () { f(now()); }, 16); };
  var caf = root.cancelAnimationFrame ? root.cancelAnimationFrame.bind(root) : clearTimeout;
  function fmt(n) { return Math.round(+n || 0).toLocaleString('en-US'); }
  // a seeded random source: every cosmetic choice of a race comes from its seed, so every overlay agrees
  function mulberry(seed) {
    var a = seed | 0;
    return function () {
      a = a + 0x6D2B79F5 | 0;
      var t = Math.imul(a ^ a >>> 15, 1 | a);
      t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t;
      return ((t ^ t >>> 14) >>> 0) / 4294967296;
    };
  }
  function pickR(rng, a) { return a[Math.floor(rng() * a.length)]; }
  function fill(s, o) { return String(s).replace(/\{(\w+)\}/g, function (m, k) { return o && o[k] != null ? o[k] : m; }); }
  // clean_language: what becomes #@$%& (compounds too: dipshit, dumbass, fuckface; never "passed" or "class")
  var SWEAR = /\b\w*(?:fuck|shit)\w*|\b(?:dumb|jack|smart|fat|lard|bad|kiss|kick)?ass(?:holes?|es)?\b|\b(?:bitch|dick|piss|prick|goddamn|bastard|cock)\w*|\bpuss(?:y|ies)\b/gi;

  // ---------------------------------------------------------------- assets
  var BASE_URL = (function () {
    try {
      var s = doc && doc.currentScript;
      if (s && s.src) return s.src.replace(/[^\/]*$/, '').replace(/\?.*$/, '');
    } catch (e) { /* not in a page */ }
    return '/plugins/games_derby/static/';
  })();
  var ASSETS = { state: 'idle', roster: [], byId: {}, lines: {}, waiting: [] };
  function getJson(url, cb) {
    try {
      var x = new root.XMLHttpRequest();
      x.open('GET', url);
      x.onload = function () { var d = null; try { d = JSON.parse(x.responseText); } catch (e) { d = null; } cb(d); };
      x.onerror = function () { cb(null); };
      x.send();
    } catch (e) { cb(null); }
  }
  function loadAssets(cb) {
    if (ASSETS.state === 'ready') { cb(); return; }
    ASSETS.waiting.push(cb);
    if (ASSETS.state === 'loading' || !doc) return;
    ASSETS.state = 'loading';
    var left = 2;
    function done() {
      if (--left) return;
      var imgs = ASSETS.roster.length, k;
      function one() { if (--imgs <= 0) { ASSETS.state = 'ready'; var w = ASSETS.waiting; ASSETS.waiting = []; for (k = 0; k < w.length; k++) w[k](); } }
      if (!imgs) { imgs = 1; one(); return; }
      ASSETS.roster.forEach(function (h) {
        var im = new root.Image();
        im.onload = im.onerror = one;
        im.src = BASE_URL + h.sheet.file;
        h.img = im;
      });
    }
    getJson(BASE_URL + 'derby_hounds.json', function (d) {
      ASSETS.roster = (d && d.hounds) || [];
      ASSETS.roster.forEach(function (h) { ASSETS.byId[h.id] = h; });
      done();
    });
    getJson(BASE_URL + 'derby_lines.json', function (d) { ASSETS.lines = d || {}; done(); });
  }
  function hound(id) { return ASSETS.byId[id] || null; }
  function lines(a, b) { var v = ASSETS.lines[a]; if (b != null) v = v && v[b]; return Array.isArray(v) ? v : []; }
  var UI_COLOUR = { terry: '#d8301c', harry: '#e9dfc6', mange: '#9a7b55' };
  function colourOf(id) { var h = hound(id); return UI_COLOUR[id] || 'hsl(' + ((24 + ((h && h.hue) || 0)) % 360) + ' 95% 58%)'; }

  // ---------------------------------------------------------------- the race maths (= derby.py)
  function curve(spec, u) {
    var k = spec[0], a = spec.length > 1 ? spec[1] : 0, b = spec.length > 2 ? spec[2] : 0;
    if (k === 'tumble') return u < 0.72 ? 0.04 : 0.04 + (u - 0.72) / 0.28 * 0.96;
    if (k === 'hold') return u < a ? b : 1;
    if (k === 'ramp') return a + (1 - a) * u;
    if (k === 'ramp2') return a + (1 - a) * u * u;
    if (k === 'const') return a;
    if (k === 'femur') return u < 0.4 ? -0.85 : u < 0.52 ? 0 : 1.1;
    if (k === 'scorch') return u < 0.15 ? 0.45 : 1.35;
    return 1;
  }
  function simulate(wob, evs, length, V, DT) {
    var a1 = wob[0], a2 = wob[1], w1 = wob[2], w2 = wob[3], p1 = wob[4], p2 = wob[5];
    var xs = [0], x = 0, t = 0, R = null, prev, i, v;
    while (t < 120) {
      t += DT;
      v = V * (1 + a1 * Math.sin(w1 * t + p1) + a2 * Math.sin(w2 * t + p2)) * Math.pow(Math.min(1, t / 1.3), 0.6);
      for (i = 0; i < evs.length; i++) if (evs[i][0] <= t && t < evs[i][0] + evs[i][1]) v *= curve(evs[i][2], (t - evs[i][0]) / evs[i][1]);
      prev = x; x = x + v * DT;
      xs.push(x);
      if (R === null && x >= length) R = t - DT + (length - prev) / (x - prev) * DT;
      if (R !== null && t > R + 10) break;
    }
    return { xs: xs, R: R !== null ? R : t };
  }
  function sample(xs, t, DT) {
    var i = t / DT, j = Math.floor(i);
    if (j < 0) return 0;
    if (j >= xs.length - 1) return xs[xs.length - 1];
    return xs[j] + (xs[j + 1] - xs[j]) * (i - j);
  }
  function replay(script, i) {
    var r = script.runners[i], V = script.v || 700, DT = script.dt || 1 / 60, L = script.length;
    var w0 = simulate(r.wob, [], L, V, DT).R / r.T, evs = [];
    (script.events || []).forEach(function (e) {
      if (e.a === i) evs.push([e.t * w0, e.dur * w0, e.me]);
      if (e.b === i && e.them) evs.push([e.t * w0, e.dur * w0, e.them]);
    });
    var s = simulate(r.wob, evs, L, V, DT);
    return { xs: s.xs, warp: s.R / r.T };
  }

  // ---------------------------------------------------------------- the track
  var ROWS_N = 6, SPRK = 0.95, CX = 800, TRACK_TOP = 600, TRACK_BOT = 1048, NOSE = 318, BODY = [190, 140];
  var ROWS = [];
  for (var ri = 0; ri < ROWS_N; ri++) ROWS.push({ y: 650 + ri * 68, s: (0.70 + ri * 0.07) * SPRK });
  var S_FAR = ROWS[0].s * 0.92, S_NEAR = ROWS[ROWS_N - 1].s * 1.08;
  function sx(x, s, cam) { return CX + (x - cam) * s; }
  var bg = mulberry(9001);
  var PEAKS = [], CROWD = [];
  for (var pi = 0; pi < 40; pi++) PEAKS.push({ x: pi * 160, h: 60 + bg() * 160 });
  for (var ci = 0; ci < 120; ci++) CROWD.push({ x: ci * 55 + (bg() - 0.5) * 20, y: Math.floor(bg() * 3), k: 0.8 + bg() * 0.4, ph: bg() * 6, horn: bg() < 0.7 });
  var JUNK_NAMES = ['BONE', 'SKULL', 'FLAMING TRASH BAG', 'MUG OF BLOOD', 'TOILET', 'ROTTEN EYEBALL', 'SEVERED HAND'];

  /*FX:BEGIN*/
  // ---------------------------------------------------------------- effects (fire prints, slime, lightning, ...)
  // An effect host L (one per hound): parts, hue, pt(key) -> [x, y] of an anchor in the frame shown, speed (how
  // fast the track slides past on screen), dx (how far the hound moved on screen), ground (y), size.
  var FXR = Math.random;
  function rnd(a, b) { return a + FXR() * (b - a); }
  function fire(t, hue, a) { return 'hsla(' + ((48 - 46 * t + hue + 720) % 360) + ',100%,' + (95 - 52 * t) + '%,' + (a == null ? 1 : a) + ')'; }
  function spawn(L, p) {
    if (L.parts.length >= 900) return;
    var k = L.size || 1, q = { age: 0, world: true, vx: 0, vy: 0 }, n;
    for (n in p) if (has(p, n)) q[n] = p[n];
    q.r *= k; q.vx *= k; q.vy *= k;
    L.parts.push(q);
  }
  function flame(L, x, y, o) {
    o = o || {};
    spawn(L, { kind: 'flame', x: x + rnd(-2, 2), y: y + rnd(-2, 2), vx: rnd(-15, 15) + (o.vx || 0), vy: rnd(-110, -55) + (o.vy || 0),
      life: rnd(0.28, 0.55) * (o.life || 1), r: rnd(4, 9) * (o.size || 1), hue: o.hue != null ? o.hue : L.hue });
  }
  function spark(L, x, y) { spawn(L, { kind: 'spark', x: x, y: y, vx: rnd(-25, 25), vy: rnd(-90, -35), life: rnd(0.7, 1.5), r: rnd(1, 2.2), hue: L.hue }); }
  function smoke(L, x, y, tint, o) { spawn(L, { kind: 'smoke', x: x, y: y, vx: rnd(-10, 10), vy: rnd(-35, -15), life: rnd(0.8, 1.4), r: rnd(4, 7) * ((o && o.size) || 1), tint: tint }); }
  function print(L, x, y) { spawn(L, { kind: 'print', x: x, y: y + 2, life: 1.3, r: 6, hue: L.hue }); }
  function dust(L, x, y, o) { spawn(L, { kind: 'smoke', x: x + rnd(-4, 4), y: y, vx: rnd(-30, 10), vy: rnd(-25, -8), life: rnd(0.5, 0.9), r: rnd(3, 5) * ((o && o.size) || 1), tint: (o && o.tint) || '120,90,70' }); }
  function bolt(L, a, b, life, jag, width) {
    var pts = [], n = 7, k = L.size || 1, i, f, j;
    for (i = 0; i <= n; i++) { f = i / n; j = (i === 0 || i === n) ? 0 : jag * k; pts.push([a[0] + (b[0] - a[0]) * f + rnd(-j, j), a[1] + (b[1] - a[1]) * f + rnd(-j, j)]); }
    L.parts.push({ kind: 'bolt', age: 0, x: 0, y: 0, vx: 0, vy: 0, r: 1, life: life, pts: pts, width: width * k, world: true });
  }
  function along(L, a, b) { var p = L.pt(a), q = L.pt(b), k = FXR(); return [p[0] + (q[0] - p[0]) * k, p[1] + (q[1] - p[1]) * k]; }
  var FX = {
    paw_fire: { step: function (L, downs) { downs.forEach(function (p) { for (var i = 0; i < 8; i++) flame(L, p[0], p[1], { size: 0.8 }); print(L, p[0], p[1]); }); } },
    dust: { step: function (L, downs) { downs.forEach(function (p) { for (var i = 0; i < 4; i++) dust(L, p[0], p[1]); }); } },
    embers: { tick: function (L, dt) { if (FXR() < 22 * dt) { var p = along(L, 'back', 'hips'); spark(L, p[0], p[1]); } } },
    // the tail itself burns: tongues of flame lick up along it and ride with the hound
    tail_flame: { tick: function (L, dt) {
      var pts = [L.pt('tail_base'), L.pt('tail_mid'), L.pt('tail')];
      for (var n = Math.round(rnd(0, 1.5) + 70 * dt); n > 0; n--) {
        var k = FXR() * 2, i = Math.min(1, Math.floor(k)), f = k - i, a = pts[i], b = pts[i + 1];
        spawn(L, { kind: 'flame', world: false, x: a[0] + (b[0] - a[0]) * f + rnd(-2, 2), y: a[1] + (b[1] - a[1]) * f - rnd(0, 3),
          vx: -L.speed * 0.18 / (L.size || 1) + rnd(-10, 10), vy: rnd(-70, -35), life: rnd(0.18, 0.34), r: rnd(3, 6) * (1.15 - 0.2 * k), hue: L.hue });
      } } },
    sulfur: {
      step: function (L, downs) { downs.forEach(function (p) {
        for (var i = 0; i < 5; i++) spawn(L, { kind: 'grit', x: p[0] + rnd(-3, 3), y: p[1], vx: rnd(-70, 30), vy: rnd(-150, -60), life: rnd(0.4, 0.8), r: rnd(1, 1.8), hue: L.hue });
        smoke(L, p[0], p[1] - 2, '210,200,70'); }); },
      tick: function (L, dt) { if (FXR() < 7 * dt) { var a = L.pt('back'), b = L.pt('head'), k = FXR() * 0.7;
        spawn(L, { kind: 'wisp', world: false, x: a[0] + (b[0] - a[0]) * k + rnd(-4, 4), y: a[1] + (b[1] - a[1]) * k - 4, vx: -L.speed * 0.15 / (L.size || 1), vy: rnd(-30, -18), life: rnd(0.7, 1.1), r: rnd(2, 3.5) }); } } },
    drool: { tick: function (L, dt) {
      L.cool = (L.cool || 0) - dt;
      if (L.cool <= 0) { L.cool = rnd(0.25, 0.7); var m = L.pt('mouth'); spawn(L, { kind: 'drop', x: m[0], y: m[1] + 3, vx: rnd(-20, 10), vy: rnd(0, 40), life: 3, r: rnd(1.6, 2.6), hue: L.hue }); } } },
    flies: { tick: function (L, dt) {
      if (!L.flies) { L.flies = []; for (var i = 0; i < 5; i++) L.flies.push({ a: rnd(0, 6.28), w: rnd(5, 9) * (FXR() < 0.5 ? -1 : 1), rx: rnd(22, 40), ry: rnd(10, 22), j: rnd(0, 6) }); }
      L.flies.forEach(function (f) { f.a += f.w * dt; }); } },
    slime: {
      step: function (L, downs) { downs.forEach(function (p) { spawn(L, { kind: 'puddle', x: p[0] + rnd(-3, 3), y: p[1] + 2, life: 2.4, r: rnd(7, 10) });
        for (var i = 0; i < 2; i++) spawn(L, { kind: 'drip', x: p[0], y: p[1], vx: rnd(-50, 30), vy: rnd(-120, -60), life: 2, r: rnd(1.5, 2.5) }); }); },
      tick: function (L, dt) {
        L.cool = (L.cool || 0) - dt;
        if (L.cool <= 0) { L.cool = rnd(0.2, 0.5); var m = L.pt('mouth'); spawn(L, { kind: 'drip', x: m[0], y: m[1] + 2, vx: rnd(-15, 10), vy: rnd(0, 30), life: 3, r: rnd(1.8, 3) }); }
        L.parts.forEach(function (p) { if (p.kind === 'puddle' && FXR() < 1.2 * dt)
          L.parts.push({ kind: 'bubble', age: 0, world: true, x: p.x + rnd(-p.r * 0.6, p.r * 0.6), y: p.y - 1, vx: 0, vy: rnd(-22, -10) * (L.size || 1), life: rnd(0.4, 0.8), r: rnd(1.2, 2.6) * (L.size || 1) }); }); } },
    // lightning: a crackling print where each paw lands, and a faint charge along the ground behind
    lightning: {
      step: function (L, downs) { downs.forEach(function (p) {
        spawn(L, { kind: 'eprint', x: p[0], y: p[1] + 2, life: 1.4, r: rnd(6, 8), seed: FXR() });
        for (var i = 0; i < 3; i++) spawn(L, { kind: 'zap', x: p[0], y: p[1], vx: rnd(-60, 60), vy: rnd(-70, -20), life: rnd(0.12, 0.25), r: 1.2 }); }); },
      tick: function (L, dt) {
        L.cool = (L.cool || 0) - dt;
        if (L.cool <= 0) { L.cool = rnd(0.05, 0.12);
          var a = L.pt('RH'), b = L.pt('LF'), k = L.size || 1, y = Math.max(a[1], b[1]) + 2, x = Math.min(a[0], b[0]) - rnd(0, 30) * k;
          bolt(L, [x, y], [x - rnd(20, 45) * k, y + rnd(-1, 1)], rnd(0.1, 0.2), 3, 2.5); } } },
    frost: {
      step: function (L, downs) { downs.forEach(function (p) { spawn(L, { kind: 'frost', x: p[0], y: p[1] + 2, life: 2.6, r: rnd(6, 8), rot: rnd(0, 1) });
        for (var i = 0; i < 4; i++) spawn(L, { kind: 'shard', x: p[0], y: p[1], vx: rnd(-80, 50), vy: rnd(-140, -50), life: rnd(0.4, 0.8), r: rnd(1.5, 3), rot: rnd(0, 6) }); }); },
      tick: function (L, dt) { if (FXR() < 30 * dt) { var p = along(L, 'back', 'tail');
        spawn(L, { kind: 'flake', x: p[0] + rnd(-4, 4), y: p[1] + rnd(-6, 6), vx: rnd(-20, 20), vy: rnd(-8, 25), life: rnd(0.8, 1.6), r: rnd(0.8, 1.8) }); } } },
    soul_fire: { tick: function (L, dt) {
      var e = L.pt('eye'), n;
      for (n = Math.round(rnd(0, 1) + 45 * dt); n > 0; n--) flame(L, e[0], e[1], { size: 0.7, hue: 150 });
      if (FXR() < 25 * dt) { var p = along(L, 'back', 'hips'); flame(L, p[0], p[1], { size: 0.8, hue: 150 }); } } }
  };
  function drawFX(L, c, dt) {
    var parts = L.parts, G = L.ground, dx = L.dx || 0, drift = (L.speed || 0) * dt, i, p, t, a, k, s, ang, len, q, g, r;
    for (i = parts.length - 1; i >= 0; i--) {
      p = parts[i]; p.age += dt;
      if (p.age >= p.life) { parts.splice(i, 1); continue; }
      if (p.kind === 'drop' || p.kind === 'drip' || p.kind === 'shard' || p.kind === 'zap' || p.kind === 'grit') {
        p.vy += (p.kind === 'zap' ? 300 : p.kind === 'drop' ? 900 : 800) * dt * (L.size || 1);
        if (p.y + p.vy * dt >= G) {
          parts.splice(i, 1);
          if (p.kind === 'drip') spawn(L, { kind: 'puddle', x: p.x, y: G, life: 1.6, r: rnd(3, 5) });
          if (p.kind === 'drop') { for (k = 0; k < 3; k++) spawn(L, { kind: 'spark', x: p.x, y: G, vx: rnd(-40, 40), vy: rnd(-80, -30), life: 0.4, r: 1.2, hue: p.hue }); print(L, p.x, G - 2); }
          continue;
        }
      }
      if (p.kind === 'bolt') { for (k = 0; k < p.pts.length; k++) p.pts[k][0] += p.world ? -drift : dx; }
      else { p.x += p.vx * dt + (p.world ? -drift : dx); p.y += p.vy * dt; }
      t = p.age / p.life;
      switch (p.kind) {
        case 'puddle':
          c.globalCompositeOperation = 'source-over'; a = Math.min(1, (1 - t) * 1.6);
          c.fillStyle = 'rgba(70,200,40,' + 0.75 * a + ')'; c.beginPath(); c.ellipse(p.x, p.y, p.r, p.r * 0.32, 0, 0, 6.283); c.fill();
          c.globalCompositeOperation = 'lighter'; c.fillStyle = 'rgba(140,255,90,' + 0.45 * a + ')';
          c.beginPath(); c.ellipse(p.x - p.r * 0.25, p.y - p.r * 0.08, p.r * 0.35, p.r * 0.1, 0, 0, 6.283); c.fill();
          break;
        case 'drip':
          c.globalCompositeOperation = 'lighter'; c.fillStyle = 'rgba(120,255,70,.9)';
          c.beginPath(); c.ellipse(p.x, p.y, p.r * 0.8, p.r * (1.2 + Math.min(1.5, p.vy / 300)), 0, 0, 6.283); c.fill();
          break;
        case 'bubble':
          c.globalCompositeOperation = 'lighter'; c.strokeStyle = 'rgba(160,255,110,' + 0.8 * (1 - t) + ')'; c.lineWidth = 1;
          c.beginPath(); c.arc(p.x, p.y, p.r * (1 + t * 0.6), 0, 6.283); c.stroke();
          break;
        case 'bolt': {
          c.globalCompositeOperation = 'lighter';
          var flick = FXR() < 0.25 ? 0.3 : 1, fade = 1 - t * 0.6, W = p.width || 6, layers = [[W, 'rgba(70,160,255,' + 0.25 * flick * fade + ')'], [W * 0.4, 'rgba(140,210,255,' + 0.7 * flick * fade + ')'], [Math.max(0.8, W / 6), 'rgba(255,255,255,' + flick * fade + ')']];
          for (k = 0; k < 3; k++) {
            c.strokeStyle = layers[k][1]; c.lineWidth = layers[k][0]; c.lineJoin = 'round'; c.beginPath();
            for (q = 0; q < p.pts.length; q++) { if (q) c.lineTo(p.pts[q][0], p.pts[q][1]); else c.moveTo(p.pts[q][0], p.pts[q][1]); }
            c.stroke();
          }
          break;
        }
        case 'zap': c.globalCompositeOperation = 'lighter'; c.fillStyle = 'rgba(190,230,255,' + (1 - t) + ')'; c.fillRect(p.x - 1, p.y - 1, 2.2, 2.2); break;
        case 'eprint':
          c.globalCompositeOperation = 'lighter'; a = (1 - t) * (FXR() < 0.2 ? 0.35 : 1);
          c.fillStyle = 'rgba(90,170,255,' + 0.45 * a + ')'; c.beginPath(); c.ellipse(p.x, p.y, p.r, p.r * 0.3, 0, 0, 6.283); c.fill();
          c.strokeStyle = 'rgba(200,235,255,' + a + ')'; c.lineWidth = 1; c.beginPath();
          for (k = 0; k < 5; k++) {
            ang = p.seed * 6.283 + k * 1.2566; len = p.r * (1.1 + 0.5 * Math.sin(p.seed * 50 + k)); c.moveTo(p.x, p.y);
            for (s = 1; s <= 3; s++) c.lineTo(p.x + Math.cos(ang) * len * s / 3 + rnd(-1.5, 1.5), p.y + Math.sin(ang) * len * 0.35 * s / 3 + rnd(-1, 1));
          }
          c.stroke();
          break;
        case 'grit': c.globalCompositeOperation = 'lighter'; c.fillStyle = fire(0.15 + t * 0.5, p.hue, 1 - t * 0.7); c.fillRect(p.x - p.r / 2, p.y - p.r / 2, p.r, p.r); break;
        case 'wisp':
          c.globalCompositeOperation = 'source-over'; c.fillStyle = 'rgba(205,195,95,' + 0.16 * Math.sin(Math.PI * t) + ')';
          c.beginPath(); c.ellipse(p.x, p.y, p.r * (1 + t), p.r * (1.6 + t * 2), 0, 0, 6.283); c.fill();
          break;
        case 'frost':
          c.globalCompositeOperation = 'lighter'; a = (1 - t) * 0.8;
          c.fillStyle = 'rgba(150,210,255,' + a * 0.5 + ')'; c.beginPath(); c.ellipse(p.x, p.y, p.r, p.r * 0.3, 0, 0, 6.283); c.fill();
          c.strokeStyle = 'rgba(220,240,255,' + a + ')'; c.lineWidth = 1; c.beginPath();
          for (k = 0; k < 6; k++) { ang = p.rot + k * 1.047; c.moveTo(p.x, p.y); c.lineTo(p.x + Math.cos(ang) * p.r * 1.3, p.y + Math.sin(ang) * p.r * 0.45); }
          c.stroke();
          break;
        case 'shard':
          c.globalCompositeOperation = 'lighter'; c.fillStyle = 'rgba(200,235,255,' + (1 - t) + ')';
          p.rot += 12 * dt; c.save(); c.translate(p.x, p.y); c.rotate(p.rot);
          c.beginPath(); c.moveTo(0, -p.r * 1.6); c.lineTo(p.r * 0.6, 0); c.lineTo(0, p.r * 1.6); c.lineTo(-p.r * 0.6, 0); c.fill(); c.restore();
          break;
        case 'flake': c.globalCompositeOperation = 'lighter'; c.fillStyle = 'rgba(230,245,255,' + 0.8 * (1 - t) + ')'; c.beginPath(); c.arc(p.x, p.y, p.r, 0, 6.283); c.fill(); break;
        case 'flame':
          p.vx *= 0.97; r = p.r * (1 - t * 0.6);
          c.globalCompositeOperation = 'lighter';
          g = c.createRadialGradient(p.x, p.y, 0, p.x, p.y, r);
          g.addColorStop(0, fire(t, p.hue, 0.9 * (1 - t))); g.addColorStop(1, fire(Math.min(1, t + 0.3), p.hue, 0));
          c.fillStyle = g; c.beginPath(); c.arc(p.x, p.y, r, 0, 6.283); c.fill();
          break;
        case 'spark':
          c.globalCompositeOperation = 'lighter'; p.vx += rnd(-80, 80) * dt;
          c.fillStyle = fire(t * 0.7, p.hue, (1 - t) * (0.6 + 0.4 * FXR())); c.beginPath(); c.arc(p.x, p.y, p.r, 0, 6.283); c.fill();
          break;
        case 'print':
          c.globalCompositeOperation = 'lighter'; c.fillStyle = fire(0.35 + t * 0.6, p.hue, 0.7 * (1 - t));
          c.beginPath(); c.ellipse(p.x, p.y, p.r, p.r * 0.35, 0, 0, 6.283); c.fill();
          break;
        case 'smoke':
          c.globalCompositeOperation = 'source-over'; c.fillStyle = 'rgba(' + p.tint + ',' + 0.35 * (1 - t) + ')';
          c.beginPath(); c.arc(p.x, p.y, p.r * (1 + t * 2.2), 0, 6.283); c.fill();
          break;
        case 'drop':
          c.globalCompositeOperation = 'lighter'; c.fillStyle = fire(0.25, p.hue, 0.9);
          c.beginPath(); c.ellipse(p.x, p.y, p.r * 0.8, p.r * 1.5, 0, 0, 6.283); c.fill();
          break;
      }
    }
    c.globalCompositeOperation = 'source-over';
    if (L.flies) {
      var b = L.pt('back'), kk = L.size || 1;
      L.flies.forEach(function (f) {
        var x = b[0] + (Math.cos(f.a) * f.rx + Math.sin(f.a * 2.7 + f.j) * 4) * kk, y = b[1] + (-14 + Math.sin(f.a) * f.ry) * kk;
        c.fillStyle = 'rgba(20,16,12,.95)'; c.beginPath(); c.arc(x, y, 1.8 * kk, 0, 6.283); c.fill();
        c.fillStyle = 'rgba(200,200,220,.35)'; c.beginPath(); c.ellipse(x - kk, y - 2 * kk, 2 * kk, kk, -0.5, 0, 6.283); c.fill();
      });
    }
  }
  /*FX:END*/

  // ---------------------------------------------------------------- one race, ready to play
  // Built once per script: every runner's run (replayed), the hazards and the crowd's junk, who says what
  // and when, the commentary. Nothing here depends on when you look: it is all a function of the script.
  function buildRace(g) {
    var S = g.script, D = S.length, rng = mulberry((S.seed | 0) ^ 0x5bd1e995), field = g.field || [];
    var R = { id: g.id + '/' + S.seed, S: S, D: D, gate: (S.gate_ms || 3200) / 1000, photoS: (S.photo_ms || 4600) / 1000,
      runners: [], events: S.events || [], hazards: [], lobs: [], talk: [], ticker: [], photo: S.photo || null, cache: null };
    S.runners.forEach(function (sr, i) {
      var f = field[i] || {}, h = hound(sr.id) || { id: sr.id, name: f.name || sr.id, short: f.short || f.name || sr.id, sheet: null, anchors: null, fx: [] };
      var rep = replay(S, i), row = f.row != null ? f.row : i, Rw = ROWS[row % ROWS_N];
      R.runners.push({ i: i, num: sr.num, id: sr.id, T: sr.T, xs: rep.xs, warp: rep.warp, h: h, A: h.anchors, row: row, y: Rw.y, s: Rw.s,
        name: f.name || h.name, short: f.short || h.short || h.name, streak: f.streak || 0, colour: colourOf(sr.id), hue: h.hue || 0,
        sat: h.sat != null ? h.sat : 1.2, off: rng(), fx: (h.fx || []).map(function (n) { return FX[n]; }).filter(Boolean),
        parts: [], size: Rw.s / (240 / 360), speed: 0, dx: 0, ground: Rw.y, left: 0, top: 0, f: 0, prevF: 0, shown: false,
        x: 0, ox: 0, oy: 0, rot: 0, flip: false, frozen: false });
    });
    var DT = S.dt || 1 / 60, rr = R.runners;
    function xAt(r, t) { return sample(r.xs, Math.max(0, t) * r.warp, DT); }
    R.xAt = xAt;
    var order = S.order || [], winT = order.length ? rr[order[0]].T : Math.max.apply(null, rr.map(function (r) { return r.T; }));
    var lastT = Math.max.apply(null, rr.map(function (r) { return r.T; }));
    R.winT = winT; R.lastT = lastT;
    // hazards and the scripted junk (the thrown arcs from the seed)
    R.events.forEach(function (e) {
      var a = rr[e.a];
      if (e.hazard) R.hazards.push({ kind: e.hazard, x: xAt(a, e.t) + e.at, row: a.row, t: e.t, dur: e.dur });
      if (e.thrown) {
        var Rw = ROWS[a.row % ROWS_N], fly = e.thrown === 'hit' ? 0.9 : 1.1, tl = e.thrown === 'hit' ? e.t : e.t - 0.7, t0 = tl - fly;
        R.hazards.push({ kind: 'thrown', mode: e.thrown, obj: e.obj, t0: t0, tl: tl, t: e.t, dur: e.dur, row: a.row, eats: e.type === 'junkfood',
          x0: xAt(a, t0) + (rng() * 1000 - 300), x1: e.thrown === 'hit' ? xAt(a, e.t) - 170 : xAt(a, e.t) + (e.at || 70),
          y1: e.thrown === 'hit' ? Rw.y - 120 * Rw.s : Rw.y - 8 * Rw.s, spin: (6 + rng() * 8) * (rng() < 0.5 ? -1 : 1) });
      }
    });
    // the crowd lobs junk all race long; most of it misses (dressing)
    for (var lt = 1 + rng(); lt < winT - 1; lt += 1 + rng() * 1.2) {
      var live = rr.filter(function (r) { var x = xAt(r, lt); return x > 400 && x < D - 1500; });
      if (!live.length) continue;
      var lr = live[Math.floor(rng() * live.length)], lfly = 0.9 + rng() * 0.4, lx = xAt(lr, lt);
      R.lobs.push({ obj: pickR(rng, JUNK_NAMES), t0: lt, tl: lt + lfly, row: lr.row, x0: lx + rng() * 1000 - 300,
        x1: xAt(lr, lt + lfly) + rng() * 800 - 380, spin: (6 + rng() * 8) * (rng() < 0.5 ? -1 : 1), near: lr, says: rng() < 0.25 ? pickR(rng, lines('react', 'miss').length ? lines('react', 'miss') : ['HA! MISSED!']) : null });
    }
    // who says what: the events' own lines (picked by the server), trash talk when one passes another, the
    // finish; never more than three bubbles up at once
    var talk = [];
    R.events.forEach(function (e) {
      if (e.lines) e.lines.forEach(function (l, k) { talk.push({ t: e.t + k * 0.9, r: rr[e.a], text: l }); });
      else if (e.line) talk.push({ t: e.t + (e.by === 'b' ? 0.3 : 0.15), r: rr[e.by === 'b' ? e.b : e.a], text: e.line });
      if (e.ticker) R.ticker.push({ t: e.t, text: e.ticker });
    });
    R.lobs.forEach(function (j) { if (j.says && Math.abs(j.x1 - xAt(j.near, j.tl)) < 320) talk.push({ t: j.tl + 0.1, r: j.near, text: j.says }); });
    var nextTalk = 4, prev = rr.map(function (r) { return xAt(r, 3); });
    for (var pt = 3.05; pt < winT; pt += 0.05) {
      var cur = rr.map(function (r) { return xAt(r, pt); });
      for (var a1 = 0; a1 < rr.length && pt >= nextTalk; a1++) for (var b1 = 0; b1 < rr.length; b1++) {
        if (a1 === b1 || !(prev[a1] < prev[b1] && cur[a1] >= cur[b1])) continue;
        nextTalk = pt + 3.5 + rng() * 2.5;
        talk.push({ t: pt, r: rr[a1], text: taunt(rng, rr[a1], rr[b1]), pass: true });
        if (rng() < 0.35) talk.push({ t: pt + 1.6, r: rr[b1], text: fill(pickR(rng, lines('retort').length ? lines('retort') : ['Fuck you!']), { t: rr[a1].short }) });
        break;
      }
      prev = cur;
    }
    var P = R.photo, said = S.talk || {}, resume = P ? P.at : 0;
    if (P && P.kind === 'win') { talk.push({ t: resume + 0.2, r: rr[P.a], text: said.photo_win || 'BY A NOSE!' }); talk.push({ t: resume + 1.2, r: rr[P.b], text: said.photo_lost || 'NO!' }); }
    else if (order.length) talk.push({ t: winT + 0.3, r: rr[order[0]], text: said.win || 'WINNER!' });
    if (P && P.kind === 'last') talk.push({ t: lastT + 0.3, r: rr[P.a], text: said.photo_lost || 'NO!' });
    else if (order.length > 1) talk.push({ t: lastT + 0.3, r: rr[order[order.length - 1]], text: said.last || '...' });
    talk.sort(function (x, y) { return x.t - y.t; });
    talk.forEach(function (b) {
      var up = R.talk.filter(function (q) { return b.t < q.t + 2.8 && b.t >= q.t; }).length;
      if (up < 3) R.talk.push({ t: b.t, r: b.r, text: b.text, life: 2.8, x: null, y: null, pass: !!b.pass });
    });
    var all = rr.slice().sort(function (x, y) { return x.num - y.num; }).map(function (r) { return r.name; }).join(' · ');
    R.ticker.push({ t: -R.gate, text: fill(pickR(rng, lines('ticker', 'start').length ? lines('ticker', 'start') : ['THEY\'RE IN THE GATE: {all}']), { all: all }).toUpperCase() });
    if (order.length) {
      R.ticker.push({ t: winT, text: fill(pickR(rng, lines('ticker', 'win').length ? lines('ticker', 'win') : ['{a} WINS!']), { a: rr[order[0]].name }).toUpperCase() });
      R.ticker.push({ t: lastT, text: fill(pickR(rng, lines('ticker', 'last').length ? lines('ticker', 'last') : ['{a} IS DEAD LAST.']), { a: rr[order[order.length - 1]].name }).toUpperCase() });
    }
    R.ticker.sort(function (x, y) { return x.t - y.t; });
    R.lava = { parts: [], hue: 0, size: 1, speed: 0, dx: 0, ground: TRACK_BOT, pt: function () { return [0, 0]; } };
    return R;
  }
  function taunt(rng, a, b) {
    var pools = [[lines('trash'), 6], [lines('at', b.id), 3], [lines('by', a.id), 2],
      [a.streak >= 2 ? lines('hot') : [], 4], [b.streak >= 2 ? lines('at_hot') : [], 4], [b.streak <= -4 ? lines('at_cold') : [], 4]]
      .filter(function (p) { return p[0].length; });
    if (!pools.length) return 'Move it!';
    var x = rng() * pools.reduce(function (n, p) { return n + p[1]; }, 0), k, pool = pools[0][0];
    for (k = 0; k < pools.length; k++) if ((x -= pools[k][1]) <= 0) { pool = pools[k][0]; break; }
    var n = pool === lines('hot') ? a.streak : Math.abs(b.streak);
    return fill(pickR(rng, pool), { t: b.short, n: n });
  }

  // where everything is at race time t (seconds after the gate): positions, poses, frames
  function raceAt(R, t) {
    var rr = R.runners, NF;
    rr.forEach(function (r) {
      r.lastX = r.x; r.x = R.xAt(r, t);
      pose(R, r, t);
      NF = r.h.sheet ? r.h.sheet.frames : 16;
      var ph, stride = r.A ? r.A.stride : 245;   // (a hound the art roster doesn't know: no sprite, still a runner)
      if (r.hold != null) ph = R.xAt(r, r.hold) / stride;
      else if (r.back != null) ph = (R.xAt(r, r.back) + Math.abs(r.x - R.xAt(r, r.back))) / stride;
      else ph = r.x / stride;
      ph += r.off;
      r.prevF = r.f;
      r.f = Math.floor((((ph % 1) + 1) % 1) * NF) % NF;
    });
    var sorted = rr.slice().sort(function (a, b) { return b.x - a.x; });
    sorted.forEach(function (r, i) { r.now = i + 1; });
    R.cam = camAt(R, t);
  }
  function camTarget(R, t) {
    var xs = R.runners.map(function (r) { return R.xAt(r, t); }), front = Math.max.apply(null, xs), mean = xs.reduce(function (a, b) { return a + b; }, 0) / xs.length;
    return clamp(0.6 * front + 0.4 * mean - 250, 0, R.D + 200);
  }
  function camAt(R, t) {   // a smooth camera that is still a function of t: the target averaged over the last 0.8 s
    var s = 0, k;
    for (k = 0; k < 5; k++) s += camTarget(R, Math.max(0, t - k * 0.2));
    return s / 5;
  }

  // a hound's pose in its events: offsets, turns, a held frame (the tumbles are somersaults)
  function pose(R, r, t) {
    r.ox = 0; r.oy = 0; r.rot = 0; r.flip = false; r.hold = null; r.back = null;
    var s = r.s;
    R.events.forEach(function (e) {
      if (t < e.t || t > e.t + e.dur) return;
      var u = (t - e.t) / e.dur, k;
      if (e.a === r.i) {
        switch (e.type) {
          case 'trip': case 'junk':
            r.hold = e.t;
            if (u < 0.55) { k = ease(u / 0.55); r.rot = k * 6.283; r.oy = -80 * s * Math.sin(Math.PI * u / 0.55); }
            else if (u < 0.75) { r.rot = 6.283 + 0.25; r.oy = 10 * s; }
            break;
          case 'slime': r.hold = e.t; r.rot = 0.4 * Math.sin(u * 20) * (1 - u); r.ox = 30 * s * u; break;
          case 'leap': r.oy = -110 * s * Math.sin(Math.PI * u); break;
          case 'scorch': if (u < 0.92) { r.oy = -75 * s * Math.abs(Math.sin(t * 12)); r.rot = 0.14 + 0.08 * Math.sin(t * 24); } break;
          case 'bonk': r.oy = -20 * s * Math.sin(Math.PI * Math.min(1, u / 0.3)); r.rot = 0.22 * Math.sin(t * 22) * (1 - u); break;
          case 'junkfood': case 'snack': r.hold = e.t; r.oy = 8 * s * Math.abs(Math.sin(t * 14)); r.rot = 0.08; break;
          case 'hand': if (u < 0.8) { r.hold = e.t; r.ox = Math.sin(t * 60) * 3 * s; r.rot = 0.06 * Math.sin(t * 40); } break;
          case 'bite': case 'snap': r.ox = 70 * s * Math.sin(Math.PI * Math.min(1, u / 0.45)); break;
          case 'femur': if (u < 0.42) { r.flip = true; r.back = e.t; } break;
          case 'fleas': if (u < 0.8) { r.hold = e.t; r.rot = 0.1 * Math.sin(t * 50); r.oy = 4 * s * Math.sin(t * 37); } break;
          case 'argue': r.ox = 6 * s * Math.sin(t * 30); break;
          case 'yap': r.oy = -14 * s * Math.abs(Math.sin(t * 18)); break;
          case 'showoff': r.oy = -30 * s * Math.sin(Math.PI * u); r.rot = -0.08 * Math.sin(Math.PI * u); break;
        }
      }
      if (e.b === r.i) {
        switch (e.type) {
          case 'bite': case 'snap': k = Math.sin(Math.PI * Math.min(1, u / 0.4)); r.oy = -45 * s * k; r.rot = -0.2 * k; break;
          case 'kick': case 'flare': r.rot = 0.07 * Math.sin(t * 45) * (1 - u); break;
          case 'zap': if (u < 0.5) { r.hold = e.t; r.rot = 0.25 * Math.sin(t * 70); r.oy = -12 * s * Math.abs(Math.sin(t * 50)); } break;
        }
      }
    });
  }

  // ---------------------------------------------------------------- drawing: the scene
  function drawBackdrop(c, R, cam, t) {
    var D = R ? R.D : 31000, g = c.createLinearGradient(0, 0, 0, TRACK_TOP), i, rep, p, x, y, k, a, b, n, j;
    g.addColorStop(0, TH.sky[0]); g.addColorStop(0.55, TH.sky[1]); g.addColorStop(1, TH.sky[2]);
    c.fillStyle = g; c.fillRect(0, 0, BASE_W, TRACK_TOP + 2);
    var glow = c.createRadialGradient(BASE_W * 0.55, 470, 40, BASE_W * 0.55, 470, 900);
    glow.addColorStop(0, TH.glow[0]); glow.addColorStop(0.4, TH.glow[1]); glow.addColorStop(1, TH.glow[2]);
    c.fillStyle = glow; c.fillRect(0, 0, BASE_W, TRACK_TOP);
    var off = -(cam * 0.05) % 6400;
    c.fillStyle = TH.peaks; c.beginPath(); c.moveTo(0, 450);
    for (rep = -1; rep < 2; rep++) for (i = 0; i < PEAKS.length; i++) c.lineTo(off + rep * 6400 + PEAKS[i].x, 450 - PEAKS[i].h);
    c.lineTo(BASE_W, 450); c.lineTo(BASE_W, 500); c.lineTo(0, 500); c.fill();
    c.globalCompositeOperation = 'lighter';
    for (rep = -1; rep < 2; rep++) for (i = 3; i < 40; i += 7) {
      p = PEAKS[i]; x = off + rep * 6400 + p.x;
      if (x < -50 || x > BASE_W + 50) continue;
      var lg = c.createLinearGradient(0, 450 - p.h, 0, 470);
      var fl = TH.fall, fk = 0.85 + 0.15 * Math.sin(t * 3 + i);
      lg.addColorStop(0, 'rgba(' + fl[0] + ',' + fl[1] + ',' + fl[2] + ',0)');
      lg.addColorStop(0.3, 'rgba(' + Math.round(fl[0] * fk) + ',' + Math.round(fl[1] * fk) + ',' + Math.round(fl[2] * fk) + ',.7)');
      lg.addColorStop(1, 'rgba(' + fl[0] + ',' + Math.round(fl[1] / 2) + ',' + Math.round(fl[2] / 2) + ',.2)');
      c.fillStyle = lg; c.fillRect(x - 4, 450 - p.h + 10, 8, p.h + 20);
    }
    c.globalCompositeOperation = 'source-over';
    var so = -(cam * 0.35) % 6600;
    c.fillStyle = TH.stands; c.fillRect(0, 490, BASE_W, 112);
    for (rep = -1; rep < 2; rep++) for (i = 0; i < CROWD.length; i++) {
      var cr = CROWD[i]; x = so + rep * 6600 + cr.x;
      if (x < -40 || x > BASE_W + 40) continue;
      y = 528 + cr.y * 22 + Math.sin(t * 6 + cr.ph) * 4; k = cr.k;
      c.fillStyle = cr.y === 0 ? TH.crowd[0] : TH.crowd[1];
      c.beginPath(); c.arc(x, y, 12 * k, 0, 6.283); c.fill(); c.fillRect(x - 14 * k, y + 8 * k, 28 * k, 30);
      if (cr.horn) { c.beginPath(); c.moveTo(x - 9 * k, y - 6 * k); c.lineTo(x - 14 * k, y - 22 * k); c.lineTo(x - 4 * k, y - 9 * k);
        c.moveTo(x + 9 * k, y - 6 * k); c.lineTo(x + 14 * k, y - 22 * k); c.lineTo(x + 4 * k, y - 9 * k); c.fill(); }
      c.fillStyle = TH.eyes; c.fillRect(x - 5 * k, y - 2 * k, 3, 2); c.fillRect(x + 2 * k, y - 2 * k, 3, 2);
    }
    var tg = c.createLinearGradient(0, TRACK_TOP, 0, TRACK_BOT);
    tg.addColorStop(0, TH.track[0]); tg.addColorStop(0.6, TH.track[1]); tg.addColorStop(1, TH.track[2]);
    c.fillStyle = tg; c.fillRect(0, TRACK_TOP, BASE_W, BASE_H - TRACK_TOP);
    c.fillStyle = '#0e0404'; c.fillRect(0, TRACK_TOP - 8, BASE_W, 8);
    for (x = Math.floor((cam - 2000) / 300) * 300; x < cam + 3000; x += 300) {
      var px = sx(x, S_FAR, cam);
      if (px > -20 && px < BASE_W + 20) { c.fillStyle = TH.posts; c.fillRect(px - 3, TRACK_TOP - 34, 6, 30); }
    }
    c.lineWidth = 2;
    for (x = Math.floor((cam - 2500) / 1000) * 1000; x < cam + 3500; x += 1000) {
      if (x <= 0 || x >= D) continue;
      a = sx(x, S_FAR, cam); b = sx(x, S_NEAR, cam);
      if (Math.max(a, b) < -50 || Math.min(a, b) > BASE_W + 50) continue;
      c.strokeStyle = TH.lines; c.beginPath(); c.moveTo(a, TRACK_TOP); c.lineTo(b, TRACK_BOT); c.stroke();
      c.fillStyle = TH.marks; c.font = 'bold 22px Impact, sans-serif'; c.textAlign = 'center';
      c.fillText(Math.round((D - x) / 100) + ' m', a, TRACK_TOP - 44);
    }
    [[0, 'start'], [D, 'finish']].forEach(function (m) {
      var la = sx(m[0], S_FAR, cam), lb = sx(m[0], S_NEAR, cam), q;
      if (Math.max(la, lb) < -200 || Math.min(la, lb) > BASE_W + 200) return;
      n = 14;
      for (q = 0; q < n; q++) for (j = 0; j < 2; j++) {
        var y0 = TRACK_TOP + (TRACK_BOT - TRACK_TOP) * q / n, y1 = TRACK_TOP + (TRACK_BOT - TRACK_TOP) * (q + 1) / n;
        var x0 = la + (lb - la) * q / n, x1 = la + (lb - la) * (q + 1) / n, w = 14 + 8 * q / n;
        c.fillStyle = (q + j) % 2 ? '#eee' : '#111';
        c.beginPath(); c.moveTo(x0 + j * w, y0); c.lineTo(x0 + (j + 1) * w, y0); c.lineTo(x1 + (j + 1) * w, y1); c.lineTo(x1 + j * w, y1); c.fill();
      }
      if (m[1] === 'finish') {   // an arch of bones over the line (its near post is drawn over the hounds)
        c.strokeStyle = '#d9cfb8'; c.lineWidth = 14;
        c.beginPath(); c.moveTo(la - 10, TRACK_TOP); c.lineTo(la - 10, 360); c.lineTo(lb + 30, 330); c.stroke();
        c.fillStyle = TH.banner; c.fillRect(la - 30, 330, lb - la + 80, 70);
        c.fillStyle = TH.gold; c.font = 'bold 54px Impact, sans-serif'; c.textAlign = 'center'; c.fillText('FINISH', (la + lb) / 2 + 20, 386);
      }
    });
  }
  function drawNearPost(c, R, cam) {
    var b = sx(R.D, S_NEAR, cam);
    if (b < -60 || b > BASE_W + 60) return;
    c.strokeStyle = '#d9cfb8'; c.lineWidth = 14; c.lineCap = 'round';
    c.beginPath(); c.moveTo(b + 30, 330); c.lineTo(b + 30, BASE_H + 20); c.stroke();
    c.strokeStyle = 'rgba(60,30,20,.5)'; c.lineWidth = 4; c.beginPath(); c.moveTo(b + 35, 340); c.lineTo(b + 35, BASE_H + 20); c.stroke();
    c.lineCap = 'butt';
  }
  function drawJunk(c, name, x, y, s, rot) {
    c.save(); c.translate(x, y); c.rotate(rot); c.scale(s, s);
    c.lineWidth = 3; c.strokeStyle = '#1a0d08';
    var e, d, f;
    switch (name) {
      case 'BONE':
        c.fillStyle = '#e6dcc4'; c.beginPath(); c.roundRect(-30, -5, 60, 10, 5); c.fill(); c.stroke();
        for (e = -1; e <= 1; e += 2) for (d = -1; d <= 1; d += 2) { c.beginPath(); c.arc(e * 32, d * 6, 8, 0, 6.283); c.fill(); c.stroke(); }
        break;
      case 'SKULL':
        c.fillStyle = '#e9e0c8'; c.beginPath(); c.arc(0, -6, 22, 0, 6.283); c.fill(); c.stroke();
        c.beginPath(); c.roundRect(-13, 8, 26, 14, 4); c.fill(); c.stroke();
        c.fillStyle = '#1a0d08'; c.beginPath(); c.arc(-8, -6, 6, 0, 6.283); c.arc(8, -6, 6, 0, 6.283); c.fill();
        c.fillRect(-7, 12, 3, 8); c.fillRect(-1, 12, 3, 8); c.fillRect(5, 12, 3, 8);
        break;
      case 'FLAMING TRASH BAG':
        c.fillStyle = '#151515'; c.beginPath(); c.ellipse(0, 4, 24, 20, 0, 0, 6.283); c.fill(); c.stroke();
        c.beginPath(); c.moveTo(-6, -14); c.lineTo(0, -24); c.lineTo(6, -14); c.fill();
        break;
      case 'MUG OF BLOOD':
        c.fillStyle = '#c9c2b0'; c.beginPath(); c.roundRect(-16, -18, 32, 38, 4); c.fill(); c.stroke();
        c.beginPath(); c.arc(20, 0, 9, -1.4, 1.4); c.lineWidth = 5; c.strokeStyle = '#c9c2b0'; c.stroke();
        c.fillStyle = '#8a0a06'; c.fillRect(-13, -15, 26, 10);
        break;
      case 'TOILET':
        c.fillStyle = '#f2f2ee';
        c.beginPath(); c.roundRect(-26, -34, 22, 34, 4); c.fill(); c.stroke();
        c.beginPath(); c.ellipse(6, -4, 24, 9, 0, 0, 6.283); c.fill(); c.stroke();
        c.beginPath(); c.moveTo(-8, -2); c.lineTo(20, -2); c.lineTo(12, 22); c.lineTo(-2, 22); c.closePath(); c.fill(); c.stroke();
        break;
      case 'ROTTEN EYEBALL':
        c.fillStyle = '#e8e4c8'; c.beginPath(); c.arc(0, 0, 16, 0, 6.283); c.fill(); c.stroke();
        c.fillStyle = '#5a7a1a'; c.beginPath(); c.arc(5, -2, 7, 0, 6.283); c.fill();
        c.fillStyle = '#000'; c.beginPath(); c.arc(6, -2, 3, 0, 6.283); c.fill();
        c.strokeStyle = '#a01a10'; c.lineWidth = 1.5; c.beginPath(); c.moveTo(-14, 4); c.lineTo(-6, 2); c.moveTo(-12, -8); c.lineTo(-5, -5); c.stroke();
        break;
      case 'SEVERED HAND':
        c.fillStyle = '#7a2a1a'; c.beginPath(); c.roundRect(-14, -10, 24, 22, 6); c.fill(); c.stroke();
        for (f = 0; f < 4; f++) { c.beginPath(); c.roundRect(8, -10 + f * 6, 18, 5, 2); c.fill(); c.stroke(); }
        c.fillStyle = '#c01a10'; c.fillRect(-18, -8, 5, 18);
        break;
    }
    c.restore();
  }
  function drawHazard(c, R, z, cam, t, dt) {
    var Rw = ROWS[z.row % ROWS_N], s = Rw.s, x = sx(z.x, s, cam), y = Rw.y, u = (t - z.t) / z.dur, k, i;
    if (z.kind !== 'thrown' && (x < -300 || x > BASE_W + 300)) return;
    switch (z.kind) {
      case 'slime':
        c.fillStyle = 'rgba(70,200,40,.85)'; c.beginPath(); c.ellipse(x, y + 4, 80 * s, 14 * s, 0, 0, 6.283); c.fill();
        c.fillStyle = 'rgba(170,255,120,.6)'; c.beginPath(); c.ellipse(x - 20 * s, y + 1, 30 * s, 4 * s, 0, 0, 6.283); c.fill();
        break;
      case 'soul': {
        if (u > 0.1) break;
        var bob = Math.sin(t * 5) * 6, gy = y - 50 * s + bob;
        c.globalCompositeOperation = 'lighter';
        var g = c.createRadialGradient(x, gy, 2, x, gy, 40 * s);
        g.addColorStop(0, 'rgba(180,255,230,.8)'); g.addColorStop(1, 'rgba(120,255,200,0)');
        c.fillStyle = g; c.beginPath(); c.arc(x, gy, 40 * s, 0, 6.283); c.fill();
        c.globalCompositeOperation = 'source-over';
        c.fillStyle = '#0a1a14'; c.fillRect(x - 8 * s, gy - 8 * s, 4 * s, 6 * s); c.fillRect(x + 4 * s, gy - 8 * s, 4 * s, 6 * s);
        break;
      }
      case 'geyser':
        c.fillStyle = '#120302'; c.beginPath(); c.ellipse(x, y + 4, 40 * s, 9 * s, 0, 0, 6.283); c.fill();
        c.fillStyle = 'rgba(255,90,20,.7)'; c.beginPath(); c.ellipse(x, y + 4, 26 * s, 5 * s, 0, 0, 6.283); c.fill();
        if (dt > 0 && u > -0.5 && u < 1.2) {
          k = clamp((u + 0.5) / 0.4, 0, 1) * clamp((1.2 - u) / 0.4, 0, 1);
          var L = { parts: R.lava.parts, hue: 0, size: s * 1.4 };
          for (i = 0; i < 6 * k; i++) flame(L, x + rnd(-15, 15) * s, y, { vy: -rnd(300, 520), life: 1.3, size: 1.6 });
        }
        break;
      case 'thrown': {
        if (t < z.t0) break;
        var X, Y, sc, rot = (t - z.t0) * z.spin;
        if (t < z.tl) {
          k = (t - z.t0) / (z.tl - z.t0); var s0 = S_FAR * 0.75;
          sc = s0 + (Rw.s - s0) * k; X = sx(z.x0 + (z.x1 - z.x0) * k, sc, cam); Y = 530 + (z.y1 - 530) * k - 340 * 4 * k * (1 - k);
        } else if (z.mode === 'hit') {
          if (t - z.tl > 1.6) break;
          k = Math.min(1, (t - z.tl) / 0.8);
          sc = Rw.s; X = sx(z.x1 - 380 * k, sc, cam); Y = z.y1 + (Rw.y - 10 * Rw.s - z.y1) * k - 140 * Rw.s * Math.sin(Math.PI * k);
          if (k >= 1) rot = 0;
        } else {
          if (z.eats && u > 0.15) break;
          sc = Rw.s; rot = 0.5;
          if (u > 0.2) { k = Math.min(1, (u - 0.2) / 0.35); if (k >= 1) break; X = sx(z.x1 + 500 * k, sc, cam); Y = z.y1 - 180 * Rw.s * Math.sin(Math.PI * k); rot = 0.5 + k * 9; }
          else { X = sx(z.x1, sc, cam); Y = z.y1; }
        }
        drawJunk(c, z.obj, X, Y, sc / SPRK, rot);
        if (dt > 0 && z.obj === 'FLAMING TRASH BAG' && FXR() < 0.7) flame(R.lava, X, Y - 20 * sc, { size: 1.2 });
        break;
      }
      case 'bone':
        if (u < 0 || u > 0.45) break;
        c.fillStyle = '#e6dcc4'; c.strokeStyle = '#3a2a20'; c.lineWidth = 2;
        c.beginPath(); c.roundRect(x - 34 * s, y - 6 * s, 68 * s, 10 * s, 5 * s); c.fill(); c.stroke();
        for (var e = -1; e <= 1; e += 2) for (var d = -1; d <= 1; d += 2) { c.beginPath(); c.arc(x + e * 36 * s, y - s + d * 5 * s, 7 * s, 0, 6.283); c.fill(); c.stroke(); }
        break;
      case 'hand': {
        k = u < 0 ? 0 : u < 0.15 ? u / 0.15 : u < 0.85 ? 1 : Math.max(0, 1 - (u - 0.85) / 0.15);
        if (k <= 0) break;
        var top = y - 70 * s * k;
        c.fillStyle = '#3a0a0a';
        c.beginPath(); c.moveTo(x - 16 * s, y + 4); c.lineTo(x - 12 * s, top + 20 * s); c.lineTo(x + 12 * s, top + 20 * s); c.lineTo(x + 16 * s, y + 4); c.fill();
        for (var fi = -2; fi <= 2; fi++) { c.beginPath(); c.moveTo(x + fi * 6 * s, top + 22 * s); c.quadraticCurveTo(x + fi * 9 * s, top - 4 * s, x + fi * 4 * s + 10 * s, top + 2 * s); c.lineWidth = 6 * s; c.strokeStyle = '#3a0a0a'; c.stroke(); }
        break;
      }
    }
  }
  function drawLob(c, R, j, cam, t, air, dt) {
    if (t < j.t0) return;
    var Rw = ROWS[j.row % ROWS_N], k, X, Y, sc;
    if (t < j.tl) {
      if (!air) return;
      k = (t - j.t0) / (j.tl - j.t0); var s0 = S_FAR * 0.75; sc = s0 + (Rw.s - s0) * k;
      X = sx(j.x0 + (j.x1 - j.x0) * k, sc, cam); Y = 530 + (Rw.y - 8 * Rw.s - 530) * k - 340 * 4 * k * (1 - k);
      drawJunk(c, j.obj, X, Y, sc / SPRK, (t - j.t0) * j.spin);
      if (dt > 0 && j.obj === 'FLAMING TRASH BAG' && FXR() < 0.6) flame(R.lava, X, Y - 20 * sc, { size: 1.1 });
      return;
    }
    if (air) return;
    var b = Math.min(1, (t - j.tl) / 0.45);
    X = sx(j.x1 + 70 * b, Rw.s, cam);
    if (X < -250 || X > BASE_W + 250) return;
    Y = Rw.y - 8 * Rw.s - 46 * Rw.s * Math.sin(Math.PI * b) * (1 - b * 0.5);
    drawJunk(c, j.obj, X, Y, Rw.s / SPRK, (j.tl - j.t0) * j.spin + b * 2.5);
    if (dt > 0 && j.obj === 'FLAMING TRASH BAG' && FXR() < 0.35) flame(R.lava, X, Y - 16 * Rw.s, { size: 0.9 });
  }
  function frameRect(h, f) { var sh = h.sheet; return [(f % sh.cols) * sh.w, Math.floor(f / sh.cols) * sh.h, sh.w, sh.h]; }
  function drawRunner(c, R, r, cam) {
    var nose = sx(r.x, r.s, cam), left = nose - NOSE * r.s + r.ox, top = r.y - (r.A ? r.A.ground : 210) * r.s + r.oy;
    r.dx = r.shown ? left - r.left : 0; r.left = left; r.top = top; r.shown = true;
    var h = r.h;
    if (!h.img || !h.sheet || !h.img.complete || !h.img.naturalWidth) return;
    var fr = frameRect(h, r.f), w = fr[2] * r.s, hh = fr[3] * r.s;
    c.save();
    if (r.hue || r.sat !== 1.2) c.filter = 'hue-rotate(' + r.hue + 'deg) saturate(' + r.sat + ')';
    c.translate(left + BODY[0] * r.s, top + BODY[1] * r.s); c.rotate(r.rot); if (r.flip) c.scale(-1, 1);
    c.drawImage(h.img, fr[0], fr[1], fr[2], fr[3], -BODY[0] * r.s, -BODY[1] * r.s, w, hh);
    c.restore();
  }
  function ptOf(r) {
    return function (key) {
      var fr = r.A && r.A.frames[r.f], p = fr && (fr[key] || fr.tail);
      return p ? [r.left + p[0] * r.s, r.top + p[1] * r.s, p[2]] : [r.left, r.top, 0];
    };
  }
  // the scene at race time t: dt = 0 draws it without moving the particles (the photo-finish snapshot)
  function drawScene(c, R, t, dt, cfg) {
    var cam = R.cam, rr = R.runners, lastCam = R.lastCam == null ? cam : R.lastCam;
    var camV = dt > 0 ? (cam - lastCam) / dt : 0;
    R.lastCam = cam;
    drawBackdrop(c, R, cam, t);
    R.hazards.forEach(function (z) { if (z.kind !== 'geyser' && z.kind !== 'thrown') drawHazard(c, R, z, cam, t, dt); });
    if (cfg.show_crowd) R.lobs.forEach(function (j) { drawLob(c, R, j, cam, t, false, dt); });
    rr.slice().sort(function (a, b) { return a.row - b.row; }).forEach(function (r) {
      R.hazards.forEach(function (z) { if (z.kind === 'geyser' && z.row === r.row) drawHazard(c, R, z, cam, t, dt); });
      drawRunner(c, R, r, cam);
      if (!r.pt) r.pt = ptOf(r);
      r.speed = camV * r.s;
      if (dt > 0 && t > 0 && r.hold == null && r.A) {
        var downs = [], NF = r.h.sheet ? r.h.sheet.frames : 16, q;
        for (q = r.prevF; q !== r.f; q = (q + 1) % NF) {
          var a = r.A.frames[q], b = r.A.frames[(q + 1) % NF];
          ['LH', 'RH', 'LF', 'RF'].forEach(function (p) { if (!a[p][2] && b[p][2]) downs.push(r.pt(p)); });
          if (downs.length > 8) break;
        }
        r.fx.forEach(function (f) { if (f.tick) f.tick(r, dt); if (f.step && downs.length) f.step(r, downs); });
      }
      if (dt > 0) R.events.forEach(function (e) {      // a flaming arse
        if (e.a !== r.i || e.type !== 'scorch' || t < e.t || t > e.t + e.dur * 0.95) return;
        var h = r.pt('tail_base');
        for (var i = 0; i < 5; i++) flame(r, h[0] + rnd(-8, 8) * r.s, h[1] + rnd(-6, 10) * r.s, { size: 1.8, vx: -120 });
        if (FXR() < 0.5) smoke(r, h[0], h[1] - 10 * r.s, '60,45,40', { size: 2 });
      });
      drawFX(r, c, dt);
    });
    drawNearPost(c, R, cam);
    R.hazards.forEach(function (z) { if (z.kind === 'thrown') drawHazard(c, R, z, cam, t, dt); });
    if (cfg.show_crowd) R.lobs.forEach(function (j) { drawLob(c, R, j, cam, t, true, dt); });
    R.lava.speed = camV * ROWS[ROWS_N - 1].s;
    drawFX(R.lava, c, dt);
  }

  // ---------------------------------------------------------------- drawing: on top of the scene
  function say(cfg, s) { return cfg.clean_language ? String(s).replace(SWEAR, function (w) { return '#@$%&!*#@$%&!*#@$%&!*'.slice(0, w.length); }) : String(s); }
  function drawTag(c, r) {
    var p = r.pt('head'), k = r.s / SPRK;
    c.font = 'bold ' + Math.round(20 * k) + 'px system-ui, sans-serif';
    var bw = 30 * k, w = c.measureText(r.name).width + bw + 16, x = clamp(p[0] - w / 2, 4, BASE_W - w - 4), y = p[1] - 64 * r.s;
    c.fillStyle = 'rgba(10,4,4,.75)'; c.beginPath(); c.roundRect(x, y - 19 * k, w, 28 * k, 8); c.fill();
    c.fillStyle = r.colour; c.beginPath(); c.roundRect(x, y - 19 * k, bw, 28 * k, 8); c.fill();
    c.fillStyle = '#000'; c.textAlign = 'center'; c.font = '900 ' + Math.round(22 * k) + 'px Impact, sans-serif'; c.fillText(String(r.num), x + bw / 2, y + 2 * k);
    c.fillStyle = '#fff'; c.textAlign = 'left'; c.font = 'bold ' + Math.round(20 * k) + 'px system-ui, sans-serif'; c.fillText(r.name, x + bw + 8, y + 1);
  }
  function drawBubbles(c, R, t, dt, cfg) {
    var placed = [];
    R.talk.forEach(function (b) {
      var age = t - b.t;
      if (age < 0 || age > b.life) { b.x = null; return; }
      var r = b.r, s = Math.max(0.85, r.s / SPRK), text = say(cfg, b.text);
      c.font = '900 ' + Math.round(30 * s) + 'px Impact, "Arial Black", sans-serif';
      var w = Math.min(600, c.measureText(text).width + 34), h = 50 * s, nose = sx(r.x, r.s, R.cam);
      var tx = clamp(nose - 120 * r.s - w * 0.35, 8, BASE_W - w - 8), ty = clamp(r.y - 290 * r.s, 8, BASE_H - h - 8), k;
      for (k = 0; k < 8; k++) {
        var hit = null;
        placed.forEach(function (q) { if (!hit && tx < q.x + q.w && tx + w > q.x && ty < q.y + q.h && ty + h > q.y) hit = q; });
        if (!hit) break;
        ty = hit.y - h - 10;
      }
      if (b.x === null) { b.x = tx; b.y = ty; }
      var m = Math.min(1, dt * 5); b.x += (tx - b.x) * m; b.y += (ty - b.y) * m;
      var x = b.x, y = b.y;
      placed.push({ x: x, y: y, w: w, h: h });
      var tipX = clamp(nose - 130 * r.s, x + 16, x + w - 16), tipY = Math.max(r.y - 215 * r.s, y + h + 14), bx = clamp(tipX - 14, x + 10, x + w - 40);
      c.globalAlpha = Math.min(1, age / 0.15, (b.life - age) / 0.3);
      c.fillStyle = '#fff'; c.strokeStyle = '#000'; c.lineWidth = 3;
      c.beginPath(); c.roundRect(x, y, w, h, 16); c.fill(); c.stroke();
      c.beginPath(); c.moveTo(bx, y + h - 2); c.lineTo(tipX, tipY); c.lineTo(bx + 26, y + h - 2); c.closePath(); c.fill();
      c.beginPath(); c.moveTo(bx, y + h); c.lineTo(tipX, tipY); c.lineTo(bx + 26, y + h); c.stroke();
      c.fillStyle = '#111'; c.textAlign = 'center'; c.textBaseline = 'middle'; c.fillText(text, x + w / 2, y + h / 2 + 1, w - 18);
      c.textBaseline = 'alphabetic'; c.globalAlpha = 1;
    });
  }
  function drawHud(c, R, t, cfg) {
    c.fillStyle = TH.dark; c.beginPath(); c.roundRect(24, 22, 430, 70, 12); c.fill();
    var title = String(cfg.title || DEFAULTS.title).toUpperCase();
    c.fillStyle = TH.accent; c.font = 'bold ' + (title.length > 18 ? 32 : 40) + 'px Impact, sans-serif'; c.textAlign = 'left'; c.fillText(title, 40, 72, 400);
    c.fillStyle = TH.dark; c.beginPath(); c.roundRect(BASE_W - 180, 22, 156, 70, 12); c.fill();
    c.fillStyle = '#fff'; c.font = 'bold 40px Impact, sans-serif'; c.textAlign = 'right'; c.fillText(t < 0 ? '0.0' : t.toFixed(1), BASE_W - 40, 72);
    var x0 = 520, x1 = BASE_W - 240, y = 56;
    c.fillStyle = TH.dark; c.beginPath(); c.roundRect(x0 - 20, 30, x1 - x0 + 60, 52, 12); c.fill();
    c.strokeStyle = '#6a2a1a'; c.lineWidth = 6; c.beginPath(); c.moveTo(x0, y); c.lineTo(x1, y); c.stroke();
    c.fillStyle = '#eee'; c.fillRect(x1, y - 14, 6, 28);
    R.runners.slice().sort(function (a, b) { return a.x - b.x; }).forEach(function (r) {
      var px = x0 + (x1 - x0) * clamp(r.x / R.D, 0, 1);
      c.fillStyle = r.colour; c.beginPath(); c.arc(px, y, 15, 0, 6.283); c.fill(); c.strokeStyle = '#000'; c.lineWidth = 2; c.stroke();
      c.fillStyle = '#000'; c.font = '900 20px Impact, sans-serif'; c.textAlign = 'center'; c.fillText(String(r.num), px, y + 7);
    });
    var line = null;
    R.ticker.forEach(function (q) { if (q.t <= t) line = q; });
    c.fillStyle = TH.dark; c.fillRect(0, BASE_H - 64, BASE_W, 64);
    c.fillStyle = TH.hot; c.fillRect(0, BASE_H - 64, BASE_W, 4);
    if (line) {
      c.globalAlpha = clamp((t - line.t) / 0.2, 0, 1); c.fillStyle = TH.gold; c.font = 'bold 34px Impact, sans-serif'; c.textAlign = 'center';
      c.fillText(say(cfg, line.text), BASE_W / 2, BASE_H - 20, BASE_W - 80); c.globalAlpha = 1;
    }
    if (t < 0) {
      var n = Math.ceil(-t);
      c.fillStyle = TH.gold; c.font = 'bold 220px Impact, sans-serif'; c.textAlign = 'center'; c.fillText(n > 2 ? 'READY' : String(n), BASE_W / 2, 420);
    } else if (t < 0.8) {
      c.fillStyle = 'rgba(255,90,30,' + (1 - t / 0.8) + ')'; c.font = 'bold 240px Impact, sans-serif'; c.textAlign = 'center'; c.fillText('GO!', BASE_W / 2, 430);
    }
  }
  function drawPhoto(c, R, P, age, k) {
    var snap = R.snap, sc = R.snapK;
    c.drawImage(snap, 0, 0, BASE_W, BASE_H);
    c.fillStyle = 'rgba(0,0,0,' + Math.min(0.65, age * 2) + ')'; c.fillRect(0, 0, BASE_W, BASE_H);
    var src = R.snapSrc, e = ease(Math.min(1, age / 0.5)), pw = 1240, ph = pw * src[3] / src[2], px = (BASE_W - pw) / 2, py = 110 + (1 - e) * 50;
    c.save(); c.globalAlpha = e;
    c.translate(BASE_W / 2, py + ph / 2); c.rotate(-0.025 * e); c.translate(-BASE_W / 2, -(py + ph / 2));
    c.fillStyle = '#efe4cf'; c.fillRect(px - 20, py - 20, pw + 40, ph + 120);
    c.filter = 'sepia(.85) saturate(1.4) contrast(1.2) brightness(1.35)';
    c.drawImage(snap, src[0] * sc, src[1] * sc, src[2] * sc, src[3] * sc, px, py, pw, ph);
    c.filter = 'none';
    var fx = pw / src[2], fy = ph / src[3], ln = R.snapLine;
    c.save(); c.beginPath(); c.rect(px, py, pw, ph); c.clip();
    c.strokeStyle = 'rgba(230,20,10,.95)'; c.lineWidth = 3; c.setLineDash([14, 9]);
    c.beginPath(); c.moveTo(px + (ln[0][0] - src[0]) * fx, py + (ln[0][1] - src[1]) * fy); c.lineTo(px + (ln[1][0] - src[0]) * fx, py + (ln[1][1] - src[1]) * fy); c.stroke();
    c.setLineDash([]);
    R.snapTags.forEach(function (q) {                 // who is who: the two hounds it was between
      var x = px + (q.x - src[0]) * fx, y = py + (q.y - src[1]) * fy;
      c.fillStyle = q.colour; c.beginPath(); c.arc(x, y, 24, 0, 6.283); c.fill(); c.strokeStyle = '#000'; c.lineWidth = 3; c.stroke();
      c.fillStyle = '#000'; c.font = '900 30px Impact, sans-serif'; c.textAlign = 'center'; c.fillText(String(q.num), x, y + 11);
    });
    c.restore();
    var grain = mulberry(Math.floor(age * 12));
    for (var i = 0; i < 600; i++) { c.fillStyle = grain() < 0.5 ? 'rgba(0,0,0,' + grain() * 0.14 + ')' : 'rgba(255,255,255,' + grain() * 0.12 + ')'; c.fillRect(px + grain() * pw, py + grain() * ph, 2, 2); }
    var who = R.runners[P.a].name.toUpperCase();
    c.fillStyle = '#1a0d06'; c.font = 'bold 62px Impact, sans-serif'; c.textAlign = 'center';
    c.fillText(P.kind === 'win' ? who + ' WINS BY ' + P.label + '!' : who + ' IS DEAD LAST BY ' + P.label + '!', BASE_W / 2, py + ph + 76, pw);
    c.translate(px + 190, py + 78); c.rotate(-0.12);
    c.strokeStyle = '#b0140c'; c.lineWidth = 6; c.strokeRect(-165, -44, 330, 88);
    c.fillStyle = '#b0140c'; c.font = 'bold 54px Impact, sans-serif'; c.fillText('PHOTO FINISH', 0, 19);
    c.restore(); c.globalAlpha = 1;
    if (age < 0.22) { c.fillStyle = 'rgba(255,255,255,' + (1 - age / 0.22) + ')'; c.fillRect(0, 0, BASE_W, BASE_H); }
  }

  // ---------------------------------------------------------------- the lineup (bets open)
  function spotlightOf(inst, h) {
    var sp = inst.spot;
    if (!sp || sp.id !== h.id) {
      var Ah = hound(h.id) || {};
      sp = inst.spot = { id: h.id, phase: 0, f: 0, line: '', parts: [], hue: Ah.hue || 0, size: 1.55, speed: 0, dx: 0, ground: 820,
        fx: (Ah.fx || []).map(function (n) { return FX[n]; }).filter(Boolean), h: Ah };
      var rng = mulberry(((inst.st && inst.st.game && inst.st.game.id) || 'x').length * 7919 + h.num), pool;
      if (h.streak >= 2) pool = lines('hot'); else if (h.streak <= -4) pool = lines('cold'); else pool = lines('by', h.id);
      if (!pool.length) pool = lines('win');
      sp.line = pool.length ? fill(pickR(rng, pool), { t: 'losers', n: Math.abs(h.streak || 0) }) : '';
    }
    return sp;
  }
  function drawLineup(c, inst, g, dt, cfg, left) {
    var field = g.field || [], total = Math.max(1, (+g.phase_ms || 1) / 1000);
    c.fillStyle = 'rgba(6,1,1,.82)'; c.fillRect(0, 0, BASE_W, BASE_H);
    c.textAlign = 'left'; c.fillStyle = TH.gold; c.font = 'bold 34px Impact, sans-serif'; c.fillText(String(cfg.title || DEFAULTS.title).toUpperCase(), 74, 66, 1000);
    c.fillStyle = TH.accent; c.font = 'bold 72px Impact, sans-serif'; c.fillText('PLACE YOUR BETS', 70, 136);
    if (cfg.show_rules) {
      c.fillStyle = TH.ink; c.font = '28px system-ui, sans-serif';
      c.fillText('Pick the winner by its number. If it wins you get your bet × its multiplier.', 74, 184, 1060);
    }
    if (!field.length) return;
    var each = total / field.length, idx = clamp(Math.floor((total - left) / each), 0, field.length - 1), h = field[idx];
    var sp = spotlightOf(inst, h), Ah = sp.h, k = 1.3, gx = 70, gy = 840;
    if (Ah && Ah.sheet && Ah.img && Ah.img.naturalWidth) {
      var A = Ah.anchors, NF = Ah.sheet.frames;
      sp.phase += 2.4 * dt * (Ah.cadence || 1);
      var prevF = sp.f, f = Math.floor(((sp.phase % 1 + 1) % 1) * NF);
      sp.f = f;
      var top = gy - A.ground * k;
      sp.pt = function (key) { var fr = A.frames[f], p = fr[key] || fr.tail; return [gx + p[0] * k, top + p[1] * k]; };
      sp.speed = A.stride * k * 2.4 * (Ah.cadence || 1);
      c.strokeStyle = 'rgba(255,110,40,.35)'; c.lineWidth = 3;
      var off = (sp.phase * A.stride * k) % 80;
      for (var x = gx - 40 - off; x < gx + 540; x += 80) { c.beginPath(); c.moveTo(x, gy + 4); c.lineTo(x + 40, gy + 4); c.stroke(); }
      var fr = frameRect(Ah, f);
      c.save();
      if (Ah.hue || (Ah.sat != null && Ah.sat !== 1.2)) c.filter = 'hue-rotate(' + (Ah.hue || 0) + 'deg) saturate(' + (Ah.sat != null ? Ah.sat : 1.2) + ')';
      c.drawImage(Ah.img, fr[0], fr[1], fr[2], fr[3], gx, top, fr[2] * k, fr[3] * k);
      c.restore();
      var downs = [];
      for (var q = prevF; q !== f; q = (q + 1) % NF) {
        var a = A.frames[q], b = A.frames[(q + 1) % NF];
        ['LH', 'RH', 'LF', 'RF'].forEach(function (p) { if (!a[p][2] && b[p][2]) downs.push(sp.pt(p)); });
        if (downs.length > 8) break;
      }
      sp.fx.forEach(function (fx) { if (fx.tick) fx.tick(sp, dt); if (fx.step && downs.length) fx.step(sp, downs); });
      drawFX(sp, c, dt);
    }
    var col = colourOf(h.id);
    c.fillStyle = col; c.beginPath(); c.roundRect(80, 240, 120, 120, 18); c.fill();
    c.fillStyle = '#000'; c.font = '900 96px Impact, sans-serif'; c.textAlign = 'center'; c.fillText(String(h.num), 140, 336);
    c.textAlign = 'left'; c.fillStyle = '#fff'; c.font = 'bold 76px Impact, sans-serif'; c.fillText(String(h.name).toUpperCase(), 222, 312, 920);
    c.fillStyle = TH.dim; c.font = 'italic 28px system-ui, sans-serif'; c.fillText(h.trait || '', 224, 352, 900);
    c.fillStyle = TH.gold; c.font = 'bold 44px Impact, sans-serif'; c.fillText('PAYS ×' + (+h.mult).toFixed(2), 224, 410);
    c.fillStyle = TH.ink; c.font = '26px system-ui, sans-serif'; c.fillText(Math.round(h.chance * 100) + '% to win · ' + (h.note || ''), 520, 408, 600);
    if (sp.line && cfg.show_talk) {
      c.font = '900 34px Impact, "Arial Black", sans-serif';
      var text = say(cfg, sp.line), qw = Math.min(600, c.measureText(text).width + 40);
      c.fillStyle = '#fff'; c.strokeStyle = '#000'; c.lineWidth = 3; c.beginPath(); c.roundRect(560, 470, qw, 62, 18); c.fill(); c.stroke();
      c.fillStyle = '#111'; c.textAlign = 'center'; c.textBaseline = 'middle'; c.fillText(text, 560 + qw / 2, 502, qw - 24); c.textBaseline = 'alphabetic';
    }
    // the odds board: number, name, form, multiplier, and the money on each hound
    var money = {};
    (g.markers || []).forEach(function (m) { money[m.num] = m.total; });
    var bx = 1190, by = 110, bw = 660, rowH = Math.min(108, Math.floor(760 / field.length));
    c.fillStyle = TH.panel; c.beginPath(); c.roundRect(bx, by, bw, 128 + field.length * rowH, 20); c.fill();
    c.strokeStyle = TH.hot; c.lineWidth = 3; c.stroke();
    c.fillStyle = TH.accent; c.font = 'bold 46px Impact, sans-serif'; c.textAlign = 'center'; c.fillText('TONIGHT\'S DAMNED', bx + bw / 2, by + 66);
    var fav = field.reduce(function (a, b) { return a.chance > b.chance ? a : b; }), lng = field.reduce(function (a, b) { return a.chance < b.chance ? a : b; });
    field.forEach(function (q, i) {
      var y = by + 108 + i * rowH, on = q === h, cq = colourOf(q.id);
      c.fillStyle = on ? 'rgba(255,120,40,.22)' : 'rgba(255,255,255,.04)'; c.beginPath(); c.roundRect(bx + 18, y, bw - 36, rowH - 16, 14); c.fill();
      c.fillStyle = cq; c.beginPath(); c.roundRect(bx + 30, y + 12, 68, 68, 12); c.fill();
      c.fillStyle = '#000'; c.font = '900 54px Impact, sans-serif'; c.textAlign = 'center'; c.fillText(String(q.num), bx + 64, y + 66);
      c.textAlign = 'left'; c.fillStyle = '#fff'; c.font = 'bold 38px Impact, sans-serif'; c.fillText(q.name, bx + 118, y + 52, 330);
      c.fillStyle = TH.dim; c.font = '20px system-ui, sans-serif';
      var note = (q === fav ? 'the favourite · ' : q === lng ? 'the longshot · ' : '') + (q.streak >= 2 ? '🔥 ' : '') + (q.note || '');
      c.fillText(note + (money[q.num] ? ' · ' + fmt(money[q.num]) + ' on it' : ''), bx + 120, y + 80, 380);
      c.textAlign = 'right'; c.fillStyle = TH.gold; c.font = 'bold 46px Impact, sans-serif'; c.fillText('×' + (+q.mult).toFixed(2), bx + bw - 38, y + 62);
    });
    if (cfg.show_players) drawBackers(c, g, cfg);
    var by2 = 930, frac = clamp(left / total, 0, 1);
    c.fillStyle = 'rgba(255,255,255,.08)'; c.beginPath(); c.roundRect(80, by2, 1020, 26, 13); c.fill();
    c.fillStyle = left < 5 ? '#ff3a1a' : TH.accent; c.beginPath(); c.roundRect(80, by2, Math.max(26, 1020 * frac), 26, 13); c.fill();
    c.textAlign = 'left'; c.fillStyle = '#fff'; c.font = 'bold 40px Impact, sans-serif'; c.fillText('BETS CLOSE IN ' + Math.ceil(left), 80, by2 - 16);
    c.textAlign = 'right'; c.fillStyle = TH.ink; c.font = 'bold 30px system-ui, sans-serif';
    c.fillText(String(g.commands_text || cfg.commands_text || ''), 1100, by2 - 18, 620);
    if (g.test) { c.textAlign = 'left'; c.fillStyle = '#ff6a5a'; c.font = 'bold 22px system-ui, sans-serif'; c.fillText('TEST GAME', 82, 1010); }
  }

  // who has money on which hound (the lineup), most first
  function drawBackers(c, g, cfg) {
    var ps = g.players || [], x = 640, y = 572, w = 500, max = clamp(Math.round(+cfg.players_max || 8), 1, 9), rows = ps.slice(0, max);
    var hgt = 58 + Math.max(1, rows.length) * 32 + (ps.length > max ? 26 : 0);
    c.fillStyle = TH.panel; c.beginPath(); c.roundRect(x, y, w, hgt, 14); c.fill();
    c.fillStyle = TH.accent; c.font = 'bold 26px Impact, sans-serif'; c.textAlign = 'left'; c.fillText('BACKERS', x + 18, y + 36);
    c.textAlign = 'right'; c.fillStyle = TH.dim; c.font = '18px system-ui, sans-serif';
    c.fillText(ps.length ? fmt(g.at_risk) + ' ' + (g.currency || cfg.currency || 'coins') + ' riding' : '', x + w - 18, y + 34);
    if (!rows.length) { c.textAlign = 'left'; c.fillStyle = TH.ink; c.font = 'italic 22px system-ui, sans-serif'; c.fillText('No bets yet. Be the first.', x + 18, y + 80); return; }
    rows.forEach(function (p, i) {
      var yy = y + 80 + i * 32, nums = p.bets.map(function (b) { return '#' + b.num; }).join(' ');
      c.textAlign = 'left'; c.fillStyle = '#fff'; c.font = 'bold 22px system-ui, sans-serif'; c.fillText(p.user, x + 18, yy, 220);
      c.fillStyle = TH.gold; c.fillText(nums, x + 250, yy, 120);
      c.textAlign = 'right'; c.fillStyle = TH.ink; c.fillText(fmt(p.stake), x + w - 18, yy);
    });
    if (ps.length > max) { c.textAlign = 'left'; c.fillStyle = TH.dim; c.font = '18px system-ui, sans-serif'; c.fillText('+ ' + (ps.length - max) + ' more', x + 18, y + 80 + rows.length * 32); }
  }

  // ---------------------------------------------------------------- the result card
  function drawResult(c, g, R, cfg) {
    var last = g.last || {}, order = last.order || [], cur = g.currency || cfg.currency || 'coins', byNum = {};
    (g.field || []).forEach(function (h) { byNum[h.num] = h; });
    var won = {}, people = [];             // each player's take: what they won minus what they lost
    (last.winners || []).forEach(function (b) { var p = won[b.user] || (won[b.user] = { user: b.user, net: 0 }); p.net += b.pays - b.amount; });
    (last.losers || []).forEach(function (b) { var p = won[b.user] || (won[b.user] = { user: b.user, net: 0 }); p.net -= b.amount; });
    for (var u in won) if (has(won, u)) people.push(won[u]);
    people.sort(function (a, b) { return b.net - a.net || (a.user < b.user ? -1 : 1); });
    var shown = cfg.show_players ? people.slice(0, clamp(Math.round(+cfg.players_max || 8), 1, 20)) : [], prow = Math.ceil(shown.length / 2);
    var x = BASE_W / 2 - 430, y = 120, w = 860, rows = order.length, h = 150 + rows * 62 + 70 + (prow ? 20 + prow * 34 : 0);
    c.fillStyle = TH.panel; c.beginPath(); c.roundRect(x, y, w, h, 18); c.fill(); c.strokeStyle = TH.hot; c.lineWidth = 3; c.stroke();
    c.fillStyle = TH.accent; c.font = 'bold 54px Impact, sans-serif'; c.textAlign = 'center'; c.fillText(order.length ? 'RESULTS' : 'NO RACE', BASE_W / 2, y + 70);
    var P = last.photo;
    order.forEach(function (num, i) {
      var q = byNum[num] || { name: '#' + num, mult: 0 }, yy = y + 130 + i * 62, dead = i === order.length - 1;
      c.fillStyle = dead ? 'rgba(140,20,10,.6)' : i === 0 ? 'rgba(255,150,40,.25)' : 'rgba(255,255,255,.04)'; c.fillRect(x + 20, yy - 40, w - 40, 54);
      c.fillStyle = colourOf(q.id); c.fillRect(x + 20, yy - 40, 8, 54);
      c.fillStyle = '#fff'; c.font = 'bold 36px Impact, sans-serif'; c.textAlign = 'left'; c.fillText((i + 1) + '.  #' + num + '  ' + q.name, x + 48, yy, 480);
      c.textAlign = 'right'; c.fillStyle = dead ? '#ff9a7a' : '#ccc';
      var tag = i === 0 ? 'WINNER · PAYS ×' + (+q.mult).toFixed(2) : dead ? (P && P.kind === 'last' ? 'DEAD LAST · by ' + P.label.toLowerCase() : 'DEAD LAST')
        : (i === 1 && P && P.kind === 'win' ? 'by ' + P.label.toLowerCase() : '');
      c.fillText(tag, x + w - 40, yy);
    });
    var winners = last.winners || [], paid = winners.reduce(function (a, b) { return a + b.pays; }, 0);
    c.textAlign = 'center'; c.fillStyle = TH.ink; c.font = 'bold 28px system-ui, sans-serif';
    var line = winners.length ? winners.length + ' winning bet' + (winners.length === 1 ? '' : 's') + ' paid ' + fmt(paid) + ' ' + cur + ': ' +
      winners.slice(0, 4).map(function (wn) { return wn.user + ' +' + fmt(wn.pays); }).join(' · ') + (winners.length > 4 ? ' …' : '')
      : !order.length ? String((g.summary && g.summary.text) || 'No bets, no race.')
      : ((g.players || []).length || (last.losers || []).length ? 'Nobody backed the winner. The bank thanks you.' : 'No bets on this one.');
    var ly = y + 150 + rows * 62;
    c.fillText(line, BASE_W / 2, ly + 22, w - 60);
    shown.forEach(function (p, i) {
      var col = i < prow ? 0 : 1, yy = ly + 70 + (i % prow) * 34, xx = x + 40 + col * 410;
      c.textAlign = 'left'; c.fillStyle = '#fff'; c.font = 'bold 24px system-ui, sans-serif'; c.fillText(p.user, xx, yy, 250);
      c.textAlign = 'right'; c.fillStyle = p.net > 0 ? '#7dff8a' : p.net < 0 ? '#ff8a7a' : TH.dim;
      c.fillText((p.net > 0 ? '+' : p.net < 0 ? '−' : '') + fmt(Math.abs(p.net)), xx + 370, yy);
    });
  }
  function drawIdle(c, st, cfg) {
    var form = (st.idle && st.idle.form) || [];
    c.fillStyle = TH.panel; c.beginPath(); c.roundRect(BASE_W / 2 - 430, 140, 860, 160 + form.length * 64, 18); c.fill();
    c.strokeStyle = TH.hot; c.lineWidth = 3; c.stroke();
    c.fillStyle = TH.accent; c.font = 'bold 56px Impact, sans-serif'; c.textAlign = 'center'; c.fillText(String(cfg.title || DEFAULTS.title).toUpperCase(), BASE_W / 2, 214);
    c.fillStyle = TH.ink; c.font = '26px system-ui, sans-serif'; c.fillText('The form book', BASE_W / 2, 256);
    form.forEach(function (f, i) {
      var y = 316 + i * 64;
      c.fillStyle = colourOf(f.id); c.fillRect(BASE_W / 2 - 400, y - 36, 8, 50);
      c.textAlign = 'left'; c.fillStyle = '#fff'; c.font = 'bold 34px Impact, sans-serif'; c.fillText(f.name, BASE_W / 2 - 380, y);
      c.textAlign = 'right'; c.fillStyle = TH.gold; c.fillText(f.wins + ' / ' + f.races, BASE_W / 2 + 400, y);
      c.fillStyle = TH.dim; c.font = '22px system-ui, sans-serif'; c.fillText(f.note || '', BASE_W / 2 + 250, y - 2);
    });
  }

  // ==================================================================== sound (synthesised: no files)
  function Sfx() { this.ctx = null; this.vol = 0.6; }
  Sfx.prototype.ac = function () {
    if (!this.ctx) {
      var AC = root.AudioContext || root.webkitAudioContext;
      if (!AC) return null;
      try { this.ctx = new AC(); } catch (e) { return null; }
    }
    if (this.ctx.state === 'suspended') { try { this.ctx.resume(); } catch (e) { /* needs a gesture outside OBS */ } }
    return this.ctx;
  };
  Sfx.prototype._buf = function (c) {
    if (this._noise) return this._noise;
    var b = c.createBuffer(1, c.sampleRate, c.sampleRate), d = b.getChannelData(0);
    for (var i = 0; i < d.length; i++) d[i] = Math.random() * 2 - 1;
    return (this._noise = b);
  };
  Sfx.prototype.noise = function (dur, type, f0, f1, q, gain, delay) {
    var c = this.ac(); if (!c) return;
    var t = c.currentTime + (delay || 0);
    var s = c.createBufferSource(); s.buffer = this._buf(c);
    var f = c.createBiquadFilter(); f.type = type; f.frequency.setValueAtTime(f0, t);
    if (f1) f.frequency.exponentialRampToValueAtTime(f1, t + dur);
    f.Q.value = q || 1;
    var g = c.createGain(); g.gain.setValueAtTime(gain * this.vol, t); g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
    s.connect(f); f.connect(g); g.connect(c.destination);
    s.start(t, Math.random() * 0.5); s.stop(t + dur + 0.02);
  };
  Sfx.prototype.tone = function (freq, dur, gain, type, to, delay, lp, vib) {
    var c = this.ac(); if (!c) return;
    var t = c.currentTime + (delay || 0);
    var o = c.createOscillator(); o.type = type || 'sine'; o.frequency.setValueAtTime(freq, t);
    if (to) o.frequency.exponentialRampToValueAtTime(to, t + dur);
    var g = c.createGain(); g.gain.setValueAtTime(0.0001, t); g.gain.exponentialRampToValueAtTime(gain * this.vol, t + Math.min(0.012, dur / 3));
    g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
    if (lp) { var f = c.createBiquadFilter(); f.type = 'lowpass'; f.frequency.value = lp; f.Q.value = 2.5; o.connect(f); f.connect(g); } else o.connect(g);
    if (vib) { var l = c.createOscillator(), lg = c.createGain(); l.frequency.value = vib[0]; lg.gain.value = vib[1]; l.connect(lg); lg.connect(o.frequency); l.start(t); l.stop(t + dur + 0.02); }
    g.connect(c.destination); o.start(t); o.stop(t + dur + 0.02);
  };
  Sfx.prototype.play = function (name) {
    var r = Math.random, i;
    switch (name) {
      case 'tick': this.tone(1200, 0.05, 0.16, 'square'); break;
      case 'beep': this.tone(660, 0.18, 0.3, 'square', 0, 0, 2400); break;
      case 'horn': this.tone(330, 0.7, 0.35, 'sawtooth', 0, 0, 1400); this.tone(415, 0.7, 0.3, 'sawtooth', 0, 0, 1400); this.tone(494, 0.7, 0.25, 'sawtooth', 0, 0, 1400); break;
      case 'roar': this.noise(1.6, 'bandpass', 500, 900, 0.6, 0.45); this.noise(1.3, 'bandpass', 1500, 1100, 0.8, 0.25, 0.1); break;
      case 'growl': var f = 90 + r() * 50; this.tone(f, 0.32, 0.38, 'sawtooth', f * 0.8, 0, 600, [28, 14]); break;
      case 'thud': this.tone(95, 0.28, 0.9, 'sine', 40); this.noise(0.12, 'lowpass', 500, 0, 1, 0.5); this.tone(90, 0.22, 0.6, 'sine', 45, 0.25); break;
      case 'yelp': this.tone(900, 0.22, 0.32, 'sawtooth', 1500, 0, 2600); this.tone(1400, 0.2, 0.28, 'sawtooth', 700, 0.18, 2600); break;
      case 'bonk': this.tone(540, 0.12, 0.5, 'square', 320, 0, 2200); this.noise(0.04, 'bandpass', 1800, 0, 3, 0.4); this.tone(110, 0.14, 0.5, 'sine', 60); break;
      case 'splat': this.noise(0.35, 'lowpass', 1400, 200, 1.2, 0.6); for (i = 0; i < 4; i++) this.tone(250 + r() * 300, 0.06, 0.18, 'sine', 120, 0.1 + i * 0.06); break;
      case 'geyser': this.noise(0.9, 'lowpass', 160, 70, 0.8, 0.5); this.noise(1.0, 'highpass', 1400, 500, 0.7, 0.4, 0.15); break;
      case 'snap': this.noise(0.05, 'highpass', 3000, 0, 1, 0.8); this.tone(300, 0.12, 0.4, 'square', 120, 0, 1400); break;
      case 'grab': for (i = 0; i < 6; i++) this.noise(0.03, 'bandpass', 2600 + r() * 1500, 0, 8, 0.28, i * 0.05); this.tone(70, 0.5, 0.4, 'sawtooth', 50, 0.1, 300); break;
      case 'zap': for (i = 0; i < 5; i++) this.noise(0.05, 'highpass', 5000, 2000, 2, 0.35, i * 0.05 + r() * 0.02); this.tone(110, 0.3, 0.3, 'sawtooth', 0, 0, 0, [55, 40]); break;
      case 'whoosh': this.noise(0.6, 'bandpass', 400, 2400, 0.9, 0.45); break;
      case 'chomp': for (i = 0; i < 3; i++) { this.noise(0.05, 'lowpass', 900, 0, 1, 0.5, i * 0.16); this.tone(160, 0.07, 0.3, 'sine', 90, i * 0.16); } break;
      case 'scratch': for (i = 0; i < 8; i++) this.noise(0.04, 'bandpass', 3200 + r() * 800, 0, 4, 0.18, i * 0.07); break;
      case 'boing': this.tone(180, 0.45, 0.45, 'sine', 560, 0, 0, [14, 70]); break;
      case 'yap': for (i = 0; i < 3; i++) this.tone(1100 + r() * 300, 0.07, 0.24, 'square', 700, i * 0.13, 3000); break;
      case 'shutter': this.noise(0.04, 'highpass', 2500, 0, 1, 0.7); this.noise(0.05, 'bandpass', 1800, 0, 2, 0.5, 0.09); this.tone(2400, 0.4, 0.12, 'sine', 600, 0.1); break;
      case 'fanfare': [523, 659, 784, 1047].forEach(function (fq, n) { this.tone(fq, 0.22, 0.34, 'triangle', 0, n * 0.12); }, this); this.tone(1047, 0.7, 0.3, 'triangle', 0, 0.5); this.tone(1568, 0.7, 0.2, 'sine', 0, 0.5); break;
      case 'sad': [[311, 0], [294, 0.3], [277, 0.6]].forEach(function (n) { this.tone(n[0], 0.28, 0.3, 'sawtooth', 0, n[1], 800, [6, 6]); }, this); this.tone(262, 0.9, 0.3, 'sawtooth', 185, 0.9, 800, [7, 9]); break;
      default: break;
    }
  };
  // what each event sounds like, and when (seconds after it starts)
  var EV_SOUND = { trip: [['thud', 0.35], ['yelp', 0.1]], junk: [['bonk', 0], ['thud', 0.4]], hand: [['grab', 0]], snack: [['chomp', 0.1]],
    junkfood: [['chomp', 0.1]], femur: [['boing', 0]], slime: [['splat', 0]], fleas: [['scratch', 0.05]], argue: [['growl', 0], ['growl', 0.5]],
    bite: [['snap', 0.2], ['yelp', 0.3]], snap: [['snap', 0.2]], zap: [['zap', 0]], kick: [['thud', 0.1]], flare: [['whoosh', 0]],
    bonk: [['bonk', 0]], yap: [['yap', 0]], showoff: [['whoosh', 0]], leap: [['boing', 0]], scorch: [['geyser', -0.2], ['yelp', 0.25]] };

  // ==================================================================== the instance
  function DB(container, config, opts) {
    this.box = container;
    this.cfg = merge(DEFAULTS, config);
    this.canvas = doc.createElement('canvas');
    this.canvas.style.cssText = 'position:absolute;left:0;top:0;width:100%;height:100%;pointer-events:none';
    this.ctx = this.canvas.getContext('2d');
    container.appendChild(this.canvas);
    this.k = 1; this.st = null; this.at = now(); this.R = null; this.spot = null; this.last = now();
    this.sfx = opts && opts.sound === false ? null : new Sfx(); this.done = {};
    if (this.sfx) this.sfx.vol = clamp(+this.cfg.sfx_volume || 0, 0, 1);
    this.resize();
    var self = this;
    loadAssets(function () { self.R = null; self.spot = null; });
    this._loop = function (t) { if (self.dead) return; try { self._frame(t); } catch (e) { self._err(e); } self._raf = raf(self._loop); };
    this._raf = raf(this._loop);
  }
  var P = DB.prototype;
  P._err = function (e) {
    if (this._errAt && now() - this._errAt < 5000) return;
    this._errAt = now();
    if (root.console) console.error('[derby] frame error:', e);
  };
  P.setConfig = function (cfg) { this.cfg = merge(DEFAULTS, cfg); if (this.sfx) this.sfx.vol = clamp(+this.cfg.sfx_volume || 0, 0, 1); };
  P.resize = function () {
    var r = this.box.getBoundingClientRect ? this.box.getBoundingClientRect() : { width: BASE_W };
    var dpr = root.devicePixelRatio || 1, w = r.width > 4 ? r.width : BASE_W;
    var k = Math.max(0.3, Math.min(2, w * dpr / BASE_W)), cw = Math.round(BASE_W * k), ch = Math.round(BASE_H * k);
    if (cw === this.canvas.width && ch === this.canvas.height) return;
    this.k = k; this.canvas.width = cw; this.canvas.height = ch;
  };
  P.setState = function (st) {
    this.st = st && typeof st === 'object' ? st : null;
    this.at = now();
    var g = this.st && this.st.game;
    if (!g || !g.script) { this.R = null; return; }
    if (this.R && this.R.id === g.id + '/' + g.script.seed) return;
    this.R = ASSETS.state === 'ready' ? buildRace(g) : null;
  };
  P.reset = function () { this.st = null; this.R = null; this.spot = null; };
  P.destroy = function () {
    this.dead = true; caf(this._raf);
    if (this.sfx && this.sfx.ctx) { try { this.sfx.ctx.close(); } catch (e) { /* closed */ } }
    if (this.canvas.parentNode) this.canvas.parentNode.removeChild(this.canvas);
  };
  P._elapsed = function (t) { var g = this.st && this.st.game; return g ? ((+g.elapsed_ms || 0) + (t - this.at)) / 1000 : 0; };
  P._frame = function (tNow) {
    var dt = Math.min(0.05, Math.max(0, (tNow - this.last) / 1000)); this.last = tNow;
    var c = this.ctx, cfg = this.cfg, st = this.st, g = st && st.game;
    TH = THEMES[cfg.theme] || THEMES.inferno;
    c.setTransform(1, 0, 0, 1, 0, 0); c.clearRect(0, 0, this.canvas.width, this.canvas.height);
    if (ASSETS.state !== 'ready') { loadAssets(function () {}); return; }
    c.setTransform(this.k, 0, 0, this.k, 0, 0);
    if (!g) { if (st && st.visible) drawIdle(c, st, cfg); return; }   // the host hides the slot when it isn't
    if (g.script && !this.R) this.R = buildRace(g);
    var e = this._elapsed(tNow), R = this.R;
    if (g.phase === 'betting') {
      var left = Math.max(0, ((+g.ends_in_ms || 0) - (tNow - this.at)) / 1000), total = Math.max(1, (+g.phase_ms || 1) / 1000);
      if (left <= 5.5) for (var n = 5; n >= 1; n--) this._cue(g.id + ':tick' + n, total - n, total - left, 'tick');
      drawBackdrop(c, null, -2600, e);
      drawLineup(c, this, g, dt, cfg, left);
      return;
    }
    if (!R) {                     // a lineup that ended without a race (nobody bet, or the game was stopped)
      if (g.phase === 'result') { drawBackdrop(c, null, -2600, e); drawResult(c, g, null, cfg); }
      return;
    }
    var t, photo = R.photo, hold = -1;
    if (g.phase === 'racing') {
      t = e - R.gate;
      if (photo && t >= photo.at) { if (t < photo.at + R.photoS) { hold = t - photo.at; t = photo.at; } else t -= R.photoS; }
    } else {
      t = R.lastT + 1.5 + Math.min(e, 2);
    }
    if (g.phase === 'racing') this._audio(R, t);
    if (hold >= 0) {
      if (!R.snap) this._snapshot(R, photo);
      drawPhoto(c, R, photo, hold, this.k);
      return;
    }
    raceAt(R, t);
    drawScene(c, R, t, dt, cfg);
    if (g.phase === 'racing') {
      if (t >= -R.gate + 0.5) R.runners.forEach(function (r) { if (t < r.T + 1.5) drawTag(c, r); });
      if (cfg.show_talk) drawBubbles(c, R, t, dt, cfg);
      drawHud(c, R, t, cfg);
      if (t >= R.winT && t < R.winT + 3) {
        var w = R.runners[R.S.order[0]];
        c.fillStyle = 'rgba(0,0,0,.45)'; c.fillRect(0, 160, BASE_W, 140);
        c.fillStyle = w.colour; c.font = 'bold 110px Impact, sans-serif'; c.textAlign = 'center'; c.fillText(w.name.toUpperCase() + ' WINS!', BASE_W / 2, 268);
      }
    } else {
      drawHud(c, R, t, cfg);
      drawResult(c, g, R, cfg);
    }
  };
  // ------------------------------------------------------------------ sound cues, from the script
  P._snd = function (name) { if (this.sfx && this.cfg.sfx) { try { this.sfx.play(name); } catch (e) { /* no audio */ } } };
  P._cue = function (key, due, tnow, snd) {
    // a one-shot sound at script time `due` (s): never replayed, never played late (a late join is quiet)
    if (this.done[key] || tnow < due) return;
    this.done[key] = 1;
    if (tnow - due < 0.5) this._snd(snd);
  };
  P._audio = function (R, t) {
    if (!this.sfx || !this.cfg.sfx) return;
    var self = this, id = R.id + ':';
    if (t < 0.5) { self._cue(id + 'g2', -2, t, 'beep'); self._cue(id + 'g1', -1, t, 'beep'); self._cue(id + 'go', 0, t, 'horn'); self._cue(id + 'go2', 0.1, t, 'roar'); }
    R.events.forEach(function (e, i) {
      if (t < e.t - 0.5 || t > e.t + 2) return;
      (EV_SOUND[e.type] || []).forEach(function (q, k) { self._cue(id + 'e' + i + ':' + k, e.t + q[1], t, q[0]); });
    });
    if (this.cfg.show_talk) R.talk.forEach(function (b, i) { if (b.pass && t >= b.t && t < b.t + 1) self._cue(id + 'p' + i, b.t, t, 'growl'); });
    if (R.photo) this._cue(id + 'photo', R.photo.at, t, 'shutter');
    this._cue(id + 'win', R.winT + (R.photo && R.photo.kind === 'win' ? 0.1 : 0), t, 'fanfare');
    this._cue(id + 'win2', R.winT + 0.2, t, 'roar');
    this._cue(id + 'last', R.lastT + 0.2, t, 'sad');
  };
  // the photo finish: the scene at the instant of the crossing, the line marked in red, kept as a picture
  P._snapshot = function (R, photo) {
    var cv = doc.createElement('canvas'), k = this.k;
    cv.width = this.canvas.width; cv.height = this.canvas.height;
    var c = cv.getContext('2d');
    c.setTransform(k, 0, 0, k, 0, 0);
    raceAt(R, photo.at);
    drawScene(c, R, photo.at, 0, this.cfg);
    var cam = R.cam, lineAt = function (y) { var a = sx(R.D, S_FAR, cam), b = sx(R.D, S_NEAR, cam); return a + (b - a) * (y - TRACK_TOP) / (TRACK_BOT - TRACK_TOP); };
    // the line through the nose of the hound that is on it (the winner; for a dead-last photo the one just ahead),
    // parallel to the painted line: the track is in perspective and a nose is well above its own row's paint
    var on = R.runners[photo.kind === 'last' ? photo.b : photo.a], fr = on.A && on.A.frames[on.f], m = fr && (fr.mouth || fr.head);
    var nx = on.left + NOSE * on.s, ny = m ? on.top + m[1] * on.s : on.y - 120 * on.s, slope = (lineAt(TRACK_BOT) - lineAt(TRACK_TOP)) / (TRACK_BOT - TRACK_TOP);
    R.snapLine = [[nx + slope * (TRACK_TOP - ny), TRACK_TOP], [nx + slope * (TRACK_BOT - ny), TRACK_BOT]];
    R.snapTags = [R.runners[photo.a], R.runners[photo.b]].map(function (r) { var p = r.pt('head'); return { x: p[0], y: p[1] - 60 * r.s, num: r.num, colour: r.colour }; });
    var ya = R.runners[photo.a].y, yb = R.runners[photo.b].y, top = Math.min(ya, yb) - 250 * S_NEAR, bot = Math.max(ya, yb) + 40;
    var sh = Math.max(380, bot - top), sw = sh * 16 / 9, cy = (top + bot) / 2;
    R.snap = cv; R.snapK = k; R.snapSrc = [lineAt(cy) - sw * 0.55, cy - sh / 2, sw, sh];
  };

  // ==================================================================== the editor's sample game + demo
  var DEMO_PLAYERS = [['ghostly', 3, 250], ['hexfan', 1, 100], ['pitboss', 5, 40], ['brimstone_babe', 3, 120], ['lurker42', 2, 15], ['vex', 6, 60]];
  function wholeState(game, idle) {
    return { state: game ? game.phase : 'idle', visible: true, spin: null, last: null, history: [], busy_ms: 0, announce: null, table: null,
      game: game, idle: idle || {}, preview: null };
  }
  function demoField(c) {
    var ros = ASSETS.roster.length ? ASSETS.roster : [{ id: 'cinder', name: 'Cinder' }, { id: 'gnash', name: 'Gnash' }, { id: 'terry', name: 'Three-Heads Terry' },
      { id: 'sparks', name: 'Sparks' }, { id: 'bubba', name: 'Beelzebubba' }, { id: 'harry', name: 'Hungry Harry' }];
    var n = clamp(Math.round(+c.field_size || 6), 3, 6), chances = [0.27, 0.21, 0.17, 0.14, 0.12, 0.09].slice(0, n), tot = chances.reduce(function (a, b) { return a + b; }, 0);
    var edge = +c.house_edge_pct || 0, notes = ['won the last 2', '1 win in 4', 'first race', '5 races without a win', 'won last time', '2 wins in 7'], streaks = [2, -1, 0, -5, 1, -2];
    return ros.slice(0, n).map(function (h, i) {
      var ch = chances[i] / tot;
      return { num: i + 1, id: h.id, name: h.name, short: h.short || h.name, trait: h.bio || h.note || '', row: [2, 0, 4, 1, 5, 3][i], chance: Math.round(ch * 1e6) / 1e6,
        mult: Math.max(1.01, Math.floor((1 - edge / 100) / ch * 100) / 100), note: notes[i], streak: streaks[i] };
    });
  }
  function sampleState(c, gid, ms) {
    var field = demoField(c), by = {}, players = {};
    DEMO_PLAYERS.forEach(function (p) {
      var num = Math.min(p[1], field.length), h = field[num - 1];
      var m = by[num] || (by[num] = { num: num, total: 0, users: [] }); m.total += p[2]; m.users.push({ user: p[0], amount: p[2] });
      var pl = players[p[0]] || (players[p[0]] = { user: p[0], stake: 0, bets: [] });
      pl.stake += p[2]; pl.bets.push({ num: num, hound: h.id, amount: p[2], mult: h.mult, pays: Math.floor(p[2] * h.mult) });
    });
    var markers = field.map(function (h) { return by[h.num] || { num: h.num, total: 0, users: [] }; });
    var plist = Object.keys(players).map(function (k) { return players[k]; }).sort(function (a, b) { return b.stake - a.stake; });
    return wholeState({ id: gid, test: false, phase: 'betting', ends_in_ms: ms, phase_ms: ms, elapsed_ms: 0, field: field, edge_pct: +c.house_edge_pct || 0,
      script: null, markers: markers, players: plist, at_risk: plist.reduce(function (a, p) { return a + p.stake; }, 0), last: null, outcome: null,
      summary: null, currency: String(c.currency || 'coins'), min_bet: c.min_bet || 1, max_bet: c.max_bet || 0, commands_text: c.commands_text || '' },
      { form: [], currency: String(c.currency || 'coins') });
  }
  // a short local race for ▶ Preview: the six of the sample, a couple of antics, no money
  function demoScript(field, seed) {
    var rng = mulberry(seed), n = field.length, order = [], left = field.map(function (h, i) { return i; }), runners = [], t = 12 * (1 + rng() * 0.04);
    while (left.length) order.push(left.splice(Math.floor(rng() * left.length), 1)[0]);
    order.forEach(function (i, k) { if (k) t += 0.15 + rng() * 0.6; runners[i] = { i: i, num: field[i].num, id: field[i].id, T: Math.round(t * 1e4) / 1e4,
      wob: [0.03 + rng() * 0.03, 0.02 + rng() * 0.02, 0.4 + rng() * 0.4, 1.2 + rng() * 0.8, rng() * 6.3, rng() * 6.3] }; });
    var events = [{ type: 'scorch', t: 4, dur: 2, a: order[1], b: null, me: ['scorch'], hazard: 'geyser', at: -230, line: 'MY ASS IS ON FIRE!', by: 'a', ticker: field[order[1]].name.toUpperCase() + ' GETS A LAVA ENEMA!' },
      { type: 'trip', t: 7, dur: 1.7, a: order[n - 1], b: null, me: ['tumble'], line: 'FUCK!', by: 'a', ticker: field[order[n - 1]].name.toUpperCase() + ' EATS DIRT!' }];
    var maxT = Math.max.apply(null, runners.map(function (r) { return r.T; }));
    return { seed: seed, length: 700 * 12, v: 700, dt: 1 / 60, gate_ms: 3200, photo_ms: 4600, duration_ms: Math.round((3.2 + maxT + 2) * 1000),
      runners: runners, order: order, events: events, photo: null, talk: { win: 'SUCK IT, LOSERS!', last: 'I meant to do that.', photo_win: '', photo_lost: '' } };
  }
  function demoSteps(c, gid) {
    var s0 = sampleState(c, gid, 3000), g0 = s0.game, script = demoScript(g0.field, Math.floor(Math.random() * 2147483647) + 1);
    var order = script.order.map(function (i) { return g0.field[i].num; }), win = g0.field[script.order[0]];
    var winners = [], losers = [];
    g0.players.forEach(function (p) { p.bets.forEach(function (b) { if (b.num === win.num) winners.push({ user: p.user, num: b.num, amount: b.amount, mult: b.mult, pays: b.pays }); else losers.push({ user: p.user, num: b.num, amount: b.amount }); }); });
    var last = { order: order, winner: { num: win.num, id: win.id, name: win.name, mult: win.mult }, last: order[order.length - 1], photo: null, winners: winners, losers: losers };
    function step(phase, ms, extra) {
      var g = merge(g0, { phase: phase, ends_in_ms: ms, phase_ms: ms, elapsed_ms: 0 });
      for (var k in extra) if (has(extra, k)) g[k] = extra[k];
      return { ms: ms, state: wholeState(g, s0.idle) };
    }
    return [step('betting', 3000), step('racing', script.duration_ms, { script: script }),
      step('result', Math.min(clamp(+c.result_seconds || 12, 3, 60) * 1000, 5000), { script: script, last: last, players: [] })];
  }

  // ------------------------------------------------------------------ registration
  var API = {
    BASE_W: BASE_W, BASE_H: BASE_H, DEFAULTS: merge(DEFAULTS, null), APPEARANCE: APPEARANCE.slice(),
    create: function (container, config, opts) { return new DB(container, config, opts); },
    sampleState: sampleState, demoSteps: demoSteps, demoScript: demoScript,
    simulate: simulate, replay: replay, sample: sample, curve: curve, buildRace: buildRace, raceAt: raceAt, ASSETS: ASSETS
  };
  HG.derby = API;
  if (typeof module === 'object' && module && module.exports) module.exports = API;
})(typeof window !== 'undefined' ? window : (typeof globalThis !== 'undefined' ? globalThis : this));
