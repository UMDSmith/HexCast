/*
 * Hexcast Games — Hexfall renderer                          static/hexfall.js
 *
 * Shared by the OBS overlay (/games/overlay) and the control panel (/games#hexfall).
 * Plain browser script: no modules, no dependencies, no build step.
 *
 * Registers window.HexGames.hexfall:
 *
 *   BASE_W, BASE_H            scene size in stage px at scale 1 (1280 x 860)
 *   THEMES                    { coven, ember, frost }
 *   DEFAULTS, APPEARANCE      same values / keys as the backend
 *   PRESETS                   the backend's built-in multiplier tables (risk -> rows -> [multipliers])
 *   slotsOf(rows, mults)      [{slot, mult, bust, pct, ...}] the way STATE.game.slots carries them (the editor's sample)
 *   create(container, config, opts) -> instance      opts: {sound: true}
 *
 * Branding: config `title` is the header's name ("Hexfall"), shown as text and shrunk to fit.
 *
 *   instance.setConfig(cfg)   instance.resize()    instance.setState(STATE)
 *   instance.reset()          instance.destroy()
 *
 * setState() takes the game's whole STATE (the server's state_view(): {state, visible, game,
 * idle, ...}); game.ends_in_ms / game.elapsed_ms must already be corrected for the time the
 * message sat in the host. Everything on screen is a function of the state and the time since
 * its phase started, so a late join (or the panel's mirror) lands on the same frame as every
 * other overlay.
 *
 * The drop (phase "dropping", `fall.ms` long) is choreographed backwards from its end, and it
 * animates exactly the server's path (fall.path: 0 = left, 1 = right at each peg). The seed only
 * varies cosmetic jitter (hop heights, hop times, sparks), never where the token goes:
 *   0 - tI            the rune ring above the pyramid charges, the token glows
 *   tI - tI+0.46 s    the token is released and falls onto the first peg
 *   then one hop per row: the skull tumbles from peg to peg (peg flash, spark,
 *                     squash, a click); the last two hops are slower
 *   D-0.85            it drops into its slot: the pocket flashes (beam, shock ring, confetti or ash)
 *   D                 the server settles: the result badge + each player's payout
 */
