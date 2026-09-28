/*
 * Hexcast Games — Roulette renderer                      static/games/roulette.js
 *
 * Shared by the OBS overlay (/games/overlay) and the control panel (/games).
 * Plain browser script: no modules, no dependencies, no build step.
 *
 * Registers window.HexGames.roulette:
 *
 *   BASE_SIZE                 wheel diameter in stage px at scale 1 (400)
 *   AMERICAN, RED             wheel data (identical to games.py). American
 *                             double-zero wheel only: 38 pockets, 0, 00, 1..36.
 *   THEMES                    { classic, neon, midnight, royal } — each has .red/.black/.green
 *                             (the theme's default pocket colours) and .accent
 *   DEFAULTS                  roulette config defaults (same values as the backend)
 *   APPEARANCE                the keys a spin's `overrides` may change
 *   DEMO_TABLE                sample TABLE (bets + a countdown) for editors: setTable(DEMO_TABLE)
 *                             shows it greyed out with the countdown standing still
 *   resultFor(label, wheel)   -> RESULT (same shape as the server's) or null.
 *                             `wheel` is accepted for API compatibility and ignored:
 *                             the result is always on the American wheel.
 *   create(container, config, opts) -> instance      opts: {sound: true, demo: false}
 *   motion                    the pure, DOM-free spin math (plan / wheelAngle /
 *                             ballState / GEOM ...) — used by the node tests
 *   table                     the pure table helpers (config / rows / countdown / overflow)
 *
 *   instance.setConfig(cfg)   instance.resize()          instance.play(spin) -> Promise
 *   instance.showResult(spin) instance.setHistory(list)  instance.reset()
 *   instance.destroy()        instance.onLanded = fn(spin)
 *   instance.setAnnounce(a)   Hex's own winners card (STATE.announce, null = none): replaces
 *                             the computed winners card, held until the ball lands, cleared
 *                             by reset() and by the next play()/showResult()
 *   instance.setTable(table)  STATE.table (null = none): the "NEXT SPIN" countdown
 *                             (table.auto_spin_in_ms, ticking locally) and the "on the table"
 *                             board (table.bets, or Hex's table.display_board instead). Both
 *                             only show before a spin (idle); a table that arrives while the
 *                             ball is in the air is held until it lands (never spoils)
 *   opts.demo                 editor preview: DEMO_HISTORY / DEMO_TABLE stand in while the
 *                             instance has no history / table of its own
 *
 * The winners card after landing lists the spin's direct `bets` and the table's
 * `settlements` together (winners, biggest payout first; bets_max rows; currency).
 *
 * Container contract: the HOST sizes the container to BASE_SIZE x BASE_SIZE stage
 * px and positions/scales it. The renderer fills it with a canvas (drawn 8% larger
 * on each side so the drop shadow / neon glow are not clipped) and its own DOM
 * overlays (result badge, caption, countdown, history strip, bets list, table
 * board), class prefix hgr-. Above the wheel: the caption, or the countdown when no
 * spin is up (below the history strip instead when there is no room above). The
 * board goes where table_position says (right / left / above / below), to the other
 * side when it would run off the stage there; the result card and the winners card
 * never share the screen with it.
 *
 * Motion model — everything is a pure function of (plan, t), t = seconds since
 * launch, so any overlay can seek to elapsed_ms and every client draws the same
 * spin from the same seed:
 *   wheel  W(t) = W0 + w0*tw*(1 - e^(-t/tw))   clockwise, decelerating, coasting on
 *   ball   A  on the track, counter-clockwise, fast, decelerating to the drop speed
 *          B  leaves the track (55-65% of the spin), spirals down the slope and
 *             hits one of the 8 diamond deflectors (exactly at its angle)
 *          C  2-4 damped hops over the number ring / frets (seeded)
 *          D  skips over the frets, slows relative to the wheel, rattles between
 *             the frets and stops dead in the pocket at exactly duration_ms
 *          E  rides with the coasting wheel.
 *   Exactness: working backwards from the server's pocket, the ball's speed after
 *   the deflector is solved from a linear equation so that the relative travel
 *   deflector -> pocket starts on a real deflector, and the launch angle absorbs
 *   the rest. The correction is therefore spread over the whole flight (invisible)
 *   and the landing is exact for every pocket, seed and duration.
 */
