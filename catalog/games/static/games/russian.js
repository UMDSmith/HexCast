/*
 * Hexcast Games — Russian Roulette renderer                 static/games/russian.js
 *
 * Shared by the OBS overlay (/games/overlay) and the control panel (/games#russian).
 * Plain browser script: no modules, no dependencies, no build step.
 *
 * Registers window.HexGames.russian:
 *
 *   BASE_W, BASE_H            scene size in stage px at scale 1 (1100 x 560)
 *   THEMES                    { saloon, noir, neon }
 *   DEFAULTS, APPEARANCE      same values / keys as the backend
 *   create(container, config, opts) -> instance      opts: {sound: true}
 *
 * Branding: config `title` is the header's name ("Russian Roulette"), shown as text and
 * shrunk to fit.
 *
 *   instance.setConfig(cfg)   instance.resize()    instance.setState(STATE)
 *   instance.reset()          instance.destroy()
 *
 * setState() takes the game's whole STATE (the server's state_view(): {state, visible,
 * game, idle, ...}); game.ends_in_ms / game.elapsed_ms must already be corrected for
 * the time the message sat in the host. Everything on screen is a function of the
 * state and the time since its phase started, so a late join (or the panel's mirror)
 * lands on the same frame as every other overlay.
 *
 * The pull (phase "pulling", pull_seconds long) is choreographed backwards from its
 * end, where the server's outcome lands:
 *   0 - 1.2 s     the cylinder swings out on its crane, one more round slides into it
 *   1.2 - 1.6 s   it snaps shut
 *   1.6 s - D-2.6 the spin: fast (motion blur), then slowing to a stop. The spin is seeded per pull
 *                 and has nothing to do with which chamber fires - nothing on screen tells until:
 *   D-2.4 - D-1.9 the hammer is cocked (the cylinder indexes one chamber)
 *   D-0.15 - D    the trigger is pulled; at D the hammer drops: BANG (`fired`) or *click*
 * After a bang the dummy is hit, its stuffing bursts out and it crumples off its post;
 * at the end of the game-over card it is stuffed again and climbs back up.
 */
