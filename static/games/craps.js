/*
 * Hexcast Games — Craps renderer                          static/games/craps.js
 *
 * Shared by the OBS overlay (/games/overlay) and the control panel (/games).
 * Plain browser script: no modules, no dependencies, no build step.
 *
 * Registers window.HexGames.craps:
 *
 *   BASE_W, BASE_H            tray size in stage px at scale 1 (720 x 405)
 *   THEMES                    { classic, neon, midnight, royal } — felt / rail / trim colours, .accent
 *   DICE_STYLES               { red, white, black, gold }
 *   DEFAULTS                  craps config defaults (same values as the backend)
 *   APPEARANCE                the keys a roll's `overrides` may change
 *   resultFor(d1, d2, phaseBefore, pointBefore) -> RESULT (same shape as the server's)
 *                             or null. Local fallback for previews: the server owns the
 *                             real call strings.
 *   create(container, config, opts) -> instance      opts: {sound: true, demo: false}
 *   motion                    the pure, DOM-free dice math (plan / dieState / faceUp /
 *                             GEOM ...) — used by the node tests
 *
 *   instance.setConfig(cfg)   instance.resize()          instance.play(spin) -> Promise
 *   instance.showResult(spin) instance.setHistory(list)  instance.setTable(table)
 *   instance.reset()          instance.destroy()         instance.onLanded = fn(spin)
 *   instance.setAnnounce(a)   Hex's own payouts card (STATE.announce, null = none): replaces
 *                             the computed payouts board, held until the dice land, cleared
 *                             by reset() and by the next play()/showResult()
 *   table.display_board       (setTable) when non-null, Hex's own bets board is shown instead
 *                             of the one computed from table.bets
 *
 * Container contract: the HOST sizes the container to BASE_W x BASE_H stage px and
 * positions/scales it (left/top %, translate(-50%,-50%) scale(s)). The renderer fills
 * it with a canvas (drawn GEOM.PAD px larger on every side so the tray's drop shadow
 * and dice in the air are never clipped) and its own DOM overlays (class prefix hgc-):
 * shooter caption + countdown and history strip above the tray, the stickman call
 * banner on the bottom rail, the bets board on the left and the payouts board on
 * the right (both flip sides when the tray sits near a stage edge, and shrink to the
 * room left beside a big tray so they stay on the stage). Below a placement scale of
 * 0.9 these shrink less than the tray (--hgc-ui) so they stay readable.
 * Hosts must not clip the container.
 *
 * Motion model — everything is a pure function of (plan, t), t = seconds since the
 * roll started, so any overlay can seek to elapsed_ms and every client draws the same
 * throw from the same seed. Per die:
 *   pickup   the dice leave the shooter's spot at the left end and rise into the hand
 *   shake    rattled in the hand above the left rail (fills long roll_seconds)
 *   flight   released from the left edge tumbling (torque-free: constant angular
 *            velocity between impacts), ballistic height arc, 1-2 felt bounces, hits
 *            the pyramid back wall on the right (it pops up off the rubber), 1-3
 *            damped hops back towards the middle
 *   roll     0-3 roll-overs about the leading bottom edge (fast at the slap, slow over
 *            the apex, speed lost at every slap)
 *   slide    a short slide + spin on its face, decelerating to a stop (kept short and
 *            not slowed down with the rest, so the faces only show for good in the
 *            last moments: <= 0.45 s before duration_ms, one die may stop a beat early)
 *   rest     from duration_ms on: the server's face up, never moves again.
 *   Exactness is built backwards from the rest pose: the slide, the roll-overs and the
 *   rebound line all end exactly on the resting face/position, the throw is solved so
 *   the die touches the wall exactly on that line, and the orientation difference
 *   between the free tumble and the required roll-start pose is spread over the whole
 *   flight in proportion to how fast the die is spinning (invisible) — so the landing
 *   is exact for every combination, seed and duration.
 */