(function (root) {
  'use strict';

  var HG = root.HexGames || (root.HexGames = {});

  // ======================================================================
  // Wheel data (identical to games.py) — American double-zero wheel only
  // ======================================================================
  var WHEEL = 'american';
  var AMERICAN = ['0', '28', '9', '26', '30', '11', '7', '20', '32', '17', '5', '22', '34', '15', '3', '24', '36', '13',
    '1', '00', '27', '10', '25', '29', '12', '8', '19', '31', '18', '6', '21', '33', '16', '4', '23', '35', '14', '2'];
  var RED = [1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36];
  var RED_SET = {};
  for (var ri = 0; ri < RED.length; ri++) RED_SET[RED[ri]] = true;

  var BASE_SIZE = 400;
  var TAU = Math.PI * 2;

  var DEFAULTS = {
    x: 50, y: 50, scale: 1.25, theme: 'classic',
    red_color: '', black_color: '', green_color: '',
    spin_seconds: 9, result_seconds: 6, hide_when_idle: true,
    show_result: true, result_position: 'center', result_details: true,
    show_history: true, history_count: 10, show_user: true,
    show_bets: true, bets_max: 5, sfx: true, sfx_volume: 0.5,
    spin_clip: '', land_clip: '', cooldown_seconds: 0,
    currency: 'hexcoins', min_bet: 1, max_bet: 100000, auto_spin: false, bet_window_seconds: 20,
    show_when_bets: true, show_table: true, table_max: 6, table_position: 'right'
  };
  var APPEARANCE = ['x', 'y', 'scale', 'theme', 'red_color', 'black_color', 'green_color', 'result_position',
    'result_details', 'show_result', 'show_history', 'history_count', 'show_user', 'show_bets',
    'bets_max', 'sfx', 'sfx_volume', 'show_table', 'table_max', 'table_position'];
  var TABLE_POS = { right: 1, left: 1, above: 1, below: 1 };

  var THEMES = {
    classic: {
      label: 'Classic', red: '#c0282d', black: '#16161a', green: '#0f7a3c',
      rimA: '#5b321a', rimB: '#26130a', grain: '#170902', grainHi: '#e8b27a',
      trim: '#d4af37', trimHi: '#fff1b8', trimLo: '#6c500f',
      track: '#3d2413', slope: '#26140a',
      fret: '#d4af37', fretHi: '#fff4c8', fretLo: '#6c500f',
      coneA: '#7c4a28', coneB: '#2a160a', inlay: '#d4af37',
      accent: '#d4af37', glow: '#ffd966', edgeGlow: '', fretGlow: ''
    },
    neon: {
      label: 'Neon', red: '#e3262f', black: '#0c0c11', green: '#00a862',
      rimA: '#16161c', rimB: '#050507', grain: '#000000', grainHi: '#ff5a50',
      trim: '#ff3b30', trimHi: '#ffc2bd', trimLo: '#7a0f0a',
      track: '#0c0c11', slope: '#13131a',
      fret: '#7df9ff', fretHi: '#ffffff', fretLo: '#157787',
      coneA: '#1d1d27', coneB: '#060609', inlay: '#7df9ff',
      accent: '#ff3b30', glow: '#7df9ff', edgeGlow: '#ff3b30', fretGlow: '#35e8ff'
    },
    midnight: {
      label: 'Midnight', red: '#b8222c', black: '#11151d', green: '#0f7a52',
      rimA: '#1e2f52', rimB: '#0a1226', grain: '#040914', grainHi: '#a9c2f0',
      trim: '#c8d1dc', trimHi: '#ffffff', trimLo: '#4b5667',
      track: '#1a2844', slope: '#0e1629',
      fret: '#c9d2de', fretHi: '#ffffff', fretLo: '#525e70',
      coneA: '#2f4470', coneB: '#0a1328', inlay: '#c9d2de',
      accent: '#b9c8dc', glow: '#e6efff', edgeGlow: '', fretGlow: ''
    },
    royal: {
      label: 'Royal', red: '#b8192f', black: '#140f1a', green: '#0f6e48',
      rimA: '#4b1a68', rimB: '#1a0727', grain: '#10021a', grainHi: '#dcaeff',
      trim: '#e0b84a', trimHi: '#fff3c6', trimLo: '#77570f',
      track: '#361749', slope: '#200b2b',
      fret: '#e0b84a', fretHi: '#fff3c6', fretLo: '#77570f',
      coneA: '#652a8e', coneB: '#1d0830', inlay: '#e0b84a',
      accent: '#e0b84a', glow: '#ffe28a', edgeGlow: '', fretGlow: ''
    }
  };

  // ======================================================================
  // Small helpers (pure)
  // ======================================================================
  function clamp(v, a, b) { return v < a ? a : (v > b ? b : v); }
  function mod(a, m) { a = a % m; return a < 0 ? a + m : a; }
  function wrapPi(a) { return mod(a + Math.PI, TAU) - Math.PI; }
  function normLabel(label) {
    if (label == null) return '';
    var s = String(label).replace(/\s+/g, '');
    if (s === '00') return '00';
    if (!/^\d{1,2}$/.test(s)) return '';
    return String(parseInt(s, 10));
  }
  function colorOf(label) {
    if (label === '0' || label === '00') return 'green';
    return RED_SET[+label] ? 'red' : 'black';
  }
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

  // RESULT object, same shape as the server's. The `wheel` argument is only
  // there for API compatibility: there is one wheel (American), always.
  function resultFor(label) {
    var s = normLabel(label);
    var idx = s ? AMERICAN.indexOf(s) : -1;
    if (idx < 0) return null;
    var value = s === '00' ? -1 : parseInt(s, 10);
    var res = {
      number: s, value: value, color: colorOf(s), index: idx, wheel: WHEEL,
      parity: null, range: null, dozen: null, column: null
    };
    if (value > 0) {
      res.parity = value % 2 ? 'odd' : 'even';
      res.range = value <= 18 ? 'low' : 'high';
      res.dozen = Math.ceil(value / 12);
      res.column = ((value - 1) % 3) + 1;
    }
    return res;
  }

  // ======================================================================
  // Spin math — pure functions of (plan, t). No DOM in this section.
  // ======================================================================
  // Radii are fractions of the wheel radius (1 = outer edge of the rim).
  var GEOM = {
    OVER: 1.16,        // canvas side / wheel diameter (room for shadow + glow)
    RIM_IN: 0.905,     // wooden rim: RIM_IN..1
    TRACK_IN: 0.828,   // polished ball track: TRACK_IN..RIM_IN
    SLOPE_IN: 0.712,   // lower bowl slope with the deflectors: SLOPE_IN..TRACK_IN
    HEAD_R: 0.700,     // rotating head (outer metal ring)
    NUM_OUT: 0.690,    // number ring
    NUM_IN: 0.598,
    POCKET_OUT: 0.598, // pocket ring
    POCKET_IN: 0.470,
    CONE_OUT: 0.462,   // centre cone
    BALL_R: 0.034,     // ball radius
    TRACK_R: 0.866,    // ball centre while on the track
    POCKET_R: 0.532,   // ball centre at rest in a pocket
    DEFL_R: 0.772,     // deflector centres
    HIT_R: 0.806,      // ball centre when it strikes a deflector
    ARM_R: 0.285,      // turret knob radius
    TURRET_R: 0.098
  };
  var DEFL_N = 8;
  var DEFL_0 = Math.PI / 8;
  var BUMP_H = 0.011;   // height of a fret hop while skipping over the pockets

  function deflectorAngle(k) { return DEFL_0 + k * TAU / DEFL_N; }
  function pocketAngle(i, N) { return i * TAU / N; }

  function pocketIndex(res, order) {
    res = res || {};
    var lab = normLabel(res.number != null ? res.number : (res.value === -1 ? '00' : res.value));
    var i = res.index;
    if (typeof i === 'number' && i >= 0 && i < order.length && (i | 0) === i && (!lab || order[i] === lab)) return i;
    return lab ? order.indexOf(lab) : -1;
  }

  function spinSeed(spin) {
    var s = spin.seed;
    if (s != null && s !== '' && isFinite(+s)) return (+s) >>> 0;
    return hashStr(String(spin.id || 'hexcast'));
  }

  // --- wheel -------------------------------------------------------------
  function wheelDet(P, t) { return P.W0 + P.w0 * P.tw * (1 - Math.exp(-t / P.tw)); }
  function wheelSpeedDet(P, t) { return P.w0 * Math.exp(-t / P.tw); }

  // Optional per-instance hand-off from the idle wheel into the seeded spin:
  // a cubic Hermite offset that starts at the current angle/speed and is gone
  // well before the ball leaves the track, so the ball's path is unaffected.
  function blendPlan(P, t0, angle, speed) {
    P.blend = null;
    if (!P || !(t0 >= 0) || !isFinite(angle) || !isFinite(speed)) return P;
    var tau = Math.min(3.0, 0.8 * P.tDrop - t0);
    if (tau < 0.6) return P;
    var x = mod(wheelDet(P, t0) - angle, TAU);   // how far the seeded wheel is "ahead"
    var d = -x;
    if (d < -TAU + 1.2) d += TAU;                 // small backward nudges are fine
    P.blend = { t0: t0, t1: t0 + tau, tau: tau, d: d, v: speed - wheelSpeedDet(P, t0) };
    return P;
  }

  function wheelAngle(P, t) {
    var a = wheelDet(P, t), b = P.blend;
    if (b && t < b.t1) {
      var s = t <= b.t0 ? 0 : (t - b.t0) / b.tau, s2 = s * s, s3 = s2 * s;
      a += b.d * (2 * s3 - 3 * s2 + 1) + b.tau * b.v * (s3 - 2 * s2 + s);
    }
    return a;
  }
  function wheelSpeed(P, t) {
    var w = wheelSpeedDet(P, t), b = P.blend;
    if (b && t < b.t1 && t >= b.t0) {
      var s = (t - b.t0) / b.tau;
      w += (b.d * (6 * s * s - 6 * s) + b.tau * b.v * (3 * s * s - 4 * s + 1)) / b.tau;
    }
    return w;
  }

  // --- ball, phase A (track) and B (drop) -----------------------------------
  function travelA(P, s) {
    return P.tDrop * P.Oc * (s + P.kA / P.dA * ((1 - Math.exp(-P.lam * s)) / P.lam - s * P.eL));
  }
  function omegaA(P, s) { return P.Oc * (1 + P.kA * (Math.exp(-P.lam * s) - P.eL) / P.dA); }
  function travelB(P, u) { return P.Oc * P.tB * (u + P.gB * u * u / 2); }

  // Build the full, deterministic plan of a spin. Returns null if the spin has
  // no usable result.
  function planSpin(spin) {
    spin = spin || {};
    var res = spin.result || {};
    var order = AMERICAN, N = order.length;   // spin.wheel is informational ("american")
    var idx = pocketIndex(res, order);
    if (idx < 0) return null;
    // The server clamps duration to 4..30 s; stay finite/sane for any other caller.
    var dms = +spin.duration_ms;
    var T = dms > 0 && isFinite(dms) ? dms / 1000 : 9;
    T = clamp(T, 2.5, 120);
    var seed = spinSeed(spin);
    var rnd = mulberry32((seed ^ 0x2F6B9A5D) >>> 0);
    var i;
    var P = { wheel: WHEEL, N: N, index: idx, label: order[idx], p: pocketAngle(idx, N), T: T, seed: seed, blend: null };

    // wheel: 0.42-0.58 rev/s at launch, long exponential coast
    P.W0 = rnd() * TAU;
    P.w0 = TAU * (0.42 + 0.16 * rnd());
    P.tw = clamp(1.5 * T, 9, 45) * (0.9 + 0.2 * rnd());

    // ball on the track: launched at 2.4-3 rev/s, leaves the track at ~0.8 rev/s
    P.O0 = 15 + 4 * rnd();
    P.Oc = 4.7 + 0.8 * rnd();
    P.lam = 1.2 + 1.0 * rnd();
    var dropFrac = 0.55 + 0.10 * rnd();
    P.tB = clamp(0.085 * T, 0.5, 2.6) * (0.85 + 0.3 * rnd());
    P.gB = 0.08 + 0.10 * rnd();
    P.tDrop = dropFrac * T;
    var minC = Math.max(0.7, 0.2 * T);
    if (P.tDrop + P.tB > T - minC) { var f = (T - minC) / (P.tDrop + P.tB); P.tDrop *= f; P.tB *= f; }
    P.tHit = P.tDrop + P.tB;
    P.kA = P.O0 / P.Oc - 1; P.eL = Math.exp(-P.lam); P.dA = 1 - P.eL;
    P.TA = travelA(P, 1); P.TB = travelB(P, 1);
    var OB = P.Oc * (1 + P.gB);     // speed when it strikes the deflector
    P.OB = OB;

    // bounce phase draws (fixed count so the stream never depends on nArcs)
    var nArcs = 2 + Math.floor(rnd() * 3); if (nArcs > 4) nArcs = 4;
    var kd = 0.32 + 0.25 * rnd();
    var dur = [0.30 + 0.12 * rnd()], kf = [];
    for (i = 1; i < 4; i++) dur[i] = dur[i - 1] * (0.52 + 0.18 * rnd());
    for (i = 0; i < 4; i++) kf[i] = 0.35 + 0.25 * rnd();
    var H1 = 0.05 + 0.03 * rnd();
    var rho1 = 0.625 + 0.02 * rnd(), q = 0.35 + 0.15 * rnd();
    var rockA = 0.009 + 0.006 * rnd(), rockM = 2 + Math.floor(rnd() * 2), rockR = 0.006 + 0.005 * rnd();
    var dfMax = 1.6 + 1.2 * rnd();

    var C = T - P.tHit, sum, st;
    for (;;) {   // short spins: drop hops that would be too quick to read
      sum = 0;
      for (i = 0; i < nArcs; i++) sum += dur[i];
      st = clamp(0.5 * C / sum, 0.4, 1.3);
      if (sum * st > 0.62 * C) st = 0.62 * C / sum;
      if (nArcs <= 2 || dur[nArcs - 1] * st >= 0.07) break;
      nArcs--;
    }
    dur.length = nArcs;
    var tau = [P.tHit], H = [], rho = [GEOM.HIT_R];
    for (i = 0; i < nArcs; i++) { dur[i] *= st; tau[i + 1] = tau[i] + dur[i]; }
    H[0] = H1 * Math.min(1, Math.pow(st, 1.5));
    for (i = 1; i < nArcs; i++) H[i] = H[i - 1] * Math.pow(dur[i] / dur[i - 1], 2);
    for (i = 1; i <= nArcs; i++) rho[i] = GEOM.POCKET_R + (rho1 - GEOM.POCKET_R) * Math.pow(q, i - 1);
    P.tn = tau[nArcs];
    P.L = T - P.tn;

    // Relative travel (ball vs wheel) from the deflector hit to the pocket is
    // affine in the post-deflector speed Om1: D = A*Om1 + Bc.
    var a = 1, b = 0, A = 0, Bc = 0, w, aArr = [], bArr = [];
    for (i = 0; i < nArcs; i++) {
      aArr[i] = a; bArr[i] = b;
      A += a * dur[i];
      Bc += b * dur[i] + (wheelDet(P, tau[i + 1]) - wheelDet(P, tau[i]));
      if (i < nArcs - 1) {
        w = wheelSpeedDet(P, tau[i + 1]);
        b = kf[i] * (b + w) - w;       // each landing keeps kf of the relative speed
        a = kf[i] * a;
      }
    }
    var wn = wheelSpeedDet(P, P.tn);
    var aL = aArr[nArcs - 1], bL = bArr[nArcs - 1];
    var Om1nom = kd * OB;
    var cN = kf[nArcs - 1];
    var sigEst = cN * (aL * Om1nom + bL + wn);
    if (sigEst * P.L / 3 > dfMax) cN *= dfMax / (sigEst * P.L / 3);   // long spins: don't skate forever
    A += cN * aL * P.L / 3;
    Bc += cN * (bL + wn) * P.L / 3;

    // The deflector hit must happen on a deflector: B_hit = W(tHit) + p + D
    // must equal DEFL_0 + k*45deg, so D is fixed modulo 45deg. Take the value
    // closest to the seeded nominal whose Om1 stays plausible.
    var step = TAU / DEFL_N;
    var x = DEFL_0 - wheelDet(P, P.tHit) - P.p;
    var Dnom = A * Om1nom + Bc;
    var base = x + step * Math.round((Dnom - x) / step);
    var lo = 0.18 * OB, hi = 0.72 * OB, best = base, bestErr = Infinity;
    for (var j = 0; j < 13; j++) {
      var jj = j === 0 ? 0 : (j % 2 ? (j + 1) / 2 : -j / 2);
      var Dc = base + jj * step;
      var Om = (Dc - Bc) / A;
      var err = Om < lo ? lo - Om : (Om > hi ? Om - hi : 0);
      if (err < bestErr - 1e-12) { bestErr = err; best = Dc; if (err === 0) break; }
    }
    var Om1 = (best - Bc) / A;
    P.D = best;
    P.nArcs = nArcs; P.dur = dur; P.tau = tau; P.H = H; P.rho = rho;
    P.Om = [];
    for (i = 0; i < nArcs; i++) P.Om[i] = aArr[i] * Om1 + bArr[i];
    P.sigN = cN * (P.Om[nArcs - 1] + wn);
    P.Bhit = wheelDet(P, P.tHit) + P.p + best;
    P.B0 = P.Bhit + P.TA + P.TB;
    P.deflector = Math.round(mod(P.Bhit - DEFL_0, TAU) / step) % DEFL_N;
    P.Bi = [P.Bhit];
    for (i = 0; i < nArcs; i++) P.Bi[i + 1] = P.Bi[i] - P.Om[i] * dur[i];
    // Remaining relative distance at the start of the settle phase, computed
    // from the actual position so the landing is exact to the last bit.
    var phiN = P.Bi[nArcs] - wheelDet(P, P.tn);
    var Dexp = P.sigN * P.L / 3;
    P.Dr = phiN - (P.p + TAU * Math.round((phiN - P.p - Dexp) / TAU));

    // rattle between the frets just before it stops
    var win = Math.min(0.9, 0.45 * P.L);
    P.tr = T - win;
    var soft = Math.min(1, (win / 0.6) * (win / 0.6));
    P.rockA = -rockA * soft; P.rockM = rockM; P.rockR = rockR * soft;

    // sound / event script
    var ev = [{ t: tau[0], kind: 'deflect', heavy: true, vol: 0.95, rate: 1 }];
    for (i = 1; i <= nArcs; i++) {
      ev.push({ t: tau[i], kind: 'hop', heavy: i === 1, vol: 0.75 * Math.sqrt(H[i - 1] / H[0]) + 0.15, rate: 0.95 + 0.1 * i });
    }
    if (soft > 0.2) {
      for (i = 0; i < rockM; i++) {
        var vr = (i + 0.5) / rockM, env = 6.75 * vr * (1 - vr) * (1 - vr);
        ev.push({ t: P.tr + vr * win, kind: 'rattle', heavy: false, vol: 0.35 * env * soft, rate: 1.25 });
      }
    }
    ev.push({ t: T, kind: 'land', heavy: true, vol: 0.8, rate: 0.82 });
    ev.sort(function (e1, e2) { return e1.t - e2.t; });
    P.events = ev;
    P.impacts = tau.slice();
    return P;
  }

  // Ball state at time t (seconds since launch). Writes into `o` (no allocation).
  //   angle  screen angle, radians clockwise from 12 o'clock (unwrapped)
  //   wheel  wheel angle at t;  rel = (angle - wheel) mod 2pi  (wheel-relative)
  //   radius / height in wheel radii;  speed = d(angle)/dt (+ = clockwise)
  //   phase  0 track, 1 drop, 2 hops, 3 settle, 4 landed
  function ballState(P, t, o) {
    o = o || {};
    var W = wheelAngle(P, t), ang, r = GEOM.TRACK_R, h = 0, sp, ph;
    if (t < P.tDrop) {
      var s = t <= 0 ? 0 : t / P.tDrop;
      ang = P.B0 - travelA(P, s); sp = -omegaA(P, s); ph = 0;
    } else if (t < P.tHit) {
      var u = (t - P.tDrop) / P.tB;
      ang = P.B0 - P.TA - travelB(P, u);
      r = GEOM.TRACK_R - (GEOM.TRACK_R - GEOM.HIT_R) * u * u;
      sp = -P.Oc * (1 + P.gB * u); ph = 1;
    } else if (t < P.tn) {
      var i = 0;
      while (i < P.nArcs - 1 && t >= P.tau[i + 1]) i++;
      var lt = t - P.tau[i], u2 = lt / P.dur[i];
      ang = P.Bi[i] - P.Om[i] * lt;
      r = P.rho[i] + (P.rho[i + 1] - P.rho[i]) * u2;
      h = 4 * P.H[i] * u2 * (1 - u2);
      sp = -P.Om[i]; ph = 2;
    } else if (t < P.T) {
      var v = (t - P.tn) / P.L, iv = 1 - v;
      var phi = P.p + P.Dr * iv * iv * iv;
      var sig = 3 * P.Dr / P.L * iv * iv;
      r = GEOM.POCKET_R + (P.rho[P.nArcs] - GEOM.POCKET_R) * iv * iv;
      if (t > P.tr) {
        var vr = (t - P.tr) / (P.T - P.tr), env = 6.75 * vr * (1 - vr) * (1 - vr);
        phi += P.rockA * env * Math.sin(Math.PI * P.rockM * vr);
        r += P.rockR * env * Math.sin(Math.PI * (P.rockM + 1) * vr);
      }
      ang = W + phi;
      var fr = Math.sin(Math.PI * phi * P.N / TAU), ramp = Math.min(1, (t - P.tn) / 0.1);
      h = BUMP_H * ramp * (P.Dr > 0 ? iv * iv : 0) * fr * fr;
      sp = wheelSpeed(P, t) - sig; ph = 3;
    } else {
      ang = W + P.p; r = GEOM.POCKET_R; sp = wheelSpeed(P, t); ph = 4;
    }
    o.angle = ang; o.wheel = W; o.rel = mod(ang - W, TAU);
    o.radius = r; o.height = h; o.speed = sp; o.phase = ph; o.visible = t >= 0;
    return o;
  }

  var motion = {
    GEOM: GEOM, DEFL_N: DEFL_N,
    plan: planSpin, blend: blendPlan,
    wheelAngle: wheelAngle, wheelSpeed: wheelSpeed, ballState: ballState,
    pocketAngle: pocketAngle, deflectorAngle: deflectorAngle,
    mulberry32: mulberry32, wrapPi: wrapPi
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
  function sanitize(o) {
    delete o.wheel;   // American wheel only: a legacy `wheel` key is simply dropped
    if (!Object.prototype.hasOwnProperty.call(THEMES, o.theme)) o.theme = 'classic';
    var ck = ['red_color', 'black_color', 'green_color'];
    for (var i = 0; i < ck.length; i++) o[ck[i]] = /^#[0-9a-f]{6}$/i.test(String(o[ck[i]] || '')) ? String(o[ck[i]]) : '';
    if (o.result_position !== 'below' && o.result_position !== 'above') o.result_position = 'center';
    o.x = toNum(o.x, 50, 0, 100); o.y = toNum(o.y, 50, 0, 100);
    o.scale = toNum(o.scale, 1.25, 0.2, 5);
    o.sfx_volume = toNum(o.sfx_volume, 0.5, 0, 1);
    o.history_count = Math.round(toNum(o.history_count, 10, 1, 20));
    o.bets_max = Math.round(toNum(o.bets_max, 5, 1, 20));
    o.spin_seconds = toNum(o.spin_seconds, 9, 4, 30);
    o.result_seconds = toNum(o.result_seconds, 6, 1, 120);
    // the table (countdown + board)
    if (!TABLE_POS[o.table_position]) o.table_position = 'right';
    o.table_max = Math.round(toNum(o.table_max, 6, 1, 20));
    o.bet_window_seconds = toNum(o.bet_window_seconds, 20, 5, 300);
    o.currency = typeof o.currency === 'string' || typeof o.currency === 'number' ? annStr(o.currency, 24) : DEFAULTS.currency;
    var bk = ['hide_when_idle', 'show_result', 'result_details', 'show_history', 'show_user', 'show_bets', 'sfx',
      'show_table', 'show_when_bets', 'auto_spin'];
    for (i = 0; i < bk.length; i++) o[bk[i]] = toBool(o[bk[i]], DEFAULTS[bk[i]]);
    return o;
  }
  function normConfig(c) {
    c = (c && typeof c === 'object') ? c : {};
    var src = (c.roulette && typeof c.roulette === 'object') ? c.roulette : c, o = {}, k;
    for (k in DEFAULTS) o[k] = DEFAULTS[k];
    for (k in src) if (Object.prototype.hasOwnProperty.call(src, k) && src[k] != null) o[k] = src[k];
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
  function detailsFor(res) {
    if (!res) return '';
    if (res.number === '0') return 'ZERO';
    if (res.number === '00') return 'DOUBLE ZERO';
    var ord = ['', '1ST', '2ND', '3RD'], parts = [];
    if (res.parity) parts.push(String(res.parity).toUpperCase());
    if (res.range) parts.push(String(res.range).toUpperCase());
    if (res.dozen) parts.push(ord[res.dozen] + ' 12');
    return parts.join(' \u00B7 ');
  }
  function fmtNum(n) {
    n = Math.round((+n || 0) * 100) / 100;
    var neg = n < 0; n = Math.abs(n);
    var parts = String(n).split('.');
    parts[0] = parts[0].replace(/\B(?=(\d{3})+(?!\d))/g, ',');
    return (neg ? '-' : '') + parts.join('.');
  }

  // Hex's own winners card (setAnnounce) — STATE.announce, cleaned again here by the
  // server's rules so any caller (overlay, panel mirror) gets the same card: <= 50 lines
  // of {user, text, amount}, a line needs a user or a text, an unusable amount drops the
  // line. Returns null for "no card" (none, or already expired).
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
  // null = no amount; undefined = unusable (not a finite number, |amount| > 1e12)
  function annAmount(v) {
    if (v == null || (typeof v === 'string' && !v.trim())) return null;
    var n = typeof v === 'number' ? v : (typeof v === 'string' ? +v : NaN);
    return isFinite(n) && Math.abs(n) <= ANN_MAX_AMOUNT ? n : undefined;
  }
  function normAnnounce(a, curDflt) {
    if (!a || typeof a !== 'object' || Array.isArray(a)) return null;
    // expires_in_ms as handed over (the host ages it): 0 or less = already gone
    var ttl = typeof a.expires_in_ms === 'number' && !isNaN(a.expires_in_ms) ? a.expires_in_ms : null;
    if (ttl !== null && ttl <= 0) return null;
    var src = Array.isArray(a.lines) ? a.lines : [], lines = [];
    for (var i = 0; i < src.length && lines.length < 50; i++) {
      var l = src[i];
      if (!l || typeof l !== 'object') continue;
      var u = annStr(annStr(l.user, 1000).replace(/^@+/, ''), 40), t = annStr(l.text, 60), m = annAmount(l.amount);
      if ((!u && !t) || m === undefined) continue;
      lines.push({ user: u, text: t, amount: m });
    }
    var o = {
      id: annStr(a.id, 64), spin_id: annStr(a.spin_id, 64),
      title: annStr(a.title, 40) || 'WINNERS', lines: lines,
      empty_text: annStr(a.empty_text, 60) || 'No winners',
      currency: a.currency == null ? (curDflt || '') : annStr(a.currency, 24),
      ttl: ttl
    };
    o.sig = JSON.stringify([o.id, o.spin_id, o.title, o.lines, o.empty_text, o.currency]);
    return o;
  }
  // signed amount: "+200" / "\u221250" (a real minus sign) / "0", then the currency when there is one
  function annAmountText(n, cur) {
    var r = Math.round(n * 100) / 100;
    var s = r > 0 ? '+' + fmtNum(r) : (r < 0 ? '\u2212' + fmtNum(-r) : '0');
    return cur ? s + ' ' + cur : s;
  }
  function annAmountClass(n) {
    var r = Math.round(n * 100) / 100;
    return r > 0 ? '' : (r < 0 ? 'neg' : 'zero');
  }

  // The "on the table" board (setTable), as rows of {key, user, text, amount}: Hex's own
  // board (TABLE.display_board, cleaned by the server's rules like the craps board: <= 100
  // lines, a line needs a user or a text, a negative / unusable amount drops the line) or,
  // when that is null, the table's bets (BETVIEW: @user, bet label, stake), biggest first.
  // -> null (nothing to show) or {title (null = the renderer's), rows, total, showTotal, hex}
  function boardRows(tb) {
    if (!tb || typeof tb !== 'object') return null;
    var db = tb.display_board, rows = [], i, sum = 0, amt = false;
    if (db && typeof db === 'object' && !Array.isArray(db)) {
      var src = Array.isArray(db.bets) ? db.bets : [], seen = Object.create(null);
      for (i = 0; i < src.length && rows.length < 100; i++) {
        var l = src[i];
        if (!l || typeof l !== 'object') continue;
        var u = annStr(annStr(l.user, 1000).replace(/^@+/, ''), 40), t = annStr(l.text, 60), m = annAmount(l.amount);
        if ((!u && !t) || m === undefined || (m != null && m < 0)) continue;
        var k = 'h|' + u + '|' + t;
        seen[k] = (seen[k] || 0) + 1;                 // (the same line twice keeps two keys)
        rows.push({ key: k + '|' + seen[k], user: u, text: t, amount: m });
        if (m != null) { sum += m; amt = true; }
      }
      var dt = typeof db.total === 'number' && isFinite(db.total) ? db.total : sum;
      return { title: annStr(db.title, 40) || 'ON THE TABLE', rows: rows, total: dt, showTotal: amt || dt !== 0, hex: true };
    }
    var bets = Array.isArray(tb.bets) ? tb.bets : [];
    for (i = 0; i < bets.length; i++) {
      var b = bets[i];
      if (!b || typeof b !== 'object') continue;
      var a = +b.amount;
      a = isFinite(a) && a > 0 ? a : 0;
      var bu = annStr(annStr(b.user, 1000).replace(/^@+/, ''), 40) || 'anon', bt = annStr(b.label || b.bet, 60);
      rows.push({ key: b.id != null ? 'b' + b.id : 'b' + i + '|' + bu + '|' + bt, user: bu, text: bt, amount: a, i: i });
      sum += a;
    }
    rows.sort(function (x, y) { return (y.amount - x.amount) || (x.i - y.i); });
    var tot = typeof tb.total_on_table === 'number' && isFinite(tb.total_on_table) ? tb.total_on_table : sum;
    return { title: null, rows: rows, total: tot, showTotal: rows.length > 0, hex: false };
  }
  // the countdown pill for `left` ms to go: "NEXT SPIN" + m:ss (urgent in the last 5 s),
  // at zero "NO MORE BETS" until the spin starts
  function countdownParts(left) {
    left = Math.max(0, +left || 0);
    var s = Math.ceil(left / 1000);
    if (left <= 0) return { label: 'RIEN NE VA PLUS', text: 'NO MORE BETS', urgent: false, zero: true };
    return { label: 'NEXT SPIN', text: Math.floor(s / 60) + ':' + ('0' + (s % 60)).slice(-2), urgent: left <= 5000, zero: false };
  }
  // how far (px, summed over the four edges) rect `r` runs outside rect `st`
  function overflowPx(r, st) {
    return Math.max(0, st.left - r.left) + Math.max(0, r.right - st.right) +
      Math.max(0, st.top - r.top) + Math.max(0, r.bottom - st.bottom);
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
  // Canvas art — static layers are pre-rendered once per size/theme.
  // (Everything below touches the DOM; only called from instances.)
  // ======================================================================
  var LIGHT = [-0.6, -0.8];          // unit vector towards the light (top-left)
  var ARM0 = Math.PI / 4;            // turret arm angle offset
  var MAX_CANVAS = 2048;
  var FONT = '"Segoe UI", "Helvetica Neue", Arial, sans-serif';

  function mk(w, h) {
    var c = document.createElement('canvas');
    c.width = Math.max(1, Math.ceil(w)); c.height = Math.max(1, Math.ceil(h == null ? w : h));
    return c;
  }
  function circle(g, r) { g.beginPath(); g.arc(0, 0, r, 0, TAU); }
  function ringPath(g, r1, r2) { g.beginPath(); g.arc(0, 0, r2, 0, TAU, false); g.arc(0, 0, r1, TAU, 0, true); g.closePath(); }
  function wedgePath(g, r1, r2, a0, a1) {   // screen angles (clockwise from 12)
    var h = Math.PI / 2;
    g.beginPath(); g.arc(0, 0, r2, a0 - h, a1 - h, false); g.arc(0, 0, r1, a1 - h, a0 - h, true); g.closePath();
  }
  function radGrad(g, r0, r1, stops) {
    var gr = g.createRadialGradient(0, 0, r0, 0, 0, r1);
    for (var i = 0; i < stops.length; i += 2) gr.addColorStop(stops[i], stops[i + 1]);
    return gr;
  }
  function dirGrad(g, r, hi, lo) {
    var gr = g.createLinearGradient(-r * 0.6, -r * 0.8, r * 0.6, r * 0.8);
    gr.addColorStop(0, 'rgba(255,255,255,' + hi + ')');
    gr.addColorStop(0.46, 'rgba(255,255,255,0)');
    gr.addColorStop(0.54, 'rgba(0,0,0,0)');
    gr.addColorStop(1, 'rgba(0,0,0,' + lo + ')');
    return gr;
  }
  function metalGrad(g, r, th) {   // directional metal (for static rings)
    var gr = g.createLinearGradient(-r * 0.6, -r * 0.8, r * 0.6, r * 0.8);
    gr.addColorStop(0, th.trimHi); gr.addColorStop(0.3, th.trim); gr.addColorStop(0.62, th.trimLo);
    gr.addColorStop(0.82, th.trim); gr.addColorStop(1, th.trimLo);
    return gr;
  }
  // Soft, tapered specular streak along an arc (degrees, clockwise from 12).
  function specArc(g, r, d0, d1, lw, alpha) {
    var a0 = d0 * Math.PI / 180, a1 = d1 * Math.PI / 180;
    var gr = g.createLinearGradient(r * Math.sin(a0), -r * Math.cos(a0), r * Math.sin(a1), -r * Math.cos(a1));
    gr.addColorStop(0, 'rgba(255,255,255,0)'); gr.addColorStop(0.5, 'rgba(255,255,255,1)'); gr.addColorStop(1, 'rgba(255,255,255,0)');
    g.save(); g.strokeStyle = gr; g.lineCap = 'round';
    var passes = [[1, 0.25], [0.55, 0.45], [0.2, 1]];
    for (var i = 0; i < passes.length; i++) {
      g.globalAlpha = alpha * passes[i][1]; g.lineWidth = Math.max(0.5, lw * passes[i][0]);
      g.beginPath(); g.arc(0, 0, r, a0 - Math.PI / 2, a1 - Math.PI / 2, false); g.stroke();
    }
    g.restore();
  }
  function roundRectPath(g, x, y, w, h, r) {
    r = Math.min(r, w / 2, h / 2);
    g.beginPath(); g.moveTo(x + r, y); g.lineTo(x + w - r, y); g.arcTo(x + w, y, x + w, y + r, r);
    g.lineTo(x + w, y + h - r); g.arcTo(x + w, y + h, x + w - r, y + h, r); g.lineTo(x + r, y + h);
    g.arcTo(x, y + h, x, y + h - r, r); g.lineTo(x, y + r); g.arcTo(x, y, x + r, y, r); g.closePath();
  }
  function sphere(g, x, y, r, hi, mid, lo, edge) {
    var gr = g.createRadialGradient(x - r * 0.36, y - r * 0.42, r * 0.05, x, y, r);
    gr.addColorStop(0, hi); gr.addColorStop(0.3, mid); gr.addColorStop(0.82, lo); gr.addColorStop(1, edge);
    g.fillStyle = gr; g.beginPath(); g.arc(x, y, r, 0, TAU); g.fill();
  }

  function drawDeflector(g, R, a, vertical, th) {
    var ly = R * (vertical ? 0.054 : 0.023), lx = R * (vertical ? 0.019 : 0.049);
    var pts = [[0, -ly], [lx, 0], [0, ly], [-lx, 0]];
    g.save(); g.rotate(a); g.translate(0, -R * GEOM.DEFL_R);
    function diamond() { g.beginPath(); g.moveTo(pts[0][0], pts[0][1]); for (var k = 1; k < 4; k++) g.lineTo(pts[k][0], pts[k][1]); g.closePath(); }
    g.save();
    g.shadowColor = th.fretGlow ? th.fretGlow : 'rgba(0,0,0,0.75)';
    g.shadowBlur = R * (th.fretGlow ? 0.035 : 0.02);
    if (!th.fretGlow) { g.shadowOffsetX = R * 0.008; g.shadowOffsetY = R * 0.012; }
    g.fillStyle = th.fretLo; diamond(); g.fill();
    g.restore();
    var lo = hexRgb(th.fretLo), hi = hexRgb(th.fretHi), ca = Math.cos(a), sa = Math.sin(a);
    for (var f = 0; f < 4; f++) {
      var p0 = pts[f], p1 = pts[(f + 1) % 4];
      var nx = p1[1] - p0[1], ny = -(p1[0] - p0[0]), nl = Math.sqrt(nx * nx + ny * ny) || 1;
      nx /= nl; ny /= nl;
      var wx = nx * ca - ny * sa, wy = nx * sa + ny * ca;
      var bright = clamp(0.5 + 0.6 * (wx * LIGHT[0] + wy * LIGHT[1]), 0, 1);
      g.fillStyle = css(mixRgb(lo, hi, Math.pow(bright, 1.15)));
      g.beginPath(); g.moveTo(0, 0); g.lineTo(p0[0], p0[1]); g.lineTo(p1[0], p1[1]); g.closePath(); g.fill();
    }
    g.lineWidth = Math.max(0.5, R * 0.0022);
    g.strokeStyle = 'rgba(255,255,255,0.35)';
    g.beginPath(); g.moveTo(pts[0][0], pts[0][1]); g.lineTo(pts[2][0], pts[2][1]);
    g.moveTo(pts[1][0], pts[1][1]); g.lineTo(pts[3][0], pts[3][1]); g.stroke();
    g.strokeStyle = 'rgba(0,0,0,0.5)'; diamond(); g.stroke();
    g.fillStyle = 'rgba(255,255,255,0.85)'; g.beginPath(); g.arc(0, 0, Math.max(0.6, R * 0.0045), 0, TAU); g.fill();
    g.restore();
  }

  // --- static bowl: shadow, wooden rim, ball track, slope, deflectors -------
  function buildBowl(S, R, th) {
    var cv = mk(S), g = cv.getContext('2d'), h = S / 2, k, j;
    var rnd = mulberry32(hashStr('bowl-' + th.label));
    g.translate(h, h);

    // drop shadow + contact shadow
    g.save();
    g.fillStyle = th.rimB;
    g.shadowColor = 'rgba(0,0,0,0.55)'; g.shadowBlur = R * 0.13; g.shadowOffsetX = R * 0.02; g.shadowOffsetY = R * 0.06;
    circle(g, R * 0.99); g.fill();
    g.shadowColor = 'rgba(0,0,0,0.7)'; g.shadowBlur = R * 0.03; g.shadowOffsetX = 0; g.shadowOffsetY = R * 0.015;
    g.fill();
    g.restore();
    if (th.edgeGlow) {
      g.save(); g.shadowColor = th.edgeGlow; g.shadowBlur = R * 0.1;
      g.strokeStyle = th.edgeGlow; g.lineWidth = R * 0.014; circle(g, R * 0.993); g.stroke(); g.stroke();
      g.restore();
    }

    // rim: segmented, lacquered wood veneer
    var nSeg = 16, off = rnd() * TAU, rimA = hexRgb(th.rimA);
    var grain = hexRgb(th.grain), grainHi = hexRgb(th.grainHi);
    for (k = 0; k < nSeg; k++) {
      var a0 = off + k * TAU / nSeg, a1 = a0 + TAU / nSeg, tone = (rnd() - 0.5) * 0.18;
      g.fillStyle = css(tone >= 0 ? mixRgb(rimA, [255, 255, 255], tone * 0.6) : mixRgb(rimA, [0, 0, 0], -tone));
      wedgePath(g, R * GEOM.RIM_IN, R, a0, a1); g.fill();
      g.save(); wedgePath(g, R * GEOM.RIM_IN, R, a0, a1); g.clip();
      for (j = 0; j < 30; j++) {
        var rr = R * (GEOM.RIM_IN + (1 - GEOM.RIM_IN) * rnd());
        var dark = rnd() < 0.72;
        g.strokeStyle = dark ? rgba(grain, (0.10 + 0.25 * rnd()).toFixed(3)) : rgba(grainHi, (0.03 + 0.07 * rnd()).toFixed(3));
        g.lineWidth = Math.max(0.5, R * (0.0012 + 0.0038 * rnd()));
        var wob = (rnd() - 0.5) * 0.02 * R;
        g.beginPath(); g.arc(0, wob * 0.2, rr, a0 - Math.PI / 2 - 0.02, a1 - Math.PI / 2 + 0.02); g.stroke();
      }
      g.restore();
    }
    g.lineWidth = Math.max(0.6, R * 0.0035);
    for (k = 0; k < nSeg; k++) {
      var aj = off + k * TAU / nSeg, sx = Math.sin(aj), sy = -Math.cos(aj);
      g.strokeStyle = 'rgba(0,0,0,0.5)';
      g.beginPath(); g.moveTo(sx * R * GEOM.RIM_IN, sy * R * GEOM.RIM_IN); g.lineTo(sx * R, sy * R); g.stroke();
    }
    // lacquer: rounded profile + directional light + specular streaks
    g.fillStyle = radGrad(g, R * GEOM.RIM_IN, R, [0, 'rgba(0,0,0,0.5)', 0.16, 'rgba(0,0,0,0.08)', 0.5, 'rgba(255,255,255,0.08)',
      0.8, 'rgba(0,0,0,0)', 0.95, 'rgba(0,0,0,0.25)', 1, 'rgba(0,0,0,0.6)']);
    ringPath(g, R * GEOM.RIM_IN, R); g.fill();
    g.fillStyle = dirGrad(g, R, 0.2, 0.34); ringPath(g, R * GEOM.RIM_IN, R); g.fill();
    specArc(g, R * 0.955, 282, 346, R * 0.045, 0.22);
    specArc(g, R * 0.975, 292, 330, R * 0.01, 0.55);
    specArc(g, R * 0.95, 112, 150, R * 0.03, 0.07);
    g.lineWidth = Math.max(0.6, R * 0.006); g.strokeStyle = 'rgba(0,0,0,0.65)'; circle(g, R * 0.997); g.stroke();
    if (th.edgeGlow) { g.lineWidth = Math.max(0.8, R * 0.006); g.strokeStyle = th.edgeGlow; circle(g, R * 0.994); g.stroke(); }

    // ball track (polished)
    g.fillStyle = th.track; ringPath(g, R * GEOM.TRACK_IN, R * GEOM.RIM_IN); g.fill();
    g.fillStyle = radGrad(g, R * GEOM.TRACK_IN, R * GEOM.RIM_IN, [0, 'rgba(0,0,0,0.35)', 0.3, 'rgba(255,255,255,0.05)',
      0.62, 'rgba(255,255,255,0.03)', 0.86, 'rgba(0,0,0,0.3)', 1, 'rgba(0,0,0,0.7)']);
    ringPath(g, R * GEOM.TRACK_IN, R * GEOM.RIM_IN); g.fill();
    g.fillStyle = dirGrad(g, R, 0.16, 0.25); ringPath(g, R * GEOM.TRACK_IN, R * GEOM.RIM_IN); g.fill();
    specArc(g, R * GEOM.TRACK_R, 282, 346, R * 0.055, 0.26);
    specArc(g, R * (GEOM.TRACK_R + 0.012), 294, 334, R * 0.008, 0.6);
    specArc(g, R * GEOM.TRACK_R, 112, 158, R * 0.04, 0.09);

    // slope with the deflectors
    g.fillStyle = th.slope; ringPath(g, R * GEOM.SLOPE_IN, R * GEOM.TRACK_IN); g.fill();
    g.fillStyle = radGrad(g, R * GEOM.SLOPE_IN, R * GEOM.TRACK_IN, [0, 'rgba(0,0,0,0.6)', 0.25, 'rgba(0,0,0,0.25)',
      0.8, 'rgba(255,255,255,0.05)', 1, 'rgba(0,0,0,0.2)']);
    ringPath(g, R * GEOM.SLOPE_IN, R * GEOM.TRACK_IN); g.fill();
    g.lineWidth = Math.max(0.5, R * 0.0015);
    for (k = 0; k < 14; k++) {
      g.strokeStyle = 'rgba(255,255,255,' + (0.012 + 0.02 * rnd()).toFixed(3) + ')';
      circle(g, R * (GEOM.SLOPE_IN + (GEOM.TRACK_IN - GEOM.SLOPE_IN) * rnd())); g.stroke();
    }
    g.fillStyle = dirGrad(g, R, 0.1, 0.22); ringPath(g, R * GEOM.SLOPE_IN, R * GEOM.TRACK_IN); g.fill();
    // lip between track and slope
    g.lineWidth = Math.max(0.6, R * 0.004); g.strokeStyle = 'rgba(0,0,0,0.55)'; circle(g, R * (GEOM.TRACK_IN - 0.003)); g.stroke();
    g.lineWidth = Math.max(0.5, R * 0.003); g.strokeStyle = 'rgba(255,255,255,0.2)'; circle(g, R * GEOM.TRACK_IN); g.stroke();
    for (k = 0; k < DEFL_N; k++) drawDeflector(g, R, deflectorAngle(k), k % 2 === 0, th);

    // gold/steel trim ring between rim and track
    g.lineWidth = Math.max(1, R * 0.017); g.strokeStyle = metalGrad(g, R, th); circle(g, R * GEOM.RIM_IN); g.stroke();
    g.lineWidth = Math.max(0.5, R * 0.003); g.strokeStyle = 'rgba(0,0,0,0.55)';
    circle(g, R * (GEOM.RIM_IN - 0.0095)); g.stroke();
    circle(g, R * (GEOM.RIM_IN + 0.0095)); g.stroke();
    if (th.edgeGlow) {
      g.save(); g.shadowColor = th.edgeGlow; g.shadowBlur = R * 0.04; g.lineWidth = Math.max(0.8, R * 0.005);
      g.strokeStyle = th.edgeGlow; circle(g, R * GEOM.RIM_IN); g.stroke(); g.restore();
    }
    return cv;
  }

  // --- rotating head: number ring, pockets, frets, cone, turret arms --------
  function buildHead(R, th, cols, order) {
    var N = order.length, step = TAU / N, i, k;
    var half = Math.ceil(R * (GEOM.HEAD_R + 0.03)) + 2, cv = mk(half * 2), g = cv.getContext('2d');
    g.translate(half, half);
    var pcol = [], pdeep = [];
    for (i = 0; i < N; i++) {
      var hex = cols[colorOf(order[i])];
      pcol[i] = hex; pdeep[i] = shade(hex, -0.22);
    }
    // rotor body + ambient shadow onto the bowl (rotation invariant)
    g.save(); g.shadowColor = 'rgba(0,0,0,0.7)'; g.shadowBlur = R * 0.025;
    g.fillStyle = th.fretLo; circle(g, R * GEOM.HEAD_R); g.fill(); g.restore();
    g.fillStyle = radGrad(g, R * GEOM.NUM_OUT, R * GEOM.HEAD_R, [0, th.fretLo, 0.5, th.fretHi, 1, th.fretLo]);
    ringPath(g, R * GEOM.NUM_OUT, R * GEOM.HEAD_R); g.fill();

    // number ring
    for (i = 0; i < N; i++) {
      g.fillStyle = pcol[i]; wedgePath(g, R * GEOM.NUM_IN, R * GEOM.NUM_OUT, i * step - step / 2 - 0.002, i * step + step / 2 + 0.002); g.fill();
    }
    g.fillStyle = radGrad(g, R * GEOM.NUM_IN, R * GEOM.NUM_OUT, [0, 'rgba(0,0,0,0.22)', 0.35, 'rgba(255,255,255,0.03)', 0.8, 'rgba(255,255,255,0.07)', 1, 'rgba(0,0,0,0.2)']);
    ringPath(g, R * GEOM.NUM_IN, R * GEOM.NUM_OUT); g.fill();
    g.lineWidth = Math.max(0.7, R * 0.004); g.strokeStyle = th.fret;
    for (i = 0; i < N; i++) {
      var sa = Math.sin(i * step + step / 2), ca = -Math.cos(i * step + step / 2);
      g.beginPath(); g.moveTo(sa * R * GEOM.NUM_IN, ca * R * GEOM.NUM_IN); g.lineTo(sa * R * GEOM.NUM_OUT, ca * R * GEOM.NUM_OUT); g.stroke();
    }
    // numbers: white, radial, readable
    var fs = R * 0.063, rn = R * (GEOM.NUM_IN + GEOM.NUM_OUT) / 2;
    g.save();
    g.font = '700 ' + fs.toFixed(2) + 'px ' + FONT;
    g.textAlign = 'center'; g.textBaseline = 'middle'; g.fillStyle = '#ffffff';
    g.shadowColor = 'rgba(0,0,0,0.6)'; g.shadowBlur = Math.max(1, R * 0.008);
    for (i = 0; i < N; i++) {
      g.save(); g.rotate(i * step); g.translate(0, -rn);
      if (order[i].length > 1) g.scale(0.8, 1);
      g.fillText(order[i], 0, fs * 0.05);
      g.restore();
    }
    g.restore();

    // pockets: coloured floors, concave shading
    for (i = 0; i < N; i++) {
      g.fillStyle = pdeep[i];
      wedgePath(g, R * GEOM.POCKET_IN, R * GEOM.POCKET_OUT, i * step - step / 2 - 0.002, i * step + step / 2 + 0.002); g.fill();
    }
    var hw = R * GEOM.POCKET_OUT * Math.sin(step / 2);
    for (i = 0; i < N; i++) {
      g.save(); g.rotate(i * step);
      wedgePath(g, R * GEOM.POCKET_IN, R * GEOM.POCKET_OUT, -step / 2, step / 2); g.clip();
      var cg = g.createLinearGradient(-hw, 0, hw, 0);
      cg.addColorStop(0, 'rgba(0,0,0,0.55)'); cg.addColorStop(0.22, 'rgba(0,0,0,0.08)');
      cg.addColorStop(0.5, 'rgba(255,255,255,0.07)'); cg.addColorStop(0.78, 'rgba(0,0,0,0.08)'); cg.addColorStop(1, 'rgba(0,0,0,0.55)');
      g.fillStyle = cg; g.fillRect(-hw - 2, -R * GEOM.POCKET_OUT - 2, hw * 2 + 4, R * (GEOM.POCKET_OUT - GEOM.POCKET_IN) + 4);
      g.restore();
    }
    // inner metal ring of the pocket ring + separator ring under the numbers
    g.lineWidth = Math.max(1, R * 0.014);
    g.strokeStyle = radGrad(g, R * (GEOM.POCKET_IN - 0.01), R * (GEOM.POCKET_IN + 0.01), [0, th.fretLo, 0.5, th.fretHi, 1, th.fretLo]);
    circle(g, R * GEOM.POCKET_IN); g.stroke();
    g.lineWidth = Math.max(0.8, R * 0.007);
    g.strokeStyle = radGrad(g, R * (GEOM.NUM_IN - 0.005), R * (GEOM.NUM_IN + 0.005), [0, th.fretLo, 0.5, th.fretHi, 1, th.fretLo]);
    circle(g, R * GEOM.NUM_IN); g.stroke();

    // frets: polished metal bars with a rounded cap at the outer end
    var fw = Math.max(1.3, R * 0.012), y0 = -R * (GEOM.POCKET_OUT + 0.006), y1 = -R * (GEOM.POCKET_IN - 0.004);
    var fg = g.createLinearGradient(-fw / 2, 0, fw / 2, 0);
    fg.addColorStop(0, th.fretLo); fg.addColorStop(0.42, th.fretHi); fg.addColorStop(0.62, th.fret); fg.addColorStop(1, th.fretLo);
    for (i = 0; i < N; i++) {
      g.save(); g.rotate(i * step + step / 2);
      g.save();
      g.shadowColor = th.fretGlow || 'rgba(0,0,0,0.75)'; g.shadowBlur = R * (th.fretGlow ? 0.02 : 0.012);
      g.fillStyle = fg; roundRectPath(g, -fw / 2, y0, fw, y1 - y0, fw / 2); g.fill();
      g.restore();
      sphere(g, 0, y0 + fw * 0.2, fw * 0.95, th.fretHi, th.fret, th.fretLo, shade(th.fretLo, -0.4));
      g.restore();
    }

    // cone
    g.fillStyle = radGrad(g, R * 0.08, R * GEOM.CONE_OUT, [0, th.coneB, 0.55, th.coneA, 0.9, shade(th.coneA, -0.1), 1, shade(th.coneA, -0.35)]);
    circle(g, R * GEOM.CONE_OUT); g.fill();
    for (k = 0; k < 16; k++) {
      g.fillStyle = k % 2 ? 'rgba(0,0,0,0.12)' : 'rgba(255,255,255,0.035)';
      wedgePath(g, R * 0.105, R * (GEOM.CONE_OUT - 0.02), k * TAU / 16, (k + 1) * TAU / 16); g.fill();
    }
    var crnd = mulberry32(hashStr('cone-' + th.label));
    g.lineWidth = Math.max(0.5, R * 0.0014);
    for (k = 0; k < 26; k++) {
      g.strokeStyle = (crnd() < 0.6 ? 'rgba(0,0,0,' : 'rgba(255,255,255,') + (0.03 + 0.06 * crnd()).toFixed(3) + ')';
      circle(g, R * (0.11 + (GEOM.CONE_OUT - 0.13) * crnd())); g.stroke();
    }
    g.lineWidth = Math.max(0.7, R * 0.005);
    g.strokeStyle = th.inlay; circle(g, R * 0.36); g.stroke();
    g.lineWidth = Math.max(0.5, R * 0.0025); circle(g, R * 0.345); g.stroke();
    g.strokeStyle = 'rgba(0,0,0,0.5)'; circle(g, R * (GEOM.CONE_OUT - 0.004)); g.stroke();

    // turret arms (knobs + dome are drawn per frame with static lighting)
    for (k = 0; k < 4; k++) {
      g.save(); g.rotate(ARM0 + k * Math.PI / 2);
      var bw = R * 0.030, tw = R * 0.013;
      g.beginPath();
      g.moveTo(-bw, -R * 0.07); g.lineTo(-tw, -R * (GEOM.ARM_R - 0.02));
      g.quadraticCurveTo(0, -R * (GEOM.ARM_R + 0.012), tw, -R * (GEOM.ARM_R - 0.02));
      g.lineTo(bw, -R * 0.07); g.closePath();
      g.save(); g.shadowColor = 'rgba(0,0,0,0.7)'; g.shadowBlur = R * 0.02;
      var ag = g.createLinearGradient(-bw, 0, bw, 0);
      ag.addColorStop(0, th.fretLo); ag.addColorStop(0.4, th.fretHi); ag.addColorStop(0.58, th.fret); ag.addColorStop(1, shade(th.fretLo, -0.25));
      g.fillStyle = ag; g.fill(); g.restore();
      g.lineWidth = Math.max(0.5, R * 0.002); g.strokeStyle = 'rgba(0,0,0,0.35)'; g.stroke();
      g.restore();
    }
    return { cv: cv, half: half };
  }

  // --- static lighting over the rotor (does not rotate with the head) -------
  function buildLight(S, R, th) {
    var cv = mk(S), g = cv.getContext('2d'), h = S / 2;
    g.translate(h, h);
    // rotor shadow onto the slope
    g.fillStyle = radGrad(g, R * GEOM.HEAD_R, R * (GEOM.HEAD_R + 0.03), [0, 'rgba(0,0,0,0.55)', 1, 'rgba(0,0,0,0)']);
    ringPath(g, R * GEOM.HEAD_R, R * (GEOM.HEAD_R + 0.03)); g.fill();
    // number ring bevel + sheen
    g.fillStyle = dirGrad(g, R * 0.75, 0.16, 0.3); ringPath(g, R * GEOM.CONE_OUT, R * GEOM.HEAD_R); g.fill();
    // pocket depth: dark under the number-ring lip and at the inner rim
    g.fillStyle = radGrad(g, R * GEOM.POCKET_IN, R * GEOM.POCKET_OUT, [0, 'rgba(0,0,0,0.35)', 0.2, 'rgba(0,0,0,0)',
      0.7, 'rgba(0,0,0,0.06)', 0.9, 'rgba(0,0,0,0.28)', 1, 'rgba(0,0,0,0.55)']);
    ringPath(g, R * GEOM.POCKET_IN, R * GEOM.POCKET_OUT); g.fill();
    specArc(g, R * (GEOM.NUM_IN + GEOM.NUM_OUT) / 2, 295, 335, R * 0.05, 0.1);
    specArc(g, R * (GEOM.HEAD_R - 0.005), 290, 340, R * 0.008, 0.5);
    // cone: rises towards the centre -> lit top-left, shaded bottom-right
    g.fillStyle = dirGrad(g, R * GEOM.CONE_OUT, 0.24, 0.42); circle(g, R * GEOM.CONE_OUT); g.fill();
    g.fillStyle = radGrad(g, R * (GEOM.CONE_OUT - 0.07), R * GEOM.CONE_OUT, [0, 'rgba(0,0,0,0)', 1, 'rgba(0,0,0,0.45)']);
    circle(g, R * GEOM.CONE_OUT); g.fill();
    var sg = g.createRadialGradient(-R * 0.2, -R * 0.24, 0, -R * 0.2, -R * 0.24, R * 0.2);
    sg.addColorStop(0, 'rgba(255,255,255,0.16)'); sg.addColorStop(1, 'rgba(255,255,255,0)');
    g.fillStyle = sg; circle(g, R * GEOM.CONE_OUT); g.fill();
    if (th.fretGlow) {
      g.save(); g.shadowColor = th.fretGlow; g.shadowBlur = R * 0.05; g.lineWidth = Math.max(0.8, R * 0.004);
      g.strokeStyle = th.fretGlow; circle(g, R * (GEOM.HEAD_R - 0.002)); g.stroke(); g.restore();
    }
    return cv;
  }

  // --- small sprites (drawn upright each frame so their highlights stay put) --
  function buildKnob(R, th) {
    var kr = R * 0.037, half = Math.ceil(kr * 1.9) + 2, cv = mk(half * 2), g = cv.getContext('2d');
    g.translate(half, half);
    g.save(); g.shadowColor = 'rgba(0,0,0,0.6)'; g.shadowBlur = R * 0.014; g.shadowOffsetX = R * 0.006; g.shadowOffsetY = R * 0.01;
    g.fillStyle = th.fretLo; circle(g, kr * 0.96); g.fill(); g.restore();
    sphere(g, 0, 0, kr, th.fretHi, th.fret, th.fretLo, shade(th.fretLo, -0.45));
    g.fillStyle = 'rgba(255,255,255,0.9)'; g.beginPath(); g.arc(-kr * 0.34, -kr * 0.4, kr * 0.16, 0, TAU); g.fill();
    return { cv: cv, half: half };
  }
  function buildDome(R, th) {
    var dr = R * GEOM.TURRET_R, half = Math.ceil(dr * 1.4) + 2, cv = mk(half * 2), g = cv.getContext('2d');
    g.translate(half, half);
    g.save(); g.shadowColor = 'rgba(0,0,0,0.7)'; g.shadowBlur = R * 0.03; g.shadowOffsetX = R * 0.008; g.shadowOffsetY = R * 0.012;
    g.fillStyle = th.fretLo; circle(g, dr); g.fill(); g.restore();
    var gr = g.createLinearGradient(-dr, -dr, dr, dr);
    gr.addColorStop(0, th.fretHi); gr.addColorStop(0.35, th.fret); gr.addColorStop(0.7, th.fretLo); gr.addColorStop(1, shade(th.fretLo, -0.3));
    g.fillStyle = gr; circle(g, dr); g.fill();
    g.lineWidth = Math.max(0.5, R * 0.003); g.strokeStyle = 'rgba(0,0,0,0.45)'; circle(g, dr * 0.99); g.stroke();
    g.fillStyle = radGrad(g, dr * 0.55, dr * 0.8, [0, 'rgba(0,0,0,0)', 0.5, 'rgba(0,0,0,0.35)', 1, 'rgba(0,0,0,0)']);
    circle(g, dr * 0.8); g.fill();
    sphere(g, 0, 0, dr * 0.62, th.fretHi, th.fret, th.fretLo, shade(th.fretLo, -0.35));
    sphere(g, 0, 0, dr * 0.24, '#ffffff', th.fretHi, th.fret, th.fretLo);
    g.fillStyle = 'rgba(255,255,255,0.8)'; g.beginPath(); g.arc(-dr * 0.22, -dr * 0.27, dr * 0.1, 0, TAU); g.fill();
    return { cv: cv, half: half };
  }
  function buildBall(R) {
    var br = R * GEOM.BALL_R * 1.6, half = Math.ceil(br) + 2, cv = mk(half * 2), g = cv.getContext('2d');
    var scale = br / (R * GEOM.BALL_R);   // sprite is drawn larger, then scaled down (crisper)
    g.translate(half, half);
    var gr = g.createRadialGradient(-br * 0.36, -br * 0.42, br * 0.04, 0, 0, br);
    gr.addColorStop(0, '#ffffff'); gr.addColorStop(0.28, '#f7f5ef'); gr.addColorStop(0.62, '#d9d5cc');
    gr.addColorStop(0.88, '#9e9a92'); gr.addColorStop(1, '#6f6b65');
    g.fillStyle = gr; circle(g, br); g.fill();
    var rl = g.createRadialGradient(br * 0.45, br * 0.5, 0, br * 0.45, br * 0.5, br * 0.6);
    rl.addColorStop(0, 'rgba(255,255,255,0.28)'); rl.addColorStop(1, 'rgba(255,255,255,0)');
    g.save(); circle(g, br); g.clip(); g.fillStyle = rl; g.fillRect(-br, -br, br * 2, br * 2); g.restore();
    var sp = g.createRadialGradient(-br * 0.34, -br * 0.4, 0, -br * 0.34, -br * 0.4, br * 0.3);
    sp.addColorStop(0, 'rgba(255,255,255,1)'); sp.addColorStop(0.45, 'rgba(255,255,255,0.85)'); sp.addColorStop(1, 'rgba(255,255,255,0)');
    g.fillStyle = sp; circle(g, br); g.fill();
    g.lineWidth = Math.max(0.5, br * 0.05); g.strokeStyle = 'rgba(0,0,0,0.25)'; circle(g, br * 0.985); g.stroke();
    return { cv: cv, half: half / scale };
  }
  function buildShadow(R) {
    var sr = R * GEOM.BALL_R * 1.55, half = Math.ceil(sr) + 1, cv = mk(half * 2), g = cv.getContext('2d');
    g.translate(half, half);
    g.fillStyle = radGrad(g, 0, sr, [0, 'rgba(0,0,0,0.9)', 0.42, 'rgba(0,0,0,0.62)', 0.75, 'rgba(0,0,0,0.2)', 1, 'rgba(0,0,0,0)']);
    circle(g, sr); g.fill();
    return { cv: cv, half: half };
  }
  // `c` is an [r, g, b] array (NOT a CSS string: the glow colour is derived
  // with shadeRgb, and a css() 'rgb(...)' string would not parse as hex).
  function buildGlow(R, N, c) {
    var step = TAU / N, pad = R * 0.07;
    var halfW = R * GEOM.HEAD_R * Math.sin(step / 2) + pad;
    var top = R * GEOM.HEAD_R + pad, bot = R * GEOM.POCKET_IN * Math.cos(step / 2) - pad;
    var cv = mk(halfW * 2, top - bot), g = cv.getContext('2d');
    g.translate(halfW, top);
    g.save(); g.shadowColor = rgba(c, 1); g.shadowBlur = R * 0.06;
    g.fillStyle = rgba(c, 0.5);
    wedgePath(g, R * GEOM.POCKET_IN, R * GEOM.HEAD_R, -step / 2, step / 2); g.fill(); g.fill();
    g.restore();
    g.lineWidth = Math.max(1, R * 0.007); g.strokeStyle = 'rgba(255,255,255,0.95)';
    wedgePath(g, R * (GEOM.POCKET_IN + 0.003), R * (GEOM.HEAD_R - 0.003), -step / 2 + 0.004, step / 2 - 0.004); g.stroke();
    return { cv: cv, x: -halfW, y: -top };
  }

  // ======================================================================
  // Sound — WebAudio, synthesized, created lazily; failures are silent.
  // ======================================================================
  function noop() { }
  function Sfx() { this.ctx = null; this.master = null; this.failed = false; this.rum = null; this.lastLevel = -1; }
  Sfx.prototype.ensure = function (vol) {
    if (this.failed) return false;
    if (!this.ctx) {
      var AC = root.AudioContext || root.webkitAudioContext;
      if (!AC) { this.failed = true; return false; }
      try {
        var ctx = new AC();
        this.ctx = ctx;
        this.master = ctx.createGain(); this.master.gain.value = clamp(+vol || 0, 0, 1); this.master.connect(ctx.destination);
        this.bufNoise = makeNoise(ctx, 2);
        this.bufTick = makeHit(ctx, false);
        this.bufClack = makeHit(ctx, true);
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
  Sfx.prototype.startRumble = function () {
    if (!this.ctx || this.rum) return;
    try {
      var c = this.ctx, src = c.createBufferSource(), hp = c.createBiquadFilter(), lp = c.createBiquadFilter(), g = c.createGain();
      src.buffer = this.bufNoise; src.loop = true;
      hp.type = 'highpass'; hp.frequency.value = 60;
      lp.type = 'lowpass'; lp.frequency.value = 500; lp.Q.value = 0.8;
      g.gain.value = 0;
      src.connect(hp); hp.connect(lp); lp.connect(g); g.connect(this.master);
      src.start();
      this.rum = src; this.rumGain = g; this.rumFilter = lp; this.lastLevel = 0;
    } catch (e) { this.rum = null; }
  };
  Sfx.prototype.rumble = function (level, bright) {
    if (!this.rum) return;
    if (Math.abs(level - this.lastLevel) < 0.008) return;
    this.lastLevel = level;
    try {
      var now = this.ctx.currentTime;
      this.rumGain.gain.setTargetAtTime(level, now, 0.07);
      this.rumFilter.frequency.setTargetAtTime(220 + 1300 * bright, now, 0.09);
    } catch (e) { }
  };
  Sfx.prototype.stopRumble = function () {
    if (!this.rum) return;
    var src = this.rum, g = this.rumGain;
    this.rum = null;
    try { g.gain.setTargetAtTime(0, this.ctx.currentTime, 0.05); src.stop(this.ctx.currentTime + 0.5); } catch (e) { }
  };
  Sfx.prototype.hit = function (heavy, vol, rate) {
    if (!this.ctx || this.ctx.state !== 'running' || !(vol > 0.005)) return;
    try {
      var c = this.ctx, s = c.createBufferSource(), g = c.createGain();
      s.buffer = heavy ? this.bufClack : this.bufTick;
      s.playbackRate.value = rate || 1;
      g.gain.value = clamp(vol, 0, 1.2);
      s.connect(g); g.connect(this.master); s.start();
    } catch (e) { }
  };
  Sfx.prototype.close = function () {
    this.stopRumble();
    if (this.ctx) { try { var p = this.ctx.close(); if (p && p.catch) p.catch(noop); } catch (e) { } }
    this.ctx = null;
  };
  function makeNoise(ctx, secs) {
    var n = Math.floor(ctx.sampleRate * secs), buf = ctx.createBuffer(1, n, ctx.sampleRate), d = buf.getChannelData(0), last = 0;
    var r = mulberry32(99);
    for (var i = 0; i < n; i++) { last = (last + 0.035 * (r() * 2 - 1)) / 1.035; d[i] = last * 3.2; }
    return buf;
  }
  function makeHit(ctx, heavy) {
    var sr = ctx.sampleRate, n = Math.floor(sr * (heavy ? 0.14 : 0.04)), buf = ctx.createBuffer(1, n, sr), d = buf.getChannelData(0);
    var r = mulberry32(heavy ? 7 : 3), peak = 0, i;
    var f1 = heavy ? 1150 : 3300, f2 = heavy ? 2450 : 5200;
    for (i = 0; i < n; i++) {
      var t = i / sr;
      var v = (r() * 2 - 1) * Math.exp(-t / (heavy ? 0.006 : 0.0022)) * 0.55 +
        Math.sin(TAU * f1 * t) * Math.exp(-t / (heavy ? 0.018 : 0.006)) * 0.5 +
        Math.sin(TAU * f2 * t) * Math.exp(-t / (heavy ? 0.01 : 0.004)) * 0.3;
      if (heavy) v += Math.sin(TAU * 170 * t) * Math.exp(-t / 0.035) * 0.55;
      d[i] = v; if (Math.abs(v) > peak) peak = Math.abs(v);
    }
    for (i = 0; i < n; i++) d[i] = d[i] / (peak || 1) * 0.9;
    return buf;
  }

  // ======================================================================
  // DOM overlays — styles are injected once
  // ======================================================================
  var STYLE = [
    '.hgr-root{position:absolute;left:0;top:0;width:100%;height:100%;pointer-events:none;',
    'font-family:"Segoe UI",system-ui,-apple-system,"Helvetica Neue",Arial,sans-serif;color:#fff;',
    '-webkit-font-smoothing:antialiased;line-height:1.2;}',
    '.hgr-canvas{position:absolute;left:-8%;top:-8%;width:116%;height:116%;display:block;}',
    '.hgr-hide{display:none !important;}',
    '.hgr-center{position:absolute;left:50%;top:50%;width:0;height:0;}',
    '.hgr-center>.hgr-badge{position:absolute;left:0;top:0;transform:translate(-50%,-50%);}',
    '.hgr-top{position:absolute;left:50%;bottom:100%;margin-bottom:24px;transform:translateX(-50%);',
    'display:flex;flex-direction:column-reverse;align-items:center;gap:12px;}',
    '.hgr-bottom{position:absolute;left:50%;top:100%;margin-top:26px;transform:translateX(-50%);',
    'display:flex;flex-direction:column;align-items:center;gap:14px;}',
    '.hgr-side{position:absolute;top:50%;transform:translateY(-50%);}',
    '.hgr-side.hgr-r{left:100%;margin-left:30px;}.hgr-side.hgr-l{right:100%;margin-right:30px;}',
    /* caption */
    '.hgr-cap{display:flex;align-items:center;gap:9px;white-space:nowrap;padding:8px 18px 8px 13px;border-radius:999px;',
    'background:linear-gradient(180deg,rgba(30,30,40,.9),rgba(10,10,14,.9));border:1px solid rgba(255,255,255,.13);',
    'box-shadow:0 10px 26px rgba(0,0,0,.5),inset 0 1px 0 rgba(255,255,255,.07);font-size:18px;font-weight:600;color:#d7d7e2;}',
    '.hgr-cap b{color:var(--hgr-accent);font-weight:800;max-width:260px;overflow:hidden;text-overflow:ellipsis;}',
    '.hgr-cap i{width:9px;height:9px;border-radius:50%;background:var(--hgr-accent);box-shadow:0 0 10px var(--hgr-accent);',
    'animation:hgr-blink 1.1s ease-in-out infinite;}',
    '.hgr-cap.hgr-in{animation:hgr-rise .45s cubic-bezier(.2,.9,.3,1.2) both;}',
    /* result badge */
    '.hgr-badge{display:flex;flex-direction:column;align-items:center;}',
    '.hgr-medal{position:relative;width:var(--hgr-d);height:var(--hgr-d);flex:0 0 auto;}',
    '.hgr-halo{position:absolute;left:-30%;top:-30%;width:160%;height:160%;border-radius:50%;',
    'background:radial-gradient(closest-side,var(--hgr-halo) 0%,var(--hgr-halo-a) 45%,rgba(0,0,0,0) 100%);',
    'animation:hgr-pulse 1.5s ease-in-out infinite;}',
    '.hgr-ring{position:absolute;left:0;top:0;width:100%;height:100%;box-sizing:border-box;border-radius:50%;',
    'padding:var(--hgr-rw);background:var(--hgr-ringbg);',
    'box-shadow:0 12px 34px rgba(0,0,0,.6),0 0 0 1px rgba(0,0,0,.55),inset 0 1px 1px rgba(255,255,255,.5);}',
    '.hgr-disc{position:relative;width:100%;height:100%;border-radius:50%;overflow:hidden;display:flex;',
    'flex-direction:column;align-items:center;justify-content:center;background:var(--hgr-discbg);',
    'box-shadow:inset 0 -8px 16px rgba(0,0,0,.5),inset 0 3px 6px rgba(255,255,255,.18),inset 0 0 0 2px rgba(0,0,0,.35);}',
    '.hgr-disc:after{content:"";position:absolute;left:13%;top:4%;width:74%;height:46%;border-radius:50%;',
    'background:linear-gradient(180deg,rgba(255,255,255,.34),rgba(255,255,255,0));}',
    '.hgr-disc:before{content:"";position:absolute;left:-60%;top:0;width:40%;height:100%;z-index:2;',
    'background:linear-gradient(100deg,rgba(255,255,255,0),rgba(255,255,255,.55),rgba(255,255,255,0));',
    'transform:skewX(-18deg) translateX(-120%);}',
    '.hgr-badge.hgr-in .hgr-disc:before{animation:hgr-shine .9s .35s ease-out both;}',
    '.hgr-num{position:relative;z-index:1;font-size:var(--hgr-fs);font-weight:900;line-height:1;letter-spacing:-.03em;',
    'color:#fff;font-variant-numeric:tabular-nums;text-shadow:0 2px 0 rgba(0,0,0,.35),0 5px 16px rgba(0,0,0,.5);}',
    '.hgr-det{position:relative;z-index:1;margin-top:6px;font-size:11px;font-weight:800;letter-spacing:.12em;line-height:1.35;',
    'text-align:center;color:rgba(255,255,255,.93);white-space:nowrap;text-shadow:0 1px 3px rgba(0,0,0,.7);}',
    '.hgr-pill{margin:12px 0;padding:6px 14px;border-radius:999px;font-size:13px;font-weight:800;letter-spacing:.14em;',
    'white-space:nowrap;color:#fff;background:linear-gradient(180deg,rgba(30,30,40,.92),rgba(10,10,14,.92));',
    'border:1px solid rgba(255,255,255,.14);box-shadow:0 8px 22px rgba(0,0,0,.5);}',
    '.hgr-badge.hgr-in .hgr-medal{animation:hgr-pop .62s cubic-bezier(.18,.9,.25,1.3) both;}',
    '.hgr-badge.hgr-in .hgr-pill{animation:hgr-rise .5s .25s cubic-bezier(.2,.9,.3,1.2) both;}',
    /* history strip */
    '.hgr-hist{display:flex;align-items:center;gap:6px;padding:7px 11px;border-radius:999px;white-space:nowrap;',
    'background:linear-gradient(180deg,rgba(28,28,38,.88),rgba(8,8,12,.88));border:1px solid rgba(255,255,255,.11);',
    'box-shadow:0 10px 24px rgba(0,0,0,.5),inset 0 1px 0 rgba(255,255,255,.06);}',
    '.hgr-hist.hgr-demo{opacity:.55;}',
    '.hgr-chip{width:27px;height:27px;border-radius:50%;flex:0 0 auto;display:flex;align-items:center;justify-content:center;',
    'font-size:12.5px;font-weight:800;letter-spacing:-.02em;color:#fff;text-shadow:0 1px 2px rgba(0,0,0,.6);',
    'box-shadow:inset 0 1px 1px rgba(255,255,255,.3),inset 0 -3px 5px rgba(0,0,0,.45),0 1px 3px rgba(0,0,0,.6);}',
    '.hgr-chip.hgr-red{background:radial-gradient(circle at 35% 28%,var(--hgr-red-hi),var(--hgr-red) 58%,var(--hgr-red-lo));}',
    '.hgr-chip.hgr-black{background:radial-gradient(circle at 35% 28%,var(--hgr-black-hi),var(--hgr-black) 58%,var(--hgr-black-lo));}',
    '.hgr-chip.hgr-green{background:radial-gradient(circle at 35% 28%,var(--hgr-green-hi),var(--hgr-green) 58%,var(--hgr-green-lo));}',
    '.hgr-chip.hgr-new{width:35px;height:35px;font-size:15.5px;margin-right:4px;',
    'box-shadow:0 0 0 2px var(--hgr-accent),0 0 16px var(--hgr-accent-a),inset 0 1px 1px rgba(255,255,255,.3),inset 0 -3px 5px rgba(0,0,0,.45);}',
    '.hgr-hist .hgr-chip:nth-child(n+7){opacity:.85;}.hgr-hist .hgr-chip:nth-child(n+11){opacity:.7;}',
    '.hgr-hist.hgr-anim .hgr-new{animation:hgr-chip .55s cubic-bezier(.2,.9,.3,1.35) both;}',
    /* bets */
    '.hgr-bets{min-width:220px;max-width:310px;padding:11px 14px 9px;border-radius:16px;',
    'background:linear-gradient(180deg,rgba(28,28,38,.92),rgba(8,8,12,.92));border:1px solid rgba(255,255,255,.11);',
    'box-shadow:0 14px 34px rgba(0,0,0,.55),inset 0 1px 0 rgba(255,255,255,.06);}',
    '.hgr-bets.hgr-in{animation:hgr-rise .5s cubic-bezier(.2,.9,.3,1.1) both;}',
    '.hgr-bh{display:flex;justify-content:space-between;align-items:baseline;gap:14px;margin-bottom:6px;padding-bottom:7px;',
    'border-bottom:1px solid rgba(255,255,255,.09);}',
    '.hgr-bh b{font-size:11.5px;letter-spacing:.2em;font-weight:800;color:var(--hgr-accent);}',
    '.hgr-bh span{font-size:11.5px;color:rgba(255,255,255,.55);font-weight:600;}',
    '.hgr-row{display:flex;align-items:center;gap:9px;padding:4px 0;font-size:15px;}',
    '.hgr-bets.hgr-in .hgr-row{animation:hgr-rise .4s cubic-bezier(.2,.9,.3,1.1) both;}',
    '.hgr-row .hgr-u{font-weight:700;color:#fff;max-width:120px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}',
    '.hgr-row .hgr-l{flex:1 1 auto;min-width:0;color:rgba(255,255,255,.58);font-size:12.5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}',
    '.hgr-row .hgr-p{font-weight:800;color:#3ddc84;font-variant-numeric:tabular-nums;white-space:nowrap;}',
    '.hgr-more,.hgr-none{font-size:12px;color:rgba(255,255,255,.55);padding-top:4px;}',
    '.hgr-none{font-size:14px;color:rgba(255,255,255,.78);padding:2px 0 3px;}',
    /* Hex's own card (setAnnounce): same card, signed amounts, free-form title / text-only lines */
    '.hgr-bh b.hgr-at{text-transform:uppercase;line-height:1.3;overflow-wrap:anywhere;}',
    '.hgr-bets.hgr-ann{max-width:330px;}',
    '.hgr-row .hgr-t{flex:1 1 auto;min-width:0;font-weight:700;color:#fff;line-height:1.3;overflow:hidden;',
    'display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow-wrap:anywhere;}',
    '.hgr-row .hgr-p.hgr-neg{color:#ff6b5e;}',
    '.hgr-row .hgr-p.hgr-zero{color:rgba(255,255,255,.45);}',
    /* countdown (setTable: table.auto_spin_in_ms). The ring is a little wheel: 38 pocket
       segments under a sweep in the theme's fret colour that drains to zero */
    '.hgr-cd{display:flex;align-items:center;gap:10px;white-space:nowrap;padding:6px 18px 6px 7px;border-radius:999px;',
    'background:linear-gradient(180deg,rgba(30,30,40,.92),rgba(10,10,14,.92));border:1px solid var(--hgr-edge);',
    'box-shadow:0 10px 26px rgba(0,0,0,.5),0 0 16px var(--hgr-edge-glow),inset 0 1px 0 rgba(255,255,255,.07);}',
    '.hgr-cd svg{width:36px;height:36px;transform:rotate(-90deg);flex:0 0 auto;overflow:visible;filter:var(--hgr-cd-f);}',
    '.hgr-cd circle{fill:none;stroke-width:4;}',
    '.hgr-cd .hgr-ring0{stroke:rgba(255,255,255,.16);stroke-dasharray:1.98 .5;}',
    '.hgr-cd .hgr-ring1{stroke:var(--hgr-cd);stroke-linecap:round;}',
    '.hgr-cd small{display:block;font-size:10.5px;letter-spacing:.2em;font-weight:800;color:var(--hgr-accent);line-height:1.1;}',
    '.hgr-cd b{display:block;font-size:21px;font-weight:800;color:#fff;font-variant-numeric:tabular-nums;line-height:1.05;}',
    '.hgr-cd.hgr-in{animation:hgr-rise .45s cubic-bezier(.2,.9,.3,1.2) both;}',
    '.hgr-cd.hgr-urgent b{color:#ff6b5e;}.hgr-cd.hgr-urgent .hgr-ring1{stroke:#ff3b30;}',
    '.hgr-cd.hgr-urgent svg{filter:var(--hgr-cd-uf);}',
    '.hgr-cd.hgr-urgent{animation:hgr-throb 1s ease-in-out infinite;}',
    /* editor sample, greyed like the sample history (a filter: the pop-ins animate opacity) */
    '.hgr-cd.hgr-demo,.hgr-board.hgr-demo{filter:opacity(.55);}',
    /* the "on the table" board (setTable): the winners card's look, stakes in white; a
       live dot and the theme's edge while the countdown runs */
    '.hgr-board{min-width:230px;max-width:330px;padding:11px 14px 9px;border-radius:16px;',
    'background:linear-gradient(180deg,rgba(28,28,38,.92),rgba(8,8,12,.92));border:1px solid rgba(255,255,255,.11);',
    'box-shadow:0 14px 34px rgba(0,0,0,.55),inset 0 1px 0 rgba(255,255,255,.06);}',
    '.hgr-board.hgr-open{border-color:var(--hgr-edge);',
    'box-shadow:0 14px 34px rgba(0,0,0,.55),0 0 18px var(--hgr-edge-glow),inset 0 1px 0 rgba(255,255,255,.06);}',
    '.hgr-board.hgr-in{animation:hgr-rise .5s cubic-bezier(.2,.9,.3,1.1) both;}',
    '.hgr-board.hgr-in .hgr-row,.hgr-board .hgr-row.hgr-fresh{animation:hgr-rise .4s cubic-bezier(.2,.9,.3,1.1) both;}',
    '.hgr-dot{display:inline-block;width:7px;height:7px;margin:0 8px 1px 0;border-radius:50%;vertical-align:middle;',
    'background:var(--hgr-accent);box-shadow:0 0 8px var(--hgr-accent);animation:hgr-blink 1.1s ease-in-out infinite;}',
    '.hgr-board .hgr-row .hgr-u{flex:0 0 auto;}',
    '.hgr-row .hgr-a{font-weight:800;color:#fff;font-variant-numeric:tabular-nums;white-space:nowrap;}',
    '.hgr-row .hgr-a small{margin-left:4px;font-size:11.5px;font-weight:700;color:rgba(255,255,255,.5);}',
    /* above / below the wheel: about as wide as the wheel, two columns */
    '.hgr-board.hgr-wide{width:440px;min-width:0;max-width:none;box-sizing:border-box;}',
    '.hgr-wide .hgr-rows{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));column-gap:22px;}',
    '.hgr-wide .hgr-rows.hgr-one{grid-template-columns:minmax(0,1fr);}',
    '.hgr-wide .hgr-row{gap:7px;font-size:14.5px;}',
    '.hgr-wide .hgr-row .hgr-u{max-width:112px;}.hgr-wide .hgr-row .hgr-l{font-size:12px;}',
    /* keyframes */
    '@keyframes hgr-pop{0%{transform:scale(.25);opacity:0}55%{transform:scale(1.1);opacity:1}78%{transform:scale(.97)}100%{transform:scale(1);opacity:1}}',
    '@keyframes hgr-pulse{0%,100%{opacity:.45;transform:scale(.92)}50%{opacity:.95;transform:scale(1.05)}}',
    '@keyframes hgr-rise{0%{opacity:0;transform:translateY(10px) scale(.96)}100%{opacity:1;transform:none}}',
    '@keyframes hgr-chip{0%{transform:scale(.2);opacity:0}70%{transform:scale(1.15);opacity:1}100%{transform:scale(1);opacity:1}}',
    '@keyframes hgr-shine{0%{transform:skewX(-18deg) translateX(-120%)}100%{transform:skewX(-18deg) translateX(520%)}}',
    '@keyframes hgr-blink{0%,100%{opacity:1}50%{opacity:.35}}',
    '@keyframes hgr-throb{0%,100%{box-shadow:0 10px 26px rgba(0,0,0,.5)}50%{box-shadow:0 10px 26px rgba(0,0,0,.5),0 0 18px rgba(255,59,48,.55)}}'
  ].join('\n');

  function injectStyle() {
    if (document.getElementById('hgr-style')) return;
    var st = document.createElement('style');
    st.id = 'hgr-style'; st.textContent = STYLE;
    (document.head || document.documentElement).appendChild(st);
  }
  function el(tag, cls, parent) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (parent) parent.appendChild(e);
    return e;
  }
  function perfNow() { return (root.performance && root.performance.now) ? root.performance.now() : Date.now(); }
  // An element re-inserted into the DOM replays its CSS animations: before moving one,
  // take the pop-in classes off unless it has only just been revealed (same frame).
  function calmAnim(e, freshAt) {
    if (perfNow() - (freshAt || 0) < 60) return;
    e.classList.remove('hgr-in');
    var n = e.querySelectorAll('.hgr-fresh');
    for (var i = 0; i < n.length; i++) n[i].classList.remove('hgr-fresh');
  }
  var raf = root.requestAnimationFrame ? function (f) { return root.requestAnimationFrame(f); } : function (f) { return setTimeout(function () { f(perfNow()); }, 16); };
  var caf = root.cancelAnimationFrame ? function (id) { root.cancelAnimationFrame(id); } : function (id) { clearTimeout(id); };

  var IDLE_SPEED = 0.26;   // rad/s idle drift of the head
  var DEMO_HISTORY = ['17', '32', '0', '21', '8', '29', '14', '3', '26', '11'];
  // editor sample (greyed out, the countdown stands still): a TABLE as the server sends it
  var DEMO_TABLE = {
    demo: true, auto_spin_in_ms: 14000, bets_open: true, total_on_table: 1110, display_board: null,
    bets: [
      { id: 'rb-demo0001', user: 'alice', bet: 'red', label: 'Red', type: 'red', odds: 1, amount: 250 },
      { id: 'rb-demo0002', user: 'hexcaster', bet: '17', label: 'Straight 17', type: 'straight', odds: 35, amount: 100 },
      { id: 'rb-demo0003', user: 'bob', bet: 'dozen2', label: '2nd 12', type: 'dozen', odds: 2, amount: 200 },
      { id: 'rb-demo0004', user: 'viewer_42', bet: 'split:17/20', label: 'Split 17/20', type: 'split', odds: 17, amount: 50 },
      { id: 'rb-demo0005', user: 'dicey', bet: 'odd', label: 'Odd', type: 'odd', odds: 1, amount: 150 },
      { id: 'rb-demo0006', user: 'night_owl', bet: '00', label: 'Straight 00', type: 'straight', odds: 35, amount: 25 },
      { id: 'rb-demo0007', user: 'alice', bet: 'col3', label: 'Column 3', type: 'column', odds: 2, amount: 100 },
      { id: 'rb-demo0008', user: 'lucky7', bet: 'corner:25', label: 'Corner 25/26/28/29', type: 'corner', odds: 8, amount: 235 }
    ]
  };
  var OPPOSITE = { right: 'left', left: 'right', above: 'below', below: 'above' };
  var RING_C = 94.25;      // countdown ring circumference (r = 15)

  // ======================================================================
  // Instance
  // ======================================================================
  function Roulette(container, config, opts) {
    opts = opts || {};
    this.container = container;
    this.demo = !!opts.demo;
    this.soundOn = opts.sound !== false && !this.demo;
    this.onLanded = null;
    this.cfg = normConfig(config);
    this.eff = this.cfg;
    this.plan = null; this.spin = null; this.mode = 'idle'; this.landed = false;
    this.badgeOn = false; this.betsOn = false;
    this.startMs = 0; this.t = 0; this.prevT = -1; this.evIdx = 0; this.lastFret = -1; this.sndN = 0;
    this.curAngle = Math.random() * TAU; this.curSpeed = IDLE_SPEED;
    this.idleAngle = this.curAngle; this.idleSpeed = IDLE_SPEED;
    this.ball = { angle: 0, wheel: 0, rel: 0, radius: 0, height: 0, speed: 0, phase: -1, visible: false };
    this.history = []; this.pendingHistory = null; this.histSig = '';
    // Hex's own card (setAnnounce): the cleaned announce, the one on screen, and the one a
    // reset / new spin just took down (the host re-sends it at once: no second pop-in)
    this.announce = null; this.annShown = ''; this.annGone = ''; this.annTimer = 0;
    // the table (setTable): countdown end (perfNow ms) + board rows on screen, by key
    this.table = null; this.pendingTable = null; this.tableSig = null;
    this.cdEnd = 0; this.cdStart = 0; this.cdMs = null; this.cdTimer = 0; this.cdOn = false; this.cdRingEnd = -1;
    this.cdZero = false; this.cdDemo = false; this.cdFreshAt = 0; this.cdMoved = false;
    this.boardOn = false; this.boardKeys = null; this.boardData = null; this.boardPos = ''; this.boardFreshAt = 0;
    this.S = 0; this.L = null; this.layersKey = ''; this.glow = null;
    this.rafId = 0; this.slowId = 0; this.lastNow = 0; this.frameN = 0; this.visible = true; this.lastPollW = 0;
    this.posSet = false;
    this.timers = []; this._res = null; this.destroyed = false;
    this.sfx = this.soundOn ? new Sfx() : null;
    var self = this;
    this._frame = function (now) { self._onFrame(now); };
    injectStyle();
    this._buildDom();
    this._applyEff();
    this.resize();
    this._kick();
  }
  var RP = Roulette.prototype;

  RP._buildDom = function () {
    var c = this.container;
    try {
      if (root.getComputedStyle && root.getComputedStyle(c).position === 'static') { c.style.position = 'relative'; this.posSet = true; }
    } catch (e) { }
    var r = this.root = el('div', 'hgr-root');
    this.canvas = el('canvas', 'hgr-canvas', r);
    this.ctx = this.canvas.getContext('2d');
    this.centerBox = el('div', 'hgr-center', r);
    this.topBox = el('div', 'hgr-top', r);
    this.bottomBox = el('div', 'hgr-bottom', r);
    this.sideBox = el('div', 'hgr-side hgr-r', r);
    this.tableSide = el('div', 'hgr-side hgr-r', r);
    this.capEl = el('div', 'hgr-cap hgr-hide', this.topBox);
    this.histEl = el('div', 'hgr-hist hgr-hide', this.bottomBox);
    this.betsEl = el('div', 'hgr-bets hgr-hide', this.sideBox);
    // countdown: same slot as the caption (they are never up together)
    var cd = this.cdEl = el('div', 'hgr-cd hgr-hide', this.topBox);
    var NS = 'http://www.w3.org/2000/svg', svg = document.createElementNS(NS, 'svg');
    svg.setAttribute('viewBox', '0 0 36 36');
    var c1 = document.createElementNS(NS, 'circle'), c2 = document.createElementNS(NS, 'circle');
    c1.setAttribute('cx', '18'); c1.setAttribute('cy', '18'); c1.setAttribute('r', '15'); c1.setAttribute('class', 'hgr-ring0');
    c2.setAttribute('cx', '18'); c2.setAttribute('cy', '18'); c2.setAttribute('r', '15'); c2.setAttribute('class', 'hgr-ring1');
    c2.setAttribute('stroke-dasharray', String(RING_C)); c2.setAttribute('stroke-dashoffset', '0');
    svg.appendChild(c1); svg.appendChild(c2); cd.appendChild(svg);
    this.cdRing = c2;
    var tx = el('div', '', cd);
    this.cdLab = el('small', '', tx); this.cdLab.textContent = 'NEXT SPIN';
    this.cdTxt = el('b', '', tx);
    // the "on the table" board (moved to its table_position by _fitTable)
    this.boardEl = el('div', 'hgr-board hgr-hide', this.tableSide);
    this.boardTitleEl = null;
    // badge
    var b = this.badgeEl = el('div', 'hgr-badge hgr-hide');
    var m = this.medalEl = el('div', 'hgr-medal', b);
    el('div', 'hgr-halo', m);
    var ring = el('div', 'hgr-ring', m);
    var disc = el('div', 'hgr-disc', ring);
    this.numEl = el('div', 'hgr-num', disc);
    this.detEl = el('div', 'hgr-det', disc);
    this.pillEl = el('div', 'hgr-pill', b);
    this.centerBox.appendChild(b);
    c.appendChild(r);
  };

  // --- config / theme ----------------------------------------------------
  RP._applyEff = function () {
    var e = this.eff = effConfig(this.cfg, this.spin && this.spin.overrides);
    var th = this.th = THEMES[e.theme] || THEMES.classic;
    var cols = this.cols = { red: e.red_color || th.red, black: e.black_color || th.black, green: e.green_color || th.green };
    this.glow = null;
    var s = this.root.style, acc = hexRgb(th.accent);
    s.setProperty('--hgr-accent', th.accent);
    s.setProperty('--hgr-accent-a', rgba(acc, 0.55));
    var keys = ['red', 'black', 'green'];
    for (var i = 0; i < keys.length; i++) {
      s.setProperty('--hgr-' + keys[i], cols[keys[i]]);
      s.setProperty('--hgr-' + keys[i] + '-hi', shade(cols[keys[i]], 0.3));
      s.setProperty('--hgr-' + keys[i] + '-lo', shade(cols[keys[i]], -0.45));
    }
    s.setProperty('--hgr-ringbg', 'linear-gradient(145deg,' + th.trimHi + ' 0%,' + th.trim + ' 28%,' + th.trimLo + ' 55%,' +
      th.trim + ' 78%,' + th.trimHi + ' 100%)');
    // countdown + board: the ring in the wheel's fret colour (neon: glowing), accent edges
    s.setProperty('--hgr-cd', th.fret);
    s.setProperty('--hgr-cd-f', th.fretGlow ? 'drop-shadow(0 0 3px ' + th.fretGlow + ')' : 'none');
    s.setProperty('--hgr-cd-uf', th.fretGlow ? 'drop-shadow(0 0 4px rgba(255,59,48,.9))' : 'none');
    s.setProperty('--hgr-edge', rgba(acc, 0.34));
    s.setProperty('--hgr-edge-glow', th.edgeGlow ? rgba(hexRgb(th.edgeGlow), 0.32) : 'rgba(0,0,0,0)');
    this._ensureLayers(false);
    this._renderHistory();
    // re-render only what has already been revealed (not during the landing delays)
    if (this.badgeOn) this._showBadge(false);
    if (this.spin) this._showCaption(false); else this._hideCaption();
    if (this.betsOn) this._showBets(false);
    this._renderTable(true);
    if (this.sfx) {
      if (!e.sfx) this.sfx.stopRumble();
      else if (this.mode === 'spin' && !this.landed && this.plan && this.t < this.plan.tHit &&
        this.sfx.ensure(e.sfx_volume)) this.sfx.startRumble();   // sfx switched on mid-spin
      this.sfx.setVolume(e.sfx_volume);
    }
  };

  RP._ensureLayers = function (force) {
    if (!(this.S > 0)) return;
    var c = this.cols, th = this.th;
    var key = this.S + '|' + th.label + '|' + c.red + c.black + c.green;
    if (!force && key === this.layersKey && this.L) return;
    var S = this.S, R = S / (2 * GEOM.OVER);
    var head = buildHead(R, th, c, AMERICAN), knob = buildKnob(R, th), dome = buildDome(R, th);
    var ball = buildBall(R), sh = buildShadow(R);
    this.L = {
      R: R, bowl: buildBowl(S, R, th), head: head.cv, headHalf: head.half, light: buildLight(S, R, th),
      knob: knob.cv, knobHalf: knob.half, dome: dome.cv, domeHalf: dome.half,
      ball: ball.cv, ballHalf: ball.half, shadow: sh.cv, shadowHalf: sh.half
    };
    this.layersKey = key;
    this.glow = null;
  };

  // --- public API -----------------------------------------------------------
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
    var dpr = root.devicePixelRatio || 1;
    var S = Math.round(clamp(w * GEOM.OVER * dpr, 96, MAX_CANVAS));
    this.lastPollW = w;
    if (S !== this.S) {
      this.S = S;
      this.canvas.width = S; this.canvas.height = S;
      this._ensureLayers(true);
    }
    this.visible = true;
    this._draw();
    this._fitTable();   // moved / resized: the countdown and the board may need the other side
    this._kick();   // back on RAF at once if the loop was parked while hidden
  };

  RP.play = function (spin) {
    var self = this;
    if (this.destroyed) return Promise.resolve(null);
    var P = planSpin(spin);
    this._settle(null);
    this._clearTimers();
    if (!P) { this.reset(); return Promise.resolve(null); }
    var el0 = Math.max(0, +(spin && spin.elapsed_ms) || 0), t0 = el0 / 1000;
    if (t0 < 0.6) blendPlan(P, t0, this.curAngle, this.curSpeed);
    var pr = new Promise(function (res) { self._res = res; });
    this._begin(spin, P, el0);
    if (t0 >= P.T) {
      this._land(true, true);
    } else {
      if (this.sfx && this.eff.sfx && this.sfx.ensure(this.eff.sfx_volume)) this.sfx.startRumble();
      // landing also fires from a timer, so a throttled/background tab still resolves
      this._later(Math.ceil((P.T - t0) * 1000) + 40, function () { self._catchUp(); });
    }
    this._kick();
    return pr;
  };

  RP.showResult = function (spin) {
    if (this.destroyed) return;
    var P = planSpin(spin);
    if (!P) { this.reset(); return; }   // unusable spin: never leave a stale result up
    this._settle(null);
    this._clearTimers();
    this._begin(spin, P, Math.max(+(spin && spin.elapsed_ms) || 0, P.T * 1000));
    this._land(true, false);
    this._kick();
  };

  RP.setHistory = function (results) {
    if (this.destroyed) return;
    var list = Array.isArray(results) ? results.slice(0, 20) : [];
    if (this.mode === 'spin' && !this.landed) { this.pendingHistory = list; return; }
    this.history = list; this.pendingHistory = null;
    this._renderHistory();
    this._fitTable();   // the strip's width decides where a side board may sit
  };

  // STATE.table: the countdown (auto_spin_in_ms, remaining ms when the message was built;
  // the host takes off the time it sat there) and the "on the table" board. While the ball
  // is in the air it is held and applied at landing, so nothing about the next round ever
  // shows before this one has landed; both only show once no spin is up (reset()).
  RP.setTable = function (table) {
    if (this.destroyed) return;
    var tb = table && typeof table === 'object' && !Array.isArray(table) ? table : null;
    if (this.mode === 'spin' && !this.landed) { this.pendingTable = { t: tb, at: perfNow() }; return; }
    this.pendingTable = null;
    this._applyTable(tb, perfNow(), false);
  };

  // Hex's own winners card (STATE.announce): replaces the computed winners card for the
  // current result, same look. Held while the ball is in the air (shown with the card at
  // landing); null brings the computed card back (if the spin had bets) or nothing.
  // A new play()/showResult() and reset() clear it. A card for another spin than the one
  // on screen is stale and never shown as its result. The server takes the card down when
  // it expires; should that never arrive, it goes by itself a little after expires_in_ms.
  RP.setAnnounce = function (announce) {
    if (this.destroyed) return;
    var a = normAnnounce(announce, '');
    var sid = this.spin ? annStr(this.spin.id, 64) : '';
    if (a && a.spin_id && sid && a.spin_id !== sid) a = null;
    if ((a ? a.sig : '') === (this.announce ? this.announce.sig : '')) return;   // same card
    this.announce = a;
    this._annExpiry(a);
    this._annApply();
  };
  RP._annApply = function () {
    if (this.mode === 'spin') {
      // in the air: held; landed but the card's reveal delay is still running: it shows then
      if (!this.landed || !this.betsOn) return;
      this._showBets(true);
      return;
    }
    // idle (the result phase is over, or a fresh overlay): the card stands on its own
    this.betsOn = !!this.announce;
    this._showBets(true);
    this._renderBoard(true);   // the board waits while a winners card is up
    this._fitTable();
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
    if (this.sfx) this.sfx.stopRumble();
    this.idleAngle = mod(this.curAngle, TAU);
    this.idleSpeed = isFinite(this.curSpeed) ? this.curSpeed : IDLE_SPEED;
    this.mode = 'idle'; this.plan = null; this.spin = null; this.landed = false;
    this.ball.visible = false;
    if (this.pendingHistory) { this.history = this.pendingHistory; this.pendingHistory = null; }
    this._hideResult();
    var pt = this.pendingTable; this.pendingTable = null;
    if (pt) this._applyTable(pt.t, pt.at, true);
    this._applyEff();   // (shows the countdown / board of the next round, if any)
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
    this.L = null; this.glow = null; this.plan = null; this.spin = null; this.pendingHistory = null;
    this.pendingTable = null; this.table = null;
    this._annExpiry(null); this.announce = null;
    this.onLanded = null;
  };

  // --- spin lifecycle -------------------------------------------------------
  RP._begin = function (spin, P, elMs) {
    this._dropAnnounce();   // a new spin: Hex's card for the last one goes
    // the new spin starts from what is on screen now: flush a table held back
    var pt = this.pendingTable; this.pendingTable = null;
    this.spin = spin; this.plan = P; this.mode = 'spin'; this.landed = false;
    this.startMs = perfNow() - elMs;
    this.t = elMs / 1000; this.prevT = -1; this.lastFret = -1;
    this.evIdx = 0;
    while (this.evIdx < P.events.length && P.events[this.evIdx].t <= this.t) this.evIdx++;
    this._hideResult();
    if (pt) this._applyTable(pt.t, pt.at, true);
    this._applyEff();   // (the countdown and the board go: the ball is in play)
    this.curAngle = wheelAngle(P, this.t); this.curSpeed = wheelSpeed(P, this.t);
    ballState(P, this.t, this.ball);
    this._showCaption(true);
    this.frameN = 0;
  };

  RP._catchUp = function () {
    if (this.destroyed || this.mode !== 'spin' || this.landed || !this.plan) return;
    this._step((perfNow() - this.startMs) / 1000);
  };

  RP._step = function (t) {
    var P = this.plan;
    this.t = t;
    this.curAngle = wheelAngle(P, t);
    this.curSpeed = wheelSpeed(P, t);
    ballState(P, t, this.ball);
    if (this.sfx && this.eff.sfx) this._sound(t);
    this.prevT = t;
    if (!this.landed && t >= P.T) this._land(false, true);
  };

  RP._land = function (instant, fire) {
    if (this.landed || !this.plan) return;
    var self = this, spin = this.spin;
    this.landed = true;
    if (this.sfx) this.sfx.stopRumble();
    if (this.pendingHistory) { this.history = this.pendingHistory; this.pendingHistory = null; }
    this._renderHistory();
    // a table that came in mid-air: taken now, shown once the result phase is over
    var pt = this.pendingTable; this.pendingTable = null;
    if (pt) this._applyTable(pt.t, pt.at, true);
    if (instant) {
      this.badgeOn = true; this.betsOn = true;
      this._showBadge(false); this._showBets(false);
    } else {
      this._later(170, function () { self.badgeOn = true; self._showBadge(true); });
      this._later(700, function () { self.betsOn = true; self._showBets(true); });
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

  // --- sound ----------------------------------------------------------------
  RP._sound = function (t) {
    var P = this.plan, b = this.ball, sf = this.sfx;
    var cont = this.prevT >= 0 && t >= this.prevT && t - this.prevT < 0.25;
    var ev = P.events;
    while (this.evIdx < ev.length && ev[this.evIdx].t <= t) {
      var e = ev[this.evIdx++];
      if (cont && e.t > this.prevT) sf.hit(e.heavy, e.vol, e.rate);
    }
    if (b.phase === 3) {
      var fi = Math.floor(b.rel * P.N / TAU + 0.5) % P.N;
      if (cont && this.lastFret >= 0 && fi !== this.lastFret) {
        var sig = this.curSpeed - b.speed;
        sf.hit(false, clamp(0.12 + sig / 3, 0.12, 0.7), 0.9 + 0.3 * Math.random());
      }
      this.lastFret = fi;
    } else this.lastFret = -1;
    if ((this.sndN++ % 3) === 0) {
      var lv = 0, br = 0;
      if (b.phase === 0) { var sp = -b.speed / P.O0; lv = 0.1 + 0.5 * Math.pow(clamp(sp, 0, 1), 0.8); br = clamp(sp, 0, 1); }
      else if (b.phase === 1) { lv = 0.14; br = 0.22; }
      else if (b.phase === 3) { lv = 0.06 * clamp((this.curSpeed - b.speed) / 2, 0, 1); br = 0.1; }
      sf.rumble(lv, br);
    }
  };

  // --- DOM overlays -----------------------------------------------------------
  RP._result = function () {
    var s = this.spin, P = this.plan;
    if (!s || !P) return null;
    // Derived from the planned pocket, so it always matches where the ball is.
    return resultFor(P.label);
  };

  RP._showBadge = function (anim) {
    var e = this.eff, res = this._result(), b = this.badgeEl;
    if (!e.show_result || !res || !this.landed) { b.classList.add('hgr-hide'); return; }
    var col = this.cols[res.color] || '#333', th = this.th, pos = e.result_position;
    var d = pos === 'center' ? 156 : 124;
    var st = b.style;
    st.setProperty('--hgr-d', d + 'px');
    st.setProperty('--hgr-fs', (pos === 'center' ? (res.number.length > 1 ? 66 : 72) : (res.number.length > 1 ? 54 : 58)) + 'px');
    st.setProperty('--hgr-rw', (pos === 'center' ? 6 : 5) + 'px');
    st.setProperty('--hgr-discbg', 'radial-gradient(circle at 36% 26%,' + shade(col, 0.32) + ' 0%,' + col + ' 46%,' + shade(col, -0.55) + ' 100%)');
    var haloHex = res.color === 'black' ? th.glow : css(shadeRgb(col, 0.25));
    st.setProperty('--hgr-halo', haloHex);
    st.setProperty('--hgr-halo-a', rgba(hexRgb(res.color === 'black' ? th.glow : col), 0.28));
    this.numEl.textContent = res.number;
    var det = e.result_details ? detailsFor(res) : '';
    // inside the disc the line is split in two so it never clips: "ODD - LOW" / "2ND 12"
    this.detEl.textContent = '';
    if (det && pos === 'center') {
      var parts = det.split(' \u00B7 ');
      var lines = parts.length > 2 ? [parts.slice(0, parts.length - 1).join(' \u00B7 '), parts[parts.length - 1]] : [det];
      for (var li = 0; li < lines.length; li++) el('div', '', this.detEl).textContent = lines[li];
    }
    this.detEl.classList.toggle('hgr-hide', !(det && pos === 'center'));
    this.pillEl.textContent = det && pos !== 'center' ? det : '';
    this.pillEl.classList.toggle('hgr-hide', !(det && pos !== 'center'));
    if (pos === 'center') { if (b.parentNode !== this.centerBox) this.centerBox.appendChild(b); }
    else {
      var box = pos === 'above' ? this.topBox : this.bottomBox;
      if (b.parentNode !== box || box.firstChild !== b) box.insertBefore(b, box.firstChild);
    }
    b.classList.remove('hgr-hide');
    if (anim) { b.classList.remove('hgr-in'); void b.offsetWidth; b.classList.add('hgr-in'); }
  };

  RP._showCaption = function (anim) {
    var s = this.spin, u = s && s.user ? String(s.user).replace(/^@+/, '').trim() : '';
    var c = this.capEl;
    if (!this.eff.show_user || !u) { c.classList.add('hgr-hide'); return; }
    if (c.getAttribute('data-u') !== u || c.classList.contains('hgr-hide')) {
      c.textContent = '';
      el('i', '', c);
      var bb = el('b', '', c); bb.textContent = '@' + u;
      c.appendChild(document.createTextNode('spins'));
      c.setAttribute('data-u', u);
    }
    c.classList.remove('hgr-hide');
    if (anim) { c.classList.remove('hgr-in'); void c.offsetWidth; c.classList.add('hgr-in'); }
  };
  RP._hideCaption = function () { this.capEl.classList.add('hgr-hide'); this.capEl.removeAttribute('data-u'); };

  RP._showBets = function (anim) {
    var e = this.eff, s = this.spin, box = this.betsEl;
    // Hex's own card replaces the computed one (never while the ball is in the air)
    if (this.announce && !(this.mode === 'spin' && !this.landed)) { this._showAnnounce(anim); return; }
    this.annShown = '';
    box.classList.remove('hgr-ann');
    // the spin's direct bets (resolved on /spin) and the table's settlements, one list
    var bets = s && Array.isArray(s.bets) ? s.bets : [];
    var sts = s && Array.isArray(s.settlements) ? s.settlements : [];
    if (!e.show_bets || !this.landed || !(bets.length || sts.length)) { box.classList.add('hgr-hide'); return; }
    var all = bets.concat(sts), wins = [], valid = 0, wagered = 0, i;
    for (i = 0; i < all.length; i++) {
      var bt = all[i] || {};
      if (bt.valid === false) continue;
      valid++; wagered += +bt.amount || 0;
      if (bt.win) wins.push(bt);
    }
    // only invalid bets (all refunded): nothing won or lost, so no card
    if (!valid) { box.classList.add('hgr-hide'); return; }
    wins.sort(function (a, b) { return (+b.payout || 0) - (+a.payout || 0); });
    var cur = e.currency ? ' ' + e.currency : '';
    box.textContent = '';
    var hd = el('div', 'hgr-bh', box);
    var hb = el('b', '', hd); hb.textContent = wins.length ? (wins.length === 1 ? 'WINNER' : 'WINNERS') : 'NO WINNERS';
    var hs = el('span', '', hd); hs.textContent = wins.length + ' of ' + valid + (valid === 1 ? ' bet' : ' bets');
    var n = Math.min(wins.length, e.bets_max);
    for (i = 0; i < n; i++) {
      var w = wins[i], row = el('div', 'hgr-row', box);
      row.style.animationDelay = (0.08 + i * 0.07).toFixed(2) + 's';
      var uu = el('span', 'hgr-u', row); uu.textContent = w.user ? '@' + String(w.user).replace(/^@+/, '') : 'anon';
      var ll = el('span', 'hgr-l', row); ll.textContent = w.label || w.bet || '';
      var pp = el('span', 'hgr-p', row); pp.textContent = (+w.payout > 0) ? '+' + fmtNum(w.payout) + cur : 'WIN';
    }
    if (wins.length > n) { var more = el('div', 'hgr-more', box); more.textContent = '+' + (wins.length - n) + ' more'; }
    if (!wins.length) {
      var none = el('div', 'hgr-none', box);
      none.textContent = wagered > 0 ? 'House takes ' + fmtNum(wagered) + cur : 'House wins this round';
    }
    this._placeBets(anim);
  };

  // Hex's card, in the winners card's place and style: title, up to bets_max lines
  // (@user, text, signed amount) + "+N more", or its empty_text. Text only (no HTML).
  RP._showAnnounce = function (anim) {
    var e = this.eff, a = this.announce, box = this.betsEl, i;
    if (!e.show_bets) { box.classList.add('hgr-hide'); this.annShown = ''; return; }
    // the same card again right after a reset / new spin took it down: no second pop-in
    if (anim && a.sig === this.annGone) anim = false;
    this.annGone = '';
    this.annShown = a.sig;
    box.textContent = '';
    box.classList.add('hgr-ann');
    var hd = el('div', 'hgr-bh', box);
    el('b', 'hgr-at', hd).textContent = a.title;
    var n = Math.min(a.lines.length, e.bets_max);
    for (i = 0; i < n; i++) {
      var ln = a.lines[i], row = el('div', 'hgr-row', box);
      row.style.animationDelay = (0.08 + i * 0.07).toFixed(2) + 's';
      if (ln.user) {
        el('span', 'hgr-u', row).textContent = '@' + ln.user;
        el('span', 'hgr-l', row).textContent = ln.text;
      } else {
        el('span', 'hgr-t', row).textContent = ln.text;
      }
      if (ln.amount != null) {
        var cls = annAmountClass(ln.amount);
        el('span', 'hgr-p' + (cls ? ' hgr-' + cls : ''), row).textContent = annAmountText(ln.amount, a.currency);
      }
    }
    if (a.lines.length > n) el('div', 'hgr-more', box).textContent = '+' + (a.lines.length - n) + ' more';
    if (!a.lines.length) el('div', 'hgr-none', box).textContent = a.empty_text;
    this._placeBets(anim);
  };

  // side of the wheel for the card, then reveal it
  RP._placeBets = function (anim) {
    var box = this.betsEl;
    // flip to the left when the wheel sits on the right of its stage
    var right = true;
    try {
      var cr = this.container.getBoundingClientRect(), par = this.container.offsetParent || document.body, pr = par.getBoundingClientRect();
      if (pr.width > 0 && cr.left + cr.width / 2 > pr.left + pr.width * 0.62) right = false;
    } catch (err) { }
    this.sideBox.className = 'hgr-side ' + (right ? 'hgr-r' : 'hgr-l');
    box.classList.remove('hgr-hide');
    if (anim) { box.classList.remove('hgr-in'); void box.offsetWidth; box.classList.add('hgr-in'); }
    else box.classList.remove('hgr-in');
  };

  RP._hideResult = function () {
    this.badgeOn = false; this.betsOn = false;
    this.badgeEl.classList.add('hgr-hide'); this.badgeEl.classList.remove('hgr-in');
    this.betsEl.classList.add('hgr-hide');
    this.annShown = '';
    if (!this.spin) this._hideCaption();
  };

  RP._renderHistory = function () {
    var e = this.eff, h = this.histEl;
    if (!e.show_history) { h.classList.add('hgr-hide'); return; }
    var list = this.history, demo = false, i;
    if (!list.length && this.demo) {
      list = [];
      for (i = 0; i < DEMO_HISTORY.length; i++) list.push(DEMO_HISTORY[i]);
      demo = true;
    }
    if (!list.length) { h.classList.add('hgr-hide'); this.histSig = ''; return; }
    var n = Math.min(list.length, e.history_count), labels = [];
    for (i = 0; i < n; i++) {
      var r = list[i];
      var lab = normLabel(r && typeof r === 'object' ? r.number : r);
      labels.push(lab);
    }
    var sig = (demo ? 'd:' : '') + labels.join(',') + '|' + this.cols.red + this.cols.black + this.cols.green;
    h.classList.remove('hgr-hide');
    h.classList.toggle('hgr-demo', demo);
    if (sig === this.histSig) return;
    var prevFirst = this.histSig ? this.histSig.split('|')[0].replace(/^d:/, '') : null;
    var animate = !demo && prevFirst !== null && prevFirst !== labels.join(',');
    this.histSig = sig;
    h.textContent = '';
    for (i = 0; i < labels.length; i++) {
      if (!labels[i]) continue;
      var chip = el('div', 'hgr-chip hgr-' + colorOf(labels[i]) + (i === 0 ? ' hgr-new' : ''), h);
      chip.textContent = labels[i];
    }
    h.classList.remove('hgr-anim');
    if (animate) { void h.offsetWidth; h.classList.add('hgr-anim'); }
  };

  // --- the table: countdown + "on the table" board ------------------------------
  // Both only show while no spin is up (idle): never with the ball in the air, the result
  // badge or a winners card. `quiet`: a table held back mid-air, applied without pop-ins.
  RP._applyTable = function (tb, at, quiet) {
    this.table = tb;
    // countdown: remaining ms measured when the message was built, ticking locally
    var ms = tb && typeof tb.auto_spin_in_ms === 'number' && isFinite(tb.auto_spin_in_ms) ? Math.max(0, tb.auto_spin_in_ms) : null;
    if (ms == null) this.cdMs = null;
    else {
      var end = at + ms;
      if (this.cdMs == null || Math.abs(end - this.cdEnd) > 600) { this.cdEnd = end; this.cdStart = at; }
      this.cdMs = ms;
    }
    this._countdown();
    var sig = tb ? JSON.stringify([tb.bets, tb.total_on_table, tb.display_board, !!tb.demo]) : '';
    if (sig !== this.tableSig) { this.tableSig = sig; this._renderBoard(!quiet); }
    else this._boardHead();   // the countdown may have started / stopped: title + live dot
    this._fitTable();
  };

  RP._tableShown = function () {
    if (this.table) return this.table;
    return this.demo ? DEMO_TABLE : null;
  };

  RP._renderTable = function (anim) {
    this._countdown();
    this._renderBoard(anim);
    this._fitTable();
  };

  // bets are still being taken: a countdown is up and has not reached zero
  RP._betsOpen = function () { return this.cdOn ? !this.cdZero : !!(this.cdDemo); };

  RP._countdown = function () {
    var c = this.cdEl, tb = this._tableShown(), demo = !!(tb && tb.demo);
    var dms = demo && typeof tb.auto_spin_in_ms === 'number' && isFinite(tb.auto_spin_in_ms) ? Math.max(0, tb.auto_spin_in_ms) : null;
    var on = this.mode === 'idle' && (demo ? dms != null : this.cdMs != null);
    var was = !c.classList.contains('hgr-hide');
    this.cdDemo = false;
    if (!on) {
      c.classList.add('hgr-hide'); this._stopCountdown(); this.cdOn = false;
      if (was) this._boardHead();
      return;
    }
    c.classList.toggle('hgr-demo', demo);
    c.classList.remove('hgr-hide');
    if (!was) { c.classList.remove('hgr-in'); void c.offsetWidth; c.classList.add('hgr-in'); this.cdFreshAt = perfNow(); }
    if (demo) {
      // editor sample: stands still (a ticking sample would sit on NO MORE BETS for good)
      this._stopCountdown(); this.cdOn = false; this.cdDemo = true; this.cdRingEnd = -1;
      var ring = this.cdRing, total = Math.max(this.eff.bet_window_seconds * 1000, dms);
      ring.style.transition = 'none';
      ring.style.strokeDashoffset = String(RING_C * (1 - clamp(dms / total, 0, 1)));
      this._cdText(dms);
      return;
    }
    if (!this.cdOn || this.cdRingEnd !== this.cdEnd) this._ring();
    this.cdOn = true;
    this._tickCountdown();
  };
  // (re)start the ring: jump to the current fraction, then run down linearly on the
  // compositor. Also after the pill moved in the DOM (a re-inserted element loses its
  // running transition).
  RP._ring = function () {
    var ring = this.cdRing, left = Math.max(0, this.cdEnd - perfNow());
    var total = Math.max(this.eff.bet_window_seconds * 1000, this.cdEnd - (this.cdStart || this.cdEnd));
    ring.style.transition = 'none';
    ring.style.strokeDashoffset = String(RING_C * (1 - clamp(left / total, 0, 1)));
    void ring.getBoundingClientRect();
    ring.style.transition = 'stroke-dashoffset ' + Math.round(left) + 'ms linear';
    ring.style.strokeDashoffset = String(RING_C);
    this.cdRingEnd = this.cdEnd;
  };
  RP._tickCountdown = function () {
    var self = this;
    if (this.cdTimer) { clearTimeout(this.cdTimer); this.cdTimer = 0; }
    if (!this.cdOn || this.destroyed) return;
    var left = Math.max(0, this.cdEnd - perfNow());
    this._cdText(left);
    if (left > 0) this.cdTimer = setTimeout(function () { self.cdTimer = 0; self._tickCountdown(); }, (left % 1000) + 15);
  };
  RP._cdText = function (left) {
    var p = countdownParts(left);
    this.cdLab.textContent = p.label; this.cdTxt.textContent = p.text;
    this.cdEl.classList.toggle('hgr-urgent', p.urgent && !this.cdDemo);
    if (p.zero !== this.cdZero) { this.cdZero = p.zero; this._boardHead(); }
  };
  RP._stopCountdown = function () { if (this.cdTimer) { clearTimeout(this.cdTimer); this.cdTimer = 0; } };

  // The board: Hex's display_board, or the table's bets (biggest first) as
  // "@user  label  amount currency"; table_max rows + "+N more"; the total. Rows that
  // were not on the board before pop in (a bet just placed); the rest stays still.
  RP._renderBoard = function (anim) {
    var e = this.eff, box = this.boardEl, tb = this._tableShown(), d = boardRows(tb), i;
    var cardUp = !this.betsEl.classList.contains('hgr-hide');
    if (this.mode !== 'idle' || !e.show_table || !d || !d.rows.length || cardUp) { this._hideBoard(); return; }
    var wide = e.table_position === 'above' || e.table_position === 'below';
    var cur = e.currency, n = Math.min(d.rows.length, e.table_max), was = this.boardOn, prev = this.boardKeys, keys = {};
    box.textContent = '';
    box.className = 'hgr-board' + (wide ? ' hgr-wide' : '') + (tb.demo ? ' hgr-demo' : '');
    var hd = el('div', 'hgr-bh', box);
    this.boardTitleEl = el('b', d.hex ? 'hgr-at' : '', hd);
    if (d.showTotal) el('span', '', hd).textContent = fmtNum(d.total) + (cur ? ' ' + cur : '');
    var list = el('div', 'hgr-rows' + (n < 2 ? ' hgr-one' : ''), box);
    for (i = 0; i < n; i++) {
      var r = d.rows[i], row = el('div', 'hgr-row', list);
      keys[r.key] = 1;
      if (!was) row.style.animationDelay = (0.05 + i * 0.05).toFixed(2) + 's';
      else if (anim && prev && !prev[r.key]) { row.classList.add('hgr-fresh'); this.boardFreshAt = perfNow(); }
      if (r.user) {
        el('span', 'hgr-u', row).textContent = '@' + r.user;
        el('span', 'hgr-l', row).textContent = r.text;
      } else el('span', 'hgr-t', row).textContent = r.text;
      if (r.amount != null) {
        var a = el('span', 'hgr-a', row);
        a.textContent = fmtNum(r.amount);
        if (cur && !wide) el('small', '', a).textContent = cur;   // wide: the total carries it
      }
    }
    if (d.rows.length > n) el('div', 'hgr-more', box).textContent = '+' + (d.rows.length - n) + ' more';
    this.boardKeys = keys; this.boardData = d; this.boardOn = true;
    this._boardHead();
    if (anim && !was) { void box.offsetWidth; box.classList.add('hgr-in'); this.boardFreshAt = perfNow(); }
  };
  // title: Hex's, or PLACE YOUR BETS while the countdown runs (ON THE TABLE otherwise and
  // from NO MORE BETS on), with a live dot + the theme's edge while bets are open
  RP._boardHead = function () {
    var t = this.boardTitleEl, d = this.boardData;
    if (!this.boardOn || !t || !d) return;
    var open = this._betsOpen();
    t.textContent = '';
    if (open) el('i', 'hgr-dot', t);
    t.appendChild(document.createTextNode(d.title || (open ? 'PLACE YOUR BETS' : 'ON THE TABLE')));
    this.boardEl.classList.toggle('hgr-open', open);
  };
  RP._hideBoard = function () {
    this.boardEl.classList.add('hgr-hide');
    this.boardEl.classList.remove('hgr-in');
    this.boardOn = false; this.boardKeys = null; this.boardData = null; this.boardTitleEl = null;
  };

  // Where the countdown and the board go. The countdown sits above the wheel (the
  // caption's slot), or under the history strip when that would leave the stage. The
  // board goes to its table_position; when it would run off the stage there, to the
  // opposite side if that is better. A side board is nudged up clear of a history strip
  // that is wider than the wheel. (Layout reads: only on changes, never per frame.)
  RP._fitTable = function () {
    if (this.destroyed) return;
    var cdOn = !this.cdEl.classList.contains('hgr-hide');
    if (!cdOn && !this.boardOn) return;
    var st = this._stageRect();
    this.cdMoved = false;
    this._putCd(true);
    if (cdOn && st) {
      var r = this.cdEl.getBoundingClientRect();
      if (r.height > 0 && r.top < st.top - 1) this._putCd(false);
    }
    if (this.cdMoved && this.cdOn) this._ring();
    if (!this.boardOn) return;
    var pos = this.eff.table_position;
    this._putBoard(pos);
    if (st) {
      var over = overflowPx(this.boardEl.getBoundingClientRect(), st);
      if (over > 0.5) {
        this._putBoard(OPPOSITE[pos]);
        if (!(overflowPx(this.boardEl.getBoundingClientRect(), st) < over)) this._putBoard(pos);
      }
    }
    this._nudgeBoard();
  };
  RP._putCd = function (top) {
    var c = this.cdEl, box = top ? this.topBox : this.bottomBox, after = top ? this.capEl : this.histEl;
    if (c.parentNode === box && c.previousSibling === after) return;
    calmAnim(c, this.cdFreshAt);
    box.insertBefore(c, after.nextSibling);
    this.cdMoved = true;
  };
  RP._putBoard = function (pos) {
    var b = this.boardEl, side = this.tableSide, box = null;
    side.style.top = '';
    this.boardPos = pos;
    if (pos === 'above' || pos === 'below') {
      box = pos === 'above' ? this.topBox : this.bottomBox;   // after the countdown / history strip
      if (b.parentNode === box && box.lastChild === b) return;
    } else {
      side.className = 'hgr-side ' + (pos === 'left' ? 'hgr-l' : 'hgr-r');
      if (b.parentNode === side) return;
      box = side;
    }
    calmAnim(b, this.boardFreshAt);
    box.appendChild(b);
  };
  RP._nudgeBoard = function () {
    if (this.boardPos !== 'left' && this.boardPos !== 'right') return;
    if (this.histEl.classList.contains('hgr-hide')) return;
    var br = this.boardEl.getBoundingClientRect(), hr = this.histEl.getBoundingClientRect();
    if (!(br.width > 0) || !(hr.width > 0)) return;
    if (Math.min(br.right, hr.right) - Math.max(br.left, hr.left) <= 0) return;   // no horizontal overlap
    var k = br.width / (this.boardEl.offsetWidth || br.width);                     // screen px per box px
    var dy = (br.bottom - hr.top) / (k || 1) + 12;
    if (dy > 12) this.tableSide.style.top = 'calc(50% - ' + dy.toFixed(1) + 'px)';
  };
  // the stage the container is placed on (the host's 1920x1080 layer), in screen px;
  // null while nothing can be measured (hidden)
  RP._stageRect = function () {
    try {
      var cr = this.container.getBoundingClientRect();
      if (!(cr.width > 1)) return null;
      var par = this.container.offsetParent;
      if (!par || par === document.body || par === document.documentElement) {
        return { left: 0, top: 0, right: root.innerWidth || 0, bottom: root.innerHeight || 0 };
      }
      var pr = par.getBoundingClientRect();
      return pr.width > 0 && pr.height > 0 ? pr : null;
    } catch (e) { return null; }
  };

  // --- frame loop -------------------------------------------------------------
  // Wake the loop now (next animation frame); cancels a pending slow check.
  RP._kick = function () {
    if (this.destroyed || this.rafId) return;
    if (this.slowId) { clearTimeout(this.slowId); this.slowId = 0; }
    this.rafId = raf(this._frame);
  };
  // Schedule the next tick at the end of a frame. RAF while anything can be seen
  // moving; while idle AND not visible (display:none, zero size, hidden tab) the
  // RAF stops and a cheap 4 Hz check waits for the wheel to be visible again.
  RP._next = function () {
    if (this.destroyed || this.rafId || this.slowId) return;
    if (this.mode === 'idle' && !this.visible) {
      var self = this;
      this.slowId = setTimeout(function () { self.slowId = 0; self._onFrame(perfNow()); }, 250);
    } else {
      this.rafId = raf(this._frame);
    }
  };

  RP._poll = function () {
    var w = 0;
    try { w = this.container.getBoundingClientRect().width; } catch (e) { }
    var hidden = (typeof document !== 'undefined' && document.hidden) || !(w > 1) || this.root.offsetParent === null;
    this.visible = !hidden;
    if (hidden) return;
    var dpr = root.devicePixelRatio || 1;
    var want = Math.round(clamp(w * GEOM.OVER * dpr, 96, MAX_CANVAS));
    if (!this.S || (Math.abs(want - this.S) / this.S > 0.12 && Math.abs(w - this.lastPollW) < 0.02 * w)) this.resize();
    this.lastPollW = w;
  };

  RP._onFrame = function (now) {
    this.rafId = 0;
    if (this.destroyed) return;
    var dt = this.lastNow ? (now - this.lastNow) / 1000 : 0;
    if (dt > 0.1) dt = 0.1; else if (dt < 0) dt = 0;
    this.lastNow = now;
    var n = this.frameN++;
    if (n % 12 === 0 || (!this.visible && this.mode === 'idle')) this._poll();   // parked: every 4 Hz tick
    if (this.mode === 'idle') {
      this.idleSpeed += (IDLE_SPEED - this.idleSpeed) * (1 - Math.exp(-dt / 2));
      this.idleAngle = mod(this.idleAngle + this.idleSpeed * dt, TAU);
      this.curAngle = this.idleAngle; this.curSpeed = this.idleSpeed;
      this.ball.visible = false;
    } else if (this.plan) {
      this._step((perfNow() - this.startMs) / 1000);
    }
    if (this.visible) this._draw();
    this._next();
  };

  RP._draw = function () {
    var L = this.L;
    if (!L || this.destroyed) return;
    var c = this.ctx, S = this.S, h = S / 2, R = L.R, a = this.curAngle;
    c.setTransform(1, 0, 0, 1, 0, 0);
    c.globalAlpha = 1; c.globalCompositeOperation = 'source-over';
    c.clearRect(0, 0, S, S);
    c.drawImage(L.bowl, 0, 0);
    var ca = Math.cos(a), sa = Math.sin(a);
    c.setTransform(ca, sa, -sa, ca, h, h);
    c.drawImage(L.head, -L.headHalf, -L.headHalf);
    c.setTransform(1, 0, 0, 1, 0, 0);
    c.drawImage(L.light, 0, 0);

    var P = this.plan;
    if (this.landed && P) {
      var tl = this.t - P.T;
      var ga = Math.min(1, tl / 0.35) * (0.72 + 0.28 * Math.sin(tl * 5.2));
      if (ga > 0.01) {
        if (!this.glow) {   // built once per landing / size / colour change (cleared there)
          var gc = colorOf(P.label);
          this.glow = buildGlow(R, P.N, gc === 'black' ? hexRgb(this.th.glow) : shadeRgb(this.cols[gc], 0.35));
        }
        var g = a + P.p, cg = Math.cos(g), sg = Math.sin(g);
        c.setTransform(cg, sg, -sg, cg, h, h);
        c.globalCompositeOperation = 'lighter'; c.globalAlpha = ga;
        c.drawImage(this.glow.cv, this.glow.x, this.glow.y);
        c.globalAlpha = 1; c.globalCompositeOperation = 'source-over';
        c.setTransform(1, 0, 0, 1, 0, 0);
      }
    }

    var kr = GEOM.ARM_R * R, kh = L.knobHalf;
    for (var k = 0; k < 4; k++) {
      var ka = a + ARM0 + k * Math.PI / 2;
      c.drawImage(L.knob, h + kr * Math.sin(ka) - kh, h - kr * Math.cos(ka) - kh);
    }
    c.drawImage(L.dome, h - L.domeHalf, h - L.domeHalf);

    var b = this.ball;
    if (b.visible && P) {
      var fade = this.t < 0.12 ? Math.max(0, this.t / 0.12) : 1;
      var br = b.radius * R, bx = h + br * Math.sin(b.angle), by = h - br * Math.cos(b.angle), hh = b.height;
      // contact shadow: grows, softens and slides away as the ball lifts
      var so = (0.008 + 0.65 * hh) * R, ss = 1 + 1.4 * hh, sw = L.shadowHalf * ss;
      c.globalAlpha = fade * (1 - Math.min(0.25, hh * 2.5));
      c.drawImage(L.shadow, bx + so * 0.8 - sw, by + so - sw, sw * 2, sw * 2);
      // motion streak while it is fast on the track: stacked arcs build a
      // tapered comet tail (brightest and widest right behind the ball)
      var sp = b.speed < 0 ? -b.speed : b.speed;
      if (sp > 6 && b.phase <= 1) {
        var span = Math.min(0.6, sp * 0.03), amt = Math.min(1, (sp - 6) / 6), d0 = GEOM.BALL_R * R * 2;
        c.strokeStyle = '#f3f0e8'; c.lineCap = 'round';
        for (var j = 0; j < 7; j++) {
          var f = 1 - j * 0.13, a0 = b.angle - Math.PI / 2, a1 = a0;
          if (b.speed < 0) a1 = a0 + span * f; else a0 = a1 - span * f;
          c.globalAlpha = fade * amt * 0.055;
          c.lineWidth = d0 * (0.36 + 0.105 * j);
          c.beginPath(); c.arc(h, h, br, a0, a1, false); c.stroke();
        }
      }
      var bw = L.ballHalf * (1 + 3.2 * hh);
      c.globalAlpha = fade;
      c.drawImage(L.ball, bx - bw, by - bw, bw * 2, bw * 2);
      c.globalAlpha = 1;
    }
  };

  // ======================================================================
  // Registration
  // ======================================================================
  function copyObj(o) { var r = {}; for (var k in o) if (Object.prototype.hasOwnProperty.call(o, k)) r[k] = o[k]; return r; }
  var API = {
    BASE_SIZE: BASE_SIZE,
    AMERICAN: AMERICAN.slice(),
    RED: RED.slice(),
    THEMES: THEMES,
    DEFAULTS: copyObj(DEFAULTS),       // copies: a host mutating them can't change the renderer
    APPEARANCE: APPEARANCE.slice(),
    DEMO_TABLE: JSON.parse(JSON.stringify(DEMO_TABLE)),
    resultFor: resultFor,
    details: detailsFor,
    create: function (container, config, opts) { return new Roulette(container, config, opts); },
    motion: motion,
    // the pure, DOM-free table helpers (config clean-up, board rows, countdown text) — node tests
    table: { config: normConfig, rows: boardRows, countdown: countdownParts, overflow: overflowPx }
  };
  HG.roulette = API;
  if (typeof module === 'object' && module && module.exports) module.exports = API;
})(typeof window !== 'undefined' ? window : (typeof globalThis !== 'undefined' ? globalThis : this));