(function (root) {
  'use strict';

  var HG = root.HexGames || (root.HexGames = {});
  var BASE_W = 1100, BASE_H = 560;
  var TAU = Math.PI * 2, STEP = TAU / 6;

  var DEFAULTS = {
    x: 50, y: 50, scale: 1.0, theme: 'saloon', title: 'Russian Roulette', show_rules: true, show_players: true,
    players_max: 8, show_odds: true, sfx: true, sfx_volume: 0.6, hide_when_idle: true, commands_text: '',
    rounds: 3, house_edge_pct: 5, open_bet_seconds: 30, between_seconds: 20, pull_seconds: 9,
    result_seconds: 4, summary_seconds: 10, currency: 'coins', min_bet: 1, max_bet: 100000,
    max_payout: 0, volunteer_cut_pct: 5, dummy_name: 'Dummy', pull_clip: '', click_clip: '', bang_clip: ''
  };
  var APPEARANCE = ['x', 'y', 'scale', 'theme', 'title', 'show_rules', 'show_players', 'players_max', 'show_odds',
    'sfx', 'sfx_volume'];

  var THEMES = {
    saloon: {
      bgTop: '#3a2415', bgBot: '#170d07', plank: 'rgba(0,0,0,.28)', plankHi: 'rgba(255,220,170,.05)',
      lamp: 'rgba(255,184,96,.30)', floor: '#2a1809', floorHi: 'rgba(255,200,140,.06)',
      accent: '#e7ae4b', accent2: '#c0392b', ink: '#fbf0de', dim: '#cbb49a', panel: 'rgba(22,13,7,.88)',
      line: 'rgba(231,174,75,.5)', good: '#7ee08f', bad: '#ff5b47',
      steel1: '#4a5058', steel2: '#16181c', steelHi: '#b7c0ca', grip1: '#7a4220', grip2: '#3b1c0b',
      post: '#6b4423', post2: '#3d2513', burlap: '#c9a26a', burlap2: '#a57f4d', stitch: '#4a2f17',
      font: "'Rockwell','Rockwell Extra Bold','Georgia',serif", glow: 'none'
    },
    noir: {
      bgTop: '#2b2d31', bgBot: '#0d0e10', plank: 'rgba(0,0,0,.25)', plankHi: 'rgba(255,255,255,.035)',
      lamp: 'rgba(255,255,255,.16)', floor: '#18191c', floorHi: 'rgba(255,255,255,.05)',
      accent: '#f2f2f2', accent2: '#d0202a', ink: '#f5f5f5', dim: '#a9abb0', panel: 'rgba(12,12,14,.9)',
      line: 'rgba(255,255,255,.3)', good: '#9be7a8', bad: '#ff4b4b',
      steel1: '#5a5e66', steel2: '#1a1b1f', steelHi: '#d8dce2', grip1: '#2c2c30', grip2: '#101012',
      post: '#4b4b50', post2: '#2a2a2e', burlap: '#bdb3a2', burlap2: '#8e8574', stitch: '#2b2620',
      font: "'Bahnschrift','Segoe UI',sans-serif", glow: 'none'
    },
    neon: {
      bgTop: '#1a0b2a', bgBot: '#07030d', plank: 'rgba(255,40,160,.07)', plankHi: 'rgba(0,240,255,.05)',
      lamp: 'rgba(255,40,180,.25)', floor: '#12071d', floorHi: 'rgba(0,240,255,.08)',
      accent: '#ff3fb4', accent2: '#00f0ff', ink: '#fff4fd', dim: '#c9a7e0', panel: 'rgba(14,5,24,.88)',
      line: 'rgba(255,63,180,.6)', good: '#5cffb0', bad: '#ff3f6a',
      steel1: '#6d7a8c', steel2: '#1c2029', steelHi: '#e6f6ff', grip1: '#43235e', grip2: '#1c0d2b',
      post: '#3d2a55', post2: '#1f1430', burlap: '#d0a8ff', burlap2: '#8f6ac2', stitch: '#2a1440',
      font: "'Bahnschrift','Segoe UI',sans-serif", glow: '0 0 12px rgba(255,63,180,.8)'
    }
  };

  // scene layout (base px)
  var GROUND = 522;
  var GUN = { x: 520, y: 318 };                  // cylinder centre; the barrel points right
  var DUMMY = { x: 930, hips: GROUND - 150 };    // hips on the post
  var CHEST = { x: 12, y: -150 };                // impact point, dummy space

  // ------------------------------------------------------------------ helpers
  function has(o, k) { return Object.prototype.hasOwnProperty.call(o, k); }
  function merge(a, b) { var o = {}, k; for (k in a) if (has(a, k)) o[k] = a[k]; if (b) for (k in b) if (has(b, k) && b[k] != null) o[k] = b[k]; return o; }
  function clamp(v, a, b) { return v < a ? a : v > b ? b : v; }
  function lerp(a, b, t) { return a + (b - a) * t; }
  function ease(t) { t = clamp(t, 0, 1); return t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2; }
  function easeOut(t) { t = clamp(t, 0, 1); return 1 - Math.pow(1 - t, 3); }
  function easeIn(t) { t = clamp(t, 0, 1); return t * t; }
  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function fmt(n) { n = Math.round(+n || 0); return String(Math.abs(n)).replace(/\B(?=(\d{3})+(?!\d))/g, ',').replace(/^/, n < 0 ? '−' : ''); }
  function mult(m) { return '×' + (+m).toFixed(2); }
  function mulberry(a) {
    return function () {
      a |= 0; a = a + 0x6D2B79F5 | 0;
      var t = Math.imul(a ^ a >>> 15, 1 | a);
      t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t;
      return ((t ^ t >>> 14) >>> 0) / 4294967296;
    };
  }
  function now() { return (root.performance && root.performance.now) ? root.performance.now() : Date.now(); }
  var raf = root.requestAnimationFrame ? function (f) { return root.requestAnimationFrame(f); } : function (f) { return setTimeout(function () { f(now()); }, 16); };
  var caf = root.cancelAnimationFrame ? function (id) { root.cancelAnimationFrame(id); } : function (id) { clearTimeout(id); };


  // ==================================================================== art
  // The revolver (a heavy double-action swing-out) and the room (back wall, lamp + light cone, dust, the
  // benchrest the gun sits on, muzzle flash, smoke, camera shake). Both are pure Canvas 2D, deterministic
  // (seeded, clock-driven) and cache everything static per theme; they only need a theme palette.
  var REVOLVER = (function () {
/*
 * swingout -- a heavy double-action swing-out revolver (full-underlug vent-rib barrel, target
 * hammer, finger-groove stocks) for Hexcast's Russian Roulette overlay.
 *
 *   drawRevolver(c, pose, th, t)
 *     c    2D context, already placed so (0,0) is the cylinder axis, +x toward the muzzle (base px)
 *     pose open      0..1  cylinder swings out on its crane (down and toward us, rear face turning to us)
 *          bullets   0..6  rounds in chambers 0..bullets-1 (only visible while open)
 *          loadIn    0..1  the newest round (chamber bullets-1) sliding in; 0 = outside, 1 = seated (default 1)
 *          loaded/newChamber  optional: explicit chamber index list + sliding chamber (overrides bullets)
 *          spin      rad   cylinder angle; chamber i sits at spin + i*60deg, 0 = top (under the hammer), i running
 *                          clockwise on the open face (top -> muzzle side -> bottom). Increasing spin moves the
 *                          front of the drum down the screen. 
 *          spinSpeed rad/s SIGNED d(spin)/dt: motion blur from ~4 rad/s (full by ~14) + speed lines from ~3 rad/s.
 *                          The sign matters: blur exposures trail the flutes and the streaks drift with the
 *                          surface. Never Math.abs.
 *          cock      0..1  hammer back, TRAVEL rad about a low pivot: at rest the spur lies flat along the tang;
 *                          cocked it sweeps back past the grip and opens a notch showing the firing-pin
 *                          bushing (the trigger follows it half way back)
 *          trig      0..1  trigger travel. The hammer HOLDS at full cock while the trigger takes up slack
 *                          and drops between 70% and 95% travel (2-3 frames of a 0.15 s pull). An
 *                          uncocked hammer never lifts: releasing the trigger with cock = 0 leaves it down.
 *          flash     -1 | 0..0.15 s since the shot: cylinder-gap jet, hot muzzle (centred on REVOLVER_MUZZLE),
 *                          barrel heat
 *     th   theme palette (steel1, steel2, steelHi, grip1, grip2, accent, accent2, lamp, bgTop, bgBot, floor, glow, font)
 *     t    ms timestamp (optional: speed lines while spinning, a soft glint sweeps the steel every 7 s)
 *
 * In russian.js: spin is a seeded, outcome-independent angle (see spinTotal) and spinSpeed its exact
 * derivative; the muzzle star, tracer and smoke are drawn in the same recoil transform as the gun, at
 * REVOLVER_MUZZLE, so there is one flash centre riding the barrel.
 *
 * Closed, the cylinder is identical whatever is loaded and whichever chamber is up: only flutes and
 * cylinder-stop notches are drawn and both repeat every 60deg.
 *
 * Everything that never moves (frame, window interior, barrel, grips, sights, silhouette contour,
 * backlight) is painted once per theme and per device scale (1/8 steps, read from c.getTransform())
 * into an offscreen sprite; the hammer gets its own small sprite. Per frame only the trigger, cylinder
 * and effects are drawn as vectors. No randomness at draw time (textures use a seeded PRNG and are
 * baked; speed lines are keyed to spin angle and t).
 */
var REVOLVER_MUZZLE = { x: 268, y: -19 };
var REVOLVER_BOUNDS = { x0: -123, y0: -57, x1: 271, y1: 147 };   // opaque pixels over all poses (+ ~6 px soft backlight)

var drawRevolver = (function () {
  'use strict';
  var TAU = Math.PI * 2, STEP = TAU / 6;

  // ------------------------------------------------------------------ geometry (gun space)
  var CR = 33.5, HL = 32, CH = 4.6;               // cylinder radius, half length, end chamfer
  var RC = 19.5, HOLE = 7.4, RIM = 8.8, PRIM = 2.7; // chamber pitch radius, chamber, rim, primer
  var FL0 = -20, FL1 = 22.5, FLB = 0.24;          // flute start / end along the axis, angular half width
  var MX = 268, BY = -19;                          // muzzle face x, bore axis y
  var WX = 36, WY = 30.5;                          // frame window half size (the drum overlaps it top + bottom)
  var HP = { x: -62, y: 5 };                        // hammer pivot
  var TP = { x: 4, y: 37 };                        // trigger pivot
  var KP = { x: 41, y: 33 };                       // crane pivot (front-bottom of the frame window)
  var SB = { x0: -150, y0: -78, x1: 290, y1: 160 };// sprite box (bounds + contour + backlight room)
  var HB = { x0: -30, y0: -54, x1: 24, y1: 8 };     // hammer sprite box (pivot-local)
  var CART = 57, NOSE = 13, CGAP = 16;             // cartridge length, bullet nose, loading gap
  var ROD = 58;                                     // ejector rod length past the cylinder
  var TRAVEL = 0.8;                                 // hammer rotation at full cock (rad)
  var EDGE = 2.5;                                   // outer silhouette contour width

  // ------------------------------------------------------------------ small helpers
  function clamp01(v) { return v < 0 ? 0 : v > 1 ? 1 : v; }
  function smooth(a, b, v) { v = clamp01((v - a) / (b - a)); return v * v * (3 - 2 * v); }
  function ease(v) { v = clamp01(v); return v < 0.5 ? 4 * v * v * v : 1 - Math.pow(-2 * v + 2, 3) / 2; }
  function num(v, d) { return typeof v === 'number' && isFinite(v) ? v : d; }
  function frac(v) { return v - Math.floor(v); }
  function mulberry(a) {
    return function () {
      a |= 0; a = a + 0x6D2B79F5 | 0;
      var t = Math.imul(a ^ a >>> 15, 1 | a);
      t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t;
      return ((t ^ t >>> 14) >>> 0) / 4294967296;
    };
  }
  function hash(n) {             // integer -> [0,1), deterministic
    n = (n | 0) ^ 0x9e3779b9; n = Math.imul(n ^ n >>> 16, 0x85ebca6b); n = Math.imul(n ^ n >>> 13, 0xc2b2ae35);
    return ((n ^ n >>> 16) >>> 0) / 4294967296;
  }
  function doc() { return (typeof document !== 'undefined') ? document : null; }
  function makeCanvas(w, h) {
    if (typeof OffscreenCanvas !== 'undefined' && !doc()) return new OffscreenCanvas(w, h);
    var cv = doc().createElement('canvas'); cv.width = w; cv.height = h; return cv;
  }

  // colours ---------------------------------------------------------
  function col(s, fb) {
    s = (s == null ? fb : s) || '#808080';
    if (s.charAt(0) === '#') {
      var h = s.slice(1);
      if (h.length === 3) h = h.charAt(0) + h.charAt(0) + h.charAt(1) + h.charAt(1) + h.charAt(2) + h.charAt(2);
      var n = parseInt(h.slice(0, 6), 16);
      return [n >> 16 & 255, n >> 8 & 255, n & 255, 1];
    }
    var m = s.match(/[\d.]+/g);
    if (!m || m.length < 3) return col(fb || '#808080');
    return [+m[0], +m[1], +m[2], m[3] == null ? 1 : +m[3]];
  }
  function mix(a, b, t) { return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t, 1]; }
  function css(a, al) {
    return 'rgba(' + Math.round(a[0]) + ',' + Math.round(a[1]) + ',' + Math.round(a[2]) + ',' +
      (al == null ? 1 : +(+al).toFixed(3)) + ')';
  }
  function hsl(a) {
    var r = a[0] / 255, g = a[1] / 255, b = a[2] / 255, mx = Math.max(r, g, b), mn = Math.min(r, g, b);
    var l = (mx + mn) / 2, d = mx - mn, h = 0, s = 0;
    if (d > 0) {
      s = l > 0.5 ? d / (2 - mx - mn) : d / (mx + mn);
      if (mx === r) h = ((g - b) / d + (g < b ? 6 : 0)) / 6;
      else if (mx === g) h = ((b - r) / d + 2) / 6;
      else h = ((r - g) / d + 4) / 6;
    }
    return [h, s, l];
  }
  function stops(gr, list) { for (var i = 0; i < list.length; i += 2) gr.addColorStop(list[i], list[i + 1]); return gr; }

  var WHITE = [255, 255, 255, 1], BLACK = [0, 0, 0, 1];
  var BRASS_HI = [255, 238, 176, 1], BRASS = [216, 162, 62, 1], BRASS_D = [112, 72, 22, 1];
  var COPPER_HI = [255, 200, 160, 1], COPPER = [200, 112, 64, 1], COPPER_D = [96, 44, 22, 1];
  // colour case hardening: straw, amber, peacock blue, plum, slate
  var CASE = [[214, 184, 120, 1], [58, 92, 168, 1], [112, 70, 136, 1], [78, 122, 176, 1], [150, 150, 162, 1], [44, 58, 110, 1], [66, 110, 170, 1], [188, 150, 96, 1]];

  // ------------------------------------------------------------------ palette (per theme, cached)
  var PALS = [];
  function palette(th) {
    th = th || {};
    var key = [th.steel1, th.steel2, th.steelHi, th.grip1, th.grip2, th.accent, th.accent2, th.lamp, th.bgTop,
      th.bgBot, th.floor, th.glow, th.font].join('|');
    for (var i = 0; i < PALS.length; i++) if (PALS[i].key === key) return PALS[i];
    var s1 = col(th.steel1, '#4a5058'), s2 = col(th.steel2, '#16181c'), hi = col(th.steelHi, '#b7c0ca');
    var lamp = col(th.lamp, 'rgba(255,184,96,.3)'); lamp[3] = 1;
    var p = {
      key: key,
      s1: s1, s2: s2, hi: hi,
      deep: mix(s2, BLACK, 0.45), ink: mix(s2, BLACK, 0.74), mid: mix(s1, s2, 0.5),
      lite: mix(s1, hi, 0.45), spec: mix(hi, WHITE, 0.6),
      lamp: lamp, wall: col(th.bgTop, '#3a2415'), floor: col(th.floor, '#2a1809'),
      g1: col(th.grip1, '#7a4220'), g2: col(th.grip2, '#3b1c0b'),
      acc: col(th.accent, '#e7ae4b'), acc2: col(th.accent2 || th.accent, '#c0392b'),
      neon: !!(th.glow && th.glow !== 'none'),
      font: th.font || 'Georgia, serif',
      sprites: [], cache: {}
    };
    // silhouette ink: the scene's darkest wall tone, darker still
    p.edge = mix(col(th.bgBot, '#120c08'), BLACK, 0.5);
    // reflections pick up the room: warm lamp above, floor below
    p.sky = mix(mix(hi, lamp, 0.22), WHITE, 0.1);
    p.gnd = mix(mix(s1, p.floor, 0.35), lamp, 0.08);
    p.rimTop = p.neon ? col(th.accent2 || '#00f0ff') : mix(lamp, WHITE, 0.45);
    p.rimBot = p.neon ? p.acc : null;
    p.fireCore = p.neon ? mix([255, 240, 250, 1], p.acc, 0.15) : [255, 250, 225, 1];
    p.fireMid = p.neon ? mix([255, 170, 90, 1], p.acc, 0.45) : [255, 200, 90, 1];
    p.fireOut = p.neon ? mix([255, 90, 30, 1], p.acc, 0.6) : [255, 100, 30, 1];
    p.insert = p.neon ? col(th.accent2 || '#00f0ff') : col(th.accent2, '#d23a2a');
    var hs = hsl(p.g1);
    p.wood = hs[1] > 0.3 && hs[0] > 0.01 && hs[0] < 0.16 && hs[2] > 0.12;
    p.warm = p.wood && !p.neon;                 // brass trigger + case-coloured hammer
    if (hs[2] < 0.24) {                          // near-black stocks vanish on a dark wall: lift them
      p.g1 = mix(p.g1, hi, 0.15); p.g2 = mix(p.g2, hi, 0.08);
    }
    p.gHi = mix(p.g1, WHITE, p.wood ? 0.22 : 0.2);
    p.gDeep = mix(p.g2, BLACK, 0.45);
    // the drum is turned bright steel: a touch lighter than the blued frame
    p.cs1 = mix(s1, hi, 0.16); p.cdeep = mix(p.deep, s1, 0.32); p.clite = mix(p.lite, hi, 0.22);
    PALS.push(p);
    if (PALS.length > 8) PALS.shift();
    return p;
  }

  // gradient recipes -------------------------------------------------
  // round steel (barrel tube): specular top, sky, dark horizon, floor bounce
  function roundStops(p) {
    return [0, css(p.deep), 0.05, css(p.spec), 0.1, css(p.sky), 0.26, css(p.lite), 0.44, css(p.s1),
      0.56, css(p.deep), 0.66, css(p.mid), 0.84, css(p.gnd), 0.95, css(p.mid), 1, css(p.ink)];
  }
  // flat polished side (frame, underlug): bevel on top, a crisp mirror horizon
  function flatStops(p) {
    return [0, css(p.sky), 0.07, css(p.lite), 0.3, css(p.s1), 0.47, css(p.mid), 0.5, css(p.deep),
      0.62, css(p.mid), 0.86, css(p.gnd), 1, css(p.s2)];
  }

  // ------------------------------------------------------------------ static paths
  function rrect(g, x, y, w, h, r) {
    g.moveTo(x + r, y); g.lineTo(x + w - r, y); g.quadraticCurveTo(x + w, y, x + w, y + r);
    g.lineTo(x + w, y + h - r); g.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
    g.lineTo(x + r, y + h); g.quadraticCurveTo(x, y + h, x, y + h - r);
    g.lineTo(x, y + r); g.quadraticCurveTo(x, y, x + r, y); g.closePath();
  }
  // catmull-rom through points (the pen must already be at pts[0])
  function crTo(g, pts) {
    for (var i = 0; i < pts.length - 1; i++) {
      var p0 = pts[i - 1] || pts[i], p1 = pts[i], p2 = pts[i + 1], p3 = pts[i + 2] || p2;
      g.bezierCurveTo(p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6,
        p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6, p2[0], p2[1]);
    }
  }

  function pFrame(g) {
    g.beginPath();
    g.moveTo(-47, -40);
    g.lineTo(48, -40);
    g.lineTo(48, 20);
    g.quadraticCurveTo(48, 42, 28, 42);
    g.lineTo(-22, 42);
    g.quadraticCurveTo(-31, 42, -35, 51);
    g.lineTo(-72, 124);
    g.lineTo(-104, 124);
    g.lineTo(-91, 4);
    g.bezierCurveTo(-95, -8, -96.5, -22, -91, -27.5);      // tang: hugs the back of the hammer
    g.bezierCurveTo(-86, -31.5, -79.5, -30.5, -76.5, -25.5);
    g.bezierCurveTo(-74.5, -21.5, -73.5, -14, -70, -11.5);
    g.lineTo(-51, -11);
    g.quadraticCurveTo(-47, -11, -47, -15);
    g.lineTo(-47, -40);
    g.closePath();
  }
  // the hammer shows only above the side walls of its slot / the tang, and behind the back strap
  var HCLIP = [[-46.4, -95], [-46.4, -14.5], [-49.5, -11.6], [-70, -12.2], [-72.6, -14.2], [-74.2, -19.6], [-76.2, -25],
    [-78.6, -28.6], [-82.6, -30.6], [-87, -30], [-90.6, -27.6], [-93.6, -23], [-95.3, -15], [-95.6, -4], [-100.6, 14],
    [-160, 14], [-160, -95]];
  function pWindow(g) { g.beginPath(); rrect(g, -WX, -WY, WX * 2, WY * 2, 2); }
  function pTube(g) { g.beginPath(); g.rect(46, -31.5, MX - 46, 24); }
  function pLug(g) {
    g.beginPath();
    g.moveTo(46, -9); g.lineTo(MX, -9); g.lineTo(MX, 5);
    g.quadraticCurveTo(MX, 14, MX - 9, 14);
    g.lineTo(60, 14);
    g.quadraticCurveTo(51, 14, 48.5, 22);
    g.lineTo(46, 22);
    g.closePath();
  }
  function pRib(g) { g.beginPath(); g.rect(46, -40.5, MX - 50, 4.2); }
  function pRibPosts(g) { g.beginPath(); g.rect(46, -36.5, MX - 50, 5.5); }
  function pFront(g) {
    g.beginPath();
    g.moveTo(MX - 2, -40.5); g.lineTo(250, -52.5); g.quadraticCurveTo(249, -53.5, 247, -53.5);
    g.lineTo(243, -53.5); g.lineTo(242, -40.5); g.closePath();
  }
  function pRear(g) {
    g.beginPath();
    g.moveTo(-44, -40); g.lineTo(-44, -43); g.lineTo(-41, -43.5); g.lineTo(-41, -50.5);
    g.lineTo(-22, -50.5); g.quadraticCurveTo(-15, -50.5, -11, -45.5); g.lineTo(-5, -43); g.lineTo(-3, -40);
    g.closePath();
  }
  function pGuard(g) {
    g.beginPath();
    g.moveTo(30, 40);
    g.bezierCurveTo(34, 64, 24, 82, 2, 83);
    g.bezierCurveTo(-18, 84, -30, 70, -30, 47);
  }
  // firing-pin bushing standing proud of the frame's breech face (seen when the hammer is back)
  function pBushing(g) {
    g.beginPath();
    g.moveTo(-46.6, BY - 5.6); g.lineTo(-50, BY - 5.6);
    g.quadraticCurveTo(-54.4, BY - 5.6, -54.4, BY); g.quadraticCurveTo(-54.4, BY + 5.6, -50, BY + 5.6);
    g.lineTo(-46.6, BY + 5.6); g.closePath();
  }

  // grip: soft finger grooves on the front strap, a palm swell at the back, rounded butt
  var GRIP_FRONT = (function () {
    var A = [-66, 134], B = [-27, 42], u = [B[0] - A[0], B[1] - A[1]], L = Math.sqrt(u[0] * u[0] + u[1] * u[1]);
    var n = [-u[1] / L, u[0] / L];
    var prof = [[0, 0], [0.1, -2.6], [0.19, -3.8], [0.28, -1], [0.36, 3.6], [0.44, 1], [0.52, -3.4], [0.6, -3],
      [0.68, 0], [0.75, 3.6], [0.82, 1], [0.9, -2.8], [1, 0]];
    return prof.map(function (q) { return [A[0] + u[0] * q[0] + n[0] * q[1], A[1] + u[1] * q[0] + n[1] * q[1]]; });
  })();
  var GRIP_BACK = [[-95, -4], [-100, 14], [-106, 44], [-115, 80], [-119.5, 108], [-118, 126]];
  function pGrip(g) {
    g.beginPath();
    var f = GRIP_FRONT, top = f[f.length - 1];
    g.moveTo(top[0], top[1]);
    g.bezierCurveTo(-42, 32, -62, 8, -80, -8);
    g.quadraticCurveTo(-90, -15, -95, -4);
    crTo(g, GRIP_BACK);
    g.bezierCurveTo(-117, 136, -110, 141, -98, 141);
    g.lineTo(-80, 140);
    g.quadraticCurveTo(-68, 139, f[0][0], f[0][1]);
    crTo(g, f);
    g.closePath();
  }
  // checkered panel on wooden stocks
  function pPanel(g) {
    g.beginPath();
    g.save(); g.translate(-86, 76); g.rotate(0.4);
    g.moveTo(0, -48);
    g.bezierCurveTo(13, -48, 16, -30, 15, 0);
    g.bezierCurveTo(14, 30, 12, 46, 0, 46);
    g.bezierCurveTo(-12, 46, -15, 30, -15, 0);
    g.bezierCurveTo(-15, -30, -13, -48, 0, -48);
    g.closePath();
    g.restore();
  }

  // union of the metal (for glints / heat / shadows), built lazily
  var METAL = null, BARREL = null, EXCL = null, OCC = null;
  function metalPaths() {
    if (METAL || typeof Path2D === 'undefined') return;
    METAL = new Path2D(); BARREL = new Path2D(); EXCL = new Path2D();
    // occluders for the crane + ejector rod: each is "sprite box + piece", clipped even-odd one after the
    // other (= outside the union). (1) the underlug and the front strap between window and barrel, down to
    // the lug's lower edge: the rod slides up into its channel there. (2) the crane's closed footprint, a
    // capsule from the cylinder front to its pivot: closed, the yoke is the frame the sprite already shows.
    OCC = [new Path2D(), new Path2D()];
    OCC.forEach(function (o) { o.rect(SB.x0, SB.y0, SB.x1 - SB.x0, SB.y1 - SB.y0); });
    var o1 = OCC[0];
    o1.moveTo(WX, -14); o1.lineTo(MX + 2, -14); o1.lineTo(MX + 2, 5); o1.quadraticCurveTo(MX + 2, 14.2, MX - 9, 14.2);
    o1.lineTo(60, 14.2); o1.quadraticCurveTo(51, 14.2, 48.6, 22.2); o1.lineTo(WX, 22.2); o1.closePath();
    var ya = Math.atan2(KP.y, KP.x - HL), YR = 9.9, o2 = OCC[1];
    o2.moveTo(HL + Math.cos(ya + Math.PI / 2) * YR, Math.sin(ya + Math.PI / 2) * YR);
    o2.arc(HL, 0, YR, ya + Math.PI / 2, ya + Math.PI * 1.5);
    o2.arc(KP.x, KP.y, YR, ya - Math.PI / 2, ya + Math.PI / 2);
    o2.closePath();
    EXCL.rect(SB.x0, SB.y0, SB.x1 - SB.x0, SB.y1 - SB.y0);
    var eg = new Path2D(); pGrip(proxy(eg)); EXCL.addPath(eg);
    var ew = new Path2D(); pWindow(proxy(ew)); EXCL.addPath(ew);
    var parts = [pFrame, pTube, pLug, pRib, pRibPosts, pFront, pRear];
    parts.forEach(function (fn) {
      var pp = new Path2D(); fn(proxy(pp)); METAL.addPath(pp);
      if (fn === pTube || fn === pLug || fn === pRib || fn === pRibPosts) BARREL.addPath(pp);
    });
  }
  function proxy(pp) {
    return {
      beginPath: function () {}, moveTo: function (a, b) { pp.moveTo(a, b); }, lineTo: function (a, b) { pp.lineTo(a, b); },
      quadraticCurveTo: function (a, b, c2, d) { pp.quadraticCurveTo(a, b, c2, d); },
      bezierCurveTo: function (a, b, c2, d, e, f) { pp.bezierCurveTo(a, b, c2, d, e, f); },
      save: function () {}, restore: function () {}, translate: function () {}, rotate: function () {},
      rect: function (a, b, c2, d) { pp.rect(a, b, c2, d); }, closePath: function () { pp.closePath(); },
      arc: function (a, b, r, s, e, cc) { pp.arc(a, b, r, s, e, cc); }
    };
  }

  // inner bevel: light along the upper inside edge, dark along the lower
  function bevel(g, fn, light, dark, w, off) {
    off = off || 1;
    g.save(); fn(g); g.clip();
    g.lineWidth = w;
    g.translate(off * 0.6, off); fn(g); g.strokeStyle = light; g.stroke();
    g.translate(-off * 1.2, -off * 2); fn(g); g.strokeStyle = dark; g.stroke();
    g.restore();
  }
  function outline(g, fn, colr, w) { fn(g); g.lineWidth = w; g.strokeStyle = colr; g.stroke(); }
  // a rim line: fn's outline shifted by (dx,dy), kept inside a box
  function rim(g, fn, box, dx, dy, colr, w) {
    g.save(); g.beginPath(); g.rect(box[0], box[1], box[2], box[3]); g.clip();
    g.translate(dx, dy); fn(g); g.lineWidth = w; g.strokeStyle = colr; g.stroke();
    g.restore();
  }

  function screw(g, p, x, y, r, a) {
    var gr = g.createRadialGradient(x - r * 0.35, y - r * 0.4, 0.2, x, y, r);
    stops(gr, [0, css(p.spec), 0.45, css(p.s1), 1, css(p.deep)]);
    g.beginPath(); g.arc(x, y, r + 0.7, 0, TAU); g.fillStyle = css(p.ink, 0.7); g.fill();
    g.beginPath(); g.arc(x, y, r, 0, TAU); g.fillStyle = gr; g.fill();
    g.save(); g.translate(x, y); g.rotate(a);
    g.fillStyle = css(p.ink, 0.9); g.fillRect(-r * 0.9, -0.45, r * 1.8, 0.9);
    g.fillStyle = css(p.spec, 0.35); g.fillRect(-r * 0.9, 0.45, r * 1.8, 0.4);
    g.restore();
  }
  // a drilled hole in a lit face: bright lip below, black bore
  function hole(g, p, x, y, rx, ry) {
    g.beginPath(); g.ellipse(x + 0.3, y + 0.5, rx + 0.9, ry + 0.9, 0, 0, TAU); g.fillStyle = css(p.sky, 0.55); g.fill();
    g.beginPath(); g.ellipse(x, y, rx + 0.6, ry + 0.6, 0, 0, TAU); g.fillStyle = css(p.ink, 0.9); g.fill();
    g.beginPath(); g.ellipse(x, y, rx, ry, 0, 0, TAU); g.fillStyle = '#040405'; g.fill();
  }

  // ------------------------------------------------------------------ the frame window: a recessed interior
  function paintWindow(g, p) {
    var gr, x0 = -WX;
    g.save();
    pWindow(g); g.clip();
    // far wall of the frame, in shadow; the floor (crane cut) catches a little bounce
    gr = g.createLinearGradient(0, -WY, 0, WY);
    stops(gr, [0, css(p.ink), 0.18, css(mix(p.ink, p.deep, 0.7)), 0.55, css(mix(p.deep, p.mid, 0.35)), 0.85, css(mix(p.deep, p.gnd, 0.5)), 1, css(p.ink)]);
    g.fillStyle = gr; g.fillRect(-WX, -WY, WX * 2, WY * 2);
    // underside of the top strap
    g.fillStyle = css(p.mid, 0.5); g.fillRect(-WX, -WY, WX * 2, 2.2);
    // recoil shield: the breech wall at the back, seen a little from behind
    gr = g.createLinearGradient(x0, 0, x0 + 14, 0);
    stops(gr, [0, css(p.mid), 0.3, css(p.lite), 0.62, css(p.s1), 1, css(p.deep)]);
    g.beginPath(); g.moveTo(x0, -WY); g.lineTo(x0 + 9, -WY + 3.5); g.quadraticCurveTo(x0 + 14.5, 0, x0 + 9, WY - 3.5);
    g.lineTo(x0, WY); g.closePath();
    g.fillStyle = gr; g.fill();
    g.lineWidth = 0.9; g.strokeStyle = css(p.ink, 0.85); g.stroke();
    hole(g, p, x0 + 6, BY, 1.5, 2.6);                 // firing-pin hole
    hole(g, p, x0 + 6.8, 0, 1.6, 2.8);                // centre-pin hole
    // the hand, peeking out of its slot
    g.fillStyle = css(p.ink); g.fillRect(x0 + 7.2, 8, 3.6, 13);
    gr = g.createLinearGradient(x0 + 7.5, 0, x0 + 11, 0); stops(gr, [0, css(p.lite), 1, css(p.mid)]);
    g.fillStyle = gr; g.beginPath(); g.moveTo(x0 + 7.8, 20); g.lineTo(x0 + 7.8, 12); g.lineTo(x0 + 10.4, 9.2); g.lineTo(x0 + 10.4, 20); g.closePath(); g.fill();
    // cylinder bolt rising from the floor
    g.fillStyle = css(p.ink); g.fillRect(-7.5, WY - 7, 7.5, 7);
    gr = g.createLinearGradient(0, WY - 6.5, 0, WY); stops(gr, [0, css(p.sky), 0.25, css(p.lite), 1, css(p.mid)]);
    g.fillStyle = gr; g.fillRect(-6.8, WY - 6.4, 6.1, 6.4);
    // forcing cone: the barrel's breech end, just in front of the drum
    gr = g.createLinearGradient(0, BY - 8.5, 0, BY + 8.5); stops(gr, roundStops(p));
    g.fillStyle = gr; g.fillRect(WX - 6.5, BY - 8.5, 6.5, 17);
    g.fillStyle = css(p.ink); g.fillRect(WX - 7.4, BY - 8.5, 1.3, 17);
    g.fillStyle = css(p.sky, 0.5); g.fillRect(WX - 6, BY - 8.5, 0.8, 17);
    // ambient occlusion along the rim of the opening
    pWindow(g); g.lineWidth = 5; g.strokeStyle = css(p.ink, 0.4); g.stroke();
    g.restore();
    // crisp edge + a lit sill under it
    pWindow(g); g.lineWidth = 1; g.strokeStyle = css(p.ink, 0.8); g.stroke();
    g.fillStyle = css(p.sky, 0.4); g.fillRect(-WX + 1.5, WY + 0.9, WX * 2 - 3, 0.9);
  }

  // ------------------------------------------------------------------ the static sprite
  function paintStatic(g, p) {
    g.lineJoin = 'round'; g.lineCap = 'round';
    var gr, i;

    // trigger guard (drawn first: the frame overlaps its roots)
    pGuard(g); g.lineWidth = 9; g.strokeStyle = css(p.ink); g.stroke();
    gr = g.createLinearGradient(0, 40, 0, 86);
    stops(gr, [0, css(p.mid), 0.55, css(p.s1), 0.8, css(p.lite), 1, css(p.deep)]);
    pGuard(g); g.lineWidth = 6.6; g.strokeStyle = gr; g.stroke();
    g.save(); g.translate(0, -1.2); pGuard(g); g.lineWidth = 1.2; g.strokeStyle = css(p.sky, 0.5); g.stroke(); g.restore();

    // frame: one light for the whole side, so the tang is the same steel as the strap
    gr = g.createLinearGradient(-20, -44, 0, 48);
    stops(gr, flatStops(p));
    pFrame(g); g.fillStyle = gr; g.fill();
    // the tang behind the hammer: round over the top, falling into shade toward the grip
    g.save(); pFrame(g); g.clip();
    gr = g.createLinearGradient(0, -32, 0, -2);
    stops(gr, [0, css(p.s1), 0.35, css(mix(p.s1, p.mid, 0.5)), 0.75, css(p.mid), 1, css(p.deep)]);
    g.fillStyle = gr; g.fillRect(-100, -34, 52.4, 32);
    g.restore();
    bevel(g, pFrame, css(p.sky, 0.75), css(p.ink, 0.8), 1.6, 1.1);
    paintWindow(g, p);
    // recoil shield / yoke seams
    g.lineWidth = 0.9;
    g.strokeStyle = css(p.ink, 0.85);
    g.beginPath(); g.moveTo(-38.6, -32); g.lineTo(-38.6, 33); g.moveTo(39, -9); g.lineTo(39, 31);
    g.quadraticCurveTo(39, 37, 33.5, 37); g.lineTo(-30, 37); g.stroke();
    // side plate
    g.strokeStyle = css(p.ink, 0.7);
    g.beginPath(); g.moveTo(-36, 42); g.bezierCurveTo(-40, 26, -42, 8, -44.5, -1); g.quadraticCurveTo(-46.5, -6.5, -52, -6.5);
    g.lineTo(-71, -6.5); g.quadraticCurveTo(-79, -7.5, -84, -14);
    g.stroke();
    g.strokeStyle = css(p.sky, 0.22);
    g.beginPath(); g.moveTo(-35, 42); g.bezierCurveTo(-39, 26, -41, 8, -43.5, -1); g.stroke();
    // shadow inside the hammer slot
    g.fillStyle = css(p.ink, 0.55); g.fillRect(-70, -12.2, 23, 1.6);
    // breech face behind the hammer (lit), with the firing-pin bushing standing proud of it
    gr = g.createLinearGradient(-47, 0, -43.5, 0); stops(gr, [0, css(p.sky, 0.9), 0.5, css(p.lite, 0.4), 1, css(p.lite, 0)]);
    g.fillStyle = gr; g.fillRect(-47, -39, 3.5, 24);
    gr = g.createRadialGradient(-51.5, BY - 2.4, 0.3, -50, BY, 6.2);
    stops(gr, [0, css(p.spec), 0.4, css(mix(p.lite, p.spec, 0.3)), 1, css(p.mid)]);
    pBushing(g); g.fillStyle = gr; g.fill(); g.lineWidth = 1.1; g.strokeStyle = css(p.ink); g.stroke();
    hole(g, p, -51.2, BY, 1.6, 2.9);

    // underlug + barrel
    gr = g.createLinearGradient(0, -9, 0, 14);
    stops(gr, flatStops(p));
    pLug(g); g.fillStyle = gr; g.fill();
    bevel(g, pLug, css(p.sky, 0.6), css(p.ink, 0.8), 1.4, 1);
    gr = g.createLinearGradient(0, -31.5, 0, -7.5);
    stops(gr, roundStops(p));
    pTube(g); g.fillStyle = gr; g.fill();
    // shadow line where the round tube meets the flat lug
    g.fillStyle = css(p.ink, 0.55); g.fillRect(46, -8.8, MX - 46, 1.4);
    g.fillStyle = css(p.sky, 0.28); g.fillRect(48, -7.2, MX - 52, 0.8);
    // vent rib: posts + windows, then the top plate
    gr = g.createLinearGradient(0, -36.5, 0, -31);
    stops(gr, [0, css(p.lite), 1, css(p.mid)]);
    pRibPosts(g); g.fillStyle = gr; g.fill();
    for (i = 0; i < 8; i++) {
      var vx = 58 + i * 23.5;
      if (vx + 15 > 236) break;
      g.fillStyle = css(p.ink); g.fillRect(vx, -36.2, 15, 4.6);
      g.fillStyle = css(p.gnd, 0.55); g.fillRect(vx + 1, -32.4, 13, 0.9);
    }
    gr = g.createLinearGradient(0, -40.5, 0, -36.3);
    stops(gr, [0, css(p.spec), 0.35, css(p.lite), 1, css(p.s1)]);
    pRib(g); g.fillStyle = gr; g.fill();
    // fine serrations on the rib top
    g.fillStyle = css(p.ink, 0.25);
    for (i = 50; i < 240; i += 3) g.fillRect(i, -39.6, 1, 1.2);
    // front sight ramp with coloured insert
    gr = g.createLinearGradient(0, -54, 0, -40);
    stops(gr, [0, css(p.spec), 0.25, css(p.lite), 1, css(p.mid)]);
    pFront(g); g.fillStyle = gr; g.fill();
    g.fillStyle = css(p.insert);
    g.beginPath(); g.moveTo(243.4, -52.6); g.lineTo(247.6, -52.6); g.lineTo(247, -45); g.lineTo(243.2, -45); g.closePath(); g.fill();
    g.fillStyle = css(WHITE, 0.45); g.fillRect(243.6, -52.2, 1.2, 5.4);
    screw(g, p, 255, -44.5, 1.5, 0.4);
    outline(g, pFront, css(p.ink), 1.2);
    // muzzle crown
    g.fillStyle = css(p.ink, 0.7); g.fillRect(MX - 1.3, -40.5, 1.3, 50);
    g.fillStyle = css(p.sky, 0.45); g.fillRect(MX - 3.2, -31, 1, 23);
    // outlines of barrel parts
    outline(g, pRib, css(p.ink), 1.2);
    outline(g, pTube, css(p.ink), 1.2);
    outline(g, pLug, css(p.ink), 1.4);
    // ejector-rod locking lug at the front of the shroud
    g.fillStyle = css(p.ink, 0.5); g.beginPath(); g.arc(MX - 10, 2.5, 2.2, 0, TAU); g.fill();
    g.fillStyle = css(p.sky, 0.3); g.beginPath(); g.arc(MX - 10.4, 2, 1, 0, TAU); g.fill();

    // top strap edge + rear sight
    g.fillStyle = css(p.spec, 0.55); g.fillRect(-46, -40, 94, 1.1);
    gr = g.createLinearGradient(0, -51, 0, -40);
    stops(gr, [0, css(p.spec), 0.3, css(p.lite), 0.7, css(p.s1), 1, css(p.deep)]);
    pRear(g); g.fillStyle = gr; g.fill();
    outline(g, pRear, css(p.ink), 1.1);
    g.fillStyle = css(p.ink, 0.8); g.fillRect(-41, -44.5, 26, 0.9);
    screw(g, p, -28, -47.3, 1.7, 1.2);
    screw(g, p, -8, -42, 1.3, 0.2);

    // frame outline & screws
    outline(g, pFrame, css(p.ink), 1.5);
    screw(g, p, 41, 26, 2.6, 0.7);          // yoke screw
    screw(g, p, -46, 30, 2.4, -0.4);        // side plate screws
    g.fillStyle = css(p.ink, 0.8); g.beginPath(); g.arc(HP.x, HP.y, 3.4, 0, TAU); g.fill();
    var hs = g.createRadialGradient(HP.x - 1, HP.y - 1, 0.3, HP.x, HP.y, 2.8);
    stops(hs, [0, css(p.spec), 0.5, css(p.lite), 1, css(p.mid)]);
    g.fillStyle = hs; g.beginPath(); g.arc(HP.x, HP.y, 2.6, 0, TAU); g.fill();
    // trigger pivot pin
    g.fillStyle = css(p.ink, 0.7); g.beginPath(); g.arc(TP.x, TP.y, 2, 0, TAU); g.fill();
    g.fillStyle = css(p.lite); g.beginPath(); g.arc(TP.x, TP.y, 1.3, 0, TAU); g.fill();

    // engraving on the lug flat
    g.save();
    g.font = '700 6.5px ' + p.font; g.textBaseline = 'middle';
    g.fillStyle = css(p.sky, 0.22); g.fillText('.44  MAGNUM', 150.6, 5.4);
    g.fillStyle = css(p.ink, 0.55); g.fillText('.44  MAGNUM', 150, 4.6);
    g.restore();

    // polished reflections: soft diagonal sheen bands over all the steel
    metalPaths();
    if (METAL) {
      g.save(); g.clip(METAL); g.clip(EXCL, 'evenodd');
      g.globalCompositeOperation = 'lighter';
      [[-10, 0.1, 40], [128, 0.09, 56], [214, 0.06, 26]].forEach(function (b) {
        var sg = g.createLinearGradient(b[0] - b[2], 0, b[0] + b[2], 0);
        stops(sg, [0, css(p.sky, 0), 0.35, css(p.sky, b[1] * 0.7), 0.5, css(p.spec, b[1]), 0.65, css(p.sky, b[1] * 0.7), 1, css(p.sky, 0)]);
        g.save(); g.transform(1, 0, -0.45, 1, 0, 0); g.fillStyle = sg; g.fillRect(b[0] - b[2] - 60, -70, b[2] * 2 + 120, 150); g.restore();
      });
      g.restore();
    }

    // rim light. Warm themes: the lamp along the top edges. Neon: two-tone tubes -- cyan on every top
    // edge, pink on every bottom edge -- instead of a halo.
    g.save();
    g.globalCompositeOperation = 'lighter';
    var tang = [-97, -34, 22, 12];
    if (p.neon) {
      g.shadowBlur = 4 * g.__s;
      g.shadowColor = css(p.rimTop, 0.9); g.strokeStyle = css(p.rimTop, 0.9); g.lineWidth = 1.3;
      g.beginPath(); g.moveTo(47, -40.3); g.lineTo(242, -40.3); g.moveTo(-46, -40.2); g.lineTo(46, -40.2);
      g.moveTo(-40.5, -50.4); g.lineTo(-22, -50.4); g.moveTo(243.5, -53.2); g.lineTo(249, -53.2);
      g.moveTo(MX - 0.6, -39); g.lineTo(MX - 0.6, -9);
      g.stroke();
      rim(g, pFrame, tang, 0.3, 0.9, css(p.rimTop, 0.8), 1.2);
      g.shadowColor = css(p.rimBot, 0.9); g.strokeStyle = css(p.rimBot, 0.9); g.lineWidth = 1.3;
      g.beginPath(); g.moveTo(60, 13.4); g.lineTo(MX - 9, 13.4); g.moveTo(-20, 41.4); g.lineTo(28, 41.4); g.stroke();
      g.beginPath(); g.moveTo(MX - 0.6, -6); g.lineTo(MX - 0.6, 5); g.stroke();
      rim(g, pGuard, [-36, 60, 76, 30], 0, 2.9, css(p.rimBot, 0.9), 1.3);
    } else {
      g.strokeStyle = css(p.rimTop, 0.5); g.lineWidth = 1.1;
      g.beginPath(); g.moveTo(47, -40.3); g.lineTo(242, -40.3); g.moveTo(-46, -40.2); g.lineTo(46, -40.2);
      g.moveTo(-40.5, -50.4); g.lineTo(-22, -50.4); g.moveTo(243.5, -53.2); g.lineTo(249, -53.2);
      g.stroke();
      rim(g, pFrame, tang, 0.3, 0.9, css(p.rimTop, 0.2), 1);
    }
    g.restore();

    // grips (raised above the frame: cast a small shadow on it)
    g.save();
    g.shadowColor = 'rgba(0,0,0,.55)'; g.shadowBlur = 5 * g.__s; g.shadowOffsetX = 1.5 * g.__s; g.shadowOffsetY = 2 * g.__s;
    pGrip(g); g.fillStyle = css(p.g2); g.fill();
    g.restore();
    paintGrip(g, p);
  }

  function paintGrip(g, p) {
    var gr, i, rnd = mulberry(p.wood ? 1337 : 4242);
    g.save();
    pGrip(g); g.clip();
    // base: lit from the upper front
    gr = g.createLinearGradient(-44, 24, -118, 130);
    stops(gr, [0, css(p.gHi), 0.3, css(p.g1), 0.75, css(mix(p.g1, p.g2, 0.6)), 1, css(p.g2)]);
    g.fillStyle = gr; g.fillRect(-140, -20, 130, 170);
    var ax = -0.39, ay = 0.92;               // grip axis
    if (p.wood) {
      // figured grain running along the stock
      for (i = 0; i < 52; i++) {
        var o = -46 + i * 1.8 + rnd() * 1.2, ph = rnd() * 6, amp = 1.2 + rnd() * 3;
        g.beginPath();
        for (var k = 0; k <= 26; k++) {
          var tt = -30 + k * 7.5, wv = Math.sin(tt * 0.045 + ph) * amp + Math.sin(tt * 0.12 + ph * 2) * amp * 0.3;
          var x = -84 + ax * tt + (o + wv) * 0.92, y = 64 + ay * tt + (o + wv) * 0.39;
          if (k) g.lineTo(x, y); else g.moveTo(x, y);
        }
        var dark = rnd() < 0.62;
        g.strokeStyle = css(dark ? p.gDeep : p.gHi, dark ? 0.1 + rnd() * 0.2 : 0.06 + rnd() * 0.1);
        g.lineWidth = 0.5 + rnd() * 1.3;
        g.stroke();
      }
      // checkered panel with a carved border
      g.save();
      pPanel(g); g.clip();
      g.fillStyle = css(p.gDeep, 0.22); g.fillRect(-120, 20, 70, 110);
      g.lineWidth = 0.75;
      for (var dir = -1; dir <= 1; dir += 2) {
        g.save(); g.translate(-86, 76); g.rotate(0.4 + dir * 0.62);
        for (var l = -70; l <= 70; l += 2.4) {
          g.strokeStyle = css(p.gDeep, 0.55); g.beginPath(); g.moveTo(l, -80); g.lineTo(l, 80); g.stroke();
          g.strokeStyle = css(p.gHi, 0.14); g.beginPath(); g.moveTo(l + 0.9, -80); g.lineTo(l + 0.9, 80); g.stroke();
        }
        g.restore();
      }
      g.restore();
      g.save(); g.translate(0.6, 0.8); pPanel(g); g.lineWidth = 1; g.strokeStyle = css(p.gHi, 0.3); g.stroke(); g.restore();
      pPanel(g); g.lineWidth = 1.1; g.strokeStyle = css(p.gDeep, 0.8); g.stroke();
    } else {
      // pebbled rubber
      for (i = 0; i < 1100; i++) {
        var px = -130 + rnd() * 110, py = -14 + rnd() * 158, r = 0.5 + rnd() * 0.9;
        g.fillStyle = rnd() < 0.55 ? css(p.gDeep, 0.4) : css(p.gHi, 0.2);
        g.fillRect(px, py, r, r);
      }
    }
    // finger groove relief: dark in the hollows, light on the ridges
    var f = GRIP_FRONT;
    [[2, -1], [4, 1], [7, -1], [9, 1], [11, -1]].forEach(function (q) {
      var pt = f[q[0]], rg = g.createRadialGradient(pt[0], pt[1], 0, pt[0], pt[1], 22);
      if (q[1] < 0) stops(rg, [0, css(p.gDeep, 0.55), 1, css(p.gDeep, 0)]);
      else stops(rg, [0, css(p.gHi, 0.3), 1, css(p.gHi, 0)]);
      g.fillStyle = rg; g.fillRect(pt[0] - 24, pt[1] - 24, 48, 48);
    });
    // rounded edges: a soft light band inside the back edge, shade along the front
    g.save(); g.translate(5, -1.5); pGrip(g); g.lineWidth = 7; g.strokeStyle = css(p.gHi, p.wood ? 0.16 : 0.14); g.stroke(); g.restore();
    g.save(); g.translate(2.2, -0.6); pGrip(g); g.lineWidth = 1.6; g.strokeStyle = css(p.gHi, p.wood ? 0.3 : 0.3); g.stroke(); g.restore();
    g.save(); g.translate(-4, 1); pGrip(g); g.lineWidth = 6; g.strokeStyle = css(p.gDeep, 0.3); g.stroke(); g.restore();
    // varnish / sheen spot
    var vg = g.createRadialGradient(-92, 30, 0, -92, 30, 34);
    stops(vg, [0, css(WHITE, p.wood ? 0.13 : 0.08), 1, css(WHITE, 0)]);
    g.fillStyle = vg; g.fillRect(-130, -10, 80, 80);
    // bottom falloff
    gr = g.createLinearGradient(0, 104, 0, 142);
    stops(gr, [0, 'rgba(0,0,0,0)', 1, 'rgba(0,0,0,.4)']);
    g.fillStyle = gr; g.fillRect(-140, 100, 130, 45);
    g.restore();
    // edges
    bevel(g, pGrip, css(p.gHi, 0.5), css(p.gDeep, 0.8), 1.6, 1);
    g.save(); g.globalCompositeOperation = 'lighter';
    if (p.neon) {
      // pink tube along the heel and butt
      g.shadowColor = css(p.rimBot, 0.9); g.shadowBlur = 4 * g.__s;
      rim(g, pGrip, [-128, 96, 70, 50], 0.9, -0.9, css(p.rimBot, 0.9), 1.3);
    } else {
      // lamp rim down the back strap
      rim(g, pGrip, [-126, -10, 34, 118], 1.1, 0.3, css(p.rimTop, p.wood ? 0.22 : 0.42), 1.3);
    }
    g.restore();
    outline(g, pGrip, css(p.gDeep), 1.4);
    // medallion
    var mx = -80, my = 24, mg = g.createRadialGradient(mx - 1.5, my - 1.5, 0.3, mx, my, 5);
    stops(mg, [0, css(mix(p.acc, WHITE, 0.6)), 0.5, css(p.acc), 1, css(mix(p.acc, BLACK, 0.45))]);
    g.beginPath(); g.arc(mx, my, 5.6, 0, TAU); g.fillStyle = css(p.gDeep, 0.9); g.fill();
    g.beginPath(); g.arc(mx, my, 4.6, 0, TAU); g.fillStyle = mg; g.fill();
    g.beginPath(); g.arc(mx, my, 2.6, 0, TAU); g.lineWidth = 0.6; g.strokeStyle = css(mix(p.acc, BLACK, 0.5), 0.8); g.stroke();
  }

  // outer silhouette contour: the sprite's alpha, dilated by EDGE px, tucked under everything
  function contour(cv, g, w, h, p, r) {
    var sil = makeCanvas(w, h), sg = sil.getContext('2d');
    sg.drawImage(cv, 0, 0);
    sg.globalCompositeOperation = 'source-in'; sg.fillStyle = css(p.edge); sg.fillRect(0, 0, w, h);
    g.save();
    g.setTransform(1, 0, 0, 1, 0, 0);
    g.globalCompositeOperation = 'destination-over';
    for (var k = 0; k < 16; k++) { var a = k * TAU / 16; g.drawImage(sil, Math.cos(a) * r, Math.sin(a) * r); }
    g.restore();
  }

  function sprite(p, s) {
    var q = Math.max(0.5, Math.ceil(s * 8 - 0.001) / 8);
    for (var i = 0; i < p.sprites.length; i++) if (p.sprites[i].q === q) return p.sprites[i];
    var w = Math.ceil((SB.x1 - SB.x0) * q), h = Math.ceil((SB.y1 - SB.y0) * q);
    var cv = makeCanvas(w, h), g = cv.getContext('2d');
    g.__s = q;
    g.setTransform(q, 0, 0, q, -SB.x0 * q, -SB.y0 * q);
    paintStatic(g, p);
    contour(cv, g, w, h, p, EDGE * q);
    // a faint backlight so the silhouette separates from the wall (neon keeps a coloured glow)
    var tmp = makeCanvas(w, h), tg = tmp.getContext('2d');
    tg.drawImage(cv, 0, 0);
    g.setTransform(1, 0, 0, 1, 0, 0);
    g.globalCompositeOperation = 'destination-over';
    g.shadowColor = p.neon ? css(p.acc, 0.3) : css(mix(p.lamp, WHITE, 0.2), 0.07);
    g.shadowBlur = (p.neon ? 9 : 5) * q;
    g.shadowOffsetX = w + 50;
    g.drawImage(tmp, -(w + 50), 0);
    g.shadowColor = 'rgba(0,0,0,0)'; g.shadowOffsetX = 0;
    g.globalCompositeOperation = 'source-over';
    var sp = { q: q, cv: cv, ham: hammerSprite(p, q) };
    p.sprites.push(sp);
    if (p.sprites.length > 4) p.sprites.shift();
    return sp;
  }

  // ------------------------------------------------------------------ hammer (pivot-local)
  // At rest the hammer sits down in the frame: its checkered spur lies low behind the rear sight and laid
  // back along the tang, barely proud of the top strap, face flush with the breech. At full cock (TRAVEL rad back) the spur
  // sweeps back over the tang and a wide notch opens in front of it, showing the firing-pin bushing.
  // Outline in gun space (the path helper subtracts the pivot).
  var HAM = [
    ['M', -47, -10],
    ['L', -47, -16.4],
    ['L', -44.6, -17.2], ['Q', -43, -19, -44.6, -20.8], ['L', -47, -21.6],     // firing-pin nose
    ['L', -47, -36.5],
    ['Q', -47, -42, -51.5, -42.4],                                              // front top
    ['C', -58, -43, -65, -42.8, -71, -41.8],                                    // checkered spur top
    ['C', -76.5, -41, -81.5, -39.2, -85.6, -36],                                // spur laid back along the tang
    ['Q', -88.8, -33.4, -86.6, -32.2],                                          // rounded tip, tucked down
    ['C', -84.5, -31.6, -81, -33.2, -76.2, -31],                                // under the spur, just off the tang
    ['C', -73.6, -28, -72.8, -22, -71.4, -14],                                  // back of the hammer
    ['C', -70.4, -2, -69, 9, -62, 10],
    ['Q', -50, 10, -47, -10],
    ['Z']
  ];
  function pHammer(g) {
    var X = HP.x, Y = HP.y;
    g.beginPath();
    for (var i = 0; i < HAM.length; i++) {
      var s = HAM[i];
      if (s[0] === 'M') g.moveTo(s[1] - X, s[2] - Y);
      else if (s[0] === 'L') g.lineTo(s[1] - X, s[2] - Y);
      else if (s[0] === 'Q') g.quadraticCurveTo(s[1] - X, s[2] - Y, s[3] - X, s[4] - Y);
      else if (s[0] === 'C') g.bezierCurveTo(s[1] - X, s[2] - Y, s[3] - X, s[4] - Y, s[5] - X, s[6] - Y);
      else g.closePath();
    }
  }
  function hammerSprite(p, q) {
    var key = 'ham' + q;
    if (p.cache[key]) return p.cache[key];
    var w = Math.ceil((HB.x1 - HB.x0) * q), h = Math.ceil((HB.y1 - HB.y0) * q);
    var cv = makeCanvas(w, h), g = cv.getContext('2d'), i;
    g.setTransform(q, 0, 0, q, -HB.x0 * q, -HB.y0 * q);
    g.lineJoin = 'round'; g.lineCap = 'round';
    var X = HP.x, Y = HP.y;          // gun -> pivot-local offsets for the detail below
    // silhouette contour
    pHammer(g); g.lineWidth = EDGE * 2; g.strokeStyle = css(p.edge); g.stroke();
    var gr = g.createLinearGradient(0, -46 - Y, 0, -8 - Y);
    stops(gr, [0, css(p.spec), 0.1, css(mix(p.lite, p.hi, 0.35)), 0.3, css(p.lite), 0.5, css(p.s1), 0.62, css(p.mid),
      0.85, css(p.s1), 1, css(p.deep)]);
    pHammer(g); g.fillStyle = gr; g.fill();
    g.save(); pHammer(g); g.clip();
    if (p.warm) {
      // colour case hardening: straw and peacock-blue swirls over the steel
      var rnd = mulberry(90210);
      g.globalCompositeOperation = 'color';
      for (i = 0; i < 40; i++) {
        var cx = -90 - X + rnd() * 46, cy = -48 - Y + rnd() * 44, r = 2 + rnd() * 5, cc = CASE[Math.floor(rnd() * CASE.length)];
        var rg = g.createRadialGradient(cx, cy, 0, cx, cy, r);
        stops(rg, [0, css(cc, 0.4 + rnd() * 0.3), 1, css(cc, 0)]);
        g.fillStyle = rg; g.fillRect(cx - r, cy - r, r * 2, r * 2);
      }
      g.globalCompositeOperation = 'source-over';
    }
    // flat side of the hammer: a recessed panel below the spur
    g.beginPath(); g.moveTo(-49.5 - X, -35.5 - Y); g.quadraticCurveTo(-66 - X, -38 - Y, -68 - X, -29 - Y);
    g.quadraticCurveTo(-69 - X, -19 - Y, -66.5 - X, -12 - Y);
    g.lineWidth = 1; g.strokeStyle = css(p.ink, 0.4); g.stroke();
    g.beginPath(); g.moveTo(-49.5 - X, -34.3 - Y); g.quadraticCurveTo(-64.8 - X, -36.8 - Y, -66.8 - X, -29 - Y);
    g.lineWidth = 0.7; g.strokeStyle = css(p.sky, 0.3); g.stroke();
    // checkered spur top
    g.lineWidth = 0.9;
    for (i = 0; i < 11; i++) {
      var x = -53 - i * 2.6 - X, y = -42.4 + (i > 5 ? (i - 5) * (i - 5) * 0.16 : 0) - Y;
      g.strokeStyle = css(p.ink, 0.8); g.beginPath(); g.moveTo(x + 0.8, y - 1); g.lineTo(x - 0.4, y + 4); g.stroke();
      g.strokeStyle = css(p.spec, 0.4); g.beginPath(); g.moveTo(x + 1.9, y - 1); g.lineTo(x + 0.7, y + 4); g.stroke();
    }
    // bevel
    g.lineWidth = 1.4;
    g.translate(0.6, 1); pHammer(g); g.strokeStyle = css(p.sky, 0.6); g.stroke();
    g.translate(-1.2, -2); pHammer(g); g.strokeStyle = css(p.ink, 0.7); g.stroke();
    g.restore();
    pHammer(g); g.lineWidth = 1.2; g.strokeStyle = css(p.ink); g.stroke();
    // firing-pin nose catches the light
    g.fillStyle = css(p.spec, 0.8); g.fillRect(-45.6 - X, -19.6 - Y, 2.2, 0.9);
    // rim along the spur top: cyan tube on neon, lamp elsewhere
    g.globalCompositeOperation = 'lighter';
    if (p.neon) { g.shadowColor = css(p.rimTop, 0.9); g.shadowBlur = 3 * q; }
    g.strokeStyle = css(p.rimTop, p.neon ? 0.9 : 0.3); g.lineWidth = p.neon ? 1.3 : 1;
    g.beginPath(); g.moveTo(-47.6 - X, -36.5 - Y); g.quadraticCurveTo(-47.6 - X, -41.4 - Y, -51.5 - X, -41.7 - Y);
    g.bezierCurveTo(-58 - X, -42.3 - Y, -65 - X, -42.1 - Y, -71 - X, -41.1 - Y);
    g.bezierCurveTo(-76.3 - X, -40.3 - Y, -81 - X, -38.6 - Y, -85 - X, -35.5 - Y); g.stroke();
    g.globalCompositeOperation = 'source-over'; g.shadowBlur = 0;
    var sp = { cv: cv, w: w / q, h: h / q };
    p.cache[key] = sp;
    return sp;
  }
  function drawHammer(c, p, ang, hs) {
    c.save();
    c.beginPath();
    for (var h = 0; h < HCLIP.length; h++) c.lineTo(HCLIP[h][0], HCLIP[h][1]);
    c.closePath(); c.clip();
    c.translate(HP.x, HP.y); c.rotate(ang);
    c.drawImage(hs.cv, HB.x0, HB.y0, hs.w, hs.h);
    var k = -ang / TRAVEL;
    if (k > 0.05) {
      // cocked, the checkered spur faces up into the lamp: a gleam that grows with the travel
      var X = HP.x, Y = HP.y;
      c.globalCompositeOperation = 'lighter';
      c.lineCap = 'round';
      k = Math.min(1, k);
      c.beginPath(); c.moveTo(-50 - X, -41.2 - Y); c.bezierCurveTo(-58 - X, -41.8 - Y, -65 - X, -41.6 - Y, -71 - X, -40.6 - Y);
      c.bezierCurveTo(-76 - X, -39.8 - Y, -80.5 - X, -38.2 - Y, -84 - X, -35.4 - Y);
      c.lineWidth = 2.4; c.strokeStyle = css(p.neon ? p.rimTop : p.spec, 0.55 * k); c.stroke();
      // ...and the face that sat against the breech is out in the light, edging the open notch
      c.beginPath(); c.moveTo(-48 - X, -13 - Y); c.lineTo(-48 - X, -16 - Y); c.moveTo(-48 - X, -22.4 - Y); c.lineTo(-48 - X, -36.4 - Y);
      c.lineWidth = 1.6; c.strokeStyle = css(p.sky, 0.7 * k); c.stroke();
      c.globalCompositeOperation = 'source-over';
    }
    c.restore();
    // the slot wall's top edge casts a thin shadow onto the hammer
    c.beginPath(); c.moveTo(-47, -12.4); c.lineTo(-70, -12.6); c.bezierCurveTo(-73.4, -14.4, -74.4, -21.4, -76.4, -25.6);
    c.bezierCurveTo(-79.4, -30.6, -86, -31.6, -90.6, -27.8);
    c.lineWidth = 1.4; c.strokeStyle = css(p.ink, 0.7); c.stroke();
  }

  // ------------------------------------------------------------------ trigger (pivot-local)
  function pTrigger(g) {
    g.beginPath();
    g.moveTo(-6, -4);
    g.lineTo(7, -4);
    g.bezierCurveTo(9, 8, 4, 18, 9.5, 29);
    g.quadraticCurveTo(11, 34.5, 5.5, 34);
    g.bezierCurveTo(-4.5, 31, -9, 18, -7, 4);
    g.closePath();
  }
  function drawTrigger(c, p, ang) {
    var gr = p.cache.trig;
    if (!gr) {
      gr = p.cache.trig = c.createLinearGradient(-7, 0, 10, 0);
      if (p.warm) stops(gr, [0, css(mix(p.acc, BLACK, 0.62)), 0.32, css(mix(p.acc, BLACK, 0.22)), 0.66, css(p.acc),
        0.84, css(mix(p.acc, WHITE, 0.62)), 1, css(mix(p.acc, BLACK, 0.3))]);
      else stops(gr, [0, css(p.deep), 0.35, css(p.s1), 0.72, css(p.lite), 0.86, css(p.sky), 1, css(p.mid)]);
    }
    c.save();
    c.translate(TP.x, TP.y); c.rotate(ang);
    pTrigger(c); c.lineWidth = EDGE * 1.6; c.strokeStyle = css(p.edge); c.stroke();
    c.fillStyle = gr; c.fill();
    c.lineWidth = 1.1; c.strokeStyle = css(p.warm ? mix(p.acc, BLACK, 0.72) : p.ink); c.stroke();
    c.strokeStyle = css(p.warm ? mix(p.acc, WHITE, 0.75) : p.spec, 0.5); c.lineWidth = 0.8;
    c.beginPath(); c.moveTo(6.2, 0); c.bezierCurveTo(7.8, 9, 3.2, 18, 8.2, 28.5); c.stroke();
    if (p.neon) {
      c.globalCompositeOperation = 'lighter';
      c.strokeStyle = css(p.rimBot, 0.85); c.lineWidth = 1.1;
      c.beginPath(); c.moveTo(8.6, 31.5); c.quadraticCurveTo(9, 33.4, 5.5, 33.2); c.bezierCurveTo(-3.8, 30.4, -8.2, 18, -6.3, 5); c.stroke();
    }
    c.restore();
  }

  // ------------------------------------------------------------------ cylinder
  // 3D frame: yaw (rear face turns toward the viewer) then an in-plane roll, orthographic. The crane
  // arc is shallow: open, the drum hangs just under the window (its lowest point stays above y ~ 85).
  function cylFrame(e) {
    var psi = 0.82 * e, tau = -0.12 * e;
    var cp = Math.cos(psi), sp = Math.sin(psi), ct = Math.cos(tau), st = Math.sin(tau);
    function rot(x, y, z) {
      var x1 = x * cp + z * sp, z1 = -x * sp + z * cp;
      return [x1 * ct - y * st, x1 * st + y * ct, z1];
    }
    var ang = -0.85 * e, ox = -KP.x, oy = -KP.y, ca = Math.cos(ang), sa = Math.sin(ang);
    return {
      d: rot(1, 0, 0), e1: rot(0, -1, 0), e2: rot(0, 0, 1), k: 1 + 0.1 * e,
      cx: KP.x + ox * ca - oy * sa + 12 * e, cy: KP.y + ox * sa + oy * ca - 1 * e
    };
  }
  function P3(F, u, r, a) {  // point on the cylinder: axial u, radius r, angle a
    var ca = Math.cos(a) * r * F.k, sa = Math.sin(a) * r * F.k; u *= F.k;
    return [F.cx + F.d[0] * u + F.e1[0] * ca + F.e2[0] * sa, F.cy + F.d[1] * u + F.e1[1] * ca + F.e2[1] * sa];
  }
  function hull(pts) {
    pts.sort(function (a, b) { return a[0] - b[0] || a[1] - b[1]; });
    function cross(o, a, b) { return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]); }
    var lo = [], up = [], i;
    for (i = 0; i < pts.length; i++) { while (lo.length >= 2 && cross(lo[lo.length - 2], lo[lo.length - 1], pts[i]) <= 0) lo.pop(); lo.push(pts[i]); }
    for (i = pts.length - 1; i >= 0; i--) { while (up.length >= 2 && cross(up[up.length - 2], up[up.length - 1], pts[i]) <= 0) up.pop(); up.push(pts[i]); }
    up.pop(); lo.pop();
    return lo.concat(up);
  }

  function fluteStops(p) {
    return p.cache.fst || (p.cache.fst = [0, css(p.ink, 0.95), 0.2, css(p.deep, 0.9), 0.46, css(p.mid, 0.7),
      0.76, css(p.clite, 0.7), 0.9, css(p.sky, 0.6), 1, css(p.mid, 0.9)]);
  }
  function cylStops(p) {
    return [0, css(p.ink), 0.06, css(p.cdeep), 0.16, css(p.cs1), 0.25, css(p.sky), 0.31, css(p.spec), 0.37, css(p.sky),
      0.47, css(p.clite), 0.58, css(p.cs1), 0.68, css(p.cdeep), 0.8, css(p.mid), 0.9, css(p.gnd), 1, css(p.ink)];
  }
  // band between two rings over the half that faces the viewer
  function ringBand(c, F, u0, r0, u1, r1) {
    var ph = Math.atan2(F.e2[2], F.e1[2]), k, a, q;
    c.beginPath();
    for (k = 0; k <= 12; k++) { a = ph - Math.PI / 2 + k * Math.PI / 12; q = P3(F, u0, r0, a); if (k) c.lineTo(q[0], q[1]); else c.moveTo(q[0], q[1]); }
    for (k = 12; k >= 0; k--) { a = ph - Math.PI / 2 + k * Math.PI / 12; q = P3(F, u1, r1, a); c.lineTo(q[0], q[1]); }
    c.closePath();
  }
  function ringLine(c, F, u, r) {
    var ph = Math.atan2(F.e2[2], F.e1[2]), k, a, q;
    c.beginPath();
    for (k = 0; k <= 12; k++) { a = ph - Math.PI / 2 + k * Math.PI / 12; q = P3(F, u, r, a); if (k) c.lineTo(q[0], q[1]); else c.moveTo(q[0], q[1]); }
  }

  function drawFlutes(c, p, F, spin, alpha, crisp) {
    var kx = F.d[0] * 11, ky = F.d[1] * 11, fs = fluteStops(p);
    for (var i = 0; i < 6; i++) {
      var a = spin + i * STEP + STEP / 2;
      var nz = F.e1[2] * Math.cos(a) + F.e2[2] * Math.sin(a);
      if (nz > 0.02) {
        var vis = smooth(0.02, 0.4, nz) * alpha;
        var A = P3(F, FL0, CR, a - FLB), B = P3(F, FL1, CR, a - FLB), C2 = P3(F, FL1, CR, a + FLB), D = P3(F, FL0, CR, a + FLB);
        c.beginPath();
        c.moveTo(A[0], A[1]); c.lineTo(B[0], B[1]);
        c.bezierCurveTo(B[0] + kx, B[1] + ky, C2[0] + kx, C2[1] + ky, C2[0], C2[1]);
        c.lineTo(D[0], D[1]);
        c.bezierCurveTo(D[0] - kx, D[1] - ky, A[0] - kx, A[1] - ky, A[0], A[1]);
        // a concave groove: shadowed under its upper lip, its lower wall reflects the lamp
        var gr = c.createLinearGradient((A[0] + B[0]) / 2, (A[1] + B[1]) / 2, (C2[0] + D[0]) / 2, (C2[1] + D[1]) / 2);
        stops(gr, fs);
        c.globalAlpha = vis; c.fillStyle = gr; c.fill();
        if (crisp) {
          c.lineWidth = 0.9; c.strokeStyle = css(p.ink, 0.8); c.stroke();
          c.beginPath(); c.moveTo(D[0], D[1] + 0.6); c.lineTo(C2[0], C2[1] + 0.6);
          c.lineWidth = 0.8; c.strokeStyle = css(p.spec, 0.45); c.stroke();
        }
      }
      // cylinder-stop notch near the rear, at the chamber angle
      var na = a - STEP / 2, nz2 = F.e1[2] * Math.cos(na) + F.e2[2] * Math.sin(na);
      if (nz2 > 0.05) {
        var n1 = P3(F, -26.5, CR, na - 0.05), n2 = P3(F, -22.8, CR, na - 0.05), n3 = P3(F, -22.8, CR, na + 0.05), n4 = P3(F, -26.5, CR, na + 0.05);
        c.globalAlpha = smooth(0.05, 0.4, nz2) * alpha * 0.6;
        c.beginPath(); c.moveTo(n1[0], n1[1]); c.lineTo(n2[0], n2[1]); c.lineTo(n3[0], n3[1]); c.lineTo(n4[0], n4[1]); c.closePath();
        c.fillStyle = css(p.ink, 0.85); c.fill();
        c.beginPath(); c.moveTo(n4[0], n4[1] + 0.5); c.lineTo(n3[0], n3[1] + 0.5);
        c.lineWidth = 0.6; c.strokeStyle = css(p.sky, 0.5); c.stroke();
      }
    }
    c.globalAlpha = 1;
  }

  function hullPath(c, H) {
    c.beginPath();
    for (var i = 0; i < H.length; i++) { if (i) c.lineTo(H[i][0], H[i][1]); else c.moveTo(H[i][0], H[i][1]); }
    c.closePath();
  }
  // a thin lens: tapered at both ends, widest in the middle
  function lens(c, x0, y0, x1, y1, w) {
    var mx = (x0 + x1) / 2, my = (y0 + y1) / 2, dx = x1 - x0, dy = y1 - y0, l = Math.sqrt(dx * dx + dy * dy) || 1;
    var nx = -dy / l * w, ny = dx / l * w;
    c.moveTo(x0, y0); c.quadraticCurveTo(mx + nx, my + ny, x1, y1); c.quadraticCurveTo(mx - nx, my - ny, x0, y0);
  }

  function drawCylinder(c, p, pose, e, spin, speed, t) {
    var F = cylFrame(e), i;
    var open = e > 0.001;
    // body silhouette (convex hull of the chamfered end rings)
    var pts = [], rings = [[-HL, CR - CH], [-HL + CH * 0.35, CR - CH * 0.3], [-HL + CH, CR], [HL - CH, CR], [HL - CH * 0.35, CR - CH * 0.3], [HL, CR - CH]];
    for (var r = 0; r < rings.length; r++) for (i = 0; i < 24; i++) pts.push(P3(F, rings[r][0], rings[r][1], i * TAU / 24));
    var H = hull(pts);

    if (open) {
      var fa = clamp01(e * 4);
      // soft shadow on the gun behind (the only thing that fades: it is a shadow, not a part)
      metalPaths();
      c.save(); if (METAL) c.clip(METAL);
      c.globalAlpha = 0.35 * fa; c.translate(5, 7); hullPath(c, H); c.fillStyle = '#000'; c.fill(); c.restore();
      // yoke + ejector rod (behind the cylinder: its front faces away from us). Always opaque; while the
      // drum seats they are hidden by occlusion: the rod slides up into the underlug channel and the yoke
      // folds into its recess in the frame (see OCC), so nothing ever shows through as a double exposure.
      var FR = P3(F, HL, 0, 0), RE = P3(F, HL + ROD, 0, 0), RK = P3(F, HL + ROD - 9, 0, 0);
      c.save(); c.lineCap = 'round';
      if (OCC) { c.clip(OCC[0], 'evenodd'); c.clip(OCC[1], 'evenodd'); }
      c.strokeStyle = css(p.edge); c.lineWidth = 19;
      c.beginPath(); c.moveTo(FR[0], FR[1]); c.lineTo(KP.x, KP.y); c.stroke();
      var cg = c.createLinearGradient(KP.x - 6, KP.y - 6, KP.x + 4, KP.y + 8);
      stops(cg, [0, css(p.sky), 0.35, css(p.s1), 1, css(p.deep)]);
      c.strokeStyle = cg; c.lineWidth = 14;
      c.beginPath(); c.moveTo(FR[0], FR[1]); c.lineTo(KP.x, KP.y); c.stroke();
      // rod
      c.lineCap = 'butt';
      c.strokeStyle = css(p.edge); c.lineWidth = 9.2;
      c.beginPath(); c.moveTo(FR[0], FR[1]); c.lineTo(RE[0], RE[1]); c.stroke();
      c.strokeStyle = css(p.s1); c.lineWidth = 5.2;
      c.beginPath(); c.moveTo(FR[0], FR[1]); c.lineTo(RE[0], RE[1]); c.stroke();
      c.strokeStyle = css(p.spec, 0.75); c.lineWidth = 1.3;
      c.beginPath(); c.moveTo(FR[0], FR[1] - 1.3); c.lineTo(RE[0], RE[1] - 1.3); c.stroke();
      // knurled head
      c.lineCap = 'round';
      c.strokeStyle = css(p.edge); c.lineWidth = 12.6;
      c.beginPath(); c.moveTo(RK[0], RK[1]); c.lineTo(RE[0], RE[1]); c.stroke();
      var kg = c.createLinearGradient(0, RE[1] - 5, 0, RE[1] + 5);
      stops(kg, [0, css(p.spec), 0.3, css(p.lite), 0.7, css(p.s1), 1, css(p.deep)]);
      c.strokeStyle = kg; c.lineWidth = 8.6;
      c.beginPath(); c.moveTo(RK[0], RK[1]); c.lineTo(RE[0], RE[1]); c.stroke();
      c.strokeStyle = css(p.ink, 0.55); c.lineWidth = 0.7;
      for (i = 1; i < 6; i++) { var kk = P3(F, HL + ROD - 9 + i * 1.5, 0, 0); c.beginPath(); c.moveTo(kk[0], kk[1] - 4); c.lineTo(kk[0], kk[1] + 4); c.stroke(); }
      c.restore();
      // the drum's own silhouette contour: grows out of the window edge instead of fading in
      hullPath(c, H); c.lineWidth = EDGE * 2 * fa; c.strokeStyle = css(p.edge); c.lineJoin = 'round'; c.stroke();
    } else {
      // closed: the drum stands proud of the frame and shades the window sill + strap edge
      c.save(); c.translate(1.2, 2.4); hullPath(c, H); c.fillStyle = css(p.ink, 0.4); c.fill(); c.restore();
    }

    // body: gradient across the silhouette, perpendicular to the axis
    var ax = F.d[0], ay = F.d[1], al = Math.sqrt(ax * ax + ay * ay) || 1, nx = -ay / al, ny = ax / al;
    var mn = 1e9, mxv = -1e9;
    for (i = 0; i < H.length; i++) { var dd = (H[i][0] - F.cx) * nx + (H[i][1] - F.cy) * ny; if (dd < mn) mn = dd; if (dd > mxv) mxv = dd; }
    var gr;
    if (!open) gr = p.cache.cyl || (p.cache.cyl = stops(c.createLinearGradient(0, -CR, 0, CR), cylStops(p)));
    else gr = stops(c.createLinearGradient(F.cx + nx * mn, F.cy + ny * mn, F.cx + nx * mxv, F.cy + ny * mxv), cylStops(p));
    hullPath(c, H);
    c.fillStyle = gr; c.fill();
    c.save(); c.clip();

    // flutes (+ motion blur: several exposures spread over one frame's worth of rotation)
    var aspd = Math.abs(speed), blur = smooth(2.5, 14, aspd);
    if (blur > 0.02) {
      var n = 3 + Math.round(blur * 5), spread = Math.min(STEP, aspd / 60 * 1.15) * (speed < 0 ? -1 : 1);
      var ga = (1 - Math.pow(1 - 0.85, 1 / n)) / 0.85;
      // below full blur, keep one crisp exposure so the flutes stay readable while they smear
      if (blur < 1) drawFlutes(c, p, F, spin, 1 - blur, true);
      for (i = 0; i < n; i++) drawFlutes(c, p, F, spin - spread * i / (n - 1), ga * blur, false);
      if (!open) {
        // the smeared grooves: soft horizontal banding + streaks where the flute ends sweep through the light
        c.globalAlpha = blur;
        var sg = p.cache.blur || (p.cache.blur = stops(c.createLinearGradient(0, -CR, 0, CR),
          [0, 'rgba(0,0,0,0)', 0.22, css(p.spec, 0.22), 0.3, css(p.spec, 0.32), 0.38, 'rgba(0,0,0,0)', 0.55, css(p.ink, 0.22), 0.7, 'rgba(0,0,0,0)',
            0.84, css(p.gnd, 0.35), 1, 'rgba(0,0,0,0)']));
        c.fillStyle = sg; c.fillRect(FL0 - 6, -CR, FL1 - FL0 + 12, CR * 2);
        var vg = p.cache.vstreak || (p.cache.vstreak = stops(c.createLinearGradient(0, -CR, 0, CR),
          [0, 'rgba(0,0,0,0)', 0.3, css(p.spec, 0.75), 0.5, css(p.sky, 0.3), 0.9, css(p.gnd, 0.35), 1, 'rgba(0,0,0,0)']));
        c.fillStyle = vg;
        c.fillRect(FL0 - 7, -CR, 1.4, CR * 2); c.fillRect(FL1 + 5.5, -CR, 1.4, CR * 2);
        c.fillStyle = css(p.ink, 0.35);
        c.fillRect(FL0 - 4.5, -CR, 2.2, CR * 2); c.fillRect(FL1 + 2.3, -CR, 2.2, CR * 2);
        c.globalAlpha = 1;
      }
    } else {
      drawFlutes(c, p, F, spin, 1, true);
    }
    // speed lines: tapered streaks running over the drum in the direction of travel (the surface moves
    // up or down the screen). Their phase is spin * k (k < 1/(2 pi): they drift with the flutes, a little
    // slower) plus a slow drift in the sign of spinSpeed, and they re-seed with t, so a still frame reads as
    // "spinning" and a live one flickers. A soft dark halo under each bright streak keeps them legible on
    // the bright band as well as the dark one.
    var ls = open ? 0 : smooth(2.5, 9, aspd);
    if (ls > 0.02) {
      var tt = typeof t === 'number' ? t : 0, dir = speed < 0 ? -1 : 1, fk = Math.floor(tt / 45), NS = 8;
      var span = 2 * (HL - CH) - 4, SL = [];
      for (var j = 0; j < NS; j++) {
        if (hash(fk * 13 + j) < 0.12) continue;
        var sx = -HL + CH + 2 + span * (j + 0.15 + 0.7 * hash(j * 7 + 3 + (fk % 3) * 17)) / NS;
        var len = CR * (0.9 + 0.7 * hash(j * 11 + 1)) * (0.55 + 0.45 * ls);
        var ph = frac(spin * (0.16 + 0.05 * (j % 3)) + hash(j * 5 + 2) + dir * tt * 0.0007);
        SL.push([sx, -CR - len * 0.5 + ph * (CR * 2 + len), len, 1.5 + 1.1 * hash(j * 3 + 9)]);
      }
      c.beginPath();
      for (j = 0; j < SL.length; j++) lens(c, SL[j][0], SL[j][1] - SL[j][2] * 0.55, SL[j][0], SL[j][1] + SL[j][2] * 0.55, SL[j][3] * 2.6);
      // the drum curves away top and bottom: streaks fade out toward its silhouette
      var sg1 = p.cache.sl1 || (p.cache.sl1 = stops(c.createLinearGradient(0, -CR, 0, CR),
        [0, css(p.ink, 0), 0.1, css(p.ink, 0), 0.34, css(p.ink, 0.3), 0.66, css(p.ink, 0.3), 0.9, css(p.ink, 0), 1, css(p.ink, 0)]));
      var slc = p.neon ? mix(p.spec, p.rimTop, 0.35) : p.spec;
      var sg2 = p.cache.sl2 || (p.cache.sl2 = stops(c.createLinearGradient(0, -CR, 0, CR),
        [0, css(slc, 0), 0.08, css(slc, 0), 0.32, css(slc, 1), 0.68, css(slc, 1), 0.92, css(slc, 0), 1, css(slc, 0)]));
      c.globalAlpha = ls;
      c.fillStyle = sg1; c.fill();
      c.beginPath();
      for (j = 0; j < SL.length; j++) lens(c, SL[j][0], SL[j][1] - SL[j][2] / 2, SL[j][0], SL[j][1] + SL[j][2] / 2, SL[j][3]);
      c.globalAlpha = 0.75 * ls; c.fillStyle = sg2; c.fill();
      c.globalAlpha = 1;
    }
    // chamfered ends: rounded lathe shoulders facing away from the light
    ringBand(c, F, -HL, CR - CH, -HL + CH, CR); c.fillStyle = css(p.ink, 0.38); c.fill();
    ringBand(c, F, HL - CH, CR, HL, CR - CH); c.fillStyle = css(p.ink, 0.3); c.fill();
    ringLine(c, F, -HL + CH + 0.5, CR); c.lineWidth = 1; c.strokeStyle = css(p.spec, 0.4); c.stroke();
    ringLine(c, F, HL - CH - 0.5, CR); c.lineWidth = 1; c.strokeStyle = css(p.spec, 0.3); c.stroke();
    ringLine(c, F, -HL + 0.9, CR - CH + 0.4); c.lineWidth = 0.8; c.strokeStyle = css(p.sky, 0.35); c.stroke();
    ringLine(c, F, HL - 0.9, CR - CH + 0.4); c.lineWidth = 0.8; c.strokeStyle = css(p.sky, 0.3); c.stroke();
    // the turn line worn by the cylinder stop
    ringLine(c, F, -24.6, CR); c.lineWidth = 0.7; c.strokeStyle = css(p.spec, 0.22); c.stroke();
    c.restore();
    hullPath(c, H); c.lineWidth = 1.1; c.strokeStyle = css(p.ink, open ? 1 : 0.75); c.stroke();
    if (!open) {
      c.save(); c.globalCompositeOperation = 'lighter';
      c.fillStyle = css(p.rimTop, p.neon ? 0.55 : 0.28); c.fillRect(-HL + CH, -CR + 0.5, 2 * (HL - CH), 1);
      if (p.neon) { c.fillStyle = css(p.rimBot, 0.45); c.fillRect(-HL + CH, CR - 1.5, 2 * (HL - CH), 1); }
      c.restore();
    }

    // rear face with chambers (only when it turns toward us)
    var fz = -F.d[2];
    if (open && fz > 0.015) drawFace(c, p, pose, F, spin, fz, t);
  }

  function drawFace(c, p, pose, F, spin, fz, t) {
    var i, a;
    var FC = P3(F, -HL, 0, 0);
    c.save();
    c.transform(F.e1[0] * F.k, F.e1[1] * F.k, F.e2[0] * F.k, F.e2[1] * F.k, FC[0], FC[1]);
    // face disk + chamfer ring
    var fg = p.cache.face || (p.cache.face = stops(c.createLinearGradient(CR * 0.8, -CR * 0.8, -CR * 0.8, CR * 0.8),
      [0, css(p.spec), 0.2, css(p.clite), 0.55, css(p.cs1), 1, css(p.deep)]));
    c.beginPath(); c.arc(0, 0, CR, 0, TAU); c.fillStyle = css(p.lite); c.fill();
    c.beginPath(); c.arc(0, 0, CR - CH, 0, TAU); c.fillStyle = fg; c.fill();
    c.lineWidth = 0.8; c.strokeStyle = css(p.ink, 0.6); c.stroke();
    c.beginPath(); c.arc(0, 0, CR, 0, TAU); c.lineWidth = 1.2; c.strokeStyle = css(p.ink); c.stroke();
    // extractor star
    c.beginPath();
    for (i = 0; i < 12; i++) {
      a = spin + i * STEP / 2 + STEP / 2;
      var rr = i % 2 ? 9.8 : 15;
      c.lineTo(Math.cos(a) * rr, Math.sin(a) * rr);
    }
    c.closePath(); c.fillStyle = css(p.s1); c.fill();
    c.lineWidth = 0.8; c.strokeStyle = css(p.ink, 0.85); c.stroke();
    c.beginPath(); c.arc(0, 0, 4.4, 0, TAU); c.fillStyle = css(p.deep); c.fill();
    c.beginPath(); c.arc(0.6, -0.6, 2.3, 0, TAU); c.fillStyle = css(p.lite); c.fill();

    // which chambers hold a round: chambers 0..bullets-1 (the newest, bullets-1, may still be sliding in);
    // optionally pose.loaded = [chamber indices] + pose.newChamber for arbitrary layouts
    var list = Object.prototype.toString.call(pose.loaded) === '[object Array]' ? pose.loaded : null;
    var bullets = Math.max(0, Math.min(6, Math.round(num(pose.bullets, 0))));
    var loadIn = clamp01(num(pose.loadIn, 1));
    var newest = list ? Math.round(num(pose.newChamber, -1)) : bullets - 1;
    // chambers (index i sits at angle spin + i*60deg; 0 = top, i.e. under the hammer when closed)
    for (i = 0; i < 6; i++) {
      a = spin + i * STEP;
      var x = Math.cos(a) * RC, y = Math.sin(a) * RC;
      c.beginPath(); c.arc(x, y, HOLE + 1, 0, TAU); c.fillStyle = css(p.deep); c.fill();
      c.beginPath(); c.arc(x, y, HOLE, 0, TAU); c.fillStyle = '#050506'; c.fill();
      // far inner wall catches a little light
      c.beginPath(); c.arc(x, y, HOLE - 1, 0.5, 2.7); c.lineWidth = 1.4; c.strokeStyle = css(p.mid, 0.9); c.stroke();
      var has = list ? list.indexOf(i) >= 0 : i < bullets;
      if (has && !(i === newest && loadIn < 1)) cartHead(c, p, x, y);
    }
    // the cartridge sliding in. It is only brought up once the face has turned toward us: nearly edge-on,
    // the loading axis points along the barrel and a waiting round would lie flat across the grip.
    if (newest >= 0 && newest < 6 && loadIn < 1 && fz > 0.25) {
      a = spin + newest * STEP;
      slidingCart(c, p, F, Math.cos(a) * RC, Math.sin(a) * RC, loadIn, t);
    }
    c.restore();
  }

  function brassGrad(c, p) {
    return p.cache.brass || (p.cache.brass = stops(c.createRadialGradient(2.6, -2.6, 0.3, 0, 0, RIM),
      [0, css(BRASS_HI), 0.45, css(BRASS), 0.85, css(mix(BRASS, BRASS_D, 0.6)), 1, css(BRASS_D)]));
  }
  function cartHead(c, p, x, y) {
    c.save(); c.translate(x, y);
    c.beginPath(); c.arc(0, 0, RIM, 0, TAU); c.fillStyle = brassGrad(c, p); c.fill();
    c.lineWidth = 0.7; c.strokeStyle = css(BRASS_D, 0.9); c.stroke();
    c.beginPath(); c.arc(0, 0, RIM - 2.2, 0, TAU); c.lineWidth = 0.5; c.strokeStyle = css(BRASS_D, 0.45); c.stroke();
    var pg = p.cache.primer || (p.cache.primer = stops(c.createRadialGradient(0.8, -0.8, 0.1, 0, 0, PRIM),
      [0, '#f4f1ea', 0.6, '#b9b3a6', 1, '#6e685c']));
    c.beginPath(); c.arc(0, 0, PRIM + 0.6, 0, TAU); c.fillStyle = css(BRASS_D); c.fill();
    c.beginPath(); c.arc(0, 0, PRIM, 0, TAU); c.fillStyle = pg; c.fill();
    c.restore();
  }
  // a stadium in face-local space: the projection of a round rod along the chamber axis
  function stadium(c, x0, y0, x1, y1, r) {
    var ang = Math.atan2(y1 - y0, x1 - x0);
    c.beginPath();
    c.arc(x1, y1, r, ang - Math.PI / 2, ang + Math.PI / 2);
    c.arc(x0, y0, r, ang + Math.PI / 2, ang + Math.PI * 1.5);
    c.closePath();
  }
  function slidingCart(c, p, F, x, y, k, t) {
    // direction "out of the face" (toward the viewer, -d) expressed in face-local coordinates
    var m00 = F.e1[0], m10 = F.e1[1], m01 = F.e2[0], m11 = F.e2[1];
    var det = m00 * m11 - m01 * m10;
    if (Math.abs(det) < 1e-3) return;
    var vx = -F.d[0], vy = -F.d[1];
    var qx = (m11 * vx - m01 * vy) / det, qy = (-m10 * vx + m00 * vy) / det;
    var s = (1 - k) * (CART + CGAP);        // rim position (distance out of the face)
    var w0 = Math.max(0, s - CART), wN = s - CART + NOSE, wR = s - 2.2;
    var ql = Math.sqrt(qx * qx + qy * qy) || 1, px = -qy / ql, py = qx / ql, ux = qx / ql, uy = qy / ql;
    // speed lines trailing the round while it slides home
    var mv = smooth(0, 0.06, k) * (1 - smooth(0.9, 0.99, k));
    if (mv > 0.01) {
      var tt = typeof t === 'number' ? t : 0, fl = Math.floor(tt / 60);
      c.beginPath();
      for (var j = 0; j < 4; j++) {
        var off = (j - 1.5) * RIM * 0.62, L = (20 + 16 * hash(j * 5 + fl * 3)) * (0.5 + 0.5 * mv);
        var b0 = s + 3 + 2 * hash(j + fl), bx = x + qx * b0 + px * off, by = y + qy * b0 + py * off;
        var ex = bx + ux * L, ey = by + uy * L, wd = 1.1 + 0.5 * (j % 2);
        c.moveTo(bx + px * wd, by + py * wd); c.lineTo(ex, ey); c.lineTo(bx - px * wd, by - py * wd); c.closePath();
      }
      c.fillStyle = css(mix(BRASS_HI, WHITE, 0.4), 0.55 * mv); c.fill();
    }
    // bullet nose (copper): stacked shrinking discs make a rounded ogive
    if (wN > 0) {
      for (var jj = 0; jj <= 6; jj++) {
        var wf = s - CART + NOSE * (jj / 6);
        if (wf < 0) continue;
        var rr = 2.2 + (HOLE - 0.6 - 2.2) * Math.sqrt(jj / 6);
        c.beginPath(); c.arc(x + qx * wf, y + qy * wf, rr, 0, TAU);
        c.fillStyle = css(mix(COPPER_D, COPPER, 0.4 + jj / 10)); c.fill();
      }
      c.beginPath(); c.arc(x + qx * wN - px * 2, y + qy * wN - py * 2, 2.2, 0, TAU);
      c.fillStyle = css(COPPER_HI, 0.6); c.fill();
    }
    // case body
    var wa = Math.max(w0, wN, 0);
    if (wR > wa) {
      var cg = c.createLinearGradient(x - px * HOLE, y - py * HOLE, x + px * HOLE, y + py * HOLE);
      stops(cg, [0, css(BRASS_D), 0.3, css(BRASS_HI), 0.55, css(BRASS), 1, css(BRASS_D)]);
      stadium(c, x + qx * wa, y + qy * wa, x + qx * wR, y + qy * wR, HOLE - 0.6);
      c.fillStyle = cg; c.fill();
      c.lineWidth = 0.6; c.strokeStyle = css(BRASS_D); c.stroke();
    }
    // rim + head
    if (s > 0) {
      stadium(c, x + qx * Math.max(0, wR), y + qy * Math.max(0, wR), x + qx * s, y + qy * s, RIM);
      c.fillStyle = css(mix(BRASS, BRASS_D, 0.4)); c.fill();
      cartHead(c, p, x + qx * s, y + qy * s);
    } else cartHead(c, p, x, y);
  }

  // ------------------------------------------------------------------ effects
  function drawFlash(c, p, f) {
    // f: 1 at the shot, 0 at 0.15 s
    var f2 = f * f;
    metalPaths();
    if (BARREL && f > 0) {
      // barrel heat: the muzzle end and the forcing cone glow briefly
      c.save(); c.clip(BARREL);
      c.globalCompositeOperation = 'lighter';
      var hg = c.createLinearGradient(46, 0, MX, 0);
      stops(hg, [0, css(p.fireOut, 0.35 * f2), 0.12, css(p.fireOut, 0), 0.72, css(p.fireOut, 0),
        0.92, css(p.fireOut, 0.22 * f2), 1, css(p.fireMid, 0.5 * f2)]);
      c.fillStyle = hg; c.fillRect(44, -60, MX - 42, 90);
      c.restore();
    }
    c.save(); c.globalCompositeOperation = 'lighter';
    // gas jetting out of the cylinder gap: a thin vertical fan
    c.save(); c.translate(HL + 2, BY); c.scale(0.32, 1);
    var gr = 40 * (0.55 + 0.45 * f);
    var gg = c.createRadialGradient(0, 0, 0, 0, 0, gr);
    stops(gg, [0, css(p.fireCore, f), 0.22, css(p.fireMid, 0.85 * f), 0.55, css(p.fireOut, 0.35 * f), 1, css(p.fireOut, 0)]);
    c.fillStyle = gg; c.beginPath(); c.arc(0, 0, gr, 0, TAU); c.fill();
    c.restore();
    // hot muzzle
    var mr = 18 + 12 * f;
    var mg = c.createRadialGradient(MX + 2, BY, 0, MX + 2, BY, mr);
    stops(mg, [0, css(p.fireCore, f), 0.3, css(p.fireMid, 0.75 * f), 1, css(p.fireOut, 0)]);
    c.fillStyle = mg; c.beginPath(); c.arc(MX + 2, BY, mr, 0, TAU); c.fill();
    c.restore();
  }

  function drawGlint(c, p, t) {
    var per = 7000, ph = ((t % per) + per) % per / per;
    if (ph > 0.16) return;
    metalPaths();
    if (!METAL) return;
    var x = -60 + ph / 0.16 * 360;
    c.save(); c.clip(METAL); c.clip(EXCL, 'evenodd');
    c.globalCompositeOperation = 'lighter';
    var gg = p.cache.glint || (p.cache.glint = stops(c.createLinearGradient(-26, 0, 26, 0),
      [0, 'rgba(255,255,255,0)', 0.3, css(p.spec, 0.05), 0.5, css(p.spec, 0.2), 0.7, css(p.spec, 0.05), 1, 'rgba(255,255,255,0)']));
    c.transform(1, 0, -0.5, 1, x, 0);
    c.fillStyle = gg; c.fillRect(-26, -70, 52, 150);
    c.restore();
  }

  // ------------------------------------------------------------------ entry point
  function scaleOf(c) {
    if (c.getTransform) { var m = c.getTransform(); return Math.sqrt(m.a * m.a + m.b * m.b) || 1; }
    return 2;
  }

  return function drawRevolver(c, pose, th, t) {
    pose = pose || {};
    var p = palette(th);
    var open = clamp01(num(pose.open, 0)), e = ease(open);
    var cock = clamp01(num(pose.cock, 0)), trig = clamp01(num(pose.trig, 0));
    var spin = num(pose.spin, 0), speed = num(pose.spinSpeed, 0), flash = num(pose.flash, -1);
    // single action: the hammer holds at full cock while the trigger takes up its slack, then the sear
    // breaks and it drops between 70% and 95% travel. The trigger never lifts the hammer by itself.
    var hamA = -TRAVEL * cock * (1 - smooth(0.7, 0.95, trig));
    var tp = cock * 0.5; tp += (1 - tp) * trig;        // the trigger rides half way back with the hammer
    var trigA = 0.42 * tp;

    var spr = sprite(p, scaleOf(c));
    c.save();
    c.lineJoin = 'round'; c.lineCap = 'round';
    drawTrigger(c, p, trigA);
    c.drawImage(spr.cv, SB.x0, SB.y0, spr.cv.width / spr.q, spr.cv.height / spr.q);
    drawHammer(c, p, hamA, spr.ham);
    if (typeof t === 'number') drawGlint(c, p, t);
    drawCylinder(c, p, pose, e, spin, speed, t);
    if (flash >= 0 && flash <= 0.15) drawFlash(c, p, 1 - flash / 0.15);
    c.restore();
  };
})();

    return { draw: drawRevolver, MUZZLE: REVOLVER_MUZZLE, BOUNDS: REVOLVER_BOUNDS };
  })();