(function (root) {
  'use strict';

  var HG = root.HexGames || (root.HexGames = {});

  var BASE_W = 720, BASE_H = 405;
  var TAU = Math.PI * 2;

  var DEFAULTS = {
    x: 50, y: 50, scale: 1.0, theme: 'classic', dice_style: 'red',
    roll_seconds: 4, result_seconds: 5, cooldown_seconds: 0,
    hide_when_idle: true, show_when_bets: true, show_point: true,
    show_history: true, history_count: 10, show_user: true,
    show_bets: true, bets_max: 6, show_payouts: true, payouts_max: 5,
    sfx: true, sfx_volume: 0.5, roll_clip: '', land_clip: '',
    currency: 'hexcoins', min_bet: 1, max_bet: 100000, odds_rule: '345',
    field_12_pays: 3, auto_roll: false, bet_window_seconds: 20, show_rules: true
  };
  var APPEARANCE = ['x', 'y', 'scale', 'theme', 'dice_style', 'show_point', 'show_history', 'history_count',
    'show_user', 'show_bets', 'bets_max', 'show_payouts', 'payouts_max', 'sfx', 'sfx_volume', 'show_rules'];

  // felt / rail / layout colours per theme
  var THEMES = {
    classic: {
      label: 'Classic', felt: '#0f7d44', feltHi: '#18a35a', feltLo: '#053a1e',
      leather: '#3b2416', leatherHi: '#8a5b37', leatherLo: '#120905', stitch: '#d8b27c',
      wood: '#5b321a', woodHi: '#a36a3c', woodLo: '#211008', grain: '#170902',
      trim: '#d4af37', trimHi: '#fff1b8', trimLo: '#6c500f',
      line: '#f4ecd2', num: '#f7e4a0', glow: '',
      rubber: '#26262b', rubberHi: '#8c8c96', rubberLo: '#050506',
      accent: '#d4af37'
    },
    neon: {
      label: 'Neon', felt: '#111118', feltHi: '#1d1d28', feltLo: '#040406',
      leather: '#17171d', leatherHi: '#474753', leatherLo: '#040405', stitch: '#ff3b30',
      wood: '#0d0d11', woodHi: '#2c2c34', woodLo: '#000000', grain: '#000000',
      trim: '#ff3b30', trimHi: '#ffc2bd', trimLo: '#7a0f0a',
      line: '#ff3b30', num: '#ffffff', glow: '#ff3b30',
      rubber: '#18181e', rubberHi: '#5c5c6a', rubberLo: '#000000',
      accent: '#ff3b30'
    },
    midnight: {
      label: 'Midnight', felt: '#173d78', feltHi: '#2458a3', feltLo: '#081733',
      leather: '#131c33', leatherHi: '#4a6293', leatherLo: '#04070f', stitch: '#aebfdc',
      wood: '#1e2f52', woodHi: '#5a74a6', woodLo: '#070d1c', grain: '#040914',
      trim: '#c8d1dc', trimHi: '#ffffff', trimLo: '#4b5667',
      line: '#e3eaf5', num: '#ffffff', glow: '',
      rubber: '#101829', rubberHi: '#6679a0', rubberLo: '#01030a',
      accent: '#b9c8dc'
    },
    royal: {
      label: 'Royal', felt: '#4d1d74', feltHi: '#6a2c9c', feltLo: '#200833',
      leather: '#2a0c35', leatherHi: '#77428f', leatherLo: '#0d0211', stitch: '#e0b84a',
      wood: '#3a1450', woodHi: '#8d4fb0', woodLo: '#12031b', grain: '#10021a',
      trim: '#e0b84a', trimHi: '#fff3c6', trimLo: '#77570f',
      line: '#e8c766', num: '#ffe7a3', glow: '',
      rubber: '#1d0f26', rubberHi: '#735a88', rubberLo: '#040107',
      accent: '#e0b84a'
    }
  };

  // dice materials
  var DICE_STYLES = {
    red: {
      label: 'Casino red', body: '#c3101d', hi: '#ff7a78', lo: '#3d0005',
      face: '#c8141f', faceHi: '#f2424a', faceLo: '#7a0610', edge: '#5a0209',
      pip: '#fbf8f1', pipLo: '#b4ad9f', pipRim: 'rgba(60,0,4,0.55)',
      translucent: true, gloss: 0.95, glint: '#ffe2de', shadow: [70, 0, 6], mini: '#d0141f', miniHi: '#ff6a6a', miniLo: '#5a0209', miniPip: '#ffffff'
    },
    white: {
      label: 'Ivory', body: '#eee7d6', hi: '#ffffff', lo: '#8c826c',
      face: '#f2ecde', faceHi: '#fffdf6', faceLo: '#d3c9b1', edge: '#b3a88f',
      pip: '#17130f', pipLo: '#000000', pipRim: 'rgba(0,0,0,0.25)',
      translucent: false, gloss: 0.6, glint: '#ffffff', shadow: [0, 0, 0], mini: '#f2ecde', miniHi: '#ffffff', miniLo: '#a79c83', miniPip: '#141414'
    },
    black: {
      label: 'Black', body: '#1c1c21', hi: '#8e8e9a', lo: '#000000',
      face: '#202027', faceHi: '#3b3b46', faceLo: '#0c0c10', edge: '#050507',
      pip: '#f6f5f1', pipLo: '#9d9a93', pipRim: 'rgba(0,0,0,0.6)',
      translucent: false, gloss: 0.85, glint: '#dde3f2', shadow: [0, 0, 0], mini: '#24242b', miniHi: '#5a5a66', miniLo: '#000000', miniPip: '#ffffff'
    },
    gold: {
      label: 'Gold', body: '#cfa23a', hi: '#fff3bf', lo: '#553b08',
      face: '#d6aa42', faceHi: '#ffe697', faceLo: '#94700f', edge: '#6b4d0b',
      pip: '#231706', pipLo: '#000000', pipRim: 'rgba(255,240,190,0.35)',
      translucent: false, gloss: 1, metal: true, glint: '#fff6d2', shadow: [30, 18, 0], mini: '#d6aa42', miniHi: '#fff0b0', miniLo: '#6b4d0b', miniPip: '#231706'
    }
  };

  // ======================================================================
  // Small helpers (pure)
  // ======================================================================
  function clamp(v, a, b) { return v < a ? a : (v > b ? b : v); }
  function smooth01(x) { return x <= 0 ? 0 : (x >= 1 ? 1 : x * x * (3 - 2 * x)); }
  function hashStr(s) {
    var h = 2166136261;
    for (var i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); }
    return h >>> 0;
  }
  // mulberry32: tiny deterministic PRNG -> [0, 1)
  function mulberry32(a) {
    return function () {
      a |= 0; a = (a + 0x6D2B79F5) | 0;
      var t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  // ======================================================================
  // RESULT (same shape as the server's) — local fallback for previews
  // ======================================================================
  var WORDS = ['', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten', 'eleven', 'twelve'];
  var PROP_CALLS = { 2: 'ACES', 3: 'ACE-DEUCE', 11: 'YO-LEVEN', 12: 'BOXCARS' };
  function cap1(s) { return s ? s.charAt(0).toUpperCase() + s.slice(1) : s; }
  function isDie(v) { return typeof v === 'number' && v >= 1 && v <= 6 && (v | 0) === v; }

  function resultFor(d1, d2, phaseBefore, pointBefore) {
    d1 = +d1; d2 = +d2;
    if (!isDie(d1) || !isDie(d2)) return null;
    var total = d1 + d2, pair = d1 === d2, hard = pair && (total === 4 || total === 6 || total === 8 || total === 10);
    var pt = +pointBefore;
    var phase = phaseBefore === 'point' && (pt === 4 || pt === 5 || pt === 6 || pt === 8 || pt === 9 || pt === 10) ? 'point' : 'come_out';
    if (phase !== 'point') pt = null;
    // same calls as the server's _stickman() (games.py): the server owns these strings,
    // this is only the fallback for local previews
    var ev, call, sub, phA = phase, ptA = pt, w = WORDS[total], prop = PROP_CALLS[total];
    if (phase === 'come_out') {
      if (total === 7 || total === 11) {
        ev = 'natural'; call = total === 7 ? 'SEVEN \u00B7 WINNER' : 'YO-LEVEN'; sub = 'Front line winner';
      } else if (prop) {
        ev = 'craps'; call = prop; sub = total === 12 ? 'Craps \u00B7 bar the 12' : 'Craps \u00B7 line away';
      } else {
        ev = 'point_set'; phA = 'point'; ptA = total;
        call = 'POINT IS ' + total;
        sub = cap1((total === 5 || total === 9 ? '' : (pair ? 'hard ' : 'easy ')) + w + ' \u00B7 mark it');
      }
    } else if (total === pt) {
      ev = 'point_made'; phA = 'come_out'; ptA = null; call = 'WINNER ' + total; sub = 'Pay the line';
    } else if (total === 7) {
      ev = 'seven_out'; phA = 'come_out'; ptA = null; call = 'SEVEN OUT'; sub = "Line away \u00B7 don't pass wins";
    } else {
      ev = 'roll';
      call = prop || (hard || total === 4 || total === 6 || total === 8 || total === 10 ? (pair ? 'HARD ' : 'EASY ') + w.toUpperCase() : w.toUpperCase());
      sub = 'Point is ' + pt;
    }
    return {
      dice: [d1, d2], total: total, pair: pair, hard: hard,
      number: String(total), label: d1 + '-' + d2,
      event: ev, call: call, sub: sub,
      phase_before: phase, point_before: pt, phase_after: phA, point_after: ptA
    };
  }

  // [d1, d2] from a RESULT (or null)
  function diceOf(res) {
    if (!res || typeof res !== 'object') return null;
    var a, b, d = res.dice;
    if (Array.isArray(d) && d.length >= 2) { a = +d[0]; b = +d[1]; }
    else if (typeof res.label === 'string' && /^\s*[1-6]\s*-\s*[1-6]\s*$/.test(res.label)) {
      var p = res.label.split('-'); a = +p[0]; b = +p[1];
    } else return null;
    return isDie(a) && isDie(b) ? [a, b] : null;
  }

  // ======================================================================
  // Dice math — pure functions of (plan, t). No DOM in this section.
  //
  // Coordinates: stage px of the 720x405 tray, x right, y down; z = height of a
  // die's centre above the felt. A die resting on a face has z = HALF.
  // Orientation: unit quaternion [w, x, y, z], body -> world. Body axes carry the
  // faces +z:1 -z:6 +x:2 -x:5 +y:3 -y:4 (opposite faces add up to 7).
  // ======================================================================
  var GEOM = {
    W: BASE_W, H: BASE_H,
    PAD: 40,                   // canvas overhang on every side (shadow, dice in the air)
    RAIL: 22,                  // padded rail width; felt = [RAIL, W-RAIL] x [RAIL, H-RAIL]
    CORNER: 30,                // outer corner radius
    WALL_X: 668,               // inner face of the pyramid back wall (right end)
    PT_Y0: 28, PT_Y1: 100,     // point row band
    PT_X0: 118, PT_X1: 660,    // point boxes 4 5 SIX 8 NINE 10
    OFF_X: 70, OFF_Y: 64,      // where the OFF puck is parked
    PUCK_R: 18,
    E: 46, RND: 5.5,           // die edge / edge rounding radius
    REST: [280, 560, 146, 326],// x0 x1 y0 y1 of resting die centres
    BAND: [128, 346],          // y range of die centres while they touch the felt
    IDLE: [[74, 206], [74, 272]],   // the dice waiting at the shooter's end
    HAND_X: 44,
    TILT: 0.24,                // camera tilt (sin): height shifts a die up the screen
    ZC: 460                    // camera distance for the height perspective
  };
  var HALF = GEOM.E / 2;                                 // 23
  var AIN = HALF - GEOM.RND;                             // inner cube half size
  var FOOT_R = AIN * Math.SQRT2 + GEOM.RND;              // circumradius of a resting die's footprint
  var LSTEP = 2 * AIN + GEOM.RND * Math.PI / 2;          // centre travel per roll-over
  var MIN_HOP = 0.075;                                   // nominal s: shorter hops turn into a roll
  var R2 = Math.SQRT1_2;
  var UPQ = [null, [1, 0, 0, 0], [R2, 0, -R2, 0], [R2, R2, 0, 0], [R2, -R2, 0, 0], [R2, 0, R2, 0], [0, 1, 0, 0]];
  var IDLE_Q = [[0, 0, 0, 0], [0, 0, 0, 0]];
  // FACES: value, body normal n, in-face axes u / w (u x w = n) — texture x / y
  var FACES = [
    { v: 1, n: [0, 0, 1], u: [1, 0, 0], w: [0, 1, 0] },
    { v: 6, n: [0, 0, -1], u: [0, 1, 0], w: [1, 0, 0] },
    { v: 2, n: [1, 0, 0], u: [0, 1, 0], w: [0, 0, 1] },
    { v: 5, n: [-1, 0, 0], u: [0, 0, 1], w: [0, 1, 0] },
    { v: 3, n: [0, 1, 0], u: [0, 0, 1], w: [1, 0, 0] },
    { v: 4, n: [0, -1, 0], u: [1, 0, 0], w: [0, 0, 1] }
  ];

  // --- quaternions (write into `o`; no allocation) --------------------------
  function qset(o, w, x, y, z) { o[0] = w; o[1] = x; o[2] = y; o[3] = z; return o; }
  function qcopy(a, o) { o[0] = a[0]; o[1] = a[1]; o[2] = a[2]; o[3] = a[3]; return o; }
  function qmul(a, b, o) {       // o = a * b (b first, then a) — o may alias a or b
    var aw = a[0], ax = a[1], ay = a[2], az = a[3], bw = b[0], bx = b[1], by = b[2], bz = b[3];
    o[0] = aw * bw - ax * bx - ay * by - az * bz;
    o[1] = aw * bx + ax * bw + ay * bz - az * by;
    o[2] = aw * by - ax * bz + ay * bw + az * bx;
    o[3] = aw * bz + ax * by - ay * bx + az * bw;
    return o;
  }
  function qconj(a, o) { o[0] = a[0]; o[1] = -a[1]; o[2] = -a[2]; o[3] = -a[3]; return o; }
  function qaxis(ax, ay, az, ang, o) { var h = ang / 2, s = Math.sin(h); return qset(o, Math.cos(h), ax * s, ay * s, az * s); }
  function qnorm(o) {
    var n = Math.sqrt(o[0] * o[0] + o[1] * o[1] + o[2] * o[2] + o[3] * o[3]) || 1;
    o[0] /= n; o[1] /= n; o[2] /= n; o[3] /= n; return o;
  }
  var QT1 = [1, 0, 0, 0], QT2 = [1, 0, 0, 0];
  // o = exp(w * dt) * q0   (world-frame angular velocity w, rad/s)
  function qspin(wx, wy, wz, dt, q0, o) {
    var m = Math.sqrt(wx * wx + wy * wy + wz * wz);
    if (m < 1e-12) return qcopy(q0, o);
    qaxis(wx / m, wy / m, wz / m, m * dt, QT1);
    return qmul(QT1, q0, o);
  }
  // rotation angle (rad, 0..pi) between two orientations
  function qangle(a, b) {
    var d = Math.abs(a[0] * b[0] + a[1] * b[1] + a[2] * b[2] + a[3] * b[3]);
    return 2 * Math.acos(d > 1 ? 1 : d);
  }
  // body -> world rotation matrix, row major
  function qmat(q, m) {
    var w = q[0], x = q[1], y = q[2], z = q[3];
    m[0] = 1 - 2 * (y * y + z * z); m[1] = 2 * (x * y - w * z); m[2] = 2 * (x * z + w * y);
    m[3] = 2 * (x * y + w * z); m[4] = 1 - 2 * (x * x + z * z); m[5] = 2 * (y * z - w * x);
    m[6] = 2 * (x * z - w * y); m[7] = 2 * (y * z + w * x); m[8] = 1 - 2 * (x * x + y * y);
    return m;
  }
  // value of the face pointing up (world +z)
  function faceUp(q) {
    var w = q[0], x = q[1], y = q[2], z = q[3];
    var zx = 2 * (x * z - w * y), zy = 2 * (y * z + w * x), zz = 1 - 2 * (x * x + y * y);
    var ax = Math.abs(zx), ay = Math.abs(zy), az = Math.abs(zz);
    if (az >= ax && az >= ay) return zz > 0 ? 1 : 6;
    if (ax >= ay) return zx > 0 ? 2 : 5;
    return zy > 0 ? 3 : 4;
  }
  // half extents (x, y) of the rounded cube's footprint for orientation q
  function extents(q, o) {
    var w = q[0], x = q[1], y = q[2], z = q[3];
    var m0 = 1 - 2 * (y * y + z * z), m1 = 2 * (x * y - w * z), m2 = 2 * (x * z + w * y);
    var m3 = 2 * (x * y + w * z), m4 = 1 - 2 * (x * x + z * z), m5 = 2 * (y * z - w * x);
    o = o || {};
    o.x = AIN * (Math.abs(m0) + Math.abs(m1) + Math.abs(m2)) + GEOM.RND;
    o.y = AIN * (Math.abs(m3) + Math.abs(m4) + Math.abs(m5)) + GEOM.RND;
    return o;
  }
  function randUnit(rnd) {
    var z = 2 * rnd() - 1, a = TAU * rnd(), s = Math.sqrt(Math.max(0, 1 - z * z));
    return [s * Math.cos(a), s * Math.sin(a), z];
  }
  function unit3(v) { var n = Math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2]) || 1; return [v[0] / n, v[1] / n, v[2] / n]; }

  // resting pose of the idle dice at the shooter's end (face 6 and 5 up, a little skewed)
  qmul(qaxis(0, 0, 1, 0.21, [1, 0, 0, 0]), UPQ[6], IDLE_Q[0]);
  qmul(qaxis(0, 0, 1, -0.33, [1, 0, 0, 0]), UPQ[5], IDLE_Q[1]);

  function spinSeed(spin) {
    var s = spin.seed;
    if (s != null && s !== '' && isFinite(+s)) return (+s) >>> 0;
    return hashStr(String(spin.id || 'hexcast'));
  }

  // --- per-die random parameters (fixed number of draws) ---------------------
  function drawParams(rnd) {
    var p = {}, i, r;
    p.theta = Math.PI + (rnd() * 2 - 1) * 0.8;       // rebound direction (leftwards +-46 deg)
    r = rnd(); p.k = r < 0.1 ? 0 : (r < 0.42 ? 1 : (r < 0.84 ? 2 : 3));   // roll-overs
    p.sd = rnd(); p.st = 0.13 + 0.09 * rnd();          // final slide on the resting face, s
    p.psi = (rnd() < 0.5 ? -1 : 1) * (0.2 + 0.5 * rnd());
    p.d0 = 0.12 + 0.05 * rnd();
    p.gr = [1.15 + 0.25 * rnd(), 1.15 + 0.25 * rnd(), 1.15 + 0.25 * rnd()];
    p.b = [0.2 + 0.2 * rnd(), 0.35 + 0.2 * rnd(), 0.5 + 0.25 * rnd()];
    p.g = 1500 * (0.88 + 0.24 * rnd());               // nominal gravity, px/s^2
    p.dt0 = 0.46 + 0.14 * rnd();                      // release -> first felt contact
    p.ev = 0.42 + 0.12 * rnd();                       // vertical restitution on the felt
    p.kick = 100 + 140 * rnd();                       // upward pop off the pyramids, px/s
    p.mu = []; for (i = 0; i < 8; i++) p.mu.push(0.72 + 0.16 * rnd());   // horizontal speed kept per felt contact
    p.ew = 0.38 + 0.22 * rnd();                       // preferred wall restitution
    p.twf = rnd();                                    // tie-break for the wall timing
    p.m0 = 13 + 8 * rnd();                            // release spin, rad/s
    p.r0 = randUnit(rnd);
    p.rv = []; p.rm = [];
    for (i = 0; i < 10; i++) { p.rv.push(randUnit(rnd)); p.rm.push(rnd()); }
    p.yaw = rnd() * TAU;
    p.lastw = randUnit(rnd);
    return p;
  }

  // Vertical free flight of one die in nominal time (s since release), with the wall
  // hit at `tw` (Infinity = no wall: probe the first contacts). Returns
  // {pieces:[{t0,t1,z0,vz,f,post,start}], contacts:[t], impacts:[v], tr, Spre, Spost}
  // or null. Spre / Spost = integral of the horizontal speed factor before / after
  // the wall (speed is multiplied by mu at every felt contact).
  function simulate(p, hA, tw, probe) {
    var g = p.g, t = 0, z = hA, vz = (-hA + g * p.dt0 * p.dt0 / 2) / p.dt0;
    var f = 1, Spre = 0, Spost = 0, wall = false, nC = 0, mi = 0;
    var pieces = [], contacts = [], impacts = [], start = 'release';
    for (var guard = 0; guard < 24; guard++) {
      var tc = t + (vz + Math.sqrt(Math.max(0, vz * vz + 2 * g * z))) / g;
      if (!wall && tw <= tc) {
        Spre += f * (tw - t);
        pieces.push({ t0: t, t1: tw, z0: z, vz: vz, f: f, post: false, start: start });
        var dt = tw - t;
        z = z + vz * dt - g * dt * dt / 2; vz = vz - g * dt + p.kick; t = tw;
        wall = true; f = 1; start = 'wall';
        continue;
      }
      pieces.push({ t0: t, t1: tc, z0: z, vz: vz, f: f, post: wall, start: start });
      if (wall) Spost += f * (tc - t); else Spre += f * (tc - t);
      var vimp = vz - g * (tc - t);
      t = tc; nC++;
      contacts.push(t); impacts.push(-vimp);
      f *= p.mu[Math.min(mi++, p.mu.length - 1)];
      vz = p.ev * (-vimp); z = 0; start = 'felt';
      if (probe && nC >= 3) return { contacts: contacts };
      if (!wall && nC >= 3) return null;               // the wall must come within two bounces
      if (wall && (2 * vz / g < MIN_HOP || nC >= 8)) {
        return { pieces: pieces, contacts: contacts, impacts: impacts, tr: t, Spre: Spre, Spost: Spost };
      }
    }
    return null;
  }

  // Rebound line of one die: from the wall (x = Wx) back to its resting point, with k
  // roll-overs and a slide of Ds at the end, travelling in direction theta.
  function reboundLine(rest, k, theta, sd, Wx) {
    var dx = Math.cos(theta), dy = Math.sin(theta), Ds = k ? 5 + 9 * sd : 14 + 18 * sd;
    var Ps = { x: rest.x - Ds * dx, y: rest.y - Ds * dy };
    var Pr = { x: Ps.x - k * LSTEP * dx, y: Ps.y - k * LSTEP * dy };
    var tau = (Pr.x - Wx) / dx;
    return { dx: dx, dy: dy, Ds: Ds, Ps: Ps, Pr: Pr, tau: tau, W: { x: Wx, y: Pr.y - tau * dy } };
  }
  function lineOk(r, minTau) {
    return r.tau >= minTau && r.W.y >= GEOM.BAND[0] && r.W.y <= GEOM.BAND[1];
  }

  // Nominal geometry + timeline of one die. rest: {x,y}; A: release point; zA: release
  // height of the centre; rho: half width of the die along x when it touches the wall.
  // fix: {k, theta, twi} chosen on the first pass (kept while rho is refined, so the
  // refinement is continuous); null: choose.
  function geomDie(p, rest, A, zA, rho, fix, relaxed) {
    var Wx = GEOM.WALL_X - rho, sel = fix, r, i, kk;
    if (!sel) {
      var d = p.theta - Math.PI, ths = [p.theta, Math.PI + 0.6 * d, Math.PI - 0.5 * d, Math.PI + 0.25 * d, Math.PI];
      for (kk = p.k; kk >= 0 && !sel; kk--) {
        for (i = 0; i < ths.length; i++) {
          r = reboundLine(rest, kk, ths[i], p.sd, Wx);
          if (lineOk(r, 80)) { sel = { k: kk, theta: ths[i], twi: null }; break; }
        }
      }
      if (!sel) {
        if (!relaxed) return null;
        sel = { k: 0, theta: Math.PI, twi: null };
      }
    }
    var k = sel.k;
    r = reboundLine(rest, k, sel.theta, p.sd, Wx);
    if (!lineOk(r, relaxed ? 12 : 50)) return null;
    var dx = r.dx, dy = r.dy, Ds = r.Ds, Ps = r.Ps, Pr = r.Pr, W = r.W, tau = r.tau;
    var ex = W.x - A.x, ey = W.y - A.y, Dpre = Math.sqrt(ex * ex + ey * ey);
    var hA = zA - HALF;
    var pr = simulate(p, hA, Infinity, true);
    if (!pr || pr.contacts.length < 3) return null;
    var lo = pr.contacts[0] + 0.04, hi = pr.contacts[2] - 0.02, N = 24;
    if (!(hi > lo)) return null;
    var best = -1, bestErr = Infinity, sim = null;
    if (sel.twi != null) best = sel.twi;
    else {
      for (i = 0; i <= N; i++) {
        var s = simulate(p, hA, lo + (hi - lo) * i / N, false);
        if (!s) continue;
        var ew = (tau / Dpre) * s.Spre / s.Spost, v0 = Dpre / s.Spre;
        var bad = relaxed ? (ew > 0 ? 0 : 1e9) :
          Math.max(0, 0.25 - ew, ew - 0.8) * 10 + Math.max(0, 330 - v0, v0 - 1400) / 100;
        var err = bad * 100 + Math.abs(ew - p.ew) + 0.02 * Math.abs(i / N - p.twf);
        if (err < bestErr) { bestErr = err; best = i; }
      }
      if (best < 0 || (!relaxed && bestErr >= 100)) return null;
    }
    var tw = lo + (hi - lo) * best / N;
    sim = simulate(p, hA, tw, false);
    if (!sim) return null;
    // roll-overs + slide (nominal)
    var steps = [], d = p.d0, Tr = 0;
    for (i = 0; i < k; i++) { steps.push({ d: d, b: p.b[i] }); Tr += d; d *= p.gr[i]; }
    var st = k ? p.st : p.st * 1.25;
    return {
      k: k, dir: { x: dx, y: dy }, Ds: Ds, Ps: Ps, Pr: Pr, W: W, A: { x: A.x, y: A.y }, zA: zA,
      Dpre: Dpre, Dpost: tau, pre: { x: ex / Dpre, y: ey / Dpre },
      tw: tw, fix: { k: k, theta: sel.theta, twi: best }, sim: sim, steps: steps, Tr: Tr, st: st, Tn: sim.tr + Tr + st
    };
  }

  // Rattle-in-the-hand orientation chain, 0 .. tRel (own PRNG: independent of retries)
  function buildChain(D, q0, tPick, tRel, rnd) {
    var tb = [0], qs = [q0.slice()], ws = [], t = 0, q = q0.slice();
    while (t < tRel || ws.length === 0) {
      var pd = 0.075 + 0.06 * rnd();
      var u = randUnit(rnd), m = 7 + 9 * rnd();
      var ramp = t < tPick ? 0.3 + 0.7 * (t / Math.max(tPick, 1e-6)) : 1;
      var w = [u[0] * m * ramp, u[1] * m * ramp, u[2] * m * ramp];
      ws.push(w);
      var q1 = [1, 0, 0, 0];
      qspin(w[0], w[1], w[2], pd, q, q1); qnorm(q1);
      t += pd; tb.push(t); qs.push(q1); q = q1;
      if (tb.length > 4000) break;
    }
    D.chain = { tb: tb, q: qs, w: ws };
  }
  function chainQ(D, t, o) {
    var c = D.chain, tb = c.tb;
    if (t <= 0) return qcopy(c.q[0], o);
    var lo = 0, hi = tb.length - 2;
    if (t >= tb[hi]) lo = hi;
    else {
      while (lo < hi) { var mid = (lo + hi + 1) >> 1; if (tb[mid] <= t) lo = mid; else hi = mid - 1; }
    }
    var w = c.w[lo];
    return qspin(w[0], w[1], w[2], t - tb[lo], c.q[lo], o);
  }

  // Hand position (die centre) during pickup / shake. Writes o.x, o.y, o.z.
  function handAt(P, D, t, o) {
    var H = P.hand, tp = P.tPick, tr = P.tRel;
    var bx = H.x + D.ox, by = H.y + D.oy, bz = H.z + D.oz;
    if (t < tp) {                                   // lifting the dice off the felt
      var u = tp > 0 ? t / tp : 1, e = smooth01(u), ez = 1 - (1 - u) * (1 - u);
      o.x = D.idle[0] + (bx - D.idle[0]) * e;
      o.y = D.idle[1] + (by - D.idle[1]) * e;
      o.z = HALF + (bz - HALF) * ez;
      return o;
    }
    var span = tr - tp;
    var env = smooth01((t - tp) / 0.18) * (1 - smooth01((t - (tr - 0.3)) / 0.3));
    var sx = env * (3.4 * Math.sin(TAU * H.f1 * t + H.p1) + 5 * Math.sin(TAU * 0.55 * t + H.p2));
    var sy = env * (7 * Math.sin(TAU * 0.42 * t + H.p3) + 1.6 * Math.sin(TAU * H.f1 * 1.31 * t + H.p2));
    var sz = env * 8 * Math.sin(TAU * H.f1 * t + H.p1 + 1.2);
    var jx = env * 1.7 * Math.sin(TAU * D.jf * t + D.jp), jy = env * 1.7 * Math.cos(TAU * D.jf * 1.17 * t + D.jp);
    var wu = span > 0.5 && t > tr - 0.26 ? Math.sin(Math.PI * (t - (tr - 0.26)) / 0.26) : 0;   // wind-up
    o.x = bx + sx + jx - 13 * wu;
    o.y = by + sy + jy;
    o.z = bz + sz + 12 * wu;
    return o;
  }

  var HS = { x: 0, y: 0, z: 0 };
  // Pose of die i at time t (s). Writes o.x, o.y (felt position of the centre),
  // o.z (height of the centre), o.q (quaternion), o.phase:
  // 0 pickup, 1 shake, 2 flight, 3 roll, 4 slide, 5 rest.
  function dieState(P, i, t, o) {
    var D = P.dies[i], q = o.q || (o.q = [1, 0, 0, 0]);
    if (!(t > 0)) t = 0;
    if (t >= D.tEnd) {
      o.x = D.rest.x; o.y = D.rest.y; o.z = HALF; qcopy(D.qFin, q); o.phase = 5; return o;
    }
    var u, j;
    if (t >= D.tSlide) {
      u = (t - D.tSlide) / D.slideT; var iu = 1 - u, s = D.Ds * (1 - iu * iu);
      o.x = D.Ps.x + D.dir.x * s; o.y = D.Ps.y + D.dir.y * s; o.z = HALF;
      qaxis(0, 0, 1, -D.psi * iu * iu, QT2); qmul(QT2, D.qFin, q);
      o.phase = 4; return o;
    }
    if (t >= D.tRoll) {
      j = 0; while (j < D.k - 1 && t >= D.steps[j + 1].t0) j++;
      var sp = D.steps[j], tau = (t - sp.t0) / sp.d; if (tau > 1) tau = 1;
      var phi = (Math.PI / 2) * (tau + sp.b * Math.sin(TAU * tau) / TAU);
      var dist = j * LSTEP + AIN * (Math.sin(phi) - Math.cos(phi)) + GEOM.RND * phi + AIN;
      o.x = D.Pr.x + D.dir.x * dist; o.y = D.Pr.y + D.dir.y * dist;
      o.z = GEOM.RND + AIN * (Math.sin(phi) + Math.cos(phi));
      qaxis(D.rax[0], D.rax[1], D.rax[2], j * Math.PI / 2 + phi - D.thTot, QT2); qmul(QT2, D.qS, q);
      o.phase = 3; return o;
    }
    if (t >= P.tRel) {
      var segs = D.segs; j = 0;
      while (j < segs.length - 1 && t >= segs[j + 1].t0) j++;
      var sg = segs[j], dt = t - sg.t0;
      o.x = sg.x0 + sg.vx * dt; o.y = sg.y0 + sg.vy * dt;
      var zr = sg.z0 + sg.vz * dt - sg.g * dt * dt / 2;
      o.z = HALF + (zr > 0 ? zr : 0);
      qspin(sg.w[0], sg.w[1], sg.w[2], dt, sg.q0, q);
      // orientation correction, spread over the flight in proportion to the spin
      u = (t - P.tRel) / D.span;
      var G = u - u * u * u / 3, F = sg.F0 + sg.wm * D.span * (G - sg.G0);
      qaxis(D.cax[0], D.cax[1], D.cax[2], D.cang * F / D.Ftot, QT2);
      qmul(QT2, q, q);
      o.phase = 2; return o;
    }
    handAt(P, D, t, HS);
    o.x = HS.x; o.y = HS.y; o.z = HS.z;
    chainQ(D, t, q);
    o.phase = t < P.tPick ? 0 : 1;
    return o;
  }
  function diceState(P, t, out) {
    out = out || [{ q: [1, 0, 0, 0] }, { q: [1, 0, 0, 0] }];
    dieState(P, 0, t, out[0]); dieState(P, 1, t, out[1]);
    return out;
  }

  // Scale die i's nominal timeline into absolute time and build its orientation plan.
  function finishDie(P, D, g, p, lam, rnd) {
    var tRel = P.tRel, i, sim = g.sim;
    D.k = g.k; D.dir = g.dir; D.Ds = g.Ds; D.Ps = g.Ps; D.Pr = g.Pr; D.W = g.W;
    D.lam = lam;
    // flight segments
    var segs = [], x = g.A.x, y = g.A.y, v0 = g.Dpre / sim.Spre, v1 = g.Dpost / sim.Spost;
    var fwdPre = [-g.pre.y, g.pre.x, 0], fwdPost = [-g.dir.y, g.dir.x, 0];
    var w = unit3([fwdPre[0] + 0.6 * p.r0[0], fwdPre[1] + 0.6 * p.r0[1], 0.6 * p.r0[2]]);
    w = [w[0] * p.m0, w[1] * p.m0, w[2] * p.m0];
    var ri = 0;
    for (i = 0; i < sim.pieces.length; i++) {
      var pc = sim.pieces[i], dir = pc.post ? g.dir : g.pre, v = (pc.post ? v1 : v0) * pc.f;
      if (i > 0) {
        var rv = p.rv[ri % 10], rm = p.rm[ri % 10]; ri++;
        if (pc.start === 'wall') {
          w = [rv[0] * (5 + 5 * rm) + fwdPost[0] * (2.5 + 3 * rm), rv[1] * (5 + 5 * rm) + fwdPost[1] * (2.5 + 3 * rm), rv[2] * (5 + 5 * rm)];
        } else {
          var fw = pc.post ? fwdPost : fwdPre;
          w = [0.5 * w[0] + fw[0] * (3.5 + 3 * rm) + rv[0] * (1.2 + 1.8 * rm),
            0.5 * w[1] + fw[1] * (3.5 + 3 * rm) + rv[1] * (1.2 + 1.8 * rm),
            0.5 * w[2] + rv[2] * (1.2 + 1.8 * rm)];
        }
      }
      segs.push({
        t0: tRel + lam * pc.t0, t1: tRel + lam * pc.t1, x0: x, y0: y,
        vx: dir.x * v / lam, vy: dir.y * v / lam, z0: pc.z0, vz: pc.vz / lam, g: p.g / (lam * lam),
        w: [w[0] / lam, w[1] / lam, w[2] / lam], q0: null, F0: 0, G0: 0, wm: 0, start: pc.start, post: pc.post
      });
      x += dir.x * v * (pc.t1 - pc.t0); y += dir.y * v * (pc.t1 - pc.t0);
    }
    D.tRoll = tRel + lam * sim.tr;
    D.tWall = tRel + lam * g.tw;
    D.span = D.tRoll - tRel;
    // roll-overs + slide
    var t = D.tRoll;
    D.steps = [];
    for (i = 0; i < g.k; i++) { D.steps.push({ t0: t, d: g.steps[i].d * lam, b: g.steps[i].b }); t += g.steps[i].d * lam; }
    D.tSlide = t; D.slideT = D.tEnd - t;     // tEnd is exact (T, or T - lag): no float drift
    D.psi = g.k ? p.psi : p.psi * 1.4;
    D.rax = [-g.dir.y, g.dir.x, 0];
    D.thTot = g.k * Math.PI / 2;
    D.qFin = qnorm(qmul(qaxis(0, 0, 1, p.yaw, [1, 0, 0, 0]), UPQ[D.v], [1, 0, 0, 0]));
    D.qS = qnorm(qmul(qaxis(0, 0, 1, -D.psi, [1, 0, 0, 0]), D.qFin, [1, 0, 0, 0]));
    var qR = qnorm(qmul(qaxis(D.rax[0], D.rax[1], D.rax[2], -D.thTot, [1, 0, 0, 0]), D.qS, [1, 0, 0, 0]));
    // last flight segment spins into the roll (or the slide) smoothly
    var last = segs[segs.length - 1];
    if (g.k) {
      var th0 = (Math.PI / 2) * (1 + g.steps[0].b) / (g.steps[0].d * lam) * 1.1;
      last.w = [D.rax[0] * th0 + 0.4 * p.lastw[0] / lam, D.rax[1] * th0 + 0.4 * p.lastw[1] / lam, 0.4 * p.lastw[2] / lam];
    } else {
      var yawRate = 2 * D.psi / D.slideT;
      last.w = [1.2 * p.lastw[0] / lam, 1.2 * p.lastw[1] / lam, yawRate];
    }
    // chain the free tumble
    buildChain(D, IDLE_Q[D.i], P.tPick, P.tRel, rnd);
    var q = chainQ(D, tRel, [1, 0, 0, 0]);
    var Ft = 0;
    for (i = 0; i < segs.length; i++) {
      var sgi = segs[i];
      sgi.q0 = q.slice();
      sgi.wm = Math.sqrt(sgi.w[0] * sgi.w[0] + sgi.w[1] * sgi.w[1] + sgi.w[2] * sgi.w[2]);
      var u0 = (sgi.t0 - tRel) / D.span, u1 = (sgi.t1 - tRel) / D.span;
      sgi.G0 = u0 - u0 * u0 * u0 / 3;
      sgi.F0 = Ft;
      Ft += sgi.wm * D.span * ((u1 - u1 * u1 * u1 / 3) - sgi.G0);
      var q1 = [1, 0, 0, 0];
      qspin(sgi.w[0], sgi.w[1], sgi.w[2], sgi.t1 - sgi.t0, q, q1); qnorm(q1);
      q = q1;
    }
    D.segs = segs;
    D.Ftot = Ft > 1e-9 ? Ft : 1;
    // C = qR * conj(qNat(tRoll)), shortest way round
    var C = qmul(qR, qconj(q, [1, 0, 0, 0]), [1, 0, 0, 0]);
    if (C[0] < 0) { C[0] = -C[0]; C[1] = -C[1]; C[2] = -C[2]; C[3] = -C[3]; }
    var ang = 2 * Math.acos(Math.min(1, C[0])), sn = Math.sqrt(C[1] * C[1] + C[2] * C[2] + C[3] * C[3]);
    D.cang = sn > 1e-9 ? ang : 0;
    D.cax = sn > 1e-9 ? [C[1] / sn, C[2] / sn, C[3] / sn] : [0, 0, 1];
    D.qR = qR;
  }

  function pickRest(rnd, relaxed) {
    var R = GEOM.REST;
    for (var n = 0; n < 40; n++) {
      var a = { x: R[0] + (R[1] - R[0]) * rnd(), y: R[2] + (R[3] - R[2]) * rnd() };
      var b = { x: R[0] + (R[1] - R[0]) * rnd(), y: R[2] + (R[3] - R[2]) * rnd() };
      var d = Math.sqrt((a.x - b.x) * (a.x - b.x) + (a.y - b.y) * (a.y - b.y));
      if (d < 1.62 * GEOM.E || d > 4.4 * GEOM.E) continue;
      return a.y <= b.y ? [a, b] : [b, a];
    }
    return relaxed ? [{ x: 430, y: 186 }, { x: 492, y: 282 }] : null;
  }

  var SA = { q: [1, 0, 0, 0] }, SB = { q: [1, 0, 0, 0] }, EXT = { x: 0, y: 0 };

  // squared distance between segments p1-p2 and q1-q2
  function segDist2(p1, p2, q1, q2) {
    function pd(p, a, b) {
      var vx = b.x - a.x, vy = b.y - a.y, l = vx * vx + vy * vy, t = l > 0 ? ((p.x - a.x) * vx + (p.y - a.y) * vy) / l : 0;
      t = clamp(t, 0, 1); var dx = a.x + vx * t - p.x, dy = a.y + vy * t - p.y; return dx * dx + dy * dy;
    }
    var d1x = p2.x - p1.x, d1y = p2.y - p1.y, d2x = q2.x - q1.x, d2y = q2.y - q1.y;
    var den = d1x * d2y - d1y * d2x;
    if (Math.abs(den) > 1e-9) {
      var ta = ((q1.x - p1.x) * d2y - (q1.y - p1.y) * d2x) / den, tb = ((q1.x - p1.x) * d1y - (q1.y - p1.y) * d1x) / den;
      if (ta >= 0 && ta <= 1 && tb >= 0 && tb <= 1) return 0;
    }
    return Math.min(pd(p1, q1, q2), pd(p2, q1, q2), pd(q1, p1, p2), pd(q2, p1, p2));
  }
  // Choose roll-overs + rebound angle for both dice together: die 0 (upper) keeps to the
  // upper lane — wall hits apart, rebound lines apart — so they don't run into each other.
  function pickLanes(prm, rest, Wx) {
    var cand = [[], []], i, j, kk, a, b;
    for (i = 0; i < 2; i++) {
      var p = prm[i], d = p.theta - Math.PI;
      var ths = [p.theta, Math.PI + 0.6 * d, Math.PI - 0.5 * d, Math.PI + 0.25 * d, Math.PI, Math.PI - d, Math.PI + 0.45, Math.PI - 0.45];
      for (kk = p.k; kk >= 0; kk--) {
        for (j = 0; j < ths.length; j++) {
          var r = reboundLine(rest[i], kk, ths[j], p.sd, Wx);
          if (lineOk(r, 80)) cand[i].push({ k: kk, theta: ths[j], twi: null, r: r });
        }
      }
    }
    var need = (1.55 * GEOM.E) * (1.55 * GEOM.E);
    for (var n = 0; n < cand[0].length + cand[1].length; n++) {     // prefer early candidates of both
      for (a = 0; a <= n && a < cand[0].length; a++) {
        b = n - a; if (b >= cand[1].length) continue;
        var A = cand[0][a].r, B = cand[1][b].r;
        if (B.W.y - A.W.y < 1.7 * GEOM.E) continue;
        if (segDist2(A.W, rest[0], B.W, rest[1]) < need) continue;
        return [cand[0][a], cand[1][b]];
      }
    }
    return [null, null];
  }

  function tryPlan(dice, T, seed, att, relaxed) {
    var rnd = mulberry32((seed ^ Math.imul(att + 1, 0x9E3779B1) ^ 0x5DEECE66) >>> 0);
    var P = { T: T, seed: seed, dice: dice.slice(), attempt: att, relaxed: relaxed };
    P.tPick = Math.min(0.22, 0.09 * T);
    var H = P.hand = {
      x: GEOM.HAND_X, y: 206 + 64 * rnd(), z: HALF + 64 + 20 * rnd(),
      f1: 2.8 + 0.9 * rnd(), p1: TAU * rnd(), p2: TAU * rnd(), p3: TAU * rnd()
    };
    var rest = pickRest(rnd, relaxed);
    if (!rest) return null;
    var first = rnd() < 0.5 ? 0 : 1, lag = Math.min(0.03 + 0.09 * rnd(), 0.05 * T);
    var ends = [T, T]; ends[first] = T - lag;
    var prm = [drawParams(rnd), drawParams(rnd)];
    P.dies = [];
    for (var i = 0; i < 2; i++) {
      P.dies.push({
        i: i, v: dice[i], idle: GEOM.IDLE[i], rest: rest[i], tEnd: ends[i],
        ox: (rnd() - 0.5) * 8, oy: (i ? 1 : -1) * 0.8 * GEOM.E, oz: (i ? 6 : -6) + (rnd() - 0.5) * 6,
        jf: 7 + 5 * rnd(), jp: TAU * rnd()
      });
    }
    var rho = [0.62 * GEOM.E, 0.62 * GEOM.E], G = [null, null];
    var fix = pickLanes(prm, rest, GEOM.WALL_X - rho[0]);
    var lamT = clamp(1.0 + 0.045 * T, 1.08, 1.45);
    for (var iter = 0; iter < 6; iter++) {
      for (i = 0; i < 2; i++) {
        var D = P.dies[i];
        var A = { x: H.x + D.ox, y: H.y + D.oy };
        G[i] = geomDie(prm[i], D.rest, A, H.z + D.oz, rho[i], fix[i], relaxed);
        if (!G[i]) return null;
        fix[i] = G[i].fix;
      }
      // shared release time; each die's timeline scales to end exactly at its end time
      // the final slide keeps (about) its natural length: a slow-motion slide would show
      // the result early. Only the throw itself stretches to fill the time.
      var sl = [G[0].st * Math.min(lamT, 1.1), G[1].st * Math.min(lamT, 1.1)];
      var tRel = Math.max(P.tPick, Math.min(ends[0] - lamT * (G[0].Tn - G[0].st) - sl[0],
        ends[1] - lamT * (G[1].Tn - G[1].st) - sl[1]));
      P.tRel = tRel;
      var lam = [(ends[0] - tRel - sl[0]) / (G[0].Tn - G[0].st), (ends[1] - tRel - sl[1]) / (G[1].Tn - G[1].st)];
      if (!relaxed && (lam[0] < 0.7 || lam[1] < 0.7 || lam[0] > 1.65 || lam[1] > 1.65)) return null;
      if (!(lam[0] > 0.05 && lam[1] > 0.05)) return null;
      var moved = 0;
      for (i = 0; i < 2; i++) {
        var crnd = mulberry32((seed ^ Math.imul(i + 7, 0x85EBCA6B) ^ Math.imul(att + 3, 0xC2B2AE35)) >>> 0);
        finishDie(P, P.dies[i], G[i], prm[i], lam[i], crnd);
        // the die must just touch the wall: measure its real reach around the hit
        var Dd = P.dies[i], mx = -Infinity;
        for (var tt = Dd.tWall - 0.12; tt <= Dd.tWall + 0.12; tt += 0.004) {
          if (tt < P.tRel || tt >= Dd.tRoll) continue;
          dieState(P, i, tt, SA); extents(SA.q, EXT);
          if (SA.x + EXT.x > mx) mx = SA.x + EXT.x;
        }
        var err = mx - GEOM.WALL_X;
        if (Math.abs(err) > 0.4) { rho[i] = clamp(rho[i] + err, 0.5 * GEOM.E, 0.9 * GEOM.E); moved++; }
      }
      if (!moved) break;
    }
    // the two dice must not run into each other while both are low on the felt, and
    // a die touching the felt must stay on it (a tumbling die near a corner of the
    // band could otherwise clip the rail)
    if (!relaxed) {
      var x0 = GEOM.RAIL + 0.5, x1 = GEOM.WALL_X + 1, y0 = GEOM.RAIL + 2.5, y1 = GEOM.H - GEOM.RAIL - 0.5;
      for (var t = P.tRel; t <= T; t += 0.004) {
        dieState(P, 0, t, SA); dieState(P, 1, t, SB);
        var ddx = SA.x - SB.x, ddy = SA.y - SB.y, dz = SA.z - SB.z;
        if (Math.abs(dz) < 0.9 * GEOM.E && ddx * ddx + ddy * ddy < (1.5 * GEOM.E) * (1.5 * GEOM.E)) return null;
        for (i = 0; i < 2; i++) {
          var S = i ? SB : SA;
          if (S.z > HALF + 3) continue;
          extents(S.q, EXT);
          if (S.x - EXT.x < x0 || S.x + EXT.x > x1 || S.y - EXT.y < y0 || S.y + EXT.y > y1) return null;
        }
      }
    }
    buildEvents(P);
    return P;
  }

  function buildEvents(P) {
    var ev = [], i, j, rnd = mulberry32((P.seed ^ 0x6A09E667) >>> 0);
    if (P.tPick > 0.05) ev.push({ t: 0.03, kind: 'tick', vol: 0.3, rate: 1.1 });
    var c = P.dies[0].chain;
    for (j = 1; j < c.tb.length; j++) {
      var tb = c.tb[j];
      if (tb < P.tPick + 0.05 || tb > P.tRel - 0.05) continue;
      if (rnd() < 0.75) ev.push({ t: tb, kind: 'rattle', vol: 0.12 + 0.16 * rnd(), rate: 0.85 + 0.5 * rnd() });
    }
    for (i = 0; i < 2; i++) {
      var D = P.dies[i], impRef = 0;
      for (j = 0; j < D.segs.length; j++) {
        var s = D.segs[j];
        if (s.start === 'felt') {
          // bounce speed is proportional to the impact: loudest on the first landing
          if (!impRef) impRef = Math.max(1, s.vz);
          ev.push({ t: s.t0, kind: 'felt', vol: clamp(0.25 + 0.75 * s.vz / impRef, 0.15, 1), rate: 0.9 + 0.2 * rnd() });
        } else if (s.start === 'wall') {
          var sp = Math.sqrt(s.vx * s.vx + s.vy * s.vy);
          ev.push({ t: s.t0, kind: 'wall', vol: clamp(sp / 260, 0.35, 1), rate: 0.92 + 0.16 * rnd() });
        }
      }
      ev.push({ t: D.tRoll, kind: 'felt', vol: 0.3, rate: 1.05 });
      for (j = 0; j < D.steps.length; j++) {
        ev.push({ t: D.steps[j].t0 + D.steps[j].d, kind: 'clack', vol: 0.55 * Math.pow(0.72, j), rate: 0.95 + 0.15 * rnd() });
      }
      ev.push({ t: D.tEnd, kind: 'settle', vol: 0.28, rate: 1 });
    }
    ev.sort(function (a, b) { return a.t - b.t; });
    P.events = ev;
    var imp = [0, P.tPick, P.tRel];
    for (i = 0; i < 2; i++) {
      var d = P.dies[i];
      for (j = 0; j < d.segs.length; j++) imp.push(d.segs[j].t0);
      imp.push(d.tRoll);
      for (j = 0; j < d.steps.length; j++) imp.push(d.steps[j].t0);
      imp.push(d.tSlide, d.tEnd);
    }
    P.impacts = imp.sort(function (a, b) { return a - b; });
  }

  // Build the full, deterministic plan of a roll. Returns null if the spin has no
  // usable dice. Never depends on elapsed_ms (seeking == playing).
  function planRoll(spin) {
    spin = spin || {};
    var dice = diceOf(spin.result);
    if (!dice) return null;
    // the server clamps roll_seconds to 2.5..10 s; stay finite/sane for any caller
    var dms = +spin.duration_ms;
    var T = dms > 0 && isFinite(dms) ? dms / 1000 : 4;
    T = clamp(T, 1.2, 120);
    var seed = spinSeed(spin), P = null;
    for (var att = 0; att < 64 && !P; att++) P = tryPlan(dice, T, seed, att, false);
    if (!P) P = tryPlan(dice, T, seed, 0, true);
    return P;
  }

  var motion = {
    GEOM: GEOM, HALF: HALF, AIN: AIN, FOOT_R: FOOT_R, LSTEP: LSTEP, FACES: FACES,
    IDLE_Q: IDLE_Q, UPQ: UPQ,
    plan: planRoll, dieState: dieState, diceState: diceState,
    faceUp: faceUp, extents: extents, qmat: qmat, qangle: qangle, qmul: qmul, qconj: qconj,
    diceOf: diceOf, mulberry32: mulberry32
  };

  // ======================================================================
  // Config helpers (pure)
  // ======================================================================
  function toBool(v, dflt) {
    if (v === true || v === false) return v;
    if (v == null || v === '') return dflt;
    var s = String(v).toLowerCase();
    if (s === '1' || s === 'true' || s === 'yes' || s === 'on') return true;
    if (s === '0' || s === 'false' || s === 'no' || s === 'off') return false;
    return dflt;
  }
  function toNum(v, dflt, lo, hi) { var n = +v; if (v === '' || v == null || !isFinite(n)) n = dflt; return clamp(n, lo, hi); }
  function has(o, k) { return Object.prototype.hasOwnProperty.call(o, k); }
  function sanitize(o) {
    if (!has(THEMES, o.theme)) o.theme = 'classic';
    if (!has(DICE_STYLES, o.dice_style)) o.dice_style = 'red';
    o.x = toNum(o.x, 50, 0, 100); o.y = toNum(o.y, 50, 0, 100);
    o.scale = toNum(o.scale, 1.0, 0.2, 5);
    o.sfx_volume = toNum(o.sfx_volume, 0.5, 0, 1);
    o.history_count = Math.round(toNum(o.history_count, 10, 1, 20));
    o.bets_max = Math.round(toNum(o.bets_max, 6, 1, 20));
    o.payouts_max = Math.round(toNum(o.payouts_max, 5, 1, 20));
    o.roll_seconds = toNum(o.roll_seconds, 4, 2.5, 10);
    o.result_seconds = toNum(o.result_seconds, 5, 1, 120);
    o.cooldown_seconds = toNum(o.cooldown_seconds, 0, 0, 3600);
    o.bet_window_seconds = toNum(o.bet_window_seconds, 20, 5, 300);
    var cur = String(o.currency == null ? '' : o.currency).trim().slice(0, 24);
    o.currency = cur || 'hexcoins';
    var bk = ['hide_when_idle', 'show_when_bets', 'show_point', 'show_history', 'show_user', 'show_bets',
      'show_payouts', 'sfx', 'auto_roll', 'show_rules'];
    for (var i = 0; i < bk.length; i++) o[bk[i]] = toBool(o[bk[i]], DEFAULTS[bk[i]]);
    return o;
  }
  function normConfig(c) {
    c = (c && typeof c === 'object') ? c : {};
    var src = (c.craps && typeof c.craps === 'object') ? c.craps : c, o = {}, k;
    for (k in DEFAULTS) o[k] = DEFAULTS[k];
    for (k in src) if (has(src, k) && src[k] != null) o[k] = src[k];
    return sanitize(o);
  }
  function effConfig(cfg, ov) {
    var o = {}, k;
    for (k in cfg) o[k] = cfg[k];
    if (ov && typeof ov === 'object') {
      for (var i = 0; i < APPEARANCE.length; i++) { k = APPEARANCE[i]; if (ov[k] != null) o[k] = ov[k]; }
    }
    return sanitize(o);
  }
  function fmtNum(n) {
    n = Math.round((+n || 0) * 100) / 100;
    var neg = n < 0; n = Math.abs(n);
    var parts = String(n).split('.');
    parts[0] = parts[0].replace(/\B(?=(\d{3})+(?!\d))/g, ',');
    return (neg ? '-' : '') + parts.join('.');
  }
  function userName(u) { return u == null ? '' : String(u).replace(/^@+/, '').trim(); }

  // Hex's own display data ("Hex does the math"): STATE.announce (payouts board) and
  // TABLE.display_board (bets board), cleaned again here by the server's rules so any
  // caller (overlay, panel mirror) gets the same boards. Lines are {user, text, amount};
  // a line needs a user or a text, an unusable amount drops the line. Everything is
  // rendered as text (textContent), never as HTML.
  var ANN_MAX_AMOUNT = 1e12, ANN_GRACE_MS = 2000;
  // at most `max` characters counted like the server does (code points, so an emoji is
  // never cut in half), control characters removed, trimmed
  function annStr(v, max) {
    if (v == null || typeof v === 'object' || typeof v === 'boolean') return '';
    var s = String(v).replace(/[\u0000-\u001f\u007f]/g, '').trim(), out = '', n = 0, i = 0;
    while (i < s.length && n < max) {
      var c = s.charCodeAt(i), w = c >= 0xD800 && c <= 0xDBFF && i + 1 < s.length &&
        s.charCodeAt(i + 1) >= 0xDC00 && s.charCodeAt(i + 1) <= 0xDFFF ? 2 : 1;
      out += s.substr(i, w); i += w; n++;
    }
    return out.trim();
  }
  // null = no amount; undefined = unusable (not a finite number, |amount| > 1e12, or
  // negative on the bets board)
  function annAmount(v, signed) {
    if (v == null || (typeof v === 'string' && !v.trim())) return null;
    var n = typeof v === 'number' ? v : (typeof v === 'string' ? +v : NaN);
    return isFinite(n) && Math.abs(n) <= ANN_MAX_AMOUNT && (signed || n >= 0) ? n : undefined;
  }
  function annLines(src, max, signed) {
    var lines = [];
    src = Array.isArray(src) ? src : [];
    for (var i = 0; i < src.length && lines.length < max; i++) {
      var l = src[i];
      if (!l || typeof l !== 'object') continue;
      var u = annStr(userName(annStr(l.user, 1000)), 40), t = annStr(l.text, 60), m = annAmount(l.amount, signed);
      if ((!u && !t) || m === undefined) continue;
      lines.push({ user: u, text: t, amount: m });
    }
    return lines;
  }
  // -> null (no card, or already expired) or {title, lines, empty_text, currency (null =
  // the config's), ttl (expires_in_ms as handed over, null = none), sig}
  function normAnnounce(a) {
    if (!a || typeof a !== 'object' || Array.isArray(a)) return null;
    var ttl = typeof a.expires_in_ms === 'number' && !isNaN(a.expires_in_ms) ? a.expires_in_ms : null;
    if (ttl !== null && ttl <= 0) return null;
    var o = {
      id: annStr(a.id, 64), spin_id: annStr(a.spin_id, 64),
      title: annStr(a.title, 40) || 'WINNERS', lines: annLines(a.lines, 50, true),
      empty_text: annStr(a.empty_text, 60) || 'No winners',
      currency: a.currency == null ? null : annStr(a.currency, 24),
      ttl: ttl
    };
    o.sig = JSON.stringify([o.id, o.spin_id, o.title, o.lines, o.empty_text, o.currency]);
    return o;
  }
  // -> null (the computed board) or {title, lines, total, showTotal}
  function normBoard(db) {
    if (!db || typeof db !== 'object' || Array.isArray(db)) return null;
    var lines = annLines(db.bets, 100, false), sum = 0, amt = false;
    for (var i = 0; i < lines.length; i++) if (lines[i].amount != null) { sum += lines[i].amount; amt = true; }
    var total = typeof db.total === 'number' && isFinite(db.total) ? db.total : sum;
    return { title: annStr(db.title, 40) || 'ON THE TABLE', lines: lines, total: total, showTotal: amt || total !== 0 };
  }
  // signed amount: "+200" / "\u221250" (a real minus sign) / "0", then the currency when there is one
  function annAmountText(n, cur) {
    var r = Math.round(n * 100) / 100;
    var s = r > 0 ? '+' + fmtNum(r) : (r < 0 ? '\u2212' + fmtNum(-r) : '0');
    return cur ? s + ' ' + cur : s;
  }
  function annAmountClass(n) {
    var r = Math.round(n * 100) / 100;
    return r > 0 ? 'hgc-win' : (r < 0 ? 'hgc-loss' : 'hgc-zero');
  }

  // short bet labels for the boards
  var SHORT = {
    pass: 'Pass', dont_pass: 'Don\u2019t Pass', come: 'Come', dont_come: 'Don\u2019t Come', place: 'Place',
    hard: 'Hard', field: 'Field', any7: 'Any 7', any_craps: 'Any Craps', aces: 'Aces', ace_deuce: 'Ace-Deuce',
    yo: 'Yo', boxcars: 'Boxcars', horn: 'Horn', ce: 'C&E'
  };
  function betShort(b) {
    var base = (has(SHORT, b.type) && SHORT[b.type]) || b.label || b.type || 'Bet';
    if (b.number != null && b.number !== '' && (b.type === 'place' || b.type === 'hard' || b.type === 'come' || b.type === 'dont_come')) base += ' ' + b.number;
    return base;
  }

  // Colour helpers
  function hexRgb(hex) {
    var h = String(hex || '').replace('#', '');
    if (h.length === 3) h = h[0] + h[0] + h[1] + h[1] + h[2] + h[2];
    var n = parseInt(h, 16);
    if (h.length !== 6 || isNaN(n)) return [0, 0, 0];
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  }
  function mixRgb(a, b, t) {
    return [Math.round(a[0] + (b[0] - a[0]) * t), Math.round(a[1] + (b[1] - a[1]) * t), Math.round(a[2] + (b[2] - a[2]) * t)];
  }
  function shadeRgb(hex, amt) { var c = hexRgb(hex); return amt >= 0 ? mixRgb(c, [255, 255, 255], amt) : mixRgb(c, [0, 0, 0], -amt); }
  function css(c) { return 'rgb(' + c[0] + ',' + c[1] + ',' + c[2] + ')'; }
  function rgba(c, a) { return 'rgba(' + c[0] + ',' + c[1] + ',' + c[2] + ',' + a + ')'; }
  function shade(hex, amt) { return css(shadeRgb(hex, amt)); }

  // ======================================================================
  // Canvas art — static layers are pre-rendered once per size/theme/dice style.
  // (Everything below touches the DOM; only called from instances.)
  // ======================================================================
  var MAX_W = 2600;
  var SERIF = 'Georgia, "Times New Roman", "DejaVu Serif", serif';
  var FONT = '"Segoe UI", "Helvetica Neue", Arial, sans-serif';
  var PT_NUMS = [4, 5, 6, 8, 9, 10];
  var PT_LABELS = ['4', '5', 'SIX', '8', 'NINE', '10'];
  var L3 = unit3([-0.42, -0.55, 0.72]);                  // towards the light (top-left, above)
  var VIEW = [0, GEOM.TILT, Math.sqrt(1 - GEOM.TILT * GEOM.TILT)];   // towards the camera
  var HV = unit3([L3[0] + VIEW[0], L3[1] + VIEW[1], L3[2] + VIEW[2]]);

  function mk(w, h) {
    var c = document.createElement('canvas');
    c.width = Math.max(1, Math.ceil(w)); c.height = Math.max(1, Math.ceil(h == null ? w : h));
    return c;
  }
  function roundRectPath(g, x, y, w, h, r) {
    r = Math.min(r, w / 2, h / 2);
    g.beginPath(); g.moveTo(x + r, y); g.lineTo(x + w - r, y); g.arcTo(x + w, y, x + w, y + r, r);
    g.lineTo(x + w, y + h - r); g.arcTo(x + w, y + h, x + w - r, y + h, r); g.lineTo(x + r, y + h);
    g.arcTo(x, y + h, x, y + h - r, r); g.lineTo(x, y + r); g.arcTo(x, y, x + r, y, r); g.closePath();
  }
  function rrect(g, x, y, w, h, r) {   // sub-path (no beginPath)
    r = Math.min(r, w / 2, h / 2);
    g.moveTo(x + r, y); g.lineTo(x + w - r, y); g.arcTo(x + w, y, x + w, y + r, r);
    g.lineTo(x + w, y + h - r); g.arcTo(x + w, y + h, x + w - r, y + h, r); g.lineTo(x + r, y + h);
    g.arcTo(x, y + h, x, y + h - r, r); g.lineTo(x, y + r); g.arcTo(x, y, x + r, y, r); g.closePath();
  }
  function ptBox(i) {
    var bw = (GEOM.PT_X1 - GEOM.PT_X0) / 6;
    return { x: GEOM.PT_X0 + bw * i, y: GEOM.PT_Y0, w: bw, h: GEOM.PT_Y1 - GEOM.PT_Y0 };
  }
  function puckSpot(point) {
    var i = PT_NUMS.indexOf(+point);
    if (i < 0) return { x: GEOM.OFF_X, y: GEOM.OFF_Y, on: false, i: -1 };
    var b = ptBox(i);
    return { x: b.x + b.w / 2, y: b.y + 24, on: true, i: i };
  }
  function glowText(g, th, fn) {   // neon: draw twice with a glow
    if (th.glow) { g.save(); g.shadowColor = th.glow; g.shadowBlur = 10; fn(); g.restore(); }
    fn();
  }

  // --- the tray: rail, felt, layout print, pyramid back wall -----------------
  function buildTray(k, th) {
    var W = GEOM.W, H = GEOM.H, PAD = GEOM.PAD, RL = GEOM.RAIL, RO = GEOM.CORNER;
    var cv = mk((W + 2 * PAD) * k, (H + 2 * PAD) * k), g = cv.getContext('2d');
    var rnd = mulberry32(hashStr('tray-' + th.label));
    var i, j, x, y;
    g.setTransform(k, 0, 0, k, PAD * k, PAD * k);

    // drop shadow + contact shadow
    g.save();
    g.fillStyle = th.woodLo;
    g.shadowColor = 'rgba(0,0,0,0.55)'; g.shadowBlur = 30 * k; g.shadowOffsetY = 14 * k;
    roundRectPath(g, 2, 2, W - 4, H - 4, RO); g.fill();
    g.shadowColor = 'rgba(0,0,0,0.7)'; g.shadowBlur = 6 * k; g.shadowOffsetY = 3 * k; g.fill();
    g.restore();
    if (th.glow) {
      g.save(); g.shadowColor = th.glow; g.shadowBlur = 22 * k; g.strokeStyle = th.glow; g.lineWidth = 2.2;
      roundRectPath(g, 1, 1, W - 2, H - 2, RO); g.stroke(); g.stroke(); g.restore();
    }

    // wooden outer frame
    g.save(); roundRectPath(g, 0, 0, W, H, RO); g.clip();
    var wg = g.createLinearGradient(0, 0, 0, H);
    wg.addColorStop(0, shade(th.wood, 0.12)); wg.addColorStop(0.5, th.wood); wg.addColorStop(1, shade(th.wood, -0.25));
    g.fillStyle = wg; g.fillRect(0, 0, W, H);
    var grain = hexRgb(th.grain), gHi = hexRgb(th.woodHi);
    for (i = 0; i < 220; i++) {
      var dark = rnd() < 0.7, side = Math.floor(rnd() * 4), off = rnd() * 9, len = 40 + rnd() * 220, st0 = rnd();
      g.strokeStyle = dark ? rgba(grain, (0.12 + 0.22 * rnd()).toFixed(3)) : rgba(gHi, (0.05 + 0.1 * rnd()).toFixed(3));
      g.lineWidth = 0.4 + rnd() * 1.1;
      g.beginPath();
      if (side < 2) { y = side ? H - off : off; x = st0 * W; g.moveTo(x, y); g.bezierCurveTo(x + len / 3, y + (rnd() - 0.5) * 2, x + 2 * len / 3, y + (rnd() - 0.5) * 2, x + len, y); }
      else { x = side === 2 ? off : W - off; y = st0 * H; g.moveTo(x, y); g.bezierCurveTo(x + (rnd() - 0.5) * 2, y + len / 3, x + (rnd() - 0.5) * 2, y + 2 * len / 3, x, y + len); }
      g.stroke();
    }
    var lg = g.createLinearGradient(0, 0, W * 0.35, H);
    lg.addColorStop(0, 'rgba(255,255,255,0.16)'); lg.addColorStop(0.5, 'rgba(255,255,255,0)'); lg.addColorStop(1, 'rgba(0,0,0,0.3)');
    g.fillStyle = lg; g.fillRect(0, 0, W, H);
    g.restore();
    // outer bevel line
    g.lineWidth = 1.2; g.strokeStyle = 'rgba(255,255,255,0.18)'; roundRectPath(g, 0.8, 0.8, W - 1.6, H - 1.6, RO - 0.8); g.stroke();
    g.strokeStyle = 'rgba(0,0,0,0.6)'; roundRectPath(g, 0.3, 0.3, W - 0.6, H - 0.6, RO); g.stroke();

    // metal trim between wood and leather
    var mg = g.createLinearGradient(0, 0, W, H);
    mg.addColorStop(0, th.trimHi); mg.addColorStop(0.3, th.trim); mg.addColorStop(0.55, th.trimLo);
    mg.addColorStop(0.8, th.trim); mg.addColorStop(1, th.trimLo);
    g.save();
    if (th.glow) { g.shadowColor = th.glow; g.shadowBlur = 8 * k; }
    g.lineWidth = 1.8; g.strokeStyle = th.glow ? th.trim : mg; roundRectPath(g, 7, 7, W - 14, H - 14, RO - 7); g.stroke();
    g.restore();

    // padded leather rail (a tube around the felt)
    var o0 = 8.2, o1 = RL;
    g.save();
    g.beginPath(); rrect(g, o0, o0, W - 2 * o0, H - 2 * o0, RO - o0); rrect(g, o1, o1, W - 2 * o1, H - 2 * o1, 12);
    g.clip('evenodd');
    g.fillStyle = th.leatherLo; g.fillRect(0, 0, W, H);
    var mid = (o0 + o1) / 2, wid = o1 - o0 + 2;
    var tubes = [[1.0, th.leatherLo, 1], [0.86, th.leather, 1], [0.6, shade(th.leather, 0.1), 0.9], [0.36, th.leatherHi, 0.45], [0.14, shade(th.leatherHi, 0.35), 0.35]];
    for (i = 0; i < tubes.length; i++) {
      g.globalAlpha = tubes[i][2]; g.lineWidth = wid * tubes[i][0]; g.strokeStyle = tubes[i][1];
      roundRectPath(g, mid - (i > 2 ? 0.7 : 0), mid - (i > 2 ? 0.9 : 0), W - 2 * mid, H - 2 * mid, RO - mid); g.stroke();
    }
    g.globalAlpha = 1;
    // leather grain
    for (i = 0; i < 1400; i++) {
      g.fillStyle = rnd() < 0.5 ? 'rgba(0,0,0,0.16)' : 'rgba(255,255,255,0.05)';
      var side2 = Math.floor(rnd() * 4), a2 = rnd(), b2 = o0 + rnd() * (o1 - o0);
      if (side2 === 0) g.fillRect(a2 * W, b2, 0.9, 0.9); else if (side2 === 1) g.fillRect(a2 * W, H - b2, 0.9, 0.9);
      else if (side2 === 2) g.fillRect(b2, a2 * H, 0.9, 0.9); else g.fillRect(W - b2, a2 * H, 0.9, 0.9);
    }
    // stitching
    g.setLineDash([3.2, 2.6]); g.lineWidth = 0.9; g.strokeStyle = rgba(hexRgb(th.stitch), 0.55);
    if (th.glow) { g.shadowColor = th.glow; g.shadowBlur = 4 * k; }
    roundRectPath(g, o1 - 3.1, o1 - 3.1, W - 2 * (o1 - 3.1), H - 2 * (o1 - 3.1), 15); g.stroke();
    g.shadowBlur = 0;
    roundRectPath(g, o0 + 2.8, o0 + 2.8, W - 2 * (o0 + 2.8), H - 2 * (o0 + 2.8), RO - o0 - 2.8); g.stroke();
    g.setLineDash([]);
    // directional light over the rail
    var rg = g.createLinearGradient(0, 0, W * 0.3, H);
    rg.addColorStop(0, 'rgba(255,255,255,0.14)'); rg.addColorStop(0.45, 'rgba(255,255,255,0)'); rg.addColorStop(1, 'rgba(0,0,0,0.35)');
    g.fillStyle = rg; g.fillRect(0, 0, W, H);
    g.restore();

    // ---- felt ----
    var FX = RL, FY = RL, FW = W - 2 * RL, FH = H - 2 * RL;
    g.save();
    roundRectPath(g, FX, FY, FW, FH, 12); g.clip();
    var fg = g.createRadialGradient(W * 0.46, H * 0.5, 20, W * 0.5, H * 0.52, W * 0.62);
    fg.addColorStop(0, th.feltHi); fg.addColorStop(0.55, th.felt); fg.addColorStop(1, th.feltLo);
    g.fillStyle = fg; g.fillRect(FX, FY, FW, FH);
    // felt fibre texture: device-pixel noise, then short fibres
    var nz = mk(128), ng = nz.getContext('2d'), id = ng.createImageData(128, 128), dd = id.data, nr = mulberry32(77);
    for (i = 0; i < dd.length; i += 4) {
      var v = nr(); var c = v < 0.5 ? 0 : 255;
      dd[i] = dd[i + 1] = dd[i + 2] = c; dd[i + 3] = Math.floor(Math.abs(v - 0.5) * 2 * 26);
    }
    ng.putImageData(id, 0, 0);
    var pat = g.createPattern(nz, 'repeat');
    if (pat) {
      g.save(); g.setTransform(Math.max(1, k * 0.8), 0, 0, Math.max(1, k * 0.8), 0, 0);
      g.fillStyle = pat; g.fillRect(0, 0, cv.width, cv.height); g.restore();
    }
    var fHi = hexRgb(th.feltHi), fLo = hexRgb(th.feltLo);
    g.lineWidth = 0.5;
    for (i = 0; i < 2600; i++) {
      x = FX + rnd() * FW; y = FY + rnd() * FH;
      var an = rnd() * TAU, ln = 1.5 + rnd() * 3.5;
      g.strokeStyle = rnd() < 0.5 ? rgba(fHi, 0.16) : rgba(fLo, 0.22);
      g.beginPath(); g.moveTo(x, y); g.lineTo(x + Math.cos(an) * ln, y + Math.sin(an) * ln); g.stroke();
    }
    // overhead light + vignette
    var sp = g.createRadialGradient(W * 0.4, H * 0.38, 10, W * 0.42, H * 0.42, W * 0.5);
    sp.addColorStop(0, 'rgba(255,255,255,0.10)'); sp.addColorStop(1, 'rgba(255,255,255,0)');
    g.fillStyle = sp; g.fillRect(FX, FY, FW, FH);
    var vg = g.createRadialGradient(W * 0.5, H * 0.52, H * 0.3, W * 0.5, H * 0.52, W * 0.6);
    vg.addColorStop(0, 'rgba(0,0,0,0)'); vg.addColorStop(1, 'rgba(0,0,0,0.42)');
    g.fillStyle = vg; g.fillRect(FX, FY, FW, FH);

    drawLayout(g, th, k);
    drawWall(g, th, k);

    // rail shadow on the felt (light from the top-left: top and left edges darker)
    g.save();
    g.shadowColor = 'rgba(0,0,0,0.75)'; g.shadowBlur = 12 * k; g.shadowOffsetX = 3 * k; g.shadowOffsetY = 4 * k;
    g.lineWidth = 30; g.strokeStyle = '#000';
    roundRectPath(g, FX - 15, FY - 15, FW + 30, FH + 30, 26); g.stroke();
    g.restore();
    g.restore();
    // felt edge lip
    g.lineWidth = 1; g.strokeStyle = 'rgba(0,0,0,0.55)'; roundRectPath(g, FX - 0.5, FY - 0.5, FW + 1, FH + 1, 12.5); g.stroke();
    g.strokeStyle = 'rgba(255,255,255,0.12)'; roundRectPath(g, FX - 1.6, FY - 1.6, FW + 3.2, FH + 3.2, 13.5); g.stroke();
    if (th.glow) {
      g.save(); g.shadowColor = th.glow; g.shadowBlur = 10 * k; g.strokeStyle = rgba(hexRgb(th.glow), 0.85); g.lineWidth = 1.4;
      roundRectPath(g, FX - 1, FY - 1, FW + 2, FH + 2, 13); g.stroke(); g.restore();
    }
    return cv;
  }

  // layout print on the felt: point row, OFF spot, centre emblem, pass line
  function drawLayout(g, th, k) {
    var i, b, line = th.line, lr = hexRgb(line);
    g.save();
    g.lineJoin = 'round';
    // point row
    var x0 = GEOM.PT_X0, x1 = GEOM.PT_X1, y0 = GEOM.PT_Y0, y1 = GEOM.PT_Y1;
    g.fillStyle = 'rgba(0,0,0,0.10)'; g.fillRect(x0, y0, x1 - x0, y1 - y0);
    glowText(g, th, function () {
      g.lineWidth = 2; g.strokeStyle = line;
      g.strokeRect(x0, y0, x1 - x0, y1 - y0);
      g.beginPath();
      for (var j = 1; j < 6; j++) { var bb = ptBox(j); g.moveTo(bb.x, y0); g.lineTo(bb.x, y1); }
      g.stroke();
    });
    for (i = 0; i < 6; i++) {
      b = ptBox(i);
      var lab = PT_LABELS[i], word = lab.length > 2;
      g.font = (word ? '700 25px ' : '700 34px ') + SERIF;
      g.textAlign = 'center'; g.textBaseline = 'middle'; g.fillStyle = th.num;
      var cx = b.x + b.w / 2, cy = b.y + b.h * 0.7;
      glowText(g, th, function () {
        g.save(); g.shadowColor = th.glow || 'rgba(0,0,0,0.45)'; g.shadowBlur = th.glow ? 8 : 2; g.shadowOffsetY = th.glow ? 0 : 1;
        g.fillText(lab, cx, cy + 1); g.restore();
      });
      // small puck guide dot where the ON puck sits
      g.fillStyle = rgba(lr, 0.18); g.beginPath(); g.arc(cx, b.y + 24, 3, 0, TAU); g.fill();
    }
    // OFF spot
    g.save();
    g.setLineDash([3, 3]); g.lineWidth = 1.3; g.strokeStyle = rgba(lr, 0.55);
    g.beginPath(); g.arc(GEOM.OFF_X, GEOM.OFF_Y, GEOM.PUCK_R + 4, 0, TAU); g.stroke();
    g.restore();
    g.font = '700 9px ' + FONT; g.textAlign = 'center'; g.textBaseline = 'middle'; g.fillStyle = rgba(lr, 0.55);
    g.fillText('PUCK', GEOM.OFF_X, GEOM.OFF_Y + GEOM.PUCK_R + 13);

    // centre emblem: hexagon + CRAPS
    var ex = (GEOM.RAIL + GEOM.WALL_X) / 2, ey = (GEOM.PT_Y1 + GEOM.H - GEOM.RAIL) / 2 - 6;
    g.save();
    g.globalAlpha = th.glow ? 0.5 : 0.22;
    g.lineWidth = 2; g.strokeStyle = line;
    glowText(g, th, function () {
      g.beginPath();
      for (var j = 0; j < 6; j++) {
        var an = Math.PI / 6 + j * Math.PI / 3, px = ex + 70 * Math.cos(an), py = ey + 70 * Math.sin(an);
        if (j) g.lineTo(px, py); else g.moveTo(px, py);
      }
      g.closePath(); g.stroke();
      g.lineWidth = 1;
      g.beginPath();
      for (j = 0; j < 6; j++) {
        var an2 = Math.PI / 6 + j * Math.PI / 3, qx = ex + 63 * Math.cos(an2), qy = ey + 63 * Math.sin(an2);
        if (j) g.lineTo(qx, qy); else g.moveTo(qx, qy);
      }
      g.closePath(); g.stroke();
    });
    g.fillStyle = line; g.textAlign = 'center'; g.textBaseline = 'middle';
    g.font = '700 30px ' + SERIF;
    spaced(g, 'CRAPS', ex, ey + 4, 5);
    g.font = '700 9px ' + FONT;
    spaced(g, 'HEXCAST', ex, ey - 24, 3.5);
    g.restore();

    // pass line along the players' side
    g.save();
    g.globalAlpha = th.glow ? 0.75 : 0.5;
    glowText(g, th, function () {
      g.lineWidth = 1.6; g.strokeStyle = line;
      roundRectPath(g, 44, GEOM.H - GEOM.RAIL - 40, GEOM.WALL_X - 66, 30, 15); g.stroke();
    });
    g.fillStyle = line; g.font = '700 15px ' + SERIF; g.textAlign = 'center'; g.textBaseline = 'middle';
    spaced(g, 'PASS LINE', (44 + GEOM.WALL_X - 22) / 2, GEOM.H - GEOM.RAIL - 25, 4);
    g.restore();
    g.restore();
  }
  function spaced(g, text, cx, cy, sp) {   // letter-spaced centred text
    var w = 0, i, ws = [];
    for (i = 0; i < text.length; i++) { ws.push(g.measureText(text[i]).width); w += ws[i] + (i ? sp : 0); }
    var x = cx - w / 2;
    g.textAlign = 'left';
    for (i = 0; i < text.length; i++) { g.fillText(text[i], x, cy); x += ws[i] + sp; }
    g.textAlign = 'center';
  }

  // the back wall: pyramid-diamond rubber on the right. Each pyramid has four facets
  // lit by the same top-left light as the dice; grooves between them stay dark.
  function drawWall(g, th, k) {
    var x0 = GEOM.WALL_X, x1 = GEOM.W - GEOM.RAIL, y0 = GEOM.RAIL, y1 = GEOM.H - GEOM.RAIL, w = x1 - x0;
    var hd = w / 3, lo = hexRgb(th.rubberLo), hi = hexRgb(th.rubberHi), base = hexRgb(th.rubber);
    var fi, r, c;
    // ambient occlusion on the felt along the foot of the wall
    var ao = g.createLinearGradient(x0 - 10, 0, x0, 0);
    ao.addColorStop(0, 'rgba(0,0,0,0)'); ao.addColorStop(1, 'rgba(0,0,0,0.34)');
    g.fillStyle = ao; g.fillRect(x0 - 10, y0, 10, y1 - y0);
    g.save();
    g.beginPath(); g.rect(x0, y0, w, y1 - y0); g.clip();
    g.fillStyle = css(mixRgb(lo, base, 0.35)); g.fillRect(x0, y0, w, y1 - y0);
    // facet normals: 45 degree slopes towards NE, SE, SW, NW
    var fn = [[0.5, -0.5], [0.5, 0.5], [-0.5, 0.5], [-0.5, -0.5]], cols = [], cHi = [];
    for (fi = 0; fi < 4; fi++) {
      var d = clamp(fn[fi][0] * L3[0] + fn[fi][1] * L3[1] + 0.7071 * L3[2], 0, 1);
      var cc = d > 0.5 ? mixRgb(base, hi, Math.pow((d - 0.5) * 2, 1.2)) : mixRgb(lo, base, 0.25 + 1.5 * d);
      cols.push(css(cc)); cHi.push(rgba(mixRgb(cc, [255, 255, 255], 0.18), 1));
    }
    for (r = -1; (r - 1) * hd < y1 - y0; r++) {
      var cy = y0 + r * hd;
      for (c = -1; c < 3; c++) {
        var cx = x0 + hd + 2 * hd * c + (r & 1 ? hd : 0), e = hd * 0.9;   // 10% groove
        var pts = [[cx, cy - e], [cx + e, cy], [cx, cy + e], [cx - e, cy]];
        for (fi = 0; fi < 4; fi++) {
          var p0 = pts[fi], p1 = pts[(fi + 1) % 4];
          var fg = g.createLinearGradient(cx, cy, (p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2);
          fg.addColorStop(0, cHi[fi]); fg.addColorStop(1, cols[fi]);
          g.fillStyle = fg;
          g.beginPath(); g.moveTo(cx, cy); g.lineTo(p0[0], p0[1]); g.lineTo(p1[0], p1[1]); g.closePath(); g.fill();
        }
        // lit ridge (towards the light) + apex glint
        g.strokeStyle = 'rgba(255,255,255,0.22)'; g.lineWidth = 0.6;
        g.beginPath(); g.moveTo(pts[3][0], pts[3][1]); g.lineTo(cx, cy); g.lineTo(pts[0][0], pts[0][1]); g.stroke();
        g.fillStyle = 'rgba(255,255,255,0.5)'; g.beginPath(); g.arc(cx - 0.3, cy - 0.3, 0.75, 0, TAU); g.fill();
      }
    }
    // rounded rubber lip facing the felt, darker ends under the rail
    var lg = g.createLinearGradient(x0, 0, x0 + 6, 0);
    lg.addColorStop(0, 'rgba(255,255,255,0.22)'); lg.addColorStop(0.35, 'rgba(255,255,255,0.06)'); lg.addColorStop(1, 'rgba(0,0,0,0)');
    g.fillStyle = lg; g.fillRect(x0, y0, 6, y1 - y0);
    var sg = g.createLinearGradient(0, y0, 0, y1);
    sg.addColorStop(0, 'rgba(0,0,0,0.5)'); sg.addColorStop(0.1, 'rgba(0,0,0,0)'); sg.addColorStop(0.5, 'rgba(255,255,255,0.03)');
    sg.addColorStop(0.9, 'rgba(0,0,0,0)'); sg.addColorStop(1, 'rgba(0,0,0,0.55)');
    g.fillStyle = sg; g.fillRect(x0, y0, w, y1 - y0);
    var rg = g.createLinearGradient(x1 - 7, 0, x1, 0);        // shadow under the rail on the far side
    rg.addColorStop(0, 'rgba(0,0,0,0)'); rg.addColorStop(1, 'rgba(0,0,0,0.45)');
    g.fillStyle = rg; g.fillRect(x1 - 7, y0, 7, y1 - y0);
    g.restore();
    g.fillStyle = 'rgba(0,0,0,0.55)'; g.fillRect(x0 - 1, y0, 1, y1 - y0);
    g.fillStyle = 'rgba(255,255,255,0.14)'; g.fillRect(x0, y0, 0.7, y1 - y0);
    if (th.glow) {
      g.save(); g.shadowColor = th.glow; g.shadowBlur = 9 * k; g.strokeStyle = th.glow; g.lineWidth = 1.4;
      g.beginPath(); g.moveTo(x0 - 0.7, y0); g.lineTo(x0 - 0.7, y1); g.stroke(); g.restore();
    }
  }

  // --- dice faces ------------------------------------------------------------------
  var PIPS = {
    1: [[0, 0]], 2: [[-1, -1], [1, 1]], 3: [[-1, -1], [0, 0], [1, 1]],
    4: [[-1, -1], [1, -1], [-1, 1], [1, 1]], 5: [[-1, -1], [1, -1], [0, 0], [-1, 1], [1, 1]],
    6: [[-1, -1], [-1, 0], [-1, 1], [1, -1], [1, 0], [1, 1]]
  };
  function drawPip(g, x, y, r, ds) {
    // a drilled, painted pip: dark rim, concave paint (lit on the far side)
    g.fillStyle = ds.pipRim;
    g.beginPath(); g.arc(x + r * 0.05, y + r * 0.07, r * 1.12, 0, TAU); g.fill();
    var gr = g.createRadialGradient(x + r * 0.28, y + r * 0.32, r * 0.1, x, y, r);
    gr.addColorStop(0, ds.pip); gr.addColorStop(0.55, ds.pip); gr.addColorStop(1, ds.pipLo);
    g.fillStyle = gr; g.beginPath(); g.arc(x, y, r, 0, TAU); g.fill();
    // shadow of the hole's lit-side wall
    g.save(); g.beginPath(); g.arc(x, y, r, 0, TAU); g.clip();
    g.fillStyle = 'rgba(0,0,0,0.28)'; g.beginPath(); g.arc(x - r * 0.35, y - r * 0.42, r * 0.95, 0, TAU);
    g.arc(x + r * 0.05, y + r * 0.02, r * 1.02, 0, TAU, true); g.fill();
    g.restore();
  }
  function buildFace(v, TS, ds) {
    var cv = mk(TS), g = cv.getContext('2d'), h = TS / 2, i;
    var unit = TS / (2 * AIN);          // texture px per stage px
    // base material
    var gr = g.createRadialGradient(TS * 0.36, TS * 0.3, TS * 0.04, h, h, TS * 0.8);
    gr.addColorStop(0, ds.faceHi); gr.addColorStop(0.5, ds.face); gr.addColorStop(1, ds.faceLo);
    g.fillStyle = gr; g.fillRect(0, 0, TS, TS);
    if (ds.metal) {          // brushed gold bands
      var mg = g.createLinearGradient(0, 0, TS, TS);
      mg.addColorStop(0, 'rgba(255,255,255,0.28)'); mg.addColorStop(0.25, 'rgba(255,255,255,0)');
      mg.addColorStop(0.5, 'rgba(255,245,200,0.22)'); mg.addColorStop(0.7, 'rgba(0,0,0,0.12)'); mg.addColorStop(1, 'rgba(255,255,255,0.1)');
      g.fillStyle = mg; g.fillRect(0, 0, TS, TS);
      var br = mulberry32(v * 31);
      g.lineWidth = Math.max(0.5, TS / 160);
      for (i = 0; i < 60; i++) {
        var yy = br() * TS; g.strokeStyle = br() < 0.5 ? 'rgba(255,255,255,0.08)' : 'rgba(80,50,0,0.08)';
        g.beginPath(); g.moveTo(0, yy); g.lineTo(TS, yy + (br() - 0.5) * TS * 0.1); g.stroke();
      }
    }
    if (ds.translucent) {    // light transmitted through the body: glow + ghost pips of the far face
      var ig = g.createRadialGradient(TS * 0.62, TS * 0.66, 0, TS * 0.6, TS * 0.64, TS * 0.55);
      ig.addColorStop(0, 'rgba(255,120,110,0.35)'); ig.addColorStop(1, 'rgba(255,90,80,0)');
      g.fillStyle = ig; g.fillRect(0, 0, TS, TS);
      var ghost = PIPS[7 - v], po = 0.5 * HALF * unit, pr = 0.17 * HALF * unit;
      g.save();
      try { g.filter = 'blur(' + Math.max(0.6, TS / 48).toFixed(2) + 'px)'; } catch (e) { }
      g.fillStyle = 'rgba(255,225,215,0.13)';
      for (i = 0; i < ghost.length; i++) {
        g.beginPath(); g.arc(h - ghost[i][0] * po * 0.92 + TS * 0.035, h + ghost[i][1] * po * 0.92 + TS * 0.05, pr * 0.9, 0, TAU); g.fill();
      }
      g.restore();
    }
    // bevel start: the flat face darkens a touch towards its edges
    var eg, ew = TS * 0.07;
    var sides = [[0, 0, ew, 0, 0, 0, ew, TS], [TS, 0, TS - ew, 0, TS - ew, 0, ew, TS], [0, 0, 0, ew, 0, 0, TS, ew], [0, TS, 0, TS - ew, 0, TS - ew, TS, ew]];
    for (i = 0; i < 4; i++) {
      var s = sides[i];
      eg = g.createLinearGradient(s[0], s[1], s[2], s[3]);
      eg.addColorStop(0, ds.translucent ? 'rgba(60,0,4,0.35)' : 'rgba(0,0,0,0.16)'); eg.addColorStop(1, 'rgba(0,0,0,0)');
      g.fillStyle = eg; g.fillRect(s[4], s[5], s[6], s[7]);
    }
    // pips
    var pips = PIPS[v], off = 0.5 * HALF * unit, rr = 0.17 * HALF * unit;
    for (i = 0; i < pips.length; i++) drawPip(g, h + pips[i][0] * off, h + pips[i][1] * off, rr, ds);
    return cv;
  }
  function buildSheen(TS) {
    var cv = mk(TS), g = cv.getContext('2d');
    var gr = g.createRadialGradient(TS * 0.3, TS * 0.26, 0, TS * 0.36, TS * 0.32, TS * 0.8);
    gr.addColorStop(0, 'rgba(255,255,255,0.95)'); gr.addColorStop(0.35, 'rgba(255,255,255,0.35)'); gr.addColorStop(1, 'rgba(255,255,255,0)');
    g.fillStyle = gr; g.fillRect(0, 0, TS, TS);
    return cv;
  }
  // soft rounded-square shadow sprite (drawn off-canvas, only its shadow lands)
  function buildShadow(k, blur, alpha, tint) {
    var size = GEOM.E * k * 0.96, pad = blur * k * 2.4 + 2, S = Math.ceil(size + 2 * pad), cv = mk(S), g = cv.getContext('2d');
    g.shadowColor = rgba(tint, alpha); g.shadowBlur = blur * k; g.shadowOffsetX = S * 3;
    g.fillStyle = '#000';
    roundRectPath(g, -S * 3 + pad, pad, size, size, size * 0.22); g.fill();
    return { cv: cv, half: S / 2 };
  }
  function buildPuck(k, on) {
    var R = GEOM.PUCK_R, S = Math.ceil((2 * R + 14) * k), cv = mk(S), g = cv.getContext('2d');
    g.translate(S / 2, S / 2); g.scale(k, k);
    // edge + drop shadow
    g.save(); g.shadowColor = 'rgba(0,0,0,0.6)'; g.shadowBlur = 5 * k; g.shadowOffsetX = 1.5 * k; g.shadowOffsetY = 2.5 * k;
    g.fillStyle = on ? '#b9b9b9' : '#050505'; g.beginPath(); g.arc(0, 0, R, 0, TAU); g.fill(); g.restore();
    var gr = g.createRadialGradient(-R * 0.35, -R * 0.4, R * 0.1, 0, 0, R);
    if (on) { gr.addColorStop(0, '#ffffff'); gr.addColorStop(0.7, '#f1f1f1'); gr.addColorStop(1, '#bdbdbd'); }
    else { gr.addColorStop(0, '#4a4a4f'); gr.addColorStop(0.65, '#1b1b1f'); gr.addColorStop(1, '#050506'); }
    g.fillStyle = gr; g.beginPath(); g.arc(0, 0, R - 1, 0, TAU); g.fill();
    g.lineWidth = 1.2; g.strokeStyle = on ? 'rgba(0,0,0,0.25)' : 'rgba(255,255,255,0.18)';
    g.beginPath(); g.arc(0, 0, R - 3.2, 0, TAU); g.stroke();
    g.font = '900 ' + (on ? 13 : 11) + 'px ' + FONT; g.textAlign = 'center'; g.textBaseline = 'middle';
    g.fillStyle = on ? '#111111' : '#ffffff';
    spaced(g, on ? 'ON' : 'OFF', 0, 0.8, 1);
    var sh = g.createLinearGradient(0, -R, 0, 0);
    sh.addColorStop(0, 'rgba(255,255,255,' + (on ? 0.6 : 0.22) + ')'); sh.addColorStop(1, 'rgba(255,255,255,0)');
    g.fillStyle = sh; g.beginPath(); g.ellipse(0, -R * 0.45, R * 0.7, R * 0.42, 0, 0, TAU); g.fill();
    return { cv: cv, half: S / 2 / k };
  }
  function buildHighlight(k, th) {
    var b = ptBox(0), pad = 16, cv = mk((b.w + 2 * pad) * k, (b.h + 2 * pad) * k), g = cv.getContext('2d');
    var ac = hexRgb(th.accent);
    g.scale(k, k);
    g.save(); g.shadowColor = rgba(ac, 0.95); g.shadowBlur = 14 * k;
    g.strokeStyle = rgba(ac, 1); g.lineWidth = 3; g.strokeRect(pad + 1.5, pad + 1.5, b.w - 3, b.h - 3); g.restore();
    var ig = g.createLinearGradient(0, pad, 0, pad + b.h);
    ig.addColorStop(0, rgba(ac, 0.28)); ig.addColorStop(1, rgba(ac, 0.08));
    g.fillStyle = ig; g.fillRect(pad + 3, pad + 3, b.w - 6, b.h - 6);
    return { cv: cv, pad: pad };
  }

  // ======================================================================
  // Sound — WebAudio, synthesized, created lazily; failures are silent.
  // ======================================================================
  function noop() { }
  function Sfx() { this.ctx = null; this.master = null; this.failed = false; this.buf = {}; }
  Sfx.prototype.ensure = function (vol) {
    if (this.failed) return false;
    if (!this.ctx) {
      var AC = root.AudioContext || root.webkitAudioContext;
      if (!AC) { this.failed = true; return false; }
      try {
        var ctx = new AC();
        this.ctx = ctx;
        this.master = ctx.createGain(); this.master.gain.value = clamp(+vol || 0, 0, 1); this.master.connect(ctx.destination);
        this.buf.felt = makeSound(ctx, 'felt'); this.buf.wall = makeSound(ctx, 'wall');
        this.buf.clack = makeSound(ctx, 'clack'); this.buf.tick = makeSound(ctx, 'tick');
        this.buf.rattle = makeSound(ctx, 'rattle'); this.buf.settle = makeSound(ctx, 'settle');
      } catch (e) { this.failed = true; this.ctx = null; return false; }
    }
    if (this.ctx.state === 'suspended' && this.ctx.resume) {
      try { var pr = this.ctx.resume(); if (pr && pr.catch) pr.catch(noop); } catch (e) { }
    }
    this.setVolume(vol);
    return true;
  };
  Sfx.prototype.setVolume = function (v) {
    if (!this.ctx || !this.master) return;
    try { this.master.gain.setTargetAtTime(clamp(+v || 0, 0, 1), this.ctx.currentTime, 0.03); } catch (e) { }
  };
  Sfx.prototype.hit = function (kind, vol, rate) {
    if (!this.ctx || this.ctx.state !== 'running' || !(vol > 0.005)) return;
    var b = this.buf[kind]; if (!b) return;
    try {
      var c = this.ctx, s = c.createBufferSource(), g = c.createGain();
      s.buffer = b; s.playbackRate.value = rate || 1;
      g.gain.value = clamp(vol, 0, 1.2);
      s.connect(g); g.connect(this.master); s.start();
    } catch (e) { }
  };
  Sfx.prototype.close = function () {
    if (this.ctx) { try { var p = this.ctx.close(); if (p && p.catch) p.catch(noop); } catch (e) { } }
    this.ctx = null;
  };
  function makeSound(ctx, kind) {
    var sr = ctx.sampleRate, len = { felt: 0.12, wall: 0.16, clack: 0.09, tick: 0.035, rattle: 0.05, settle: 0.07 }[kind] || 0.1;
    var n = Math.floor(sr * len), buf = ctx.createBuffer(1, n, sr), d = buf.getChannelData(0);
    var r = mulberry32(hashStr(kind)), peak = 0, lp = 0, i;
    for (i = 0; i < n; i++) {
      var t = i / sr, ns = r() * 2 - 1, v = 0;
      if (kind === 'felt') {           // muffled thump on cloth
        lp += (ns - lp) * 0.08;
        v = lp * Math.exp(-t / 0.018) * 2.2 + Math.sin(TAU * 150 * t) * Math.exp(-t / 0.028) * 0.7 +
          Math.sin(TAU * 1900 * t) * Math.exp(-t / 0.004) * 0.25;
      } else if (kind === 'wall') {    // knock on the rubber pyramids + a short rattle
        lp += (ns - lp) * 0.3;
        v = lp * Math.exp(-t / 0.012) * 1.2 + Math.sin(TAU * 430 * t) * Math.exp(-t / 0.03) * 0.6 +
          Math.sin(TAU * 1250 * t) * Math.exp(-t / 0.012) * 0.4 + (t > 0.035 ? ns * Math.exp(-(t - 0.035) / 0.01) * 0.35 : 0);
      } else if (kind === 'clack' || kind === 'settle') {   // die slapping flat
        lp += (ns - lp) * 0.25;
        v = lp * Math.exp(-t / 0.008) * 1.2 + Math.sin(TAU * 2350 * t) * Math.exp(-t / 0.009) * 0.45 +
          Math.sin(TAU * 3700 * t) * Math.exp(-t / 0.005) * 0.3 + Math.sin(TAU * 210 * t) * Math.exp(-t / 0.02) * 0.45;
      } else {                          // tick / rattle: dice clicking together
        v = ns * Math.exp(-t / 0.0025) * 0.6 + Math.sin(TAU * (kind === 'tick' ? 3900 : 3100) * t) * Math.exp(-t / 0.004) * 0.6 +
          Math.sin(TAU * 5600 * t) * Math.exp(-t / 0.002) * 0.3;
      }
      d[i] = v; if (Math.abs(v) > peak) peak = Math.abs(v);
    }
    for (i = 0; i < n; i++) d[i] = d[i] / (peak || 1) * 0.9;
    return buf;
  }

  // ======================================================================
  // DOM overlays — styles are injected once
  // ======================================================================
  function pipBg(v) {
    var P = { TL: '27% 27%', TR: '73% 27%', ML: '27% 50%', C: '50% 50%', MR: '73% 50%', BL: '27% 73%', BR: '73% 73%' };
    var L = { 1: ['C'], 2: ['TL', 'BR'], 3: ['TL', 'C', 'BR'], 4: ['TL', 'TR', 'BL', 'BR'], 5: ['TL', 'TR', 'C', 'BL', 'BR'], 6: ['TL', 'ML', 'BL', 'TR', 'MR', 'BR'] }[v];
    var out = [];
    for (var i = 0; i < L.length; i++) out.push('radial-gradient(circle var(--pr) at ' + P[L[i]] + ',var(--hgc-pip) 0,var(--hgc-pip) 72%,transparent 100%)');
    return '.hgc-v' + v + '{background-image:' + out.join(',') + ';}';
  }
  var STYLE = [
    '.hgc-root{position:absolute;left:0;top:0;width:100%;height:100%;pointer-events:none;',
    'font-family:"Segoe UI",system-ui,-apple-system,"Helvetica Neue",Arial,sans-serif;color:#fff;',
    '-webkit-font-smoothing:antialiased;line-height:1.2;}',
    '.hgc-canvas{position:absolute;left:-' + GEOM.PAD + 'px;top:-' + GEOM.PAD + 'px;width:calc(100% + ' + (2 * GEOM.PAD) + 'px);',
    'height:calc(100% + ' + (2 * GEOM.PAD) + 'px);display:block;}',
    '.hgc-hide{display:none !important;}',
    '.hgc-demo{opacity:.55;}',
    /* above the tray: history strip (closest), caption + countdown row */
    /* --hgc-tui (set by _fitTop) = --hgc-ui, or smaller when there is no room above the tray */
    '.hgc-top{position:absolute;left:50%;bottom:100%;margin-bottom:16px;transform:translateX(-50%) scale(var(--hgc-tui,var(--hgc-ui,1)));',
    'transform-origin:50% 100%;',
    'display:flex;flex-direction:column-reverse;align-items:center;gap:12px;}',
    '.hgc-caprow{display:flex;align-items:center;gap:12px;}',
    '.hgc-pillbg{background:linear-gradient(180deg,rgba(30,30,40,.92),rgba(10,10,14,.92));border:1px solid rgba(255,255,255,.13);',
    'box-shadow:0 10px 26px rgba(0,0,0,.5),inset 0 1px 0 rgba(255,255,255,.07);}',
    '.hgc-cap{display:flex;align-items:center;gap:9px;white-space:nowrap;padding:8px 18px 8px 13px;border-radius:999px;',
    'font-size:19px;font-weight:600;color:#d7d7e2;}',
    '.hgc-cap b{color:var(--hgc-accent);font-weight:800;max-width:260px;overflow:hidden;text-overflow:ellipsis;}',
    '.hgc-cap em{font-style:normal;color:rgba(255,255,255,.55);font-weight:700;}',
    '.hgc-cap i{width:9px;height:9px;border-radius:50%;background:var(--hgc-accent);box-shadow:0 0 10px var(--hgc-accent);}',
    '.hgc-cap.hgc-live i{animation:hgc-blink 1.1s ease-in-out infinite;}',
    '.hgc-cap.hgc-in{animation:hgc-rise .45s cubic-bezier(.2,.9,.3,1.2) both;}',
    /* countdown */
    '.hgc-cd{display:flex;align-items:center;gap:10px;white-space:nowrap;padding:6px 18px 6px 7px;border-radius:999px;}',
    '.hgc-cd svg{width:34px;height:34px;transform:rotate(-90deg);flex:0 0 auto;}',
    '.hgc-cd circle{fill:none;stroke-width:4;}',
    '.hgc-cd .hgc-ring0{stroke:rgba(255,255,255,.14);}',
    '.hgc-cd .hgc-ring1{stroke:var(--hgc-accent);stroke-linecap:round;}',
    '.hgc-cd small{display:block;font-size:10.5px;letter-spacing:.2em;font-weight:800;color:rgba(255,255,255,.6);line-height:1.1;}',
    '.hgc-cd b{display:block;font-size:21px;font-weight:800;font-variant-numeric:tabular-nums;line-height:1.05;}',
    '.hgc-cd.hgc-urgent b{color:#ff6b5e;}.hgc-cd.hgc-urgent .hgc-ring1{stroke:#ff3b30;}',
    '.hgc-cd.hgc-urgent{animation:hgc-throb 1s ease-in-out infinite;}',
    /* history strip */
    '.hgc-hist{display:flex;align-items:flex-end;gap:6px;padding:6px 10px;border-radius:16px;white-space:nowrap;}',
    '.hgc-hi{display:flex;flex-direction:column;align-items:center;gap:3px;padding:4px 5px 3px;border-radius:9px;',
    'background:rgba(255,255,255,.05);box-shadow:inset 0 0 0 1px rgba(255,255,255,.06);}',
    '.hgc-hi .hgc-pair{display:flex;gap:3px;}',
    '.hgc-hi .hgc-md{--d:17px;--pr:1.85px;}',
    '.hgc-hi b{font-size:12px;font-weight:800;color:rgba(255,255,255,.85);line-height:1;font-variant-numeric:tabular-nums;}',
    '.hgc-hi.hgc-7{background:rgba(255,59,48,.24);box-shadow:inset 0 0 0 1px rgba(255,96,86,.6);}',
    '.hgc-hi.hgc-7 b{color:#ff9b91;}',
    '.hgc-hi.hgc-pm{box-shadow:inset 0 0 0 1px rgba(255,214,90,.7);background:rgba(255,200,60,.14);}',
    '.hgc-hi.hgc-new{padding:5px 7px 4px;box-shadow:0 0 0 2px var(--hgc-accent),0 0 14px var(--hgc-accent-a);}',
    '.hgc-hi.hgc-new .hgc-md{--d:22px;--pr:2.4px;}',
    '.hgc-hi.hgc-new b{font-size:14px;color:#fff;}',
    '.hgc-hi:nth-child(n+7){opacity:.85;}.hgc-hi:nth-child(n+11){opacity:.7;}',
    '.hgc-hist.hgc-anim .hgc-new{animation:hgc-chip .55s cubic-bezier(.2,.9,.3,1.35) both;}',
    /* mini dice (history, call banner) */
    '.hgc-md{position:relative;display:block;width:var(--d);height:var(--d);border-radius:24%;flex:0 0 auto;',
    'background-color:var(--hgc-die);',
    'box-shadow:inset 0 calc(var(--d) * -.1) calc(var(--d) * .16) var(--hgc-die-lo),inset 0 calc(var(--d) * .06) calc(var(--d) * .08) var(--hgc-die-hi),0 1px 3px rgba(0,0,0,.6);}',
    pipBg(1), pipBg(2), pipBg(3), pipBg(4), pipBg(5), pipBg(6),
    /* stickman call banner, across the bottom rail */
    '.hgc-call{position:absolute;left:50%;top:100%;transform:translate(-50%,-26%) scale(var(--hgc-ui,1));transform-origin:50% 26%;',
    'display:flex;align-items:center;gap:16px;',
    'padding:11px 28px 11px 15px;border-radius:18px;white-space:nowrap;',
    'background:linear-gradient(180deg,rgba(32,32,42,.95),rgba(8,8,12,.95));border:1px solid rgba(255,255,255,.14);',
    'box-shadow:0 16px 40px rgba(0,0,0,.6),0 0 0 2px var(--hgc-cc-ring),0 0 30px var(--hgc-cc-glow),inset 0 1px 0 rgba(255,255,255,.08);}',
    '.hgc-call .hgc-pair{display:flex;gap:7px;}',
    '.hgc-call .hgc-md{--d:36px;--pr:3.9px;}',
    '.hgc-cw{display:flex;flex-direction:column;align-items:flex-start;}',
    '.hgc-cc{font-size:40px;font-weight:900;letter-spacing:.04em;line-height:1;color:var(--hgc-cc);',
    'text-shadow:0 2px 0 rgba(0,0,0,.45),0 0 20px var(--hgc-cc-glow);}',
    '.hgc-cs{margin-top:6px;font-size:14px;font-weight:700;letter-spacing:.13em;text-transform:uppercase;color:rgba(255,255,255,.78);}',
    '.hgc-call.hgc-in{animation:hgc-callpop .6s cubic-bezier(.18,.9,.25,1.25) both;}',
    '.hgc-call.hgc-in .hgc-md{animation:hgc-chip .55s .12s cubic-bezier(.2,.9,.3,1.35) both;}',
    /* side boards */
    /* a column as tall as the tray (after its scale): the boards are centred on the tray
       while they fit and grow downwards when they don't. --hgc-sui (set per column by
       _placeSides) = --hgc-ui, or smaller when the column would run off the stage */
    '.hgc-side{position:absolute;top:0;height:calc(100% / var(--hgc-sui,var(--hgc-ui,1)));display:flex;flex-direction:column;',
    'transform:scale(var(--hgc-sui,var(--hgc-ui,1)));}',
    '.hgc-side.hgc-l{right:100%;margin-right:28px;transform-origin:100% 0;}',
    '.hgc-side.hgc-r{left:100%;margin-left:28px;transform-origin:0 0;}',
    '.hgc-sidein{margin:auto 0;display:flex;flex-direction:column;gap:14px;}',
    '.hgc-l .hgc-sidein{align-items:flex-end;}.hgc-r .hgc-sidein{align-items:flex-start;}',
    '.hgc-board{width:max-content;min-width:230px;max-width:310px;padding:11px 15px 10px;border-radius:16px;',
    'background:linear-gradient(180deg,rgba(28,28,38,.93),rgba(8,8,12,.93));border:1px solid rgba(255,255,255,.11);',
    'box-shadow:0 14px 34px rgba(0,0,0,.55),inset 0 1px 0 rgba(255,255,255,.06);}',
    '.hgc-board.hgc-in{animation:hgc-rise .5s cubic-bezier(.2,.9,.3,1.1) both;}',
    '.hgc-bh{display:flex;justify-content:space-between;align-items:baseline;gap:16px;margin-bottom:5px;padding-bottom:7px;',
    'border-bottom:1px solid rgba(255,255,255,.09);}',
    '.hgc-bh b{font-size:12px;letter-spacing:.2em;font-weight:800;color:var(--hgc-accent);white-space:nowrap;}',
    '.hgc-bh span{font-size:12.5px;color:rgba(255,255,255,.62);font-weight:700;white-space:nowrap;font-variant-numeric:tabular-nums;}',
    '.hgc-row{padding:5px 0 4px;}',
    '.hgc-row+.hgc-row{border-top:1px solid rgba(255,255,255,.05);}',
    '.hgc-board.hgc-in .hgc-row{animation:hgc-rise .4s cubic-bezier(.2,.9,.3,1.1) both;}',
    '.hgc-ru{display:flex;align-items:baseline;justify-content:space-between;gap:12px;font-size:16px;white-space:nowrap;}',
    '.hgc-ru b{font-weight:700;color:#fff;max-width:170px;overflow:hidden;text-overflow:ellipsis;}',
    '.hgc-ru span{font-weight:800;color:#fff;font-variant-numeric:tabular-nums;}',
    '.hgc-ru span.hgc-win{color:#3ddc84;}',
    '.hgc-rb{margin-top:2px;font-size:12.5px;color:rgba(255,255,255,.62);line-height:1.35;max-width:280px;',
    'display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;}',
    '.hgc-rb i{font-style:normal;color:rgba(255,255,255,.38);}',
    '.hgc-more,.hgc-none{font-size:12.5px;color:rgba(255,255,255,.55);padding-top:5px;}',
    /* how to play (show_rules): the same board, edged in the theme's accent, only while bets are open.
       A round in two numbered steps (the one the table is on lit), then the bets: name + pays on one
       line, what it means under it; a tip line at the foot */
    '.hgc-board.hgc-rules{width:560px;min-width:0;max-width:none;box-sizing:border-box;border-color:var(--hgc-edge);}',
    '.hgc-rsteps,.hgc-rcols{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);column-gap:14px;}',
    '.hgc-rcols{column-gap:18px;}',
    '.hgc-rh{margin:5px 0 1px;font-size:10.5px;letter-spacing:.18em;font-weight:900;color:var(--hgc-accent);}',
    '.hgc-rh small{margin-left:6px;font-size:10.5px;letter-spacing:.02em;font-weight:600;color:rgba(255,255,255,.45);}',
    '.hgc-st{display:flex;gap:9px;align-items:flex-start;margin:4px 0;padding:6px 9px;border-radius:10px;',
    'font-size:12.5px;line-height:1.38;color:rgba(255,255,255,.6);}',
    '.hgc-st.hgc-on{color:#fff;background:linear-gradient(rgba(0,0,0,.42),rgba(0,0,0,.42)),var(--hgc-accent-a);}',
    '.hgc-st u{flex:0 0 auto;width:19px;height:19px;border-radius:50%;text-decoration:none;display:flex;align-items:center;',
    'justify-content:center;font-size:11px;font-weight:900;color:#111;background:rgba(255,255,255,.55);margin-top:1px;}',
    '.hgc-st.hgc-on u{background:var(--hgc-accent);}',
    '.hgc-st b{display:block;font-size:11.5px;letter-spacing:.14em;font-weight:900;color:inherit;}',
    '.hgc-rl{padding:4px 0 3px;}',
    '.hgc-rl+.hgc-rl,.hgc-st+.hgc-rl{border-top:1px solid rgba(255,255,255,.06);}',
    '.hgc-rn{display:flex;justify-content:space-between;align-items:baseline;gap:10px;font-size:13px;}',
    '.hgc-rn b{font-weight:800;color:#fff;white-space:nowrap;}',
    '.hgc-rn i{font-style:normal;font-weight:800;color:var(--hgc-accent);white-space:nowrap;font-variant-numeric:tabular-nums;}',
    '.hgc-rd{margin-top:1px;font-size:11.5px;line-height:1.33;color:rgba(255,255,255,.66);}',
    '.hgc-rt{margin-top:6px;padding-top:6px;border-top:1px solid rgba(255,255,255,.09);font-size:11.5px;line-height:1.4;',
    'color:rgba(255,255,255,.55);}',
    '.hgc-rt b{color:var(--hgc-accent);font-weight:800;}',
    '.hgc-none{font-size:14.5px;color:rgba(255,255,255,.8);padding:3px 0 2px;}',
    /* Hex's own boards (setAnnounce / table.display_board): same boards, signed amounts,
       free-form titles and text-only lines */
    '.hgc-bh b.hgc-at{text-transform:uppercase;white-space:normal;line-height:1.3;overflow-wrap:anywhere;}',
    '.hgc-ru b.hgc-t{max-width:250px;white-space:normal;line-height:1.3;display:-webkit-box;-webkit-line-clamp:2;',
    '-webkit-box-orient:vertical;overflow-wrap:anywhere;}',
    '.hgc-ru span.hgc-loss{color:#ff6b5e;}',
    '.hgc-ru span.hgc-zero{color:rgba(255,255,255,.45);}',
    /* keyframes */
    '@keyframes hgc-callpop{0%{transform:translate(-50%,-26%) scale(calc(var(--hgc-ui,1) * .3));opacity:0}',
    '55%{transform:translate(-50%,-26%) scale(calc(var(--hgc-ui,1) * 1.08));opacity:1}',
    '78%{transform:translate(-50%,-26%) scale(calc(var(--hgc-ui,1) * .97))}',
    '100%{transform:translate(-50%,-26%) scale(var(--hgc-ui,1));opacity:1}}',
    '@keyframes hgc-rise{0%{opacity:0;transform:translateY(10px) scale(.96)}100%{opacity:1;transform:none}}',
    '@keyframes hgc-chip{0%{transform:scale(.2);opacity:0}70%{transform:scale(1.15);opacity:1}100%{transform:scale(1);opacity:1}}',
    '@keyframes hgc-blink{0%,100%{opacity:1}50%{opacity:.35}}',
    '@keyframes hgc-throb{0%,100%{box-shadow:0 10px 26px rgba(0,0,0,.5)}50%{box-shadow:0 10px 26px rgba(0,0,0,.5),0 0 18px rgba(255,59,48,.55)}}'
  ].join('\n');

  function injectStyle() {
    if (document.getElementById('hgc-style')) return;
    var st = document.createElement('style');
    st.id = 'hgc-style'; st.textContent = STYLE;
    (document.head || document.documentElement).appendChild(st);
  }
  function el(tag, cls, parent) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (parent) parent.appendChild(e);
    return e;
  }
  function miniDie(v, parent) { return el('i', 'hgc-md hgc-v' + v, parent); }
  function perfNow() { return (root.performance && root.performance.now) ? root.performance.now() : Date.now(); }
  var raf = root.requestAnimationFrame ? function (f) { return root.requestAnimationFrame(f); } : function (f) { return setTimeout(function () { f(perfNow()); }, 16); };
  var caf = root.cancelAnimationFrame ? function (id) { root.cancelAnimationFrame(id); } : function (id) { clearTimeout(id); };

  var DEMO_HISTORY = [[3, 4], [5, 5], [2, 6], [1, 1], [6, 5], [4, 2], [3, 3], [6, 1], [2, 3], [5, 4]];
  var DEMO_TABLE = {
    phase: 'point', point: 6, shooter: 'hexcaster', hand_rolls: 2, total_on_table: 485, demo: true,
    bets: [
      { user: 'alice', type: 'pass', amount: 100, odds: 50, working: true },
      { user: 'alice', type: 'place', number: 8, amount: 60, odds: 0, working: true },
      { user: 'bob', type: 'dont_pass', amount: 75, odds: 0, working: true },
      { user: 'bob', type: 'field', amount: 25, odds: 0, working: true },
      { user: 'hexcaster', type: 'hard', number: 6, amount: 20, odds: 0, working: true },
      { user: 'hexcaster', type: 'come', number: 9, amount: 50, odds: 30, working: true },
      { user: 'viewer_42', type: 'yo', amount: 25, odds: 0, working: true },
      { user: 'dicey', type: 'any7', amount: 50, odds: 0, working: true }
    ]
  };

  // ======================================================================
  // Instance
  // ======================================================================
  var M9 = new Float64Array(9);
  var PX = new Float64Array(8), PY = new Float64Array(8);   // projected inner corners of one face
  var PUSH_S = 0.75, PUCK_S = 0.62;

  function newPose(i) {
    return { x: GEOM.IDLE[i][0], y: GEOM.IDLE[i][1], z: HALF, q: IDLE_Q[i].slice(), phase: 5 };
  }

  function Craps(container, config, opts) {
    opts = opts || {};
    this.container = container;
    this.demo = !!opts.demo;
    this.soundOn = opts.sound !== false && !this.demo;
    this.onLanded = null;
    this.cfg = normConfig(config);
    this.eff = this.cfg;
    this.plan = null; this.spin = null; this.mode = 'idle'; this.landed = false;
    this.t = 0; this.prevT = -1; this.evIdx = 0; this.startMs = 0;
    this.pose = [newPose(0), newPose(1)];
    this.order = [0, 1];
    this.blend = null; this.push = null;
    this.table = null; this.pendingTable = null; this.tableSig = null;
    this.history = []; this.pendingHistory = null; this.histSig = '';
    this.puck = { x: GEOM.OFF_X, y: GEOM.OFF_Y, on: false, i: -1, anim: null, hlI: -1, hlT: 0 };
    this.puckHold = false;   // true from a landing until the puck's move (after the call)
    this.callOn = false; this.payOn = false;
    // Hex's own payouts card (setAnnounce): the cleaned announce, the one on screen, and the
    // one a reset / new roll just took down (the host re-sends it at once: no second pop-in)
    this.announce = null; this.annShown = ''; this.annGone = ''; this.annTimer = 0;
    this.cdEnd = 0; this.cdTimer = 0; this.cdOn = false; this.cdMs = null;
    this.k = 0; this.cw = 0; this.ch = 0; this.L = null; this.layersKey = '';
    this.rafId = 0; this.slowId = 0; this.lastNow = 0; this.frameN = 0; this.visible = true; this.lastPollW = 0;
    this.animUntil = 0; this.posSet = false; this.topH = -1; this.topW = -1; this.topBand = null;
    this.timers = []; this._res = null; this.destroyed = false;
    this.sfx = this.soundOn ? new Sfx() : null;
    var self = this;
    this._frame = function (now) { self._onFrame(now); };
    injectStyle();
    this._buildDom();
    this._applyEff();
    this._updatePuck(true);
    this.resize();
    this._kick();
  }
  var RP = Craps.prototype;

  RP._buildDom = function () {
    var c = this.container;
    try {
      if (root.getComputedStyle && root.getComputedStyle(c).position === 'static') { c.style.position = 'relative'; this.posSet = true; }
    } catch (e) { }
    var r = this.root = el('div', 'hgc-root');
    this.canvas = el('canvas', 'hgc-canvas', r);
    this.ctx = this.canvas.getContext('2d');
    this.topBox = el('div', 'hgc-top', r);
    this.histEl = el('div', 'hgc-hist hgc-pillbg hgc-hide', this.topBox);
    this.capRow = el('div', 'hgc-caprow', this.topBox);
    this.capEl = el('div', 'hgc-cap hgc-pillbg hgc-hide', this.capRow);
    // countdown
    var cd = this.cdEl = el('div', 'hgc-cd hgc-pillbg hgc-hide', this.capRow);
    var NS = 'http://www.w3.org/2000/svg', svg = document.createElementNS(NS, 'svg');
    svg.setAttribute('viewBox', '0 0 36 36');
    var c1 = document.createElementNS(NS, 'circle'), c2 = document.createElementNS(NS, 'circle');
    c1.setAttribute('cx', '18'); c1.setAttribute('cy', '18'); c1.setAttribute('r', '15'); c1.setAttribute('class', 'hgc-ring0');
    c2.setAttribute('cx', '18'); c2.setAttribute('cy', '18'); c2.setAttribute('r', '15'); c2.setAttribute('class', 'hgc-ring1');
    c2.setAttribute('stroke-dasharray', '94.25'); c2.setAttribute('stroke-dashoffset', '0');
    svg.appendChild(c1); svg.appendChild(c2); cd.appendChild(svg);
    this.cdRing = c2;
    var tx = el('div', '', cd);
    this.cdLab = el('small', '', tx); this.cdLab.textContent = 'NEXT ROLL';
    this.cdTxt = el('b', '', tx);
    // call banner
    var call = this.callEl = el('div', 'hgc-call hgc-hide', r);
    this.callDice = el('div', 'hgc-pair', call);
    var cw = el('div', 'hgc-cw', call);
    this.callTxt = el('div', 'hgc-cc', cw);
    this.callSub = el('div', 'hgc-cs', cw);
    // side boards
    this.sideL = el('div', 'hgc-sidein', el('div', 'hgc-side hgc-l', r));
    this.sideR = el('div', 'hgc-sidein', el('div', 'hgc-side hgc-r', r));
    this.betsEl = el('div', 'hgc-board hgc-hide', this.sideL);
    this.payEl = el('div', 'hgc-board hgc-hide', this.sideR);
    // how to play: the right column is free while bets are open (the payouts only show after a roll)
    this.rulesEl = el('div', 'hgc-board hgc-rules hgc-hide', this.sideR);
    this.rulesSig = '';
    c.appendChild(r);
  };

  // --- config / theme ------------------------------------------------------
  RP._applyEff = function () {
    var e = this.eff = effConfig(this.cfg, this.spin && this.spin.overrides);
    var th = this.th = THEMES[e.theme] || THEMES.classic;
    var ds = this.ds = DICE_STYLES[e.dice_style] || DICE_STYLES.red;
    var s = this.root.style, acc = hexRgb(th.accent);
    s.setProperty('--hgc-accent', th.accent);
    s.setProperty('--hgc-accent-a', rgba(acc, 0.55));
    s.setProperty('--hgc-edge', rgba(acc, 0.4));
    // text around a small tray stays readable: below scale 0.9 the boards, strips and
    // the call shrink less than the tray (from the placement scale, not the rendered
    // size, so the panel's scaled preview shows the same proportions as OBS)
    this.ui = clamp(0.9 / e.scale, 1, 1.7);
    s.setProperty('--hgc-ui', this.ui.toFixed(3));
    s.setProperty('--hgc-die', ds.mini); s.setProperty('--hgc-die-hi', ds.miniHi);
    s.setProperty('--hgc-die-lo', ds.miniLo); s.setProperty('--hgc-pip', ds.miniPip);
    this._ensureLayers(false);
    this._renderHistory();
    this._renderCaption(false);
    this._renderBets(false);
    this._renderCountdown();
    if (this.callOn) this._renderCall(false);
    if (this.payOn) this._renderPayouts(false);
    this._renderRules(false);
    this._updatePuck(true);
    if (this.sfx) this.sfx.setVolume(e.sfx_volume);
    this._draw();
  };

  RP._ensureLayers = function (force) {
    if (!(this.k > 0)) return;
    var th = this.th, ds = this.ds, k = this.k;
    var key = k.toFixed(3) + '|' + th.label + '|' + ds.label;
    if (!force && key === this.layersKey && this.L) return;
    var old = this.L, L = {};
    if (old && old.trayKey === k.toFixed(3) + th.label) { L.tray = old.tray; L.hl = old.hl; }
    else { L.tray = buildTray(k, th); L.hl = buildHighlight(k, th); }
    L.trayKey = k.toFixed(3) + th.label;
    var TS = Math.round(clamp(2 * AIN * k * 2, 32, 320));
    L.TS = TS; L.faces = [null];
    for (var v = 1; v <= 6; v++) L.faces.push(buildFace(v, TS, ds));
    L.sheen = buildSheen(TS);
    L.shA = buildShadow(k, 2.2, 0.8, ds.shadow);
    L.shB = buildShadow(k, 9, 0.55, ds.shadow);
    L.puckOn = buildPuck(k, true); L.puckOff = buildPuck(k, false);
    // body gradient in unit space (1 = half the die's edge); reused for every die/frame
    var bg = this.ctx.createRadialGradient(-0.42, -0.5, 0.05, -0.1, -0.1, 1.55);
    bg.addColorStop(0, ds.hi); bg.addColorStop(0.42, ds.body); bg.addColorStop(0.8, ds.edge); bg.addColorStop(1, ds.lo);
    L.body = bg;
    L.dark = ds.translucent ? 0.5 : (ds.metal ? 0.62 : 0.66);   // max shading of a face turned away from the light
    this.L = L;
    this.layersKey = key;
  };

  // --- public API ------------------------------------------------------------
  RP.setConfig = function (config) {
    if (this.destroyed) return;
    this.cfg = normConfig(config);
    this._applyEff();
    this.frameN = 0;
    this._kick();
  };

  RP.resize = function () {
    if (this.destroyed) return;
    var r = this.container.getBoundingClientRect(), w = r ? r.width : 0;
    if (!(w > 1)) return;
    var dpr = root.devicePixelRatio || 1, k = w / BASE_W * dpr;
    var fullW = BASE_W + 2 * GEOM.PAD, fullH = BASE_H + 2 * GEOM.PAD;
    if (k * fullW > MAX_W) k = MAX_W / fullW;
    k = clamp(k, 0.15, 8);
    this.lastPollW = w;
    var cw = Math.round(fullW * k), ch = Math.round(fullH * k);
    if (cw !== this.cw || ch !== this.ch || Math.abs(k - this.k) > 1e-3) {
      this.k = cw / fullW; this.cw = cw; this.ch = ch;
      this.canvas.width = cw; this.canvas.height = ch;
      this._ensureLayers(true);
    }
    this.visible = true;
    this._placeSides();
    this._draw();
    this._kick();
  };

  RP.play = function (spin) {
    var self = this;
    if (this.destroyed) return Promise.resolve(null);
    var P = planRoll(spin);
    this._settle(null);
    this._clearTimers();
    if (!P) { this.reset(); return Promise.resolve(null); }
    var el0 = Math.max(0, +(spin && spin.elapsed_ms) || 0), t0 = el0 / 1000;
    var pr = new Promise(function (res) { self._res = res; });
    this._begin(spin, P, el0, true);
    if (t0 >= P.T) {
      this._land(true, true);
    } else {
      if (this.sfx && this.eff.sfx) this.sfx.ensure(this.eff.sfx_volume);
      // landing also fires from a timer, so a throttled/background tab still resolves
      this._later(Math.ceil((P.T - t0) * 1000) + 40, function () { self._catchUp(); });
    }
    this._kick();
    return pr;
  };

  RP.showResult = function (spin) {
    if (this.destroyed) return;
    var P = planRoll(spin);
    if (!P) { this.reset(); return; }   // unusable spin: never leave a stale result up
    this._settle(null);
    this._clearTimers();
    this._begin(spin, P, Math.max(+(spin && spin.elapsed_ms) || 0, P.T * 1000), false);
    this._land(true, false);
    this._kick();
  };

  RP.setHistory = function (results) {
    if (this.destroyed) return;
    var list = Array.isArray(results) ? results.slice(0, 20) : [];
    if (this.mode === 'roll' && !this.landed) { this.pendingHistory = list; return; }
    this.history = list; this.pendingHistory = null;
    this._renderHistory();
  };

  RP.setTable = function (table) {
    if (this.destroyed) return;
    var tb = table && typeof table === 'object' ? table : null;
    if (this.mode === 'roll' && !this.landed) { this.pendingTable = { t: tb, at: perfNow() }; return; }
    this.pendingTable = null;
    this._applyTable(tb, perfNow(), false);
  };

  // Hex's own payouts card (STATE.announce): replaces the computed payouts board for the
  // current result, same look. Held while the dice are in the air (shown with the board at
  // landing); null brings the computed board back (if the roll had settlements) or nothing.
  // A new play()/showResult() and reset() clear it. A card for another roll than the one
  // on screen is stale and never shown as its result. The server takes the card down when
  // it expires; should that never arrive, it goes by itself a little after expires_in_ms.
  RP.setAnnounce = function (announce) {
    if (this.destroyed) return;
    var a = normAnnounce(announce);
    var sid = this.spin ? annStr(this.spin.id, 64) : '';
    if (a && a.spin_id && sid && a.spin_id !== sid) a = null;
    if ((a ? a.sig : '') === (this.announce ? this.announce.sig : '')) return;   // same card
    this.announce = a;
    this._annExpiry(a);
    this._annApply();
  };
  RP._annApply = function () {
    if (this.mode === 'roll') {
      // in the air: held; landed but the board's reveal delay is still running: it shows then
      if (!this.landed || !this.payOn) return;
      this._renderPayouts(true);
      return;
    }
    // idle (the result phase is over, or a fresh overlay): the card stands on its own
    this.payOn = !!this.announce;
    this._renderPayouts(true);
  };
  // safety net behind the server's own expiry (its clearing state normally comes first)
  RP._annExpiry = function (a) {
    if (this.annTimer) { clearTimeout(this.annTimer); this.annTimer = 0; }
    if (!a || a.ttl === null) return;
    var self = this;
    this.annTimer = setTimeout(function () {
      self.annTimer = 0;
      if (self.destroyed || self.announce !== a) return;
      self.announce = null;
      self._annApply();
    }, Math.min(a.ttl + ANN_GRACE_MS, 0x7fffffff));
  };
  RP._dropAnnounce = function () {
    this.annGone = this.annShown;
    this.announce = null;
    this._annExpiry(null);
  };

  RP.reset = function () {
    if (this.destroyed) return;
    this._settle(null);
    this._clearTimers();
    this._dropAnnounce();
    var wasRest = this.mode === 'roll' && this.landed;
    this.mode = 'idle'; this.plan = null; this.spin = null; this.landed = false; this.blend = null;
    this.puckHold = false;
    if (this.pendingHistory) { this.history = this.pendingHistory; this.pendingHistory = null; }
    var pt = this.pendingTable; this.pendingTable = null;
    this._hideResult();
    this._applyEff();
    if (pt) this._applyTable(pt.t, pt.at, true);
    // the stickman pushes the dice back to the shooter's end
    this._pushBack(wasRest && this.visible);
    this._updatePuck(false);
    this._renderCaption(false);
    this._renderCountdown();
    this.frameN = 0;
    this._kick();
  };

  RP.destroy = function () {
    if (this.destroyed) return;
    this._settle(null);
    this._clearTimers();
    this._stopCountdown();
    this.destroyed = true;
    if (this.rafId) { caf(this.rafId); this.rafId = 0; }
    if (this.slowId) { clearTimeout(this.slowId); this.slowId = 0; }
    if (this.sfx) { this.sfx.close(); this.sfx = null; }
    try { this.canvas.width = 0; this.canvas.height = 0; } catch (e) { }   // free the backing store now
    if (this.root && this.root.parentNode) this.root.parentNode.removeChild(this.root);
    if (this.posSet) { try { this.container.style.position = ''; } catch (e) { } this.posSet = false; }
    this.L = null; this.plan = null; this.spin = null; this.pendingHistory = null; this.pendingTable = null;
    this._annExpiry(null); this.announce = null;
    this.onLanded = null;
  };

  // --- roll lifecycle -----------------------------------------------------------
  RP._begin = function (spin, P, elMs, blend) {
    var t = elMs / 1000;
    this._dropAnnounce();   // a new roll: Hex's card for the last one goes
    // the new roll starts from what is on screen now: flush anything held back
    if (this.pendingHistory) { this.history = this.pendingHistory; this.pendingHistory = null; }
    var pt = this.pendingTable; this.pendingTable = null;
    this.push = null;
    // hand-off from the dice on screen into the seeded throw (only while being picked up)
    this.blend = null;
    if (blend && t < P.tPick - 0.03) {
      var bl = { t0: t, t1: P.tPick, dp: [], dq: [] };
      for (var i = 0; i < 2; i++) {
        var cur = this.pose[i], ps = dieState(P, i, t, { q: [1, 0, 0, 0] });
        bl.dp.push([cur.x - ps.x, cur.y - ps.y, cur.z - ps.z]);
        var dq = qmul(cur.q, qconj(ps.q, [1, 0, 0, 0]), [1, 0, 0, 0]);
        if (dq[0] < 0) { dq[0] = -dq[0]; dq[1] = -dq[1]; dq[2] = -dq[2]; dq[3] = -dq[3]; }
        var an = 2 * Math.acos(Math.min(1, dq[0])), sn = Math.sqrt(dq[1] * dq[1] + dq[2] * dq[2] + dq[3] * dq[3]);
        bl.dq.push(sn > 1e-9 ? [dq[1] / sn, dq[2] / sn, dq[3] / sn, an] : [0, 0, 1, 0]);
      }
      this.blend = bl;
    }
    this.spin = spin; this.plan = P; this.mode = 'roll'; this.landed = false; this.puckHold = false;
    this.startMs = perfNow() - elMs;
    this.t = t; this.prevT = -1;
    this.evIdx = 0;
    while (this.evIdx < P.events.length && P.events[this.evIdx].t <= t) this.evIdx++;
    this._hideResult();
    this._renderRules(false);
    this._applyEff();
    if (pt) this._applyTable(pt.t, pt.at, true);
    this._renderHistory();
    this._step(t, true);
    this._updatePuck(true);
    this._renderCaption(true);
    this._renderCountdown();
    this.frameN = 0;
  };

  RP._catchUp = function () {
    if (this.destroyed || this.mode !== 'roll' || this.landed || !this.plan) return;
    this._step((perfNow() - this.startMs) / 1000);
  };

  var BQ = [1, 0, 0, 0];
  // Poses at time t (+ sound events, + the landing once t reaches duration_ms unless
  // `noLand`: _begin only positions the dice, the caller decides how to land).
  RP._step = function (t, noLand) {
    var P = this.plan, i;
    this.t = t;
    for (i = 0; i < 2; i++) dieState(P, i, t, this.pose[i]);
    var b = this.blend;
    if (b) {
      if (t >= b.t1) this.blend = null;
      else {
        var w = 1 - smooth01((t - b.t0) / (b.t1 - b.t0));
        for (i = 0; i < 2; i++) {
          var p = this.pose[i], dp = b.dp[i], dq = b.dq[i];
          p.x += dp[0] * w; p.y += dp[1] * w; p.z += dp[2] * w;
          qaxis(dq[0], dq[1], dq[2], dq[3] * w, BQ); qmul(BQ, p.q, p.q);
        }
      }
    }
    if (this.sfx && this.eff.sfx) this._sound(t);
    this.prevT = t;
    if (!noLand && !this.landed && t >= P.T) this._land(false, true);
  };

  RP._land = function (instant, fire) {
    if (this.landed || !this.plan) return;
    var self = this, spin = this.spin, P = this.plan;
    this.landed = true; this.blend = null;
    // the puck moves after the call, not before it: hold it even when the table after
    // the roll arrived mid-air and is applied right below (it would snap the puck)
    this.puckHold = !instant;
    for (var i = 0; i < 2; i++) dieState(P, i, Math.max(this.t, P.T), this.pose[i]);
    if (this.pendingHistory) { this.history = this.pendingHistory; this.pendingHistory = null; }
    this._renderHistory();
    var pt = this.pendingTable; this.pendingTable = null;
    if (pt) this._applyTable(pt.t, pt.at, true);
    this._renderCaption(false);
    this._renderCountdown();
    if (instant) {
      this.callOn = true; this.payOn = true;
      this._renderCall(false); this._renderPayouts(false);
      this._updatePuck(true);
    } else {
      this._later(150, function () { self.callOn = true; self._renderCall(true); });
      this._later(420, function () { self.puckHold = false; self._updatePuck(false); self._kick(); });
      this._later(650, function () { self.payOn = true; self._renderPayouts(true); });
      this.animUntil = perfNow() + 1400;
    }
    this._settle(spin);
    if (fire && typeof this.onLanded === 'function') {
      try { this.onLanded(spin); } catch (e) { setTimeout(function () { throw e; }, 0); }
    }
  };

  RP._settle = function (v) { if (this._res) { var r = this._res; this._res = null; r(v); } };
  RP._later = function (ms, fn) {
    var self = this, id = setTimeout(function () {
      var i = self.timers.indexOf(id); if (i >= 0) self.timers.splice(i, 1);
      if (!self.destroyed) fn();
    }, ms);
    this.timers.push(id);
  };
  RP._clearTimers = function () { for (var i = 0; i < this.timers.length; i++) clearTimeout(this.timers[i]); this.timers.length = 0; };

  // dice back to the shooter's end (animated when the result was on screen)
  RP._pushBack = function (animate) {
    var p = this.pose;
    // keep the dice in the same vertical order so their paths never cross
    if (p[0].y > p[1].y) { var tmp = p[0]; p[0] = p[1]; p[1] = tmp; }
    var at0 = Math.abs(p[0].x - GEOM.IDLE[0][0]) + Math.abs(p[0].y - GEOM.IDLE[0][1]) + Math.abs(p[0].z - HALF);
    var at1 = Math.abs(p[1].x - GEOM.IDLE[1][0]) + Math.abs(p[1].y - GEOM.IDLE[1][1]) + Math.abs(p[1].z - HALF);
    if (at0 + at1 < 0.5) { this.push = null; return; }
    if (!animate) {
      for (var i = 0; i < 2; i++) {
        if (p[i].phase !== 5) qcopy(IDLE_Q[i], p[i].q);    // aborted mid-air: fresh dice
        p[i].x = GEOM.IDLE[i][0]; p[i].y = GEOM.IDLE[i][1]; p[i].z = HALF; p[i].phase = 5;
      }
      this.push = null; return;
    }
    this.push = { t0: perfNow(), from: [[p[0].x, p[0].y], [p[1].x, p[1].y]] };
  };

  // --- sound -------------------------------------------------------------------
  RP._sound = function (t) {
    var P = this.plan, sf = this.sfx;
    var cont = this.prevT >= 0 && t >= this.prevT && t - this.prevT < 0.25;
    var ev = P.events;
    while (this.evIdx < ev.length && ev[this.evIdx].t <= t) {
      var e = ev[this.evIdx++];
      if (cont && e.t > this.prevT) sf.hit(e.kind, e.vol, e.rate);
    }
  };

  // --- table / puck --------------------------------------------------------------
  RP._applyTable = function (tb, at, quiet) {
    this.table = tb;
    // countdown: remaining ms measured when the message was built, ticking locally
    var ms = tb && typeof tb.auto_roll_in_ms === 'number' && isFinite(tb.auto_roll_in_ms) ? Math.max(0, tb.auto_roll_in_ms) : null;
    if (ms == null) this.cdMs = null;
    else {
      var end = at + ms;
      if (this.cdMs == null || Math.abs(end - this.cdEnd) > 600) { this.cdEnd = end; this.cdStart = at; }
      this.cdMs = ms;
    }
    var sig = tb ? JSON.stringify([tb.phase, tb.point, tb.shooter, tb.hand_rolls, tb.total_on_table, tb.currency, tb.bets,
      tb.display_board]) : '';
    var first = this.tableSig === null;           // the first table this instance sees
    var changed = sig !== this.tableSig;
    this.tableSig = sig;
    if (changed) {
      this._renderBets(!quiet);
      this._renderRules(!quiet);
      this._renderCaption(false);
      // the puck only travels when the point changes, not when a fresh instance (the
      // overlay re-creates it each time the game is shown) learns where it already is
      this._updatePuck(quiet || first);
    }
    this._renderCountdown();
    this._kick();
  };

  RP._tableShown = function () {
    if (this.table) return this.table;
    return this.demo ? DEMO_TABLE : null;
  };

  // point the puck shows right now: the roll on screen decides while one is up
  RP._displayPoint = function () {
    var s = this.spin, res = s && s.result;
    if (s && res && res.phase_before != null) {
      if (this.landed) return res.phase_after === 'point' ? +res.point_after : null;
      return res.phase_before === 'point' ? +res.point_before : null;
    }
    var tb = this._tableShown();
    return tb && tb.phase === 'point' && tb.point != null ? +tb.point : null;
  };

  RP._updatePuck = function (instant) {
    if (this.puckHold) return;                    // landing: moves once the call is up
    var tgt = puckSpot(this._displayPoint()), pk = this.puck;
    var endX = pk.anim ? pk.anim.x1 : pk.x, endY = pk.anim ? pk.anim.y1 : pk.y, endOn = pk.anim ? pk.anim.on1 : pk.on;
    if (Math.abs(endX - tgt.x) < 0.01 && Math.abs(endY - tgt.y) < 0.01 && endOn === tgt.on) return;
    if (instant || !this.visible) {
      pk.anim = null; pk.x = tgt.x; pk.y = tgt.y; pk.on = tgt.on; pk.i = tgt.i;
      pk.hlI = tgt.i; pk.hlT = perfNow() - 1000;
      this._draw();
      return;
    }
    pk.anim = { t0: perfNow(), x0: pk.x, y0: pk.y, on0: pk.on, x1: tgt.x, y1: tgt.y, on1: tgt.on, i1: tgt.i };
    pk.hlI = -1;
    this._kick();
  };

  // --- DOM overlays ---------------------------------------------------------------
  RP._shooter = function () {
    var tb = this._tableShown(), s = this.spin, u = '';
    var rollerOf = s ? userName(s.shooter || s.user || '') : '';
    if (s && this.mode === 'roll' && !this.landed) u = rollerOf || userName(tb && tb.shooter);   // who throws now
    else {
      // after a real seven out the hand is over: the next roller shoots (nobody yet),
      // even if the table after the roll hasn't arrived yet
      var res = this.landed && s && s.result;
      if (res && res.event === 'seven_out' && !s.test) u = '';
      else if (tb) u = userName(tb.shooter) || rollerOf;   // no shooter on the table yet: the roller
      else u = rollerOf;
    }
    var hr = tb && typeof tb.hand_rolls === 'number' && isFinite(tb.hand_rolls) ? tb.hand_rolls + 1 : null;
    return { user: u, roll: hr, demo: !!(tb && tb.demo) };
  };

  // the strip above the tray: re-fit it (and the boards beside it) when its size changes
  RP._renderCaption = function (anim) { this._caption(anim); this._topChanged(); };
  RP._renderCountdown = function () { this._countdown(); this._topChanged(); };
  RP._renderHistory = function () { this._history(); this._topChanged(); };

  RP._caption = function (anim) {
    var c = this.capEl, sh = this._shooter();
    if (!this.eff.show_user || !sh.user) { c.classList.add('hgc-hide'); c.removeAttribute('data-k'); return; }
    var live = this.mode === 'roll' && !this.landed;
    var key = sh.user + '|' + sh.roll + '|' + live;
    if (c.getAttribute('data-k') !== key) {
      c.textContent = '';
      el('i', '', c);
      var b = el('b', '', c); b.textContent = '@' + sh.user;
      c.appendChild(document.createTextNode(live ? 'rolls the dice' : 'is shooting'));
      if (sh.roll) { var em = el('em', '', c); em.textContent = '\u00B7 roll ' + sh.roll; }
      c.setAttribute('data-k', key);
    }
    c.classList.toggle('hgc-live', live);
    c.classList.toggle('hgc-demo', sh.demo);
    var was = c.classList.contains('hgc-hide');
    c.classList.remove('hgc-hide');
    if (anim || was) { c.classList.remove('hgc-in'); void c.offsetWidth; c.classList.add('hgc-in'); }
  };

  RP._countdown = function () {
    var on = this.cdMs != null && !(this.mode === 'roll' && !this.landed);
    var c = this.cdEl;
    if (!on) { c.classList.add('hgc-hide'); this._stopCountdown(); this.cdOn = false; return; }
    var total = Math.max(this.eff.bet_window_seconds * 1000, this.cdEnd - (this.cdStart || this.cdEnd));
    var left = Math.max(0, this.cdEnd - perfNow());
    var ring = this.cdRing, C = 94.25;
    c.classList.remove('hgc-hide');
    if (!this.cdOn || this.cdRingEnd !== this.cdEnd) {
      // restart the ring: jump to the current fraction, then run down linearly on the compositor
      ring.style.transition = 'none';
      ring.style.strokeDashoffset = String(C * (1 - clamp(left / total, 0, 1)));
      void ring.getBoundingClientRect();
      ring.style.transition = 'stroke-dashoffset ' + Math.round(left) + 'ms linear';
      ring.style.strokeDashoffset = String(C);
      this.cdRingEnd = this.cdEnd;
    }
    this.cdOn = true;
    c.classList.remove('hgc-hide');
    this._tickCountdown();
  };
  RP._tickCountdown = function () {
    var self = this;
    if (this.cdTimer) { clearTimeout(this.cdTimer); this.cdTimer = 0; }
    if (!this.cdOn || this.destroyed) return;
    var left = Math.max(0, this.cdEnd - perfNow());
    var s = Math.ceil(left / 1000);
    if (left <= 0) { this.cdLab.textContent = 'DICE COMING OUT'; this.cdTxt.textContent = 'NO MORE BETS'; }
    else { this.cdLab.textContent = 'NEXT ROLL'; this.cdTxt.textContent = Math.floor(s / 60) + ':' + ('0' + (s % 60)).slice(-2); }
    this.cdEl.classList.toggle('hgc-urgent', left > 0 && left <= 5000);
    if (left > 0) this.cdTimer = setTimeout(function () { self.cdTimer = 0; self._tickCountdown(); }, (left % 1000) + 15);
  };
  RP._stopCountdown = function () { if (this.cdTimer) { clearTimeout(this.cdTimer); this.cdTimer = 0; } };

  RP._renderBets = function (anim) {
    var e = this.eff, box = this.betsEl, tb = this._tableShown();
    // Hex keeps the bets ("Hex does the math"): its board replaces the computed one
    var db = tb && normBoard(tb.display_board);
    if (db) { this._renderBoard(db, anim); return; }
    var bets = tb && Array.isArray(tb.bets) ? tb.bets : [];
    if (!e.show_bets || !bets.length) { box.classList.add('hgc-hide'); this._placeSides(); return; }
    // no prototype: a chatter may well be called "constructor" or "toString"
    var cur = e.currency, users = Object.create(null), order = [], i, total = 0;
    for (i = 0; i < bets.length; i++) {
      var b = bets[i] || {}, u = userName(b.user) || 'anon';
      if (!users[u]) { users[u] = { user: u, total: 0, items: [] }; order.push(users[u]); }
      var amt = +b.amount || 0, odds = +b.odds || 0;
      users[u].total += amt + odds; total += amt + odds;
      // "working: false" on a come bet means only its odds are off (come-out roll);
      // place / hard bets are off as a whole
      var oddsOff = odds > 0 && (b.odds_working === false || (b.type === 'come' && b.working === false));
      users[u].items.push({
        text: betShort(b) + ' ' + fmtNum(amt), odds: odds > 0 ? '+' + fmtNum(odds) + ' odds' : '',
        off: b.working === false && !(b.type === 'come' && odds > 0), oddsOff: oddsOff
      });
    }
    if (typeof tb.total_on_table === 'number' && isFinite(tb.total_on_table)) total = tb.total_on_table;
    order.sort(function (a, b) { return b.total - a.total; });
    box.textContent = '';
    var hd = el('div', 'hgc-bh', box);
    el('b', '', hd).textContent = 'ON THE TABLE';
    el('span', '', hd).textContent = fmtNum(total) + ' ' + cur;
    var n = Math.min(order.length, e.bets_max);
    for (i = 0; i < n; i++) {
      var o = order[i], row = el('div', 'hgc-row', box);
      row.style.animationDelay = (0.05 + i * 0.05).toFixed(2) + 's';
      var ru = el('div', 'hgc-ru', row);
      el('b', '', ru).textContent = '@' + o.user;
      el('span', '', ru).textContent = fmtNum(o.total);
      var rb = el('div', 'hgc-rb', row);
      for (var j = 0; j < o.items.length; j++) {
        var it = o.items[j];
        if (j) rb.appendChild(document.createTextNode(' \u00B7 '));
        if (it.off) { el('i', '', rb).textContent = it.text + (it.odds ? ' ' + it.odds : '') + ' (off)'; continue; }
        rb.appendChild(document.createTextNode(it.text));
        if (!it.odds) continue;
        if (it.oddsOff) el('i', '', rb).textContent = ' ' + it.odds + ' (off)';
        else rb.appendChild(document.createTextNode(' ' + it.odds));
      }
    }
    if (order.length > n) el('div', 'hgc-more', box).textContent = '+' + (order.length - n) + ' more';
    box.classList.toggle('hgc-demo', !!tb.demo);
    box.classList.remove('hgc-hide');
    if (anim) { box.classList.remove('hgc-in'); void box.offsetWidth; box.classList.add('hgc-in'); }
    else box.classList.remove('hgc-in');
    this._placeSides();
  };

  // table.display_board, in the bets board's place and style: title + total, one row per
  // line as Hex sent it (@user amount / text) up to bets_max, "+N more". No lines: no board
  // (like the computed board with nothing down), and the computed one stays away.
  RP._renderBoard = function (db, anim) {
    var e = this.eff, box = this.betsEl, i;
    if (!e.show_bets || !db.lines.length) { box.classList.add('hgc-hide'); this._placeSides(); return; }
    box.textContent = '';
    var hd = el('div', 'hgc-bh', box);
    el('b', 'hgc-at', hd).textContent = db.title;
    if (db.showTotal) el('span', '', hd).textContent = fmtNum(db.total) + ' ' + e.currency;
    var n = Math.min(db.lines.length, e.bets_max);
    for (i = 0; i < n; i++) {
      var ln = db.lines[i], row = el('div', 'hgc-row', box);
      row.style.animationDelay = (0.05 + i * 0.05).toFixed(2) + 's';
      this._annRow(row, ln, ln.amount != null ? fmtNum(ln.amount) : null, '');
    }
    if (db.lines.length > n) el('div', 'hgc-more', box).textContent = '+' + (db.lines.length - n) + ' more';
    box.classList.remove('hgc-demo');
    box.classList.remove('hgc-hide');
    if (anim) { box.classList.remove('hgc-in'); void box.offsetWidth; box.classList.add('hgc-in'); }
    else box.classList.remove('hgc-in');
    this._placeSides();
  };

  // one board row from Hex's data: "@user   amount" with the text below it, or the text
  // alone on the first line when there is no user
  RP._annRow = function (row, ln, amtText, amtCls) {
    var ru = el('div', 'hgc-ru', row);
    if (ln.user) el('b', '', ru).textContent = '@' + ln.user;
    else el('b', 'hgc-t', ru).textContent = ln.text;
    if (amtText != null) el('span', amtCls, ru).textContent = amtText;
    if (ln.user && ln.text) el('div', 'hgc-rb', row).textContent = ln.text;
  };

  RP._result = function () {
    var s = this.spin, P = this.plan;
    if (!s || !P) return null;
    var r = s.result || {}, d = P.dice;
    // what the dice show is what the plan landed on; strings from the server when present
    var fb = resultFor(d[0], d[1], r.phase_before, r.point_before) || {};
    var o = {}, k;
    for (k in fb) o[k] = fb[k];
    for (k in r) if (has(r, k) && r[k] != null) o[k] = r[k];
    o.dice = d.slice();
    return o;
  };

  RP._renderCall = function (anim) {
    var c = this.callEl, res = this._result();
    if (!res || !this.landed) { c.classList.add('hgc-hide'); return; }
    var ev = res.event, win = ev === 'natural' || ev === 'point_made', lose = ev === 'seven_out' || ev === 'craps';
    var col = win ? '#ffe27a' : (lose ? '#ff7266' : (ev === 'point_set' ? '#ffffff' : '#ffffff'));
    var glow = win ? 'rgba(255,205,70,.5)' : (lose ? 'rgba(255,59,48,.5)' : (ev === 'point_set' ? this.th.accent : 'rgba(255,255,255,.14)'));
    var ring = win ? 'rgba(255,214,90,.75)' : (lose ? 'rgba(255,80,68,.75)' : (ev === 'point_set' ? this.th.accent : 'rgba(255,255,255,.12)'));
    var s = c.style;
    s.setProperty('--hgc-cc', col); s.setProperty('--hgc-cc-glow', glow); s.setProperty('--hgc-cc-ring', ring);
    this.callDice.textContent = '';
    miniDie(res.dice[0], this.callDice); miniDie(res.dice[1], this.callDice);
    this.callTxt.textContent = String(res.call || res.total || '');
    this.callSub.textContent = String(res.sub || '');
    this.callSub.classList.toggle('hgc-hide', !res.sub);
    c.classList.remove('hgc-hide');
    if (anim) { c.classList.remove('hgc-in'); void c.offsetWidth; c.classList.add('hgc-in'); }
    else c.classList.remove('hgc-in');
  };

  RP._renderPayouts = function (anim) {
    var e = this.eff, s = this.spin, box = this.payEl;
    // Hex's own card replaces the computed one (never while the dice are in the air)
    if (this.announce && !(this.mode === 'roll' && !this.landed)) { this._renderAnnounce(anim); return; }
    this.annShown = '';
    var st = s && Array.isArray(s.settlements) ? s.settlements : [];
    if (!e.show_payouts || !this.landed || !st.length) { box.classList.add('hgc-hide'); this._placeSides(); return; }
    var users = Object.create(null), wins = [], lost = 0, i, paid = 0;
    for (i = 0; i < st.length; i++) {
      var x = st[i] || {};
      // (a come bet losing on a come-out 7 gets its odds back: `lost` is the flat only)
      if (x.outcome === 'lose') lost += typeof x.lost === 'number' && isFinite(x.lost) ? x.lost : (+x.amount || 0) + (+x.odds || 0);
      if (x.outcome !== 'win' || !(+x.won > 0)) continue;
      var u = userName(x.user) || 'anon';
      if (!users[u]) { users[u] = { user: u, won: 0, labels: [] }; wins.push(users[u]); }
      users[u].won += +x.won; paid += +x.won;
      var lab = x.label || betShort(x);
      if (users[u].labels.indexOf(lab) < 0) users[u].labels.push(lab);
    }
    var sum = s.summary || {};
    if (typeof sum.total_won === 'number') paid = sum.total_won;
    if (typeof sum.total_lost === 'number') lost = sum.total_lost;
    wins.sort(function (a, b) { return b.won - a.won; });
    var cur = e.currency;
    box.textContent = '';
    var hd = el('div', 'hgc-bh', box);
    el('b', '', hd).textContent = wins.length ? (wins.length === 1 ? 'WINNER' : 'WINNERS') : 'NO WINNERS';
    el('span', '', hd).textContent = wins.length ? '+' + fmtNum(paid) + ' ' + cur : '';
    var n = Math.min(wins.length, e.payouts_max);
    for (i = 0; i < n; i++) {
      var w = wins[i], row = el('div', 'hgc-row', box);
      row.style.animationDelay = (0.08 + i * 0.07).toFixed(2) + 's';
      var ru = el('div', 'hgc-ru', row);
      el('b', '', ru).textContent = '@' + w.user;
      var sp = el('span', 'hgc-win', ru); sp.textContent = '+' + fmtNum(w.won) + ' ' + cur;
      el('div', 'hgc-rb', row).textContent = w.labels.join(' \u00B7 ');
    }
    if (wins.length > n) el('div', 'hgc-more', box).textContent = '+' + (wins.length - n) + ' more';
    if (!wins.length) el('div', 'hgc-none', box).textContent = lost > 0 ? 'House collects ' + fmtNum(lost) + ' ' + cur : 'No winners this roll';
    box.classList.remove('hgc-hide');
    if (anim) { box.classList.remove('hgc-in'); void box.offsetWidth; box.classList.add('hgc-in'); }
    else box.classList.remove('hgc-in');
    this._placeSides();
  };

  // Hex's card, in the payouts board's place and style: title, up to payouts_max lines
  // (@user, signed amount + currency, text) + "+N more", or its empty_text.
  RP._renderAnnounce = function (anim) {
    var e = this.eff, a = this.announce, box = this.payEl, i;
    if (!e.show_payouts) { box.classList.add('hgc-hide'); this.annShown = ''; this._placeSides(); return; }
    // the same card again right after a reset / new roll took it down: no second pop-in
    if (anim && a.sig === this.annGone) anim = false;
    this.annGone = '';
    this.annShown = a.sig;
    var cur = a.currency == null ? e.currency : a.currency;
    box.textContent = '';
    var hd = el('div', 'hgc-bh', box);
    el('b', 'hgc-at', hd).textContent = a.title;
    var n = Math.min(a.lines.length, e.payouts_max);
    for (i = 0; i < n; i++) {
      var ln = a.lines[i], row = el('div', 'hgc-row', box);
      row.style.animationDelay = (0.08 + i * 0.07).toFixed(2) + 's';
      var withAmt = ln.amount != null;
      this._annRow(row, ln, withAmt ? annAmountText(ln.amount, cur) : null, withAmt ? annAmountClass(ln.amount) : '');
    }
    if (a.lines.length > n) el('div', 'hgc-more', box).textContent = '+' + (a.lines.length - n) + ' more';
    if (!a.lines.length) el('div', 'hgc-none', box).textContent = a.empty_text;
    box.classList.remove('hgc-hide');
    if (anim) { box.classList.remove('hgc-in'); void box.offsetWidth; box.classList.add('hgc-in'); }
    else box.classList.remove('hgc-in');
    this._placeSides();
  };

  // Bets board left, payouts right — unless the tray sits too close to that edge of
  // the stage (then both share the other side). A column that still doesn't fit (a big
  // tray) shrinks to the room it has, down to a readable floor, and only then slides in
  // over the tray's edge; vertically it stays on the stage. All in tray px, measured
  // against the container's offsetParent (the 1920x1080 stage in OBS and the panel).
  var SIDE_GAP = 28, TOP_GAP = 16, SIDE_EDGE = 10, SIDE_MIN = 0.75;   // SIDE_MIN: size on the stage (x design)
  // room between the tray and the stage's edges, in tray px (null: not measurable)
  RP._room = function () {
    try {
      var cr = this.container.getBoundingClientRect(), par = this.container.offsetParent || document.body;
      var pr = par.getBoundingClientRect(), sc = cr.width / BASE_W;
      if (pr.width > 0 && pr.height > 0 && sc > 0) {
        return { l: (cr.left - pr.left) / sc, r: (pr.right - cr.right) / sc, t: (cr.top - pr.top) / sc, b: (pr.bottom - cr.bottom) / sc };
      }
    } catch (err) { }
    return null;
  };
  // the history strip + caption row above the tray shrink to the room above it too
  RP._fitTop = function (R) {
    if (R === undefined) R = this._room();
    var top = this.topBox, ui = this.ui || 1, s = ui, h = top.offsetHeight, w = top.offsetWidth;
    if (R && h > 0) {
      var avail = R.t - TOP_GAP - SIDE_EDGE;
      if (h * s > avail) s = Math.min(ui, Math.max(avail / h, SIDE_MIN / (this.eff.scale || 1)));
    }
    top.style.setProperty('--hgc-tui', s.toFixed(3));
    this.topH = h; this.topW = w;
    this.topBand = { h: h * s, w: w * s };        // its size above the tray, in tray px
  };
  // after the strip's content changed (history / caption / countdown shown or hidden)
  RP._topChanged = function () {
    var t = this.topBox;
    if (t.offsetHeight !== this.topH || t.offsetWidth !== this.topW) this._placeSides();
  };
  RP._placeSides = function () {
    var bets = this.betsEl, pay = this.payEl, ui = this.ui || 1, R = this._room();
    this._fitTop(R);
    function wOf(b) { return b.classList.contains('hgc-hide') ? 0 : b.offsetWidth; }
    function fits(room, w) { return !R || room >= SIDE_GAP + (w || 300) * ui + SIDE_EDGE; }
    var rules = this.rulesEl, wB = wOf(bets), wP = wOf(pay), wR = wOf(rules);
    var bl = fits(R && R.l, wB) || !fits(R && R.r, wB), pl = !fits(R && R.r, wP) && fits(R && R.l, wP);
    var rl = !fits(R && R.r, wR) && fits(R && R.l, wR);
    var wantB = bl ? this.sideL : this.sideR, wantP = pl ? this.sideL : this.sideR, wantR = rl ? this.sideL : this.sideR;
    if (bets.parentNode !== wantB) wantB.insertBefore(bets, wantB.firstChild);
    if (pay.parentNode !== wantP) wantP.appendChild(pay);
    if (rules.parentNode !== wantR) wantR.appendChild(rules);
    this._fitSide(this.sideL, R, R && R.l, true);
    this._fitSide(this.sideR, R, R && R.r, false);
  };
  RP._fitSide = function (inner, R, room, left) {
    var col = inner.parentNode, ui = this.ui || 1, s = ui, w = 0, dx = 0, dy = 0, i, kids = inner.children;
    for (i = 0; i < kids.length; i++) if (!kids[i].classList.contains('hgc-hide')) w = Math.max(w, kids[i].offsetWidth);
    if (R && w > 0) {
      var avail = room - SIDE_GAP - SIDE_EDGE;
      if (w * s > avail) {
        s = Math.min(ui, Math.max(avail / w, SIDE_MIN / (this.eff.scale || 1)));
        if (w * s > avail) dx = w * s - avail;          // still too wide: over the tray's edge
      }
      // centred on the tray while it fits, else growing down — but never off the stage
      var h = inner.offsetHeight * s, top = h <= BASE_H ? (BASE_H - h) / 2 : 0;
      var lo = SIDE_EDGE - R.t, hi = BASE_H + R.b - SIDE_EDGE;
      if (top + h > hi) dy = hi - (top + h);
      if (top + dy < lo) dy = lo - top;
      // lifted up beside the strip above the tray (a small tray low on the stage): when
      // that strip is wider than the tray, step out sideways so it isn't covered
      var band = this.topBand;
      if (band && band.h > 0 && top + dy < -TOP_GAP + 2) {
        var clear = (band.w - BASE_W) / 2 + 8 - (SIDE_GAP - dx);            // strip overhang past the column's edge
        var spare = room - SIDE_EDGE - (SIDE_GAP - dx) - w * s;               // room left beyond the column
        if (clear > 0 && spare > 0) dx -= Math.min(clear, spare);
      }
    }
    col.style.setProperty('--hgc-sui', s.toFixed(3));
    col.style[left ? 'marginRight' : 'marginLeft'] = (SIDE_GAP - dx).toFixed(1) + 'px';
    inner.style.transform = dy ? 'translateY(' + (dy / s).toFixed(1) + 'px)' : '';
  };

  // How to play (show_rules): what the coming roll means (come-out or the point), then the
  // bets, one line each. Only while no roll is up - bets are open and the column is free.
  // [name, pays, what it means]: the bets that stay on the table until they win or lose |
  // the one-roll bets (decided by the very next roll)
  var RULE_STAY = [
    ['Pass', '1:1', 'Wins by the two steps above. The easiest bet - start here.'],
    ["Don't Pass", '1:1', 'The opposite of Pass (a 12 on the first roll is a tie).'],
    ["Come / Don't Come", '1:1', 'The same two bets, started on any roll after the point is set.'],
    ['Odds', '2:1 3:2 6:5', 'Extra behind Pass or Come once they have a number. Pays 4/10, 5/9, 6/8. No house edge.'],
    ['Lay odds', '1:2 2:3 5:6', "The same, behind Don't Pass or Don't Come."],
    ['Place 4-10', '9:5 7:5 7:6', 'Your number before a 7, again and again - the bet stays up.'],
    ['Hard way', '7:1 9:1', 'Your number as a pair (e.g. 4+4 for 8) before a 7 or the easy way.']
  ];
  var RULE_ONE = [
    ['Field', '1:1+', '2, 3, 4, 9, 10, 11 or 12 wins; 5, 6, 7 or 8 loses.'],
    ['Any 7', '4:1', 'Any 7.'],
    ['Any Craps', '7:1', 'A 2, 3 or 12.'],
    ['Aces', '30:1', 'A 2 (1+1, snake eyes).'],
    ['Ace-Deuce', '15:1', 'A 3.'],
    ['Yo', '15:1', 'An 11.'],
    ['Boxcars', '30:1', 'A 12 (6+6).'],
    ['Horn', '27:4 3:1', 'Split over 2, 3, 11 and 12 (bet a multiple of 4).'],
    ['C&E', '3:1 7:1', 'Any craps plus 11 (bet an even amount).']
  ];
  function ruleColumn(box, title, sub, rows) {
    var col = el('div', '', box), h = el('div', 'hgc-rh', col);
    h.appendChild(document.createTextNode(title));
    el('small', '', h).textContent = sub;
    for (var i = 0; i < rows.length; i++) {
      var r = rows[i], rw = el('div', 'hgc-rl', col), rn = el('div', 'hgc-rn', rw);
      el('b', '', rn).textContent = r[0];
      el('i', '', rn).textContent = r[1];
      el('div', 'hgc-rd', rw).textContent = r[2];
    }
  }
  function ruleStep(box, n, on, title, text) {
    var st = el('div', 'hgc-st' + (on ? ' hgc-on' : ''), box);
    el('u', '', st).textContent = String(n);
    var tx = el('div', '', st);
    el('b', '', tx).textContent = title;
    tx.appendChild(document.createTextNode(text));
  }
  RP._renderRules = function (anim) {
    var e = this.eff, box = this.rulesEl;
    if (!e.show_rules || this.mode !== 'idle') {
      if (!box.classList.contains('hgc-hide')) { box.classList.add('hgc-hide'); this._placeSides(); }
      return;
    }
    var tb = this._tableShown(), pt = tb && tb.phase === 'point' ? +tb.point : 0;
    var point = pt >= 4 && pt <= 10 && pt !== 7 ? pt : 0;
    var sig = point + '|' + e.field_12_pays, hidden = box.classList.contains('hgc-hide');
    if (sig !== this.rulesSig) {
      this.rulesSig = sig;
      box.textContent = '';
      var hd = el('div', 'hgc-bh', box);
      el('b', '', hd).textContent = 'HOW TO PLAY';
      el('span', '', hd).textContent = 'Craps';
      // a round, in two steps side by side: the one the table is on now is lit
      var steps = el('div', 'hgc-rsteps', box);
      ruleStep(steps, 1, !point, 'FIRST ROLL', '7 or 11: Pass wins. 2, 3 or 12: Pass loses. Any other number becomes the point.');
      ruleStep(steps, 2, !!point, point ? 'THE POINT IS ' + point : 'THEN',
        point ? 'Roll a ' + point + ' again before a 7: Pass wins. A 7 first: Pass loses, new round.'
          : 'Keep rolling. The point again before a 7: Pass wins. A 7 first: Pass loses.');
      var cols = el('div', 'hgc-rcols', box);
      ruleColumn(cols, 'STAY UP', 'until they win or lose', RULE_STAY);
      ruleColumn(cols, 'ONE ROLL', 'the very next roll only', RULE_ONE);
      var tip = el('div', 'hgc-rt', box);
      el('b', '', tip).textContent = '7:6';
      tip.appendChild(document.createTextNode(' means bet 6 to win 7. Field: a 2 pays 2:1, a 12 pays ' +
        (+e.field_12_pays === 2 ? '2:1' : '3:1') + '. Horn and C&E pay on the part that wins.'));
    }
    if (hidden) {
      box.classList.remove('hgc-hide');
      if (anim) { box.classList.remove('hgc-in'); void box.offsetWidth; box.classList.add('hgc-in'); }
    }
    this._placeSides();
  };

  RP._hideResult = function () {
    this.callOn = false; this.payOn = false;
    this.callEl.classList.add('hgc-hide'); this.callEl.classList.remove('hgc-in');
    this.payEl.classList.add('hgc-hide');
    this.annShown = '';
  };

  RP._history = function () {
    var e = this.eff, h = this.histEl;
    if (!e.show_history) { h.classList.add('hgc-hide'); return; }
    var list = this.history, demo = false, i;
    if (!list.length && this.demo) { list = DEMO_HISTORY.map(function (d) { return { dice: d, total: d[0] + d[1] }; }); demo = true; }
    var n = Math.min(list.length, e.history_count), items = [];
    for (i = 0; i < n; i++) {
      var r = list[i];
      if (r && typeof r === 'object' && r.result && typeof r.result === 'object') r = r.result;   // SPIN objects too
      var d = diceOf(r), tot = d ? d[0] + d[1] : (r && isFinite(+(r.total != null ? r.total : r.number)) ? +(r.total != null ? r.total : r.number) : null);
      if (tot == null) continue;
      items.push({ d: d, t: tot, pm: !!(r && r.event === 'point_made') });
    }
    if (!items.length) { h.classList.add('hgc-hide'); this.histSig = ''; return; }
    var sig = (demo ? 'd:' : '') + JSON.stringify(items);
    h.classList.remove('hgc-hide');
    h.classList.toggle('hgc-demo', demo);
    if (sig === this.histSig) return;
    var prev = this.histSig;
    var animate = !demo && prev !== '' && prev.indexOf('d:') !== 0;
    this.histSig = sig;
    h.textContent = '';
    for (i = 0; i < items.length; i++) {
      var it = items[i], cls = 'hgc-hi' + (it.t === 7 ? ' hgc-7' : '') + (it.pm ? ' hgc-pm' : '') + (i === 0 ? ' hgc-new' : '');
      var box = el('div', cls, h);
      if (it.d) { var pair = el('div', 'hgc-pair', box); miniDie(it.d[0], pair); miniDie(it.d[1], pair); }
      el('b', '', box).textContent = String(it.t);
    }
    h.classList.remove('hgc-anim');
    if (animate) { void h.offsetWidth; h.classList.add('hgc-anim'); }
  };

  // --- frame loop ---------------------------------------------------------------
  RP._kick = function () {
    if (this.destroyed || this.rafId) return;
    if (this.slowId) { clearTimeout(this.slowId); this.slowId = 0; }
    this.rafId = raf(this._frame);
  };
  RP._animating = function (now) {
    if (this.mode === 'roll' && !this.landed) return true;
    if (this.push || this.puck.anim) return true;
    if (now < this.animUntil) return true;
    var pk = this.puck;
    if (pk.on && pk.hlI >= 0 && now - pk.hlT < 400) return true;
    return false;
  };
  // RAF only while something moves (and is visible); otherwise park and check the
  // size/visibility with a cheap 1 Hz timer.
  RP._next = function () {
    if (this.destroyed || this.rafId || this.slowId) return;
    var now = perfNow();
    if (this._animating(now) && this.visible) this.rafId = raf(this._frame);
    else {
      var self = this;
      this.slowId = setTimeout(function () { self.slowId = 0; self._onFrame(perfNow(), true); }, this._animating(now) ? 250 : 1000);
    }
  };

  RP._poll = function () {
    var w = 0;
    try { w = this.container.getBoundingClientRect().width; } catch (e) { }
    var hidden = (typeof document !== 'undefined' && document.hidden) || !(w > 1) || this.root.offsetParent === null;
    var was = this.visible;
    this.visible = !hidden;
    if (hidden) return;
    var dpr = root.devicePixelRatio || 1, want = w / BASE_W * dpr;
    if (!this.k || !was || (Math.abs(want - this.k) / this.k > 0.08 && Math.abs(w - this.lastPollW) < 0.02 * w)) this.resize();
    this.lastPollW = w;
  };

  RP._onFrame = function (now, slow) {
    this.rafId = 0;
    if (this.destroyed) return;
    var n = this.frameN++;
    if (slow || n % 12 === 0) this._poll();
    now = perfNow();
    if (this.mode === 'roll' && this.plan && !this.landed) this._step((now - this.startMs) / 1000);
    this._animate(now);
    // the idle 1 Hz check only looks at size/visibility (resize() redraws itself)
    if (this.visible && (!slow || this._animating(now))) this._draw();
    this._next();
  };

  RP._animate = function (now) {
    var pk = this.puck, a = pk.anim;
    if (a) {
      var u = (now - a.t0) / (PUCK_S * 1000);
      if (u >= 1) {
        pk.anim = null; pk.x = a.x1; pk.y = a.y1; pk.on = a.on1; pk.i = a.i1; pk.hlI = a.i1; pk.hlT = now;
      } else {
        var e = u < 0.5 ? 2 * u * u : 1 - 2 * (1 - u) * (1 - u);
        pk.x = a.x0 + (a.x1 - a.x0) * e; pk.y = a.y0 + (a.y1 - a.y0) * e;
        pk.on = u < 0.5 ? a.on0 : a.on1;
      }
    }
    var ps = this.push;
    if (ps) {
      var v = (now - ps.t0) / (PUSH_S * 1000), done = v >= 1;
      var ev = done ? 1 : (v < 0.5 ? 2 * v * v : 1 - 2 * (1 - v) * (1 - v));
      for (var i = 0; i < 2; i++) {
        var p = this.pose[i];
        p.x = ps.from[i][0] + (GEOM.IDLE[i][0] - ps.from[i][0]) * ev;
        p.y = ps.from[i][1] + (GEOM.IDLE[i][1] - ps.from[i][1]) * ev;
        p.z = HALF;
      }
      if (done) this.push = null;
    }
  };

  // --- drawing --------------------------------------------------------------------
  RP._draw = function () {
    var L = this.L;
    if (!L || this.destroyed || !this.ctx) return;
    var c = this.ctx, k = this.k, pad = GEOM.PAD;
    c.setTransform(1, 0, 0, 1, 0, 0);
    c.globalAlpha = 1; c.globalCompositeOperation = 'source-over';
    c.clearRect(0, 0, this.cw, this.ch);
    c.drawImage(L.tray, 0, 0);
    var e = this.eff, pk = this.puck, now = perfNow();
    if (e.show_point) {
      // highlight on the point box
      if (pk.on && pk.hlI >= 0 && !pk.anim) {
        var ha = Math.min(1, (now - pk.hlT) / 350), b = ptBox(pk.hlI);
        c.globalAlpha = ha;
        c.drawImage(L.hl.cv, (b.x - L.hl.pad + pad) * k, (b.y - L.hl.pad + pad) * k);
        c.globalAlpha = 1;
      }
      // the puck (lifts and flips while it moves)
      var a = pk.anim, lift = 0, sx = 1;
      if (a) {
        var u = clamp((now - a.t0) / (PUCK_S * 1000), 0, 1);
        lift = Math.sin(Math.PI * u);
        if (a.on0 !== a.on1) sx = Math.max(0.06, Math.abs(Math.cos(Math.PI * u)));
      }
      var spr = pk.on ? L.puckOn : L.puckOff, hs = spr.half * (1 + 0.16 * lift);
      c.setTransform(k * sx, 0, 0, k, (pk.x + pad) * k, (pk.y + pad - 5 * lift) * k);
      c.drawImage(spr.cv, -hs, -hs, hs * 2, hs * 2);
      c.setTransform(1, 0, 0, 1, 0, 0);
    }
    // dice: shadows first, then back to front along the view direction (the higher die,
    // or at equal height the one nearer the bottom of the screen, is drawn last)
    var p0 = this.pose[0], p1 = this.pose[1];
    this._shadow(c, p0, L, k); this._shadow(c, p1, L, k);
    if (p0.z * VIEW[2] + p0.y * VIEW[1] <= p1.z * VIEW[2] + p1.y * VIEW[1]) { this._die(c, p0, L, k); this._die(c, p1, L, k); }
    else { this._die(c, p1, L, k); this._die(c, p0, L, k); }
    c.setTransform(1, 0, 0, 1, 0, 0);
    c.globalAlpha = 1;
  };

  RP._shadow = function (c, p, L, k) {
    var zr = p.z - HALF, pad = GEOM.PAD;
    if (zr < 0) zr = 0;
    // apparent yaw: the footprint of the most upright face
    qmat(p.q, M9);
    var yaw = Math.atan2(M9[3], M9[0]), az = Math.abs(M9[8]), ax = Math.abs(M9[6]), ay = Math.abs(M9[7]);
    if (ax > az && ax > ay) yaw = Math.atan2(M9[4], M9[1]);
    else if (ay > az && ay > ax) yaw = Math.atan2(M9[3], M9[0]);
    var cx = (p.x + pad + 2 + zr * 0.3) * k, cy = (p.y + pad + 3 + zr * 0.36) * k;
    var cs = Math.cos(yaw), sn = Math.sin(yaw);
    // contact shadow (fades as the die lifts)
    var ca = clamp(1 - zr / 18, 0, 1);
    if (ca > 0.01) {
      c.globalAlpha = 0.85 * ca;
      c.setTransform(cs, sn, -sn, cs, (p.x + pad + 1.2) * k, (p.y + pad + 1.8) * k);
      c.drawImage(L.shA.cv, -L.shA.half, -L.shA.half);
    }
    // soft cast shadow: grows and fades with height
    var s2 = 1 + zr / 160;
    c.globalAlpha = 0.62 * clamp(1 - zr / 260, 0.25, 1);
    c.setTransform(cs * s2, sn * s2, -sn * s2, cs * s2, cx, cy);
    c.drawImage(L.shB.cv, -L.shB.half, -L.shB.half);
    c.globalAlpha = 1;
  };

  RP._die = function (c, p, L, k) {
    var m = qmat(p.q, M9), pad = GEOM.PAD;
    var zr = p.z - HALF; if (zr < 0) zr = 0;
    var sc = GEOM.ZC / (GEOM.ZC - Math.min(zr, GEOM.ZC * 0.6));
    var U = HALF * k * sc;                                // px per unit (unit = half edge)
    var cx = (p.x + pad) * k, cy = (p.y + pad - (p.z - HALF) * GEOM.TILT - HALF * GEOM.TILT * 0.5) * k;
    var ct = VIEW[2], st = VIEW[1];                       // projection: (x, y*cos - z*sin)
    var ai = AIN / HALF, rr = GEOM.RND / HALF, f, F;
    // silhouette: union of the visible inner faces, stroked with the edge radius
    c.setTransform(U, 0, 0, U, cx, cy);
    c.beginPath();
    for (f = 0; f < 6; f++) {
      F = FACES[f];
      var nx = m[0] * F.n[0] + m[1] * F.n[1] + m[2] * F.n[2];
      var ny = m[3] * F.n[0] + m[4] * F.n[1] + m[5] * F.n[2];
      var nz = m[6] * F.n[0] + m[7] * F.n[1] + m[8] * F.n[2];
      if (ny * VIEW[1] + nz * VIEW[2] <= 0.0005) continue;
      var ux = m[0] * F.u[0] + m[1] * F.u[1] + m[2] * F.u[2], uy = m[3] * F.u[0] + m[4] * F.u[1] + m[5] * F.u[2], uz = m[6] * F.u[0] + m[7] * F.u[1] + m[8] * F.u[2];
      var wx = m[0] * F.w[0] + m[1] * F.w[1] + m[2] * F.w[2], wy = m[3] * F.w[0] + m[4] * F.w[1] + m[5] * F.w[2], wz = m[6] * F.w[0] + m[7] * F.w[1] + m[8] * F.w[2];
      var ox = ai * nx, oy = ai * (ny * ct - nz * st);
      var pux = ai * ux, puy = ai * (uy * ct - uz * st), pwx = ai * wx, pwy = ai * (wy * ct - wz * st);
      c.moveTo(ox - pux - pwx, oy - puy - pwy); c.lineTo(ox + pux - pwx, oy + puy - pwy);
      c.lineTo(ox + pux + pwx, oy + puy + pwy); c.lineTo(ox - pux + pwx, oy - puy + pwy); c.closePath();
    }
    c.fillStyle = L.body; c.strokeStyle = L.body; c.lineWidth = 2 * rr; c.lineJoin = 'round';
    c.fill(); c.stroke();
    // flat faces: textured, lit per face, glossy highlight
    var TS = L.TS, sk = U / TS;
    for (f = 0; f < 6; f++) {
      F = FACES[f];
      var fnx = m[0] * F.n[0] + m[1] * F.n[1] + m[2] * F.n[2];
      var fny = m[3] * F.n[0] + m[4] * F.n[1] + m[5] * F.n[2];
      var fnz = m[6] * F.n[0] + m[7] * F.n[1] + m[8] * F.n[2];
      var vis = fny * VIEW[1] + fnz * VIEW[2];
      if (vis <= 0.0005) continue;
      var fux = m[0] * F.u[0] + m[1] * F.u[1] + m[2] * F.u[2], fuy = m[3] * F.u[0] + m[4] * F.u[1] + m[5] * F.u[2], fuz = m[6] * F.u[0] + m[7] * F.u[1] + m[8] * F.u[2];
      var fwx = m[0] * F.w[0] + m[1] * F.w[1] + m[2] * F.w[2], fwy = m[3] * F.w[0] + m[4] * F.w[1] + m[5] * F.w[2], fwz = m[6] * F.w[0] + m[7] * F.w[1] + m[8] * F.w[2];
      var Xx = 2 * ai * fux, Xy = 2 * ai * (fuy * ct - fuz * st), Yx = 2 * ai * fwx, Yy = 2 * ai * (fwy * ct - fwz * st);
      var Ox = fnx - ai * fux - ai * fwx, Oy = (fny * ct - fnz * st) - ai * (fuy * ct - fuz * st) - ai * (fwy * ct - fwz * st);
      c.setTransform(Xx * sk, Xy * sk, Yx * sk, Yy * sk, cx + Ox * U, cy + Oy * U);
      c.drawImage(L.faces[F.v], 0, 0);
      var lam = fnx * L3[0] + fny * L3[1] + fnz * L3[2];
      if (lam < 0.72) { c.globalAlpha = clamp((0.72 - lam) * 0.5, 0, L.dark); c.fillStyle = '#000'; c.fillRect(0, 0, TS, TS); }
      else { c.globalAlpha = clamp((lam - 0.72) * 0.7, 0, 0.3); c.fillStyle = '#fff'; c.fillRect(0, 0, TS, TS); }
      var hv = fnx * HV[0] + fny * HV[1] + fnz * HV[2];
      if (hv > 0.6) {
        var spec = Math.pow(hv, 22) * this.ds.gloss;
        if (spec > 0.01) { c.globalAlpha = clamp(spec, 0, 1) * 0.85; c.drawImage(L.sheen, 0, 0); }
      }
      // grazing faces fade into the edge colour (they are mostly bevel)
      if (vis < 0.25) { c.globalAlpha = (0.25 - vis) * 2.4; c.fillStyle = this.ds.edge; c.fillRect(0, 0, TS, TS); }
      c.globalAlpha = 1;
      // glints on the rounded edges of this face: the bevel normal is halfway between
      // the face and its neighbour; each edge is drawn once, by the more visible face
      var gl = this.ds.gloss, e;
      c.strokeStyle = this.ds.glint; c.lineWidth = TS * 0.075; c.lineCap = 'round';
      for (e = 0; e < 4; e++) {
        var sg = e & 1 ? -1 : 1, dx = sg * (e < 2 ? fux : fwx), dy = sg * (e < 2 ? fuy : fwy), dz = sg * (e < 2 ? fuz : fwz);
        if (dy * VIEW[1] + dz * VIEW[2] > vis) continue;
        var bx = (fnx + dx) * R2, by = (fny + dy) * R2, bz = (fnz + dz) * R2;
        var bh = bx * HV[0] + by * HV[1] + bz * HV[2], bl = bx * L3[0] + by * L3[1] + bz * L3[2];
        var ga = (bh > 0.7 ? Math.pow(bh, 12) * gl * 0.95 : 0) + (bl > 0.5 ? (bl - 0.5) * 0.5 : 0);
        if (ga < 0.02) continue;
        var o = e & 1 ? -0.1 * TS : 1.1 * TS;
        c.globalAlpha = ga > 0.9 ? 0.9 : ga;
        c.beginPath();
        if (e < 2) { c.moveTo(o, 0.1 * TS); c.lineTo(o, 0.9 * TS); } else { c.moveTo(0.1 * TS, o); c.lineTo(0.9 * TS, o); }
        c.stroke();
      }
      c.globalAlpha = 1;
    }
  };

  // ======================================================================
  // Registration
  // ======================================================================
  function copyObj(o) { var r = {}; for (var k in o) if (has(o, k)) r[k] = o[k]; return r; }
  var API = {
    BASE_W: BASE_W,
    BASE_H: BASE_H,
    THEMES: THEMES,
    DICE_STYLES: DICE_STYLES,
    DEFAULTS: copyObj(DEFAULTS),       // copies: a host mutating them can't change the renderer
    APPEARANCE: APPEARANCE.slice(),
    resultFor: resultFor,
    create: function (container, config, opts) { return new Craps(container, config, opts); },
    motion: motion
  };
  HG.craps = API;
  if (typeof module === 'object' && module && module.exports) module.exports = API;
})(typeof window !== 'undefined' ? window : (typeof globalThis !== 'undefined' ? globalThis : this));