(function (root) {
  'use strict';

  var HG = root.HexGames || (root.HexGames = {});
  var BASE_W = 1280, BASE_H = 860;
  var TAU = Math.PI * 2, SIXTH = Math.PI / 3;

  var DEFAULTS = {
    x: 50, y: 50, scale: 0.85, theme: 'coven', title: 'Hexfall', show_rules: true, show_players: true,
    players_max: 8, show_odds: true, show_history: true, sfx: true, sfx_volume: 0.6, hide_when_idle: true,
    commands_text: '', drops: 3, rows: 12, risk: 'medium', drop_seconds: 9, result_seconds: 5, summary_seconds: 10,
    currency: 'coins'
  };
  var APPEARANCE = ['x', 'y', 'scale', 'theme', 'title', 'show_rules', 'show_players', 'players_max', 'show_odds',
    'show_history', 'sfx', 'sfx_volume'];

  // The backend's built-in tables (catalog/games_hexfall/hexfall.py PRESETS; a test keeps them equal).
  // risk -> rows -> one multiplier per slot (slot k = k right bounces). Big pays rare (outer slots), busts interleaved, RTP about 95%.
  var PRESETS = {"low": {"8": [5, 0, 0.4, 1, 1, 0.3, 3, 0, 0.3], "9": [1.2, 5, 0, 3, 0.3, 0.4, 1, 0.4, 0, 2.5], "10": [1.2, 0.4, 0, 3, 0.4, 1, 0.6, 0.4, 2, 0, 8], "11": [0.3, 8, 0, 1, 0.3, 0.2, 1, 3, 0, 1, 0, 3], "12": [0.6, 6, 0, 3, 0.5, 0.2, 1, 0.4, 2.5, 0, 4, 0, 10], "13": [0, 10, 0.2, 0.8, 1, 0.6, 0.2, 1.5, 2, 0, 0.5, 4, 0, 6], "14": [15, 0, 8, 0, 1.2, 0.8, 0.2, 1, 0.6, 2.5, 0, 3, 1.5, 0, 0.3], "15": [0.8, 15, 0, 1, 4, 0, 1.5, 0.4, 1.5, 0.2, 0.8, 1, 1.2, 0.5, 0, 5], "16": [0, 20, 0.3, 1.5, 0, 2.5, 1, 0.4, 1.2, 1, 0.8, 0, 2, 0.6, 3, 0, 3]}, "medium": {"8": [0, 8, 1, 0, 1, 0.8, 1.2, 0.3, 0.5], "9": [0, 10, 0.4, 3, 0, 0.2, 1, 0.3, 1.2, 1], "10": [10, 0, 0.5, 1.2, 0.7, 1.5, 0, 1.2, 1.5, 6, 0], "11": [12, 0, 0.5, 1.2, 0, 0.3, 1.5, 1, 3, 0, 5, 0.3], "12": [0, 15, 1.2, 0.8, 0, 1, 0.2, 3, 0, 0.2, 1, 0, 3], "13": [0.8, 0.3, 1.2, 0, 4, 0.6, 1, 0.5, 0, 1, 0, 10, 0, 15], "14": [20, 0, 10, 0, 2.5, 1, 0, 0.3, 1.5, 1.2, 0.3, 5, 0, 8, 0.6], "15": [5, 0, 5, 0.5, 1, 0, 1, 0.3, 3, 0, 0.2, 1.2, 0.3, 0.5, 25, 0], "16": [0, 25, 3, 0, 10, 0.2, 1, 0.8, 0, 1.2, 0.3, 2, 0, 1, 0, 6, 0]}, "high": {"8": [10, 0, 4, 0.7, 0, 1, 0.4, 0, 15], "9": [0.8, 1.5, 0.5, 1.5, 0, 0.5, 1, 0, 20, 1.2], "10": [4, 0, 2.5, 1, 0, 0.7, 1.2, 0, 0.2, 30, 0], "11": [1.2, 0.5, 0, 2, 0.2, 0, 1, 3, 0, 1, 0, 40], "12": [0, 50, 2, 0, 0.2, 1, 0, 0.3, 4, 0, 1, 0, 3], "13": [0.6, 0.5, 1, 0, 3, 1.5, 0.6, 0, 1.2, 0, 1, 0, 60, 0.3], "14": [75, 0, 1.2, 0.3, 1.5, 0, 3, 0.6, 0, 1, 0.3, 0, 5, 0, 4], "15": [1.5, 100, 0, 0.5, 1, 0, 0.2, 1, 0, 0.2, 1, 12, 0, 1.5, 0, 60], "16": [12, 0, 6, 2, 0, 0.3, 1, 0.2, 0, 4, 0, 0.2, 1, 0.5, 1.5, 0, 150]}};

  // Theme colours. Tiers colour a slot by its multiplier: bust (x0), low (< 1), mid (< 2.5), good (< 8),
  // hot (< 30), jack (30 and up); each is {fill, rim, text, glow}.
  var THEMES = {
    coven: {
      font: "'Trebuchet MS','Segoe UI',system-ui,sans-serif", fontTitle: "'Palatino Linotype','Book Antiqua',Palatino,Georgia,serif",
      bg0: '#1d0b36', bg1: '#06020d', lattice: 'rgba(190,120,255,.07)', halo: '#7a2cff', ring: '#b46bff',
      hi: 'rgba(30,13,62,.96)', accent: '#b980ff', accent2: '#56ff8f', ink: '#f5ecff', dim: '#b9a3dc', panel: 'rgba(15,7,30,.86)', line: 'rgba(185,128,255,.42)',
      good: '#6dffa8', bad: '#ff5c7a', flame0: '#f4ffe8', flame1: '#6bff5e', flame2: '#9a3dff',
      peg: { fill: '#2c1656', fill2: '#150a2c', rim: '#a56dff', glow: '#b980ff', hi: '#e6d4ff' },
      token: { core: '#f8fff0', mid: '#62ff7a', rim: '#8a3dff', glow: '#46ff80' },
      tier: {
        bust: { fill: '#2c0713', rim: '#a3203f', text: '#ff7d93', glow: '#ff2552' },
        low: { fill: '#1e1344', rim: '#6a58c4', text: '#c1b3ff', glow: '#7a63ff' },
        mid: { fill: '#0e2c3d', rim: '#2fc0cf', text: '#97f4ff', glow: '#2de0f2' },
        good: { fill: '#0e3b25', rim: '#38e183', text: '#a3ffc9', glow: '#3dff8f' },
        hot: { fill: '#3c2a07', rim: '#ffc54f', text: '#ffe7a3', glow: '#ffb82e' },
        jack: { fill: '#43093b', rim: '#ff55db', text: '#ffbcf3', glow: '#ff30d4' }
      }
    },
    ember: {
      font: "'Trebuchet MS','Segoe UI',system-ui,sans-serif", fontTitle: "'Palatino Linotype','Book Antiqua',Palatino,Georgia,serif",
      bg0: '#2d0d07', bg1: '#0c0302', lattice: 'rgba(255,150,70,.07)', halo: '#ff5a1f', ring: '#ff8a3d',
      hi: 'rgba(62,26,10,.96)', accent: '#ff8a3d', accent2: '#ffd34f', ink: '#fff3e6', dim: '#d9b79c', panel: 'rgba(24,8,4,.86)', line: 'rgba(255,138,61,.45)',
      good: '#ffd34f', bad: '#ff5a4a', flame0: '#fff6d8', flame1: '#ff9a2e', flame2: '#c4151c',
      peg: { fill: '#43160b', fill2: '#240b05', rim: '#ff9a52', glow: '#ff8a3d', hi: '#ffe0c4' },
      token: { core: '#fff7e0', mid: '#ffb02e', rim: '#ff4a1c', glow: '#ff8a2e' },
      tier: {
        bust: { fill: '#150609', rim: '#7d1a22', text: '#ff6670', glow: '#d3202b' },
        low: { fill: '#2b1409', rim: '#9c5a34', text: '#e6b896', glow: '#c47a45' },
        mid: { fill: '#2e2108', rim: '#d9a62b', text: '#ffe08a', glow: '#f2bd35' },
        good: { fill: '#3a2306', rim: '#ff9a2e', text: '#ffd29a', glow: '#ff8a1f' },
        hot: { fill: '#431305', rim: '#ff5a2a', text: '#ffb199', glow: '#ff4a1c' },
        jack: { fill: '#3d0a1c', rim: '#ff4d7e', text: '#ffb8cc', glow: '#ff2d6b' }
      }
    },
    frost: {
      font: "'Trebuchet MS','Segoe UI',system-ui,sans-serif", fontTitle: "'Palatino Linotype','Book Antiqua',Palatino,Georgia,serif",
      bg0: '#0d2540', bg1: '#030a14', lattice: 'rgba(130,210,255,.07)', halo: '#3d8bff', ring: '#79d6ff',
      hi: 'rgba(10,36,70,.96)', accent: '#79d6ff', accent2: '#e8f8ff', ink: '#eef9ff', dim: '#a5c4dc', panel: 'rgba(5,16,31,.86)', line: 'rgba(121,214,255,.42)',
      good: '#8dffd0', bad: '#ff6b8a', flame0: '#ffffff', flame1: '#78dcff', flame2: '#4a5cff',
      peg: { fill: '#14335a', fill2: '#0a1b33', rim: '#8bd8ff', glow: '#79d6ff', hi: '#e8f8ff' },
      token: { core: '#ffffff', mid: '#7fe3ff', rim: '#3d7bff', glow: '#6fd8ff' },
      tier: {
        bust: { fill: '#1b0c1d', rim: '#8a2a52', text: '#ff80a8', glow: '#ff2d6b' },
        low: { fill: '#12254a', rim: '#4a78c9', text: '#b2cdff', glow: '#5e8cff' },
        mid: { fill: '#0b3347', rim: '#31c7e6', text: '#a4f1ff', glow: '#2fe0ff' },
        good: { fill: '#0b3a3a', rim: '#3de3c5', text: '#a5fff0', glow: '#33ffd8' },
        hot: { fill: '#2f2f55', rim: '#c6c9ff', text: '#f0f1ff', glow: '#aab0ff' },
        jack: { fill: '#3a1246', rim: '#e08aff', text: '#f5cdff', glow: '#d65cff' }
      }
    }
  };

  // scene layout (base px): the pyramid sits in the middle, the panels in the columns beside it
  var BOARD = { cx: 640, w: 700, y0: 198, yLast: 704, slotTop: 748, portalY: 128 };

  // ------------------------------------------------------------------ helpers
  function has(o, k) { return Object.prototype.hasOwnProperty.call(o, k); }
  function merge(a, b) { var o = {}, k; for (k in a) if (has(a, k)) o[k] = a[k]; if (b) for (k in b) if (has(b, k) && b[k] != null) o[k] = b[k]; return o; }
  function clamp(v, a, b) { return v < a ? a : v > b ? b : v; }
  function lerp(a, b, t) { return a + (b - a) * t; }
  function smooth(a, b, x) { var t = clamp((x - a) / (b - a), 0, 1); return t * t * (3 - 2 * t); }
  function easeOut(t) { t = clamp(t, 0, 1); return 1 - Math.pow(1 - t, 3); }
  function easeInOut(t) { t = clamp(t, 0, 1); return t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2; }
  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function fmt(n) { n = Math.round(+n || 0); return String(Math.abs(n)).replace(/\B(?=(\d{3})+(?!\d))/g, ',').replace(/^/, n < 0 ? '−' : ''); }
  function signed(n) { n = Math.round(+n || 0); return (n > 0 ? '+' : '') + fmt(n); }
  // x25 / x0.5 (BUST for x0): rounded, never padded
  function fmtMult(m, bust) {
    m = +m || 0;
    if (m === 0) return bust === false ? '×0' : 'BUST';
    return '×' + (m >= 100 ? String(Math.round(m)) : m >= 10 ? String(Math.round(m * 10) / 10) : String(Math.round(m * 100) / 100));
  }
  function fmtPct(p) { p = +p || 0; return (p >= 10 ? p.toFixed(0) : p >= 1 ? p.toFixed(1) : p >= 0.1 ? p.toFixed(2) : p.toFixed(3)) + '%'; }
  function mulberry(a) {
    return function () {
      a |= 0; a = a + 0x6D2B79F5 | 0;
      var t = Math.imul(a ^ a >>> 15, 1 | a);
      t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t;
      return ((t ^ t >>> 14) >>> 0) / 4294967296;
    };
  }
  // integer -> [0, 1), deterministic (stateless particles)
  function hash(n) { var x = Math.sin(n * 127.1 + 311.7) * 43758.5453; return x - Math.floor(x); }
  function now() { return (root.performance && root.performance.now) ? root.performance.now() : Date.now(); }
  var raf = root.requestAnimationFrame ? function (f) { return root.requestAnimationFrame(f); } : function (f) { return setTimeout(function () { f(now()); }, 16); };
  var caf = root.cancelAnimationFrame ? function (id) { root.cancelAnimationFrame(id); } : function (id) { clearTimeout(id); };
  function rgb(s) {
    s = String(s || '#fff');
    if (s.charAt(0) !== '#') return [255, 255, 255];
    if (s.length === 4) s = '#' + s[1] + s[1] + s[2] + s[2] + s[3] + s[3];
    return [parseInt(s.substr(1, 2), 16), parseInt(s.substr(3, 2), 16), parseInt(s.substr(5, 2), 16)];
  }
  function rgba(s, a) { var c = rgb(s); return 'rgba(' + c[0] + ',' + c[1] + ',' + c[2] + ',' + (a == null ? 1 : a) + ')'; }
  function mixHex(a, b, t) {
    var x = rgb(a), y = rgb(b), h = '#';
    for (var i = 0; i < 3; i++) { var v = Math.round(lerp(x[i], y[i], t)); h += (v < 16 ? '0' : '') + v.toString(16); }
    return h;
  }
  function doc() { return root.document || null; }
  function makeCanvas(w, h) {
    var cv = doc().createElement('canvas');
    cv.width = Math.max(1, Math.round(w)); cv.height = Math.max(1, Math.round(h));
    return cv;
  }

  // ------------------------------------------------------------------ the table (the same math as hexfall.py)
  function slotsOf(rows, mults) {
    var total = Math.pow(2, rows), w = 1, out = [], k;
    for (k = 0; k <= rows; k++) {
      if (k > 0) w = w * (rows - k + 1) / k;
      var m = +mults[k] || 0, p = Math.round(w) / total;
      out.push({ slot: k, mult: m, bust: m === 0, ways: Math.round(w), of: total, probability: p, pct: p * 100, rtp_pct: p * m * 100 });
    }
    return out;
  }
  function rtpOf(slots) { var s = 0; slots.forEach(function (x) { s += x.probability * x.mult; }); return Math.floor(s * 10000) / 100; }
  function tableOf(rows, risk) {
    var t = PRESETS[risk] && PRESETS[risk][rows];
    return t || PRESETS.medium[12];
  }
  function tierOf(m) { return m <= 0 ? 'bust' : m < 1 ? 'low' : m < 2.5 ? 'mid' : m < 8 ? 'good' : m < 30 ? 'hot' : 'jack'; }
  // payout ladder: the slots grouped by multiplier (one row per distinct value)
  function ladderOf(slots) {
    var by = {}, out = [];
    slots.forEach(function (s) {
      var k = String(s.mult);
      if (!by[k]) { by[k] = { mult: s.mult, pct: 0, n: 0, bust: !!s.bust, slots: [] }; out.push(by[k]); }
      by[k].pct += s.pct; by[k].n++; by[k].slots.push(s.slot);
    });
    out.sort(function (a, b) { return b.mult - a.mult; });
    return out;
  }

  // ------------------------------------------------------------------ geometry
  // rows of pegs in a triangular lattice: row r has r + 1 pegs, (2j - r) * pitch / 2 off the centre;
  // the slot k (k right bounces) sits under the lattice's next row at (2k - rows) * pitch / 2.
  function makeGeo(R) {
    var pitch = BOARD.w / (R + 1);
    var dy = (BOARD.yLast - BOARD.y0) / (R - 1);
    var rp = clamp(pitch * 0.185, 6, 13);
    var rt = clamp(pitch * 0.36, 11, 26);
    var a = Math.min(pitch * 0.47, 31);                    // pocket circumradius (flat-top hexagon)
    return {
      R: R, pitch: pitch, dy: dy, rp: rp, rt: rt, a: a, ph: a * 1.732, contact: rt + rp * 0.78,
      pegX: function (r, j) { return BOARD.cx + (2 * j - r) * pitch / 2; },
      pegY: function (r) { return BOARD.y0 + r * dy; },
      slotX: function (k) { return BOARD.cx + (2 * k - R) * pitch / 2; }
    };
  }

  // ------------------------------------------------------------------ the drop (the server's path -> a timeline)
  // Everything here is a pure function of (path, seed, D, t): late joins and the panel's mirror land on the same frame.
  function buildPlan(G, path, seed, D) {
    var R = G.R, rnd = mulberry((seed | 0) ^ 0x51ed270b), i;
    var settle = 0.85, f0 = 0.46, introMin = 0.9;
    var avail = Math.max(2, D - settle - f0);
    var hop = clamp((avail - introMin) / R, 0.28, 0.62);
    var tI = Math.max(0.3, avail - R * hop);
    var w = [], sum = 0;
    for (i = 0; i < R; i++) { var wi = 1 + (rnd() - 0.5) * 0.18; if (i === R - 1) wi *= 1.3; else if (i === R - 2) wi *= 1.12; w.push(wi); sum += wi; }
    var P = [], pj = [], j = 0;
    for (i = 0; i < R; i++) { P.push({ x: G.pegX(i, j), y: G.pegY(i) - G.contact }); pj.push(j); j += path[i] ? 1 : 0; }
    P.push({ x: G.slotX(j), y: BOARD.slotTop - G.rt * 0.3 });
    var imp = [tI + f0], lift = [], dirs = [], rot = [0], spin = [];
    for (i = 0; i < R; i++) {
      imp.push(imp[i] + w[i] / sum * R * hop);
      lift.push(G.dy * (0.36 + rnd() * 0.16));
      dirs.push(path[i] ? 1 : -1);
      spin.push(TAU * (0.4 + rnd() * 0.22));                    // the skull tumbles 145-225 degrees on every bounce, the way it was kicked
      rot.push(rot[i] + dirs[i] * spin[i]);
    }
    return { G: G, R: R, path: path, D: D, tI: tI, f0: f0, hop: hop, settle: settle, imp: imp, P: P, pj: pj, lift: lift,
      dirs: dirs, rot: rot, spin: spin, slot: j, seed: seed | 0 };
  }

  // the token at time t (seconds since the drop began)
  function planAt(pl, t) {
    var R = pl.R, P = pl.P, imp = pl.imp, G = pl.G;
    var o = { x: BOARD.cx, y: BOARD.portalY, rot: 0, sx: 1, sy: 1, a: 1, mode: 'charge', hop: -1, u: 0, vx: 0, vy: 0 };
    var u, i;
    if (t < pl.tI) {
      o.u = t / pl.tI; o.y = BOARD.portalY + Math.sin(t * 3.1) * 3 * (1 - o.u * 0.6); o.rot = Math.sin(t * 2.4) * 0.14;
      return o;
    }
    if (t < imp[0]) {                                           // released: falls onto the first peg
      u = (t - pl.tI) / pl.f0; o.mode = 'fall'; o.u = u;
      o.x = P[0].x; o.y = lerp(BOARD.portalY, P[0].y, u * u);
      o.rot = Math.sin(u * Math.PI) * -0.45 + 0.0; o.sy = 1 + 0.16 * u; o.sx = 1 - 0.07 * u; o.vy = 2 * u;
      return o;
    }
    i = 0;
    while (i < R && t >= imp[i + 1]) i++;
    if (i < R) {                                                // hop i: peg i -> peg i + 1 (or the slot)
      var T = imp[i + 1] - imp[i], A = P[i], B = P[i + 1];
      u = (t - imp[i]) / T; o.mode = 'hop'; o.hop = i; o.u = u;
      o.x = lerp(A.x, B.x, u);
      o.y = lerp(A.y, B.y, u) - 4 * pl.lift[i] * u * (1 - u);
      o.vx = (B.x - A.x) / T; o.vy = ((B.y - A.y) - 4 * pl.lift[i] * (1 - 2 * u)) / T;
      o.rot = pl.rot[i] + pl.dirs[i] * pl.spin[i] * easeInOut(u);
      var sp = Math.sqrt(o.vx * o.vx + o.vy * o.vy), st = clamp(sp / 520, 0, 1) * 0.12;
      o.sy = 1 + st; o.sx = 1 - st * 0.6;
    } else {                                                    // in the pocket: two damped hops, then it dissolves into the glow
      var s = t - imp[R], B2 = P[R];
      o.mode = 'rest'; o.u = s; o.x = B2.x;
      o.rot = lerp(pl.rot[R], Math.round(pl.rot[R] / TAU) * TAU, smooth(0, 0.35, s));   // it settles upright in its pocket
      o.y = B2.y - 15 * Math.abs(Math.sin(s * 8.5)) * Math.exp(-s * 4.2) + 4 * smooth(0.15, 0.7, s);
      o.a = 1 - smooth(0.32, 0.8, s);
      var k = 1 - smooth(0.3, 0.8, s) * 0.55; o.sx = k; o.sy = k;
    }
    // squash on every impact (a touch early, then it recovers)
    var q = 0, d, j2;
    for (j2 = Math.max(0, i - 1); j2 <= Math.min(R, i + 1); j2++) {
      d = t - imp[j2];
      var qq = d < 0 ? (d > -0.04 ? (1 + d / 0.04) * 0.55 : 0) : (d < 0.3 ? Math.exp(-d / 0.065) : 0);
      if (qq > q) q = qq;
    }
    if (q > 0) { o.sy *= 1 - 0.3 * q; o.sx *= 1 + 0.24 * q; }
    return o;
  }

  // ------------------------------------------------------------------ canvas art
  function hexPath(c, x, y, r, rot) {                           // flat-top hexagon (a vertex points along +x)
    var i, a;
    c.beginPath();
    for (i = 0; i < 6; i++) {
      a = (rot || 0) + i * SIXTH;
      if (i) c.lineTo(x + Math.cos(a) * r, y + Math.sin(a) * r); else c.moveTo(x + Math.cos(a) * r, y + Math.sin(a) * r);
    }
    c.closePath();
  }

  var GLOWS = {};
  function glowSprite(color) {                                  // a soft round light, drawn scaled and additive
    var key = String(color);
    if (GLOWS[key]) return GLOWS[key];
    var cv = makeCanvas(96, 96), g = cv.getContext('2d'), gr = g.createRadialGradient(48, 48, 0, 48, 48, 48);
    gr.addColorStop(0, rgba(color, 1)); gr.addColorStop(0.25, rgba(color, 0.55)); gr.addColorStop(0.6, rgba(color, 0.16)); gr.addColorStop(1, rgba(color, 0));
    g.fillStyle = gr; g.fillRect(0, 0, 96, 96);
    return (GLOWS[key] = cv);
  }
  var GALPHA = 1;                                               // multiplies every glow() (the portal fades while the badge shows)
  function glow(c, color, x, y, r, a) {
    a *= GALPHA;
    if (a <= 0.003 || r <= 0.5) return;
    c.globalAlpha = a > 1 ? 1 : a;
    c.drawImage(glowSprite(color), x - r, y - r, r * 2, r * 2);
    c.globalAlpha = 1;
  }
  function star(c, x, y, r, rot) {                              // a four-point sparkle
    c.beginPath();
    for (var i = 0; i < 8; i++) {
      var a = rot + i * Math.PI / 4, rr = i % 2 ? r * 0.22 : r;
      if (i) c.lineTo(x + Math.cos(a) * rr, y + Math.sin(a) * rr); else c.moveTo(x + Math.cos(a) * rr, y + Math.sin(a) * rr);
    }
    c.closePath();
  }

  // a tiny skull for a bust slot (s = half height)
  function skull(c, x, y, s, fill, dark) {
    c.save(); c.translate(x, y);
    c.fillStyle = fill;
    c.beginPath();
    c.moveTo(-s * 0.78, -s * 0.05);
    c.bezierCurveTo(-s * 0.8, -s * 1.05, s * 0.8, -s * 1.05, s * 0.78, -s * 0.05);
    c.lineTo(s * 0.55, s * 0.28); c.lineTo(s * 0.5, s * 0.82); c.lineTo(-s * 0.5, s * 0.82); c.lineTo(-s * 0.55, s * 0.28);
    c.closePath(); c.fill();
    c.fillStyle = dark;
    c.beginPath(); c.ellipse(-s * 0.33, -s * 0.1, s * 0.22, s * 0.26, 0, 0, TAU); c.ellipse(s * 0.33, -s * 0.1, s * 0.22, s * 0.26, 0, 0, TAU); c.fill();
    c.beginPath(); c.moveTo(0, s * 0.12); c.lineTo(-s * 0.1, s * 0.34); c.lineTo(s * 0.1, s * 0.34); c.closePath(); c.fill();
    c.strokeStyle = dark; c.lineWidth = Math.max(1, s * 0.09); c.lineCap = 'round';
    c.beginPath();
    c.moveTo(-s * 0.2, s * 0.58); c.lineTo(-s * 0.2, s * 0.82); c.moveTo(0, s * 0.58); c.lineTo(0, s * 0.82); c.moveTo(s * 0.2, s * 0.58); c.lineTo(s * 0.2, s * 0.82);
    c.stroke();
    c.restore();
  }

  // ---- the static layer: the room, the pyramid and its pegs (cached; rebuilt on resize / theme / rows)
  function drawStatic(c, th, G) {
    var i, j, r, W = BASE_W, H = BASE_H;
    // the room
    var gr = c.createLinearGradient(0, 0, 0, H);
    gr.addColorStop(0, th.bg0); gr.addColorStop(1, th.bg1);
    c.save();
    c.beginPath(); c.moveTo(26, 0); c.lineTo(W - 26, 0); c.quadraticCurveTo(W, 0, W, 26); c.lineTo(W, H - 26); c.quadraticCurveTo(W, H, W - 26, H);
    c.lineTo(26, H); c.quadraticCurveTo(0, H, 0, H - 26); c.lineTo(0, 26); c.quadraticCurveTo(0, 0, 26, 0); c.closePath();
    c.fillStyle = gr; c.fill();
    c.clip();
    // honeycomb wallpaper
    c.strokeStyle = th.lattice; c.lineWidth = 1.2;
    var hr = 34, hx = hr * 1.5, hy = hr * 1.732;
    for (i = -1; i < W / hx + 2; i++) {
      for (j = -1; j < H / hy + 2; j++) {
        hexPath(c, i * hx, j * hy + (i % 2 ? hy / 2 : 0), hr - 1.5, 0); c.stroke();
      }
    }
    // a soft light behind the pyramid, a vignette over the corners
    var cx = BOARD.cx, cy = 470;
    var rg = c.createRadialGradient(cx, cy, 20, cx, cy, 470);
    rg.addColorStop(0, rgba(th.halo, 0.32)); rg.addColorStop(0.55, rgba(th.halo, 0.09)); rg.addColorStop(1, rgba(th.halo, 0));
    c.fillStyle = rg; c.fillRect(0, 0, W, H);
    var vg = c.createRadialGradient(W / 2, H / 2, H * 0.45, W / 2, H / 2, W * 0.66);
    vg.addColorStop(0, 'rgba(0,0,0,0)'); vg.addColorStop(1, 'rgba(0,0,0,.62)');
    c.fillStyle = vg; c.fillRect(0, 0, W, H);

    // the pyramid: a faint triangle with glowing rails, so the lattice reads as a funnel
    var apexY = BOARD.y0 - 40, rail = G.pitch * 0.62;
    var xl = BOARD.cx - BOARD.w / 2 - 6, xr = BOARD.cx + BOARD.w / 2 + 6, yb = BOARD.slotTop + 2;
    c.beginPath(); c.moveTo(BOARD.cx - rail, apexY); c.lineTo(xl, yb); c.lineTo(xr, yb); c.lineTo(BOARD.cx + rail, apexY); c.closePath();
    var pg = c.createLinearGradient(0, apexY, 0, yb);
    pg.addColorStop(0, rgba(th.halo, 0.02)); pg.addColorStop(1, rgba(th.halo, 0.15));
    c.fillStyle = pg; c.fill();
    c.lineJoin = 'round'; c.lineCap = 'round';
    [[-1, xl], [1, xr]].forEach(function (side) {
      c.beginPath(); c.moveTo(BOARD.cx + side[0] * rail, apexY); c.lineTo(side[1], yb);
      c.strokeStyle = rgba(th.accent, 0.16); c.lineWidth = 9; c.stroke();
      c.strokeStyle = rgba(th.accent, 0.5); c.lineWidth = 2; c.stroke();
    });
    // the pegs
    var spr = pegSprite(th, G.rp, 1);
    for (r = 0; r < G.R; r++) {
      for (j = 0; j <= r; j++) c.drawImage(spr.cv, G.pegX(r, j) - spr.o, G.pegY(r) - spr.o, spr.cv.width / spr.k, spr.cv.height / spr.k);
    }
    c.restore();
    // the frame
    c.beginPath(); c.moveTo(26, 1); c.lineTo(W - 26, 1); c.quadraticCurveTo(W - 1, 1, W - 1, 26); c.lineTo(W - 1, H - 26); c.quadraticCurveTo(W - 1, H - 1, W - 26, H - 1);
    c.lineTo(26, H - 1); c.quadraticCurveTo(1, H - 1, 1, H - 26); c.lineTo(1, 26); c.quadraticCurveTo(1, 1, 26, 1); c.closePath();
    c.strokeStyle = rgba(th.accent, 0.5); c.lineWidth = 2; c.stroke();
  }

  var PEGS = {};
  function pegSprite(th, rp, k) {                               // a glowing hex peg, rendered once per theme + size
    var key = th.peg.rim + '|' + rp.toFixed(2) + '|' + k;
    if (PEGS[key]) return PEGS[key];
    var pad = Math.ceil(rp * 2.4), size = (rp + pad) * 2, S = 3;       // supersampled: the sprite is shrunk back
    var cv = makeCanvas(size * S, size * S), g = cv.getContext('2d'), o = size / 2;
    g.scale(S, S);
    var gr = g.createRadialGradient(o, o, rp * 0.4, o, o, rp * 2.4);
    gr.addColorStop(0, rgba(th.peg.glow, 0.5)); gr.addColorStop(1, rgba(th.peg.glow, 0));
    g.fillStyle = gr; g.fillRect(0, 0, size, size);
    hexPath(g, o, o, rp, 0);
    var fg = g.createLinearGradient(o, o - rp, o, o + rp);
    fg.addColorStop(0, th.peg.fill); fg.addColorStop(1, th.peg.fill2);
    g.fillStyle = fg; g.fill();
    g.lineJoin = 'round'; g.strokeStyle = th.peg.rim; g.lineWidth = Math.max(1.6, rp * 0.24); g.stroke();
    hexPath(g, o, o, rp * 0.52, 0); g.strokeStyle = rgba(th.peg.hi, 0.5); g.lineWidth = Math.max(0.8, rp * 0.1); g.stroke();
    g.fillStyle = rgba(th.peg.hi, 0.85); g.beginPath(); g.arc(o - rp * 0.3, o - rp * 0.36, Math.max(0.8, rp * 0.14), 0, TAU); g.fill();
    return (PEGS[key] = { cv: cv, o: o, k: S });
  }

  // ------------------------------------------------------------------ sound
  function Sfx() { this.ctx = null; this.master = null; this.vol = 0.6; }
  Sfx.prototype.ac = function () {
    if (!this.ctx) {
      var AC = root.AudioContext || root.webkitAudioContext;
      if (!AC) return null;
      try { this.ctx = new AC(); this.master = this.ctx.createGain(); this.master.gain.value = this.vol; this.master.connect(this.ctx.destination); }
      catch (e) { this.ctx = null; return null; }
    }
    if (this.ctx.state === 'suspended') { try { var p = this.ctx.resume(); if (p && p.catch) p.catch(function () { }); } catch (e) { } }
    return this.ctx;
  };
  Sfx.prototype.setVolume = function (v) {
    this.vol = clamp(+v || 0, 0, 1);
    if (this.master) { try { this.master.gain.setTargetAtTime(this.vol, this.ctx.currentTime, 0.03); } catch (e) { } }
  };
  Sfx.prototype._buf = function (c) {
    if (this._noise) return this._noise;
    var b = c.createBuffer(1, c.sampleRate, c.sampleRate), d = b.getChannelData(0), r = mulberry(7);
    for (var i = 0; i < d.length; i++) d[i] = r() * 2 - 1;
    return (this._noise = b);
  };
  Sfx.prototype._out = function (c, pan) {
    if (pan && c.createStereoPanner) {
      var p = c.createStereoPanner(); p.pan.value = clamp(pan, -1, 1); p.connect(this.master); return p;
    }
    return this.master;
  };
  Sfx.prototype.noise = function (dur, type, f0, f1, q, gain, delay, pan) {
    var c = this.ac(); if (!c) return;
    var t = c.currentTime + (delay || 0), s = c.createBufferSource(), f = c.createBiquadFilter(), g = c.createGain();
    s.buffer = this._buf(c); f.type = type; f.frequency.setValueAtTime(f0, t);
    if (f1) f.frequency.exponentialRampToValueAtTime(Math.max(20, f1), t + dur);
    f.Q.value = q || 1;
    g.gain.setValueAtTime(gain, t); g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
    s.connect(f); f.connect(g); g.connect(this._out(c, pan));
    s.start(t, Math.random() * 0.5); s.stop(t + dur + 0.02);
  };
  Sfx.prototype.tone = function (freq, dur, gain, type, to, delay, pan) {
    var c = this.ac(); if (!c) return;
    var t = c.currentTime + (delay || 0), o = c.createOscillator(), g = c.createGain();
    o.type = type || 'sine'; o.frequency.setValueAtTime(freq, t);
    if (to) o.frequency.exponentialRampToValueAtTime(Math.max(20, to), t + dur);
    g.gain.setValueAtTime(0.0001, t); g.gain.exponentialRampToValueAtTime(gain, t + 0.006); g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
    o.connect(g); g.connect(this._out(c, pan)); o.start(t); o.stop(t + dur + 0.03);
  };
  var PENTA = [0, 2, 4, 7, 9];                                    // the pegs play a rising pentatonic scale, row by row
  Sfx.prototype.play = function (name, a, b) {
    switch (name) {
      case 'hit':                                                 // a = row index (0..), b = {rows, dir}
        var n = Math.round(a * 9 / Math.max(1, (b.rows - 1))), semi = PENTA[n % 5] + 12 * Math.floor(n / 5);
        var f = 392 * Math.pow(2, semi / 12), pan = b.dir * 0.3;
        this.noise(0.016, 'bandpass', 4200 + Math.random() * 900, 0, 5, 0.34, 0, pan);
        this.tone(f, 0.26, 0.2, 'sine', 0, 0, pan); this.tone(f * 2.003, 0.13, 0.07, 'triangle', 0, 0, pan);
        break;
      case 'charge': this.tone(110, a, 0.14, 'sawtooth', 520); this.tone(220, a, 0.1, 'sine', 1040); this.noise(a, 'bandpass', 300, 2600, 1.5, 0.22); break;
      case 'release': this.noise(0.25, 'highpass', 1800, 6000, 0.7, 0.3); this.tone(880, 0.25, 0.14, 'triangle', 220); break;
      case 'win': {
        var base = a === 'jack' ? 523 : a === 'hot' ? 494 : a === 'good' ? 440 : 392, ch = a === 'mid' ? [1, 1.5] : [1, 1.25, 1.5, 2];
        for (var i = 0; i < ch.length; i++) this.tone(base * ch[i], 0.5 + i * 0.08, 0.18, 'triangle', 0, i * 0.09);
        if (a === 'hot' || a === 'jack') {
          this.tone(base * 3, 0.9, 0.1, 'sine', 0, 0.35); this.tone(60, 0.5, 0.5, 'sine', 35); this.noise(0.9, 'highpass', 3000, 9000, 0.6, 0.12, 0.1);
          if (a === 'jack') for (var j = 0; j < 6; j++) this.tone(base * 2 * (1 + j * 0.25), 0.3, 0.1, 'triangle', 0, 0.45 + j * 0.07);
        }
        break;
      }
      case 'low': this.tone(330, 0.3, 0.16, 'triangle', 240); this.noise(0.1, 'lowpass', 900, 0, 1, 0.3); break;
      case 'bust': this.tone(130, 0.7, 0.4, 'sawtooth', 42); this.tone(98, 0.8, 0.25, 'square', 40, 0.02); this.noise(0.8, 'lowpass', 700, 90, 0.8, 0.5); break;
      case 'beep': this.tone(880, 0.07, 0.15, 'square'); break;
    }
  };

  // ------------------------------------------------------------------ styles
  var CSS_DONE = false;
  function injectCss() {
    if (CSS_DONE || !doc()) return;
    CSS_DONE = true;
    var css = [
      '.hfx{position:absolute;inset:0;font-family:var(--hfx-font);color:var(--hfx-ink);pointer-events:none;}',
      '.hfx,.hfx *{box-sizing:border-box}',
      '.hfx canvas{position:absolute;left:0;top:0;width:100%;height:100%;}',
      '.hfx-top{position:absolute;left:22px;right:22px;top:12px;height:54px;display:flex;align-items:center;gap:16px;}',
      '.hfx-title{display:flex;align-items:center;gap:12px;min-width:0;max-width:430px;}',
      '.hfx-logo{flex:none;width:38px;height:38px;filter:drop-shadow(0 0 7px var(--hfx-accent));}',
      '.hfx-tt{min-width:0}',
      '.hfx-name{display:block;font-family:var(--hfx-title);font-weight:900;font-size:30px;line-height:1.06;letter-spacing:.14em;text-transform:uppercase;color:var(--hfx-ink);text-shadow:0 0 14px var(--hfx-accent),0 2px 0 rgba(0,0,0,.6);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;padding:6px 10px;margin:-6px -10px}',
      '.hfx-sub{display:block;font-size:11px;letter-spacing:.24em;text-transform:uppercase;color:var(--hfx-dim);margin-top:-2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}',
      '.hfx-chips{display:flex;gap:7px;flex:1;justify-content:center;min-width:0}',
      // hexagonal chips: the outer shape is the border colour (--hx), the inner one (::before) the fill
      '.hfx-hx{position:relative;isolation:isolate;background:var(--hx,var(--hfx-line));clip-path:polygon(var(--cut,10px) 0,calc(100% - var(--cut,10px)) 0,100% 50%,calc(100% - var(--cut,10px)) 100%,var(--cut,10px) 100%,0 50%)}',
      '.hfx-hx::before{content:"";position:absolute;inset:1.5px;z-index:-1;background:var(--hxf,rgba(9,4,20,.92));clip-path:polygon(var(--cut,10px) 0,calc(100% - var(--cut,10px)) 0,100% 50%,calc(100% - var(--cut,10px)) 100%,var(--cut,10px) 100%,0 50%)}',
      '.hfx-chip{--cut:11px;min-width:82px;padding:5px 14px 6px;text-align:center;font-size:15px;font-weight:800;opacity:.6}',
      '.hfx-chip b{display:block;font-size:10px;font-weight:700;color:var(--hfx-dim);letter-spacing:.14em}',
      '.hfx-chip.cur{opacity:1;color:var(--hfx-ink);--hx:var(--hfx-accent);--hxf:var(--hfx-hi)}',
      '.hfx-chip.cur b{color:var(--hfx-accent2)}',
      '.hfx-chip.done{opacity:1}',
      '.hfx-timer{min-width:160px;text-align:right;white-space:nowrap}',
      '.hfx-timer b{display:block;font-size:11px;letter-spacing:.18em;color:var(--hfx-dim);text-transform:uppercase}',
      '.hfx-timer span{font-size:32px;font-weight:900;line-height:1;font-variant-numeric:tabular-nums;text-shadow:0 0 12px var(--hfx-accent)}',
      '.hfx-timer.last span{color:var(--hfx-bad);text-shadow:0 0 12px var(--hfx-bad);animation:hfx-pulse .5s ease-in-out infinite alternate}',
      '@keyframes hfx-pulse{from{transform:scale(1)}to{transform:scale(1.13)}}',
      '.hfx-col{position:absolute;top:84px;width:236px;display:flex;flex-direction:column;gap:12px}',
      '.hfx-left{left:20px}.hfx-right{right:20px}',
      '.hfx-box{background:var(--hfx-panel);border:1px solid var(--hfx-line);border-radius:14px;padding:11px 13px 12px;box-shadow:0 10px 30px rgba(0,0,0,.45),0 0 22px rgba(0,0,0,.25) inset;}',
      '.hfx-box h4{margin:0 0 7px;font-size:11.5px;letter-spacing:.2em;text-transform:uppercase;color:var(--hfx-accent);display:flex;justify-content:space-between;gap:8px}',
      '.hfx-box h4 small{font-size:10.5px;letter-spacing:.06em;color:var(--hfx-dim);font-weight:600;text-transform:none}',
      '.hfx-odds table{width:100%;border-collapse:collapse;font-size:13.5px;font-variant-numeric:tabular-nums}',
      '.hfx-odds td{padding:2.5px 3px;border-top:1px solid rgba(255,255,255,.06);white-space:nowrap}',
      '.hfx-odds td:first-child{font-weight:900;width:46%}',
      '.hfx-odds td:last-child{text-align:right;color:var(--hfx-dim);font-size:12.5px}',
      '.hfx-odds tr.hit td{background:rgba(255,255,255,.12);color:var(--hfx-ink)}',
      '.hfx-odds tr.hit td:last-child{color:var(--hfx-ink)}',
      '.hfx-rtp{margin-top:7px;padding-top:7px;border-top:1px solid var(--hfx-line);font-size:12.5px;color:var(--hfx-dim);line-height:1.55}',
      '.hfx-rtp div{display:flex;justify-content:space-between;white-space:nowrap}.hfx-rtp b{color:var(--hfx-ink);font-size:14px}',
      '.hfx-players{overflow:hidden}',
      '.hfx-p{display:flex;justify-content:space-between;align-items:baseline;gap:8px;font-size:14.5px;padding:3.5px 0;border-top:1px solid rgba(255,255,255,.07)}',
      '.hfx-p u{text-decoration:none;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:118px}',
      '.hfx-p i{font-style:normal;font-weight:800;font-variant-numeric:tabular-nums;white-space:nowrap}',
      '.hfx-p small{color:var(--hfx-dim);font-size:11px;font-weight:600}',
      '.hfx-p .pos{color:var(--hfx-good)}.hfx-p .neg{color:var(--hfx-bad)}.hfx-p .eq{color:var(--hfx-dim)}',
      '.hfx-empty{color:var(--hfx-dim);font-size:13px;padding:4px 0}',
      '.hfx-rules p{margin:3px 0 4px;font-size:13.5px;line-height:1.36}',
      '.hfx-rules em{font-style:normal;font-weight:900;color:var(--hfx-accent2)}',
      '.hfx-rules .cmd{margin-top:6px;font-size:13px;color:var(--hfx-dim)}',
      '.hfx-rules .warn{margin-top:7px;font-weight:900;color:var(--hfx-bad);font-size:15.5px;letter-spacing:.05em;animation:hfx-pulse .5s ease-in-out infinite alternate;transform-origin:left center}',
      '.hfx-strip{position:absolute;left:292px;right:292px;bottom:14px;height:30px;display:flex;align-items:center;gap:10px;transition:opacity .3s}',
      '.hfx-strip>b{font-size:10.5px;letter-spacing:.2em;color:var(--hfx-dim);white-space:nowrap}',
      '.hfx-hits{display:flex;gap:5px;flex:1;overflow:hidden}',
      '.hfx-hit{--cut:8px;flex:none;min-width:46px;padding:4px 10px;text-align:center;font-size:13px;font-weight:900;font-variant-numeric:tabular-nums}',
      '.hfx-hit:first-child{--hxf:var(--hfx-hi)}',
      '.hfx-badge{position:absolute;left:50%;top:76px;width:520px;margin-left:-260px;text-align:center;white-space:nowrap;transition:opacity .25s,transform .35s cubic-bezier(.2,.9,.3,1.3)}',
      '.hfx-badge .m{display:block;font-family:var(--hfx-title);font-weight:900;font-size:62px;line-height:1;letter-spacing:.04em;text-shadow:0 0 22px currentColor,0 4px 0 rgba(0,0,0,.65)}',
      '.hfx-badge .s{display:block;margin-top:2px;font-size:14px;font-weight:800;letter-spacing:.14em;text-transform:uppercase;color:var(--hfx-ink);text-shadow:0 2px 6px #000,0 0 10px rgba(0,0,0,.8)}',
      '.hfx-badge .s small{color:var(--hfx-dim);font-weight:700}',
      '.hfx-badge.pop .m{animation:hfx-pop .55s cubic-bezier(.2,.9,.3,1.4) both}',
      '@keyframes hfx-pop{from{transform:scale(.3);opacity:0}to{transform:scale(1);opacity:1}}',
      '.hfx-banner{position:absolute;left:430px;width:420px;top:398px;padding:14px 18px 12px;text-align:center;font-family:var(--hfx-title);font-weight:900;font-size:26px;letter-spacing:.08em;text-shadow:0 3px 0 rgba(0,0,0,.7);background:rgba(8,4,20,.84);border:1px solid var(--hfx-line);border-radius:14px;box-shadow:0 0 30px rgba(0,0,0,.5);transition:opacity .3s}',
      '.hfx-banner small{display:block;font-family:var(--hfx-font);font-size:14px;font-weight:700;color:var(--hfx-dim);letter-spacing:.04em;margin-top:2px}',
      '.hfx-sum{position:absolute;left:292px;top:116px;width:696px;padding:16px 24px 14px;text-align:center;background:rgba(10,5,20,.94)}',
      '.hfx-sum h3{margin:-8px -14px 0;padding:8px 14px;font-family:var(--hfx-title);font-size:30px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-weight:900;letter-spacing:.08em;text-transform:uppercase;color:var(--hfx-accent);text-shadow:0 0 18px var(--hfx-accent)}',
      '.hfx-sum .sub{color:var(--hfx-dim);font-size:14px;margin:0 0 10px}',
      '.hfx-sum .dr{display:flex;justify-content:center;gap:8px;margin:0 0 12px}',
      '.hfx-sum .dr span{--cut:10px;padding:5px 16px;font-weight:900;font-size:16px}',
      '.hfx-sum .row{display:flex;justify-content:space-between;font-size:16.5px;padding:3.5px 6px;border-top:1px solid rgba(255,255,255,.08)}',
      '.hfx-sum .row i{font-style:normal;font-weight:800}',
      '.hfx-sum .pos{color:var(--hfx-good)}.hfx-sum .neg{color:var(--hfx-bad)}',
      '.hfx-sum .tot{display:flex;justify-content:center;gap:20px;margin-top:11px;font-size:13px;color:var(--hfx-dim)}',
      '.hfx-sum .best{margin-top:8px;font-size:14px;color:var(--hfx-accent2)}',
      '.hfx-test{position:absolute;right:26px;bottom:50px;font-size:11px;letter-spacing:.2em;color:var(--hfx-bad);font-weight:900}',
      '.hfx-hide{opacity:0!important}'
    ].join('\n');
    var st = doc().createElement('style');
    st.setAttribute('data-hexgames', 'hexfall');
    st.textContent = css;
    doc().head.appendChild(st);
  }

  // ------------------------------------------------------------------ instance
  function HF(container, config, opts) {
    injectCss();
    opts = opts || {};
    this.box = container;
    this.cfg = merge(DEFAULTS, config);
    this.soundOn = opts.sound !== false;
    this.sfx = this.soundOn ? new Sfx() : null;
    this.root = doc().createElement('div');
    this.root.className = 'hfx';
    this.canvas = doc().createElement('canvas');
    this.ctx = this.canvas.getContext('2d');
    this.root.appendChild(this.canvas);
    this.ui = doc().createElement('div');
    this.root.appendChild(this.ui);
    container.appendChild(this.root);
    this.k = 1;
    this.st = null;            // STATE
    this.at = now();           // when st arrived (local clock)
    this.keys = {};            // what the DOM shows
    this.done = {};            // sound / effect events already fired (per drop)
    this.geoCache = {};
    this.plan = null;
    this._build();
    this.setConfig(this.cfg);
    this.resize();
    var self = this;
    // (a frame that throws must not stop the loop: the next state may be fine - it is reported once)
    this._loop = function (t) {
      if (self.dead) return;
      try { self._frame(t); } catch (e) { if (!self._failed) { self._failed = true; try { console.error('[hexfall] frame failed:', e); } catch (x) { } } }
      self._raf = raf(self._loop);
    };
    this._raf = raf(this._loop);
  }
  var P = HF.prototype;

  // (no gradient ids: the panel can hold several instances, and an id inside a hidden tab would not resolve for the others)
  var LOGO = '<svg class="hfx-logo" viewBox="-20 -20 40 40">' +
    '<path d="M17 0 8.5 14.7H-8.5L-17 0-8.5-14.7H8.5Z" stroke-width="2.6" stroke-linejoin="round" style="fill:rgba(0,0,0,.45);stroke:var(--hfx-accent)"/>' +
    '<path d="M8 0 4 6.9H-4L-8 0-4-6.9H4Z" style="fill:var(--hfx-accent2)"/></svg>';

  P._build = function () {
    this.ui.innerHTML =
      '<div class="hfx-top"><div class="hfx-title">' + LOGO + '<div class="hfx-tt"><span class="hfx-name"></span><small class="hfx-sub"></small></div></div>' +
      '<div class="hfx-chips"></div><div class="hfx-timer"><b></b><span></span></div></div>' +
      '<div class="hfx-col hfx-left"><div class="hfx-box hfx-odds"><h4>Payouts</h4><div class="hfx-oddsb"></div></div><div class="hfx-box hfx-rules"></div></div>' +
      '<div class="hfx-col hfx-right"><div class="hfx-box hfx-players"><h4><span class="hfx-pt">On the line</span><small class="hfx-ps"></small></h4><div class="hfx-pb"></div></div></div>' +
      '<div class="hfx-strip"><b>LAST HITS</b><div class="hfx-hits"></div></div>' +
      '<div class="hfx-badge"></div><div class="hfx-banner"></div><div class="hfx-box hfx-sum"></div><div class="hfx-test"></div>';
    var q = this.ui.querySelector.bind(this.ui);
    this.el = { name: q('.hfx-name'), sub: q('.hfx-sub'), chips: q('.hfx-chips'), timer: q('.hfx-timer'), tlabel: q('.hfx-timer b'),
      tval: q('.hfx-timer span'), odds: q('.hfx-odds'), oddsb: q('.hfx-oddsb'), players: q('.hfx-players'), pt: q('.hfx-pt'), ps: q('.hfx-ps'),
      pb: q('.hfx-pb'), rules: q('.hfx-rules'), strip: q('.hfx-strip'), hits: q('.hfx-hits'), badge: q('.hfx-badge'), banner: q('.hfx-banner'),
      sum: q('.hfx-sum'), test: q('.hfx-test') };
  };

  P.setConfig = function (cfg) {
    this.cfg = merge(DEFAULTS, cfg);
    var th = THEMES[this.cfg.theme] || THEMES.coven;
    this.th = th;
    var s = this.root.style;
    s.setProperty('--hfx-font', th.font); s.setProperty('--hfx-title', th.fontTitle); s.setProperty('--hfx-ink', th.ink);
    s.setProperty('--hfx-dim', th.dim); s.setProperty('--hfx-hi', th.hi); s.setProperty('--hfx-accent', th.accent); s.setProperty('--hfx-accent2', th.accent2);
    s.setProperty('--hfx-panel', th.panel); s.setProperty('--hfx-line', th.line); s.setProperty('--hfx-good', th.good); s.setProperty('--hfx-bad', th.bad);
    // the title (branding): text only, smaller as it gets longer (ellipsis past the box)
    var title = String(this.cfg.title == null ? '' : this.cfg.title).trim() || DEFAULTS.title;
    this.el.name.textContent = title;
    this.el.name.title = title;
    this.el.name.style.fontSize = (title.length <= 11 ? 30 : Math.max(15, Math.round(30 * 11 / title.length))) + 'px';
    if (this.sfx) this.sfx.setVolume(this.cfg.sfx_volume);
    this.fl = [th.flame0, mixHex(th.flame0, th.flame1, 0.55), th.flame1, mixHex(th.flame1, th.flame2, 0.3), mixHex(th.flame1, th.flame2, 0.6)];
    this._bg = null; this._spr = {};
    this.keys = {};
  };

  P.resize = function () {
    var r = this.box.getBoundingClientRect ? this.box.getBoundingClientRect() : { width: BASE_W };
    var dpr = root.devicePixelRatio || 1;
    var w = r.width > 4 ? r.width : BASE_W;
    var k = Math.max(0.25, Math.min(4, w * dpr / BASE_W));
    var cw = Math.round(BASE_W * k), ch = Math.round(BASE_H * k);
    if (cw === this.canvas.width && ch === this.canvas.height && k === this.k) return;   // resizing clears the canvas
    this.k = k;
    this.canvas.width = cw;
    this.canvas.height = ch;
    this._bg = null; this._spr = {};
  };

  P.setState = function (st) {
    this.st = st && typeof st === 'object' ? st : null;
    this.at = now();
  };

  P.reset = function () { this.st = null; this.keys = {}; this.plan = null; this.dropKey = null; this.done = {}; };
  P.destroy = function () {
    this.dead = true; caf(this._raf);
    if (this.root.parentNode) this.root.parentNode.removeChild(this.root);
    if (this.sfx && this.sfx.ctx) { try { this.sfx.ctx.close(); } catch (e) { } }
  };

  // local ms since the phase started / until it ends
  P._elapsed = function (t) { var g = this.st && this.st.game; return g ? (+g.elapsed_ms || 0) + (t - this.at) : 0; };
  P._left = function (t) {
    var g = this.st && this.st.game;
    if (!g || g.ends_in_ms == null) return null;
    return Math.max(0, (+g.ends_in_ms || 0) - (t - this.at));
  };
  P._geo = function (rows) { return this.geoCache[rows] || (this.geoCache[rows] = makeGeo(rows)); };

  P._event = function (name, due, t) {
    // fire a one-shot effect (times in s) when its time has come - never replay an old one on a late join
    var key = this.dropKey + ':' + name;
    if (this.done[key] || t < due) return false;
    this.done[key] = 1;
    return t - due < 0.4;
  };
  P._snd = function (name, a, b) { if (this.sfx && this.cfg.sfx) { try { this.sfx.play(name, a, b); } catch (e) { } } };

  // ------------------------------------------------------------------ the frame
  P._frame = function (t) {
    var st = this.st, g = st && st.game, idle = (st && st.idle) || {}, cfg = this.cfg;
    var rows = g ? g.rows : (idle.rows || cfg.rows);
    var slots = g && g.slots && g.slots.length === rows + 1 ? g.slots : (!g && idle.slots && idle.slots.length === rows + 1 ? idle.slots : slotsOf(rows, tableOf(rows, (g || idle).risk || cfg.risk)));
    var G = this._geo(rows);
    this.rows = rows; this.slots = slots; this.G = G;
    var el = this._elapsed(t) / 1000, ph = g ? g.phase : 'idle';
    var fall = g && g.fall && Array.isArray(g.fall.path) && g.fall.path.length === rows && (ph === 'dropping' || ph === 'result' || ph === 'over') ? g.fall : null;
    var dropKey = g ? g.id + '/' + (fall ? fall.drop : 'x') : 'idle';
    if (dropKey !== this.dropKey) { this.dropKey = dropKey; this.plan = null; this.done = {}; }
    var since = -1;
    if (fall) {
      var D = (+fall.ms || (+cfg.drop_seconds || 9) * 1000) / 1000, R = (+cfg.result_seconds || 5);
      if (!this.plan || this.plan.G !== G || this.plan.D !== D || this.plan.seed !== (fall.seed | 0)) this.plan = buildPlan(G, fall.path, fall.seed, D);
      since = ph === 'dropping' ? el : ph === 'result' ? D + el : D + R + el;
    }
    this._draw(t, g, slots, G, fall ? this.plan : null, since, ph, idle);
    this._dom(g, this._left(t), t, since);
  };

  // ------------------------------------------------------------------ drawing
  P._background = function (G) {
    var key = this.k + '|' + this.cfg.theme + '|' + G.R;
    if (this._bg && this._bgKey === key) return this._bg;
    var k = this.k, cv = makeCanvas(BASE_W * k, BASE_H * k), c = cv.getContext('2d');
    c.setTransform(k, 0, 0, k, 0, 0);
    drawStatic(c, this.th, G);
    this._bgKey = key;
    return (this._bg = cv);
  };

  // a pocket sprite per tier (hex tray + glow), at this canvas scale
  P._pocket = function (G, tier) {
    var key = G.R + '|' + tier + '|' + this.k + '|' + this.cfg.theme;
    if (this._spr[key]) return this._spr[key];
    var tc = this.th.tier[tier], a = G.a, pad = 18, w = a * 2 + pad * 2, h = G.ph + pad * 2, k = this.k;
    var cv = makeCanvas(w * k, h * k), c = cv.getContext('2d');
    c.scale(k, k);
    var cx = w / 2, cy = h / 2;
    var gr = c.createRadialGradient(cx, cy, a * 0.3, cx, cy, a * 1.35);
    gr.addColorStop(0, rgba(tc.glow, 0.34)); gr.addColorStop(1, rgba(tc.glow, 0));
    c.fillStyle = gr; c.fillRect(0, 0, w, h);
    hexPath(c, cx, cy, a, 0);
    var fg = c.createLinearGradient(cx, cy - G.ph / 2, cx, cy + G.ph / 2);
    fg.addColorStop(0, mixHex(tc.fill, tc.glow, 0.2)); fg.addColorStop(0.5, tc.fill); fg.addColorStop(1, mixHex(tc.fill, '#000000', 0.45));
    c.fillStyle = fg; c.fill();
    c.lineJoin = 'round'; c.strokeStyle = tc.rim; c.lineWidth = Math.max(1.6, a * 0.07); c.stroke();
    hexPath(c, cx, cy, a * 0.8, 0); c.strokeStyle = rgba(tc.rim, 0.32); c.lineWidth = 1; c.stroke();
    var rec = { cv: cv, w: w, h: h };
    this._spr[key] = rec;
    return rec;
  };

  P._draw = function (t, g, slots, G, plan, since, ph, idle) {
    var c = this.ctx, k = this.k;
    c.setTransform(1, 0, 0, 1, 0, 0);
    c.clearRect(0, 0, this.canvas.width, this.canvas.height);
    c.setTransform(k, 0, 0, k, 0, 0);
    // a heavy landing shakes the whole scene a little
    var landed = plan && since >= plan.imp[plan.R], sinceLand = landed ? since - plan.imp[plan.R] : -1;
    if (landed && +g.fall.mult >= 10 && sinceLand < 0.5) {
      var sh = Math.exp(-sinceLand * 7) * 3.2;
      c.translate(Math.sin(sinceLand * 90) * sh, Math.cos(sinceLand * 77) * sh * 0.8);
    }
    c.drawImage(this._background(G), 0, 0, BASE_W, BASE_H);
    this._ambient(c, t);
    this._sigil(c, t);
    var tok = plan && since >= plan.tI ? planAt(plan, since) : null;
    this._shimmer(c, G, t, tok);
    this._braziers(c, G, t);
    if (plan && since >= 0) this._pathMemory(c, plan, since);
    this._pegFlashes(c, plan, since);
    this._pockets(c, G, slots, plan, since, t);
    this._portal(c, G, g, plan, since, t, ph, g && ph === 'betting' && g.phase_ms ? clamp(this._left(t) / g.phase_ms, 0, 1) : null);
    if (tok) this._token(c, plan, since, t, tok);
    if (landed) this._landing(c, plan, slots, since, t);
  };

  // drifting embers (stateless: a pure function of the clock)
  P._ambient = function (c, t) {
    var th = this.th, s = t / 1000, i;
    c.globalCompositeOperation = 'lighter';
    for (i = 0; i < 34; i++) {
      var h1 = hash(i * 3.17), h2 = hash(i * 7.31 + 2), h3 = hash(i * 1.93 + 5), sp = 14 + h3 * 26;
      var y = BASE_H + 20 - ((s * sp + h2 * BASE_H * 1.4) % (BASE_H + 40)), x = h1 * BASE_W + Math.sin(s * 0.7 + i) * 18;
      var a = (0.2 + 0.5 * h3) * smooth(0, 160, y) * smooth(BASE_H + 20, BASE_H - 120, y);
      glow(c, i % 3 ? th.accent : th.accent2, x, y, 2.2 + h3 * 3.2, a * 0.8);
    }
    c.globalCompositeOperation = 'source-over';
  };

  P._sigil = function (c, t) {                                   // a faint rune circle turning behind the pyramid
    var th = this.th, s = t / 1000, cx = BOARD.cx, cy = 470, i;
    c.save();
    c.strokeStyle = rgba(th.ring, 0.1); c.lineWidth = 1.5;
    c.beginPath(); c.arc(cx, cy, 330, 0, TAU); c.stroke();
    c.beginPath(); c.arc(cx, cy, 316, 0, TAU); c.stroke();
    c.translate(cx, cy); c.rotate(s * 0.03);
    c.strokeStyle = rgba(th.ring, 0.13);
    for (i = 0; i < 72; i++) { var a = i * TAU / 72, r0 = i % 6 ? 318 : 308; c.beginPath(); c.moveTo(Math.cos(a) * r0, Math.sin(a) * r0); c.lineTo(Math.cos(a) * 330, Math.sin(a) * 330); c.stroke(); }
    c.restore();
  };

  // the lattice breathes: a slow wave of light over the pegs, brighter where the token is
  P._shimmer = function (c, G, t, tok) {
    var th = this.th, s = t / 1000, r, j, reach = G.pitch * 2.1;
    c.globalCompositeOperation = 'lighter';
    for (r = 0; r < G.R; r++) {
      for (j = 0; j <= r; j++) {
        var x = G.pegX(r, j), y = G.pegY(r), a = 0.05 + 0.07 * (0.5 + 0.5 * Math.sin(s * 1.1 - r * 0.55 + j * 0.4));
        glow(c, th.peg.glow, x, y, G.rp * 3, a);
        if (tok && tok.a > 0.05) {
          var d = Math.sqrt((x - tok.x) * (x - tok.x) + (y - tok.y) * (y - tok.y));
          if (d < reach) { var pr = 1 - d / reach; glow(c, th.accent2, x, y, G.rp * (2 + 1.6 * pr), 0.38 * pr * pr * tok.a); }
        }
      }
    }
    c.globalCompositeOperation = 'source-over';
  };

  // two braziers of green witch fire beside the slot tray (stateless: a pure function of the clock)
  P._braziers = function (c, G, t) {
    var th = this.th, s = t / 1000, i, side;
    var by = Math.min(BASE_H - 62, BOARD.slotTop + G.ph + 22);
    for (side = 0; side < 2; side++) {
      var x = BOARD.cx + (side ? 1 : -1) * (BOARD.w / 2 + 44), ph0 = side * 2.3;
      c.save(); c.translate(x, by);
      c.fillStyle = th.peg.fill2; hexPath(c, 0, 19, 14, 0); c.fill(); c.strokeStyle = rgba(th.accent, 0.5); c.lineWidth = 1.5; c.stroke();
      c.fillStyle = th.peg.fill; c.fillRect(-3.5, 2, 7, 17);
      var bg = c.createLinearGradient(0, -8, 0, 8);
      bg.addColorStop(0, th.peg.fill); bg.addColorStop(1, th.peg.fill2);
      c.beginPath(); c.moveTo(-19, -7); c.lineTo(19, -7); c.lineTo(11, 7); c.lineTo(-11, 7); c.closePath();
      c.fillStyle = bg; c.fill(); c.strokeStyle = rgba(th.accent, 0.75); c.lineWidth = 1.8; c.lineJoin = 'round'; c.stroke();
      c.restore();
      c.save(); c.globalCompositeOperation = 'lighter';
      glow(c, th.flame1, x, by - 44, 96, 0.16 + 0.05 * Math.sin(s * 7 + ph0));
      for (i = 0; i < 13; i++) {
        var f = i / 12, hgt = f * 104, wob = Math.sin(s * 5.2 + i * 0.75 + ph0) * (2 + 8 * f) + Math.sin(s * 9.1 + i * 1.7 + ph0) * 2.4 * f;
        var r = (21 - 16 * f) * (0.86 + 0.14 * Math.sin(s * 12 + i + ph0));
        glow(c, f < 0.22 ? th.flame0 : f < 0.7 ? th.flame1 : th.flame2, x + wob, by - 9 - hgt, r * 1.9, (1 - f * 0.6) * 0.5);
      }
      for (i = 0; i < 7; i++) {                                 // rising embers
        var e = (s * 0.55 + hash(i * 3.7 + side) ) % 1;
        glow(c, i % 2 ? th.flame1 : th.flame0, x + Math.sin(e * 6 + i) * 12, by - 14 - e * 110, 2.6 * (1 - e) + 0.6, (1 - e) * 0.9);
      }
      c.restore();
    }
  };

  // pegs the token has touched keep a glow (the path it took), and flash on impact
  P._pegFlashes = function (c, plan, since) {
    if (!plan || since < 0) return;
    var G = plan.G, th = this.th, i, dt;
    c.globalCompositeOperation = 'lighter';
    for (i = 0; i < plan.R; i++) {
      dt = since - plan.imp[i];
      if (dt < 0) continue;
      var x = G.pegX(i, plan.pj[i]), y = G.pegY(i), f = Math.exp(-dt / 0.22);
      glow(c, th.peg.glow, x, y, G.rp * (2.8 + 3.4 * f), 0.4 * f + 0.22);
      if (f > 0.04) glow(c, th.accent2, x, y, G.rp * (1.5 + 1.8 * f), 0.8 * f);
      if (dt < 0.22) {                                           // the peg itself kicks: a brighter, slightly larger hex for a moment
        var kick = Math.exp(-dt / 0.06);
        hexPath(c, x, y, G.rp * (1 + 0.32 * kick), 0); c.fillStyle = rgba(th.accent2, 0.5 * kick); c.fill();
        c.strokeStyle = rgba('#ffffff', 0.8 * kick); c.lineWidth = 1.6; c.stroke();
      }
      if (dt < 0.7) {                                            // an expanding hex ring
        var rr = G.rp + 26 * (1 - Math.exp(-dt / 0.2)), ra = Math.exp(-dt / 0.2) * 0.85;
        c.save(); hexPath(c, x, y, rr, 0); c.strokeStyle = rgba(th.accent2, ra); c.lineWidth = 1.8; c.stroke(); c.restore();
      }
      if (dt < 0.42) {                                           // sparks (cosmetic: seeded)
        for (var s = 0; s < 8; s++) {
          var h1 = hash(plan.seed * 0.001 + i * 13 + s * 5.1), h2 = hash(plan.seed * 0.002 + i * 7 + s * 3.3);
          var ang = -Math.PI * (0.05 + 0.9 * h1), sp = 60 + 120 * h2;
          var px = x + Math.cos(ang) * sp * dt, py = y - G.rp + Math.sin(ang) * sp * dt + 420 * dt * dt, la = 1 - dt / 0.42;
          c.strokeStyle = rgba(th.flame0, 0.85 * la); c.lineWidth = 1.7;
          c.beginPath(); c.moveTo(px, py); c.lineTo(px - Math.cos(ang) * 6 * la, py - Math.sin(ang) * 6 * la); c.stroke();
        }
      }
      // a click for every peg (and its note)
      if (this._event('hit' + i, plan.imp[i], since)) this._snd('hit', i, { rows: plan.R, dir: plan.dirs[i] });
    }
    c.globalCompositeOperation = 'source-over';
  };

  P._pathMemory = function (c, plan, since) {
    if (since < plan.imp[plan.R]) return;
    var th = this.th, i, a = smooth(0, 0.6, since - plan.imp[plan.R]) * 0.5;
    c.save(); c.globalCompositeOperation = 'lighter';
    c.strokeStyle = rgba(th.accent2, a * 0.55); c.lineWidth = 2.2; c.setLineDash([2, 6]); c.lineCap = 'round';
    c.beginPath();
    for (i = 0; i <= plan.R; i++) { var p = plan.P[i]; if (i) c.lineTo(p.x, p.y); else c.moveTo(p.x, p.y); }
    c.stroke(); c.setLineDash([]);
    c.restore();
  };

  // the slot row: hex pockets with their multipliers (the landing one blazes)
  P._pockets = function (c, G, slots, plan, since, t) {
    var th = this.th, a = G.a, i, landedAt = plan ? plan.imp[plan.R] : null, landSlot = plan ? plan.slot : -1, sinceLand = landedAt != null ? since - landedAt : -1;
    var tray = BOARD.slotTop - 7, trayH = G.ph + 22, x0 = BOARD.cx - BOARD.w / 2 - 10, trayW = BOARD.w + 20;
    // the tray the pockets sit in
    c.save();
    c.beginPath(); c.moveTo(x0 + 12, tray); c.lineTo(x0 + trayW - 12, tray); c.quadraticCurveTo(x0 + trayW, tray, x0 + trayW, tray + 12);
    c.lineTo(x0 + trayW, tray + trayH - 12); c.quadraticCurveTo(x0 + trayW, tray + trayH, x0 + trayW - 12, tray + trayH);
    c.lineTo(x0 + 12, tray + trayH); c.quadraticCurveTo(x0, tray + trayH, x0, tray + trayH - 12); c.lineTo(x0, tray + 12); c.quadraticCurveTo(x0, tray, x0 + 12, tray); c.closePath();
    c.fillStyle = 'rgba(4,2,10,.72)'; c.fill(); c.strokeStyle = rgba(th.accent, 0.28); c.lineWidth = 1.5; c.stroke();
    c.restore();
    var fs = clamp(G.pitch * 0.31, 10.5, 21);
    c.textAlign = 'center'; c.textBaseline = 'middle';
    for (i = 0; i < slots.length; i++) {
      var s = slots[i], tier = tierOf(+s.mult), tc = th.tier[tier], x = G.slotX(i), y = BOARD.slotTop + G.ph / 2;
      var spr = this._pocket(G, tier), lit = 0;
      if (sinceLand >= 0 && i === landSlot) lit = clamp(1 - (sinceLand - 0.05) / 2.2, 0, 1) * 0.7 + 0.3 * (sinceLand < 4 ? 1 : 0.5);
      var dim = sinceLand >= 0 && i !== landSlot ? 0.5 : 1;
      var pulse = 0.82 + 0.18 * Math.sin(t / 700 + i * 0.9);
      c.globalAlpha = (0.78 + 0.22 * pulse) * dim * (lit ? 1 : 1);
      c.drawImage(spr.cv, x - spr.w / 2, y - spr.h / 2, spr.w, spr.h);
      c.globalAlpha = 1;
      if (lit) {                                                // the landing slot: a white-hot fill + a halo
        var fl = sinceLand < 0 ? 0 : Math.exp(-sinceLand * 2.2);
        c.save(); c.globalCompositeOperation = 'lighter';
        hexPath(c, x, y, a * 0.96, 0); c.fillStyle = rgba(tc.glow, 0.22 + 0.5 * fl + 0.1 * Math.sin(t / 160)); c.fill();
        glow(c, tc.glow, x, y, a * (2.2 + fl * 1.4), 0.5 + 0.4 * fl);
        c.restore();
        hexPath(c, x, y, a, 0); c.strokeStyle = rgba('#ffffff', 0.55 + 0.4 * fl); c.lineWidth = 2.4; c.stroke();
      }
      if (+s.mult === 0 || s.bust) {
        skull(c, x, y + 1, clamp(G.pitch * 0.2, 7, 14), tier === 'bust' ? tc.text : '#ddd', tc.fill);
      } else {
        var label = fmtMult(s.mult);
        c.font = '900 ' + (label.length > 4 ? Math.max(8, fs * 4.15 / label.length) : fs).toFixed(1) + 'px ' + th.font;     // ×1000 still fits a small pocket
        c.lineWidth = 3.4; c.strokeStyle = 'rgba(0,0,0,.65)'; c.strokeText(label, x, y + 1);
        c.fillStyle = lit ? '#ffffff' : tc.text; c.fillText(label, x, y + 1);
      }
    }
  };

  // the rune ring above the pyramid: the token waits here, charges, is released (it fades while the result badge is up)
  P._portal = function (c, G, g, plan, since, t, ph, frac) {
    var prev = GALPHA, vis = ph === 'result' || ph === 'over' ? 0.14 : 1;
    GALPHA = vis;
    try { this._portalDraw(c, G, plan, since, t, ph, vis, frac); } finally { GALPHA = prev; }
  };
  P._portalDraw = function (c, G, plan, since, t, ph, vis, frac) {
    var th = this.th, s = t / 1000, cx = BOARD.cx, cy = BOARD.portalY;
    var charge = 0.25, rel = -1, i;
    if (plan && since >= 0 && since < plan.imp[0]) {
      charge = since < plan.tI ? 0.25 + 0.75 * easeInOut(since / plan.tI) : 1;
      rel = since - plan.tI;
    } else if (plan) charge = 0.18;
    else if (ph === 'betting') charge = 0.35 + 0.1 * Math.sin(s * 2);
    var spin = s * (0.5 + charge * 2.2);
    c.save(); c.translate(cx, cy);
    c.globalCompositeOperation = 'lighter';
    glow(c, th.accent, 0, 0, 62 + charge * 34 + (rel >= 0 && rel < 0.4 ? 44 * (1 - rel / 0.4) : 0), 0.45 + 0.35 * charge);
    c.restore();
    c.save(); c.translate(cx, cy);
    var rings = [[42, spin, 2.4, 0.9], [32, -spin * 1.3, 1.7, 0.7], [22, spin * 0.8, 1.3, 0.55]];
    rings.forEach(function (r, idx) {
      hexPath(c, 0, 0, r[0], r[1]);
      c.strokeStyle = rgba(idx === 1 ? th.accent2 : th.accent, r[3] * (0.4 + 0.6 * charge) * vis); c.lineWidth = r[2]; c.stroke();
    });
    if (frac != null) {                                         // the betting window running out, as an arc round the ring
      var warn = frac * (+this.st.game.phase_ms || 0) <= 10000, ac = warn ? th.bad : th.accent2;
      c.lineCap = 'round'; c.lineWidth = 3 + (warn ? 1.2 * Math.sin(s * 9) : 0);
      c.strokeStyle = rgba(ac, 0.22); c.beginPath(); c.arc(0, 0, 60, 0, TAU); c.stroke();
      c.strokeStyle = rgba(ac, 0.95); c.beginPath(); c.arc(0, 0, 60, -Math.PI / 2, -Math.PI / 2 + TAU * frac); c.stroke();
    }
    for (i = 0; i < 6; i++) {                                   // rune ticks around the ring
      var a = spin * 0.4 + i * SIXTH + SIXTH / 2, x = Math.cos(a) * 52, y = Math.sin(a) * 52;
      c.fillStyle = rgba(th.accent2, (0.3 + 0.6 * charge) * vis); c.beginPath(); c.arc(x, y, 1.8 + charge * 1.4, 0, TAU); c.fill();
    }
    if (rel >= 0 && rel < 0.5) {                                // the release: a shock ring
      var rr = 30 + rel * 150; hexPath(c, 0, 0, rr, spin); c.strokeStyle = rgba('#ffffff', (1 - rel / 0.5) * 0.7); c.lineWidth = 3; c.stroke();
    }
    c.restore();
    if (plan && since >= 0 && since < plan.tI) {                // charge sparks flying in
      c.save(); c.globalCompositeOperation = 'lighter';
      for (i = 0; i < 16; i++) {
        var ph2 = (since * 1.4 + hash(i * 3.3 + plan.seed * 0.0001)) % 1, ang = hash(i * 5.7) * TAU, rr2 = 120 * (1 - ph2);
        glow(c, i % 2 ? th.accent2 : th.accent, cx + Math.cos(ang) * rr2, cy + Math.sin(ang) * rr2, 3.4 + 3.4 * ph2, 0.95 * ph2 * charge);
      }
      c.restore();
    }
    if (plan) {                                                 // sounds of the build-up
      if (this._event('charge', 0, since)) this._snd('charge', Math.max(0.4, plan.tI));
      if (this._event('release', plan.tI, since)) this._snd('release');
    }
    // the token waiting for the next drop, glowing brighter as the ring charges
    if (!plan && (ph === 'betting' || ph === 'idle')) this._drawToken(c, cx, cy + Math.sin(s * 2.2) * 4, G.rt, Math.sin(s * 1.6) * 0.12, 1, 1, 0.7 + 0.3 * charge, 1);
    else if (plan && since >= 0 && since < plan.tI) {
      var pp = planAt(plan, since), cw = 1 + 0.06 * Math.sin(since * 14) * charge;
      this._drawToken(c, pp.x, pp.y, G.rt, pp.rot, cw, cw, 0.8 + 0.6 * charge, 1);
    }
  };

  P._token = function (c, plan, since, t, p) {
    var th = this.th, G = plan.G, i;
    if (p.a <= 0.01) return;
    // flame trail: particles born along the path (id = a tick of 1/55 s), drifting up as they cool. The body is drawn
    // with normal blending so the fire stays green over the purple room; only the hot core adds light.
    var life = 1.1, RATE = 55, n0 = Math.floor((since - life) * RATE), n1 = Math.floor(since * RATE), n, pts = [];
    for (n = Math.max(n0, Math.ceil((plan.tI + 0.05) * RATE)); n <= n1; n++) {
      var ts = n / RATE, age = since - ts;
      if (ts > plan.imp[plan.R] + 0.1) break;
      var q = planAt(plan, ts), f = clamp(age / life, 0, 1);
      var h1 = hash(n * 1.37 + plan.seed * 0.0007), h2 = hash(n * 2.71 + 9), h3 = hash(n * 4.13 + 3);
      pts.push({ x: q.x + (h1 - 0.5) * G.rt * 0.9 + Math.sin(age * 9 + h2 * 6) * 5 * f, y: q.y + (h2 - 0.5) * G.rt * 0.5 - age * (30 + 52 * h3),
        r: G.rt * (1.1 - 0.7 * f) * (0.7 + 0.5 * h3), f: f, a: q.a });
    }
    for (i = 0; i < pts.length; i++) {
      var pt = pts[i];
      glow(c, this.fl[Math.min(4, Math.floor(pt.f * 5))], pt.x, pt.y, pt.r * 1.7, Math.pow(1 - pt.f, 1.4) * 0.55 * pt.a);
    }
    c.save(); c.globalCompositeOperation = 'lighter';
    for (i = 0; i < pts.length; i++) {
      if (pts[i].f < 0.3) glow(c, th.flame0, pts[i].x, pts[i].y, pts[i].r * 0.9, (1 - pts[i].f / 0.3) * 0.22 * pts[i].a);
    }
    c.restore();
    c.save(); c.globalCompositeOperation = 'lighter';
    // sparkles that twinkle round the token
    for (i = 0; i < 7; i++) {
      var ang = hash(i * 9.1) * TAU + since * (1.4 + hash(i) * 1.6), rr = G.rt * (1.5 + hash(i * 2.3) * 1.4), tw = 0.5 + 0.5 * Math.sin(since * 9 + i * 2.1);
      star(c, p.x + Math.cos(ang) * rr, p.y + Math.sin(ang) * rr * 0.9, 2.8 + 4.5 * tw, since * 2 + i);
      c.fillStyle = rgba(i % 2 ? th.flame0 : th.accent2, 0.4 + 0.55 * tw); c.fill();
    }
    glow(c, th.token.glow, p.x, p.y, G.rt * 3.2, 0.45 * p.a);
    c.restore();
    this._drawToken(c, p.x, p.y, G.rt, p.rot, p.sx, p.sy, 1, p.a);
    c.save(); c.globalCompositeOperation = 'lighter'; glow(c, th.accent, p.x, p.y, G.pitch * 1.9, 0.16 * p.a); c.restore();
  };

  // the glowing skull token (a bone-bright cranium and jaw, eye sockets lit by the green fire, a nose, teeth); s = squash in world axes
  P._drawToken = function (c, x, y, r, rot, sx, sy, bright, alpha) {
    var th = this.th, tk = th.token, u = r * 1.08, i;
    c.save();
    c.globalAlpha = alpha;
    c.translate(x, y); c.scale(sx, sy); c.rotate(rot);
    glow(c, tk.glow, 0, 0, r * 2.6, 0.5 * bright);
    c.beginPath();                                              // one outline: jaw, cheek, cranium
    c.moveTo(-0.5 * u, 1.0 * u); c.lineTo(-0.5 * u, 0.62 * u);
    c.bezierCurveTo(-0.5 * u, 0.46 * u, -0.97 * u, 0.36 * u, -0.97 * u, -0.12 * u);
    c.bezierCurveTo(-0.97 * u, -0.72 * u, -0.52 * u, -1.0 * u, 0, -1.0 * u);
    c.bezierCurveTo(0.52 * u, -1.0 * u, 0.97 * u, -0.72 * u, 0.97 * u, -0.12 * u);
    c.bezierCurveTo(0.97 * u, 0.36 * u, 0.5 * u, 0.46 * u, 0.5 * u, 0.62 * u);
    c.lineTo(0.5 * u, 0.84 * u); c.quadraticCurveTo(0.5 * u, 1.0 * u, 0.34 * u, 1.0 * u);
    c.lineTo(-0.34 * u, 1.0 * u); c.quadraticCurveTo(-0.5 * u, 1.0 * u, -0.5 * u, 0.84 * u);
    c.closePath();
    var g = c.createRadialGradient(-u * 0.3, -u * 0.45, u * 0.1, 0, 0, u * 1.15);
    g.addColorStop(0, tk.core); g.addColorStop(0.5, mixHex(tk.core, tk.mid, 0.4)); g.addColorStop(0.85, mixHex(tk.core, tk.mid, 0.8)); g.addColorStop(1, tk.mid);
    c.fillStyle = g; c.fill();
    c.lineJoin = 'round'; c.lineWidth = Math.max(1.5, r * 0.14); c.strokeStyle = tk.rim; c.stroke();
    // eye sockets (dark, a flame inside), a nose, the teeth
    c.fillStyle = '#10051f';
    for (i = -1; i <= 1; i += 2) {
      c.save(); c.translate(i * 0.4 * u, -0.1 * u); c.rotate(i * -0.18);
      c.beginPath(); c.ellipse(0, 0, 0.27 * u, 0.3 * u, 0, 0, TAU); c.fill(); c.restore();
    }
    c.beginPath(); c.moveTo(0, 0.18 * u); c.lineTo(-0.12 * u, 0.44 * u); c.lineTo(0.12 * u, 0.44 * u); c.closePath(); c.fill();
    c.strokeStyle = tk.rim; c.lineWidth = Math.max(1, r * 0.07); c.lineCap = 'round';
    c.beginPath();
    for (i = -1; i <= 1; i++) { c.moveTo(i * 0.2 * u, 0.7 * u); c.lineTo(i * 0.2 * u, 0.98 * u); }
    c.moveTo(-0.5 * u, 0.7 * u); c.lineTo(0.5 * u, 0.7 * u);
    c.stroke();
    c.globalCompositeOperation = 'lighter';
    for (i = -1; i <= 1; i += 2) {
      glow(c, th.flame1, i * 0.4 * u, -0.1 * u, 0.36 * u, (0.75 + 0.25 * Math.sin(x * 0.07 + y * 0.05 + i)) * bright);
      glow(c, th.flame0, i * 0.4 * u, -0.12 * u, 0.15 * u, 0.95 * bright);
    }
    c.restore();
  };

  // the landing: a light column, a shock ring, confetti (or ash for a bust)
  P._landing = function (c, plan, slots, since, t) {
    var th = this.th, G = plan.G, s = since - plan.imp[plan.R], slot = slots[plan.slot] || { mult: 0 };
    var tier = tierOf(+slot.mult), tc = th.tier[tier], x = G.slotX(plan.slot), y = BOARD.slotTop, i;
    if (this._event('land', plan.imp[plan.R], since)) this._snd(tier === 'bust' ? 'bust' : tier === 'low' ? 'low' : 'win', tier);
    c.save(); c.globalCompositeOperation = 'lighter';
    var fl = Math.exp(-s * 6);
    if (fl > 0.02) { c.fillStyle = rgba(tc.glow, (tier === 'bust' ? 0.2 : 0.15) * fl); c.fillRect(0, 0, BASE_W, BASE_H); }
    // the beam
    var ba = Math.exp(-s * 1.1) * (tier === 'bust' ? 0.3 : 0.55), bh = tier === 'bust' ? 150 : 330 + Math.min(200, Math.log(+slot.mult + 1) * 55);
    [[1.7, 0.16], [0.95, 0.28], [0.36, 0.5]].forEach(function (L) {
      var bg = c.createLinearGradient(0, y, 0, y - bh);
      bg.addColorStop(0, rgba(tc.glow, ba * L[1])); bg.addColorStop(1, rgba(tc.glow, 0));
      c.fillStyle = bg; c.fillRect(x - G.a * L[0] / 2, y - bh, G.a * L[0], bh);
    });
    // shock rings
    for (i = 0; i < 2; i++) {
      var ss = s - i * 0.14;
      if (ss < 0 || ss > 0.9) continue;
      hexPath(c, x, y + G.ph / 2, G.a * 0.7 + ss * 150, 0); c.strokeStyle = rgba(tc.glow, (1 - ss / 0.9) * 0.8); c.lineWidth = 3.2 - i; c.stroke();
    }
    // confetti (hex shards) for a win, drifting ash + embers for a bust
    var count = tier === 'bust' ? 26 : tier === 'low' ? 14 : tier === 'mid' ? 26 : tier === 'good' ? 44 : 70, life = 2.1;
    if (s < life) {
      for (i = 0; i < count; i++) {
        var h1 = hash(i * 1.9 + plan.seed * 0.0003), h2 = hash(i * 3.1 + 4), h3 = hash(i * 5.3 + 8), age = s - h3 * 0.12;
        if (age < 0) continue;
        var f = age / life;
        if (tier === 'bust') {
          var px = x + (h1 - 0.5) * G.a * 2.2 + Math.sin(age * 3 + i) * 10, py = y + 6 - age * (24 + 60 * h2);
          glow(c, i % 3 ? '#5a1020' : tc.glow, px, py, 7 + 12 * f, (1 - f) * (i % 3 ? 0.35 : 0.5));
        } else {
          var ang = -Math.PI * (0.1 + 0.8 * h1), sp = 150 + 330 * h2 * (tier === 'jack' ? 1.4 : 1);
          var qx = x + Math.cos(ang) * sp * age, qy = y + Math.sin(ang) * sp * age + 620 * age * age;
          var col = i % 4 === 0 ? '#ffffff' : i % 4 === 1 ? tc.glow : i % 4 === 2 ? tc.rim : th.accent2;
          hexPath(c, qx, qy, 3 + 4 * h3, age * (3 + h2 * 6)); c.fillStyle = rgba(col, (1 - f) * 0.9); c.fill();
        }
      }
    }
    c.restore();
  };

  // ------------------------------------------------------------------ DOM
  P._set = function (key, el, html) {
    if (this.keys[key] === html) return;
    this.keys[key] = html;
    el.innerHTML = html;
  };
  P._show = function (el, on) { el.style.display = on ? '' : 'none'; };

  function tierColor(th, m) { return th.tier[tierOf(+m)]; }

  P._dom = function (g, left, t, since) {
    var st = this.st, cfg = this.cfg, th = this.th, idle = (st && st.idle) || {};
    var cur = (g && g.currency) || idle.currency || cfg.currency || 'coins';
    var ph = g ? g.phase : 'idle';
    var drops = g ? g.drops : (idle.drops || cfg.drops);
    var rows = this.rows, slots = this.slots, i;
    // subtitle + drop chips
    this._set('sub', this.el.sub, esc(g ? rows + ' rows · ' + g.risk + ' risk' + (g.source === 'custom' ? ' · custom' : '') : 'Drop the hex through ' + rows + ' rows'));
    var chips = '';
    for (var n = 1; n <= drops; n++) {
      var cls = '', lab = 'DROP ' + n, val = n;
      if (g) {
        var hit = (g.hits || []).filter(function (h) { return h.drop === n; })[0];
        var landed = hit && !(g.fall && g.fall.drop === n && ph === 'dropping');
        if (landed) { cls = 'done'; val = fmtMult(hit.mult); }
        if (n === g.drop && (ph === 'betting' || ph === 'dropping')) cls = 'cur';
      }
      var style = '';
      if (cls === 'done') { var tcc = tierColor(th, hit.mult); style = ' style="color:' + tcc.text + ';--hx:' + tcc.rim + '"'; }
      chips += '<div class="hfx-hx hfx-chip ' + cls + '"' + style + '><b>' + lab + '</b>' + val + '</div>';
    }
    this._set('chips', this.el.chips, chips);
    // timer
    var tl = '', tv = '', last = false;
    if (ph === 'betting') { tl = g.drop === 1 ? 'Bets close in' : 'Next drop in'; tv = Math.ceil(left / 1000) + 's'; last = left <= 10000; }
    else if (ph === 'dropping') { tl = 'Drop ' + g.drop; tv = since >= 0 && this.plan ? Math.min(this.plan.R, Math.max(0, 1 + this._rowNow(since))) + ' / ' + this.plan.R : '…'; }
    else if (ph === 'result') { tl = 'Drop ' + g.drop; tv = g.last ? fmtMult(g.last.mult) : ''; }
    else if (ph === 'over') { tl = 'Game over'; tv = ''; }
    else { tl = 'Waiting'; tv = ''; }
    this.el.tlabel.textContent = tl; this.el.tval.textContent = tv;
    this.el.timer.className = 'hfx-timer' + (last ? ' last' : '');
    if (last && ph === 'betting') {
      var sec = Math.ceil(left / 1000);
      if (sec <= 5 && sec >= 1 && this._beep !== g.id + '/' + g.drop + '/' + sec) { this._beep = g.id + '/' + g.drop + '/' + sec; this._snd('beep'); }
    }
    // payout ladder
    this._show(this.el.odds, !!cfg.show_odds);
    if (cfg.show_odds) {
      var lad = ladderOf(slots), hitMult = g && g.last && (ph === 'result' || ph === 'over') ? String(g.last.mult) : (this.plan && since >= this.plan.imp[this.plan.R] && slots[this.plan.slot] ? String(slots[this.plan.slot].mult) : null);
      var rtp = g ? g.rtp_pct : (idle.rtp_pct != null ? idle.rtp_pct : rtpOf(slots)), edge = g ? g.house_edge_pct : (idle.house_edge_pct != null ? idle.house_edge_pct : Math.round((100 - rtp) * 100) / 100);
      var rows2 = '<table>';
      lad.forEach(function (r) {
        var tc = th.tier[tierOf(r.mult)], isHit = hitMult != null && String(r.mult) === hitMult;
        rows2 += '<tr class="' + (isHit ? 'hit' : '') + '"><td style="color:' + tc.text + '">' + (r.bust ? '☠ BUST' : fmtMult(r.mult)) + '</td><td>' + fmtPct(r.pct) + '</td></tr>';
      });
      rows2 += '</table><div class="hfx-rtp"><div>Return <b>' + rtp + '%</b></div><div>House edge <b>' + edge + '%</b></div></div>';
      this._set('odds', this.el.oddsb, rows2);
    }
    // players (on the line while bets are open / the token falls, then this drop's payouts)
    var showP = !!cfg.show_players && !!g && ph !== 'over';
    this._show(this.el.players, showP);
    if (showP) {
      var max = +cfg.players_max || 8, html = '', title, sub = '';
      if ((ph === 'result' || (ph === 'over' && false)) && g.last) {
        title = 'Drop ' + g.last.drop + ' payouts';
        var rs = [].concat(g.last.winners || [], g.last.even || [], g.last.losers || []).sort(function (a, b) { return b.paid - a.paid || b.bet - a.bet; });
        sub = rs.length ? rs.length + ' in' : '';
        if (!rs.length) html = '<div class="hfx-empty">Nobody was on this drop</div>';
        for (i = 0; i < Math.min(rs.length, max); i++) {
          var r = rs[i], cl = r.net > 0 ? 'pos' : r.net < 0 ? 'neg' : 'eq';
          html += '<div class="hfx-p"><u>' + esc(r.user) + '</u><i class="' + cl + '">' + (r.paid ? (r.net ? signed(r.net) : '±0') : '−' + fmt(r.bet)) + ' <small>' + (r.paid ? 'paid ' + fmt(r.paid) : 'bust') + '</small></i></div>';
        }
        if (rs.length > max) html += '<div class="hfx-empty">+' + (rs.length - max) + ' more</div>';
      } else {
        title = ph === 'over' ? 'Final tally' : 'On the line';
        var ps = g.players || [];
        sub = ps.length ? fmt(g.on_the_line) + ' ' + cur : '';
        if (ph === 'over') {
          var tot = (g.summary && g.summary.players) || [];
          sub = tot.length ? tot.length + ' played' : '';
          if (!tot.length) html = '<div class="hfx-empty">Nobody bet</div>';
          for (i = 0; i < Math.min(tot.length, max); i++) html += '<div class="hfx-p"><u>' + esc(tot[i].user) + '</u><i class="' + (tot[i].net > 0 ? 'pos' : tot[i].net < 0 ? 'neg' : 'eq') + '">' + signed(tot[i].net) + '</i></div>';
          if (tot.length > max) html += '<div class="hfx-empty">+' + (tot.length - max) + ' more</div>';
        } else {
          if (!ps.length) html = '<div class="hfx-empty">' + (ph === 'betting' ? 'No bets yet' : '—') + '</div>';
          for (i = 0; i < Math.min(ps.length, max); i++) html += '<div class="hfx-p"><u>' + esc(ps[i].user) + '</u><i>' + fmt(ps[i].bet) + '</i></div>';
          if (ps.length > max) html += '<div class="hfx-empty">+' + (ps.length - max) + ' more</div>';
        }
      }
      this.el.pt.textContent = title; this.el.ps.textContent = sub;
      this._set('players', this.el.pb, html);
    }
    // rules box (bets open)
    var showR = !!cfg.show_rules && ph === 'betting';
    this._show(this.el.rules, showR);
    if (showR) {
      var warn = left <= 10000 ? '<div class="warn">⏱ Bets close in ' + Math.ceil(left / 1000) + '!</div>' : '';
      var best = Math.max.apply(null, slots.map(function (s) { return +s.mult; }));
      var rh = '<h4>Bets open<small>drop ' + g.drop + ' of ' + g.drops + '</small></h4>' +
        '<p><em>BET</em> ' + fmt(g.min_bet) + (g.max_bet ? '–' + fmt(g.max_bet) : '+') + ' ' + esc(cur) + ' on this drop.</p>' +
        '<p>One skull falls through ' + rows + ' rows; the slot it lands in pays your bet <em>×</em> its multiplier — up to <em>' + fmtMult(best) + '</em>.</p>' +
        (cfg.commands_text || g.commands_text ? '<div class="cmd">' + esc(cfg.commands_text || g.commands_text) + '</div>' : '');
      this._set('rules', this.el.rules, rh + '<div class="hfx-w"></div>');
      var w = this.el.rules.querySelector('.hfx-w');
      if (w && w.innerHTML !== warn) w.innerHTML = warn;
    }
    // last hits
    var recent = (g ? g.recent : idle.recent) || [];
    this._show(this.el.strip, !!cfg.show_history);
    if (cfg.show_history) {
      var hh = '';
      recent.slice(0, 12).forEach(function (h) {
        var tc = tierColor(th, h.mult);
        hh += '<span class="hfx-hx hfx-hit" style="color:' + tc.text + ';--hx:' + tc.rim + '">' + (+h.mult === 0 ? '☠' : fmtMult(h.mult)) + '</span>';
      });
      if (!recent.length) hh = '<span class="hfx-empty" style="padding:0">no drops yet</span>';
      this._set('hits', this.el.hits, hh);
    }
    // the result badge + banner
    var badge = '', bcls = '', ban = '';
    if (g && (ph === 'result') && g.last) {
      var L = g.last, tcb = tierColor(th, L.mult), nw = (L.winners || []).length;
      var who = !L.total_bet ? '' : nw ? nw + (nw > 1 ? ' winners' : ' winner') : L.bust ? 'everyone busts' : (L.losers || []).length ? 'no winners' : 'stakes back';
      badge = '<span class="m" style="color:' + tcb.text + '">' + (L.bust ? '☠ BUST' : fmtMult(L.mult)) + '</span><span class="s">Drop ' + L.drop + ' · slot ' + (L.slot + 1) + ' of ' + (rows + 1) +
        (who ? ' <small>· ' + who + '</small>' : '') + '</span>';
      bcls = 'pop';
    } else if (!g && st && st.visible) {
      ban = 'Next game soon<small>Place your bets when the timer starts</small>';
    }
    var bk = badge ? (g.id + '/' + g.last.drop) : '';
    if (this._badgeKey !== bk) { this._badgeKey = bk; this.keys.badge = null; }
    this._set('badge', this.el.badge, badge);
    this.el.badge.className = 'hfx-badge ' + bcls;
    this._show(this.el.badge, !!badge);
    this._set('banner', this.el.banner, ban);
    this._show(this.el.banner, !!ban);
    // game over
    var showS = ph === 'over' && g.summary;
    this._show(this.el.sum, !!showS);
    if (showS) this._set('sum', this.el.sum, summaryHTML(g, cur, th, rows));
    this._set('test', this.el.test, g && g.test ? 'TEST GAME · NO COINS' : '');
  };

  // the row the token is at (for the "n / rows" read-out while it falls)
  P._rowNow = function (since) {
    var pl = this.plan, i = -1;
    if (!pl) return -1;
    for (var j = 0; j < pl.R; j++) if (since >= pl.imp[j]) i = j;
    return i;
  };

  function summaryHTML(g, cur, th, rows) {
    var s = g.summary;
    var title = s.outcome === 'complete' ? 'Hexfall complete' : esc(s.text);
    var sub = s.drops.length + ' of ' + s.planned + ' drops · ' + rows + ' rows · ' + esc(s.risk) + ' risk' + (s.source === 'custom' ? ' · custom table' : '') + ' · return ' + s.rtp_pct + '%';
    var dr = s.drops.map(function (d) {
      var tc = tierColor(th, d.mult);
      return '<span class="hfx-hx" style="color:' + tc.text + ';--hx:' + tc.rim + '">' + (+d.mult === 0 ? '☠ BUST' : fmtMult(d.mult)) + '</span>';
    }).join('');
    var rowsH = (s.players || []).slice(0, 6).map(function (p) {
      return '<div class="row"><span>' + esc(p.user) + '</span><i class="' + (p.net > 0 ? 'pos' : p.net < 0 ? 'neg' : '') + '">' + signed(p.net) + '</i></div>';
    }).join('');
    if (!rowsH) rowsH = '<div class="sub">Nobody bet</div>';
    var best = s.best && s.best.net > 0 ? '<div class="best">★ Biggest win: ' + esc(s.best.user) + ' +' + fmt(s.best.net) + ' ' + esc(cur) + ' on ' + fmtMult(s.best.mult) + ' · drop ' + s.best.drop + '</div>' : '';
    return '<h3>' + title + '</h3><div class="sub">' + sub + '</div>' + (dr ? '<div class="dr">' + dr + '</div>' : '') + rowsH +
      '<div class="tot"><span>Bet ' + fmt(s.total_bet) + '</span><span>Paid ' + fmt(s.total_paid) + '</span><span>House ' +
      (s.house_net >= 0 ? '+' : '') + fmt(s.house_net) + '</span></div>' + best;
  }

  // ------------------------------------------------------------------ registration
  function copyObj(o) { var r = {}; for (var k in o) if (has(o, k)) r[k] = o[k]; return r; }
  var API = {
    BASE_W: BASE_W, BASE_H: BASE_H, THEMES: THEMES, DEFAULTS: copyObj(DEFAULTS), APPEARANCE: APPEARANCE.slice(),
    PRESETS: PRESETS, slotsOf: slotsOf, rtpOf: rtpOf, tableOf: tableOf,
    // The timeline of one drop, for tests and tools: the pegs the token touches, where it ends and at(t) (seconds since
    // the drop began) -> {x, y, rot, ...}. It only ever follows `path`; `seed` varies hop heights and times, nothing else.
    plan: function (rows, path, seed, D) {
      var G = makeGeo(rows), pl = buildPlan(G, path, seed, D);
      return { rows: rows, slot: pl.slot, slotX: G.slotX(pl.slot), duration: D, impacts: pl.imp.slice(),
        pegs: pl.pj.map(function (j, i) { return [i, j, G.pegX(i, j), G.pegY(i)]; }), at: function (t) { return planAt(pl, t); } };
    },
    create: function (container, config, opts) { return new HF(container, config, opts); }
  };
  HG.hexfall = API;
  if (typeof module === 'object' && module && module.exports) module.exports = API;
})(typeof window !== 'undefined' ? window : (typeof globalThis !== 'undefined' ? globalThis : this));