/*
 * Hexcast Games - Russian Roulette: environment + FX pass                    scene.js
 *
 * Plain browser script (no modules, no build), Canvas 2D only, no external assets.
 * Everything is deterministic: noise comes from seeded PRNGs, animation from the clock
 * passed in. Anything static (textures, sprites, gradients) is cached per theme.
 *
 * Globals (all coordinates in base px of the 1100 x 560 scene; the caller has already
 * applied its own k-scale transform):
 *
 *   drawBackground(c, th, W, H, ground)  STATIC layer - call once per theme/resize into an
 *                                        offscreen canvas. Includes the rounded 22px frame.
 *   drawAmbient(c, th, t, W, H, opts)    animated layer drawn right after the background and
 *                                        before the actors. t = milliseconds (rAF clock).
 *                                        opts (optional): {ground: 522, shot: s since the bang}
 *                                        - with shot, the lamp is knocked swinging and grit falls.
 *   drawGunRest(c, th, opts)             gun space (origin = cylinder axis, barrel +x),
 *                                        drawn BEFORE the gun, WITHOUT the recoil rotation.
 *                                        opts (optional): {dx, dy} nudge the rest.
 *   drawMuzzleFlash(c, age, th, seed)    origin at the muzzle, +x = firing direction, age s.
 *   drawSmoke(c, age, th, seed)          origin at the muzzle (world), age s (0..3).
 *   shake(age) -> {x, y, r}              camera shake, base px / radians.
 *   drawShotLight(c, age, th, mx, my, W, H)  optional: the room lit by the shot (after the actors).
 *   SCENE_LAMP(W)  -> {x, y}             where the lamp's bulb hangs (for light-aware actors).
 */
var SCENE = (function (root) {
  'use strict';

  var TAU = Math.PI * 2;
  var doc = root.document;

  // ------------------------------------------------------------------ helpers
  function clamp(v, a, b) { return v < a ? a : v > b ? b : v; }
  function smooth(a, b, x) { var t = clamp((x - a) / (b - a), 0, 1); return t * t * (3 - 2 * t); }
  function mulberry(a) {
    return function () {
      a |= 0; a = a + 0x6D2B79F5 | 0;
      var t = Math.imul(a ^ a >>> 15, 1 | a);
      t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t;
      return ((t ^ t >>> 14) >>> 0) / 4294967296;
    };
  }
  function hash1(n) { var x = Math.sin(n * 127.1 + 311.7) * 43758.5453; return x - Math.floor(x); }

  function col(s) {
    s = String(s || '').replace(/\s+/g, '');
    if (s.charAt(0) === '#') {
      var h = s.slice(1);
      if (h.length === 3 || h.length === 4) h = h.replace(/(.)/g, '$1$1');
      var n = parseInt(h.slice(0, 6), 16);
      return [n >> 16 & 255, n >> 8 & 255, n & 255, h.length === 8 ? parseInt(h.slice(6, 8), 16) / 255 : 1];
    }
    var m = s.match(/rgba?\(([^)]+)\)/i);
    if (m) { var q = m[1].split(','); return [+q[0], +q[1], +q[2], q.length > 3 ? +q[3] : 1]; }
    return [128, 128, 128, 1];
  }
  function rgba(c, a) {
    return 'rgba(' + Math.round(clamp(c[0], 0, 255)) + ',' + Math.round(clamp(c[1], 0, 255)) + ',' +
      Math.round(clamp(c[2], 0, 255)) + ',' + (a == null ? (c[3] == null ? 1 : c[3]) : +(+a).toFixed(4)) + ')';
  }
  function mix(a, b, t) { return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t, 1]; }
  function mul(a, f) { return [a[0] * f, a[1] * f, a[2] * f, 1]; }
  function lum(c) { return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]; }
  function sat(c) { var mx = Math.max(c[0], c[1], c[2]), mn = Math.min(c[0], c[1], c[2]); return mx ? (mx - mn) / mx : 0; }
  function norm(c, target) { var l = Math.max(1, lum(c)); return mul(c, target / l); }

  function mkCanvas(w, h) {
    var cv = doc.createElement('canvas');
    cv.width = Math.max(1, Math.ceil(w)); cv.height = Math.max(1, Math.ceil(h));
    return cv;
  }
  function pxScale(c) {
    try { var m = c.getTransform(); return Math.sqrt(m.a * m.a + m.b * m.b) || 1; } catch (e) { return 1; }
  }
  function roundRect(c, x, y, w, h, r) {
    c.beginPath();
    c.moveTo(x + r, y); c.lineTo(x + w - r, y); c.quadraticCurveTo(x + w, y, x + w, y + r);
    c.lineTo(x + w, y + h - r); c.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
    c.lineTo(x + r, y + h); c.quadraticCurveTo(x, y + h, x, y + h - r);
    c.lineTo(x, y + r); c.quadraticCurveTo(x, y, x + r, y); c.closePath();
  }
  function softDot(size, stops) {
    var cv = mkCanvas(size, size), g = cv.getContext('2d'), r = size / 2;
    var gr = g.createRadialGradient(r, r, 0, r, r, r);
    for (var i = 0; i < stops.length; i++) gr.addColorStop(stops[i][0], stops[i][1]);
    g.fillStyle = gr; g.fillRect(0, 0, size, size);
    return cv;
  }

  // ------------------------------------------------------------------ palette
  // Everything is derived from the theme object; `mode` only picks which derivation
  // (monochrome accent -> noir-ish, a CSS glow -> neon, otherwise warm).
  var PAL = {};
  function pal(th) {
    var key = [th.bgTop, th.bgBot, th.lamp, th.accent, th.accent2, th.steel1, th.steel2, th.steelHi,
      th.grip1, th.grip2, th.floor, th.burlap, th.ink, th.glow].join('|');
    if (PAL[key]) return PAL[key];
    var WH = [255, 255, 255, 1], BK = [0, 0, 0, 1];
    var acc = col(th.accent), acc2 = col(th.accent2 || '#c0392b');
    var mode = (th.glow && th.glow !== 'none') ? 'neon' : (sat(acc) < 0.15 ? 'mono' : 'warm');
    var bgTop = col(th.bgTop), bgBot = col(th.bgBot), lamp = col(th.lamp);
    var steel1 = col(th.steel1), steel2 = col(th.steel2), steelHi = col(th.steelHi);
    var grip1 = col(th.grip1), grip2 = col(th.grip2), floor = col(th.floor), ink = col(th.ink);
    var burlap = col(th.burlap || '#c9a26a');
    lamp = [lamp[0], lamp[1], lamp[2], 1];
    var p = { key: key, mode: mode, acc: acc, acc2: acc2, ink: ink, bgTop: bgTop, bgBot: bgBot,
      steel1: steel1, steel2: steel2, steelHi: steelHi, grip1: grip1, grip2: grip2 };
    p.neon = mode === 'neon';
    // light
    p.light = mode === 'mono' ? [255, 253, 248, 1] : mix(WH, lamp, mode === 'neon' ? 0.52 : 0.55);
    p.amb = mode === 'neon' ? 0.36 : 0.3;
    // wall / floor albedo (colour under full light), normalised so every theme reads
    p.wall = norm(bgTop, mode === 'neon' ? 46 : mode === 'mono' ? 80 : 84);
    p.seam = mul(bgBot, 0.55);
    p.floor = norm(mix(floor, bgTop, 0.25), mode === 'neon' ? 44 : 60);
    // paper targets
    p.paper = mode === 'mono' ? [222, 220, 214, 1] : mode === 'neon' ? mix(ink, [230, 225, 255, 1], 0.5) : mix(ink, burlap, 0.3);
    p.print = mode === 'neon' ? mix(grip2, BK, 0.2) : mode === 'mono' ? [22, 22, 24, 1] : [46, 30, 20, 1];
    p.bull = mode === 'neon' ? acc : acc2;
    // sandbags
    p.bag = mode === 'mono' ? mul(mix(burlap, [120, 120, 120, 1], 0.7), 0.9) :
      mode === 'neon' ? mix(mul(burlap, 0.62), grip2, 0.3) : mul(mix(burlap, [120, 104, 80, 1], 0.45), 0.92);
    // lamp fixture
    p.shade = mode === 'warm' ? mix(steel2, grip2, 0.35) : steel2;
    p.shadeHi = mix(steel1, steelHi, 0.25);
    p.rim = mode === 'mono' ? steelHi : acc;
    // gun rest
    p.iron = mix(steel2, steel1, 0.42);
    p.ironHi = steelHi;
    p.brass = mode === 'mono' ? mix(steelHi, WH, 0.3) : acc;
    p.leather = mode === 'warm' ? mix(grip1, burlap, 0.36) : mode === 'mono' ? mix(grip1, steel1, 0.35) : mix(grip1, acc, 0.14);
    p.stitch = mode === 'warm' ? mix(ink, burlap, 0.35) : mode === 'mono' ? [150, 150, 150, 1] : acc2;
    // flash
    if (mode === 'mono') {
      p.fCore = [255, 255, 255, 1]; p.fHot = [246, 246, 246, 1]; p.fMid = [196, 196, 196, 1];
      p.fOut = [150, 150, 150, 1]; p.fSparkA = [255, 255, 255, 1]; p.fSparkB = [210, 210, 210, 1]; p.fRing = [255, 255, 255, 1];
    } else if (mode === 'neon') {
      p.fCore = [255, 255, 255, 1]; p.fHot = mix(acc, WH, 0.62); p.fMid = acc; p.fOut = mix(acc, acc2, 0.55);
      p.fSparkA = mix(acc2, WH, 0.35); p.fSparkB = mix(acc, WH, 0.4); p.fRing = acc2;
    } else {
      p.fCore = [255, 252, 236, 1]; p.fHot = mix(mix(acc, [255, 226, 120, 1], 0.6), WH, 0.35);
      p.fMid = mix(acc, [255, 120, 30, 1], 0.6); p.fOut = mix(acc2, [255, 80, 20, 1], 0.5);
      p.fSparkA = [255, 222, 140, 1]; p.fSparkB = [255, 160, 70, 1]; p.fRing = [255, 236, 206, 1];
    }
    // smoke
    if (mode === 'mono') { p.smA = [226, 226, 226, 1]; p.smB = [206, 206, 210, 1]; p.smS = [74, 74, 78, 1]; }
    else if (mode === 'neon') { p.smA = mix(ink, acc, 0.2); p.smB = mix(ink, acc2, 0.28); p.smS = mix(grip1, bgTop, 0.2); }
    else { p.smA = mix(ink, [205, 196, 182, 1], 0.5); p.smB = mix(ink, [190, 186, 180, 1], 0.6); p.smS = mix(bgTop, [96, 88, 80, 1], 0.55); }
    PAL[key] = p;
    return p;
  }

  // ------------------------------------------------------------------ layout
  function lampX(W) { return Math.round((W || 1100) * 0.618); }
  var CORD_TOP = 0, SHADE_TOP = 58, RIM_Y = 92, CONE_Y = 94;       // lamp-local (pivot at ceiling)
  function coneHalf(y, ground) { return 52 + (y - CONE_Y) * (200 / ((ground || 522) - CONE_Y)); }
  function coneSoft(y, ground) { return 7 + (y - CONE_Y) * (50 / ((ground || 522) - CONE_Y)); }

  // ------------------------------------------------------------------ background
  function nail(c, p, x, y) {
    c.fillStyle = 'rgba(0,0,0,.45)'; c.beginPath(); c.arc(x + 0.8, y + 1, 2.3, 0, TAU); c.fill();
    c.fillStyle = rgba(mix(p.steel1, p.wall, 0.3)); c.beginPath(); c.arc(x, y, 2, 0, TAU); c.fill();
    c.fillStyle = 'rgba(255,255,255,.35)'; c.beginPath(); c.arc(x - 0.6, y - 0.7, 0.8, 0, TAU); c.fill();
  }

  function grainLines(c, rnd, base, x0, y0, w, h, vertical, n) {
    for (var i = 0; i < n; i++) {
      var off = 3 + rnd() * ((vertical ? w : h) - 6), amp = 0.8 + rnd() * 2.6, fr = 0.008 + rnd() * 0.02, ph = rnd() * TAU;
      var light = rnd() < 0.45;
      c.strokeStyle = rgba(light ? mul(base, 1.22) : mul(base, 0.68), 0.18 + rnd() * 0.3);
      c.lineWidth = 0.5 + rnd() * 0.9;
      c.beginPath();
      var len = vertical ? h : w, st = 7;
      for (var s = 0; s <= len + st; s += st) {
        var d = Math.sin(s * fr + ph) * amp + Math.sin(s * fr * 3.1 + ph * 2) * amp * 0.3;
        var px = vertical ? x0 + off + d : x0 + s, py = vertical ? y0 + s : y0 + off + d;
        if (s === 0) c.moveTo(px, py); else c.lineTo(px, py);
      }
      c.stroke();
    }
  }

  function knot(c, rnd, base, x, y, sz) {
    for (var k = 3; k >= 0; k--) {
      c.strokeStyle = rgba(mul(base, 0.55 + k * 0.08), 0.35);
      c.lineWidth = 0.8;
      c.beginPath(); c.ellipse(x, y, sz * (0.5 + k * 0.45), sz * (1.1 + k * 0.9), 0, 0, TAU); c.stroke();
    }
    c.fillStyle = rgba(mul(base, 0.45), 0.8); c.beginPath(); c.ellipse(x, y, sz * 0.45, sz, 0, 0, TAU); c.fill();
  }

  function vPlanks(c, p, x0, x1, y0, y1, seed) {
    var rnd = mulberry(seed), x = x0;
    while (x < x1) {
      var w = 48 + Math.floor(rnd() * 24), tone = 0.84 + rnd() * 0.26, base = mul(p.wall, tone);
      var g = c.createLinearGradient(0, y0, 0, y1);
      g.addColorStop(0, rgba(mul(base, 0.9))); g.addColorStop(0.55, rgba(base)); g.addColorStop(1, rgba(mul(base, 0.86)));
      c.fillStyle = g; c.fillRect(x, y0, w, y1 - y0);
      var gh = c.createLinearGradient(x, 0, x + w, 0);
      gh.addColorStop(0, 'rgba(255,255,255,.06)'); gh.addColorStop(0.18, 'rgba(255,255,255,0)');
      gh.addColorStop(0.75, 'rgba(0,0,0,0)'); gh.addColorStop(1, 'rgba(0,0,0,.2)');
      c.fillStyle = gh; c.fillRect(x, y0, w, y1 - y0);
      c.save(); c.beginPath(); c.rect(x + 1, y0, w - 2, y1 - y0); c.clip();
      grainLines(c, rnd, base, x, y0, w, y1 - y0, true, 6 + (rnd() * 5 | 0));
      if (rnd() < 0.4) knot(c, rnd, base, x + w * (0.3 + rnd() * 0.4), y0 + 30 + rnd() * (y1 - y0 - 60), 2 + rnd() * 2.5);
      c.restore();
      c.fillStyle = rgba(p.seam, 0.9); c.fillRect(x, y0, 2, y1 - y0);
      c.fillStyle = 'rgba(255,255,255,.05)'; c.fillRect(x + 2, y0, 1, y1 - y0);
      nail(c, p, x + 9, y0 + 14); nail(c, p, x + w - 9, y0 + 14);
      nail(c, p, x + 9, y1 - 12); nail(c, p, x + w - 9, y1 - 12);
      x += w;
    }
  }

  function hBoards(c, p, x0, x1, y0, y1, seed, tone0) {
    var rnd = mulberry(seed), y = y0;
    while (y < y1 - 4) {
      var h = Math.min(y1 - y, 22 + Math.floor(rnd() * 8)), x = x0 - rnd() * 120;
      while (x < x1) {
        var w = 150 + rnd() * 190, tone = tone0 * (0.86 + rnd() * 0.22), base = mul(p.wall, tone);
        var g = c.createLinearGradient(0, y, 0, y + h);
        g.addColorStop(0, rgba(mul(base, 1.1))); g.addColorStop(0.25, rgba(base)); g.addColorStop(1, rgba(mul(base, 0.78)));
        c.fillStyle = g; c.fillRect(x, y, w, h);
        c.save(); c.beginPath(); c.rect(x, y + 1, w, h - 2); c.clip();
        grainLines(c, rnd, base, x, y, w, h, false, 3 + (rnd() * 3 | 0));
        c.restore();
        c.fillStyle = rgba(p.seam, 0.85); c.fillRect(x, y, 2, h);
        nail(c, p, x + 7, y + h / 2);
        x += w;
      }
      c.fillStyle = rgba(p.seam, 0.9); c.fillRect(x0, y + h - 1.5, x1 - x0, 1.5);
      c.fillStyle = 'rgba(255,255,255,.06)'; c.fillRect(x0, y, x1 - x0, 1);
      y += h;
    }
  }

  function paperTarget(c, p, cx, cy, w, h, rot, kind, seed) {
    var rnd = mulberry(seed);
    c.save(); c.translate(cx, cy); c.rotate(rot);
    // soft drop shadow (the lamp is above, so it falls down)
    for (var s = 3; s >= 1; s--) {
      c.fillStyle = 'rgba(0,0,0,' + (0.1 + 0.05 * (3 - s)) + ')';
      c.fillRect(-w / 2 + 2 - s, -h / 2 + 5 - s, w + 2 * s, h + 2 * s);
    }
    // sheet with a curled bottom-right corner
    var cu = 13;
    c.beginPath(); c.moveTo(-w / 2, -h / 2); c.lineTo(w / 2, -h / 2); c.lineTo(w / 2, h / 2 - cu);
    c.lineTo(w / 2 - cu, h / 2); c.lineTo(-w / 2, h / 2); c.closePath();
    var pg = c.createLinearGradient(0, -h / 2, 0, h / 2);
    pg.addColorStop(0, rgba(mul(p.paper, 1.02))); pg.addColorStop(1, rgba(mul(p.paper, 0.86)));
    c.fillStyle = pg; c.fill();
    c.save(); c.clip();
    c.strokeStyle = rgba(p.print, 0.9); c.fillStyle = rgba(p.print, 0.92);
    var i, r0;
    if (kind === 'bull') {
      r0 = w * 0.42;
      var bx = 0, by = -h * 0.06;
      c.beginPath(); c.arc(bx, by, r0 * 0.62, 0, TAU); c.fill();             // black scoring zone
      c.lineWidth = 1;
      for (i = 1; i <= 6; i++) {
        var rr = r0 * i / 6;
        c.strokeStyle = i <= 3.7 ? rgba(p.paper, 0.85) : rgba(p.print, 0.85);
        c.beginPath(); c.arc(bx, by, rr, 0, TAU); c.stroke();
      }
      c.fillStyle = rgba(p.bull, 0.95); c.beginPath(); c.arc(bx, by, r0 / 6, 0, TAU); c.fill();
      // header strip + tiny print
      c.fillStyle = rgba(p.print, 0.85); c.fillRect(-w / 2 + 6, h / 2 - 16, w * 0.5, 3);
      c.fillRect(-w / 2 + 6, h / 2 - 10, w * 0.32, 2);
    } else {
      // B-27 style silhouette with scoring rings
      c.beginPath();
      c.moveTo(-w * 0.42, h / 2); c.quadraticCurveTo(-w * 0.44, h * 0.02, -w * 0.2, -h * 0.02);
      c.quadraticCurveTo(-w * 0.13, -h * 0.08, -w * 0.12, -h * 0.14);
      c.bezierCurveTo(-w * 0.24, -h * 0.22, -w * 0.2, -h * 0.44, 0, -h * 0.44);
      c.bezierCurveTo(w * 0.2, -h * 0.44, w * 0.24, -h * 0.22, w * 0.12, -h * 0.14);
      c.quadraticCurveTo(w * 0.13, -h * 0.08, w * 0.2, -h * 0.02);
      c.quadraticCurveTo(w * 0.44, h * 0.02, w * 0.42, h / 2); c.closePath();
      c.fill();
      c.strokeStyle = rgba(p.paper, 0.75); c.lineWidth = 1;
      for (i = 1; i <= 3; i++) { c.beginPath(); c.ellipse(0, h * 0.18, w * 0.09 * i, h * 0.09 * i, 0, 0, TAU); c.stroke(); }
      c.beginPath(); c.ellipse(0, -h * 0.28, w * 0.09, h * 0.08, 0, 0, TAU); c.stroke();
      c.fillStyle = rgba(p.bull, 0.9); c.beginPath(); c.arc(0, h * 0.18, w * 0.05, 0, TAU); c.fill();
    }
    // bullet holes (torn light rim + dark hole)
    var nh = 5 + (rnd() * 4 | 0);
    for (i = 0; i < nh; i++) {
      var hx = (rnd() - 0.5) * w * 0.6, hy = (kind === 'bull' ? -h * 0.06 : h * 0.1) + (rnd() - 0.5) * h * 0.45, hr = 1.8 + rnd() * 1.2;
      c.fillStyle = rgba(mul(p.paper, 1.12), 0.9); c.beginPath(); c.arc(hx, hy, hr + 1.3, 0, TAU); c.fill();
      c.fillStyle = 'rgba(8,6,5,.95)'; c.beginPath(); c.arc(hx, hy, hr, 0, TAU); c.fill();
    }
    c.restore();
    // the curl
    c.beginPath(); c.moveTo(w / 2, h / 2 - cu); c.lineTo(w / 2 - cu, h / 2); c.lineTo(w / 2 - cu * 0.95, h / 2 - cu * 0.85); c.closePath();
    c.fillStyle = rgba(mul(p.paper, 0.72)); c.fill();
    c.strokeStyle = 'rgba(0,0,0,.25)'; c.lineWidth = 0.8; c.stroke();
    // tacks
    [[-w / 2 + 6, -h / 2 + 6], [w / 2 - 6, -h / 2 + 6]].forEach(function (t) {
      c.fillStyle = 'rgba(0,0,0,.45)'; c.beginPath(); c.arc(t[0] + 1, t[1] + 1.8, 3.4, 0, TAU); c.fill();
      c.fillStyle = rgba(p.rim); c.beginPath(); c.arc(t[0], t[1], 3.2, 0, TAU); c.fill();
      c.fillStyle = 'rgba(255,255,255,.6)'; c.beginPath(); c.arc(t[0] - 1, t[1] - 1, 1.1, 0, TAU); c.fill();
    });
    c.restore();
  }

  function sandbag(c, p, cx, cy, w, h, seed, flip) {
    var rnd = mulberry(seed);
    c.save(); c.translate(cx, cy); if (flip) c.scale(-1, 1);
    // contact shadow
    c.fillStyle = 'rgba(0,0,0,.35)'; c.beginPath(); c.ellipse(2, h * 0.46, w * 0.5, h * 0.14, 0, 0, TAU); c.fill();
    c.beginPath();
    c.moveTo(-w * 0.44, -h * 0.34);
    c.quadraticCurveTo(0, -h * 0.62, w * 0.4, -h * 0.36);                   // top
    c.quadraticCurveTo(w * 0.5, -h * 0.3, w * 0.56, -h * 0.12);            // tied ear
    c.lineTo(w * 0.47, 0); c.lineTo(w * 0.55, h * 0.14);
    c.quadraticCurveTo(w * 0.5, h * 0.36, w * 0.4, h * 0.42);
    c.quadraticCurveTo(0, h * 0.56, -w * 0.44, h * 0.4);                   // bottom
    c.quadraticCurveTo(-w * 0.56, 0, -w * 0.44, -h * 0.34);
    c.closePath();
    var g = c.createLinearGradient(0, -h / 2, 0, h / 2);
    g.addColorStop(0, rgba(mul(p.bag, 1.25))); g.addColorStop(0.45, rgba(p.bag)); g.addColorStop(1, rgba(mul(p.bag, 0.55)));
    c.fillStyle = g; c.fill();
    c.save(); c.clip();
    // weave
    c.strokeStyle = rgba(mul(p.bag, 0.7), 0.35); c.lineWidth = 0.7;
    for (var i = -w; i < w; i += 3.2) { c.beginPath(); c.moveTo(i, -h); c.lineTo(i + 3, h); c.stroke(); }
    // creases
    c.strokeStyle = rgba(mul(p.bag, 0.5), 0.55); c.lineWidth = 1.2;
    for (var k = 0; k < 3; k++) {
      var sx = (rnd() - 0.5) * w * 0.6;
      c.beginPath(); c.moveTo(sx, -h * 0.3); c.quadraticCurveTo(sx + (rnd() - 0.5) * 16, 0, sx + (rnd() - 0.5) * 10, h * 0.32); c.stroke();
    }
    c.restore();
    // rim light along the top from the lamp
    c.strokeStyle = rgba(p.light, 0.22); c.lineWidth = 1.6;
    c.beginPath(); c.moveTo(-w * 0.4, -h * 0.37); c.quadraticCurveTo(0, -h * 0.6, w * 0.38, -h * 0.38); c.stroke();
    // tie
    c.strokeStyle = rgba(mul(p.bag, 0.45)); c.lineWidth = 2;
    c.beginPath(); c.moveTo(w * 0.47, -h * 0.14); c.lineTo(w * 0.49, h * 0.12); c.stroke();
    c.restore();
  }

  function floorBoards(c, p, W, H, LX, wallB) {
    var vpY = wallB - 300, depth = H - wallB, rnd = mulberry(91);
    var g = c.createLinearGradient(0, wallB, 0, H);
    g.addColorStop(0, rgba(mul(p.floor, 0.7))); g.addColorStop(1, rgba(mul(p.floor, 1.05)));
    c.fillStyle = g; c.fillRect(0, wallB, W, depth);
    var pitch = 44, spread = depth / (wallB - vpY);
    for (var xb = -400; xb < W + 400; xb += pitch) {
      var tone = 0.82 + rnd() * 0.3;
      var xa = xb, xc = xb + pitch;
      var xa2 = xa + (xa - LX) * spread, xc2 = xc + (xc - LX) * spread;
      c.beginPath(); c.moveTo(xa, wallB); c.lineTo(xc, wallB); c.lineTo(xc2, H); c.lineTo(xa2, H); c.closePath();
      c.fillStyle = rgba(mul(p.floor, tone), 0.55); c.fill();
      // seam
      c.strokeStyle = rgba(p.seam, 0.95); c.lineWidth = 1.4;
      c.beginPath(); c.moveTo(xa, wallB); c.lineTo(xa2, H); c.stroke();
      c.strokeStyle = 'rgba(255,255,255,.05)'; c.lineWidth = 1;
      c.beginPath(); c.moveTo(xa + 1.5, wallB); c.lineTo(xa2 + 1.8, H); c.stroke();
      // a butt joint somewhere along the board
      var u = 0.25 + rnd() * 0.6, yj = wallB + depth * u;
      var ja = xa + (xa - LX) * spread * u, jc = xc + (xc - LX) * spread * u;
      c.strokeStyle = rgba(p.seam, 0.8); c.lineWidth = 1;
      c.beginPath(); c.moveTo(ja, yj); c.lineTo(jc, yj); c.stroke();
    }
    // occlusion where the floor meets the wall
    var ao = c.createLinearGradient(0, wallB, 0, wallB + 14);
    ao.addColorStop(0, 'rgba(0,0,0,.55)'); ao.addColorStop(1, 'rgba(0,0,0,0)');
    c.fillStyle = ao; c.fillRect(0, wallB, W, 14);
  }

  function lightmap(p, W, H, LX, wallB, ground) {
    var s = 0.5, cv = mkCanvas(W * s, H * s), g = cv.getContext('2d');
    g.scale(s, s);
    g.fillStyle = rgba(mul(p.light, p.amb)); g.fillRect(0, 0, W, H);
    g.globalCompositeOperation = 'lighter';
    // broad wash on the back wall under the lamp
    g.save(); g.translate(LX, 250); g.scale(1.1, 1);
    var r = g.createRadialGradient(0, 0, 0, 0, 0, 470);
    r.addColorStop(0, rgba(mul(p.light, 0.72))); r.addColorStop(0.3, rgba(mul(p.light, 0.46)));
    r.addColorStop(0.62, rgba(mul(p.light, 0.14))); r.addColorStop(1, 'rgba(0,0,0,0)');
    g.fillStyle = r; g.fillRect(-600, -500, 1200, 1000); g.restore();
    // the cone's footprint on the wall (a brighter trapezoid, soft-edged)
    for (var y = CONE_Y; y < ground; y += 4) {
      var hw = coneHalf(y, ground) * 1.02, sf = coneSoft(y, ground) * 1.6, a = 0.3 * (1 - (y - CONE_Y) / (ground - CONE_Y) * 0.55);
      var lg = g.createLinearGradient(LX - hw - sf, 0, LX + hw + sf, 0), e = sf / (hw + sf) / 2;
      lg.addColorStop(0, 'rgba(0,0,0,0)'); lg.addColorStop(e * 2, rgba(mul(p.light, a)));
      lg.addColorStop(0.5, rgba(mul(p.light, a * 1.15))); lg.addColorStop(1 - e * 2, rgba(mul(p.light, a)));
      lg.addColorStop(1, 'rgba(0,0,0,0)');
      g.fillStyle = lg; g.fillRect(LX - hw - sf, y, 2 * (hw + sf), 4);
    }
    // hot spot around the shade
    var hs = g.createRadialGradient(LX, RIM_Y + 4, 0, LX, RIM_Y + 4, 150);
    hs.addColorStop(0, rgba(mul(p.light, 0.5))); hs.addColorStop(1, 'rgba(0,0,0,0)');
    g.fillStyle = hs; g.fillRect(LX - 160, 0, 320, 260);
    // pool on the floor
    g.save(); g.translate(LX, wallB + 30); g.scale(1, 0.13);
    var fp = g.createRadialGradient(0, 0, 0, 0, 0, 330);
    fp.addColorStop(0, rgba(mul(p.light, 0.8))); fp.addColorStop(0.6, rgba(mul(p.light, 0.3))); fp.addColorStop(1, 'rgba(0,0,0,0)');
    g.fillStyle = fp; g.fillRect(-340, -340, 680, 680); g.restore();
    if (p.mode === 'mono') {
      // venetian-blind light from a window off to the left
      g.save(); g.translate(250, 70); g.transform(1, 0.34, 0, 1, 0, 0);
      for (var b = 0; b < 9; b++) {
        var by = b * 26, fa = 0.2 * (1 - b / 11);
        var bg = g.createLinearGradient(0, 0, 300, 0);
        bg.addColorStop(0, 'rgba(0,0,0,0)'); bg.addColorStop(0.15, rgba(mul(p.light, fa)));
        bg.addColorStop(0.7, rgba(mul(p.light, fa * 0.8))); bg.addColorStop(1, 'rgba(0,0,0,0)');
        g.fillStyle = bg; g.fillRect(0, by, 300, 13);
      }
      g.restore();
    }
    g.globalCompositeOperation = 'source-over';
    // the shade throws the ceiling into shadow
    var top = g.createLinearGradient(0, 0, 0, 120);
    top.addColorStop(0, 'rgba(0,0,0,.4)'); top.addColorStop(1, 'rgba(0,0,0,0)');
    g.fillStyle = top; g.fillRect(0, 0, W, 120);
    // keep the UI column calm
    var left = g.createLinearGradient(0, 0, 360, 0);
    left.addColorStop(0, 'rgba(0,0,0,.42)'); left.addColorStop(1, 'rgba(0,0,0,0)');
    g.fillStyle = left; g.fillRect(0, 0, 360, H);
    return cv;
  }

  var GRAIN = null;
  function grainPattern(c) {
    if (!GRAIN) {
      var n = 128, cv = mkCanvas(n, n), g = cv.getContext('2d'), id = g.createImageData(n, n), d = id.data, rnd = mulberry(1234);
      for (var i = 0; i < n * n; i++) {
        var v = rnd() < 0.5 ? 0 : 255;
        d[i * 4] = d[i * 4 + 1] = d[i * 4 + 2] = v; d[i * 4 + 3] = Math.floor(rnd() * 13);
      }
      g.putImageData(id, 0, 0);
      GRAIN = cv;
    }
    return c.createPattern(GRAIN, 'repeat');
  }

  function neonTube(c, x0, y0, x1, y1, color, width) {
    c.save(); c.globalCompositeOperation = 'lighter'; c.lineCap = 'round';
    var layers = [[width * 9, 0.03], [width * 5, 0.065], [width * 2.6, 0.14], [width * 1.2, 0.42]];
    for (var i = 0; i < layers.length; i++) {
      c.strokeStyle = rgba(color, layers[i][1]); c.lineWidth = layers[i][0];
      c.beginPath(); c.moveTo(x0, y0); c.lineTo(x1, y1); c.stroke();
    }
    c.strokeStyle = rgba(mix(color, [255, 255, 255, 1], 0.6), 0.75); c.lineWidth = width * 0.5;
    c.beginPath(); c.moveTo(x0, y0); c.lineTo(x1, y1); c.stroke();
    c.restore();
    // electrodes + clips so it reads as a real tube
    c.fillStyle = 'rgba(20,16,26,.9)';
    c.fillRect(x0 - 5, y0 - width, 7, width * 2);
    for (var x = x0 + 70; x < x1 - 20; x += 150) { c.fillRect(x - 1.5, y0 - width - 1, 3, width * 2 + 2); }
  }

  function drawBackground(c, th, W, H, ground) {
    W = W || 1100; H = H || 560; ground = ground == null ? 522 : ground;
    var p = pal(th), LX = lampX(W), wallB = ground - 22, RAIL = ground - 128;
    c.save();
    roundRect(c, 4, 4, W - 8, H - 8, 22); c.clip();
    c.fillStyle = rgba(p.seam); c.fillRect(0, 0, W, H);
    // upper wall: vertical boards
    vPlanks(c, p, 0, W, 30, RAIL, 17);
    // ceiling beam + its shadow
    var bm = c.createLinearGradient(0, 0, 0, 32);
    bm.addColorStop(0, rgba(mul(p.wall, 0.45))); bm.addColorStop(0.8, rgba(mul(p.wall, 0.62))); bm.addColorStop(1, rgba(mul(p.wall, 0.9)));
    c.fillStyle = bm; c.fillRect(0, 0, W, 32);
    c.save(); c.beginPath(); c.rect(0, 0, W, 32); c.clip();
    grainLines(c, mulberry(5), mul(p.wall, 0.55), 0, 0, W, 32, false, 5);
    c.restore();
    var bs = c.createLinearGradient(0, 32, 0, 58);
    bs.addColorStop(0, 'rgba(0,0,0,.45)'); bs.addColorStop(1, 'rgba(0,0,0,0)');
    c.fillStyle = bs; c.fillRect(0, 32, W, 26);
    // pinned targets
    paperTarget(c, p, 340, 168, 74, 96, -0.055, 'bull', 31);
    paperTarget(c, p, 1044, 108, 60, 84, 0.07, 'sil', 47);
    // lower wall: horizontal wainscot boards
    hBoards(c, p, 0, W, RAIL + 12, wallB - 14, 23, 0.74);
    // chair rail
    var rl = c.createLinearGradient(0, RAIL, 0, RAIL + 13);
    rl.addColorStop(0, rgba(mul(p.wall, 1.25))); rl.addColorStop(0.3, rgba(mul(p.wall, 0.95))); rl.addColorStop(1, rgba(mul(p.wall, 0.5)));
    c.fillStyle = rl; c.fillRect(0, RAIL, W, 13);
    var rs = c.createLinearGradient(0, RAIL + 13, 0, RAIL + 24);
    rs.addColorStop(0, 'rgba(0,0,0,.4)'); rs.addColorStop(1, 'rgba(0,0,0,0)');
    c.fillStyle = rs; c.fillRect(0, RAIL + 13, W, 11);
    // baseboard
    var bb = c.createLinearGradient(0, wallB - 15, 0, wallB);
    bb.addColorStop(0, rgba(mul(p.wall, 0.95))); bb.addColorStop(0.2, rgba(mul(p.wall, 0.6))); bb.addColorStop(1, rgba(mul(p.wall, 0.42)));
    c.fillStyle = bb; c.fillRect(0, wallB - 15, W, 15);
    // floor
    floorBoards(c, p, W, H, LX, wallB);
    // sandbags stacked against the wall (right of the dummy, and one by the gun)
    sandbag(c, p, 1030, wallB - 4, 78, 32, 3, false);
    sandbag(c, p, 1100, wallB - 2, 78, 32, 4, true);
    sandbag(c, p, 1066, wallB - 30, 74, 30, 5, true);
    sandbag(c, p, 298, wallB - 3, 72, 30, 6, true);
    // light it
    c.globalCompositeOperation = 'multiply';
    c.drawImage(lightmap(p, W, H, LX, wallB, ground), 0, 0, W, H);
    c.globalCompositeOperation = 'lighter';
    var halo = c.createRadialGradient(LX, RIM_Y, 0, LX, RIM_Y, 120);
    halo.addColorStop(0, rgba(p.light, 0.16)); halo.addColorStop(1, 'rgba(0,0,0,0)');
    c.fillStyle = halo; c.fillRect(LX - 130, 0, 260, 240);
    // sheen on the floor boards
    c.save(); c.translate(LX, wallB + 24); c.scale(1, 0.1);
    var sh = c.createRadialGradient(0, 0, 0, 0, 0, 260);
    sh.addColorStop(0, rgba(p.light, 0.1)); sh.addColorStop(1, 'rgba(0,0,0,0)');
    c.fillStyle = sh; c.fillRect(-270, -270, 540, 540); c.restore();
    c.globalCompositeOperation = 'source-over';
    if (p.neon) {
      neonTube(c, 262, RAIL + 5, W + 10, RAIL + 5, p.acc2, 3);
      neonTube(c, 262, wallB - 15, W + 10, wallB - 15, p.acc, 2.2);
    }
    // vignette
    var vg = c.createRadialGradient(W * 0.6, H * 0.5, 230, W * 0.6, H * 0.52, 760);
    vg.addColorStop(0, 'rgba(0,0,0,0)'); vg.addColorStop(0.6, 'rgba(0,0,0,.25)'); vg.addColorStop(1, 'rgba(0,0,0,.7)');
    c.fillStyle = vg; c.fillRect(0, 0, W, H);
    // film grain / dither (kills gradient banding in OBS)
    c.fillStyle = grainPattern(c); c.fillRect(0, 0, W, H);
    // inner frame shadow
    c.lineWidth = 18; c.strokeStyle = 'rgba(0,0,0,.22)'; roundRect(c, 4, 4, W - 8, H - 8, 22); c.stroke();
    c.lineWidth = 7; c.strokeStyle = 'rgba(0,0,0,.25)'; c.stroke();
    c.restore();
    c.lineWidth = 2; c.strokeStyle = th.line || rgba(p.acc, 0.5); roundRect(c, 4, 4, W - 8, H - 8, 22); c.stroke();
  }

  // ------------------------------------------------------------------ ambient (lamp, cone, dust)
  var AMB = {};
  function ambCache(th, ground, W) {
    var p = pal(th), key = p.key + '|' + ground + '|' + W;
    if (AMB[key]) return AMB[key];
    var A = { p: p };
    // light cone sprite, half resolution (it is all soft), lamp-local coords
    var half = 262, top = CONE_Y, bot = ground + 14, s = 0.5;
    var cv = mkCanvas(half * 2 * s, (bot - top) * s), g = cv.getContext('2d');
    g.scale(s, s); g.translate(half, -top);
    for (var y = top; y < bot; y += 2) {
      var hw = coneHalf(y, ground), sf = coneSoft(y, ground), u = (y - top) / (ground - top);
      var a = 0.2 * Math.pow(1 - Math.min(1, u) * 0.82, 1.6) * (1 - smooth(ground - 30, bot, y)) * smooth(top - 1, top + 10, y);
      var tot = hw + sf, e = sf / tot;
      var lg = g.createLinearGradient(-tot, 0, tot, 0);
      lg.addColorStop(0, rgba(p.light, 0)); lg.addColorStop(e * 0.5, rgba(p.light, a * 0.3));
      lg.addColorStop(e, rgba(p.light, a * 0.75)); lg.addColorStop(0.5, rgba(p.light, a));
      lg.addColorStop(1 - e, rgba(p.light, a * 0.75)); lg.addColorStop(1 - e * 0.5, rgba(p.light, a * 0.3));
      lg.addColorStop(1, rgba(p.light, 0));
      g.fillStyle = lg; g.fillRect(-tot, y, tot * 2, 2);
    }
    // faint god-rays inside the cone
    g.globalCompositeOperation = 'lighter';
    var rnd = mulberry(77);
    for (var i = 0; i < 9; i++) {
      var f = (rnd() - 0.5) * 1.6, wdt = 6 + rnd() * 14, al = 0.02 + rnd() * 0.035;
      var x0 = f * 40, x1 = f * 200;
      var rg = g.createLinearGradient(0, top, 0, ground);
      rg.addColorStop(0, rgba(p.light, al)); rg.addColorStop(0.7, rgba(p.light, al * 0.4)); rg.addColorStop(1, rgba(p.light, 0));
      g.fillStyle = rg;
      g.beginPath(); g.moveTo(x0 - wdt * 0.2, top + 2); g.lineTo(x0 + wdt * 0.2, top + 2);
      g.lineTo(x1 + wdt, ground); g.lineTo(x1 - wdt, ground); g.closePath(); g.fill();
    }
    A.cone = cv; A.coneX = -half; A.coneY = top; A.coneW = half * 2; A.coneH = bot - top;
    // bulb bloom + mote sprites
    A.bloom = softDot(128, [[0, rgba(mix(p.light, [255, 255, 255, 1], 0.6), 0.9)], [0.12, rgba(p.light, 0.55)], [0.4, rgba(p.light, 0.12)], [1, rgba(p.light, 0)]]);
    A.mote = softDot(16, [[0, rgba(mix(p.light, [255, 255, 255, 1], 0.5), 1)], [0.35, rgba(p.light, 0.5)], [1, rgba(p.light, 0)]]);
    // dust motes: fixed seeded parameters, positions come from t
    var mr = mulberry(4242), motes = [];
    for (var m = 0; m < 44; m++) {
      motes.push({ x: (mr() - 0.5) * 2 * 1.05, y: mr(), vy: (mr() < 0.35 ? -1 : 1) * (3 + mr() * 9), ax: 5 + mr() * 16,
        fx: 0.12 + mr() * 0.3, ph: mr() * TAU, r: 0.7 + mr() * 1.6, tw: 0.6 + mr() * 1.8 });
    }
    A.motes = motes;
    var fall = [];
    for (m = 0; m < 30; m++) {
      fall.push({ x: (mr() - 0.5) * 640, v: 10 + mr() * 28, d: 0.03 + mr() * 0.5, ax: 3 + mr() * 10, fx: 1 + mr() * 2.2,
        ph: mr() * TAU, r: 0.6 + mr() * 1.1, a: 0.5 + mr() * 0.5 });
    }
    A.fall = fall;
    AMB[key] = A;
    return A;
  }

  var LAMPS = {};
  function lampSprite(p, sc) {
    var key = p.key + '|' + sc;
    if (LAMPS[key]) return LAMPS[key];
    var x0 = -72, y0 = -2, w = 144, h = 112;
    var cv = mkCanvas(w * sc, h * sc), g = cv.getContext('2d');
    g.scale(sc, sc); g.translate(-x0, -y0);
    // cord
    g.strokeStyle = rgba(mul(p.steel2, 0.8)); g.lineWidth = 2.4;
    g.beginPath(); g.moveTo(0, CORD_TOP - 2); g.lineTo(0, SHADE_TOP - 6); g.stroke();
    g.strokeStyle = rgba(p.steelHi, 0.25); g.lineWidth = 0.8;
    g.beginPath(); g.moveTo(-0.6, CORD_TOP - 2); g.lineTo(-0.6, SHADE_TOP - 6); g.stroke();
    // socket
    var sk = g.createLinearGradient(-8, 0, 8, 0);
    sk.addColorStop(0, rgba(mul(p.shade, 0.7))); sk.addColorStop(0.6, rgba(mix(p.shade, p.steelHi, 0.35))); sk.addColorStop(1, rgba(mul(p.shade, 0.6)));
    g.fillStyle = sk; roundRect(g, -7, SHADE_TOP - 9, 14, 12, 3); g.fill();
    g.fillStyle = rgba(p.rim, 0.9); g.fillRect(-8, SHADE_TOP - 1, 16, 2.2);
    // shade (enamel dome)
    g.beginPath();
    g.moveTo(-12, SHADE_TOP); g.bezierCurveTo(-24, SHADE_TOP + 2, -34, SHADE_TOP + 14, -42, SHADE_TOP + 22);
    g.quadraticCurveTo(-52, RIM_Y - 3, -62, RIM_Y); g.lineTo(62, RIM_Y);
    g.quadraticCurveTo(52, RIM_Y - 3, 42, SHADE_TOP + 22); g.bezierCurveTo(34, SHADE_TOP + 14, 24, SHADE_TOP + 2, 12, SHADE_TOP);
    g.closePath();
    var sg = g.createLinearGradient(-62, 0, 62, 0);
    sg.addColorStop(0, rgba(mul(p.shade, 0.55))); sg.addColorStop(0.28, rgba(mix(p.shade, p.shadeHi, 0.55)));
    sg.addColorStop(0.36, rgba(mix(p.shade, p.steelHi, 0.5))); sg.addColorStop(0.46, rgba(p.shade));
    sg.addColorStop(1, rgba(mul(p.shade, 0.5)));
    g.fillStyle = sg; g.fill();
    var sv = g.createLinearGradient(0, SHADE_TOP, 0, RIM_Y);
    sv.addColorStop(0, 'rgba(0,0,0,.25)'); sv.addColorStop(0.7, 'rgba(0,0,0,0)'); sv.addColorStop(1, rgba(p.light, 0.12));
    g.fillStyle = sv; g.fill();
    g.strokeStyle = 'rgba(0,0,0,.6)'; g.lineWidth = 1.2; g.stroke();
    // underside: the lit inside of the shade, seen from below
    var ug = g.createRadialGradient(0, RIM_Y + 1, 2, 0, RIM_Y + 1, 62);
    ug.addColorStop(0, rgba(mix(p.light, [255, 255, 255, 1], 0.7))); ug.addColorStop(0.35, rgba(p.light));
    ug.addColorStop(1, rgba(mul(p.light, 0.55)));
    g.fillStyle = ug; g.beginPath(); g.ellipse(0, RIM_Y + 0.5, 61, 6.5, 0, 0, TAU); g.fill();
    // rim
    g.strokeStyle = rgba(p.rim); g.lineWidth = 2.6;
    g.beginPath(); g.ellipse(0, RIM_Y + 0.5, 61.5, 6.5, 0, Math.PI, TAU); g.stroke();
    g.strokeStyle = rgba(mul(p.rim, 0.55)); g.lineWidth = 2.6;
    g.beginPath(); g.ellipse(0, RIM_Y + 0.5, 61.5, 6.5, 0, 0, Math.PI); g.stroke();
    // bulb
    var bgd = g.createRadialGradient(-2, RIM_Y + 2, 1, 0, RIM_Y + 3, 14);
    bgd.addColorStop(0, '#ffffff'); bgd.addColorStop(0.5, rgba(mix(p.light, [255, 255, 255, 1], 0.6))); bgd.addColorStop(1, rgba(p.light, 0.9));
    g.fillStyle = bgd; g.beginPath(); g.ellipse(0, RIM_Y + 3, 13, 9, 0, 0, TAU); g.fill();
    var L = { cv: cv, x: x0, y: y0, w: w, h: h };
    LAMPS[key] = L;
    return L;
  }

  function flicker(ts) {
    var f = 0.975 + 0.015 * Math.sin(ts * 7.3) + 0.01 * Math.sin(ts * 17.9 + 1.3);
    var win = Math.floor(ts / 7), h = hash1(win), dt = ts - win * 7 - (1 + h * 4.5);
    if (h < 0.5 && dt >= 0 && dt < 0.42) f *= 0.62 + 0.38 * hash1(win * 131 + Math.floor(dt * 26));
    return f;
  }

  function drawAmbient(c, th, t, W, H, opts) {
    W = W || 1100; H = H || 560;
    if (typeof opts === 'number') opts = { ground: opts };
    opts = opts || {};
    var ground = opts.ground || 522, shot = opts.shot == null ? -1 : +opts.shot;
    var A = ambCache(th, ground, W), p = A.p, LX = lampX(W), ts = (+t || 0) / 1000;
    var sway = 0.011 * Math.sin(ts * 0.83) + 0.004 * Math.sin(ts * 2.1 + 0.6);
    var f = flicker(ts);
    if (shot >= 0 && shot < 8) {
      // the bang knocks the lamp swinging and makes the bulb stutter
      sway += 0.075 * Math.exp(-shot * 0.62) * Math.sin(shot * 2.7) * smooth(0, 0.08, shot);
      if (shot < 0.4) f *= 0.5 + 0.5 * hash1(Math.floor(shot * 32) + 7);
    }
    var sc = Math.min(3, Math.max(1, Math.ceil(pxScale(c) * 2) / 2 * 1.5));
    var L = lampSprite(p, sc);
    c.save();
    c.translate(LX, 0); c.rotate(sway);
    c.globalCompositeOperation = 'lighter';
    c.globalAlpha = f;
    c.drawImage(A.cone, A.coneX, A.coneY, A.coneW, A.coneH);
    c.globalCompositeOperation = 'source-over';
    c.globalAlpha = 1;
    c.drawImage(L.cv, L.x, L.y, L.w, L.h);
    c.globalCompositeOperation = 'lighter';
    c.globalAlpha = 0.85 * f;
    c.drawImage(A.bloom, -70, RIM_Y + 3 - 70, 140, 140);
    c.globalAlpha = 0.22 * f;
    c.drawImage(A.bloom, -190, RIM_Y + 3 - 150, 380, 300);
    c.restore();
    // dust in the light
    c.save();
    c.globalCompositeOperation = 'lighter';
    var span = ground - 30 - (CONE_Y + 16), ms = A.motes, sn = Math.sin(sway), i, m, yy, hw, sf, xx, al, r;
    for (i = 0; i < ms.length; i++) {
      m = ms[i];
      yy = CONE_Y + 16 + (((m.y * span + m.vy * ts) % span) + span) % span;
      hw = coneHalf(yy, ground); sf = coneSoft(yy, ground);
      xx = m.x * hw + m.ax * Math.sin(ts * m.fx + m.ph) + 3 * Math.sin(ts * m.fx * 2.7 + m.ph * 1.9);
      var inside = 1 - smooth(hw - sf, hw + sf * 0.4, Math.abs(xx));
      var edge = smooth(0, 30, yy - CONE_Y - 16) * (1 - smooth(span - 40, span, yy - CONE_Y - 16));
      var tw = 0.5 + 0.5 * Math.sin(ts * m.tw + m.ph * 3);
      al = inside * edge * (0.3 + 0.7 * tw * tw) * f * (1 - 0.4 * (yy - CONE_Y) / (ground - CONE_Y));
      if (al < 0.03) continue;
      r = m.r * 2.4;
      c.globalAlpha = al;
      c.drawImage(A.mote, LX + xx + yy * sn - r, yy - r, r * 2, r * 2);
    }
    // grit shaken loose from the ceiling beam by the bang
    if (shot > 0 && shot < 6) {
      var fl = A.fall;
      for (i = 0; i < fl.length; i++) {
        var d = fl[i], st = shot - d.d;
        if (st <= 0) continue;
        yy = 34 + d.v * st + 9 * st * st;
        if (yy > ground) continue;
        xx = d.x + d.ax * Math.sin(st * d.fx + d.ph);
        hw = coneHalf(Math.max(CONE_Y, yy), ground); sf = coneSoft(Math.max(CONE_Y, yy), ground);
        var lit = yy < CONE_Y ? 0.35 : 0.18 + 0.82 * (1 - smooth(hw - sf, hw + sf * 0.4, Math.abs(xx - yy * sn)));
        al = lit * Math.min(1, st * 4) * (1 - smooth(3.5, 6, shot)) * d.a * f;
        if (al < 0.03) continue;
        r = d.r * 2.4;
        c.globalAlpha = al;
        c.drawImage(A.mote, LX + xx - r, yy - r, r * 2, r * 2);
      }
    }
    c.restore();
    // a lamp stutter briefly darkens the lit area it would be feeding
    if (f < 0.9) {
      if (!A.dim) {
        A.dim = c.createRadialGradient(LX, 260, 0, LX, 260, 480);
        A.dim.addColorStop(0, 'rgba(0,0,0,.8)'); A.dim.addColorStop(1, 'rgba(0,0,0,0)');
      }
      c.save(); c.globalAlpha = (0.9 - f) * 0.9; c.fillStyle = A.dim;
      c.fillRect(LX - 480, 0, 960, H); c.restore();
    }
  }

  // Optional: the room lit by the shot itself (additive, ~0.25 s). Call after the actors,
  // in world space; (mx, my) = the muzzle.
  function drawShotLight(c, age, th, mx, my, W, H) {
    if (!(age >= 0) || age > 0.25) return;
    W = W || 1100; H = H || 560;
    var p = pal(th), F = fxCache(th), I = age < 0.01 ? 1 : Math.exp(-(age - 0.01) * 18);
    c.save();
    c.globalCompositeOperation = 'lighter';
    c.globalAlpha = 0.5 * I;
    var R = 620;
    c.drawImage(F.glow, mx - R, my - R * 0.8, R * 2, R * 1.6);
    c.globalAlpha = 0.1 * I; c.fillStyle = rgba(p.fHot);
    roundRect(c, 4, 4, W - 8, H - 8, 22); c.fill();
    c.restore();
  }

  // ------------------------------------------------------------------ the gun rest
  // Gun space: origin on the cylinder axis, barrel +x, floor at y = 204.
  var RESTS = {};
  function drawRestShape(g, p) {
    var WH = [255, 255, 255, 1];
    var ironD = mul(p.iron, 0.5), ironM = p.iron, ironL = mix(p.iron, p.ironHi, 0.38), ironS = mix(p.iron, p.ironHi, 0.7);
    function cyl(x0, x1, y0, y1, shine) {           // a vertical turned part, lit from the upper right
      var gr = g.createLinearGradient(x0, 0, x1, 0);
      gr.addColorStop(0, rgba(mul(ironD, 0.8))); gr.addColorStop(0.3, rgba(ironM));
      gr.addColorStop(0.64, rgba(shine ? ironS : ironL)); gr.addColorStop(0.74, rgba(ironL)); gr.addColorStop(1, rgba(ironD));
      g.fillStyle = gr; g.fillRect(x0, y0, x1 - x0, y1 - y0);
    }
    function shadowBlob(x, y, rx, ry, a) {
      g.save(); g.translate(x, y); g.scale(1, ry / rx);
      var s = g.createRadialGradient(0, 0, 0, 0, 0, rx);
      s.addColorStop(0, 'rgba(0,0,0,' + a + ')'); s.addColorStop(0.55, 'rgba(0,0,0,' + a * 0.5 + ')'); s.addColorStop(1, 'rgba(0,0,0,0)');
      g.fillStyle = s; g.beginPath(); g.arc(0, 0, rx, 0, TAU); g.fill(); g.restore();
    }
    function foot(x, y, r) {                           // levelling screw + pad
      g.fillStyle = rgba(ironD); g.fillRect(x - 2.2, y - 9, 4.4, 8);
      g.strokeStyle = 'rgba(0,0,0,.5)'; g.lineWidth = 0.8;
      for (var i = 0; i < 3; i++) { g.beginPath(); g.moveTo(x - 2.2, y - 8 + i * 2.6); g.lineTo(x + 2.2, y - 7 + i * 2.6); g.stroke(); }
      g.fillStyle = rgba(mul(ironD, 0.8)); g.beginPath(); g.ellipse(x, y + 1.5, r, r * 0.3, 0, 0, TAU); g.fill();
      g.fillStyle = rgba(ironM); g.fillRect(x - r, y - 1, r * 2, 2.5);
      g.fillStyle = rgba(ironL); g.beginPath(); g.ellipse(x, y - 1, r, r * 0.3, 0, 0, TAU); g.fill();
    }
    function leg(x0, y0, x1, y1, w0, w1) {             // tapered cast leg
      var dx = x1 - x0, dy = y1 - y0, L = Math.sqrt(dx * dx + dy * dy), nx = -dy / L, ny = dx / L;
      g.beginPath();
      g.moveTo(x0 + nx * w0, y0 + ny * w0); g.lineTo(x1 + nx * w1, y1 + ny * w1);
      g.lineTo(x1 - nx * w1, y1 - ny * w1); g.lineTo(x0 - nx * w0, y0 - ny * w0); g.closePath();
      var lg = g.createLinearGradient(x0 + nx * w0, y0 + ny * w0 - 6, x0 - nx * w0, y0 - ny * w0 + 6);
      lg.addColorStop(0, rgba(ironD)); lg.addColorStop(0.5, rgba(ironM)); lg.addColorStop(1, rgba(ironL));
      g.fillStyle = lg; g.fill();
      g.strokeStyle = 'rgba(0,0,0,.55)'; g.lineWidth = 1; g.stroke();
      // top edge catch-light
      g.strokeStyle = rgba(p.ironHi, 0.35); g.lineWidth = 1;
      var sgn = ny < 0 ? 1 : -1;
      g.beginPath(); g.moveTo(x0 + sgn * nx * (w0 - 1), y0 + sgn * ny * (w0 - 1)); g.lineTo(x1 + sgn * nx * (w1 - 1), y1 + sgn * ny * (w1 - 1)); g.stroke();
    }
    // --- floor contact shadows
    shadowBlob(124, 206, 104, 10, 0.5);
    shadowBlob(40, 204, 20, 4, 0.6); shadowBlob(210, 204, 20, 4, 0.6); shadowBlob(132, 212, 22, 5, 0.6);
    // --- tripod: back legs, hub, front leg
    leg(112, 176, 42, 196, 7, 4.5);
    leg(136, 176, 208, 196, 7, 4.5);
    foot(40, 201, 10); foot(210, 201, 10);
    // hub
    g.beginPath(); g.moveTo(104, 184); g.lineTo(108, 152); g.quadraticCurveTo(124, 146, 140, 152); g.lineTo(144, 184);
    g.quadraticCurveTo(124, 190, 104, 184); g.closePath();
    var hg = g.createLinearGradient(104, 0, 144, 0);
    hg.addColorStop(0, rgba(ironD)); hg.addColorStop(0.35, rgba(ironM)); hg.addColorStop(0.66, rgba(ironL)); hg.addColorStop(1, rgba(ironD));
    g.fillStyle = hg; g.fill(); g.strokeStyle = 'rgba(0,0,0,.6)'; g.lineWidth = 1; g.stroke();
    // front leg comes toward us: short, wide, lower foot
    g.beginPath(); g.moveTo(114, 180); g.lineTo(145, 180); g.lineTo(144, 204); g.lineTo(122, 204); g.closePath();
    var fg = g.createLinearGradient(0, 180, 0, 204);
    fg.addColorStop(0, rgba(ironL)); fg.addColorStop(0.5, rgba(ironM)); fg.addColorStop(1, rgba(ironD));
    g.fillStyle = fg; g.fill(); g.strokeStyle = 'rgba(0,0,0,.6)'; g.stroke();
    g.fillStyle = rgba(p.ironHi, 0.3); g.fillRect(115, 180, 29, 1.2);
    foot(133, 210, 13);
    // maker's plate on the hub
    g.fillStyle = rgba(mul(p.brass, 0.85)); roundRect(g, 115, 160, 18, 10, 2); g.fill();
    g.fillStyle = 'rgba(0,0,0,.35)'; g.fillRect(118, 163.5, 12, 1.2); g.fillRect(118, 166.5, 8, 1.2);
    // --- column (tube) with lock collar
    cyl(111, 137, 90, 152, true);
    g.fillStyle = 'rgba(0,0,0,.35)'; g.fillRect(111, 148, 26, 4);
    cyl(107, 141, 124, 138, false);                    // lock collar
    g.fillStyle = 'rgba(0,0,0,.45)'; g.fillRect(107, 137, 34, 1.4);
    g.fillStyle = rgba(p.ironHi, 0.4); g.fillRect(107, 124, 34, 1.2);
    // T lock screw
    g.fillStyle = rgba(ironD); g.fillRect(141, 129, 14, 4.5);
    g.fillStyle = rgba(ironL); g.fillRect(141, 129, 14, 1.2);
    g.fillStyle = rgba(ironM); roundRect(g, 154, 121, 5, 20, 2.2); g.fill();
    g.fillStyle = rgba(p.ironHi, 0.45); g.fillRect(155, 122, 1.4, 18);
    // --- elevation screw + knurled wheel under the saddle
    var tg = g.createLinearGradient(116, 0, 132, 0);
    tg.addColorStop(0, rgba(mul(ironM, 0.7))); tg.addColorStop(0.62, rgba(ironS)); tg.addColorStop(1, rgba(mul(ironM, 0.6)));
    g.fillStyle = tg; g.fillRect(116, 60, 16, 32);
    g.strokeStyle = 'rgba(0,0,0,.5)'; g.lineWidth = 1;
    for (var ty = 62; ty < 90; ty += 3) { g.beginPath(); g.moveTo(116, ty + 1.2); g.lineTo(132, ty); g.stroke(); }
    var wy = 84, wg = g.createLinearGradient(0, wy - 7, 0, wy + 7);
    wg.addColorStop(0, rgba(mix(p.brass, WH, 0.45))); wg.addColorStop(0.35, rgba(p.brass)); wg.addColorStop(1, rgba(mul(p.brass, 0.38)));
    g.fillStyle = wg; roundRect(g, 92, wy - 7, 64, 14, 5); g.fill();
    g.save(); roundRect(g, 92, wy - 7, 64, 14, 5); g.clip();
    g.strokeStyle = 'rgba(0,0,0,.32)'; g.lineWidth = 1.1;
    for (var kx = 0; kx < 18; kx++) {
      var ang = -Math.PI / 2 + (kx + 0.5) / 18 * Math.PI, xx = 124 + Math.sin(ang) * 32;
      g.beginPath(); g.moveTo(xx, wy - 7); g.lineTo(xx, wy + 7); g.stroke();
    }
    var ws = g.createLinearGradient(92, 0, 156, 0);
    ws.addColorStop(0, 'rgba(0,0,0,.45)'); ws.addColorStop(0.3, 'rgba(0,0,0,0)'); ws.addColorStop(0.7, 'rgba(255,255,255,.12)'); ws.addColorStop(1, 'rgba(0,0,0,.4)');
    g.fillStyle = ws; g.fillRect(92, wy - 7, 64, 14);
    g.restore();
    g.strokeStyle = 'rgba(0,0,0,.55)'; g.lineWidth = 1; roundRect(g, 92, wy - 7, 64, 14, 5); g.stroke();
    // --- saddle (windage top) the bag sits in
    var yg = g.createLinearGradient(0, 34, 0, 62);
    yg.addColorStop(0, rgba(ironS)); yg.addColorStop(0.15, rgba(ironL)); yg.addColorStop(0.45, rgba(ironM)); yg.addColorStop(1, rgba(ironD));
    g.fillStyle = yg;
    g.beginPath(); g.moveTo(52, 30); g.lineTo(62, 30); g.lineTo(64, 46); g.lineTo(184, 46); g.lineTo(186, 30); g.lineTo(196, 30);
    g.lineTo(194, 54); g.quadraticCurveTo(193, 60, 186, 60); g.lineTo(62, 60); g.quadraticCurveTo(55, 60, 54, 54); g.closePath(); g.fill();
    g.strokeStyle = 'rgba(0,0,0,.6)'; g.lineWidth = 1; g.stroke();
    g.fillStyle = rgba(p.ironHi, 0.55); g.fillRect(52, 30, 10, 1.3); g.fillRect(186, 30, 10, 1.3);
    g.fillStyle = 'rgba(0,0,0,.35)'; g.fillRect(64, 56, 120, 1);
    [[70, 53], [178, 53], [124, 53]].forEach(function (b) {
      g.fillStyle = 'rgba(0,0,0,.5)'; g.beginPath(); g.arc(b[0] + 0.4, b[1] + 0.5, 2.3, 0, TAU); g.fill();
      g.fillStyle = rgba(mix(ironL, p.ironHi, 0.3)); g.beginPath(); g.arc(b[0], b[1], 2, 0, TAU); g.fill();
      g.strokeStyle = 'rgba(0,0,0,.6)'; g.lineWidth = 0.7; g.beginPath(); g.moveTo(b[0] - 1.4, b[1]); g.lineTo(b[0] + 1.4, b[1]); g.stroke();
    });
    // shadow of the saddle on the screw
    var us = g.createLinearGradient(0, 60, 0, 72);
    us.addColorStop(0, 'rgba(0,0,0,.55)'); us.addColorStop(1, 'rgba(0,0,0,0)');
    g.fillStyle = us; g.fillRect(114, 60, 20, 12);
    // --- the leather bag: the gun beds into its dip (anything above the barrel line is hidden by the gun)
    function bag() {
      g.beginPath();
      g.moveTo(70, -6);
      g.quadraticCurveTo(92, -6, 106, 0);
      g.quadraticCurveTo(124, 6, 142, 0);
      g.quadraticCurveTo(158, -6, 178, -8);
      g.quadraticCurveTo(191, -8, 192, 6);
      g.quadraticCurveTo(196, 30, 186, 46);
      g.lineTo(62, 46);
      g.quadraticCurveTo(52, 30, 56, 6);
      g.quadraticCurveTo(58, -6, 70, -6);
      g.closePath();
    }
    bag();
    var lg = g.createLinearGradient(0, -8, 0, 46);
    lg.addColorStop(0, rgba(mix(p.leather, WH, 0.1))); lg.addColorStop(0.4, rgba(p.leather)); lg.addColorStop(1, rgba(mul(p.leather, 0.42)));
    g.fillStyle = lg; g.fill();
    g.save(); bag(); g.clip();
    var sx = g.createLinearGradient(52, 0, 196, 0);
    sx.addColorStop(0, 'rgba(0,0,0,.35)'); sx.addColorStop(0.18, 'rgba(0,0,0,0)'); sx.addColorStop(0.82, 'rgba(0,0,0,0)'); sx.addColorStop(1, 'rgba(0,0,0,.3)');
    g.fillStyle = sx; g.fillRect(50, -12, 150, 60);
    var hl = g.createRadialGradient(160, 10, 2, 160, 10, 44);
    hl.addColorStop(0, rgba(p.light, 0.26)); hl.addColorStop(1, rgba(p.light, 0));
    g.fillStyle = hl; g.fillRect(100, -20, 100, 70);
    var hl2 = g.createRadialGradient(84, 8, 2, 84, 8, 30);
    hl2.addColorStop(0, rgba(p.light, 0.12)); hl2.addColorStop(1, rgba(p.light, 0));
    g.fillStyle = hl2; g.fillRect(50, -20, 70, 60);
    // compression creases fanning out under the gun's weight
    var cr = [[101, 2, 95, 11, 98, 22], [147, 2, 154, 9, 151, 19], [116, 6, 113, 11, 115, 15], [132, 6, 136, 12, 133, 17]];
    for (var i = 0; i < cr.length; i++) {
      var q = cr[i];
      g.strokeStyle = rgba(mul(p.leather, 0.42), 0.75); g.lineWidth = 1.3;
      g.beginPath(); g.moveTo(q[0], q[1]); g.quadraticCurveTo(q[2], q[3], q[4], q[5]); g.stroke();
      g.strokeStyle = rgba(mix(p.leather, WH, 0.3), 0.3); g.lineWidth = 0.9;
      g.beginPath(); g.moveTo(q[0] + 1.6, q[1]); g.quadraticCurveTo(q[2] + 1.6, q[3], q[4] + 1.6, q[5]); g.stroke();
    }
    var ao = g.createLinearGradient(0, 34, 0, 46);
    ao.addColorStop(0, 'rgba(0,0,0,0)'); ao.addColorStop(1, 'rgba(0,0,0,.55)');
    g.fillStyle = ao; g.fillRect(50, 34, 150, 12);
    g.restore();
    // piping + stitched seams
    g.strokeStyle = rgba(mul(p.leather, 0.28)); g.lineWidth = 1.4; bag(); g.stroke();
    g.save(); g.setLineDash([3.4, 2.6]); g.lineCap = 'round';
    g.strokeStyle = rgba(p.stitch, 0.85); g.lineWidth = 1.1;
    g.beginPath(); g.moveTo(74, 0); g.quadraticCurveTo(63, 4, 62, 18); g.quadraticCurveTo(61, 32, 66, 42); g.stroke();
    g.beginPath(); g.moveTo(176, -2); g.quadraticCurveTo(187, 0, 187, 16); g.quadraticCurveTo(188, 32, 182, 42); g.stroke();
    g.beginPath(); g.moveTo(66, 40); g.lineTo(182, 40); g.stroke();
    g.restore();
    // a small brass grommet / tie tab
    g.fillStyle = rgba(mul(p.brass, 0.9)); g.beginPath(); g.arc(187, 26, 2.6, 0, TAU); g.fill();
    g.fillStyle = 'rgba(0,0,0,.6)'; g.beginPath(); g.arc(187, 26, 1.2, 0, TAU); g.fill();
  }

  function drawGunRest(c, th, opts) {
    var p = pal(th), sc = Math.min(3, Math.max(1, Math.round(pxScale(c) * 2) / 2 * 1.5));
    var key = p.key + '|' + sc, R = RESTS[key];
    if (!R) {
      var x0 = 18, y0 = -16, w = 212, h = 236;
      var cv = mkCanvas(w * sc, h * sc), g = cv.getContext('2d');
      g.scale(sc, sc); g.translate(-x0, -y0);
      drawRestShape(g, p);
      R = RESTS[key] = { cv: cv, x: x0, y: y0, w: w, h: h };
    }
    var dx = (opts && opts.dx) || 0, dy = (opts && opts.dy) || 0;
    c.drawImage(R.cv, R.x + dx, R.y + dy, R.w, R.h);
  }

  // ------------------------------------------------------------------ muzzle flash
  var FX = {};
  function plume(p, hotLayer) {
    // an elongated flame lobe pointing +x; base at x = 10, centre line y = 48
    var w = 256, h = 96, cv = mkCanvas(w, h), g = cv.getContext('2d'), cy = h / 2;
    var layers = hotLayer ?
      [[0.9, 30, 70, p.fHot, 0.85], [0.8, 20, 44, mix(p.fHot, p.fCore, 0.6), 1], [0.7, 12, 22, p.fCore, 1]] :
      [[1.0, 44, 118, p.fOut, 0.55], [0.95, 38, 100, p.fMid, 0.8], [0.9, 30, 76, mix(p.fMid, p.fHot, 0.5), 0.8]];
    g.globalCompositeOperation = 'lighter';
    for (var i = 0; i < layers.length; i++) {
      var L = layers[i];
      g.save(); g.translate(10 + L[2], cy); g.scale(L[2] / L[1], 1);
      var gr = g.createRadialGradient(-L[1] * 0.35, 0, 0, 0, 0, L[1]);
      gr.addColorStop(0, rgba(L[3], L[4])); gr.addColorStop(0.55, rgba(L[3], L[4] * 0.55)); gr.addColorStop(1, rgba(L[3], 0));
      g.fillStyle = gr; g.beginPath(); g.arc(0, 0, L[1], 0, TAU); g.fill();
      g.restore();
    }
    // turbulent lobes along the edges
    var rnd = mulberry(hotLayer ? 11 : 12);
    for (var k = 0; k < 10; k++) {
      var lx = 30 + rnd() * (hotLayer ? 110 : 190), ly = cy + (rnd() - 0.5) * (hotLayer ? 26 : 50), lr = 7 + rnd() * (hotLayer ? 10 : 16);
      var lg = g.createRadialGradient(lx, ly, 0, lx, ly, lr);
      var cc = hotLayer ? p.fHot : p.fMid;
      lg.addColorStop(0, rgba(cc, 0.35)); lg.addColorStop(1, rgba(cc, 0));
      g.fillStyle = lg; g.fillRect(lx - lr, ly - lr, lr * 2, lr * 2);
    }
    return cv;
  }
  function fxCache(th) {
    var p = pal(th);
    if (FX[p.key]) return FX[p.key];
    var F = { p: p };
    F.cool = plume(p, false); F.hot = plume(p, true);
    F.glow = softDot(128, [[0, rgba(p.fHot, 0.85)], [0.18, rgba(p.fMid, 0.5)], [0.5, rgba(p.fOut, 0.16)], [1, rgba(p.fOut, 0)]]);
    F.core = softDot(64, [[0, 'rgba(255,255,255,1)'], [0.3, rgba(p.fCore, 0.95)], [0.6, rgba(p.fHot, 0.4)], [1, rgba(p.fHot, 0)]]);
    // a thin four-point glint spike (horizontal)
    var sp = mkCanvas(256, 16), sg = sp.getContext('2d');
    var lg = sg.createLinearGradient(0, 0, 256, 0);
    lg.addColorStop(0, rgba(p.fHot, 0)); lg.addColorStop(0.5, rgba(p.fCore, 1)); lg.addColorStop(1, rgba(p.fHot, 0));
    sg.fillStyle = lg; sg.beginPath(); sg.moveTo(0, 8); sg.lineTo(128, 2); sg.lineTo(256, 8); sg.lineTo(128, 14); sg.closePath(); sg.fill();
    F.spike = sp;
    F.sparkA = rgba(p.fSparkA); F.sparkB = rgba(p.fSparkB);
    FX[p.key] = F;
    return F;
  }

  function drawMuzzleFlash(c, age, th, seed) {
    if (!(age >= 0) || age > 0.16) return;
    var F = fxCache(th), p = F.p, rnd = mulberry(((seed | 0) ^ 0x2c1b3c6d) >>> 0);
    var I = age < 0.008 ? 1 : Math.exp(-(age - 0.008) * 21);
    var grow = 0.6 + 0.4 * smooth(0, 0.028, age);
    var hot = I * I, i;
    // seeded shape (drawn the same every frame of this shot)
    var pet = [];
    for (i = 0; i < 4; i++) pet.push([(i < 2 ? 1.02 : 0.52) * (i % 2 ? 1 : -1) + (rnd() - 0.5) * 0.28, 42 + rnd() * 34, 0.34 + rnd() * 0.12]);
    var mainL = 150 + rnd() * 40, tilt = (rnd() - 0.5) * 0.06;
    c.save();
    c.globalCompositeOperation = 'lighter';
    // 1. the big soft glow
    var gr = 150 * grow * (0.75 + 0.25 * I);
    c.globalAlpha = 0.6 * I; c.drawImage(F.glow, 26 * grow - gr, -gr, gr * 2, gr * 2);
    // 2. star petals (side blast)
    for (i = 0; i < pet.length; i++) {
      var len = pet[i][1] * grow * (0.55 + 0.45 * I), wd = len * pet[i][2];
      c.save(); c.translate(3, 0); c.rotate(pet[i][0]);
      c.globalAlpha = 0.95 * I; c.drawImage(F.cool, -4, -wd / 2, len, wd);
      c.globalAlpha = hot; c.drawImage(F.hot, -3, -wd * 0.32, len * 0.8, wd * 0.64);
      c.restore();
    }
    // 3. main forward plume - it detaches and rolls forward as it cools
    var L = mainL * grow * (0.6 + 0.4 * I), Wd = L * 0.44, off = 110 * age;
    c.save(); c.rotate(tilt);
    c.globalAlpha = Math.min(1, I * 1.1); c.drawImage(F.cool, off - 6, -Wd / 2, L, Wd);
    c.globalAlpha = hot; c.drawImage(F.hot, off - 4, -Wd * 0.34, L * 0.8, Wd * 0.68);
    c.restore();
    // 4. glint spikes, first frames only
    if (age < 0.05) {
      var ga = 1 - age / 0.05, sl = 250 * grow;
      c.globalAlpha = ga * 0.95; c.drawImage(F.spike, 14 - sl * 0.22, -5, sl, 10);
      c.save(); c.translate(14, 0); c.rotate(Math.PI / 2);
      c.drawImage(F.spike, -sl * 0.28, -4, sl * 0.56, 8); c.restore();
    }
    // 5. white-hot core just ahead of the muzzle
    var cr = 36 * grow;
    c.globalAlpha = Math.min(1, hot * 1.25); c.drawImage(F.core, 14 - cr, -cr, cr * 2, cr * 2);
    // 6. shock ring
    var rr = 12 + 860 * age, ra = 0.55 * smooth(0, 0.012, age) * Math.pow(Math.max(0, 1 - age / 0.16), 1.5);
    if (ra > 0.01) {
      var rx = 8 + 70 * age, band = c.createRadialGradient(rx, 0, rr * 0.7, rx, 0, rr);
      band.addColorStop(0, rgba(p.fRing, 0)); band.addColorStop(0.7, rgba(p.fRing, 0.16));
      band.addColorStop(0.94, rgba(p.fRing, 0.8)); band.addColorStop(1, rgba(p.fRing, 0));
      c.globalAlpha = ra; c.fillStyle = band; c.beginPath(); c.arc(rx, 0, rr, 0, TAU); c.fill();
    }
    // 7. sparks: burning powder grains
    c.lineCap = 'round';
    for (i = 0; i < 24; i++) {
      var a = (rnd() - 0.5) * 0.8; if (rnd() < 0.25) a *= 2.4;
      var sp = 650 + rnd() * 1700, life = 0.06 + rnd() * 0.1, dl = rnd() * 0.012, wdt = 0.9 + rnd() * 1.5, tint = rnd();
      var tt = age - dl;
      if (tt <= 0 || tt > life) continue;
      var dist = sp * (1 - Math.exp(-7 * tt)) / 7, v = sp * Math.exp(-7 * tt);
      var ca = Math.cos(a), sa = Math.sin(a), droop = 260 * tt * tt;
      var x = 8 + ca * dist, y = sa * dist + droop, tl = Math.max(4, v * 0.016);
      c.globalAlpha = Math.pow(1 - tt / life, 1.2);
      c.strokeStyle = tint < 0.5 ? F.sparkA : F.sparkB; c.lineWidth = wdt;
      c.beginPath(); c.moveTo(x - ca * tl, y - sa * tl - 520 * tt * 0.016); c.lineTo(x, y); c.stroke();
    }
    c.restore();
  }

  // ------------------------------------------------------------------ smoke
  var SMOKE = {};
  function puffSprite(lit, shade, seed, lumps) {
    var n = 128, cv = mkCanvas(n, n), g = cv.getContext('2d'), rnd = mulberry(seed), i, bl = [];
    for (i = 0; i < lumps; i++) {
      var a = rnd() * TAU, d = lumps > 1 ? Math.pow(rnd(), 0.7) * 26 : 0;
      bl.push([64 + Math.cos(a) * d, 64 + Math.sin(a) * d * 0.8, lumps > 1 ? 16 + rnd() * 16 : 46]);
    }
    // shadowed underside first, then the lit tops of every lump (the lamp is above)
    for (i = 0; i < bl.length; i++) {
      var b = bl[i], sg = g.createRadialGradient(b[0] + 4, b[1] + 9, 0, b[0] + 4, b[1] + 9, b[2] * 1.15);
      sg.addColorStop(0, rgba(shade, 0.62)); sg.addColorStop(1, rgba(shade, 0));
      g.fillStyle = sg; g.fillRect(0, 0, n, n);
    }
    for (i = 0; i < bl.length; i++) {
      var q = bl[i], lg = g.createRadialGradient(q[0] - q[2] * 0.3, q[1] - q[2] * 0.4, 0, q[0], q[1] - 2, q[2] * 0.92);
      lg.addColorStop(0, rgba(lit, 0.7)); lg.addColorStop(0.55, rgba(lit, 0.3)); lg.addColorStop(1, rgba(lit, 0));
      g.fillStyle = lg; g.fillRect(0, 0, n, n);
    }
    // fade the square edges
    g.globalCompositeOperation = 'destination-in';
    var m = g.createRadialGradient(64, 64, 26, 64, 64, 64);
    m.addColorStop(0, 'rgba(0,0,0,1)'); m.addColorStop(1, 'rgba(0,0,0,0)');
    g.fillStyle = m; g.fillRect(0, 0, n, n);
    return cv;
  }
  function smokeCache(th) {
    var p = pal(th);
    if (SMOKE[p.key]) return SMOKE[p.key];
    var S = { p: p,
      puffs: [puffSprite(p.smA, p.smS, 21, 9), puffSprite(p.smB, p.smS, 22, 9), puffSprite(p.smA, p.smS, 23, 7)],
      soft: [puffSprite(p.smA, p.smS, 24, 3), puffSprite(p.smB, p.smS, 25, 3)] };
    SMOKE[p.key] = S;
    return S;
  }

  function drawSmoke(c, age, th, seed) {
    if (!(age >= 0) || age > 3) return;
    var S = smokeCache(th), rnd = mulberry((((seed | 0) * 2654435761) ^ 0x51ed27) >>> 0), i;
    var fade = Math.pow(1 - age / 3, 1.4);
    c.save();
    // the blast cloud: shoved out of the barrel, drags to a stop, then rises and rolls over
    for (i = 0; i < 26; i++) {
      var core = i < 9;
      var d = rnd() * 0.04, ang = (rnd() - 0.5) * (core ? 0.3 : 0.55) - 0.07, v0 = (core ? 80 : 190) + rnd() * (core ? 260 : 420),
        r0 = (core ? 11 : 9) + rnd() * 11, rise = 6 + rnd() * 36, spin = (rnd() - 0.5) * 1.8, rot0 = rnd() * TAU,
        vi = (rnd() * 3) | 0, a0 = (core ? 0.42 : 0.24) + rnd() * 0.22, wob = rnd() * TAU;
      var t = age - d;
      if (t <= 0) continue;
      var reach = v0 / 4.4 * (1 - Math.exp(-4.4 * t));
      var x = Math.cos(ang) * reach + (6 + rise * 0.35) * t + 6 * Math.sin(t * 1.5 + wob) * Math.min(1, t);
      var y = Math.sin(ang) * reach - rise * Math.pow(t, 1.35) + 3 * Math.sin(t * 1.1 + wob * 2);
      var r = r0 * (1 + 1.9 * Math.pow(t, 0.72));
      var al = a0 * Math.min(1, t / 0.03) * fade;
      if (al < 0.01) continue;
      var stretch = 1 + 1.3 * Math.exp(-t * 7);           // smeared along the jet at first
      var lumpy = Math.exp(-t * 1.25);
      c.save(); c.translate(x, y); c.rotate(ang); c.scale(stretch, 1 / Math.sqrt(stretch)); c.rotate(rot0 + spin * t);
      c.globalAlpha = al * (0.35 + 0.65 * lumpy);
      c.drawImage(S.puffs[vi], -r * 1.3, -r * 1.3, r * 2.6, r * 2.6);
      if (lumpy < 0.9) {
        c.globalAlpha = al * (1 - lumpy) * 0.9;
        c.drawImage(S.soft[vi & 1], -r * 1.5, -r * 1.5, r * 3, r * 3);
      }
      c.restore();
    }
    // the lazy wisp curling up out of the barrel afterwards
    for (i = 0; i < 14; i++) {
      var dw = 0.08 + i * 0.085 + rnd() * 0.05, sw = rnd() * TAU, vw = (rnd() * 3) | 0, rw0 = 3.5 + rnd() * 3.5;
      var tw = age - dw, lifeW = 1.6;
      if (tw <= 0 || tw > lifeW) continue;
      var wx = 4 + 8 * tw + 8 * Math.sin(tw * 2.8 + sw) * tw;
      var wy = -30 * tw - 8 * tw * tw;
      var rw = rw0 * (1 + 1.5 * tw);
      var aw = 0.4 * Math.sin(Math.PI * Math.min(1, tw / lifeW)) * Math.max(0, 1 - dw / 1.45);
      if (aw < 0.01) continue;
      c.save(); c.translate(wx, wy); c.rotate(sw + tw * 1.3); c.scale(1, 1.25);
      c.globalAlpha = aw;
      c.drawImage(S.puffs[vw], -rw * 1.3, -rw * 1.3, rw * 2.6, rw * 2.6);
      c.restore();
    }
    c.restore();
  }

  // ------------------------------------------------------------------ camera shake
  function shake(age) {
    if (!(age >= 0) || age > 0.6) return { x: 0, y: 0, r: 0 };
    var e = Math.exp(-age * 9) * (1 - smooth(0.42, 0.6, age));
    return {
      x: e * (-7 * Math.cos(age * 95) + 2.5 * Math.sin(age * 151 + 0.7)),
      y: e * (4.5 * Math.sin(age * 113 + 0.4) + 1.5 * Math.cos(age * 177)),
      r: e * 0.012 * Math.sin(age * 71 + 1.1)
    };
  }

  function SCENE_LAMP(W) { return { x: lampX(W), y: RIM_Y + 3 }; }

  return { drawBackground: drawBackground, drawAmbient: drawAmbient, drawGunRest: drawGunRest,
    drawMuzzleFlash: drawMuzzleFlash, drawSmoke: drawSmoke, shake: shake, drawShotLight: drawShotLight,
    SCENE_LAMP: SCENE_LAMP };
})(root);

  // ------------------------------------------------------------------ sound
  function Sfx() { this.ctx = null; this.vol = 0.6; }
  Sfx.prototype.ac = function () {
    if (!this.ctx) {
      var AC = root.AudioContext || root.webkitAudioContext;
      if (!AC) return null;
      try { this.ctx = new AC(); } catch (e) { return null; }
    }
    if (this.ctx.state === 'suspended') { try { this.ctx.resume(); } catch (e) {} }
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
  Sfx.prototype.tone = function (freq, dur, gain, type, to, delay) {
    var c = this.ac(); if (!c) return;
    var t = c.currentTime + (delay || 0);
    var o = c.createOscillator(); o.type = type || 'sine'; o.frequency.setValueAtTime(freq, t);
    if (to) o.frequency.exponentialRampToValueAtTime(to, t + dur);
    var g = c.createGain(); g.gain.setValueAtTime(0.0001, t); g.gain.exponentialRampToValueAtTime(gain * this.vol, t + 0.008);
    g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
    o.connect(g); g.connect(c.destination); o.start(t); o.stop(t + dur + 0.02);
  };
  Sfx.prototype.play = function (name) {
    switch (name) {
      case 'load': this.tone(2600, 0.09, 0.18, 'triangle'); this.noise(0.03, 'bandpass', 6000, 0, 3, 0.3); break;
      case 'close': this.noise(0.06, 'bandpass', 1400, 0, 2, 0.6); this.tone(900, 0.04, 0.15, 'square'); break;
      case 'tick': this.noise(0.014, 'bandpass', 5200, 0, 6, 0.22); break;
      case 'cock': this.noise(0.02, 'bandpass', 2400, 0, 8, 0.6); this.noise(0.025, 'bandpass', 1900, 0, 8, 0.7, 0.09); break;
      case 'heart': this.tone(58, 0.14, 0.7, 'sine', 40); this.tone(52, 0.12, 0.5, 'sine', 38, 0.18); break;
      case 'click': this.noise(0.03, 'bandpass', 3600, 0, 9, 0.9); this.tone(1900, 0.025, 0.2, 'square'); break;
      case 'bang':
        this.noise(0.05, 'highpass', 2500, 0, 0.7, 1.0);
        this.noise(0.9, 'lowpass', 4200, 160, 0.8, 1.0);
        this.tone(75, 0.45, 0.9, 'sine', 30);
        this.noise(1.6, 'lowpass', 900, 120, 0.5, 0.25, 0.08);
        break;
      case 'win': this.tone(660, 0.14, 0.25, 'triangle'); this.tone(880, 0.14, 0.25, 'triangle', 0, 0.12); this.tone(1320, 0.3, 0.25, 'triangle', 0, 0.24); break;
      case 'beep': this.tone(880, 0.07, 0.18, 'square'); break;
      case 'revive': this.tone(300, 0.6, 0.18, 'triangle', 900); this.tone(1200, 0.2, 0.12, 'sine', 0, 0.5); break;
    }
  };

  // ------------------------------------------------------------------ styles
  var CSS_DONE = false;
  function injectCss() {
    if (CSS_DONE || !root.document) return;
    CSS_DONE = true;
    var css = [
      '.hrr{position:absolute;inset:0;font-family:var(--hrr-font);color:var(--hrr-ink);pointer-events:none;}',
      '.hrr canvas{position:absolute;left:0;top:0;width:100%;height:100%;}',
      '.hrr-top{position:absolute;left:14px;right:14px;top:10px;height:48px;display:flex;align-items:center;gap:14px;}',
      '.hrr-title{font-weight:900;font-size:26px;letter-spacing:.06em;text-transform:uppercase;color:var(--hrr-accent);text-shadow:0 2px 0 rgba(0,0,0,.6),var(--hrr-glow);white-space:nowrap;min-width:0;max-width:440px;}',
      // the name's clip box gets room for the glow (padding, cancelled by negative margins; the
      // bottom one also keeps the subtitle's old 2px overlap), else overflow:hidden boxes the glow in
      '.hrr-name{display:block;overflow:hidden;text-overflow:ellipsis;padding:8px 14px;margin:-8px -14px -10px}',
      '.hrr-title small{display:block;font-size:12px;letter-spacing:.2em;color:var(--hrr-dim);margin-top:-2px;text-shadow:none;overflow:hidden;text-overflow:ellipsis;max-width:440px}',
      '.hrr-chips{display:flex;gap:6px;flex:1;justify-content:center}',
      '.hrr-chip{min-width:84px;padding:5px 10px;border-radius:9px;background:var(--hrr-panel);border:1px solid var(--hrr-line);font-size:13px;font-weight:700;text-align:center;opacity:.65}',
      '.hrr-chip b{display:block;font-size:11px;font-weight:600;color:var(--hrr-dim);letter-spacing:.08em}',
      '.hrr-chip.cur{opacity:1;border-color:var(--hrr-accent);box-shadow:0 0 0 2px var(--hrr-accent) inset,var(--hrr-glow)}',
      '.hrr-chip.ok{opacity:.9;color:var(--hrr-good)} .hrr-chip.hit{opacity:1;color:#fff;background:var(--hrr-bad)}',
      '.hrr-timer{min-width:150px;text-align:right;white-space:nowrap}',
      '.hrr-timer b{display:block;font-size:11px;letter-spacing:.16em;color:var(--hrr-dim);text-transform:uppercase}',
      '.hrr-timer span{font-size:30px;font-weight:900;line-height:1;font-variant-numeric:tabular-nums}',
      '.hrr-timer.last span{color:var(--hrr-bad);animation:hrr-pulse .5s ease-in-out infinite alternate}',
      '@keyframes hrr-pulse{from{transform:scale(1)}to{transform:scale(1.12)}}',
      '.hrr-box{position:absolute;background:var(--hrr-panel);border:1px solid var(--hrr-line);border-radius:14px;padding:10px 12px;box-shadow:0 10px 30px rgba(0,0,0,.45)}',
      '.hrr-box h4{margin:0 0 6px;font-size:12px;letter-spacing:.18em;text-transform:uppercase;color:var(--hrr-accent)}',
      '.hrr-odds{left:14px;top:70px;width:236px}',
      '.hrr-odds table{width:100%;border-collapse:collapse;font-size:13px;font-variant-numeric:tabular-nums}',
      '.hrr-odds th{font-size:10px;letter-spacing:.1em;color:var(--hrr-dim);font-weight:600;text-align:right;padding:0 3px 3px}',
      '.hrr-odds th:first-child,.hrr-odds td:first-child{text-align:left}',
      '.hrr-odds td{padding:3px;text-align:right;border-top:1px solid rgba(255,255,255,.06)}',
      '.hrr-odds tr.cur td{color:var(--hrr-accent);font-weight:800}',
      '.hrr-odds tr.done td{opacity:.45}',
      '.hrr-players{left:14px;top:260px;width:236px;bottom:14px;overflow:hidden}',
      '.hrr-p{display:flex;justify-content:space-between;gap:6px;font-size:14px;padding:3px 0;border-top:1px solid rgba(255,255,255,.06)}',
      '.hrr-p u{text-decoration:none;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:110px}',
      '.hrr-p i{font-style:normal;font-weight:800;font-variant-numeric:tabular-nums;white-space:nowrap}',
      '.hrr-p .bg{color:var(--hrr-bad)} .hrr-p .sv{color:var(--hrr-good)} .hrr-p small{color:var(--hrr-dim);font-size:11px}',
      '.hrr-empty{color:var(--hrr-dim);font-size:13px;padding:4px 0}',
      '.hrr-rules{left:292px;top:66px;width:334px;transition:opacity .35s}',
      '.hrr-rules p{margin:3px 0;font-size:14px;line-height:1.3}',
      '.hrr-rules em{font-style:normal;font-weight:900;color:var(--hrr-accent)}',
      '.hrr-rules .cmd{margin-top:6px;font-size:13px;color:var(--hrr-dim)}',
      '.hrr-rules .warn{margin-top:6px;font-weight:900;color:var(--hrr-bad);font-size:16px;letter-spacing:.06em;animation:hrr-pulse .5s ease-in-out infinite alternate;transform-origin:left center}',
      '.hrr-banner{position:absolute;left:262px;right:330px;top:420px;text-align:center;font-weight:900;font-size:34px;letter-spacing:.04em;text-shadow:0 3px 0 rgba(0,0,0,.7),0 0 18px rgba(0,0,0,.6);transition:opacity .3s,transform .3s}',
      '.hrr-banner small{display:block;font-size:17px;font-weight:700;color:var(--hrr-dim);letter-spacing:.02em;margin-top:2px}',
      '.hrr-banner.bang{color:var(--hrr-bad);font-size:44px} .hrr-banner.click{color:var(--hrr-good)}',
      '.hrr-sum{left:292px;top:70px;width:520px;padding:14px 20px;text-align:center}',
      // (room for the glow in the clip box, like .hrr-name; the bottom margin collapses with .sub's 2px)
      '.hrr-sum h3{margin:-8px -14px;padding:8px 14px;font-size:26px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-weight:900;letter-spacing:.04em;color:var(--hrr-accent);text-shadow:var(--hrr-glow)}',
      '.hrr-sum h3.bad{color:var(--hrr-bad)} .hrr-sum h3.good{color:var(--hrr-good)}',
      '.hrr-sum .sub{color:var(--hrr-dim);font-size:14px;margin:2px 0 10px}',
      '.hrr-sum .row{display:flex;justify-content:space-between;font-size:16px;padding:3px 4px;border-top:1px solid rgba(255,255,255,.07)}',
      '.hrr-sum .row i{font-style:normal;font-weight:800}',
      '.hrr-sum .pos{color:var(--hrr-good)} .hrr-sum .neg{color:var(--hrr-bad)}',
      '.hrr-sum .tot{display:flex;justify-content:center;gap:18px;margin-top:10px;font-size:13px;color:var(--hrr-dim)}',
      '.hrr-sum .cut{margin-top:8px;font-size:14px;color:var(--hrr-accent)}',
      '.hrr-test{position:absolute;right:16px;bottom:12px;font-size:11px;letter-spacing:.2em;color:var(--hrr-bad);font-weight:900}',
      '.hrr-hide{opacity:0!important}'
    ].join('\n');
    var st = root.document.createElement('style');
    st.setAttribute('data-hexgames', 'russian');
    st.textContent = css;
    root.document.head.appendChild(st);
  }

  // ------------------------------------------------------------------ instance
  function RR(container, config, opts) {
    injectCss();
    opts = opts || {};
    this.box = container;
    this.cfg = merge(DEFAULTS, config);
    this.soundOn = opts.sound !== false;
    this.sfx = this.soundOn ? new Sfx() : null;
    this.root = root.document.createElement('div');
    this.root.className = 'hrr';
    this.canvas = root.document.createElement('canvas');
    this.ctx = this.canvas.getContext('2d');
    this.root.appendChild(this.canvas);
    this.ui = root.document.createElement('div');
    this.root.appendChild(this.ui);
    container.appendChild(this.root);
    this.k = 1;
    this.st = null;            // STATE
    this.at = now();           // when st arrived (local clock)
    this.keys = {};            // what the DOM shows
    this.done = {};            // sound / effect events already fired (per pull)
    this.restSpin = 0;         // cylinder angle between pulls (visual only)
    this.spinBase = 0;         // ... where the current pull's spin started
    this.parts = null;         // stuffing particles of the current bang
    this.pullKey = null;
    this._build();
    this.setConfig(this.cfg);
    this.resize();
    var self = this;
    this._loop = function (t) { if (self.dead) return; self._frame(t); self._raf = raf(self._loop); };
    this._raf = raf(this._loop);
  }
  var P = RR.prototype;

  P._build = function () {
    this.ui.innerHTML =
      '<div class="hrr-top"><div class="hrr-title"><span class="hrr-name"></span><small class="hrr-sub"></small></div>' +
      '<div class="hrr-chips"></div><div class="hrr-timer"><b></b><span></span></div></div>' +
      '<div class="hrr-box hrr-odds"><h4>Payouts</h4><div class="hrr-oddsb"></div></div>' +
      '<div class="hrr-box hrr-players"><h4>On the line</h4><div class="hrr-pb"></div></div>' +
      '<div class="hrr-box hrr-rules"></div>' +
      '<div class="hrr-banner"></div>' +
      '<div class="hrr-box hrr-sum"></div>' +
      '<div class="hrr-test"></div>';
    var q = this.ui.querySelector.bind(this.ui);
    this.el = { name: q('.hrr-name'), sub: q('.hrr-sub'), chips: q('.hrr-chips'), timer: q('.hrr-timer'), tlabel: q('.hrr-timer b'),
      tval: q('.hrr-timer span'), odds: q('.hrr-odds'), oddsb: q('.hrr-oddsb'), players: q('.hrr-players'),
      pb: q('.hrr-pb'), rules: q('.hrr-rules'), banner: q('.hrr-banner'), sum: q('.hrr-sum'), test: q('.hrr-test') };
  };

  P.setConfig = function (cfg) {
    this.cfg = merge(DEFAULTS, cfg);
    var th = THEMES[this.cfg.theme] || THEMES.saloon;
    this.th = th;
    var s = this.root.style;
    s.setProperty('--hrr-font', th.font); s.setProperty('--hrr-ink', th.ink); s.setProperty('--hrr-dim', th.dim);
    s.setProperty('--hrr-accent', th.accent); s.setProperty('--hrr-panel', th.panel); s.setProperty('--hrr-line', th.line);
    s.setProperty('--hrr-good', th.good); s.setProperty('--hrr-bad', th.bad); s.setProperty('--hrr-glow', th.glow);
    // the title (branding): text only, smaller as it gets longer (ellipsis past the box)
    var title = String(this.cfg.title == null ? '' : this.cfg.title).trim() || DEFAULTS.title;
    this.el.name.textContent = title;
    this.el.name.title = title;
    this.el.name.style.fontSize = (title.length <= 16 ? 26 : Math.max(15, Math.round(26 * 16 / title.length))) + 'px';
    if (this.sfx) this.sfx.vol = clamp(+this.cfg.sfx_volume || 0, 0, 1);
    this._bg = null; this._burlap = null;
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
    this._bg = null; this._burlap = null;
  };

  P.setState = function (st) {
    this.st = st && typeof st === 'object' ? st : null;
    this.at = now();
    var g = this.st && this.st.game;
    if (g && g.pull) {
      var key = g.id + '/' + g.pull.round;
      if (key !== this.pullKey) {
        this.pullKey = key;
        this.parts = null;
        this.spinBase = this.restSpin;
      }
    } else if (!g) {
      this.pullKey = null;
    }
  };

  P.reset = function () { this.st = null; this.keys = {}; this.parts = null; this.pullKey = null; };
  P.destroy = function () {
    this.dead = true; caf(this._raf);
    if (this.root.parentNode) this.root.parentNode.removeChild(this.root);
    if (this.sfx && this.sfx.ctx) { try { this.sfx.ctx.close(); } catch (e) {} }
  };

  // local ms since the phase started / until it ends
  P._elapsed = function (t) { var g = this.st && this.st.game; return g ? (+g.elapsed_ms || 0) + (t - this.at) : 0; };
  P._left = function (t) {
    var g = this.st && this.st.game;
    if (!g || g.ends_in_ms == null) return null;
    return Math.max(0, (+g.ends_in_ms || 0) - (t - this.at));
  };

  P._event = function (name, due, t) {
    // fire a one-shot effect (times in s) when its time has come - never replay an old one on a late join
    var key = this.pullKey + ':' + name;
    if (this.done[key] || t < due) return false;
    this.done[key] = 1;
    return t - due < 0.4;
  };
  P._snd = function (name) { if (this.sfx && this.cfg.sfx) { try { this.sfx.play(name); } catch (e) {} } };

  // ------------------------------------------------------------------ the frame
  P._frame = function (t) {
    var st = this.st, g = st && st.game;
    var el = this._elapsed(t) / 1000, left = this._left(t);
    var pose = { fall: 0, jerk: 0, flinch: 0, tremble: 0, revive: 0, hole: false, cock: 0, trig: 0,
      recoil: 0, flash: -1, spin: this.restSpin, spinSpeed: 0, loadIn: 1, open: 0,
      bullets: g ? g.loaded.length : 0, shot: -1, clickAgo: -1 };
    if (g && g.pull && (g.phase === 'pulling' || g.phase === 'result' || g.phase === 'over')) {
      this._pullPose(g, pose, t, el);
    }
    this._draw(pose, t);
    this._dom(g, left, t);
  };

  // How far the cylinder turns in a pull: 4-6 turns plus a few chambers, from the pull's seed. It is
  // deliberately unrelated to which chamber fires, so no frame of the spin gives the outcome away.
  function spinTotal(seed) {
    var rnd = mulberry((seed || 1) ^ 0x2c1b3c6d);
    return TAU * (4 + Math.floor(rnd() * 3)) + STEP * Math.floor(rnd() * 6);
  }

  P._pullPose = function (g, pose, t, el) {
    var p = g.pull, D = +this.cfg.pull_seconds || 9, R = +this.cfg.result_seconds || 4;
    var since;                                        // seconds since the pull started
    if (g.phase === 'pulling') since = el;
    else if (g.phase === 'result') since = D + el;
    else since = D + R + el;
    var spinA = 1.6, spinB = Math.max(spinA + 1.5, D - 2.6);
    var total = spinTotal(p.seed), base = this.spinBase;
    pose.bullets = p.loaded.length;
    // load + close
    if (since < 1.2) { pose.open = easeOut(since / 0.35); pose.loadIn = clamp((since - 0.35) / 0.7, 0, 1); }
    else if (since < 1.6) { pose.open = 1 - ease((since - 1.2) / 0.4); pose.loadIn = 1; }
    if (this._event('load', 0.7, since)) this._snd('load');
    if (this._event('close', 1.4, since)) this._snd('close');
    // spin: an eased deceleration; spinSpeed is its exact derivative (drives the motion blur)
    var u = clamp((since - spinA) / (spinB - spinA), 0, 1);
    pose.spin = base + total * (1 - Math.pow(1 - u, 3.2));
    pose.spinSpeed = since >= spinA && since < spinB ? total * 3.2 * Math.pow(1 - u, 2.2) / (spinB - spinA) : 0;
    var tickN = Math.floor((pose.spin - base) / STEP);
    if (since >= spinA && since < spinB + 0.1) {
      if (this._tick == null || this._tickKey !== this.pullKey) { this._tick = tickN; this._tickKey = this.pullKey; }
      if (tickN > this._tick) { this._tick = tickN; if (this.sfx && this.cfg.sfx) this._snd('tick'); }
    }
    // cock (double action: cocking turns the cylinder one chamber), tension, pull
    var idx = clamp((since - (D - 2.4)) / 0.5, 0, 1);
    pose.spin += STEP * ease(idx);
    if (since >= spinB) this.restSpin = (base + total + STEP * ease(idx)) % TAU;
    pose.cock = idx;
    if (this._event('cock', D - 1.9, since)) this._snd('cock');
    if (this._event('heart1', D - 1.6, since)) this._snd('heart');
    if (this._event('heart2', D - 0.9, since)) this._snd('heart');
    pose.tremble = since > D - 2.4 && since < D ? clamp((since - (D - 2.4)) / 2.4, 0, 1) : 0;
    pose.trig = clamp((since - (D - 0.15)) / 0.15, 0, 1);
    if (since >= D) { pose.cock = 0; pose.trig = 1 - clamp((since - D - 0.3) / 0.3, 0, 1); }
    var ago = since - D;                             // seconds since the outcome
    if (ago < 0) return;
    if (p.fired) {
      pose.shot = ago;
      if (this._event('bang', D, since)) this._snd('bang');
      var kick = ago < 0.06 ? ago / 0.06 : Math.exp(-(ago - 0.06) * 7);
      pose.recoil = kick;
      pose.flash = ago < 0.16 ? ago : -1;
      pose.hole = true;
      pose.jerk = ago < 0.35 ? Math.sin(clamp(ago / 0.35, 0, 1) * Math.PI) : 0;
      var c = clamp((ago - 0.22) / 1.2, 0, 1);
      pose.fall = c < 1 ? easeIn(c) : 1;
      if (!this.parts) this._burst(p.seed, ago);
      // the revive, over the end of the game-over card
      if (g.phase === 'over') {
        var left = this._left(t) / 1000;
        if (left != null && left < 3.2) {
          pose.revive = ease((3.2 - left) / 2.6);
          if (this._event('revive', 0, 3.2 - left)) this._snd('revive');
        }
      }
    } else {
      pose.clickAgo = ago;
      if (this._event('click', D, since)) this._snd('click');
      pose.flinch = ago < 1 ? Math.exp(-ago * 4) : 0;
      if (g.phase === 'over' && g.outcome === 'survived' && this._event('win', D + R, since)) this._snd('win');
    }
  };

  // stuffing: seeded so every overlay throws the same fluff
  P._burst = function (seed, ago) {
    var rnd = mulberry(seed || 1), parts = [];
    for (var i = 0; i < 54; i++) {
      var a = -2.6 + rnd() * 2.2, sp = 90 + rnd() * 230;
      parts.push({ x0: 0, y0: 0, vx: Math.cos(a) * sp * (0.5 + rnd() * 0.6) + 40, vy: Math.sin(a) * sp - 60 - rnd() * 120,
        r: 4 + rnd() * 7, rot: rnd() * TAU, spin: (rnd() - 0.5) * 8, tint: rnd() });
    }
    this.parts = parts;
  };

  // ------------------------------------------------------------------ drawing
  P._draw = function (pose, t) {
    var c = this.ctx, k = this.k, th = this.th;
    c.setTransform(1, 0, 0, 1, 0, 0);
    c.clearRect(0, 0, this.canvas.width, this.canvas.height);
    c.setTransform(k, 0, 0, k, 0, 0);
    // the bang shakes the whole frame
    var sh = pose.shot >= 0 ? SCENE.shake(pose.shot) : null;
    if (sh && (sh.x || sh.y || sh.r)) {
      c.translate(BASE_W / 2, BASE_H / 2); c.rotate(sh.r); c.translate(-BASE_W / 2 + sh.x, -BASE_H / 2 + sh.y);
    }
    c.drawImage(this._background(), 0, 0, BASE_W, BASE_H);
    SCENE.drawAmbient(c, th, t, BASE_W, BASE_H, { ground: GROUND, shot: pose.shot });
    this._drawDummy(c, pose, t);
    this._drawGun(c, pose, t);
    this._drawFx(c, pose, t);
    if (pose.shot >= 0) {
      var m = this._muzzle(pose, t, 0);
      SCENE.drawShotLight(c, pose.shot, th, m.x, m.y, BASE_W, BASE_H);
    }
  };

  function roundRect(c, x, y, w, h, r) {
    c.beginPath();
    c.moveTo(x + r, y); c.lineTo(x + w - r, y); c.quadraticCurveTo(x + w, y, x + w, y + r);
    c.lineTo(x + w, y + h - r); c.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
    c.lineTo(x + r, y + h); c.quadraticCurveTo(x, y + h, x, y + h - r);
    c.lineTo(x, y + r); c.quadraticCurveTo(x, y, x + r, y); c.closePath();
  }

  P._background = function () {
    if (this._bg) return this._bg;
    var k = this.k, cv = root.document.createElement('canvas');
    cv.width = Math.round(BASE_W * k); cv.height = Math.round(BASE_H * k);
    var c = cv.getContext('2d');
    c.setTransform(k, 0, 0, k, 0, 0);
    SCENE.drawBackground(c, this.th, BASE_W, BASE_H, GROUND);
    return (this._bg = cv);
  };

  P._burlapPattern = function (c) {
    if (this._burlap) return this._burlap;
    var th = this.th, cv = root.document.createElement('canvas');
    cv.width = cv.height = 24;
    var g = cv.getContext('2d');
    g.fillStyle = th.burlap; g.fillRect(0, 0, 24, 24);
    g.strokeStyle = th.burlap2; g.lineWidth = 1.4; g.globalAlpha = 0.55;
    for (var i = 0; i < 24; i += 4) { g.beginPath(); g.moveTo(i, 0); g.lineTo(i, 24); g.stroke(); g.beginPath(); g.moveTo(0, i + 2); g.lineTo(24, i + 2); g.stroke(); }
    g.globalAlpha = 0.18; g.fillStyle = '#000';
    var rnd = mulberry(3);
    for (var j = 0; j < 40; j++) g.fillRect(rnd() * 24, rnd() * 24, 1.5, 1.5);
    return (this._burlap = c.createPattern(cv, 'repeat'));
  };

  // ---- the revolver on its benchrest (origin = cylinder axis, barrel along +x)
  // The gun's frame: recoil kicks it back and up; the tremble is the suspense before the pull.
  P._gunXf = function (pose, t) {
    return { x: GUN.x - 14 * pose.recoil, y: GUN.y + 4 * pose.recoil,
      a: -0.32 * pose.recoil + (pose.tremble ? Math.sin(t / 30) * 0.004 * pose.tremble : 0) };
  };
  // a gun-space point (default: the muzzle, dx past it) in scene coordinates, recoil included
  P._muzzle = function (pose, t, dx) {
    var X = this._gunXf(pose, t), ca = Math.cos(X.a), sa = Math.sin(X.a);
    var x = REVOLVER.MUZZLE.x + (dx || 0), y = REVOLVER.MUZZLE.y;
    return { x: X.x + x * ca - y * sa, y: X.y + x * sa + y * ca };
  };
  P._drawGun = function (c, pose, t) {
    var th = this.th, X = this._gunXf(pose, t);
    c.save(); c.translate(GUN.x, GUN.y); SCENE.drawGunRest(c, th); c.restore();    // the rest stays put on the kick
    c.save(); c.translate(X.x, X.y); c.rotate(X.a);
    REVOLVER.draw(c, { open: pose.open, bullets: pose.bullets, loadIn: pose.loadIn, spin: pose.spin,
      spinSpeed: pose.spinSpeed, cock: pose.cock, trig: pose.trig, flash: pose.flash <= 0.15 ? pose.flash : -1 }, th, t);
    c.restore();
  };

  // ---- the dummy on its post
  P._drawDummy = function (c, pose, t) {
    var th = this.th, x = DUMMY.x, hy = DUMMY.hips;
    var fall = pose.fall * (1 - pose.revive);
    // the post
    c.save();
    c.fillStyle = 'rgba(0,0,0,.35)'; c.beginPath(); c.ellipse(x, GROUND + 4, 90, 10, 0, 0, TAU); c.fill();
    c.fillStyle = th.post2; c.fillRect(x - 60, GROUND - 14, 120, 16);
    c.fillStyle = th.post; c.fillRect(x - 9, hy - 262, 18, GROUND - (hy - 262) - 12);
    c.fillRect(x - 104, hy - 184, 208, 14);
    c.fillStyle = 'rgba(0,0,0,.25)'; c.fillRect(x + 4, hy - 262, 5, GROUND - (hy - 262) - 12);
    c.restore();
    // body transform
    var shake = pose.flinch ? Math.sin(t / 22) * 6 * pose.flinch : 0;
    shake += pose.tremble ? Math.sin(t / 17) * 1.6 * pose.tremble : 0;
    var sway = Math.sin(t / 900) * 0.02 * (1 - fall);
    var lean = -0.28 * fall + 0.28 * pose.jerk * (1 - fall) + sway;
    var drop = 118 * fall;
    c.save();
    c.translate(x + shake + 6 * fall, hy + drop);
    // legs
    var legA = -1.35 * fall, legB = -1.15 * fall;
    this._limb(c, -18, 0, -18 + Math.sin(-legA) * -118, Math.cos(legA) * 118, 17);
    this._limb(c, 18, 0, 18 + Math.sin(-legB) * -112, Math.cos(legB) * 112, 17);
    c.rotate(lean);
    // arms: out along the crossbar, hanging once it has fallen
    var aL = Math.PI - 1.35 * fall, aR = 1.2 * fall;
    this._limb(c, -44, -170, -44 + Math.cos(aL) * 70, -170 + Math.sin(aL) * 70, 15);
    this._limb(c, 44, -170, 44 + Math.cos(aR) * 70, -170 + Math.sin(aR) * 70, 15);
    // torso
    c.fillStyle = this._burlapPattern(c); c.strokeStyle = th.stitch; c.lineWidth = 2;
    c.beginPath(); c.moveTo(-52, -182); c.quadraticCurveTo(0, -196, 52, -182);
    c.quadraticCurveTo(58, -90, 42, -8); c.quadraticCurveTo(0, 12, -42, -8); c.quadraticCurveTo(-58, -90, -52, -182);
    c.closePath(); c.fill(); c.stroke();
    // seam + belly rope
    c.setLineDash([5, 5]); c.beginPath(); c.moveTo(0, -186); c.lineTo(0, 4); c.stroke(); c.setLineDash([]);
    c.strokeStyle = '#8a6a3a'; c.lineWidth = 5; c.beginPath(); c.moveTo(-46, -44); c.quadraticCurveTo(0, -36, 46, -44); c.stroke();
    // the hole + fluff
    if (pose.hole && pose.revive < 0.9) {
      var hr = 12 * (1 - pose.revive);
      c.fillStyle = '#1b120a'; c.beginPath(); c.arc(CHEST.x, CHEST.y, hr, 0, TAU); c.fill();
      c.fillStyle = '#f3ecdd';
      for (var i = 0; i < 6; i++) { var a = i * 1.05; c.beginPath(); c.arc(CHEST.x + Math.cos(a) * hr, CHEST.y + Math.sin(a) * hr, 4.5 * (1 - pose.revive), 0, TAU); c.fill(); }
    }
    // name tag
    this._tag(c, pose, t);
    // head
    c.save();
    c.translate(0, -186);
    c.rotate(-0.55 * fall + 0.35 * pose.jerk * (1 - fall) + (pose.flinch ? Math.sin(t / 30) * 0.08 * pose.flinch : 0));
    c.strokeStyle = '#8a6a3a'; c.lineWidth = 6; c.beginPath(); c.moveTo(-18, 0); c.lineTo(18, 0); c.stroke();
    c.fillStyle = this._burlapPattern(c); c.strokeStyle = th.stitch; c.lineWidth = 2;
    c.beginPath(); c.arc(0, -40, 40, 0, TAU); c.fill(); c.stroke();
    // face: stitched X eyes, zig-zag mouth
    c.lineWidth = 3; c.strokeStyle = th.stitch; c.lineCap = 'round';
    [[-15, -48], [15, -48]].forEach(function (e) {
      c.beginPath(); c.moveTo(e[0] - 6, e[1] - 6); c.lineTo(e[0] + 6, e[1] + 6); c.moveTo(e[0] + 6, e[1] - 6); c.lineTo(e[0] - 6, e[1] + 6); c.stroke();
    });
    c.lineWidth = 2.4; c.beginPath();
    var mo = fall > 0.3 ? 4 : (pose.tremble > 0.2 ? -2 : 0);
    c.moveTo(-16, -24 + mo);
    for (var z = 0; z < 8; z++) c.lineTo(-16 + (z + 1) * 4, -24 + mo + (z % 2 ? 0 : 4));
    c.stroke();
    // sweat when it's tense
    if (pose.tremble > 0.3 || pose.flinch > 0.2) {
      var sy = ((t / 700) % 1) * 30;
      c.fillStyle = 'rgba(150,210,255,.85)';
      c.beginPath(); c.moveTo(34, -64 + sy); c.quadraticCurveTo(40, -54 + sy, 34, -50 + sy); c.quadraticCurveTo(28, -54 + sy, 34, -64 + sy); c.fill();
    }
    c.restore();
    c.restore();
  };

  P._limb = function (c, x1, y1, x2, y2, w) {
    c.save();
    c.lineCap = 'round';
    c.strokeStyle = this.th.stitch; c.lineWidth = w + 4;
    c.beginPath(); c.moveTo(x1, y1); c.lineTo(x2, y2); c.stroke();
    c.strokeStyle = this._burlapPattern(c); c.lineWidth = w;
    c.beginPath(); c.moveTo(x1, y1); c.lineTo(x2, y2); c.stroke();
    c.fillStyle = '#8a6a3a'; c.beginPath(); c.arc(x2, y2, w * 0.45, 0, TAU); c.fill();
    c.restore();
  };

  P._tag = function (c, pose, t) {
    var st = this.st, g = st && st.game, name = (g && g.dummy && g.dummy.name) ||
      (st && st.idle && st.idle.dummy && st.idle.dummy.name) || this.cfg.dummy_name || 'Dummy';
    c.save();
    c.translate(-4, -104);
    c.rotate(-0.07 + (pose.flinch ? Math.sin(t / 40) * 0.05 * pose.flinch : 0));
    c.fillStyle = 'rgba(0,0,0,.3)'; roundRect(c, -52, -18, 108, 40, 5); c.fill();
    c.fillStyle = '#fbf7ee'; roundRect(c, -54, -21, 108, 40, 5); c.fill();
    c.strokeStyle = '#9b2b24'; c.lineWidth = 2; roundRect(c, -51, -18, 102, 34, 4); c.stroke();
    c.fillStyle = '#9b2b24'; c.font = '700 8px ' + this.th.font; c.textAlign = 'center'; c.fillText('HELLO MY NAME IS', 0, -8);
    c.fillStyle = '#1a1a1a';
    var lines = tagLines(c, name, this.th.font);
    c.font = '900 ' + lines.fs + 'px ' + this.th.font;
    if (lines.b) { c.fillText(lines.a, 0, 3); c.fillText(lines.b, 0, 3 + lines.fs); }
    else c.fillText(lines.a, 0, 10);
    c.fillStyle = '#c0c0c0'; c.beginPath(); c.arc(-44, -14, 3, 0, TAU); c.fill();
    c.restore();
  };

  // A name for the 96 px tag: one line down to 11 px, else two lines at >= 9 px (split at the space nearest
  // the middle), else the last character that fits gets an ellipsis. Split by code point, never mid-emoji.
  function tagLines(c, name, font) {
    function w(s, fs) { c.font = '900 ' + fs + 'px ' + font; return c.measureText(s).width; }
    var fs;
    for (fs = 17; fs >= 11; fs--) if (w(name, fs) <= 96) return { a: name, fs: fs };
    var ch = Array.from(name), mid = ch.length / 2, best = -1;
    for (var i = 1; i < ch.length; i++) if (ch[i] === ' ' && (best < 0 || Math.abs(i - mid) < Math.abs(best - mid))) best = i;
    var a = best > 0 ? ch.slice(0, best).join('') : ch.slice(0, Math.ceil(mid)).join('');
    var b = best > 0 ? ch.slice(best + 1).join('') : ch.slice(Math.ceil(mid)).join('');
    for (fs = 12; fs >= 9; fs--) if (w(a, fs) <= 96 && w(b, fs) <= 96) return { a: a, b: b, fs: fs };
    var cut = ch.slice();
    while (cut.length > 1 && w(cut.join('') + '…', 10) > 96) cut.pop();
    return { a: cut.join('') + '…', fs: 10 };
  }

  // ---- muzzle flash, tracer, smoke, stuffing, *click*
  P._drawFx = function (c, pose, t) {
    var th = this.th, M = REVOLVER.MUZZLE, g = this.st && this.st.game;
    var seed = g && g.pull ? g.pull.seed : 1;
    var mx = GUN.x + M.x, my = GUN.y + M.y;                     // the muzzle at rest (for the BANG! card)
    var chx = DUMMY.x + CHEST.x, chy = DUMMY.hips + CHEST.y;
    // muzzle flash + tracer ride the recoiling barrel
    if (pose.flash >= 0) {
      var X = this._gunXf(pose, t);
      c.save(); c.translate(X.x, X.y); c.rotate(X.a); c.translate(M.x + 8, M.y);
      SCENE.drawMuzzleFlash(c, pose.flash, th, seed);
      c.restore();
      if (pose.flash < 0.08) {
        var o = this._muzzle(pose, t, 20);
        c.save(); c.strokeStyle = 'rgba(255,240,190,' + (1 - pose.flash / 0.08) + ')'; c.lineWidth = 3;
        c.beginPath(); c.moveTo(o.x, o.y); c.lineTo(chx, chy); c.stroke(); c.restore();
      }
    }
    // smoke rolls off the muzzle (its source follows the barrel back down after the kick)
    if (pose.shot >= 0 && pose.shot < 3) {
      var sm = this._muzzle(pose, t, 10);
      c.save(); c.translate(sm.x, sm.y); SCENE.drawSmoke(c, pose.shot, th, seed); c.restore();
    }
    // stuffing: flies out, lands, stays; flies home on the revive
    if (pose.shot >= 0 && this.parts) {
      var s2 = Math.max(0, pose.shot - 0.03), rv = pose.revive;
      for (var q = 0; q < this.parts.length; q++) {
        var pt = this.parts[q];
        var tt = Math.min(s2, 1.6);
        var px = clamp(chx + pt.vx * tt * (1 - tt * 0.28), 470, BASE_W - 24), py = chy + pt.vy * tt + 620 * tt * tt;
        var floor = GROUND - pt.r * 0.6 - (q % 5);
        if (py > floor) py = floor;
        if (rv > 0) { px = lerp(px, chx, rv); py = lerp(py, chy, rv); }
        if (rv >= 0.98) continue;
        c.save(); c.translate(px, py); c.rotate(pt.rot + pt.spin * tt);
        c.fillStyle = pt.tint > 0.7 ? '#e9dfc9' : '#fbf8f0';
        c.beginPath(); c.arc(0, 0, pt.r, 0, TAU); c.arc(pt.r * 0.7, -pt.r * 0.3, pt.r * 0.7, 0, TAU); c.arc(-pt.r * 0.5, pt.r * 0.4, pt.r * 0.6, 0, TAU); c.fill();
        c.restore();
      }
    }
    // *click*
    if (pose.clickAgo >= 0 && pose.clickAgo < 1.4) {
      var ca = pose.clickAgo, al2 = 1 - clamp((ca - 0.8) / 0.6, 0, 1);
      c.save(); c.globalAlpha = al2; c.translate(GUN.x - 40, GUN.y - 70 - ca * 18); c.rotate(-0.12);
      c.font = '900 30px ' + th.font; c.textAlign = 'center';
      c.lineWidth = 6; c.strokeStyle = 'rgba(0,0,0,.8)'; c.strokeText('*click*', 0, 0);
      c.fillStyle = th.good; c.fillText('*click*', 0, 0);
      c.restore();
    }
    // BANG!
    if (pose.shot >= 0 && pose.shot < 1.6) {
      var b = pose.shot, sc = b < 0.12 ? 0.4 + b / 0.12 * 0.8 : 1.2 - Math.min(0.2, (b - 0.12)), al3 = 1 - clamp((b - 1.1) / 0.5, 0, 1);
      c.save(); c.globalAlpha = al3; c.translate(mx + 90, my - 90); c.rotate(-0.15); c.scale(sc, sc);
      c.fillStyle = '#ffd23f'; c.strokeStyle = '#000'; c.lineWidth = 4;
      c.beginPath();
      for (var n = 0; n < 18; n++) { var an = n / 18 * TAU, rr2 = n % 2 ? 46 : 78; c.lineTo(Math.cos(an) * rr2 * 1.3, Math.sin(an) * rr2 * 0.85); }
      c.closePath(); c.fill(); c.stroke();
      c.font = '900 44px ' + th.font; c.textAlign = 'center'; c.textBaseline = 'middle';
      c.lineWidth = 7; c.strokeText('BANG!', 0, 2); c.fillStyle = '#e8261c'; c.fillText('BANG!', 0, 2);
      c.restore();
    }
  };

  // ------------------------------------------------------------------ DOM
  P._set = function (key, el, html) {
    if (this.keys[key] === html) return;
    this.keys[key] = html;
    el.innerHTML = html;
  };
  P._show = function (el, on) { el.style.display = on ? '' : 'none'; };

  P._dom = function (g, left, t) {
    var st = this.st, cfg = this.cfg, idle = (st && st.idle) || {};
    var cur = (g && g.currency) || idle.currency || cfg.currency || 'coins';
    var ph = g ? g.phase : 'idle';
    var rounds = g ? g.rounds : (idle.rounds || cfg.rounds);
    var odds = g ? g.odds : (idle.odds || []);
    // subtitle + round chips
    var dname = g ? g.dummy.name : ((idle.dummy && idle.dummy.name) || cfg.dummy_name);
    this._set('sub', this.el.sub, esc(g ? 'Target: ' + dname : 'Next target: ' + dname));
    var chips = '';
    for (var k = 1; k <= rounds; k++) {
      var cls = '', lab = 'PULL ' + k;
      if (g) {
        var pl = null;
        if (g.pull && g.pull.round === k && (ph === 'result' || ph === 'over')) pl = g.pull;
        if (k < g.round || (pl && !pl.fired) || (ph === 'over' && g.outcome === 'survived' && k <= g.survived)) cls = 'ok';
        if (pl && pl.fired) cls = 'hit';
        if (k === g.round && (ph === 'betting' || ph === 'pulling')) cls = 'cur';
      }
      chips += '<div class="hrr-chip ' + cls + '"><b>' + lab + '</b>' + k + ' bullet' + (k > 1 ? 's' : '') + '</div>';
    }
    this._set('chips', this.el.chips, chips);
    // timer
    var tl = '', tv = '', last = false;
    if (ph === 'betting') { tl = g.round === 1 ? 'Bets close in' : 'Next pull in'; tv = Math.ceil(left / 1000) + 's'; last = left <= 10000; }
    else if (ph === 'pulling') { tl = 'Pull ' + g.round; tv = '…'; }
    else if (ph === 'result') { tl = g.pull && g.pull.fired ? 'Bang' : 'Click'; tv = ''; }
    else if (ph === 'over') { tl = 'Game over'; tv = ''; }
    else { tl = 'Waiting'; tv = ''; }
    this.el.tlabel.textContent = tl; this.el.tval.textContent = tv;
    this.el.timer.className = 'hrr-timer' + (last ? ' last' : '');
    if (last && ph === 'betting') {
      var sec = Math.ceil(left / 1000);
      if (sec <= 5 && sec >= 1 && this._beep !== g.id + '/' + g.round + '/' + sec) { this._beep = g.id + '/' + g.round + '/' + sec; this._snd('beep'); }
    }
    // odds ladder
    this._show(this.el.odds, !!cfg.show_odds);
    if (cfg.show_odds) {
      var rows = '<table><tr><th>Pull</th><th>Fire</th><th>Survive</th><th>Ride</th><th>Bang</th></tr>';
      for (var i = 0; i < odds.length; i++) {
        var o = odds[i], rc = g && o.round === g.round && ph !== 'over' ? 'cur' : (g && o.round < g.round ? 'done' : '');
        rows += '<tr class="' + rc + '"><td>' + o.round + '</td><td>' + Math.round(o.fire_pct) + '%</td><td>' + mult(o.survive) +
          '</td><td>' + mult(o.ride) + '</td><td>' + mult(o.bang) + '</td></tr>';
      }
      this._set('odds', this.el.oddsb, rows + '</table>');
    }
    // players
    var showP = !!cfg.show_players && !!g;
    this._show(this.el.players, showP);
    if (showP) {
      var ps = g.players || [], max = +cfg.players_max || 8, html = '';
      if (!ps.length) html = '<div class="hrr-empty">' + (ph === 'betting' ? 'No bets yet' : '—') + '</div>';
      for (var j = 0; j < Math.min(ps.length, max); j++) {
        var p = ps[j], bits = [];
        if (p.value) bits.push('<span class="sv">' + fmt(p.value) + (p.if_survives && ph === 'betting' ? ' <small>→ ' + fmt(p.if_survives) + '</small>' : '') + '</span>');
        if (p.bang) bits.push('<span class="bg">💥 ' + fmt(p.bang) + '</span>');
        html += '<div class="hrr-p"><u>' + esc(p.user) + '</u><i>' + bits.join(' ') + '</i></div>';
      }
      if (ps.length > max) html += '<div class="hrr-empty">+' + (ps.length - max) + ' more</div>';
      this._set('players', this.el.pb, html);
    }
    // rules box (bets open)
    var showR = !!cfg.show_rules && ph === 'betting';
    this._show(this.el.rules, showR);
    if (showR) {
      var od = odds[g.round - 1] || {}, nx = odds[g.rounds - 1] || od;
      var warn = left <= 10000 ? '<div class="warn">⏱ Bets close in ' + Math.ceil(left / 1000) + '!</div>' : '';
      var riders = (g.players || []).some(function (p) { return p.value && !p.fresh; });
      var rh = '<h4>' + (g.round === 1 ? 'Bets open · pull 1 of ' + g.rounds : 'Pull ' + g.round + ' · ' + g.round + ' bullets') + '</h4>' +
        '<p><em>SURVIVE</em> — it clicks: pays ' + mult(od.survive) + (g.rounds > g.round ? ', let it ride up to ' + mult(nx.ride) : '') + '</p>' +
        '<p><em>BANG</em> — it fires this pull: pays ' + mult(od.bang) + '</p>' +
        (riders || g.round > 1 ? '<p>Riding? Leave it in, add more, or cash out now.</p>' : '<p>Bets are against the bank · ' + esc(cur) + '</p>') +
        (cfg.commands_text || g.commands_text ? '<div class="cmd">' + esc(cfg.commands_text || g.commands_text) + '</div>' : '');
      this._set('rules', this.el.rules, rh + '<div class="hrr-w"></div>');
      var w = this.el.rules.querySelector('.hrr-w');
      if (w && w.innerHTML !== warn) w.innerHTML = warn;
    }
    // banner
    var ban = '', bcls = '';
    if (ph === 'pulling') {
      var el = this._elapsed(t) / 1000, D = +cfg.pull_seconds || 9;
      ban = el < 1.6 ? 'Loading bullet ' + g.round + '…' : (el < D - 2.4 ? 'Spinning the cylinder…' : 'Here goes…');
    } else if (ph === 'result' && g.last) {
      if (g.last.fired) { bcls = 'bang'; ban = 'BANG!' + '<small>' + esc(g.dummy.name) + ' is down</small>' + winnersLine(g.last, cur, 'bang'); }
      else { bcls = 'click'; ban = 'Survived pull ' + g.last.round + '!' + winnersLine(g.last, cur, 'survive'); }
    } else if (!g && st && st.visible) {
      ban = 'Next game soon<small>Place your bets when the timer starts</small>';
    }
    this._set('banner', this.el.banner, ban);
    this.el.banner.className = 'hrr-banner ' + bcls;
    this._show(this.el.banner, !!ban);
    // game over
    var showS = ph === 'over' && g.summary;
    this._show(this.el.sum, !!showS);
    if (showS) this._set('sum', this.el.sum, summaryHTML(g, cur));
    this._set('test', this.el.test, g && g.test ? 'TEST GAME · NO COINS' : '');
  };

  function winnersLine(last, cur, side) {
    var w = (last.winners || []).filter(function (x) { return x.side === side; });
    if (!w.length) return '<small>No winners this pull</small>';
    var top = w.slice(0, 3).map(function (x) { return esc(x.user) + ' ' + fmt(x.amount); }).join(' · ');
    return '<small>' + (side === 'bang' ? 'Paid: ' : 'Riding: ') + top + (w.length > 3 ? ' +' + (w.length - 3) : '') + '</small>';
  }

  function summaryHTML(g, cur) {
    var s = g.summary, cls = s.outcome === 'bang' ? 'bad' : (s.outcome === 'survived' || s.outcome === 'walked' ? 'good' : '');
    var title = s.outcome === 'bang' ? 'BANG! ' + esc(s.dummy.name) + ' is down' :
      s.outcome === 'survived' ? esc(s.dummy.name) + ' survived!' :
      s.outcome === 'walked' ? esc(s.dummy.name) + ' walks free' : esc(s.text);
    var sub = s.outcome === 'bang' ? 'Shot on pull ' + s.fired_on + ' of ' + s.rounds : s.pulls + ' of ' + s.rounds + ' pulls';
    var rows = (s.players || []).slice(0, 6).map(function (p) {
      return '<div class="row"><span>' + esc(p.user) + '</span><i class="' + (p.net > 0 ? 'pos' : p.net < 0 ? 'neg' : '') + '">' +
        (p.net > 0 ? '+' : '') + fmt(p.net) + '</i></div>';
    }).join('');
    if (!rows) rows = '<div class="sub">Nobody bet</div>';
    var cut = s.cut ? '<div class="cut">🎗 ' + esc(s.cut.user) + ' earns ' + fmt(s.cut.amount) + ' ' + esc(cur) + ' for volunteering</div>' : '';
    return '<h3 class="' + cls + '">' + title + '</h3><div class="sub">' + sub + '</div>' + rows +
      '<div class="tot"><span>Bet ' + fmt(s.total_bet) + '</span><span>Paid ' + fmt(s.total_paid) + '</span><span>House ' +
      (s.house_net >= 0 ? '+' : '') + fmt(s.house_net) + '</span></div>' + cut;
  }

  // ------------------------------------------------------------------ registration
  function copyObj(o) { var r = {}; for (var k in o) if (has(o, k)) r[k] = o[k]; return r; }
  var API = {
    BASE_W: BASE_W, BASE_H: BASE_H, THEMES: THEMES, DEFAULTS: copyObj(DEFAULTS), APPEARANCE: APPEARANCE.slice(),
    create: function (container, config, opts) { return new RR(container, config, opts); }
  };
  HG.russian = API;
  if (typeof module === 'object' && module && module.exports) module.exports = API;
})(typeof window !== 'undefined' ? window : (typeof globalThis !== 'undefined' ? globalThis : this));
